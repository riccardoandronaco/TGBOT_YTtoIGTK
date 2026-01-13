import os
import logging
import requests
import re
import yt_dlp
import pickle
import http.cookiejar
from upload_video_playwright import upload_video as playwright_upload
from upload_video_playwright import diagnostic_check_headless 

logger = logging.getLogger(__name__)

class TikTokHandler:
    def __init__(self, cookies_path='config/tiktok_cookies.txt'):
        self.cookies_path = cookies_path
        # Setup directories
        abs_cookies_path = os.path.abspath(cookies_path)
        self.cookies_dir = os.path.dirname(abs_cookies_path)
        
        self.session_name = "default"
        self._ensure_pickle_cookies()

    def _get_cookies_dict(self):
        """Parse Netscape cookies file into a dictionary for requests (stats scraping)."""
        cookies = {}
        if not os.path.exists(self.cookies_path):
            return cookies
        try:
            with open(self.cookies_path, 'r') as f:
                for line in f:
                    if line.startswith('#') or not line.strip():
                        continue
                    parts = line.strip().split('\t')
                    if len(parts) >= 7:
                        cookies[parts[5]] = parts[6]
        except Exception as e:
            logger.warning(f"Failed to parse cookies: {e}")
        return cookies

    def _ensure_pickle_cookies(self):
        """
        Converts the Netscape format cookie text file to the pickled list of dicts 
        (Optional now, but good for compatibility if we revert).
        """
        pickle_filename = f"tiktok_session-{self.session_name}.cookie"
        pickle_path = os.path.join(self.cookies_dir, pickle_filename)
        
        # If pickle exists, we use it. 
        if os.path.exists(pickle_path):
            return

        if not os.path.exists(self.cookies_path):
            logger.warning(f"No cookies file found at {self.cookies_path}, Login might fail.")
            return

        logger.info(f"Converting {self.cookies_path} to {pickle_path} ...")
        try:
            cj = http.cookiejar.MozillaCookieJar(self.cookies_path)
            cj.load()
            
            cookies_list = []
            for c in cj:
                cookie_dict = {
                    'name': c.name,
                    'value': c.value,
                    'domain': c.domain,
                    'path': c.path,
                    'secure': c.secure,
                    'expiry': c.expires
                }
                # Fix for some selenium issues with 'None' sameSite is handled if needed
                cookies_list.append(cookie_dict)
            
            with open(pickle_path, 'wb') as f:
                pickle.dump(cookies_list, f)
            logger.info("Cookie conversion successful.")
        except Exception as e:
            logger.error(f"Cookie conversion failed: {e}")

    def get_stats(self, username=None):
        """
        Fetches TikTok follower count, total likes, and video count.
        Returns a dict: {'followers': int, 'likes': int, 'video_count': int} or values with "N/A"
        """
        if username is None:
            username = os.getenv("TIKTOK_USERNAME", "duodisagio")

        url = f"https://www.tiktok.com/@{username}"
        stats = {'followers': "N/A", 'likes': "N/A", 'video_count': "N/A"}
        
        # Method 1: yt-dlp 
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'cookiefile': self.cookies_path
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                # followers
                count = info.get('follower_count') or info.get('channel_follower_count')
                if count is not None:
                    stats['followers'] = int(count)
                
                # video_count (sometimes available as playlist_count if treated as playlist)
                # but yt-dlp might not expose it directly on the channel info consistently
        except Exception as e:
            logger.debug(f"yt-dlp scraping failed for {url}: {e}")

        # Method 2: HTML Scraping with Cookies
        try:
            if stats['followers'] == "N/A" or stats['likes'] == "N/A" or stats['video_count'] == "N/A":
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                
                cookies = self._get_cookies_dict()
                
                logger.info(f"Scraping TikTok fallback for {username}")
                response = requests.get(url, headers=headers, cookies=cookies, timeout=10)
                html = response.text

                # Followers
                if stats['followers'] == "N/A":
                    match = re.search(r'"followerCount":(\d+)', html)
                    if match:
                        stats['followers'] = int(match.group(1))
                    else:
                        match_meta = re.search(r'(\d+)\s+Followers', html)
                        if match_meta:
                            stats['followers'] = int(match_meta.group(1))

                # Likes
                if stats['likes'] == "N/A":
                    match_hearts = re.search(r'"heartCount":(\d+)', html)
                    if match_hearts:
                        stats['likes'] = int(match_hearts.group(1))
                    else:
                        m2 = re.search(r'"heart":(\d+)', html)
                        if m2:
                            stats['likes'] = int(m2.group(1))
                        else:
                            m3 = re.search(r'(\d+)\s+Likes', html)
                            if m3:
                                stats['likes'] = int(m3.group(1))

                # Video Count
                if stats['video_count'] == "N/A":
                    match_videos = re.search(r'"videoCount":(\d+)', html)
                    if match_videos:
                        stats['video_count'] = int(match_videos.group(1))
        except Exception as e:
            logger.debug(f"HTML scraping failed: {e}")

        return stats

    def upload_video(self, video_path, description, status_callback=None):
        """
        Uploads video to TikTok using Robust Playwright Implementation.
        Returns: (success, message) tuple or raises exception
        """
        logger.info(f"Starting TikTok upload: {video_path}")
        
        try:
            result = playwright_upload(
                video_path=video_path,
                caption=description,
                cookie_path=self.cookies_path,
                headless=True, # Running headless by default
                status_callback=status_callback
            )
            
            # playwight_upload now returns (bool, string)
            if isinstance(result, tuple):
                success, msg = result
            else:
                success = result
                msg = "Upload completed" if result else "Failed (Unknown reason)"
            
            if not success:
               logger.error(f"TikTok upload failed: {msg}")
               return False, msg
               
            logger.info(f"TikTok upload completed successfully: {msg}")
            return True, msg
        except Exception as e:
            logger.error(f"TikTok upload failed with exception: {e}")
            return False, str(e)

    def check_connection(self):
        """Runs the diagnostic check."""
        return diagnostic_check_headless()


