import os
import sys
import asyncio
import logging
from dotenv import load_dotenv

# Add src to path to import handlers
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from instagram_handler import InstagramHandler
from tiktok_handler import TikTokHandler
from upload_video_playwright import diagnostic_check_headless, load_cookies
from playwright.sync_api import sync_playwright

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def test_instagram():
    print("\n[TEST] 📸 Testing Instagram...")
    username = os.getenv('INSTAGRAM_USERNAME')
    password = os.getenv('INSTAGRAM_PASSWORD')
    
    if not username or not password:
        print("❌ Instagram credentials missing in .env")
        return False

    ig = InstagramHandler(username, password)
    try:
        # Try a lighter check first or just login
        print("   Attempting Login (Check terminal for 2FA prompts!)...")
        ig.login()
        stats = ig.get_stats()
        print(f"✅ Instagram Login OK. Followers: {stats.get('followers')}")
        return True
    except Exception as e:
        print(f"❌ Instagram Login Failed: {e}")
        return False

def test_tiktok_cookies_read():
    print("\n[TEST] 🎵 Testing TikTok Cookies (Read) ...")
    cookies_path = os.path.join(os.getcwd(), os.getenv('TIKTOK_COOKIES_PATH', 'config/tiktok_cookies.txt'))
    
    if not os.path.exists(cookies_path):
        print(f"❌ Cookies file not found at: {cookies_path}")
        return False
        
    tt = TikTokHandler(cookies_path)
    try:
        stats = tt.get_stats()
        print(f"✅ TikTok Read OK. Followers: {stats.get('followers')}, Likes: {stats.get('likes')}")
        return True
    except Exception as e:
        print(f"❌ TikTok Read Failed: {e}")
        return False

def test_tiktok_upload_page():
    print("\n[TEST] 🎵 Testing TikTok Upload Access (Browser) ...")
    cookies_path = os.path.join(os.getcwd(), os.getenv('TIKTOK_COOKIES_PATH', 'config/tiktok_cookies.txt'))
    debug_dir = os.path.join(os.path.dirname(__file__), 'debug_output')
    os.makedirs(debug_dir, exist_ok=True)
    
    with sync_playwright() as p:
        print("   Launching Browser (Headless)...")
        browser = p.chromium.launch(headless=True) # Set to False to see it
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080},
            locale="en-US"
        )
        
        # Load and verify cookies content
        cookies = load_cookies(cookies_path)
        tk_cookies = [c for c in cookies if "tiktok" in c.get('domain', '')]
        print(f"   Loaded {len(tk_cookies)} TikTok cookies.")
        
        if tk_cookies:
            context.add_cookies(tk_cookies)
        
        page = context.new_page()
        
        try:
            print("   Navigating to TikTok Upload...")
            page.goto("https://www.tiktok.com/upload?lang=en", timeout=60000)
            
            # Check for redirect to login
            if page.url.startswith("https://www.tiktok.com/login") or page.locator('button:has-text("Log in")').is_visible():
                print("❌ Redirected to Login Page (Cookies Invalid or Expired)")
                page.screenshot(path=os.path.join(debug_dir, "tiktok_login_redirect.png"))
                browser.close()
                return False
                
            # Check for Upload Form
            try:
                page.wait_for_selector('iframe, input[type="file"], [aria-label="Select video"]', timeout=30000)
                print("✅ Upload Form REACHABLE.")
                page.screenshot(path=os.path.join(debug_dir, "tiktok_upload_success.png"))
                browser.close()
                return True
            except Exception as e:
                print(f"❌ Timeout waiting for Upload Form: {e}")
                page.screenshot(path=os.path.join(debug_dir, "tiktok_upload_timeout.png"))
                browser.close()
                return False

        except Exception as e:
            print(f"❌ Browser Error: {e}")
            browser.close()
            return False

if __name__ == "__main__":
    # Specify path to .env in parent directory
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    load_dotenv(dotenv_path=env_path)
    print(f"=== TGBOT DIAGNOSTICS ===")
    print(f"Loading env from: {os.path.abspath(env_path)}")
    
    # Check what to run
    # ig_ok = test_instagram()
    print("[INFO] Skipping Instagram test to avoid 2FA blocking. Uncomment in code to run.")
    ig_ok = True 
    
    tt_read_ok = test_tiktok_cookies_read()
    tt_up_ok = test_tiktok_upload_page()
    
    print("\n=== SUMMARY ===")
    print(f"Instagram: {'⚠️ SKIPPED' if ig_ok else '❌ FAIL'}")
    print(f"TikTok Read: {'✅ PASS' if tt_read_ok else '❌ FAIL'}")
    print(f"TikTok Upload: {'✅ PASS' if tt_up_ok else '❌ FAIL'}")
