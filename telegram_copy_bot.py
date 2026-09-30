from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes
import os
import json
import re
import asyncio
import logging
import sys

# Pyrogram has a bug in newer Python versions where it expects an event loop on import.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from aiohttp import web
from pyrogram import Client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_env():
    try:
        with open('.env', 'r') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    key, val = line.strip().split('=', 1)
                    os.environ[key] = val
    except FileNotFoundError:
        pass

load_env()
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_ID = os.environ.get("API_ID", "")
API_HASH = os.environ.get("API_HASH", "")
PORT = int(os.environ.get("PORT", 10000))
FQDN = os.environ.get("FQDN", f"http://localhost:{PORT}").rstrip('/')

# Pyrogram client for MTProto streaming (no_updates=True to avoid conflict with PTB)
pyro_client = None
if API_ID and API_HASH:
    pyro_client = Client(
        "bot_session",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=TOKEN,
        no_updates=True
    )
else:
    logger.warning("API_ID and API_HASH not found. Streaming large files will be disabled.")

# State
user_destinations = {}
user_saved_channels = {}
user_edit_mode = {}
user_brands = {}
pending_files = {} # prompt_id -> file data

def save_state():
    try:
        with open('bot_state.json', 'w') as f:
            json.dump({
                'dest': user_destinations, 
                'saved': user_saved_channels,
                'edit_mode': user_edit_mode,
                'brands': user_brands
            }, f)
    except Exception:
        pass

def load_state():
    global user_destinations, user_saved_channels, user_edit_mode, user_brands
    try:
        with open('bot_state.json', 'r') as f:
            data = json.load(f)
            user_destinations = {int(k): v for k, v in data.get('dest', {}).items()}
            user_saved_channels = {int(k): v for k, v in data.get('saved', {}).items()}
            user_edit_mode = {int(k): v for k, v in data.get('edit_mode', {}).items()}
            user_brands = {int(k): v for k, v in data.get('brands', {}).items()}
    except Exception:
        pass

load_state()

# --- AIOHTTP STREAMING HANDLER ---
routes = web.RouteTableDef()

@routes.get('/')
async def root_handler(request):
    return web.Response(text="Bot is running")

@routes.get('/stream/{chat_id}/{message_id}')
async def stream_handler(request):
    if not pyro_client:
        return web.Response(status=501, text="Streaming is not enabled (missing API credentials)")
    
    chat_id_str = request.match_info['chat_id']
    try:
        message_id = int(request.match_info['message_id'])
    except ValueError:
        return web.Response(status=400, text="Invalid message ID")
        
    try:
        chat_id = int(chat_id_str)
    except ValueError:
        chat_id = chat_id_str

    try:
        message = await pyro_client.get_messages(chat_id, message_id)
        if not message or getattr(message, 'empty', True):
            return web.Response(status=404, text="Message not found")
            
        media = message.document or message.video or message.audio or message.animation or message.photo
        if not media:
            return web.Response(status=404, text="No streamable media found in message")
            
        file_size = getattr(media, 'file_size', 0)
        file_name = getattr(media, 'file_name', 'file')
        mime_type = getattr(media, 'mime_type', 'application/octet-stream')
        
        range_header = request.headers.get('Range')
        offset = 0
        limit = file_size
        
        if range_header:
            range_match = re.match(r'bytes=(\d+)-(\d*)', range_header)
            if range_match:
                offset = int(range_match.group(1))
                end_str = range_match.group(2)
                if end_str:
                    end = int(end_str)
                    limit = end - offset + 1
                else:
                    limit = file_size - offset
                    
        response = web.StreamResponse(
            status=206 if range_header else 200,
            headers={
                'Content-Type': mime_type,
                'Accept-Ranges': 'bytes',
                'Content-Length': str(limit),
                'Content-Range': f'bytes {offset}-{offset+limit-1}/{file_size}',
                'Content-Disposition': f'inline; filename="{file_name}"'
            }
        )
        
        await response.prepare(request)
        
        try:
            async for chunk in pyro_client.stream_media(message, offset=offset, limit=limit):
                await response.write(chunk)
        except Exception as e:
            logger.error(f"Stream error: {e}")
            
        return response
    except Exception as e:
        logger.error(f"Handler error: {e}")
        return web.Response(status=500, text=f"Internal Server Error: {str(e)}")

# --- BOT HANDLERS ---

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "✨ *Welcome to the Ultimate Multi-Channel Copy Bot!* ✨\n\n"
        "📜 *How to use me:*\n"
        "1️⃣ Use `/add @yourchannel` to add a channel to your menu.\n"
        "2️⃣ Use `/menu` to open the visual channel selector!\n"
        "3️⃣ Tap a channel button to lock onto it, then drop files.\n\n"
        "🛠️ *Tools:*\n"
        "Use `/editmode on` to pause every file and allow you to type custom captions manually!\n\n"
        "⚡ _Make sure I am added as an Admin to your channels so I can post!_"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def toggle_editmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    current_mode = user_edit_mode.get(user_id, False)
    
    if not context.args:
        new_mode = not current_mode
    else:
        arg = context.args[0].lower()
        if arg == 'on':
            new_mode = True
        elif arg == 'off':
            new_mode = False
        else:
            new_mode = not current_mode
            
    user_edit_mode[user_id] = new_mode
    save_state()
    
    status = "🟢 ON" if new_mode else "🔴 OFF"
    msg = f"📝 **Manual Edit Mode:** {status}\n\n"
    if new_mode:
        msg += "When you send a file, the bot will now PAUSE and ask you for a custom caption before sending."
    else:
        msg += "Files will now be auto-cleaned and forwarded INSTANTLY."
        
    await update.message.reply_text(msg, parse_mode="Markdown")


async def set_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("⚠️ Please provide your brand text.\n\nExample: `/setbrand 🔥 @CinePrimeHub`", parse_mode="Markdown")
        return
        
    brand_text = " ".join(context.args)
    
    if brand_text.lower() in ["off", "clear", "none"]:
        if user_id in user_brands:
            del user_brands[user_id]
        save_state()
        await update.message.reply_text("✅ Auto-brand disabled!")
        return
        
    user_brands[user_id] = brand_text
    save_state()
    await update.message.reply_text(f"✅ Auto-brand set to:\n\n{brand_text}\n\nIt will now be automatically added to the bottom of forwarded files, and protected from deletion.")

async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Please provide a channel username or ID to add.\n\nExample: `/add @strangerthings_channel`", parse_mode="Markdown")
        return
    
    channel = context.args[0]
    user_id = update.effective_user.id
    
    if user_id not in user_saved_channels:
        user_saved_channels[user_id] = []
        
    if channel not in user_saved_channels[user_id]:
        user_saved_channels[user_id].append(channel)
        
    user_destinations[user_id] = channel
    save_state()
    
    await update.message.reply_text(f"✅ Added {channel} to your menu!\n\n🎯 **Target Channel Locked to {channel}**.\nAny files sent now will go there.", parse_mode="Markdown")

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    channels = user_saved_channels.get(user_id, [])
    
    if not channels:
        await update.message.reply_text("You haven't added any channels yet! Use `/add @yourchannel` first.")
        return
        
    keyboard = []
    for ch in channels:
        keyboard.append([InlineKeyboardButton(f"📡 {ch}", callback_data=f"set_{ch}")])
        
    keyboard.append([InlineKeyboardButton("🛑 Clear Target (Send to me)", callback_data="clear_target")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    current_target = user_destinations.get(user_id, "None (Private)")
    await update.message.reply_text(
        f"🎯 **Current Target:** {current_target}\n\nSelect a channel below to switch targets:", 
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

def generate_stream_msg(chat_id, message_id):
    if not pyro_client:
        return "⚠️ Stream link not generated (Missing API Credentials in .env)"
    return f"🔗 **Stream Link:**\n{FQDN}/stream/{chat_id}/{message_id}"

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "clear_target":
        if user_id in user_destinations:
            del user_destinations[user_id]
        save_state()
        await query.edit_message_text("🛑 Target cleared! Files will be sent back to you privately.")
        
    elif data.startswith("set_"):
        channel = data[4:]
        user_destinations[user_id] = channel
        save_state()
        await query.edit_message_text(f"✅ **Target Channel Locked!**\n\nDestination set to: {channel}\n\nDrop your files now!", parse_mode="Markdown")
        
    elif data in ("sendasis", "cancel"):
        prompt_id = query.message.message_id
        if prompt_id not in pending_files:
            await query.edit_message_text("⚠️ This file has already been processed or expired.")
            return
            
        file_data = pending_files[prompt_id]
        
        if data == "sendasis":
            try:
                sent_msg = await context.bot.copy_message(
                    chat_id=file_data['chat_id_to_send'],
                    from_chat_id=file_data['from_chat_id'],
                    message_id=file_data['message_id'],
                    caption=file_data['original_cleaned_text'] if file_data['original_cleaned_text'] else None,
                    parse_mode="HTML" if file_data['original_cleaned_text'] else None
                )
                stream_text = generate_stream_msg(file_data['chat_id_to_send'], sent_msg.message_id)
                await query.edit_message_text(f"✅ Sent as-is!\n\n{stream_text}", parse_mode="Markdown")
            except Exception as e:
                await query.edit_message_text(f"❌ Failed to send: {e}")
                
        elif data == "cancel":
            await query.edit_message_text("❌ Cancelled. File was not sent.")
            
        del pending_files[prompt_id]

def format_caption(plain_text: str, is_manual: bool = False, my_brand: str = "") -> str:
    lines = plain_text.split('\n')
    my_brand = my_brand.strip()
    
    cleaned = []
    for line in lines:
        if is_manual:
            cleaned.append(line)
        elif my_brand and my_brand.lower() in line.lower():
            cleaned.append(line)
        elif '@' not in line and 't.me/' not in line.lower():
            cleaned.append(line)
            
    formatted_lines = []
    for line in cleaned:
        line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        kv_match = re.match(r'^(.*?)(Name|Size|Audio|Subtitle[s]?|Quality)(\s*:\s*)(.+)$', line, re.IGNORECASE)
        if kv_match:
            prefix = kv_match.group(1)
            key = kv_match.group(2)
            separator = kv_match.group(3)
            value = kv_match.group(4)
            formatted_lines.append(f"{prefix}{key}{separator}<code>{value}</code>")
            continue
            
        if re.search(r'\.(mkv|mp4|avi)\s*$', line, re.IGNORECASE):
            formatted_lines.append(f"<code>{line}</code>")
            continue
            
        formatted_lines.append(line)
    
    final_text = '\n'.join(formatted_lines)
    
    # Auto-append the brand if not manual and missing
    if not is_manual and my_brand and my_brand.lower() not in final_text.lower():
        final_text = final_text.rstrip() + f"\n\n{my_brand}"
        
    return final_text

async def copy_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
        
    user_id = update.effective_user.id
    
    # 1. Check if this is a TEXT REPLY to a prompt
    if update.message.text and update.message.reply_to_message:
        prompt_id = update.message.reply_to_message.message_id
        if prompt_id in pending_files:
            file_data = pending_files[prompt_id]
            my_brand = user_brands.get(user_id, os.environ.get("MY_BRAND", ""))
            new_caption = format_caption(update.message.text, is_manual=True, my_brand=my_brand) 
            
            try:
                sent_msg = await context.bot.copy_message(
                    chat_id=file_data['chat_id_to_send'],
                    from_chat_id=file_data['from_chat_id'],
                    message_id=file_data['message_id'],
                    caption=new_caption,
                    parse_mode="HTML"
                )
                stream_text = generate_stream_msg(file_data['chat_id_to_send'], sent_msg.message_id)
                await update.message.reply_to_message.edit_text(f"✅ Sent with your custom text!\n\n{stream_text}", parse_mode="Markdown")
                del pending_files[prompt_id]
            except Exception as e:
                await update.message.reply_text(f"❌ Failed to send: {e}")
            return
            
    # 2. Otherwise, treat as an incoming file to forward
    chat_id_to_send = user_destinations.get(user_id, update.effective_chat.id)
    is_edit_mode = user_edit_mode.get(user_id, False)
    
    try:
        my_brand = user_brands.get(user_id, os.environ.get("MY_BRAND", ""))
        cleaned_text = format_caption(update.message.caption, my_brand=my_brand) if update.message.caption else None
        
        if is_edit_mode:
            keyboard = [
                [InlineKeyboardButton("➡️ Send As-Is (Original Text)", callback_data="sendasis")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
            ]
            
            preview = f"<pre>{cleaned_text}</pre>" if cleaned_text else "<i>(No text)</i>"
            
            prompt_msg = await update.message.reply_text(
                f"📝 <b>File Paused!</b>\n\nTo add your custom text, <b>REPLY</b> to this message with your new text.\n\n<i>Auto-Cleaned Original:</i> \n{preview}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            
            pending_files[prompt_msg.message_id] = {
                'chat_id_to_send': chat_id_to_send,
                'from_chat_id': update.effective_chat.id,
                'message_id': update.message.message_id,
                'original_cleaned_text': cleaned_text
            }
        else:
            # Send immediately
            sent_msg = await context.bot.copy_message(
                chat_id=chat_id_to_send,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id,
                caption=cleaned_text if cleaned_text else None,
                parse_mode="HTML" if cleaned_text else None
            )
            stream_text = generate_stream_msg(chat_id_to_send, sent_msg.message_id)
            await update.message.reply_text(f"✅ Auto-forwarded!\n\n{stream_text}", parse_mode="Markdown")
                
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to process.\n\nError: {e}", parse_mode="Markdown")

async def post_init(application: Application):
    if pyro_client:
        await pyro_client.start()
        logger.info("Pyrogram client started successfully for streaming.")
        
    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"aiohttp server started on port {PORT}")

def main():
    application = Application.builder().token(TOKEN).post_init(post_init).build()
    
    application.add_handler(CommandHandler('start', start_handler))
    application.add_handler(CommandHandler('add', add_channel))
    application.add_handler(CommandHandler('menu', show_menu))
    application.add_handler(CommandHandler('editmode', toggle_editmode))
    application.add_handler(CommandHandler('setbrand', set_brand))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, copy_message_handler))
    
    application.run_polling()

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if __name__ == '__main__':
    import sys
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    main()
