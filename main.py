import json
from telegram_bot_simple import handle_message, logger

def app(environ, start_response):
    try:
        content_length = int(environ.get('CONTENT_LENGTH', 0) or 0)
        body = environ['wsgi.input'].read(content_length)
        update = json.loads(body)
        handle_message(update)
    except Exception as e:
        logger.error(f"Webhook error: {e}")

    start_response('200 OK', [('Content-Type', 'text/plain')])
    return [b'OK']
