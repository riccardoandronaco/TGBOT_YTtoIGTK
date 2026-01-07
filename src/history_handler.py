import json
import os
import logging

logger = logging.getLogger(__name__)

class HistoryHandler:
    def __init__(self, file_path='history.json'):
        self.file_path = file_path
        self._load()

    def _load(self):
        self.data = {"instagram": set(), "tiktok": set(), "skipped": set()}
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r') as f:
                    content = json.load(f)
                    # Migration from old list format
                    if isinstance(content, list):
                        logger.info("Migrating legacy history format to new dict format.")
                        self.data["instagram"] = set(content)
                        # Assume old history means uploaded to IG or processed
                    elif isinstance(content, dict):
                        self.data["instagram"] = set(content.get("instagram", []))
                        self.data["tiktok"] = set(content.get("tiktok", []))
                        self.data["skipped"] = set(content.get("skipped", []))
            except Exception as e:
                logger.error(f"Error loading history: {e}")

    def save(self):
        try:
            export_data = {
                "instagram": list(self.data["instagram"]),
                "tiktok": list(self.data["tiktok"]),
                "skipped": list(self.data["skipped"])
            }
            with open(self.file_path, 'w') as f:
                json.dump(export_data, f)
        except Exception as e:
            logger.error(f"Error saving history: {e}")

    def add(self, video_id, platform="skipped"):
        """
        platform: 'instagram', 'tiktok', or 'skipped'
        """
        if platform in self.data:
            self.data[platform].add(video_id)
            self.save()
        else:
            logger.error(f"Unknown platform for history: {platform}")

    def exists(self, video_id, platform=None):
        """
        If platform is None, checks if it is skipped OR present in ANY platform (legacy support for yt_handler).
        If platform is specified, checks specific list.
        """
        if platform:
            return video_id in self.data.get(platform, set())
        
        # Default behavior for yt_handler: return True if we should stop processing it.
        # If it's explicitly skipped, it's processed.
        # If it's in IG AND TikTok, it's fully processed.
        # NOTE: For now, avoiding re-downloading things we already touched.
        # If it is in EITHER history, we consider it "touched" for the purpose of "oldest_unprocessed"?
        # No, the user might want to upload to TikTok something that is only on IG.
        # BUT yt_handler usually filters out stuff. 
        # Let's say: It exists if it is in 'skipped'.
        # Or if it is in BOTH IG and TikTok.
        
        # User request: "Tieni uno storico di IG e uno per Tiktok".
        # If I change this return value to `return video_id in self.data['instagram']`, 
        # then `fetch` will skip everything posted to IG. 
        # If I use `fetch`, I want new videos.
        
        # Let's define "exists" (processed) as: present in IG (since user said IG history is good).
        # This prevents re-downloading old stuff.
        return (video_id in self.data["instagram"]) or (video_id in self.data["skipped"])

