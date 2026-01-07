# Copilot Instructions for TGBOT_YTtoIGTK

## Project Overview
This project is a Telegram Bot that automates the workflow of downloading YouTube Shorts and uploading them to Instagram and TikTok. Access is restricted to specific Telegram User IDs.

### Architecture
- **Entry Point**: `src/bot.py` initializes the `python-telegram-bot` application and registers commands (`/start`, `/fetch`, `/recap`).
- **Handlers**:
  - `src/youtube_handler.py`: Wraps `yt-dlp` to check for new videos and download them.
  - `src/instagram_handler.py`: Wraps `instagrapi` for session management and video uploads.
  - `src/tiktok_handler.py` & `src/upload_video_playwright.py`: Uses `playwright` to automate the TikTok web upload flow via browser injection.
  - `src/history_handler.py`: Manages `history.json` to track processed videos and prevent duplicates.

## Critical Developer Workflows

### Running the Bot
- **Startup**: `python src/bot.py`
- **Configuration**:
  - All secrets must be in `.env`.
  - TikTok connection requires `config/tiktok_cookies.txt` (Netscape format) or `.cookie` pickle file.
  - Instagram session is persisted in `session.json` to avoid repeated login challenges.

### Debugging & Logic
- **Authorization**: Logic is centralized in `is_authorized(user_id)` within `bot.py`. Only users in `.env`'s `ALLOWED_USER_IDS` can interact.
- **YouTube Fetching**: The bot checks the specific `YOUTUBE_CHANNEL_URL` configured in `.env`. It compares video IDs against `history.json`.
- **Upload Automation**:
  - **Instagram**: Uses unofficial API (`instagrapi`). Errors are often due to check-points/challenges. Check logs if login fails.
  - **TikTok**: Uses browser automation (`playwright`). This is fragile and depends on UI selectors. Do not obscure the browser window if running in non-headless mode (default is usually headless for servers, but local debugging might show UI).

## Project Conventions

### Data Persistence (`history.json`)
The history file tracks status to ensure videos aren't reposted.
```json
{
  "instagram": ["video_id_1", "video_id_2"],
  "tiktok": ["video_id_1"],
  "skipped": ["video_id_3"]
}
```
*Always update this file via `HistoryHandler` methods immediately after a successful action.*

### Environment Variables
Use `os.getenv` with defaults where safe, but critical credentials should fail if missing.
- `TELEGRAM_BOT_TOKEN`
- `INSTAGRAM_USERNAME`, `INSTAGRAM_PASSWORD`
- `TIKTOK_COOKIES_PATH`

### Async/Sync Mixing
- `src/bot.py` is async (`async def`, `await`).
- `src/youtube_handler.py` and `instagrapi` calls are synchronous.
- **Pattern**: Blocking IO calls (YouTube download, Instagram upload) are often run directly in async handlers. *Prefer wrapping long-running sync calls in `run_in_executor` to avoid blocking the Telegram bot heartbeat.*

## External Dependencies
- **instagrapi**: Unofficial Instagram API. Treat as unstable.
- **yt_dlp**: Regular updates required to keep up with YouTube changes.
- **playwright**: Must have browsers installed (`playwright install chromium`).

## Common Tasks
- **Adding a new platform**: Create a `src/{platform}_handler.py` class following the pattern of `InstagramHandler`.
- **Updating selectors**: If TikTok upload fails, check `src/upload_video_playwright.py` for outdated CSS/XPath selectors.
