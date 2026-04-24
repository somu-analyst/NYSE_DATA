"""
Quick local check for bot creds and application build.
Run:
    python run_bot_check.py

This will read the token/chat id files and attempt to build the Telegram Application
without starting polling. It helps confirm the environment is ready before launching.
"""
import sys
sys.path.append(r'c:\Users\srini\Options_chain_data\NYSE_DATA')

try:
    from telegram_bot import load_creds
    from telegram.ext import Application
except Exception as e:
    print('Failed to import bot module or dependencies:', e)
    sys.exit(1)

try:
    token, chat_id = load_creds()
except Exception as e:
    print('Failed to load credentials from token/chatid files:', e)
    sys.exit(1)

print('Token file loaded. Chat ID loaded.')
print('Chat ID:', chat_id)
print('Token: ****' + token[-6:])

try:
    app = Application.builder().token(token).build()
    print('Application built successfully (did NOT start polling).')
    print('To run the bot: python telegram_bot.py')
except Exception as e:
    print('Failed to build Application:', e)
    sys.exit(1)
