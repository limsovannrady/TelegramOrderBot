import json
import threading
from telegram_bot_simple import handle_message, logger

def app(environ, start_response):
    try:
        content_length = int(environ.get('CONTENT_LENGTH', 0) or 0)
        body = environ['wsgi.input'].read(content_length)
        update = json.loads(body)
        threading.Thread(target=handle_message, args=(update,), daemon=True).start()
    except Exception as e:
        logger.error(f"Webhook error: {e}")

    start_response('200 OK', [('Content-Type', 'text/plain')])
    return [b'OK']
