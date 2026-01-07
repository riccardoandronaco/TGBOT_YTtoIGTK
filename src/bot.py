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

async def fetch_next_short(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    await message.reply_text("Sto cercando il prossimo short non pubblicato... (potrebbe richiedere qualche secondo)")

    try:
        loop = asyncio.get_running_loop()
        video_url = await loop.run_in_executor(None, yt_handler.get_oldest_unprocessed_video, YOUTUBE_CHANNEL_URL, history_handler)

        if not video_url:
            await message.reply_text("Nessun nuovo video trovato o tutti i video sono già stati processati.")
            return

        # Store URL in user_data
        context.user_data['found_url'] = video_url

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
        ig_followers = await loop.run_in_executor(None, ig_handler.get_followers)
        
        # 3. TikTok Stats
        tt_stats = await loop.run_in_executor(None, tiktok_handler.get_stats, TIKTOK_USERNAME)
        tt_followers = tt_stats.get('followers', "N/A")
        tt_likes = tt_stats.get('likes', "N/A")

        msg = (
            f"📈 **STATISTICHE SOCIAL**\n\n"
            f"🟥 **YouTube**: {yt_subs} Iscritti\n"
            f"🟪 **Instagram**: {ig_followers} Follower\n"
            f"⬛ **TikTok**: {tt_followers} Follower | {tt_likes} Mi piace"
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
        return

    text = update.message.text
    # Extract URL using regex to handle shared text like "Check this out https://youtu.be/..."
    url_match = re.search(r'(https?://(?:www\.)?(?:youtube\.com/|youtu\.be/)[\w\-\./\?=&]+)', text)
    
    if not url_match:
        await update.message.reply_text("Non ho trovato un link YouTube valido nel messaggio.")
        return

    url = url_match.group(0)
    
    await update.message.reply_text(f"Link trovato: {url}\nSto scaricando il video... attendi.")
    await process_video_url(update, context, url)

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

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
        if url:
            # Extract ID
            match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)
            if match:
                vid = match.group(1)
                history_handler.add(vid, "skipped")
                await query.edit_message_text("Video saltato. Cerco il prossimo...")
                await fetch_next_short(update, context)
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
                await query.edit_message_caption(caption="2/2 🎵 Pubblicazione su TikTok (Guarda il browser!)...")
                result = await loop.run_in_executor(None, tiktok_handler.upload_video, path, caption)
                if result:
                    history_handler.add(video_id, "tiktok")
                else:
                    errors.append("TikTok: Upload fallito/non confermato")
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

        await query.edit_message_caption(caption="⏳ Caricamento su TikTok in corso (Controlla la finestra del browser!)...")

        try:
            loop = asyncio.get_running_loop()
            caption = f"{title} #shorts"

            # Upload to TikTok
            result = await loop.run_in_executor(None, tiktok_handler.upload_video, path, caption)
            
            if result:
                history_handler.add(video_id, "tiktok")
                await refresh_keyboard(msg_caption=f"✅ Pubblicato su TikTok!\n{title}")
            else:
                await refresh_keyboard(msg_caption=f"⚠️ Upload TikTok fallito (o non confermato).")

        except Exception as e:
            logger.error(f"Error uploading to TikTok: {e}")
            await refresh_keyboard(msg_caption=f"❌ Errore TikTok: {e}")
        
        return

async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("start", "Avvia il bot"),
        BotCommand("fetch", "Cerca il prossimo short non pubblicato"),
        BotCommand("recap", "Visualizza statistiche iscritti/follower")
    ])

if __name__ == '__main__':
    if not TELEGRAM_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not found in .env")
        exit(1)

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('fetch', fetch_next_short))
    application.add_handler(CommandHandler('recap', recap_stats))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    application.add_handler(CallbackQueryHandler(button_click))

    print("Bot is running...")
    application.run_polling()
