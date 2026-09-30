import sys
import re
import os

filepath = 'd:/telegram-copy-bot/telegram_copy_bot.py'
with open(filepath, 'r', encoding='utf-8') as f:
    code = f.read()

# 1. Add user_brands state
code = code.replace('user_edit_mode = {}', 'user_edit_mode = {}\nuser_brands = {}')
code = code.replace("'edit_mode': user_edit_mode", "'edit_mode': user_edit_mode,\n                'brands': user_brands")
code = code.replace('global user_destinations, user_saved_channels, user_edit_mode', 'global user_destinations, user_saved_channels, user_edit_mode, user_brands')
code = code.replace('user_edit_mode = {int(k): v for k, v in data.get(\'edit_mode\', {}).items()}', 'user_edit_mode = {int(k): v for k, v in data.get(\'edit_mode\', {}).items()}\n            user_brands = {int(k): v for k, v in data.get(\'brands\', {}).items()}')

# 2. Add /setbrand command
setbrand_cmd = '''
async def set_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("⚠️ Please provide your brand text.\\n\\nExample: `/setbrand 🔥 @CinePrimeHub`", parse_mode="Markdown")
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
    await update.message.reply_text(f"✅ Auto-brand set to:\\n\\n{brand_text}\\n\\nIt will now be automatically added to the bottom of forwarded files, and protected from deletion.")
'''
code = code.replace('async def add_channel(', setbrand_cmd + '\nasync def add_channel(')

# 3. Register /setbrand in main()
code = code.replace("application.add_handler(CommandHandler('editmode', toggle_editmode))", "application.add_handler(CommandHandler('editmode', toggle_editmode))\n    application.add_handler(CommandHandler('setbrand', set_brand))")

# 4. Update format_caption signature and usage
code = code.replace('def format_caption(plain_text: str, is_manual: bool = False) -> str:', 'def format_caption(plain_text: str, is_manual: bool = False, my_brand: str = "") -> str:')
code = code.replace('my_brand = os.environ.get("MY_BRAND", "").strip()', 'my_brand = my_brand.strip()')

# 5. Update copy_message_handler to pass my_brand
# Auto forward block
code = code.replace('cleaned_text = format_caption(update.message.caption) if update.message.caption else None', 'my_brand = user_brands.get(user_id, os.environ.get("MY_BRAND", ""))\n        cleaned_text = format_caption(update.message.caption, my_brand=my_brand) if update.message.caption else None')

# Manual reply block
code = code.replace('new_caption = format_caption(update.message.text, is_manual=True)', 'my_brand = user_brands.get(user_id, os.environ.get("MY_BRAND", ""))\n            new_caption = format_caption(update.message.text, is_manual=True, my_brand=my_brand)')

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(code)
print('Patch applied successfully')
