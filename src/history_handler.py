import json
import os
import logging

logger = logging.getLogger(__name__)

class HistoryHandler:
    def __init__(self, file_path='history.json'):
        self.file_path = file_path
        self._load()

    def _load(self):
        # We use lists to maintain order (historical sequence)
        self.data = {"instagram": [], "tiktok": [], "skipped": []}
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r') as f:
                    content = json.load(f)
                    if isinstance(content, list):
                        # Migration from legacy list format
                        logger.info("Migrating legacy history format to new dict format.")
                        self.data["instagram"] = list(content)
                        self.data["skipped"] = list(content)
                    elif isinstance(content, dict):
                        # Ensure everything is a list
                        self.data["instagram"] = list(content.get("instagram", []))
                        self.data["tiktok"] = list(content.get("tiktok", []))
                        self.data["skipped"] = list(content.get("skipped", []))
            except Exception as e:
                logger.error(f"Error loading history: {e}")

    def save(self):
        try:
            # Data is already in list format
            with open(self.file_path, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving history: {e}")

    def add(self, video_id, platform="skipped"):
        """
        platform: 'instagram', 'tiktok', or 'skipped'
        """
        if platform in self.data:
            if video_id not in self.data[platform]:
                self.data[platform].append(video_id)
                self.save()
        else:
            logger.error(f"Unknown platform for history: {platform}")

    def exists(self, video_id, platform=None):
        """
        Check if video_id exists. 
        If platform is specified, checks only that list.
        If platform is None, checks if processed at all (in IG OR TT OR Skipped).
        """
        if platform:
            return video_id in self.data.get(platform, [])
        else:
            # Check if it exists in ANY list
            return (video_id in self.data["instagram"] or 
                    video_id in self.data["tiktok"] or 
                    video_id in self.data["skipped"])

    def remove(self, video_id, platform):
        """Removes a video_id from a specific platform history"""
        if platform in self.data:
            if video_id in self.data[platform]:
                self.data[platform].remove(video_id)
                self.save()
                return True
        return False

    def get_recent(self, platform, limit=5):
        """Returns the last N items for a platform (reversed order: newest first)"""
        if platform in self.data and self.data[platform]:
            return self.data[platform][-limit:][::-1]
        return []

