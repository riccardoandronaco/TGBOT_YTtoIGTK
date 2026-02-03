import os
import logging
import asyncio
import subprocess
import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler, Application

from youtube_handler import YouTubeHandler
from instagram_handler import InstagramHandler
from tiktok_handler import TikTokHandler
from history_handler import HistoryHandler

# Load environment variables
load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
INSTAGRAM_USERNAME = os.getenv('INSTAGRAM_USERNAME')
INSTAGRAM_PASSWORD = os.getenv('INSTAGRAM_PASSWORD')
TIKTOK_COOKIES_PATH = os.getenv('TIKTOK_COOKIES_PATH', 'config/tiktok_cookies.txt')
TIKTOK_USERNAME = os.getenv('TIKTOK_USERNAME', 'duodisagio')
ALLOWED_USER_IDS = [int(id.strip()) for id in os.getenv('ALLOWED_USER_IDS', '').split(',') if id.strip()]
DOWNLOAD_PATH = os.getenv('DOWNLOAD_PATH', 'downloads')

YOUTUBE_CHANNEL_URL = os.getenv('YOUTUBE_CHANNEL_URL')

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Handlers
yt_handler = YouTubeHandler(download_path=DOWNLOAD_PATH)
ig_handler = InstagramHandler(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
tiktok_handler = TikTokHandler(cookies_path=TIKTOK_COOKIES_PATH)
history_handler = HistoryHandler()

# Helper to check authorization
def is_authorized(user_id):
    if not ALLOWED_USER_IDS:
        return True # If no list is provided, allow everyone (NOT RECOMMENDED for private bots)
    return user_id in ALLOWED_USER_IDS

# Helper to escape special characters for Telegram Markdown
def escape_md(text):
    """Escape special Markdown characters for Telegram."""
    if not text:
        return ""
    # Characters that need escaping in Markdown: _ * [ ] ( ) ~ ` > # + - = | { } . !
    escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    result = str(text)
    for char in escape_chars:
        result = result.replace(char, f'\\{char}')
    return result

# Helper to truncate caption for TikTok (max 2200 characters)
def truncate_caption(text, max_length=2200):
    """Truncate caption to fit TikTok's limit, preserving hashtags at the end."""
    if len(text) <= max_length:
        return text
    # Find hashtags at the end
    hashtags = ""
    if "#" in text:
        # Extract all hashtags
        parts = text.split("#")
        base_text = parts[0].strip()
        hashtags = " " + " ".join(["#" + p.strip() for p in parts[1:] if p.strip()])
    else:
        base_text = text
    
    # Calculate how much space we have for base text
    available = max_length - len(hashtags) - 3  # -3 for "..."
    if available < 50:
        # Not enough space, just hard truncate
        return text[:max_length-3] + "..."
    
    return base_text[:available] + "..." + hashtags

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Non sei autorizzato ad usare questo bot.")
        return
    await update.message.reply_text("Ciao! Inviami un link di YouTube Short per iniziare, oppure usa /fetch per prendere il prossimo short dal canale.")

async def _fetch_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, platform_filter=None):
    # Determine the message object to reply to
    if update.message:
        message = update.message
    elif update.callback_query and update.callback_query.message:
        message = update.callback_query.message
    else:
        logger.error("No message object found in update")
        return

    if not is_authorized(update.effective_user.id):
        return

    if not YOUTUBE_CHANNEL_URL:
        await message.reply_text("URL del canale YouTube non configurato nel file .env")
        return

    filter_msg = f" ({platform_filter})" if platform_filter else ""
    # Only send "Sto cercando" if it's a new command, not from a button click update (which we handled with edit_text)
    # But checking update.callback_query might be enough.
    # Actually, visual feedback is good. Let's keep it minimal.
    # await message.reply_text(f"Sto cercando il prossimo short non pubblicato{filter_msg}... (potrebbe richiedere qualche secondo)")
    
    try:
        loop = asyncio.get_running_loop()
        # Pass the platform_filter to the handler
        video_url = await loop.run_in_executor(
            None, 
            yt_handler.get_oldest_unprocessed_video, 
            YOUTUBE_CHANNEL_URL, 
            history_handler, 
            platform_filter
        )

        if not video_url:
            await message.reply_text(f"Nessun nuovo video trovato per {platform_filter or 'tutte le piattaforme'}.")
            return

        # Store URL in user_data and the current fetch mode
        context.user_data['found_url'] = video_url
        context.user_data['fetch_mode'] = platform_filter # None, 'instagram', or 'tiktok'

        # Check if file is already downloaded (Smart Cache Pre-Check)
        # Verify if ID is in filename
        vid_id = None
        match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", video_url)
        if match:
            vid_id = match.group(1)
            
        cached_path = None
        if vid_id and os.path.exists("downloads"): 
             # Check if file exists in downloads folder
             for f in os.listdir("downloads"):
                 if vid_id in f and (f.endswith(".mp4") or f.endswith(".mkv") or f.endswith(".webm")):
                     cached_path = os.path.join("downloads", f)
                     break
        
        if cached_path:
            # Skip the "Do you want to download?" question and go straight to process
            await message.reply_text(f"Trovato (CACHED): {video_url}\nProcesso immediato...")
            await process_video_url(update, context, video_url)
        else:
            # Create button to confirm download
            keyboard = [
                [
                    InlineKeyboardButton("Scarica e Anteprima", callback_data='download_found'),
                    InlineKeyboardButton("Salta", callback_data='skip_found')
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await message.reply_text(f"Trovato: {video_url}\nVuoi scaricarlo?", reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error fetching next short: {e}")
        await message.reply_text(f"Errore durante la ricerca: {e}")

async def fetch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    
    keyboard = [
        [InlineKeyboardButton("🌍 Qualsiasi (Non pubblicato)", callback_data='fetch_mode_any')],
        [InlineKeyboardButton("📸 Solo per Instagram", callback_data='fetch_mode_ig')],
        [InlineKeyboardButton("🎵 Solo per TikTok", callback_data='fetch_mode_tiktok')]
    ]
    await update.message.reply_text("🔍 **Che tipo di video cerchi?**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
        
    keyboard = [
        [InlineKeyboardButton("Gestisci Instagram Playlist", callback_data='hist_view_instagram')],
        [InlineKeyboardButton("Gestisci TikTok Playlist", callback_data='hist_view_tiktok')],
        [InlineKeyboardButton("Gestisci Skipped Playlist", callback_data='hist_view_skipped')],
    ]
    await update.message.reply_text("📂 **Gestione Storico**\nScegli la piattaforma per vedere gli ultimi video processati e eventualmente rimuoverli:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def recap_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Non autorizzato.")
        return

    await update.message.reply_text("📊 Raccolta statistiche in corso...")

    try:
        loop = asyncio.get_running_loop()
        
        # 1. YouTube Stats
        yt_subs = "N/A"
        try:
            yt_subs = await loop.run_in_executor(None, yt_handler.get_subscriber_count, YOUTUBE_CHANNEL_URL)
        except Exception as e:
            logger.error(f"YouTube stats error: {e}")
            yt_subs = f"Errore"
        
        # 2. Instagram Stats
        ig_followers = "N/A"
        ig_media_count = "N/A"
        try:
            ig_stats = await loop.run_in_executor(None, ig_handler.get_stats)
            ig_followers = ig_stats.get('followers', "N/A")
            ig_media_count = ig_stats.get('media_count', "N/A")
        except Exception as e:
            logger.error(f"Instagram stats error: {e}")
            ig_followers = "Errore"
            ig_media_count = "Errore"
        
        # 3. TikTok Stats
        tt_followers = "N/A"
        tt_likes = "N/A"
        tt_video_count = "N/A"
        try:
            tt_stats = await loop.run_in_executor(None, tiktok_handler.get_stats, TIKTOK_USERNAME)
            tt_followers = tt_stats.get('followers', "N/A")
            tt_likes = tt_stats.get('likes', "N/A")
            tt_video_count = tt_stats.get('video_count', "N/A")
        except Exception as e:
            logger.error(f"TikTok stats error: {e}")
            tt_followers = "Errore"

        # 4. History Stats
        ig_published = len(history_handler.data.get("instagram", []))
        tt_published = len(history_handler.data.get("tiktok", []))
        skipped = len(history_handler.data.get("skipped", []))

        msg = (
            f"📈 STATISTICHE SOCIAL\n\n"
            f"🟥 YouTube: {yt_subs} Iscritti\n"
            f"🟪 Instagram: {ig_followers} Follower | {ig_media_count} Post\n"
            f"⬛ TikTok: {tt_followers} Follower | {tt_likes} Likes | {tt_video_count} Video\n\n"
            f"📂 STORICO BOT\n"
            f"📸 Instagram: {ig_published} video pubblicati\n"
            f"🎵 TikTok: {tt_published} video pubblicati\n"
            f"⏭️ Skippati: {skipped}"
        )
        await update.message.reply_text(msg)

    except Exception as e:
        logger.error(f"Error in recap: {e}")
        await update.message.reply_text(f"Errore generazione recap: {e}")


async def process_video_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    # Determine the message object to reply to
    if update.message:
        message = update.message
    elif update.callback_query and update.callback_query.message:
        message = update.callback_query.message
    else:
        logger.error("No message object found in update")
        return

    try:
        # Run blocking download in a separate thread to not block the bot
        loop = asyncio.get_running_loop()
        video_info = await loop.run_in_executor(None, yt_handler.download_video, url)
        
        # Store info in user_data context
        video_path = video_info['path']
        context.user_data['video_path'] = video_path
        context.user_data['video_title'] = video_info['title']
        context.user_data['video_description'] = video_info['description']
        context.user_data['video_id'] = video_info['id'] # Store ID for history
        
        is_cached = video_info.get('is_cached', False)
        thumbnail_url = video_info.get('thumbnail')

        # Check existing history to format buttons
        is_on_ig = history_handler.exists(video_info['id'], "instagram")
        is_on_tiktok = history_handler.exists(video_info['id'], "tiktok")

        ig_label = "✅ Su IG" if is_on_ig else "Pubblica su IG"
        tt_label = "✅ Su TikTok" if is_on_tiktok else "Pubblica su TikTok"

        # Create buttons
        keyboard = []
        
        if not (is_on_ig and is_on_tiktok):
             keyboard.append([InlineKeyboardButton("🚀 Pubblica su TUTTI (IG & TikTok)", callback_data='upload_both')])
             
        keyboard.append([
            InlineKeyboardButton(ig_label, callback_data='upload_ig'),
            InlineKeyboardButton(tt_label, callback_data='upload_tiktok')
        ])
        keyboard.append([
            InlineKeyboardButton("Salta / Prossimo", callback_data='skip'),
            InlineKeyboardButton("Annulla", callback_data='cancel')
        ])
        
        # Always show "Manda QUI" button as requested
        keyboard.append([InlineKeyboardButton("📥 Manda QUI (Telegram)", callback_data='send_telegram')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Logic: If cached, send Photo/Text to save bandwidth. If new, send Video.
        if is_cached:
            # Send Photo or Text
            msg_text = f"💾 **Video CACHED** (Già presente nel server)\nTitle: {video_info['title']}\n\nScegli dove pubblicare \n(o clicca 'Manda QUI' per vederlo):"
            if thumbnail_url:
                await message.reply_photo(
                    photo=thumbnail_url,
                    caption=msg_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                await message.reply_text(
                    text=msg_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
        else:
            # Send video preview (New Download)
            with open(video_path, 'rb') as video_file:
                await message.reply_video(
                    video=video_file,
                    caption=f"Video scaricato: {video_info['title']}\n\nScegli dove pubblicare:",
                    reply_markup=reply_markup
                )

    except Exception as e:
        logger.error(f"Error processing link: {e}")
        await message.reply_text(f"Errore durante il download: {e}")

import re

# ... existing code ...

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        # Optional: log unauthorized access attempts
        # logger.warning(f"Unauthorized access attempt from {update.effective_user.id}")
        return

    text = update.message.text
    logger.info(f"Received message: {text}")
    
    # Matches http/https, optional subdomains (www, m, music), youtube.com or youtu.be, and then ANY non-whitespace characters
    # This covers /watch?v=ID, /shorts/ID, /v/ID, and any query parameters
    url_match = re.search(r'(https?://(?:[a-zA-Z0-9-]+\.)?(?:youtube\.com|youtu\.be|music\.youtube\.com)/\S+)', text)
    
    if not url_match:
        # If text contains "youtube.com" or "youtu.be" but regex failed, log it for debugging
        if "youtube.com" in text or "youtu.be" in text:
             logger.warning(f"Message seemed to contain YouTube link but Regex failed. Text: {text}")
             await update.message.reply_text("Ho rilevato YouTube nel testo ma non sono riuscito a estrarre un link valido. Assicurati che inizi con http/https.")
        return

    url = url_match.group(0)
    # Clean trailing punctuation that might have been captured (like . or , at end of sentence)
    url = url.rstrip('.,;!?')
    
    await update.message.reply_text(f"Link trovato: {url}\nSto processando...")
    await process_video_url(update, context, url)

async def show_history_page(query, platform, page):
    page_size = 5
    items, total_count = history_handler.get_paged(platform, page, page_size)
    total_pages = (total_count + page_size - 1) // page_size
    
    # Adjust page if we deleted the last item on the last page
    if page >= total_pages and page > 0:
        page = total_pages - 1
        items, total_count = history_handler.get_paged(platform, page, page_size)

    txt = f"📜 **Storico {platform}** (Pagina {page+1}/{total_pages if total_pages > 0 else 1})\nTotale: {total_count} video\n\n"
    keyboard = []
    
    if not items:
        txt += "_Nessun video trovato._"
    
    for vid in items:
        keyboard.append([InlineKeyboardButton(f"🗑️ Rimuovi {vid}", callback_data=f"hist_del_{platform}_{page}_{vid}")])
    
    # Navigation
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prec.", callback_data=f"hist_view_{platform}_{page-1}"))
    if (page + 1) * page_size < total_count:
        nav_row.append(InlineKeyboardButton("Succ. ➡️", callback_data=f"hist_view_{platform}_{page+1}"))
    
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("❌ Chiudi", callback_data="hist_close")])
    
    await query.edit_message_text(text=txt, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # --- Draft TikTok Count Selection ---
    if query.data.startswith('draft_count_'):
        count = int(query.data.split('_')[2])
        await execute_draft_batch(update, context, count)
        return

    # --- Instagram Batch Count Selection (Step 1: count) ---
    if query.data.startswith('ig_count_'):
        count = int(query.data.split('_')[2])
        # Store count and ask for time
        context.user_data['ig_batch_count'] = count
        keyboard = [
            [
                InlineKeyboardButton("30 min", callback_data='ig_time_30'),
                InlineKeyboardButton("1 ora", callback_data='ig_time_60'),
            ],
            [
                InlineKeyboardButton("2 ore", callback_data='ig_time_120'),
                InlineKeyboardButton("4 ore", callback_data='ig_time_240'),
            ]
        ]
        await query.edit_message_text(
            f"📸 **Instagram Batch Publish**\n\n✅ Video da pubblicare: {count}\n\n⏰ Quanto tempo tra ogni post?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return
    
    # --- Instagram Batch Time Selection (Step 2: time) ---
    if query.data.startswith('ig_time_'):
        wait_minutes = int(query.data.split('_')[2])
        count = context.user_data.get('ig_batch_count', 1)
        await execute_ig_batch(update, context, count, wait_minutes)
        return

    # --- History Management ---
    if query.data.startswith('hist_view_'):
        parts = query.data.split('_')
        platform = parts[2]
        page = int(parts[3]) if len(parts) > 3 else 0
        await show_history_page(query, platform, page)
        return

    if query.data.startswith('hist_del_'):
        parts = query.data.split('_')
        # format: hist_del_platform_page_vid
        platform = parts[2]
        page = int(parts[3])
        vid = parts[4]
        
        if history_handler.remove(vid, platform):
             await query.answer(f"Video {vid} rimosso da {platform}!")
        else:
            await query.answer("Errore rimozione o già rimosso.")
        
        # Refresh view
        await show_history_page(query, platform, page)
        return

    if query.data == 'hist_close':
        await query.edit_message_text("Operazione completata.")
        return
    # --- Fetch Mode Selection ---
    if query.data.startswith('fetch_mode_'):
        mode = query.data.split('_')[2]
        filter_p = None
        mode_text = "TUTTI"
        
        if mode == 'ig': 
            filter_p = 'instagram'
            mode_text = "INSTAGRAM"
        elif mode == 'tiktok': 
            filter_p = 'tiktok'
            mode_text = "TIKTOK"
            
        await query.edit_message_text(f"🔎 Avvio ricerca per: {mode_text}...")
        # Since _fetch_logic sends a "Sto cercando" message using reply_text, it works fine.
        # But we might want to avoid double loading messages.
        # _fetch_logic uses 'reply_text' which replies to the original command message usually.
        # Here we are in a callback query from a bot message. 
        # _fetch_logic will use query.message.
        await _fetch_logic(update, context, filter_p)
        return
    # ----------------------------

    # --------------------------

    # Handle fetch flow buttons
    if query.data == 'download_found':
        url = context.user_data.get('found_url')
        if not url:
            await query.edit_message_text("Errore: URL perso. Riprova con /fetch.")
            return
        
        await query.edit_message_text(f"Scarico il video: {url} ...")
        await process_video_url(update, context, url)
        return

    if query.data == 'skip_found':
        url = context.user_data.get('found_url')
        fetch_mode = context.user_data.get('fetch_mode') # 'instagram', 'tiktok', or None
        
        if url:
            # Extract ID
            match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)
            if match:
                vid = match.group(1)
                
                # Intelligent skip logic
                skip_target = "skipped" # Default
                if fetch_mode == 'instagram':
                    skip_target = "instagram"
                elif fetch_mode == 'tiktok':
                    skip_target = "tiktok"
                
                history_handler.add(vid, skip_target)
                
                target_msg = skip_target if skip_target != 'skipped' else 'generale'
                await query.edit_message_text(f"Video aggiunto a history {target_msg}. Cerco il prossimo...")
                await _fetch_logic(update, context, fetch_mode)
                return

        await query.edit_message_text("Operazione annullata. Usa /fetch per cercare di nuovo.")
        return

    path = context.user_data.get('video_path')
    video_id = context.user_data.get('video_id')

    if query.data == 'cancel':
        await query.edit_message_caption(caption="Operazione annullata.")
        # Optionally delete the file
        if path and os.path.exists(path):
            os.remove(path)
        return

    if query.data == 'skip':
        fetch_mode = context.user_data.get('fetch_mode') # 'instagram', 'tiktok', or None
        
        if video_id:
            # Intelligent skip logic for 'skip' button too
            skip_target = "skipped" # Default
            if fetch_mode == 'instagram':
                skip_target = "instagram"
            elif fetch_mode == 'tiktok':
                skip_target = "tiktok"
            
            history_handler.add(video_id, skip_target)
            target_msg = skip_target if skip_target != 'skipped' else 'generale'
            await query.edit_message_caption(caption=f"Saltato (History: {target_msg}). Procedo al prossimo...")
        else:
            await query.edit_message_caption(caption="Video saltato.")
        
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except PermissionError:
                # Retry once after short delay
                import time
                time.sleep(1)
                try:
                    os.remove(path)
                except Exception as e:
                    logger.error(f"Could not delete file {path}: {e}")
            except Exception as e:
                 logger.error(f"Error deleting file {path}: {e}")
            
        # Use the correct internal function with the current mode
        await _fetch_logic(update, context, fetch_mode)
        return
    
    # Helper to refresh keyboard
    async def refresh_keyboard(msg_caption="Scegli dove pubblicare:"):
        is_on_ig = history_handler.exists(video_id, "instagram")
        is_on_tiktok = history_handler.exists(video_id, "tiktok")
        
        ig_label = "✅ Su IG" if is_on_ig else "Pubblica su IG"
        tt_label = "✅ Su TikTok" if is_on_tiktok else "Pubblica su TikTok"

        keyboard = []
        
        # Add main button only if not fully published
        if not (is_on_ig and is_on_tiktok):
             keyboard.append([InlineKeyboardButton("🚀 Pubblica su TUTTI (IG & TikTok)", callback_data='upload_both')])

        keyboard.append([
            InlineKeyboardButton(ig_label, callback_data='upload_ig'),
            InlineKeyboardButton(tt_label, callback_data='upload_tiktok')
        ])
        keyboard.append([
            InlineKeyboardButton("Salta / Prossimo", callback_data='skip'),
            InlineKeyboardButton("Annulla", callback_data='cancel')
        ])
        keyboard.append([InlineKeyboardButton("📥 Manda QUI (Telegram)", callback_data='send_telegram')])
        
        await query.edit_message_caption(caption=msg_caption, reply_markup=InlineKeyboardMarkup(keyboard))



    if query.data == 'send_telegram':
        if path and os.path.exists(path):
            try:
                await query.answer("Invio video in corso...")
                await context.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=open(path, 'rb'),
                    caption=f"{context.user_data.get('video_title', '')}\nID: {video_id}",
                    read_timeout=120, 
                    write_timeout=120, 
                    pool_timeout=120
                )
            except Exception as e:
                logger.error(f"Error sending video to telegram: {e}")
                await query.answer(f"Errore invio: {e}")
        else:
            await query.answer("File video non trovato!")
        return

    if query.data == 'upload_both':
        title = context.user_data.get('video_title')
        if history_handler.exists(video_id, "instagram") and history_handler.exists(video_id, "tiktok"):
             await query.answer("Video già pubblicato ovunque!")
             return

        if not path or not os.path.exists(path):
            await query.answer("File video non trovato!", show_alert=True)
            return

        await query.edit_message_caption(caption="🚀 Inizio pubblicazione su TUTTI i canali...")
        
        loop = asyncio.get_running_loop()
        caption = truncate_caption(f"{title} #shorts")
        errors = []

        # 1. Instagram
        if not history_handler.exists(video_id, "instagram"):
            try:
                await query.edit_message_caption(caption="1/2 📷 Pubblicazione su Instagram...")
                await loop.run_in_executor(None, ig_handler.login)
                await loop.run_in_executor(None, ig_handler.upload_video, path, caption)
                history_handler.add(video_id, "instagram")
            except Exception as e:
                logger.error(f"Error uploading to IG: {e}")
                errors.append(f"IG: {e}")
        
        # 2. TikTok
        if not history_handler.exists(video_id, "tiktok"):
            try:
                await query.edit_message_caption(caption="2/2 🎵 Pubblicazione su TikTok...\n(Attendi avanzamento)")
                
                # Setup specific folder for screenshots of this session
                debug_dir = os.path.join(os.getcwd(), "debug_screens")
                os.makedirs(debug_dir, exist_ok=True)
                # clear old screenshots
                for f in os.listdir(debug_dir):
                    os.remove(os.path.join(debug_dir, f))

                # Callback to update message and send screenshots
                def progress_callback(status_text, screenshot_path=None):
                     future = asyncio.run_coroutine_threadsafe(
                        _update_ui_with_screenshot(query, loop, status_text, screenshot_path),
                        loop
                     )
                     try:
                         future.result(timeout=2)
                     except:
                         pass

                # Async helper to handle UI updates + potential photo sending
                async def _update_ui_with_screenshot(q, l, text, img_path):
                     try:
                         await q.edit_message_caption(caption=f"2/2 🎵 TikTok...\n📝 {text}")
                         if img_path and os.path.exists(img_path):
                             with open(img_path, 'rb') as photo:
                                 await context.bot.send_photo(chat_id=q.message.chat_id, photo=photo, caption=f"📸 {text}")
                     except Exception as e:
                         pass

                result = await loop.run_in_executor(None, lambda: tiktok_handler.upload_video(path, caption, status_callback=progress_callback))
                
                # Unpack result tuple or handle boolean legacy
                if isinstance(result, tuple):
                    success, msg = result
                else:
                    success = result
                    msg = "Unknown"

                if success:
                    history_handler.add(video_id, "tiktok")
                else:
                    errors.append(f"TikTok: Upload fallito ({msg})")

            except Exception as e:
                logger.error(f"Error uploading to TikTok: {e}")
                errors.append(f"TikTok: {e}")


        # Final Status
        if not errors:
            await refresh_keyboard(msg_caption=f"✅ Pubblicato ovunque con successo!\n{title}")
        else:
            error_msg = "\n".join(errors)
            await refresh_keyboard(msg_caption=f"⚠️ Alcuni caricamenti sono falliti:\n{error_msg}")
        return

    if query.data == 'upload_ig':
        title = context.user_data.get('video_title')
        if history_handler.exists(video_id, "instagram"):
            await query.answer("Già pubblicato su Instagram!")
            return

        if not path or not os.path.exists(path):
            await query.answer("File video non trovato!", show_alert=True)
            return

        await query.edit_message_caption(caption="⏳ Caricamento su Instagram in corso...")

        try:
            loop = asyncio.get_running_loop()
            caption = truncate_caption(f"{title} #shorts")
            
            # Ensure login and Upload
            await loop.run_in_executor(None, ig_handler.login)
            await loop.run_in_executor(None, ig_handler.upload_video, path, caption)
            
            history_handler.add(video_id, "instagram")
            await refresh_keyboard(msg_caption=f"✅ Pubblicato su Instagram!\n{title}")

        except Exception as e:
            logger.error(f"Error uploading to IG: {e}")
            await refresh_keyboard(msg_caption=f"❌ Errore Instagram: {e}")

        return

    if query.data == 'upload_tiktok':
        title = context.user_data.get('video_title')
        if history_handler.exists(video_id, "tiktok"):
            await query.answer("Già pubblicato su TikTok!")
            return

        if not path or not os.path.exists(path):
            await query.answer("File video non trovato!", show_alert=True)
            return

        await query.edit_message_caption(caption="⏳ Caricamento su TikTok in corso...\n(Attendi avanzamento)")

        try:
            loop = asyncio.get_running_loop()
            caption = truncate_caption(f"{title} #shorts")

            # Callback to update message and send debug screenshots
            def progress_callback(status_text, screenshot_path=None):
                    async def update_ui():
                        try:
                            # Update text
                            await query.edit_message_caption(caption=f"⏳ Caricamento su TikTok in corso...\n📝 {status_text}")
                            
                            # Send screenshot if provided
                            if screenshot_path and os.path.exists(screenshot_path):
                                try:
                                    # Send as photo, separate from the edit_caption (which is editing the original message)
                                    # We send a NEW message with the photo so we don't destroy the keyboard on the original
                                    with open(screenshot_path, 'rb') as photo:
                                        await context.bot.send_photo(
                                            chat_id=update.effective_chat.id, 
                                            photo=photo, 
                                            caption=f"📸 STATUS: {status_text}"
                                        )
                                    # We don't delete immediately to allow debugging if needed, 
                                    # or we can rely on standard cleanup. 
                                    # For now, let's keep it or maybe deleting it prevents disk fill up?
                                    # Let's delete it to be safe on RPi SD card
                                    try: 
                                        os.remove(screenshot_path)
                                    except: pass
                                except Exception as e_img:
                                    logger.error(f"Failed to send status screenshot: {e_img}")

                        except Exception as e_ui:
                            logger.error(f"UI Update failed: {e_ui}")

                    future = asyncio.run_coroutine_threadsafe(update_ui(), loop)
                    try:
                        future.result(timeout=10) # 10s timeout for media upload
                    except:
                        pass

            # Upload to TikTok
            # result is now (found_bool, message_str) from updated handler logic
            result = await loop.run_in_executor(None, lambda: tiktok_handler.upload_video(path, caption, status_callback=progress_callback))
            
            # Unpack result tuple or handle boolean legacy
            if isinstance(result, tuple):
                success, msg = result
            else:
                success = result
                msg = "Unknown status"

            if success:
                history_handler.add(video_id, "tiktok")
                await refresh_keyboard(msg_caption=f"✅ Pubblicato su TikTok!\n{title}\n📝 {msg}")
            else:
                await refresh_keyboard(msg_caption=f"⚠️ Upload TikTok fallito: {msg}")
                
                # Check for debug screenshots in debug_screens folder
                debug_frames = ["debug_upload_fail.png", "debug_post_disabled.png", "debug_login_redirect.png", "debug_profile_check.png", "debug_upload_error.png", "err_input_file.png", "err_upload_check.png", "warn_upload_timeout.png"]
                
                debug_dir = os.path.join(os.getcwd(), "debug_screens")
                
                for img_name in debug_frames:
                    img_path = os.path.join(debug_dir, img_name)
                    if os.path.exists(img_path):
                        try:
                            # Send photo using FS path
                            with open(img_path, 'rb') as photo:
                                await context.bot.send_photo(
                                    chat_id=update.effective_chat.id, 
                                    photo=photo,
                                    caption=f"📸 Debug: {img_name}"
                                )
                            # Cleanup
                            try:
                                os.remove(img_path)
                            except: pass
                        except Exception as img_e:
                            logger.error(f"Failed to send debug screenshot {img_name}: {img_e}")

        except Exception as e:
            logger.error(f"Error uploading to TikTok: {e}")
            await refresh_keyboard(msg_caption=f"❌ Errore TikTok: {e}")
            
            # Send debug screenshot if available (Exception case)
            debug_fail_path = os.path.join(os.getcwd(), "debug_screens", "debug_upload_fail.png")
            if os.path.exists(debug_fail_path):
                try:
                    with open(debug_fail_path, 'rb') as photo:
                        await context.bot.send_photo(
                            chat_id=update.effective_chat.id, 
                            photo=photo,
                            caption="📸 Screenshot Errore TikTok (Exception)"
                        )
                    os.remove(debug_fail_path)
                except Exception as img_e:
                    logger.error(f"Failed to send debug screenshot: {img_e}")
        
        return

async def reboot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reboots the host system (Raspberry Pi) - Admin only"""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Non sei autorizzato a riavviare il sistema.")
        return

    await update.message.reply_text("🔄 Riavvio del sistema (Raspberry Pi) in corso... Il bot sarà offline per un paio di minuti.")
    logger.warning(f"Reboot command issued by user {user_id}")
    
    # Give time for the message to send
    await asyncio.sleep(2)
    
    # Execute reboot
    try:
        # Works on Linux/Raspberry Pi
        os.system("sudo reboot")
    except Exception as e:
        await update.message.reply_text(f"❌ Errore durante il comando di riavvio: {e}")

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Checks Network/TikTok Visibility from the Bot's perspective."""
    user_id = update.effective_user.id
    if not is_authorized(user_id): return
    
    msg = await update.message.reply_text("🕵️ Controllo connettività e Login TikTok... (apertura browser headless)")
    
    try:
        loop = asyncio.get_running_loop()
        path, info = await loop.run_in_executor(None, tiktok_handler.check_connection)
        
        caption = f"📊 **Diagnostica Raspberry**\n\n"
        caption += f"🌐 IP rilevato: `{info.get('ip', 'N/A')}`\n"
        caption += f"👤 Login: {info.get('login', 'N/A')}\n"
        caption += f"🍪 Cookies L: {info.get('cookies_loaded', '0')}\n"
        caption += f"🏠 Titolo: `{info.get('title', 'N/A')}`\n"
        
        if "error" in info:
            caption += f"❌ Errore: {info['error']}"
        else:
            caption += "✅ Check completato."
            
        with open(path, 'rb') as p:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=p, caption=caption, parse_mode="Markdown")
        
        await msg.delete() # clean up loading msg
        
    except Exception as e:
        await msg.edit_text(f"❌ Errore Critico Diagnostica: {e}")


async def reset_instagram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forza un reset della sessione Instagram (risolve errori 403)."""
    user_id = update.effective_user.id
    if not is_authorized(user_id): return
    
    msg = await update.message.reply_text("🔄 Reset sessione Instagram in corso...")
    
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, ig_handler.fresh_login)
        await msg.edit_text("✅ Sessione Instagram resettata con successo!\nOra puoi riprovare a pubblicare.")
    except Exception as e:
        logger.error(f"Reset IG failed: {e}")
        await msg.edit_text(f"❌ Errore reset Instagram: {e}")


async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Updates the bot via git pull and reboots."""
    user_id = update.effective_user.id
    if not is_authorized(user_id): return
    
    await update.message.reply_text("⬇️ Esecuzione `git pull` in corso...", parse_mode="Markdown")
    
    try:
        # Run git pull
        # Capture output to show the user what changed
        loop = asyncio.get_running_loop()
        
        def run_git():
            return subprocess.check_output(["git", "pull"], stderr=subprocess.STDOUT, text=True)
            
        result = await loop.run_in_executor(None, run_git)
        
        if "Already up to date" in result:
             await update.message.reply_text(f"✅ **Il sistema è aggiornato.**\nNessuna modifica rilevata.", parse_mode="Markdown")
             return
             
        # If changed, schedule reboot
        await update.message.reply_text(f"✅ **Aggiornamento completato!**\n\nOutput:\n`{result}`\n\n🔄 **Riavvio del sistema in corso...** (attendi 2-3 minuti)", parse_mode="Markdown")
        
        # Give time for message to fly out
        await asyncio.sleep(3)
        
        # Reboot
        os.system("sudo reboot")
        
    except subprocess.CalledProcessError as e:
        await update.message.reply_text(f"❌ Errore Git (Exit Code {e.returncode}):\n`{e.output}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Errore generico: {e}")

async def draft_tiktok_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mostra menu per selezionare quanti video caricare come Draft su TikTok.
    """
    if not is_authorized(update.effective_user.id):
        return
    
    keyboard = [
        [
            InlineKeyboardButton("1️⃣", callback_data='draft_count_1'),
            InlineKeyboardButton("3️⃣", callback_data='draft_count_3'),
            InlineKeyboardButton("5️⃣", callback_data='draft_count_5'),
        ],
        [
            InlineKeyboardButton("🔟", callback_data='draft_count_10'),
            InlineKeyboardButton("2️⃣0️⃣", callback_data='draft_count_20'),
        ]
    ]
    await update.message.reply_text(
        "🎬 **TikTok Draft Batch**\n\nQuanti video vuoi caricare come Draft?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def execute_draft_batch(update: Update, context: ContextTypes.DEFAULT_TYPE, max_uploads: int):
    """
    Esegue il batch upload di video su TikTok come Draft.
    Un messaggio per video YouTube + un messaggio stato che si aggiorna.
    """
    query = update.callback_query
    chat_id = update.effective_chat.id
    
    await query.edit_message_text(f"🎬 **TikTok Draft Batch**\nCarico {max_uploads} video come Draft...", parse_mode='Markdown')
    
    try:
        loop = asyncio.get_running_loop()
        
        success_ids = []
        fail_ids = []
        
        for i in range(1, max_uploads + 1):
            # Find oldest unprocessed video for TikTok
            video_url = await loop.run_in_executor(
                None,
                yt_handler.get_oldest_unprocessed_video,
                YOUTUBE_CHANNEL_URL,
                history_handler,
                "tiktok"
            )
            
            if not video_url:
                await context.bot.send_message(chat_id, "✅ Nessun altro video da caricare!")
                break
            
            # Send YouTube link message (no Markdown to avoid URL parsing issues)
            await context.bot.send_message(
                chat_id, 
                f"🎬 [{i}/{max_uploads}] {video_url}",
                disable_web_page_preview=False
            )
            
            # Create status message that will be updated
            status_msg = await context.bot.send_message(chat_id, "⏳ Inizializzazione...")
            
            video_id = None
            try:
                # Download video
                await status_msg.edit_text("📥 Download in corso...")
                
                video_info = await loop.run_in_executor(None, yt_handler.download_video, video_url)
                
                video_path = video_info.get('path')
                video_id = video_info.get('id')
                title = video_info.get('title', 'Unknown')
                
                if not video_path or not os.path.exists(video_path):
                    await status_msg.edit_text(f"❌ Download fallito!")
                    if video_id:
                        fail_ids.append(video_id)
                    continue
                
                await status_msg.edit_text(f"📦 Scaricato: {title[:50]}...")
                
                # Caption
                caption = truncate_caption(f"{title} #shorts")
                
                # Status callback - updates the same message
                last_status = {"text": ""}
                def status_cb(msg, screenshot=None):
                    if msg != last_status["text"]:
                        last_status["text"] = msg
                        try:
                            asyncio.run_coroutine_threadsafe(
                                status_msg.edit_text(f"📤 Upload TikTok...\n📝 {msg}"),
                                loop
                            )
                        except:
                            pass
                
                # Upload to TikTok
                await status_msg.edit_text("📤 Caricamento su TikTok Draft...")
                
                result = await loop.run_in_executor(
                    None,
                    lambda: tiktok_handler.upload_video(video_path, caption, status_callback=status_cb)
                )
                
                if isinstance(result, tuple):
                    success, msg = result
                else:
                    success = result
                    msg = "Unknown"
                
                if success:
                    history_handler.add(video_id, "tiktok")
                    await status_msg.edit_text(f"✅ Salvato come Draft!\n{video_id}")
                    success_ids.append(video_id)
                else:
                    await status_msg.edit_text(f"❌ Errore: {msg}")
                    fail_ids.append(video_id)
                
                # Small pause between uploads
                if i < max_uploads:
                    await asyncio.sleep(2)
                    
            except Exception as e:
                logger.error(f"Draft batch error for video: {e}")
                await status_msg.edit_text(f"❌ Eccezione: {e}")
                if video_id:
                    fail_ids.append(video_id)
        
        # Final report with video IDs (no Markdown to avoid issues with special chars in IDs)
        report = "🏁 Draft Batch Completato\n\n"
        report += f"✅ Successi: {len(success_ids)}\n"
        if success_ids:
            for vid in success_ids:
                report += f"   • {vid}\n"
        
        report += f"\n❌ Falliti: {len(fail_ids)}\n"
        if fail_ids:
            for vid in fail_ids:
                report += f"   • {vid}\n"
        
        await context.bot.send_message(chat_id, report)
        
    except Exception as e:
        logger.error(f"Draft batch critical error: {e}")
        await context.bot.send_message(chat_id, f"❌ Errore critico: {e}")


async def batch_instagram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mostra menu per selezionare quanti video pubblicare su Instagram.
    """
    if not is_authorized(update.effective_user.id):
        return
    
    keyboard = [
        [
            InlineKeyboardButton("1️⃣", callback_data='ig_count_1'),
            InlineKeyboardButton("2️⃣", callback_data='ig_count_2'),
            InlineKeyboardButton("3️⃣", callback_data='ig_count_3'),
            InlineKeyboardButton("5️⃣", callback_data='ig_count_5'),
        ]
    ]
    await update.message.reply_text(
        "📸 **Instagram Batch Publish**\n\nQuanti video vuoi pubblicare?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def execute_ig_batch(update: Update, context: ContextTypes.DEFAULT_TYPE, max_uploads: int, wait_minutes: int = 60):
    """
    Esegue il batch publish di video su Instagram.
    Attende wait_minutes minuti tra un video e l'altro.
    """
    query = update.callback_query
    chat_id = update.effective_chat.id
    
    # Format wait time for display
    if wait_minutes >= 60:
        wait_display = f"{wait_minutes // 60} ore" if wait_minutes > 60 else "1 ora"
    else:
        wait_display = f"{wait_minutes} min"
    
    await query.edit_message_text(f"📸 Instagram Batch Publish\nCarico {max_uploads} video (attesa {wait_display} tra post)...")
    
    try:
        loop = asyncio.get_running_loop()
        
        # Login to Instagram once at the start
        await context.bot.send_message(chat_id, "🔐 Login Instagram...")
        await loop.run_in_executor(None, ig_handler.login)
        
        success_ids = []
        fail_ids = []
        
        for i in range(1, max_uploads + 1):
            # Find oldest unprocessed video for Instagram
            video_url = await loop.run_in_executor(
                None,
                yt_handler.get_oldest_unprocessed_video,
                YOUTUBE_CHANNEL_URL,
                history_handler,
                "instagram"
            )
            
            if not video_url:
                await context.bot.send_message(chat_id, "✅ Nessun altro video da caricare!")
                break
            
            # Send YouTube link message
            await context.bot.send_message(
                chat_id, 
                f"📸 [{i}/{max_uploads}] {video_url}",
                disable_web_page_preview=False
            )
            
            # Create status message that will be updated
            status_msg = await context.bot.send_message(chat_id, "⏳ Inizializzazione...")
            
            video_id = None
            try:
                # Download video
                await status_msg.edit_text("📥 Download in corso...")
                
                video_info = await loop.run_in_executor(None, yt_handler.download_video, video_url)
                
                video_path = video_info.get('path')
                video_id = video_info.get('id')
                title = video_info.get('title', 'Unknown')
                
                if not video_path or not os.path.exists(video_path):
                    await status_msg.edit_text("❌ Download fallito!")
                    if video_id:
                        fail_ids.append(video_id)
                    continue
                
                await status_msg.edit_text(f"📦 Scaricato: {title[:50]}...")
                
                # Caption
                caption = f"{title} #shorts"
                
                # Upload to Instagram
                await status_msg.edit_text("📤 Caricamento su Instagram...")
                
                await loop.run_in_executor(None, ig_handler.upload_video, video_path, caption)
                
                history_handler.add(video_id, "instagram")
                await status_msg.edit_text(f"✅ Pubblicato su Instagram!\n{video_id}")
                success_ids.append(video_id)
                
                # Wait 10 minutes before next upload (except for last one)
                if i < max_uploads:
                    await status_msg.edit_text(f"✅ Pubblicato!\n⏰ Attendo {wait_minutes} minuti...")
                    
                    # Countdown timer
                    for remaining in range(wait_minutes, 0, -1):
                        await asyncio.sleep(60)  # 1 minute
                        try:
                            await status_msg.edit_text(f"✅ Pubblicato!\n⏰ Prossimo video tra {remaining-1} minuti...")
                        except:
                            pass
                    
            except Exception as e:
                logger.error(f"IG batch error for video: {e}")
                await status_msg.edit_text(f"❌ Eccezione: {e}")
                if video_id:
                    fail_ids.append(video_id)
        
        # Final report
        report = "🏁 Instagram Batch Completato\n\n"
        report += f"✅ Successi: {len(success_ids)}\n"
        if success_ids:
            for vid in success_ids:
                report += f"   • {vid}\n"
        
        report += f"\n❌ Falliti: {len(fail_ids)}\n"
        if fail_ids:
            for vid in fail_ids:
                report += f"   • {vid}\n"
        
        await context.bot.send_message(chat_id, report)
        
    except Exception as e:
        logger.error(f"IG batch critical error: {e}")
        await context.bot.send_message(chat_id, f"❌ Errore critico: {e}")


async def clear_cache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return

    try:
        downloads_dir = DOWNLOAD_PATH
        if not os.path.exists(downloads_dir):
             await update.message.reply_text("Cartella downloads non esistente.")
             return

        files = os.listdir(downloads_dir)
        count = 0
        deleted_size_mb = 0
        
        for f in files:
            file_path = os.path.join(downloads_dir, f)
            try:
                if os.path.isfile(file_path):
                    size = os.path.getsize(file_path)
                    os.remove(file_path)
                    count += 1
                    deleted_size_mb += size / (1024 * 1024)
            except Exception as e:
                logger.error(f"Failed to delete {file_path}: {e}")
        
        await update.message.reply_text(
            f"🗑️ **Cache Svuotata**\n"
            f"File cancellati: {count}\n"
            f"Spazio liberato: {deleted_size_mb:.2f} MB\n\n"
            f"I video verranno riscaricati col nuovo codec al prossimo tentativo."
        )
    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
        await update.message.reply_text(f"Errore pulizia cache: {e}")

async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("start", "Avvia il bot"),
        BotCommand("fetch", "Cerca nuovo short (Menu)"),
        BotCommand("drafttiktok", "Batch TikTok Draft"),
        BotCommand("batchig", "Batch Instagram Publish"),
        BotCommand("resetig", "Reset sessione Instagram"),
        BotCommand("history", "Gestisci storico video"),
        BotCommand("recap", "Visualizza statistiche"),
        BotCommand("clearcache", "Svuota cartella download"),
        BotCommand("check", "Test Connettività/Ban"),
        BotCommand("update", "Aggiorna bot e Riavvia"),
        BotCommand("reboot", "Riavvia Raspberry Pi"),
    ])

if __name__ == '__main__':
    if not TELEGRAM_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not found in .env")
        exit(1)

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('fetch', fetch_command))
    application.add_handler(CommandHandler('clearcache', clear_cache))
    application.add_handler(CommandHandler('drafttiktok', draft_tiktok_batch))
    application.add_handler(CommandHandler('batchig', batch_instagram))
    application.add_handler(CommandHandler('history', history_command))
    application.add_handler(CommandHandler('reboot', reboot_command))
    application.add_handler(CommandHandler('check', check_command))
    application.add_handler(CommandHandler('resetig', reset_instagram))
    application.add_handler(CommandHandler('update', update_command))
    application.add_handler(CommandHandler('recap', recap_stats))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    application.add_handler(CallbackQueryHandler(button_click))

    print("Bot is running...")
    application.run_polling()
