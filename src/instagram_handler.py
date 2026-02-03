import os
import logging
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ClientError
from pydantic import ValidationError

logger = logging.getLogger(__name__)

class InstagramHandler:
    def __init__(self, username, password, session_file='session.json'):
        self.username = username
        self.password = password
        self.session_file = session_file
        self.cl = Client()
        # Aumentiamo il timeout per connessioni lente o RPi
        self.cl.request_timeout = 120  # 2 minuti di timeout

    def fresh_login(self):
        """
        Forza un login pulito cancellando la sessione esistente.
        """
        logger.info("Performing fresh login (deleting old session)...")
        
        # Delete old session
        if os.path.exists(self.session_file):
            try:
                os.remove(self.session_file)
                logger.info(f"Deleted old session file: {self.session_file}")
            except Exception as e:
                logger.warning(f"Could not delete session file: {e}")
        
        # Create new client and login
        self.cl = Client()
        self.cl.request_timeout = 120
        self.cl.login(self.username, self.password)
        self.cl.dump_settings(self.session_file)
        logger.info("Fresh login completed and session saved.")

    def login(self):
        """
        Logs in to Instagram. Tries to load session first.
        """
        if os.path.exists(self.session_file):
            logger.info("Loading session from file...")
            try:
                self.cl.load_settings(self.session_file)
            except Exception as e:
                logger.warning(f"Could not load session settings: {e}")
        
        try:
            self.cl.login(self.username, self.password)
            logger.info("Logged in successfully.")
        except Exception as e:
            logger.error(f"Login failed: {e}")
            # Try to relogin without session if it failed? 
            # Usually instagrapi handles this, but let's be safe.
            try:
                logger.info("Attempting fresh login...")
                self.cl = Client()
                self.cl.login(self.username, self.password)
            except Exception as e2:
                logger.error(f"Fresh login failed: {e2}")
                raise e2

        # Save session
        self.cl.dump_settings(self.session_file)

    def get_stats(self):
        """
        Returns a dict with followers and media count.
        """
        try:
            # Try loading session and login
            if os.path.exists(self.session_file):
                try:
                    self.cl.load_settings(self.session_file)
                except Exception as e:
                    logger.debug(f"Failed to load settings for stats: {e}")
            
            # Login to ensure we're authenticated
            try:
                self.cl.login(self.username, self.password)
            except Exception as e:
                logger.debug(f"Login for stats failed (may already be logged in): {e}")
            
            # Get user info for the authenticated user
            user_id = self.cl.user_id
            info = self.cl.user_info(user_id)
            return {
                'followers': info.follower_count,
                'media_count': info.media_count
            }
        except Exception as e:
            logger.error(f"Error fetching IG stats: {e}")
            return {'followers': "Errore", 'media_count': "Errore"}

    def upload_video(self, video_path, caption):
        """
        Uploads a video to Instagram (as a Reel/Clip).
        Retries with fresh login on 403 errors.
        """
        for attempt in range(2):  # Try twice
            try:
                logger.info(f"Uploading video: {video_path} (attempt {attempt + 1})")
                # upload_clip is for Reels
                media = self.cl.clip_upload(
                    video_path,
                    caption=caption
                )
                logger.info(f"Uploaded successfully. Media PK: {media.pk}")
                return media.pk
            except ValidationError as e:
                logger.warning(f"Pydantic validation error ignored (upload likely succeeded): {e}")
                return "unknown_pk_validation_error"
            except (LoginRequired, ClientError) as e:
                error_str = str(e)
                logger.warning(f"Auth/Client error on attempt {attempt + 1}: {e}")
                if attempt == 0:  # First attempt failed
                    logger.info("Trying fresh login and retry...")
                    try:
                        self.fresh_login()
                    except Exception as login_err:
                        logger.error(f"Fresh login failed: {login_err}")
                        raise e
                else:
                    raise e
            except Exception as e:
                error_str = str(e)
                # Check for 403 in error message
                if '403' in error_str or 'Unknown' in error_str:
                    logger.warning(f"Possible 403/session error on attempt {attempt + 1}: {e}")
                    if attempt == 0:
                        logger.info("Trying fresh login and retry...")
                        try:
                            self.fresh_login()
                        except Exception as login_err:
                            logger.error(f"Fresh login failed: {login_err}")
                            raise e
                    else:
                        raise e
                else:
                    logger.error(f"Error uploading video: {e}")
                    raise e
        
        raise Exception("Upload failed after all retries")
