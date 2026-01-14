from playwright.sync_api import sync_playwright
import os
import pickle
import time
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

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

def upload_video(video_path, caption, cookie_path, headless=True, status_callback=None):
    """
    Robust upload using Playwright.
    """
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
            page.goto("https://www.tiktok.com/upload?lang=en", timeout=120000, wait_until="domcontentloaded")
            
            # Smart Wait for the spinner to disappear
            # We wait for either the upload input OR the spinner to be gone
            try:
                # Wait for spinner to detach/hide or input to appear
                # The spinner usually has class-loading like or typical divs. 
                # Instead, we just wait generously for the input.
                page.wait_for_selector('iframe, input[type="file"], [aria-label="Select video"], button:has-text("Select video")', timeout=60000)
            except:
                log("   ⚠️ Initial load timeout (Spinner detected). Reloading page...")
                try:
                    page.reload(timeout=120000, wait_until="domcontentloaded")
                    time.sleep(10) # Give it time to render after reload
                except Exception as e_reload:
                    log(f"   Reload failed: {e_reload}")
            
            # Additional small wait to ensure JS is hydrated
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
        take_screenshot(page, "2_before_upload_attempt.png")
        log(f"   Current URL: {page.url}")
        
        # RPi FIX: Wait longer for page to fully render
        log("   Waiting for page to stabilize (RPi fix)...")
        time.sleep(5)
        
        try:
            # NEW ROBUST STRATEGY: Try File Chooser Trigger first, then fallback to direct input
            
            # Detect potential upload buttons
            # We use a broad text matching to find the red button or the container
            upload_btn = upload_frame.locator('button:has-text("Select video"), div[role="button"]:has-text("Select video"), span:has-text("Select video")').first
            
            # Check if button is visible (wait briefly)
            can_click_button = False
            try:
                upload_btn.wait_for(state="visible", timeout=5000)
                can_click_button = True
            except:
                log("   'Select video' button not immediately visible, searching broader...")
                # Fallback: try to find the container description if button fails
                try:
                    upload_btn = upload_frame.locator('text="Select video"').first
                    upload_btn.wait_for(state="visible", timeout=3000)
                    can_click_button = True
                except:
                     log("   Broader search failed.")

            if can_click_button:
                log("   Strategy A: Clicking 'Select video' to trigger File Chooser...")
                try:
                    with page.expect_file_chooser(timeout=15000) as fc_info:
                        upload_btn.click()
                    file_chooser = fc_info.value
                    file_chooser.set_files(video_path)
                    log(f"   File set via FileChooser.")
                    take_screenshot(page, '2_file_selected_fc.png')
                except Exception as fc_e:
                    log(f"   Strategy A failed ({fc_e}). Trying Strategy B...")
                    # Fallback to Strategy B - but check ALL frames
                    input_found = False
                    
                    # Try main page first
                    file_input = page.locator('input[type="file"]').first
                    if file_input.count() > 0:
                        log("   Strategy B: Direct input on main page...")
                        file_input.set_input_files(video_path, timeout=60000)
                        input_found = True
                    
                    # Try each frame
                    if not input_found:
                        for frame in page.frames:
                            try:
                                fi = frame.locator('input[type="file"]').first
                                if fi.count() > 0:
                                    log(f"   Strategy B: Found input in frame: {frame.url[:50]}...")
                                    fi.set_input_files(video_path, timeout=60000)
                                    input_found = True
                                    break
                            except:
                                continue
                    
                    if not input_found:
                        # DEBUG: List all frames and their content
                        log(f"   DEBUG: Checking {len(page.frames)} frames...")
                        for idx, frame in enumerate(page.frames):
                            try:
                                inputs = frame.locator('input').count()
                                btns = frame.locator('button').count()
                                log(f"   Frame {idx}: {frame.url[:60]}... (inputs: {inputs}, buttons: {btns})")
                            except: pass
                        
                        take_screenshot(page, "2_err_no_input_found.png")
                        raise Exception("No file input found in any frame")
            else:
                log("   Strategy B: Direct Input Injection (Button not found)...")
                # Same multi-frame approach
                input_found = False
                for frame in [page] + page.frames:
                    try:
                        fi = frame.locator('input[type="file"]').first
                        if fi.count() > 0:
                            fi.set_input_files(video_path, timeout=60000)
                            input_found = True
                            log(f"   Input found and file set.")
                            break
                    except:
                        continue
                if not input_found:
                    raise Exception("No file input found")
            
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
        
        # Wait for the editor container or caption input
        # The caption editor is inside a div with 'DraftEditor' usually
        try:
            # Wait for caption area
            caption_locator = upload_frame.locator('.public-DraftEditor-content')
            caption_locator.wait_for(state="visible", timeout=60000)
            
            # Fill Caption
            # log("Setting caption...") # Too verbose
            caption_locator.click()
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
            
            # HELPER: Function to dismiss "Exit" modal if present
            def dismiss_exit_modal():
                """Check and dismiss the Exit confirmation modal if visible."""
                try:
                    # Method 1: Direct click on Cancel button in modal
                    cancel_btn = page.locator('div[role="dialog"] button:has-text("Cancel"), div[class*="modal"] button:has-text("Cancel")').first
                    if cancel_btn.count() > 0 and cancel_btn.is_visible():
                        logger.info("🚨 Exit modal detected (dialog)! Clicking Cancel...")
                        cancel_btn.click(force=True)
                        time.sleep(1)
                        return True
                    
                    # Method 2: Look for any visible Cancel next to an Exit button
                    if page.locator('button:has-text("Exit")').is_visible():
                        cancel_btn = page.locator('button:has-text("Cancel")').first
                        if cancel_btn.is_visible():
                            logger.info("🚨 Exit button visible! Clicking Cancel...")
                            cancel_btn.click(force=True)
                            time.sleep(1)
                            return True
                    
                    # Method 3: ESC key as last resort
                    if page.locator('text="Are you sure you want to exit?"').is_visible():
                        logger.info("🚨 Exit text detected! Pressing Escape...")
                        page.keyboard.press("Escape")
                        time.sleep(1)
                        return True
                        
                except Exception as e:
                    logger.debug(f"dismiss_exit_modal error: {e}")
                return False
            
            # Check for modal RIGHT AFTER caption is set
            dismiss_exit_modal()
            take_screenshot(page, "3b_after_caption_check.png")
            
            # Wait specifically for "Post" button to be enabled.
            # Be VERY specific: look for the submit button, not any "Post" text
            # TikTok Studio uses specific classes/attributes
            post_btn = None
            
            # Try multiple specific selectors in order of preference
            post_selectors = [
                'button[type="submit"]:has-text("Post")',  # Submit button
                'button[data-e2e="post_video_button"]',     # Data attribute
                'div[class*="DraftEditor"] ~ button:has-text("Post")',  # Near caption
                'button:has-text("Post"):not([disabled])',  # Enabled Post button
            ]
            
            for sel in post_selectors:
                try:
                    btn = upload_frame.locator(sel).first
                    if btn.count() > 0:
                        post_btn = btn
                        log(f"   Found Post button with selector: {sel}")
                        break
                except:
                    continue
            
            # Fallback to generic
            if not post_btn:
                post_btn = upload_frame.locator('button:has-text("Post"), button:has-text("Pubblica")').last  # Use LAST instead of first
                log("   Using fallback selector (last Post button)")
            
            # Debug: How many "Post" buttons are there?
            all_post_btns = upload_frame.locator('button:has-text("Post")')
            log(f"   DEBUG: Found {all_post_btns.count()} buttons with 'Post' text")
            
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
            time.sleep(3)
            
            # NO SCROLLING - JS click works on off-screen elements
            
            # Wait until Post button is enabled
            ready_to_click = False
            for i in range(40): # Wait up to 80 seconds
                # Check for Exit modal EVERY iteration - BEFORE anything else
                if dismiss_exit_modal():
                    log(f"   Modal dismissed at iteration {i}")
                    take_screenshot(page, f"modal_dismissed_{i}.png")
                
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
                path_warn = take_screenshot(page, "warn_post_timeout.png") 

            log("5️⃣ Clicking Post (JS)...")
            # Take screenshot BEFORE clicking
            take_screenshot(page, "5_before_post_click.png")
            
            # Retry click mechanism
            clicked_success = False
            for attempt in range(3):
                # Always try to dismiss modal first
                if dismiss_exit_modal():
                    log(f"   Modal dismissed before click attempt {attempt+1}")
                    time.sleep(1)
                    take_screenshot(page, f"5_modal_dismissed_attempt_{attempt+1}.png")

                try:
                    # DEBUG: Log what we're about to click
                    try:
                        btn_text = post_btn.text_content()
                        btn_tag = post_btn.evaluate("el => el.tagName")
                        btn_class = post_btn.evaluate("el => el.className")
                        log(f"   DEBUG: Clicking element: <{btn_tag} class='{btn_class}'>{btn_text}</>")
                    except: pass
                    
                    # Try STANDARD click first (more reliable), then JS as fallback
                    try:
                        post_btn.click(timeout=5000)
                        log(f"   Click attempt {attempt+1} (Standard)...")
                    except Exception as std_click_err:
                        log(f"   Standard click failed ({std_click_err}), trying JS...")
                        post_btn.evaluate("node => node.click()")
                        log(f"   Click attempt {attempt+1} (JS)...")
                except Exception as click_err:
                    log(f"   JS Click failed, trying standard click: {click_err}")
                    try:
                        post_btn.click(force=True)
                    except Exception as e2:
                        log(f"   Standard click failed: {e2}")

                time.sleep(5)
                
                # Check for "Post now" / "Pubblica ora" modal (sometimes appears)
                # This modal interrupts the flow, asking to confirm posting before checks are done.
                if page.locator('button:has-text("Post now")').is_visible() or \
                   page.locator('button:has-text("Pubblica ora")').is_visible():
                     log("⚠️ 'Post now' modal detected immediately.")
                     path_mod = take_screenshot(page, "modal_detected.png") 
                     break

                # Check if we moved to success page
                if "Manage your posts" in page.content() or "uploaded" in page.content():
                    clicked_success = True
                    break
                
                path_att = take_screenshot(page, f"click_attempt_{attempt+1}.png")
                log(f"🔄 Clicked Post (Attempt {attempt+1}), checking response...")
            
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

            try:
                # Primary success check
                upload_frame.wait_for_selector('text=Manage your posts', timeout=30000)
                path_succ = take_screenshot(page, "success_page.png")
                log("✅ Upload confirmed (found 'Manage your posts')!")
                success = True
                msg = "Uploaded successfully"
            except:
                # Primary success failed. 
                # DANGEROUS: Do NOT trust generic "uploaded" text as it appears in the header "Upload video"
                
                log("⚠️ 'Manage your posts' not found. Checking strict alternatives...")
                
                # Check for "Post published" toast or similar strictly
                if page.locator('div:has-text("Post published")').count() > 0 or \
                   upload_frame.locator('div:has-text("Post published")').count() > 0:
                     path_toast = take_screenshot(page, "toast_success.png")
                     log("✅ Found 'Post published' toast.")
                     success = True
                     msg = "Uploaded (Toast confirmed)"
                
                # Check for Profile verification (Requested by User)
                # Only if we suspect it might have worked (e.g. page changed) or specific request
                # To be safe, if we didn't see the Toast, we should verify on profile if we are NOT on the upload page anymore
                else:
                    # Check if we are still on the upload form
                    is_still_uploading = False
                    try:
                         if upload_frame.locator('button:has-text("Post")').is_visible():
                             is_still_uploading = True
                    except: 
                        pass
                    
                    if not is_still_uploading:
                        log("📍 Page changed but no confirmation. Skipping heavy profile verification on RPi.")
                        # RPi OPTIMIZATION: Trust that it worked if page changed and no errors, 
                        # or at least don't risk another crash by reloading profile.
                        if "upload" not in page.url:
                             log("✅ URL changed from upload page. Assuming success.")
                             success = True
                             msg = "Uploaded (Heuristic: URL Changed)"
                        else:
                             log("⚠️ URL still on upload, but button gone/changed. Unsure.")
                             take_screenshot(page, "warn_ambiguous_success.png")
                        
                        # DISABLED FOR PERFORMANCE:
                        # try:
                        #     # Go to profile to verify
                        #     page.goto("https://www.tiktok.com/profile", wait_until='domcontentloaded')
                        # ... (removed heavy profile check)
                    
                    else:
                        take_screenshot(page, "stuck_on_upload.png")
                        log("⚠️ Still on upload page. Checking for Drafts...")
                    
                        # Draft Fallback
                        # Try to find a button with "Draft" text (e.g. "Save as draft")
                        # Heuristic search for buttons containing "Draft"
                        draft_locators = [
                            'button:has-text("Draft")',
                            'div[role="button"]:has-text("Draft")', 
                            'span:has-text("Save as draft")'
                        ]
                        
                        draft_clicked = False
                        for sel in draft_locators:
                            d_btn = upload_frame.locator(sel).first
                            if d_btn.count() > 0 and d_btn.is_visible():
                                log(f"found Draft button ({sel}). Clicking...")
                                try:
                                    d_btn.click()
                                    time.sleep(3)
                                    success = True
                                    msg = "Saved as Draft (Post failed)"
                                    draft_clicked = True
                                    break
                                except Exception as de:
                                    log(f"Failed to click Draft: {de}")
                        
                        if not draft_clicked:
                            err_path = take_screenshot(page, "err_final_fail.png")
                            log("❌ Post verification failed AND No Draft option found.", err_path)
                            success = False
                            msg = "Upload Verify Failed & No Draft"
            
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
