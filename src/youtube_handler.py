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
        # Fix for video IDs starting with '-' (yt-dlp treats them as flags)
        # Ensure URL is in full format, not just the ID
        if url and not url.startswith('http'):
            # It's just a video ID, convert to full URL
            url = f"https://www.youtube.com/watch?v={url}"
        elif url and 'youtube.com/shorts/' in url:
            # Extract ID from shorts URL and use watch URL format
            # This avoids issues with special characters in shorts URLs
            video_id = url.split('/shorts/')[-1].split('?')[0].split('&')[0]
            url = f"https://www.youtube.com/watch?v={video_id}"
        elif url and 'youtu.be/' in url:
            # Convert youtu.be short links
            video_id = url.split('youtu.be/')[-1].split('?')[0].split('&')[0]
            url = f"https://www.youtube.com/watch?v={video_id}"
        
        logger.info(f"Normalized URL: {url}")
        
        # Force H.264 video and AAC audio for maximum compatibility with TikTok
        ydl_opts = {
            'format': 'bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(self.download_path, '%(id)s.%(ext)s'),
            'restrictfilenames': True,
            'quiet': True,
            'no_warnings': True,
            'ffmpeg_location': imageio_ffmpeg.get_ffmpeg_exe(),
            # Extra options to handle edge cases
            'nocheckcertificate': True,  # Avoid SSL issues on RPi
            'geo_bypass': True,  # Bypass geo restrictions
            'socket_timeout': 30,  # Increase timeout
            # Post processor to ensure consistent format if not avc1
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
        }

        # Multiple retry strategies for 403 errors
        retry_configs = [
            # Strategy 1: Default
            {},
            # Strategy 2: Android client (often bypasses restrictions)
            {
                'extractor_args': {'youtube': {'player_client': ['android']}},
                'http_headers': {
                    'User-Agent': 'com.google.android.youtube/17.36.4 (Linux; U; Android 12; GB) gzip',
                }
            },
            # Strategy 3: iOS client 
            {
                'extractor_args': {'youtube': {'player_client': ['ios']}},
                'http_headers': {
                    'User-Agent': 'com.google.ios.youtube/17.36.4 (iPhone; CPU iPhone OS 15_6 like Mac OS X)',
                }
            },
            # Strategy 4: Web client with browser headers
            {
                'extractor_args': {'youtube': {'player_client': ['web']}},
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                }
            },
            # Strategy 5: mweb (mobile web)
            {
                'extractor_args': {'youtube': {'player_client': ['mweb']}},
            },
        ]
        
        info_dict = None
        last_error = None
        
        for i, extra_opts in enumerate(retry_configs):
            try:
                current_opts = ydl_opts.copy()
                current_opts.update(extra_opts)
                
                with yt_dlp.YoutubeDL(current_opts) as ydl:
                    if i == 0:
                        logger.info(f"Fetching info for {url}")
                    else:
                        logger.info(f"Retry {i}/4 with strategy: {list(extra_opts.get('extractor_args', {}).get('youtube', {}).get('player_client', ['default']))}")
                    
                    info_dict = ydl.extract_info(url, download=False)
                    
                    if info_dict:
                        logger.info(f"Success with strategy {i}")
                        break
                        
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                
                if '403' in str(e) or 'forbidden' in error_str or 'blocked' in error_str:
                    logger.warning(f"Strategy {i} failed with 403/blocked, trying next...")
                    import time
                    time.sleep(2)  # Small delay between retries
                    continue
                elif 'video unavailable' in error_str or 'private' in error_str:
                    # Video genuinely unavailable, don't retry
                    raise e
                else:
                    # Unknown error, try next strategy anyway
                    logger.warning(f"Strategy {i} failed: {e}")
                    continue
        
        if not info_dict:
            raise last_error or Exception("All download strategies failed")
        
        try:
            video_id = info_dict.get('id')
            
            # Sanitize video_id for filename (replace problematic chars)
            safe_video_id = video_id.replace('-', '_') if video_id else 'unknown'
            
            # Check if file already exists (try both original and sanitized ID)
            expected_filename_base = os.path.join(self.download_path, video_id)
            safe_filename_base = os.path.join(self.download_path, safe_video_id)
            
            found_existing = None
            for base in [expected_filename_base, safe_filename_base]:
                for ext in ['.mp4', '.mkv', '.webm']:
                    if os.path.exists(base + ext):
                        found_existing = base + ext
                        break
                if found_existing:
                    break
            
            is_cached = False
            if found_existing:
                logger.info(f"Video {video_id} already exists at {found_existing}. Skipping download.")
                filename = found_existing
                is_cached = True
            else:
                # Proceed to download using the strategy that worked for info extraction
                logger.info(f"Downloading {url}")
                
                # Find which strategy worked and use it for download
                for i, extra_opts in enumerate(retry_configs):
                    try:
                        current_opts = ydl_opts.copy()
                        current_opts.update(extra_opts)
                        
                        with yt_dlp.YoutubeDL(current_opts) as ydl:
                            ydl.download([url])
                            filename = ydl.prepare_filename(info_dict)
                            break
                    except Exception as dl_err:
                        if i < len(retry_configs) - 1:
                            logger.warning(f"Download strategy {i} failed, trying next...")
                            import time
                            time.sleep(2)
                            continue
                        else:
                            raise dl_err
                
                # yt-dlp might merge video and audio into mkv if mp4 is not available directly
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

    def get_channel_videos(self, channel_url, limit=None):
        """
        Fetches the list of all videos from the channel.
        Returns a list of dicts with 'id', 'url', 'title' for each video.
        Videos are returned in chronological order (oldest first).
        
        limit: Maximum number of videos to return (None = all)
        """
        ydl_opts = {
            'extract_flat': True,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'playlistend': None,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logger.info(f"Fetching channel videos for {channel_url}...")
                result = ydl.extract_info(channel_url, download=False)
                
                if 'entries' not in result:
                    logger.warning("No entries found in channel.")
                    return []

                entries = list(result['entries'])
                entries = [e for e in entries if e]
                
                # Reverse to get oldest first
                entries.reverse()
                
                videos = []
                for entry in entries:
                    video_id = entry.get('id')
                    title = entry.get('title')
                    if video_id:
                        videos.append({
                            'id': video_id,
                            'url': f"https://www.youtube.com/watch?v={video_id}",
                            'title': title or 'Untitled'
                        })
                
                logger.info(f"Found {len(videos)} videos in channel.")
                
                if limit:
                    return videos[:limit]
                return videos

        except Exception as e:
            logger.error(f"Error fetching channel videos: {e}")
            raise e

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
