import os
import logging
from instagrapi import Client
from instagrapi.exceptions import LoginRequired
from pydantic import ValidationError

logger = logging.getLogger(__name__)

class InstagramHandler:
    def __init__(self, username, password, session_file='session.json'):
        self.username = username
        self.password = password
        self.session_file = session_file
        self.cl = Client()

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

    def get_followers(self):
        """
        Returns the number of followers for the specified user.
        Tries to use cached session first without full login.
        """
        try:
            # Try loading session if available to avoid being completely anonymous (rate limits)
            if os.path.exists(self.session_file):
                try:
                    self.cl.load_settings(self.session_file)
                except Exception as e:
                    logger.debug(f"Failed to load settings for stats: {e}")
            
            # user_info_by_username is often more reliable for public stats than user_info(id)
            # if we don't have the ID handy.
            info = self.cl.user_info_by_username(self.username)
            return info.follower_count
        except Exception as e:
            logger.error(f"Error fetching IG followers: {e}")
            return "Errore"

    def upload_video(self, video_path, caption):
        """
        Uploads a video to Instagram (as a Reel/Clip).
        """
        try:
            logger.info(f"Uploading video: {video_path}")
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
        except Exception as e:
            logger.error(f"Error uploading video: {e}")
            raise e
