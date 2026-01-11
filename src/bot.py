import os
import logging
import asyncio
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
        yt_subs = await loop.run_in_executor(None, yt_handler.get_subscriber_count, YOUTUBE_CHANNEL_URL)
        
        # 2. Instagram Stats (using cached session or public fetch)
        ig_stats = await loop.run_in_executor(None, ig_handler.get_stats)
        ig_followers = ig_stats.get('followers', "N/A")
        ig_media_count = ig_stats.get('media_count', "N/A")
        
        # 3. TikTok Stats
        tt_stats = await loop.run_in_executor(None, tiktok_handler.get_stats, TIKTOK_USERNAME)
        tt_followers = tt_stats.get('followers', "N/A")
        tt_likes = tt_stats.get('likes', "N/A")
        tt_video_count = tt_stats.get('video_count', "N/A")

        msg = (
            f"📈 **STATISTICHE SOCIAL**\n\n"
            f"🟥 **YouTube**: {yt_subs} Iscritti\n"
            f"🟪 **Instagram**: {ig_followers} Follower | {ig_media_count} Post\n"
            f"⬛ **TikTok**: {tt_followers} Follower | {tt_likes} Likes | {tt_video_count} Video"
        )
        await update.message.reply_text(msg, parse_mode='Markdown')

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
        context.user_data['video_path'] = video_info['path']
        context.user_data['video_title'] = video_info['title']
        context.user_data['video_description'] = video_info['description']
        context.user_data['video_id'] = video_info['id'] # Store ID for history

        # Check existing history to format buttons
        is_on_ig = history_handler.exists(video_info['id'], "instagram")
        is_on_tiktok = history_handler.exists(video_info['id'], "tiktok")

        ig_label = "✅ Su IG" if is_on_ig else "Pubblica su IG"
        tt_label = "✅ Su TikTok" if is_on_tiktok else "Pubblica su TikTok"

        # Create buttons
        keyboard = [
            [
                InlineKeyboardButton("🚀 Pubblica su TUTTI (IG & TikTok)", callback_data='upload_both')
            ],
            [
                InlineKeyboardButton(ig_label, callback_data='upload_ig'),
                InlineKeyboardButton(tt_label, callback_data='upload_tiktok')
            ],
            [
                InlineKeyboardButton("Salta / Prossimo", callback_data='skip'),
                InlineKeyboardButton("Annulla", callback_data='cancel')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Send video preview
        with open(video_info['path'], 'rb') as video_file:
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
    
    await update.message.reply_text(f"Link trovato: {url}\nSto scaricando il video... attendi.")
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
        if video_id:
            # Mark as skipped if the user intentionally skips it to move next
            # But maybe they just uploaded to one platform and want to move next?
            # If uploaded to at least one, we don't need to add to 'skipped' implicitly, 
            # OR we can just add to skipped to ensure it doesn't show up again in fetch logic.
            history_handler.add(video_id, "skipped")
            await query.edit_message_caption(caption="Procedo al prossimo video...")
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
            
        await fetch_next_short(update, context)
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
        await query.edit_message_caption(caption=msg_caption, reply_markup=InlineKeyboardMarkup(keyboard))



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
        caption = f"{title} #shorts"
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
                
                # Callback to update message
                def progress_callback(status_text):
                     future = asyncio.run_coroutine_threadsafe(
                        query.edit_message_caption(caption=f"2/2 🎵 Pubblicazione su TikTok...\n📝 {status_text}"),
                        loop
                     )
                     try:
                         future.result(timeout=1)
                     except:
                         pass

                result = await loop.run_in_executor(None, lambda: tiktok_handler.upload_video(path, caption, status_callback=progress_callback))
                if result:
                    history_handler.add(video_id, "tiktok")
                else:
                    errors.append("TikTok: Upload fallito/non confermato")
                    # Check for debug screenshot
                    if os.path.exists("debug_upload_fail.png"):
                        try:
                            await context.bot.send_photo(
                                chat_id=update.effective_chat.id, 
                                photo=open("debug_upload_fail.png", 'rb'),
                                caption="📸 Screenshot Fallimento TikTok"
                            )
                            os.remove("debug_upload_fail.png")
                        except Exception as img_e:
                            logger.error(f"Failed to send debug screenshot: {img_e}")

            except Exception as e:
                logger.error(f"Error uploading to TikTok: {e}")
                errors.append(f"TikTok: {e}")
                # Check for debug screenshot on exception
                if os.path.exists("debug_upload_fail.png"):
                    try:
                        await context.bot.send_photo(
                            chat_id=update.effective_chat.id, 
                            photo=open("debug_upload_fail.png", 'rb'),
                            caption="📸 Screenshot Errore TikTok"
                        )
                        os.remove("debug_upload_fail.png")
                    except Exception as img_e:
                        logger.error(f"Failed to send debug screenshot: {img_e}")

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
            caption = f"{title} #shorts"
            
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
            caption = f"{title} #shorts"

            # Callback to update message
            def progress_callback(status_text):
                    future = asyncio.run_coroutine_threadsafe(
                    query.edit_message_caption(caption=f"⏳ Caricamento su TikTok in corso...\n📝 {status_text}"),
                    loop
                    )
                    try:
                        future.result(timeout=1)
                    except:
                        pass

            # Upload to TikTok
            result = await loop.run_in_executor(None, lambda: tiktok_handler.upload_video(path, caption, status_callback=progress_callback))
            
            if result:
                history_handler.add(video_id, "tiktok")
                await refresh_keyboard(msg_caption=f"✅ Pubblicato su TikTok!\n{title}")
            else:
                await refresh_keyboard(msg_caption=f"⚠️ Upload TikTok fallito (o non confermato).")
                
                # Check for debug screenshot even if False returned without exception
                if os.path.exists("debug_upload_fail.png"):
                    try:
                        await context.bot.send_photo(
                            chat_id=update.effective_chat.id, 
                            photo=open("debug_upload_fail.png", 'rb'),
                            caption="📸 Screenshot Fallimento TikTok"
                        )
                        os.remove("debug_upload_fail.png")
                    except Exception as img_e:
                        logger.error(f"Failed to send debug screenshot: {img_e}")

        except Exception as e:
            logger.error(f"Error uploading to TikTok: {e}")
            await refresh_keyboard(msg_caption=f"❌ Errore TikTok: {e}")
            
            # Send debug screenshot if available
            if os.path.exists("debug_upload_fail.png"):
                try:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id, 
                        photo=open("debug_upload_fail.png", 'rb'),
                        caption="📸 Screenshot Errore TikTok (Exception)"
                    )
                    os.remove("debug_upload_fail.png")
                except Exception as img_e:
                    logger.error(f"Failed to send debug screenshot: {img_e}")
        
        return

async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("start", "Avvia il bot"),
        BotCommand("fetch", "Cerca nuovo short (Menu)"),
        BotCommand("history", "Gestisci storico video"),
        BotCommand("recap", "Visualizza statistiche")
    ])

if __name__ == '__main__':
    if not TELEGRAM_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not found in .env")
        exit(1)

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('fetch', fetch_command))
    application.add_handler(CommandHandler('history', history_command))
    application.add_handler(CommandHandler('recap', recap_stats))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    application.add_handler(CallbackQueryHandler(button_click))

    print("Bot is running...")
    application.run_polling()
