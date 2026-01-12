from playwright.sync_api import sync_playwright
import os
import pickle
import time
import logging
import re

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
             path = os.path.join(debug_dir, name)
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
            ]
        )
        
        # Create context with User Agent to avoid detection
        # We use a Linux User-Agent to match the Raspberry Pi environment better
        # USER: When generating cookies on Windows, please spoof this User-Agent in DevTools!
        # UA: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            # viewport={'width': 1920, 'height': 1080} # Let it be responsive or maximized
            viewport=None
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
        log("0️⃣ Checking initial video count...")
        try:
            page.goto("https://www.tiktok.com/profile", timeout=60000)
            time.sleep(5) # Wait for hydration
            content = page.content()
            
            # screen
            path_0 = take_screenshot(page, "0_profile_init.png")
            
            # Regex for video count
            match = re.search(r'"videoCount":(\d+)', content)
            if match:
                initial_video_count = int(match.group(1))
                log(f"   Initial Video Count: {initial_video_count}")
            else:
                log("   Could not determine initial video count (Regex failed).")
        except Exception as e:
            log(f"   Skipping initial count check: {e}")
        
        # 1. Go to Upload Page
        log("1️⃣ Navigating to TikTok upload page...")
        try:
            # Reverting to default load strategy (wait for 'load' event)
            # 'domcontentloaded' was too risky, causing premature interaction.
            page.goto("https://www.tiktok.com/upload?lang=en", timeout=90000)
            
            # Additional small wait to ensure JS is hydrated
            time.sleep(5)
            path_1 = take_screenshot(page, "1_upload_page.png")
            log("   Upload page loaded.")
            
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
            page.wait_for_selector('iframe, input[type="file"], [aria-label="Select video"], button:has-text("Select video"), div:has-text("Select video to upload")', timeout=45000)
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
                     page.wait_for_selector('iframe, input[type="file"]', timeout=30000)
                 else:
                     # Check if we are just stuck loading (Spinner)
                     # If so, a reload might help
                     log("   No Upload button found. Trying Page Reload...")
                     page.reload()
                     page.wait_for_selector('iframe, input[type="file"]', timeout=45000)
                     
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
        file_input = upload_frame.locator('input[type="file"]')
        file_set_success = False

        # Strategy A: Hidden Input
        if file_input.count() > 0:
            log("2️⃣ Uploading file via hidden input...")
            try:
                file_input.set_input_files(video_path)
                file_set_success = True
            except Exception as e:
                log(f"   Hidden input strategy failed: {e}")

        # Strategy B: Click "Select video" (FileChooser)
        if not file_set_success:
            log("   Trying 'Select Video' button (FileChooser) strategy...")
            try:
                with page.expect_file_chooser(timeout=15000) as fc_info:
                    # Look for the button with user-reported text
                    # We look for ANY element containing "Select" visible in the frame
                    # User said: "c'è scritto Select Video!"
                    btn = upload_frame.locator('text="Select Video"').first
                    if not btn.count() or not btn.is_visible():
                        btn = upload_frame.locator('text="Select video"').first # lowercase check
                    if not btn.count() or not btn.is_visible():
                        btn = upload_frame.locator('text="Select file"').first
                    if not btn.count() or not btn.is_visible():
                        # Broad check for "Select"
                         btn = upload_frame.get_by_text("Select", exact=False).first
                        
                    if btn.is_visible():
                        log("   Found visible 'Select' button, clicking...")
                        btn.click()
                    else:
                        raise Exception("Button 'Select Video' not found visible")
                
                fc_info.value.set_files(video_path)
                file_set_success = True
            except Exception as e_fc:
                log(f"❌ FileChooser strategy failed: {e_fc}")

        if not file_set_success:
            log("❌ Critical: Could not upload file (Strategies A & B failed).", take_screenshot(page, "err_file_upload_all_failed.png"))
            browser.close()
            return False, "Upload Form Error: Could not select file"

        # Wait a sec for upload interface to react
        time.sleep(5)
        path_2 = take_screenshot(page, "2_file_selected.png")
        log("   File set successfully.")
            
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
            
            # Handle Copyright checks / Compliance if they appear?
            # Usually they are passive.
            
            # Wait specifically for "Post" button to be enabled.
            # Post button usually says "Post" but has specific data attribute
            post_btn = upload_frame.locator('button[data-e2e="post_video_button"]')
            if post_btn.count() == 0:
                 # Fallback if attribute changes
                 post_btn = upload_frame.locator('button:has-text("Post")').first
            
            log("4️⃣ Waiting for 'Post' button...")
            # Wait until not disabled
            # Verify if upload is done: Progress bar usually disappears or reaches 100%
            
            # Smart wait: check repeatedly if button is enabled
            # Sometimes there are two "Post" buttons (one disabled hidden, one enabled)
            # We look for the enabled one.
            
            for i in range(30): # Wait up to 60 seconds
                if post_btn.is_enabled():
                    log("✅ Post button enabled.")
                    break
                
                # Check if it looks enabled visually/by class? 
                # Sometimes is_enabled() is strict about 'disabled' attribute but TikTok uses classes
                # We can try to assume it's ready if we waited enough
                if i > 10: 
                     # After 20 seconds, try to verify if it's actually clickable?
                     pass
                
                time.sleep(2)
            
            # Even if Playwright thinks it's disabled, if it's red/visible it might be clickable via JS.
            # We try to proceed to clicking logic regardless, but with a warning.
            if not post_btn.is_enabled():
                log("⚠️ Post button reported disabled by Playwright. Attempting Force-Click...")
                # We don't return False immediately anymore. We fall through to the click attempt.
                # Take a debug screenshot strictly for context, but don't abort yet.
                path_warn = take_screenshot(page, "warn_post_disabled.png") # WARN IS NOT ERROR, MAYBE NO SCREENSHOT FOR USER? Or yes because suspicious? Let's hide it for user.

            log("5️⃣ Clicking Post...")
            # Retry click mechanism
            clicked_success = False
            for attempt in range(3):
                try:
                    # Prefer JS click to bypass overlays or 'disabled' checks
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
                        log("📍 Page changed but no confirmation. Verifying on Profile...")
                        try:
                            # Go to profile to verify
                            page.goto("https://www.tiktok.com/profile", wait_until='domcontentloaded')
                            # Wait for post list
                            try:
                                page.wait_for_selector('[data-e2e="user-post-item"]', timeout=30000)
                            except:
                                pass # Maybe no posts or slow load
                            
                            # Get new content
                            page_content = page.content()

                            # method A: Check Caption
                            clean_caption = caption.replace("\n", " ").strip()
                            search_chunk = clean_caption[:30] 
                            
                            path_ver = take_screenshot(page, "verify_profile_end.png")
                            
                            if search_chunk in page_content:
                                log(f"✅ Found video text '{search_chunk}' in profile.")
                                success = True
                                msg = "Uploaded (Verified on Profile)"
                            
                            # Method B: Check Video Count
                            elif initial_video_count != -1:
                                match_new = re.search(r'"videoCount":(\d+)', page_content)
                                if match_new:
                                    new_count = int(match_new.group(1))
                                    log(f"   Old Count: {initial_video_count}, New Count: {new_count}")
                                    if new_count > initial_video_count:
                                        log("✅ Video count increased!")
                                        success = True
                                        msg = "Uploaded (Count Increased)"
                                    else:
                                        log(f"❌ Video count did not increase.", path_ver)
                                else:
                                    log("⚠️ Could not read new video count.", path_ver)
                            
                            if not success:
                                log(f"❌ Verification failed (Text not found & Count not increased).")
                                # page.screenshot(path="debug_profile_check.png") # superseded
                                msg = "Upload Verify Failed (Profile)"

                        except Exception as prof_e:
                            log(f"⚠️ Profile verification failed/timed out: {prof_e}")
                            take_screenshot(page, "err_verify_profile.png")
                            msg = "Upload Verify Error"
                    
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
                            log("❌ Post verification failed AND No Draft option found.")
                            take_screenshot(page, "err_final_fail.png")
                            success = False
                            msg = "Upload Verify Failed & No Draft"
            
            browser.close()
            return (success, msg)

        except Exception as e:
            logger.error(f"Error during upload workflow: {e}")
            path_crit = take_screenshot(page, "critical_error.png")
            try:
                browser.close()
            except:
                pass
            return (False, f"Exception: {e}")

if __name__ == "__main__":
    # Test run
    # upload_video(r"downloads\test.mp4", "Test Caption", "config/tiktok_cookies.txt", headless=False)
    pass
