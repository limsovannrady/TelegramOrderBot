from http.server import BaseHTTPRequestHandler
import json
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import telegram_bot_simple as bot

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            update = json.loads(body)
            logger.info(f"Received webhook update: {update.get('update_id')}")
            # Reload latest sessions and data before handling
            bot.load_sessions()
            bot.accounts_data.update(bot.load_data())
            bot.handle_message(update)
            # Persist sessions after handling
            bot.save_sessions()
        except Exception as e:
            logger.error(f"Webhook error: {e}")
        finally:
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')

    def log_message(self, format, *args):
        pass
