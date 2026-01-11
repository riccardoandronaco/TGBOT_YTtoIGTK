from playwright.sync_api import sync_playwright
import os
import pickle
import time
import logging

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
    def log(msg):
        logger.info(msg)
        if status_callback:
            try:
                status_callback(msg)
            except:
                pass

    log(f"Starting Playwright upload for {video_path}")
    
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
        
        # Raspberry Pi often needs more time due to limited CPU/RAM
        # Increasing default timeout to 120 seconds
        page.set_default_timeout(120000)
        
        # 1. Go to Upload Page
        log("1️⃣ Navigating to TikTok upload page...")
        try:
            # Reverting to default load strategy (wait for 'load' event)
            # 'domcontentloaded' was too risky, causing premature interaction.
            page.goto("https://www.tiktok.com/upload?lang=en", timeout=90000)
            
            # Additional small wait to ensure JS is hydrated
            time.sleep(5)
            
        except Exception as e:
            log(f"⚠️ Navigation warning (proceeding anyway): {e}")

        # Debug: Take a screenshot of what we see immediately
        try:
            page.screenshot(path="debug_upload_init.png")
            log("📸 Initial page screenshot saved")
        except: 
            pass
        
        # Check if login is needed (redirected to login page?)
        # A simple check: wait for iframe or select file button
        try:
            # Wait for file input or iframe
            page.wait_for_selector('iframe, input[type="file"]', timeout=45000)
        except:
             log("⚠️ Upload page check timeout - might be stuck or logged out.")

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
        
        # if file_input.count() == 0:
             # Fallback: Maybe we need to click "Select file" which triggers the hidden input
             # logger.info("Direct file input not found, looking for buttons...")
             # This is tricky without visual execution, but usually file input is there just hidden.
        
        log("2️⃣ Uploading file...")
        try:
            file_input.set_input_files(video_path)
        except Exception as e:
            logger.error(f"Failed to set input file: {e}")
            # Debug screenshot
            page.screenshot(path="debug_upload_fail.png")
            browser.close()
            return False
            
        # Wait for upload to complete (Look for "Uploaded" text or progress bar change)
        # Usually looking for the 'Caption' text box appearing is a good sign the previous step worked
        log("3️⃣ Waiting for processing...")
        
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
                    break
                time.sleep(2)
            
            if not post_btn.is_enabled():
                log("❌ Post button never enabled (Timeout). Analysis might be stuck.")
                page.screenshot(path="debug_post_disabled.png")
                browser.close()
                return False

            log("5️⃣ Clicking Post...")
            # Retry click mechanism
            clicked_success = False
            for attempt in range(3):
                post_btn.click()
                time.sleep(3)
                # Check if "Post now" modal appeared
                if page.locator('button:has-text("Post now")').is_visible():
                     log("⚠️ 'Post now' modal detected immediately.")
                     break
                # Check if we moved to success page
                if "Manage your posts" in page.content() or "uploaded" in page.content():
                    clicked_success = True
                    break
                log(f"🔄 Clicked Post (Attempt {attempt+1}), checking response...")
            
            # 5. Handle "Continue to post?" Modal (Content check)
            # This modal appears if TikTok is still checking the video but allows posting anyway.
            # Buttons: "Cancel", "Post now"
            try:
                # Look for "Post now" button. It might be in the main page or the iframe.
                # We check the main page first as modals are often top-level.
                post_now_btn = page.locator('button:has-text("Post now")')
                
                # Short wait to see if it pops up
                try:
                    post_now_btn.wait_for(state="visible", timeout=5000)
                    log("⚠️ Content check modal detected. Clicking 'Post now'...")
                    post_now_btn.click()
                except:
                    # Maybe it's inside the iframe?
                    post_now_btn_frame = upload_frame.locator('button:has-text("Post now")')
                    if post_now_btn_frame.count() > 0 and post_now_btn_frame.is_visible():
                         log("⚠️ Content check modal detected (in frame). Clicking 'Post now'...")
                         post_now_btn_frame.click()
                    else:
                        # logger.info("No content check modal detected (or timed out), proceeding.")
                        pass

            except Exception as e:
                logger.warning(f"Error handling potential modal: {e}")

            # Wait for "Your video has been uploaded" or redirect or modal
            # "Manage your posts" button usually appears
            try:
                upload_frame.wait_for_selector('text=Manage your posts', timeout=30000)
                log("✅ Upload confirmed!")
                success = True
            except:
                # Try alternatives
                if "uploaded" in page.content():
                    log("✅ Upload likely successful (found text).")
                    success = True
                else:
                    log("⚠️ Could not explicitly confirm success, but Post was clicked.")
                    page.screenshot(path="debug_after_post.png")
                    success = True # Tentative success
            
            browser.close()
            return success

        except Exception as e:
            logger.error(f"Error during upload workflow: {e}")
            page.screenshot(path="debug_upload_fail.png")
            browser.close()
            return False

if __name__ == "__main__":
    # Test run
    # upload_video(r"downloads\test.mp4", "Test Caption", "config/tiktok_cookies.txt", headless=False)
    pass
