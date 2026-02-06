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
                     page.wait_for_selector('iframe, input[type="file"], [aria-label="Select video"], button:has-text("Select video"), div:has-text("Select video to upload")', timeout=180000)
                 else:
                     # Check if we are just stuck loading (Spinner)
                     # If so, a reload might help
                     log("   No Upload button found. Trying Page Reload...")
                     page.reload()
                     page.wait_for_selector('iframe, input[type="file"], [aria-label="Select video"], button:has-text("Select video"), div:has-text("Select video to upload")', timeout=180000)
                     
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
            page.reload(timeout=120000, wait_until="domcontentloaded")  # 2 minutes
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
        time.sleep(5)
        
        # Take screenshot to see current state
        take_screenshot(page, "2a_before_file_input.png")
        
        try:
            log("   Attempting file upload...")
            input_found = False
            
            # ============================================
            # STRATEGY 1: Use file_chooser (for native dialog)
            # This intercepts the OS file picker dialog
            # ============================================
            try:
                log("   Strategy 1: File chooser (native dialog)...")
                
                # Find the clickable upload button/area
                upload_btn = None
                upload_selectors = [
                    'button:has-text("Select video")',
                    'div:has-text("Select video to upload")',
                    '[class*="upload-btn"]',
                    '[class*="select-btn"]',
                    'button[class*="upload"]',
                    # The upload area itself
                    '[class*="upload-card"]',
                    '[class*="upload-area"]',
                    '[class*="drop-zone"]',
                ]
                
                for sel in upload_selectors:
                    try:
                        btn = page.locator(sel).first
                        if btn.count() > 0 and btn.is_visible():
                            upload_btn = btn
                            log(f"   Found clickable element: {sel}")
                            break
                    except:
                        continue
                
                if upload_btn:
                    # Set up file chooser listener BEFORE clicking
                    with page.expect_file_chooser(timeout=30000) as fc_info:
                        log("   Clicking upload button...")
                        upload_btn.click()
                    
                    file_chooser = fc_info.value
                    log("   File chooser opened! Setting file...")
                    file_chooser.set_files(video_path)
                    input_found = True
                    log("   ✓ File set via file chooser!")
                else:
                    log("   No upload button found for file chooser strategy")
                    
            except Exception as fc_err:
                log(f"   File chooser strategy failed: {fc_err}")
            
            # ============================================
            # STRATEGY 2: Direct input injection (fallback)
            # ============================================
            if not input_found:
                log("   Strategy 2: Direct input injection...")
                
                # Make all file inputs visible
                try:
                    page.evaluate('''() => {
                        const inputs = document.querySelectorAll('input[type="file"]');
                        inputs.forEach(input => {
                            input.style.display = 'block';
                            input.style.visibility = 'visible';
                            input.style.opacity = '1';
                            input.style.position = 'relative';
                            input.style.width = '100px';
                            input.style.height = '100px';
                        });
                    }''')
                    time.sleep(1)
                except:
                    pass
                
                # Try main page
                file_input = page.locator('input[type="file"]').first
                if file_input.count() > 0:
                    log("   Found input on main page, setting file...")
                    file_input.set_input_files(video_path, timeout=180000)
                    input_found = True
                
                # Try frames
                if not input_found:
                    log(f"   Searching in {len(page.frames)} frames...")
                    for idx, frame in enumerate(page.frames):
                        try:
                            # Make inputs visible in frame
                            frame.evaluate('''() => {
                                const inputs = document.querySelectorAll('input[type="file"]');
                                inputs.forEach(input => {
                                    input.style.display = 'block';
                                    input.style.visibility = 'visible';
                                });
                            }''')
                            
                            fi = frame.locator('input[type="file"]').first
                            if fi.count() > 0:
                                log(f"   Found input in frame {idx}: {frame.url[:50]}...")
                                fi.set_input_files(video_path, timeout=180000)
                                input_found = True
                                break
                        except:
                            continue
            
            # ============================================
            # STRATEGY 3: Drag and drop simulation
            # ============================================
            if not input_found:
                log("   Strategy 3: Drag and drop simulation...")
                try:
                    # Find drop zone
                    drop_zone = page.locator('[class*="upload"], [class*="drop"], div:has-text("Select video to upload")').first
                    
                    if drop_zone.count() > 0:
                        # Create a DataTransfer with the file
                        with open(video_path, 'rb') as f:
                            file_content = f.read()
                        
                        # Use JavaScript to simulate drop
                        page.evaluate('''(args) => {
                            const [filePath, fileName] = args;
                            const dropZone = document.querySelector('[class*="upload"], [class*="drop"]');
                            if (!dropZone) return false;
                            
                            // Create drop event
                            const dataTransfer = new DataTransfer();
                            const dropEvent = new DragEvent('drop', {
                                bubbles: true,
                                cancelable: true,
                                dataTransfer: dataTransfer
                            });
                            dropZone.dispatchEvent(dropEvent);
                            return true;
                        }''', [video_path, os.path.basename(video_path)])
                        
                        log("   Attempted drag & drop simulation")
                        time.sleep(3)
                except Exception as dd_err:
                    log(f"   Drag & drop failed: {dd_err}")
            
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
            
            # Wait for upload to actually start and complete
            # The spinner on the button means upload is in progress
            log("   Waiting for file upload to complete...")
            
            upload_started = False
            upload_complete = False
            max_wait_seconds = 300  # 5 minutes max for upload
            
            for wait_sec in range(max_wait_seconds):
                try:
                    # Take periodic screenshots for debugging
                    if wait_sec % 30 == 0 and wait_sec > 0:
                        take_screenshot(page, f"2b_upload_wait_{wait_sec}s.png")
                        log(f"   Still waiting for upload... ({wait_sec}s)")
                    
                    # Check for upload progress indicators
                    page_html = page.content()
                    
                    # Signs that upload is complete:
                    # 1. Caption/description editor visible
                    # 2. "Uploaded" text
                    # 3. Video preview visible
                    # 4. Progress shows 100%
                    
                    # Check for caption editor (means upload done)
                    caption_visible = False
                    for selector in ['.public-DraftEditor-content', '[contenteditable="true"]', 'div[role="textbox"]']:
                        try:
                            if page.locator(selector).first.is_visible():
                                caption_visible = True
                                break
                        except:
                            pass
                    
                    if caption_visible:
                        log("   ✓ Caption editor visible - upload complete!")
                        upload_complete = True
                        break
                    
                    # Check for "Uploaded" text
                    if 'Uploaded' in page_html or 'uploaded' in page_html.lower():
                        log("   ✓ 'Uploaded' text found!")
                        upload_complete = True
                        break
                    
                    # Check for video preview (video element or thumbnail)
                    video_preview = page.locator('video, [class*="preview"], [class*="thumbnail"]').first
                    if video_preview.count() > 0 and video_preview.is_visible():
                        log("   ✓ Video preview visible!")
                        upload_complete = True
                        break
                    
                    # Check for 100% progress
                    if '100%' in page_html:
                        log("   ✓ 100% progress found!")
                        time.sleep(3)  # Wait a bit more after 100%
                        upload_complete = True
                        break
                    
                    # Check for error messages
                    error_indicators = ['Upload failed', 'Error', 'Failed to upload', 'try again']
                    for err in error_indicators:
                        if err.lower() in page_html.lower():
                            err_screenshot = take_screenshot(page, "err_upload_failed.png")
                            log(f"   ❌ Upload error detected: {err}", err_screenshot)
                            raise Exception(f"Upload failed: {err}")
                    
                    # Check if spinner is visible (upload in progress)
                    spinner_visible = False
                    spinner_selectors = [
                        '[class*="spinner"]', 
                        '[class*="loading"]', 
                        '[class*="progress"]',
                        'svg[class*="spin"]',
                        '[aria-busy="true"]'
                    ]
                    for sp in spinner_selectors:
                        try:
                            if page.locator(sp).first.is_visible():
                                spinner_visible = True
                                if not upload_started:
                                    log("   Upload in progress (spinner visible)...")
                                    upload_started = True
                                break
                        except:
                            pass
                    
                    time.sleep(1)
                    
                except Exception as inner_e:
                    logger.debug(f"Wait loop error: {inner_e}")
                    time.sleep(1)
            
            if not upload_complete:
                # Final check with screenshot
                final_screenshot = take_screenshot(page, "2c_upload_timeout.png")
                log(f"⚠️ Upload did not complete in {max_wait_seconds}s", final_screenshot)
                # Don't fail yet - maybe caption is visible anyway
            
            take_screenshot(page, "2d_after_upload_wait.png")
            
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
        # TikTok Studio 2026 might have different selectors
        try:
            # Wait for caption area - try multiple selectors
            caption_selectors = [
                '.public-DraftEditor-content',
                '[data-testid="caption-input"]',
                '[contenteditable="true"]',
                'div[role="textbox"]',
                '.notranslate[contenteditable]',
                'textarea[placeholder*="caption"]',
                'textarea[placeholder*="description"]',
            ]
            
            caption_locator = None
            for selector in caption_selectors:
                try:
                    loc = upload_frame.locator(selector).first
                    loc.wait_for(state="visible", timeout=180000)  # 3 minutes
                    caption_locator = loc
                    log(f"   Found caption editor: {selector}")
                    break
                except:
                    continue
            
            if not caption_locator:
                # Last resort: try on main page instead of iframe
                log("   Trying caption selector on main page...")
                for selector in caption_selectors:
                    try:
                        loc = page.locator(selector).first
                        if loc.is_visible():
                            caption_locator = loc
                            log(f"   Found caption on main page: {selector}")
                            break
                    except:
                        continue
            
            if not caption_locator:
                raise Exception("Caption editor not found with any selector")
            
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
            
            log("4️⃣ Waiting for video processing...")
            
            # Wait for video processing to complete
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
            time.sleep(3)
            
            # Dismiss any modal first
            dismiss_modals()
            
            # 5️⃣ SAVE AS DRAFT DIRECTLY (Skip Post - more reliable on RPi)
            log("5️⃣ Saving as Draft...")
            take_screenshot(page, "saving_draft.png")
            
            draft_locators = [
                'button:has-text("Save draft")',
                'button:has-text("Salva bozza")',
                'button:text-is("Save draft")',
                'button[data-e2e="save_draft_button"]',
            ]
            
            draft_clicked = False
            success = False
            msg = "Unknown Error"
            
            # Try in upload_frame first
            for sel in draft_locators:
                try:
                    d_btn = upload_frame.locator(sel).first
                    if d_btn.count() > 0 and d_btn.is_visible():
                        log(f"   Found Draft button (frame): {sel}")
                        d_btn.scroll_into_view_if_needed()
                        time.sleep(0.5)
                        d_btn.click(force=True)
                        time.sleep(3)
                        draft_clicked = True
                        success = True
                        msg = "Saved as Draft"
                        break
                except:
                    continue
            
            # Try on main page if not found in frame
            if not draft_clicked:
                for sel in draft_locators:
                    try:
                        d_btn = page.locator(sel).first
                        if d_btn.count() > 0 and d_btn.is_visible():
                            log(f"   Found Draft button (page): {sel}")
                            d_btn.scroll_into_view_if_needed()
                            time.sleep(0.5)
                            d_btn.click(force=True)
                            time.sleep(3)
                            draft_clicked = True
                            success = True
                            msg = "Saved as Draft"
                            break
                    except:
                        continue
            
            if draft_clicked:
                log("✅ Draft saved successfully!")
            else:
                err_path = take_screenshot(page, "err_no_draft_button.png")
                log("❌ Draft button not found.", err_path)
                success = False
                msg = "Upload failed - Draft button not found"
            
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
            page.goto("https://www.tiktok.com/?lang=en", timeout=120000, wait_until="domcontentloaded")
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
