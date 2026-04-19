import threading
from telegram_bot_simple import main as run_bot

def start_bot():
    run_bot()

bot_thread = threading.Thread(target=start_bot, daemon=True)
bot_thread.start()

def app(environ, start_response):
    start_response('200 OK', [('Content-Type', 'text/plain')])
    return [b'Bot is running']
