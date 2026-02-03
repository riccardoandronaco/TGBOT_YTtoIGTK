import os
import logging
import threading
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ClientError, ChallengeRequired
from pydantic import ValidationError

logger = logging.getLogger(__name__)

# Global variable to store verification code from Telegram
_verification_code = None
_verification_event = threading.Event()


def set_verification_code(code: str):
    """Called from bot to set the verification code."""
    global _verification_code
    _verification_code = code
    _verification_event.set()


def get_verification_code(username, choice) -> str:
    """
    Challenge handler that waits for verification code from Telegram.
    choice: 0 = SMS, 1 = Email
    """
    global _verification_code, _verification_event
    
    _verification_code = None
    _verification_event.clear()
    
    method = "SMS" if choice == 0 else "Email"
    logger.info(f"Challenge required! Instagram will send code via {method}. Waiting for /verifyig command...")
    
    # Wait up to 5 minutes for code
    if _verification_event.wait(timeout=300):
        code = _verification_code
        _verification_code = None
        _verification_event.clear()
        logger.info(f"Received verification code: {code}")
        return code
    else:
        logger.error("Timeout waiting for verification code")
        raise Exception("Timeout: nessun codice inserito entro 5 minuti. Usa /verifyig <codice>")


class InstagramHandler:
    def __init__(self, username, password, session_file='session.json'):
        self.username = username
        self.password = password
        self.session_file = session_file
        self.cl = Client()
        self.cl.request_timeout = 120
        self._pending_challenge = False
        self._setup_challenge_handler()
    
    def _setup_challenge_handler(self):
        """Setup the challenge handler for 2FA/verification."""
        self.cl.challenge_code_handler = get_verification_code
    
    def is_pending_challenge(self):
        """Check if there's a pending challenge waiting for code."""
        return self._pending_challenge

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
        self._setup_challenge_handler()
        
        try:
            self.cl.login(self.username, self.password)
            self.cl.dump_settings(self.session_file)
            logger.info("Fresh login completed and session saved.")
            self._pending_challenge = False
        except ChallengeRequired as e:
            logger.warning(f"Challenge required during fresh login: {e}")
            self._pending_challenge = True
            raise Exception("CHALLENGE_REQUIRED: Instagram richiede verifica. Controlla email/SMS e usa /verifyig <codice>")
        except Exception as e:
            error_str = str(e).lower()
            if 'challenge' in error_str or 'checkpoint' in error_str:
                self._pending_challenge = True
                raise Exception("CHALLENGE_REQUIRED: Instagram richiede verifica. Controlla email/SMS e usa /verifyig <codice>")
            raise e

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
            self._pending_challenge = False
        except ChallengeRequired as e:
            logger.warning(f"Challenge required: {e}")
            self._pending_challenge = True
            raise Exception("CHALLENGE_REQUIRED: Instagram richiede verifica. Controlla email/SMS e usa /verifyig <codice>")
        except Exception as e:
            error_str = str(e).lower()
            if 'challenge' in error_str or 'checkpoint' in error_str:
                self._pending_challenge = True
                raise Exception("CHALLENGE_REQUIRED: Instagram richiede verifica. Controlla email/SMS e usa /verifyig <codice>")
            
            logger.error(f"Login failed: {e}")
            try:
                logger.info("Attempting fresh login...")
                self.cl = Client()
                self._setup_challenge_handler()
                self.cl.login(self.username, self.password)
                self._pending_challenge = False
            except ChallengeRequired as e2:
                self._pending_challenge = True
                raise Exception("CHALLENGE_REQUIRED: Instagram richiede verifica. Controlla email/SMS e usa /verifyig <codice>")
            except Exception as e2:
                error_str2 = str(e2).lower()
                if 'challenge' in error_str2 or 'checkpoint' in error_str2:
                    self._pending_challenge = True
                    raise Exception("CHALLENGE_REQUIRED: Instagram richiede verifica. Controlla email/SMS e usa /verifyig <codice>")
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
            except ChallengeRequired as e:
                self._pending_challenge = True
                raise Exception("CHALLENGE_REQUIRED: Instagram richiede verifica. Controlla email/SMS e usa /verifyig <codice>")
            except (LoginRequired, ClientError) as e:
                error_str = str(e).lower()
                if 'challenge' in error_str or 'checkpoint' in error_str:
                    self._pending_challenge = True
                    raise Exception("CHALLENGE_REQUIRED: Instagram richiede verifica. Controlla email/SMS e usa /verifyig <codice>")
                logger.warning(f"Auth/Client error on attempt {attempt + 1}: {e}")
                if attempt == 0:  # First attempt failed
                    logger.info("Trying fresh login and retry...")
                    try:
                        self.fresh_login()
                    except Exception as login_err:
                        logger.error(f"Fresh login failed: {login_err}")
                        raise login_err
                else:
                    raise e
            except Exception as e:
                error_str = str(e).lower()
                # Check for challenge/checkpoint
                if 'challenge' in error_str or 'checkpoint' in error_str:
                    self._pending_challenge = True
                    raise Exception("CHALLENGE_REQUIRED: Instagram richiede verifica. Controlla email/SMS e usa /verifyig <codice>")
                # Check for 403 in error message
                if '403' in error_str or 'unknown' in error_str:
                    logger.warning(f"Possible 403/session error on attempt {attempt + 1}: {e}")
                    if attempt == 0:
                        logger.info("Trying fresh login and retry...")
                        try:
                            self.fresh_login()
                        except Exception as login_err:
                            logger.error(f"Fresh login failed: {login_err}")
                            raise login_err
                    else:
                        raise e
                else:
                    logger.error(f"Error uploading video: {e}")
                    raise e
        
        raise Exception("Upload failed after all retries")
