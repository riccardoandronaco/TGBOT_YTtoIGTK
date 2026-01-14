import os
import yt_dlp
import logging
import imageio_ffmpeg

logger = logging.getLogger(__name__)

class YouTubeHandler:
    def __init__(self, download_path='downloads'):
        self.download_path = download_path
        if not os.path.exists(self.download_path):
            os.makedirs(self.download_path)

    def download_video(self, url):
        """
        Downloads a video from YouTube (Shorts or regular) using yt-dlp.
        Returns a dictionary with 'path', 'title', and 'description'.
        """
        # Force H.264 video and AAC audio for maximum compatibility with TikTok
        ydl_opts = {
            'format': 'bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(self.download_path, '%(id)s.%(ext)s'),
            'restrictfilenames': True,
            'quiet': True,
            'no_warnings': True,
            'ffmpeg_location': imageio_ffmpeg.get_ffmpeg_exe(),
            # Post processor to ensure consistent format if not avc1
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
        }
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logger.info(f"Fetching info for {url}")
                info_dict = ydl.extract_info(url, download=False)
                
                video_id = info_dict.get('id')
                
                # Check if file already exists
                expected_filename_base = os.path.join(self.download_path, video_id)
                found_existing = None
                for ext in ['.mp4', '.mkv', '.webm']:
                    if os.path.exists(expected_filename_base + ext):
                        found_existing = expected_filename_base + ext
                        break
                
                is_cached = False
                if found_existing:
                    logger.info(f"Video {video_id} already exists at {found_existing}. Skipping download.")
                    filename = found_existing
                    is_cached = True
                else:
                    # Check if it's a short (usually < 60s and vertical, but yt-dlp handles it as video)
                    # We proceed to download
                    logger.info(f"Downloading {url}")
                    ydl.download([url])
                    
                    filename = ydl.prepare_filename(info_dict)
                    
                    # yt-dlp might merge video and audio into mkv if mp4 is not available directly, 
                    # but we requested mp4. If it merges, it might change extension.
                    # Let's verify the file exists, or find the one that was created.
                    if not os.path.exists(filename):
                        # Try to find the file with the same base name
                        base_name = os.path.splitext(filename)[0]
                        for ext in ['.mp4', '.mkv', '.webm']:
                            if os.path.exists(base_name + ext):
                                filename = base_name + ext
                                break
                
                return {
                    'id': info_dict.get('id'),
                    'path': filename,
                    'title': info_dict.get('title', 'No Title'),
                    'description': info_dict.get('description', ''),
                    'duration': info_dict.get('duration', 0),
                    'thumbnail': info_dict.get('thumbnail', None),
                    'is_cached': is_cached
                }
        except Exception as e:
            logger.error(f"Error downloading video: {e}")
            raise e

    def get_subscriber_count(self, channel_url):
        """
        Fetches the subscriber count from the channel.
        """
        ydl_opts = {
            'extract_flat': True,
            'quiet': True,
            'no_warnings': True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(channel_url, download=False)
                # subscriber_count might be in 'channel_follower_count' or 'subscriber_count'
                count = result.get('channel_follower_count') or result.get('subscriber_count')
                return count if count else "N/A"
        except Exception as e:
            logger.error(f"Error fetching YouTube subs: {e}")
            return "Errore"

    def get_oldest_unprocessed_video(self, channel_url, history_handler, platform_filter=None):
        """
        Fetches the list of videos from the channel, sorts them by date (oldest first),
        and returns the first one that is NOT in the history.
        
        platform_filter: 'instagram', 'tiktok', or None.
        If specified, checks existence only in that platform's history.
        """
        ydl_opts = {
            'extract_flat': True,  # Don't download, just get metadata
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            # IMPORTANT: Fetch ALL entries, not just the first page
            'playlistend': None, 
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logger.info(f"Fetching channel info for {channel_url} (this might take a while for large channels)...")
                result = ydl.extract_info(channel_url, download=False)
                
                if 'entries' not in result:
                    logger.warning("No entries found in channel.")
                    return None

                # entries can be a generator, convert to list to sort
                entries = list(result['entries'])
                logger.info(f"Found {len(entries)} videos in channel.")
                
                # Filter out None entries just in case
                entries = [e for e in entries if e]

                # yt-dlp usually returns videos in Reverse Chronological order (Newest first) for /shorts
                # Since extract_flat doesn't return upload_date for shorts, we can't sort by date.
                # We must rely on the list order and REVERSE it to get Oldest first.
                entries.reverse()
                
                if entries:
                    # After reversing:
                    # entries[0] should be the Oldest
                    # entries[-1] should be the Newest
                    logger.info(f"Oldest video in list: {entries[0].get('title')}")
                    logger.info(f"Newest video in list: {entries[-1].get('title')}")

                for entry in entries:
                    video_id = entry.get('id')
                    title = entry.get('title')
                    duration = entry.get('duration')

                    # Double check if it's likely a short (optional, but good practice)
                    # Note: flat extraction might not have duration for all entries depending on backend
                    # But if we are in /shorts, we assume they are shorts.
                    
                    if not history_handler.exists(video_id, platform_filter):
                        logger.info(f"Found oldest unprocessed video (Platform: {platform_filter or 'ANY'}): {title} ({video_id})")
                        return f"https://www.youtube.com/watch?v={video_id}"
                
                logger.info(f"No new videos found (Platform filter: {platform_filter}).")
                return None

        except Exception as e:
            logger.error(f"Error fetching channel info: {e}")
            raise e
