#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import time
import logging
import sys
import json

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = "7512276458:AAHGerJbecGFUyZwXEY24-XtEmGuLvLFS_Y"
KHMER_MESSAGE = "ជ្រើសរើស Account ដើម្បីបញ្ជាទិញ"
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

def send_message(chat_id, text):
    """Send a message to a specific chat."""
    url = f"{API_URL}/sendMessage"
    data = {
        'chat_id': chat_id,
        'text': text
    }
    
    try:
        response = requests.post(url, data=data, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to send message: {e}")
        return None

def get_updates(offset=None):
    """Get updates from Telegram API."""
    url = f"{API_URL}/getUpdates"
    params = {
        'timeout': 30,
        'limit': 100
    }
    
    if offset:
        params['offset'] = offset
    
    try:
        response = requests.get(url, params=params, timeout=35)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to get updates: {e}")
        return None

def handle_message(update):
    """Handle incoming message."""
    try:
        message = update.get('message')
        if not message:
            return
        
        chat_id = message['chat']['id']
        text = message.get('text', '')
        user = message.get('from', {})
        
        logger.info(f"Received message from user {user.get('first_name', 'Unknown')} (ID: {user.get('id', 'Unknown')}): {text}")
        
        # Handle /start command
        if text.strip() == '/start':
            logger.info(f"Handling /start command for user {user.get('id')}")
            result = send_message(chat_id, KHMER_MESSAGE)
            if result and result.get('ok'):
                logger.info(f"Successfully sent Khmer message to user {user.get('id')}")
            else:
                logger.error(f"Failed to send message to user {user.get('id')}")
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")

def main():
    """Main bot loop."""
    logger.info("Starting Telegram Bot...")
    logger.info(f"Bot token configured: {BOT_TOKEN[:10]}...")
    
    # Test bot connection
    try:
        test_url = f"{API_URL}/getMe"
        response = requests.get(test_url, timeout=10)
        response.raise_for_status()
        bot_info = response.json()
        
        if bot_info.get('ok'):
            bot_data = bot_info.get('result', {})
            logger.info(f"Bot connected successfully: @{bot_data.get('username', 'Unknown')}")
        else:
            logger.error("Failed to connect to bot")
            return
            
    except requests.RequestException as e:
        logger.error(f"Failed to test bot connection: {e}")
        return
    
    # Main polling loop
    offset = None
    logger.info("Bot is now polling for updates...")
    
    while True:
        try:
            updates = get_updates(offset)
            
            if not updates or not updates.get('ok'):
                time.sleep(1)
                continue
            
            for update in updates.get('result', []):
                handle_message(update)
                offset = update['update_id'] + 1
            
            if not updates.get('result'):
                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user interrupt")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)