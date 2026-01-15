from playwright.sync_api import sync_playwright
import os
import pickle
import time
import logging
import re
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Headless mode from ENV (default: True)
HEADLESS_MODE = os.getenv('TIKTOK_HEADLESS', 'true').lower() in ('true', '1', 'yes')

def load_cookies(cookie_path):
    """
    Load cookies from a Netscape format text file OR a pickle file.
    Returns a list of dicts suitable for Playwright.
    """
    cookies = []
    
    # CASE 1: Pickle format (list of dicts)
    if cookie_path.endswith('.cookie') or cookie_path.endswith('.pkl'):
        try:
            with open(cookie_path, 'rb') as f:
                cookies_pkl = pickle.load(f)
                # Convert selenium style to playwright style if needed
                for c in cookies_pkl:
                    cookie = {
                        'name': c.get('name'),
                        'value': c.get('value'),
                        'domain': c.get('domain'),
                        'path': c.get('path'),
                        'secure': c.get('secure'),
                        # Playwright expects 'expires' as float, sometimes it's missing
                    }
                    if c.get('expiry'):
                        cookie['expires'] = c.get('expiry')
                    
                    # Clean up domain (sometimes has leading dot which playwright handles but good to be clean)
                    cookies.append(cookie)
            return cookies
        except Exception as e:
            logger.error(f"Failed to load pickle cookies: {e}")
            return []

    # CASE 2: Netscape format (standard text file)
    if not os.path.exists(cookie_path):
         return []
         
    try:
        with open(cookie_path, 'r') as f:
            for line in f:
                if line.startswith('#') or not line.strip():
                    continue
                parts = line.strip().split('\t')
                if len(parts) >= 7:
                    # Netscape format: domain, flag, path, secure, expiry, name, value
                    cookie = {
                        'domain': parts[0],
                        'path': parts[2],
                        'secure': parts[3] == 'TRUE',
                        'expires': float(parts[4]) if parts[4] else -1,
                        'name': parts[5],
                        'value': parts[6]
                    }
                    cookies.append(cookie)
    except Exception as e:
        logger.error(f"Failed to load Netscape cookies: {e}")
    
    return cookies

def upload_video(video_path, caption, cookie_path, headless=None, status_callback=None):
    """
    Robust upload using Playwright.
    """
    # Use env variable if headless not explicitly passed
    if headless is None:
        headless = HEADLESS_MODE
    
    print(f"🔍 DEBUG: HEADLESS_MODE={HEADLESS_MODE}, headless={headless}")
    
    def log(msg, screenshot_path=None):
        logger.info(msg)
        if status_callback:
            try:
                # If screenshot_path provided, we just pass checking os.exists
                status_callback(msg, screenshot_path)
            except TypeError:
                # Fallback for old status_callbacks that only take 1 arg
                try:
                    status_callback(msg)
                except: pass
            except:
                pass

    log(f"Starting Playwright upload for {video_path}")
    
    # helper for saving progress screenshot
    def take_screenshot(p, name):
         try:
             # Ensure directory exists
             debug_dir = os.path.join(os.getcwd(), "debug_screens")
             os.makedirs(debug_dir, exist_ok=True)
             
             # Add timestamp to filename
             timestamp = datetime.now().strftime("%H%M%S")
             base_name, ext = os.path.splitext(name)
             timestamped_name = f"{timestamp}_{base_name}{ext}"
             path = os.path.join(debug_dir, timestamped_name)
             
             # Try full page screenshot if possible (only works on Page objects, not Locators)
             try:
                p.screenshot(path=path, full_page=True)
             except:
                # Fallback for Elements or if full_page fails
                p.screenshot(path=path)
             
             # PRO DEBUG: Dump HTML structure if it's an error
             if "err" in name or "warn" in name or "fail" in name:
                 try:
                     html_path = path.replace(".png", ".html")
                     with open(html_path, "w", encoding="utf-8") as f:
                         f.write(p.content())
                 except: pass # Ignore html dump fail
                 
             return path
         except:
             return None
    
    # Ensure absolute path for video (browsers need it)
    video_path = os.path.abspath(video_path)
    if not os.path.exists(video_path):
        logger.error(f"Video file not found: {video_path}")
        return False
    
    with sync_playwright() as p:
        # Launch browser with Linux-optimized args
        # Removing --disable-gpu as it might interfere with TikTok's heavy JS/Canvas
        # Removing --no-sandbox might be safer if running as root, but usually needed on Docker/RPi. 
        # Keeping no-sandbox but enabling GPU might help rendering.
        browser = p.chromium.launch(
            headless=headless, 
            args=[
                "--start-maximized", 
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage", # Prevent memory crashes on RPi
                "--disable-gpu",           # Help with white screens in headless
                "--window-size=1920,1080"
            ]
        )
        
        # Create context with User Agent to avoid detection
        # Switching to Windows UA to avoid mobile-view forced redirects or odd behavior on Linux
        # UA: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080},
            device_scale_factor=1,
            locale="en-US",
            timezone_id="Europe/Rome"
        )
        
        # Load Cookies
        cookies = load_cookies(cookie_path)
        # Filter cookies for .tiktok.com to avoid domain mismatch errors
        tiktok_cookies = [c for c in cookies if "tiktok" in c.get('domain', '') or ".tiktok" in c.get('domain', '')]
        
        if tiktok_cookies:
            context.add_cookies(tiktok_cookies)
        else:
            log("⚠️ No TikTok cookies found! Login will likely fail.")

        page = context.new_page()
        
        # Anti-Detection STEALTH Scripts
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        # --- PRO DEBUG LINSTENERS ---
        # 1. Listen for Console Logs (detect JS errors on page)
        page.on("console", lambda msg: logger.info(f"🕸️ BROWSER CONSOLE ({msg.type}): {msg.text}") if msg.type == "error" else None)
        
        # 2. Listen for Page Crashes
        page.on("pageerror", lambda exc: logger.error(f"🕸️ BROWSER CRASH: {exc}"))
        
        # 3. Network Monitor (Detect 403 Forbidden / 5xx Errors indicating blocking)
        def handle_response(response):
            try:
                if response.status >= 400 and "tiktok.com" in response.url:
                    logger.warning(f"🕸️ NET ERROR {response.status}: {response.url}")
            except: pass
        page.on("response", handle_response)
        # ----------------------------
        
        # Raspberry Pi often needs more time due to limited CPU/RAM
        # Increasing default timeout to 120 seconds
        page.set_default_timeout(120000)

        # 0. Check pre-upload video count to verify later
        initial_video_count = -1
        # DISABLED FOR PERFORMANCE ON RPI:
        # log("0️⃣ Checking initial video count...")
        # try:
        #     page.goto("https://www.tiktok.com/profile", timeout=60000)
        #     time.sleep(5) # Wait for hydration
        #     content = page.content()
            
        #     # screen
        #     # path_0 = take_screenshot(page, "0_profile_init.png")
            
        #     # Regex for video count
        #     match = re.search(r'"videoCount":(\d+)', content)
        #     if match:
        #         initial_video_count = int(match.group(1))
        #         log(f"   Initial Video Count: {initial_video_count}")
        #     else:
        #         log("   Could not determine initial video count (Regex failed).")
        # except Exception as e:
        #     log(f"   Skipping initial count check: {e}")
        
        # 1. Go to Upload Page
        log("1️⃣ Navigating to TikTok upload page...")
        try:
            # Using domcontentloaded + smart wait because networkidle is sometimes flaky on heavy SPAs
            page.goto("https://www.tiktok.com/upload?lang=en", timeout=180000, wait_until="domcontentloaded")
            
            # RPi: Wait generously for spinner to finish and page to fully load
            # TikTok is a heavy SPA that takes time on low-power devices
            log("   Waiting for page to fully load (up to 3 minutes for RPi)...")
            
            max_wait_seconds = 180  # 3 minutes total
            check_interval = 10    # Check every 10 seconds
            elapsed = 0
            page_ready = False
            
            while elapsed < max_wait_seconds:
                try:
                    # Check if upload elements are present
                    upload_ready = page.locator('input[type="file"], button:has-text("Select video"), div:has-text("Select video")').first
                    if upload_ready.count() > 0:
                        page_ready = True
                        log(f"   ✓ Page ready after {elapsed}s")
                        break
                except:
                    pass
                
                # Log progress every 30 seconds
                if elapsed > 0 and elapsed % 30 == 0:
                    log(f"   Still loading... ({elapsed}s)")
                    take_screenshot(page, f"1_loading_{elapsed}s.png")
                
                time.sleep(check_interval)
                elapsed += check_interval
            
            if not page_ready:
                log("   ⚠️ Page still loading after 3 minutes. Trying reload...")
                page.reload(timeout=120000, wait_until="domcontentloaded")
                time.sleep(30)  # Wait 30s after reload
            
            # Final stabilization wait
            time.sleep(5)
            path_1 = take_screenshot(page, "1_upload_page.png")
            log(f"   Upload page loaded. Title: {page.title()}")
            
        except Exception as e:
            log(f"⚠️ Navigation warning (proceeding anyway): {e}")

        # Debug: Take a screenshot of what we see immediately
        try:
            # page.screenshot(path="debug_upload_init.png") # Superseded by path_1
            # log("📸 Initial page screenshot saved")
            pass
        except: 
            pass
        
        # Check if login is needed (redirected to login page?)
        # A simple check: wait for iframe or select file button
        try:
            # Wait for file input, visible button, or text area confirming load
            # Added "Select video" text check to avoid false timeout warnings when UI is actually fine
            log("   Waiting for upload interface to appear (max 120s)...")
            page.wait_for_selector('iframe, input[type="file"], [aria-label="Select video"], button:has-text("Select video"), div:has-text("Select video to upload")', timeout=120000)
        except:
             path_timeout = take_screenshot(page, "warn_upload_timeout.png")
             log("⚠️ Upload page check timeout. Trying fallback navigation...", path_timeout)
             
             # Fallback: Trying to click the "Upload" button in header if we are stuck on homepage/loading
             # Screenshot shows an "Upload" button (usually + Upload)
             try:
                 upload_btn = page.locator('a[href*="/upload"], button:has-text("Upload")').first
                 if upload_btn.is_visible():
                     log("   Found 'Upload' button in header. Clicking...", path_timeout)
                     upload_btn.click()
                     # Wait again
                     time.sleep(5)
                     page.wait_for_selector('iframe, input[type="file"], [aria-label="Select video"], button:has-text("Select video"), div:has-text("Select video to upload")', timeout=60000)
                 else:
                     # Check if we are just stuck loading (Spinner)
                     # If so, a reload might help
                     log("   No Upload button found. Trying Page Reload...")
                     page.reload()
                     page.wait_for_selector('iframe, input[type="file"], [aria-label="Select video"], button:has-text("Select video"), div:has-text("Select video to upload")', timeout=60000)
                     
             except Exception as manual_nav_e:
                 log(f"   Fallback navigation/reload failed: {manual_nav_e}")

             path_err = take_screenshot(page, "err_upload_check.png")
             log("⚠️ Final check before abort.", path_err) 
             
             # Check Login Button presence
             if page.locator('button:has-text("Log in")').is_visible():
                  log("❌ Redirected to Login Page!", path_err)
                  browser.close()
                  return False, "Login Redirect detected"

        # Handle 'Select File'
        # TikTok upload often is an input[type="file"] hidden or inside an iframe
        # We try to target the frame first if it exists
        
        # Sometimes it's in an iframe
        upload_frame = page
        frames = page.frames
        for frame in frames:
            if "upload" in frame.url:
                upload_frame = frame
                break
        
        # Try finding the input in main page or frame
        # file_input = upload_frame.locator('input[type="file"]') # OLD DIRECT METHOD
        
        log("2️⃣ Uploading file...")
        
        # DEBUG: Log file info
        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        log(f"   File: {video_path} ({file_size_mb:.2f} MB)")
        
        # CRITICAL: Take screenshot of what we see BEFORE trying to upload
        pre_upload_screenshot = take_screenshot(page, "2_before_upload_attempt.png")
        log(f"   Current URL: {page.url}")
        
        # CHECK: Detect if page is stuck on spinner (only TikTok logo visible)
        # This happens when page doesn't fully load on RPi
        page_html = page.content()
        has_upload_elements = (
            'input[type="file"]' in page_html or 
            'Select video' in page_html or 
            'upload' in page_html.lower()
        )
        
        if not has_upload_elements:
            log("⚠️ Page appears stuck on spinner. Sending screenshot and aborting...", pre_upload_screenshot)
            # Try one more reload
            log("   Attempting page reload...")
            page.reload(timeout=60000, wait_until="domcontentloaded")
            time.sleep(10)
            take_screenshot(page, "2_after_reload.png")
            page_html = page.content()
            has_upload_elements = 'input' in page_html or 'Select video' in page_html
            
            if not has_upload_elements:
                err_screenshot = take_screenshot(page, "err_spinner_stuck.png")
                log("❌ Page still stuck on spinner after reload. Cookie expired?", err_screenshot)
                browser.close()
                return (False, "Page stuck on TikTok spinner - check cookies")
        
        # RPi FIX: Wait longer for page to fully render
        log("   Waiting for page to stabilize...")
        time.sleep(3)
        
        try:
            # STRATEGY B ONLY: Direct Input Injection (more reliable)
            log("   Finding file input element...")
            input_found = False
            
            # Try main page first
            file_input = page.locator('input[type="file"]').first
            if file_input.count() > 0:
                log("   Found input on main page, setting file...")
                file_input.set_input_files(video_path, timeout=60000)
                input_found = True
            
            # Try each frame if not found
            if not input_found:
                log(f"   Searching in {len(page.frames)} frames...")
                for idx, frame in enumerate(page.frames):
                    try:
                        fi = frame.locator('input[type="file"]').first
                        if fi.count() > 0:
                            log(f"   Found input in frame {idx}: {frame.url[:50]}...")
                            fi.set_input_files(video_path, timeout=60000)
                            input_found = True
                            break
                    except:
                        continue
            
            if not input_found:
                # DEBUG: List all frames and their content
                log(f"   DEBUG: No file input found. Checking {len(page.frames)} frames...")
                for idx, frame in enumerate(page.frames):
                    try:
                        inputs = frame.locator('input').count()
                        btns = frame.locator('button').count()
                        log(f"   Frame {idx}: {frame.url[:60]}... (inputs: {inputs}, buttons: {btns})")
                    except: pass
                
                err_screenshot = take_screenshot(page, "2_err_no_input_found.png")
                raise Exception("No file input found in any frame")
            
            log("   ✓ File set successfully!")
            
            # Wait a sec for upload interface to react
            time.sleep(5)
            take_screenshot(page, "2b_after_file_set.png")
            
        except Exception as e:
            logger.error(f"Failed to set input file: {e}")
            err_screenshot = take_screenshot(page, "err_input_file.png")
            log(f"❌ Input Set Failed: {e}", err_screenshot)  # This sends screenshot to Telegram
            browser.close()
            return (False, f"Input Set Failed: {e}")
            
        # Wait for upload to complete (Look for "Uploaded" text or progress bar change)
        # Usually looking for the 'Caption' text box appearing is a good sign the previous step worked
        log("3️⃣ Waiting for processing (Caption area)...")
        
        # HELPER: Function to dismiss any blocking modals/overlays (defined early)
        def dismiss_modals():
            """Check and dismiss any blocking modals (Exit, Got it, Joyride tour, etc.)."""
            dismissed = False
            try:
                # JOYRIDE TOUR OVERLAY - This blocks everything!
                # Remove it via JavaScript if present
                joyride = page.locator('#react-joyride-portal, .react-joyride__overlay, [data-test-id="overlay"]').first
                if joyride.count() > 0:
                    logger.info("🚨 Joyride tour overlay detected! Removing...")
                    page.evaluate('''() => {
                        const portal = document.getElementById('react-joyride-portal');
                        if (portal) portal.remove();
                        const overlays = document.querySelectorAll('.react-joyride__overlay, [data-test-id="overlay"]');
                        overlays.forEach(el => el.remove());
                    }''')
                    time.sleep(0.5)
                    dismissed = True
                
                # "Got it" / "Skip" buttons for tutorials
                for btn_text in ["Got it", "Skip", "Next", "Close"]:
                    btn = page.locator(f'button:has-text("{btn_text}")').first
                    if btn.count() > 0 and btn.is_visible():
                        logger.info(f"🚨 '{btn_text}' button detected! Clicking...")
                        btn.click(force=True)
                        time.sleep(0.5)
                        dismissed = True
                
                # Exit modal - Cancel button in dialog
                cancel_btn = page.locator('div[role="dialog"] button:has-text("Cancel"), div[class*="modal"] button:has-text("Cancel")').first
                if cancel_btn.count() > 0 and cancel_btn.is_visible():
                    logger.info("🚨 Exit modal detected (dialog)! Clicking Cancel...")
                    cancel_btn.click(force=True)
                    time.sleep(1)
                    dismissed = True
                
                # Exit button visible - click Cancel
                if page.locator('button:has-text("Exit")').is_visible():
                    cancel_btn = page.locator('button:has-text("Cancel")').first
                    if cancel_btn.is_visible():
                        logger.info("🚨 Exit button visible! Clicking Cancel...")
                        cancel_btn.click(force=True)
                        time.sleep(1)
                        dismissed = True
                
                # "Are you sure you want to exit?" - ESC key
                if page.locator('text="Are you sure you want to exit?"').is_visible():
                    logger.info("🚨 Exit text detected! Pressing Escape...")
                    page.keyboard.press("Escape")
                    time.sleep(1)
                    dismissed = True
                    
            except Exception as e:
                logger.debug(f"dismiss_modals error: {e}")
            return dismissed
        
        # Wait for the editor container or caption input
        # The caption editor is inside a div with 'DraftEditor' usually
        try:
            # Wait for caption area
            caption_locator = upload_frame.locator('.public-DraftEditor-content')
            caption_locator.wait_for(state="visible", timeout=60000)
            
            # DISMISS ANY BLOCKING OVERLAYS BEFORE CLICKING
            dismiss_modals()
            
            # Fill Caption
            # log("Setting caption...") # Too verbose
            caption_locator.click(force=True)  # force=True ignores overlay
            # Clear existing if any (filename usually auto-filled)
            # Make sure to wait a bit
            time.sleep(1)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            
            # Type slowly or paste
            page.keyboard.type(caption)
            time.sleep(1)
            
            path_3 = take_screenshot(page, "3_caption_set.png")
            log("   Caption set.")
            
            # Check for modal RIGHT AFTER caption is set
            dismiss_modals()
            take_screenshot(page, "3b_after_caption_check.png")
            
            # Wait specifically for "Post" button to be enabled.
            # Use SPECIFIC selector to avoid matching "Posts" in sidebar
            post_btn = None
            
            # Try multiple specific selectors in order of preference
            post_selectors = [
                'button[data-e2e="post_video_button"]',     # BEST: Unique data attribute
                'button[type="submit"]:has-text("Post")',  # Submit button
                'button:text-is("Post")',                   # Exact text match (not "Posts")
            ]
            
            for sel in post_selectors:
                try:
                    btn = upload_frame.locator(sel).first
                    if btn.count() > 0:
                        post_btn = btn
                        log(f"   Found Post button: {sel}")
                        break
                except:
                    continue
            
            # Fallback - use exact text match
            if not post_btn:
                post_btn = upload_frame.get_by_role("button", name="Post", exact=True)
                log("   Using fallback: exact 'Post' button")
            
            log("4️⃣ Waiting for 'Post' button...")
            
            # Wait for video processing to complete BEFORE checking Post button
            # Look for "Uploaded" indicator or progress bar completion
            log("   Waiting for video processing...")
            for wait_i in range(30):  # Up to 60 seconds
                try:
                    # Check if "Uploaded" text is visible (means processing done)
                    if page.locator('text="Uploaded"').is_visible():
                        log("   ✓ Video upload confirmed")
                        break
                    # Check for progress percentage
                    progress = page.locator('text=/\\d+%/').first
                    if progress.is_visible():
                        pct = progress.text_content()
                        if "100" in pct:
                            log("   ✓ Upload at 100%")
                            break
                except: pass
                time.sleep(2)
            
            # Extra wait for TikTok to finish processing
            time.sleep(2)
            
            # NO SCROLLING - JS click works on off-screen elements
            
            # SUCCESS URL to check after each click
            SUCCESS_URL = "https://www.tiktok.com/tiktokstudio/content"
            
            # Wait until Post button is enabled (OPTIMIZED: reduced from 80s to 30s)
            ready_to_click = False
            for i in range(15): # Wait up to 30 seconds
                # Check for modals EVERY iteration - BEFORE anything else
                if dismiss_modals():
                    log(f"   Modal dismissed at iteration {i}")
                
                # Check if Post button exists and is enabled
                try:
                    if post_btn.count() > 0 and post_btn.is_enabled():
                        log("✅ Post button enabled.")
                        ready_to_click = True
                        break
                except Exception as btn_err:
                    logger.debug(f"Post button check error: {btn_err}")
                
                time.sleep(2)
            
            if not ready_to_click:
                log("⚠️ Post button timeout. Proceeding anyway...")

            log("5️⃣ Clicking Post button...")
            
            # Dismiss any modal first
            dismiss_modals()
            
            # ROBUST POST BUTTON CLICK - Multiple strategies
            post_clicked = False
            
            # Strategy 1: Find the red Post button specifically by its unique attributes
            post_button_selectors = [
                'button[data-e2e="post_video_button"]',
                'button.Button__root--type-primary:has-text("Post")',
                'button[type="button"]:has-text("Post"):not(:has-text("Posts"))',
            ]
            
            for sel in post_button_selectors:
                try:
                    btn = page.locator(sel).first
                    if btn.count() > 0 and btn.is_visible():
                        log(f"   Found Post button: {sel}")
                        
                        # Scroll it into view
                        btn.scroll_into_view_if_needed()
                        time.sleep(0.5)
                        
                        # Get bounding box for coordinate click
                        box = btn.bounding_box()
                        if box:
                            # Click at center of button using coordinates (most reliable)
                            center_x = box['x'] + box['width'] / 2
                            center_y = box['y'] + box['height'] / 2
                            log(f"   Clicking at coordinates ({center_x:.0f}, {center_y:.0f})")
                            page.mouse.click(center_x, center_y)
                            post_clicked = True
                            break
                except Exception as e:
                    log(f"   Selector {sel} failed: {e}")
                    continue
            
            # Strategy 2: If coordinate click failed, try direct methods
            if not post_clicked:
                log("   Trying direct click methods...")
                try:
                    # Try the original post_btn
                    post_btn.click(force=True, timeout=3000)
                    post_clicked = True
                    log("   Direct click succeeded!")
                except:
                    try:
                        # JS click
                        post_btn.evaluate("node => node.click()")
                        post_clicked = True
                        log("   JS click succeeded!")
                    except:
                        log("   All click methods failed!")
            
            # Wait and check for success URL
            log("   Waiting for redirect (up to 15s)...")
            clicked_success = False
            
            for i in range(15):  # Check for up to 15 seconds
                time.sleep(1)
                current_url = page.url
                
                if SUCCESS_URL in current_url or "tiktokstudio/content" in current_url:
                    log(f"✅ Success! Redirected to: {current_url}")
                    clicked_success = True
                    break
                
                # Handle "Post now" modal if it appears
                try:
                    post_now_btn = page.locator('button:has-text("Post now"), button:has-text("Pubblica ora")').first
                    if post_now_btn.count() > 0 and post_now_btn.is_visible():
                        log("   'Post now' modal - clicking...")
                        post_now_btn.click(force=True)
                        time.sleep(2)
                except:
                    pass
            
            # If still not redirected, try clicking Post again
            if not clicked_success:
                log("   ⚠️ No redirect yet. Trying Post button again...")
                try:
                    post_btn_retry = page.locator('button[data-e2e="post_video_button"]').first
                    if post_btn_retry.count() > 0 and post_btn_retry.is_visible():
                        box = post_btn_retry.bounding_box()
                        if box:
                            page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                            time.sleep(5)
                            if SUCCESS_URL in page.url or "tiktokstudio/content" in page.url:
                                log("✅ Second click worked!")
                                clicked_success = True
                except:
                    pass
            
            # 5. Handle "Continue to post?" Modal (Content check)
            # This modal appears if TikTok is still checking the video but allows posting anyway.
            # Buttons: "Cancel", "Post now" (EN), "Pubblica ora" (IT)
            try:
                # We check the main page first as modals are often top-level.
                # Defined strict selectors for the confirmation button
                modal_selectors = [
                    'button:has-text("Post now")',
                    'button:has-text("Pubblica ora")'
                ]
                
                found_modal_btn = None
                
                # Check Main Page
                for sel in modal_selectors:
                     b = page.locator(sel)
                     if b.count() > 0 and b.is_visible():
                         found_modal_btn = b
                         log(f"⚠️ Content check modal detected (Main Page - {sel}). Clicking...", take_screenshot(page, "modal_main.png"))
                         path_mod2 = take_screenshot(page, "modal_main.png")
                         break
                
                # Check Iframe if not found
                if not found_modal_btn:
                     for sel in modal_selectors:
                         b = upload_frame.locator(sel)
                         if b.count() > 0 and b.is_visible():
                             found_modal_btn = b
                             log(f"⚠️ Content check modal detected (Frame - {sel}). Clicking...", take_screenshot(page, "modal_frame.png"))
                if found_modal_btn:
                    found_modal_btn.click()
                    time.sleep(5) # Wait for it to vanish/process
                else:
                    # logger.info("No content check modal detected (or timed out), proceeding.")
                    pass

            except Exception as e:
                logger.warning(f"Error handling potential modal: {e}") 
            
            # (Old modal handling block removed to prevent duplication)

            # Wait for "Your video has been uploaded" or redirect or modal
            # "Manage your posts" button usually appears
            success = False
            msg = "Unknown Error"

            # FAST CHECK: Already on success page?
            if clicked_success or SUCCESS_URL in page.url or "tiktokstudio/content" in page.url:
                log("✅ Upload confirmed (URL check)!")
                success = True
                msg = "Uploaded successfully"
                browser.close()
                return (success, msg)

            try:
                # Primary success check - wait briefly
                upload_frame.wait_for_selector('text=Manage your posts', timeout=10000)
                log("✅ Upload confirmed (found 'Manage your posts')!")
                success = True
                msg = "Uploaded successfully"
            except:
                # Check URL again
                if SUCCESS_URL in page.url or "tiktokstudio/content" in page.url:
                    log("✅ Upload confirmed (URL redirect)!")
                    success = True
                    msg = "Uploaded successfully"
                
                # Check for "Post published" toast
                elif page.locator('div:has-text("Post published")').count() > 0:
                     log("✅ Found 'Post published' toast.")
                     success = True
                     msg = "Uploaded (Toast confirmed)"
                
                else:
                    # Check if we are still on the upload form (use specific selector)
                    is_still_uploading = upload_frame.locator('button[data-e2e="post_video_button"]').is_visible() if upload_frame else False
                    
                    if not is_still_uploading and "upload" not in page.url:
                        log("✅ URL changed from upload page. Assuming success.")
                        success = True
                        msg = "Uploaded (Heuristic: URL Changed)"
                    else:
                        # LAST RESORT: Try clicking Post button one more time with all methods
                        log("⚠️ Still on upload page. Final Post attempt...")
                        take_screenshot(page, "final_attempt.png")
                        
                        final_success = False
                        try:
                            # Find the red Post button
                            final_btn = page.locator('button[data-e2e="post_video_button"]').first
                            if final_btn.count() > 0 and final_btn.is_visible():
                                # Try coordinate click
                                box = final_btn.bounding_box()
                                if box:
                                    log(f"   Final click at ({box['x'] + box['width']/2:.0f}, {box['y'] + box['height']/2:.0f})")
                                    page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                                    time.sleep(5)
                                    
                                    if SUCCESS_URL in page.url or "tiktokstudio/content" in page.url:
                                        log("✅ Final click worked!")
                                        final_success = True
                                        success = True
                                        msg = "Uploaded successfully (final attempt)"
                        except Exception as fe:
                            log(f"   Final attempt failed: {fe}")
                        
                        if not final_success:
                            err_path = take_screenshot(page, "err_final_fail.png")
                            log("❌ All Post attempts failed.", err_path)
                            success = False
                            msg = "Post button click failed"
            
            browser.close()
            return (success, msg)

        except Exception as e:
            logger.error(f"Error during upload workflow: {e}")
            path_crit = take_screenshot(page, "critical_error.png")
            log(f"❌ Critical Error: {e}", path_crit)  # Send screenshot to Telegram
            try:
                browser.close()
            except:
                pass
            return (False, f"Exception: {e}")

if __name__ == "__main__":
    # Test run
    # upload_video(r"downloads\test.mp4", "Test Caption", "config/tiktok_cookies.txt", headless=False)
    pass

def diagnostic_check_headless(cookie_path=None):
    """
    Verification to see what the browser sees on the homepage AND if login works.
    Returns: (path_to_screenshot, info_dict)
    """
    import json
    
    debug_dir = os.path.join(os.getcwd(), "debug_screens")
    os.makedirs(debug_dir, exist_ok=True)
    screenshot_path = os.path.join(debug_dir, "diagnostic_home.png")
    
    info = {"ip": "Unknown", "title": "Unknown", "login": "Checking..."}
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--start-maximized", 
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1920,1080"
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080},
            device_scale_factor=1,
            locale="en-US",
            timezone_id="Europe/Rome"
        )
        
        # Load Cookies if provided
        if cookie_path:
             cookies = load_cookies(cookie_path)
             # Filter for tiktok
             tk_cookies = [c for c in cookies if "tiktok" in c.get('domain', '')]
             if tk_cookies:
                 context.add_cookies(tk_cookies)
                 info["cookies_loaded"] = len(tk_cookies)
             else:
                 info["cookies_loaded"] = 0
        
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        
        try:
            # 1. Check IP Logic
            try:
                page.goto("https://api.ipify.org?format=json", timeout=30000)
                txt = page.locator("body").inner_text()
                info["ip"] = txt
            except: 
                info["ip"] = "Failed to grab"

            # 2. Check TikTok Home & Login Status
            page.goto("https://www.tiktok.com/?lang=en", timeout=60000, wait_until="domcontentloaded")
            time.sleep(5)
            
            # Check Login indicators
            # "Log in" button existence often means logged out.
            # Avatar or "Upload" usually means logged in.
            
            is_login_btn_visible = page.locator('button:has-text("Log in")').is_visible()
            # Also check for profile avatar (usually has alt user name or generic class)
            # A good check is attempting to go to /upload or /profile
            
            screenshot_path = os.path.join(debug_dir, "diagnostic_home.png")
            page.screenshot(path=screenshot_path)
            info["title"] = page.title()
            
            if is_login_btn_visible:
                info["login"] = "❌ LOGOUT DETECTED (Login button visible)"
            else:
                # deeper check
                if page.locator('div[data-e2e="profile-icon"]').count() > 0 or \
                   page.locator('a[href*="/upload"]').is_visible():
                       info["login"] = "✅ LOGGED IN"
                else:
                       info["login"] = "⚠️ UNCERTAIN (No login btn, but no profile icon)"

        except Exception as e:
            info["error"] = str(e)
            
        browser.close()
        return screenshot_path, info
