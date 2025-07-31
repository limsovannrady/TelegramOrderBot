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
ADMIN_ID = 5002402843
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# User session storage for tracking conversation state
user_sessions = {}

# Account storage
accounts_data = {
    'accounts': [],
    'account_types': {},
    'prices': {}
}

# Persistent keyboard - Regular keyboard (not inline)
COUPON_KEYBOARD = {
    'keyboard': [
        ['គូប៉ុង E-GetS']
    ],
    'resize_keyboard': True,
    'one_time_keyboard': False
}

def send_message(chat_id, text, reply_to_message_id=None, parse_mode=None, reply_markup=None):
    """Send a message to a specific chat."""
    url = f"{API_URL}/sendMessage"
    data = {
        'chat_id': chat_id,
        'text': text
    }
    
    if reply_to_message_id:
        data['reply_to_message_id'] = reply_to_message_id
    
    if parse_mode:
        data['parse_mode'] = parse_mode
    
    if reply_markup:
        data['reply_markup'] = json.dumps(reply_markup)
    
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

def handle_callback_query(update):
    """Handle callback query (inline button clicks)."""
    try:
        callback_query = update.get('callback_query')
        if not callback_query:
            return
        
        chat_id = callback_query['message']['chat']['id']
        callback_data = callback_query.get('data')
        user = callback_query.get('from', {})
        user_id = user.get('id')
        
        logger.info(f"Received callback from user {user.get('first_name', 'Unknown')} (ID: {user_id}): {callback_data}")
        
        # Handle buy button clicks with reply quote functionality
        if callback_data.startswith('buy_'):
            account_type = callback_data.replace('buy_', '')
            
            # Check if account type exists and has stock
            if account_type in accounts_data['account_types']:
                accounts = accounts_data['account_types'][account_type]
                count = len(accounts)
                price = accounts_data['prices'].get(account_type, 0)
                
                if count > 0:
                    # Always allow user to select account type (reset any existing session)
                    user_sessions[user_id] = {
                        'state': 'waiting_for_quantity',
                        'account_type': account_type,
                        'price': price,
                        'available_count': count
                    }
                    
                    # Create regular message without reply quote
                    reply_message = f"មាន {count} នៅក្នុងស្តុក\n"
                    reply_message += f"តម្លៃ ${price} ក្នុងមួយ Account\n\n"
                    reply_message += "*សូមបញ្ចូលចំនួន Accounts ដែលចង់ទិញ៖*"
                    
                    send_message(chat_id, reply_message, parse_mode="Markdown", reply_markup=COUPON_KEYBOARD)
                    
                    logger.info(f"User {user_id} selected account type {account_type}, waiting for quantity input")
                else:
                    send_message(chat_id, f"សុំទោស! Account {account_type} អស់ស្តុកហើយ។", reply_markup=COUPON_KEYBOARD)
        
        # Handle purchase confirmation buttons
        elif callback_data == 'confirm_purchase':
            if user_id in user_sessions and user_sessions[user_id].get('state') == 'purchase_confirmation':
                session = user_sessions[user_id]
                # Here you would implement the actual purchase logic
                send_message(chat_id, "✅ *ការទិញបានបញ្ជាក់ដោយជោគជ័យ!*\n\nនឹងផ្ញើ Account ឲ្យអ្នកក្នុងពេលឆាប់ៗនេះ។", parse_mode="Markdown", reply_markup=COUPON_KEYBOARD)
                del user_sessions[user_id]
            
        elif callback_data == 'cancel_purchase':
            if user_id in user_sessions:
                del user_sessions[user_id]
            send_message(chat_id, "🚫 *បានបោះបង់ការទិញ*", parse_mode="Markdown", reply_markup=COUPON_KEYBOARD)
            
        # Answer callback query to remove loading state
        answer_url = f"{API_URL}/answerCallbackQuery"
        requests.post(answer_url, data={'callback_query_id': callback_query['id']}, timeout=5)
        
    except Exception as e:
        logger.error(f"Error handling callback query: {e}")

def handle_message(update):
    """Handle incoming message."""
    try:
        # Handle callback queries first
        if 'callback_query' in update:
            handle_callback_query(update)
            return
            
        message = update.get('message')
        if not message:
            return
        
        chat_id = message['chat']['id']
        message_id = message.get('message_id')
        text = message.get('text', '')
        user = message.get('from', {})
        user_id = user.get('id')
        
        logger.info(f"Received message from user {user.get('first_name', 'Unknown')} (ID: {user_id}): {text}")
        
        # Function to show account selection interface
        def show_account_selection():
            # Create inline buttons for available account types
            inline_buttons = []
            for account_type, accounts in accounts_data['account_types'].items():
                count = len(accounts)
                button_text = f"ទិញ {account_type} - មានក្នុងស្តុក {count}"
                callback_data = f"buy_{account_type}"
                inline_buttons.append([{'text': button_text, 'callback_data': callback_data}])
            
            # If no accounts available, show a message
            if not inline_buttons:
                send_message(chat_id, "_សូមអភ័យទោស អស់ពីស្តុក 🪤_", parse_mode="Markdown", reply_markup=COUPON_KEYBOARD)
                return
            
            # Create inline keyboard markup
            inline_keyboard = {'inline_keyboard': inline_buttons}
            
            # Send message with inline buttons - keyboard will persist from previous messages
            purchase_message = "សូមជ្រើសរើស Account ដើម្បីទិញ៖"
            send_message(chat_id, purchase_message, reply_markup=inline_keyboard)
        
        # Handle /start command, keyboard button, and invalid commands for all users
        if text.strip() == '/start' or text.strip() == 'គូប៉ុង E-GetS':
            logger.info(f"User {user_id} triggered account selection interface")
            show_account_selection()
            return
        
        # Handle non-admin users
        if user_id != ADMIN_ID:
            # Check if user is in a purchase session
            if user_id in user_sessions:
                session = user_sessions[user_id]
                
                # Handle quantity input
                if session['state'] == 'waiting_for_quantity':
                    try:
                        quantity = int(text.strip())
                        if quantity <= 0:
                            send_message(chat_id, "សូមបញ្ចូលចំនួនធំជាង 0", reply_markup=COUPON_KEYBOARD)
                            return
                        
                        if quantity > session['available_count']:
                            send_message(chat_id, f"សុំទោស! មានត្រឹមតែ {session['available_count']} នៅក្នុងស្តុក", reply_markup=COUPON_KEYBOARD)
                            return
                        
                        # Calculate total price
                        total_price = quantity * session['price']
                        
                        # Update session for confirmation
                        session['state'] = 'purchase_confirmation'
                        session['quantity'] = quantity
                        session['total_price'] = total_price
                        
                        # Create confirmation message
                        confirmation_message = "សូមបញ្ជាក់ការទិញរបស់អ្នក\n\n"
                        confirmation_message += f"```\n🔸ប្រភេទ: {session['account_type']}\n"
                        confirmation_message += f"🔸ចំនួន: {quantity}\n"
                        confirmation_message += f"🔸តម្លៃសរុប: {total_price}USD\n```"
                        
                        # Create inline buttons for confirmation
                        inline_buttons = [
                            [
                                {'text': '🚫 បោះបង់', 'callback_data': 'cancel_purchase'},
                                {'text': '✅ បញ្ជាក់ការទិញ', 'callback_data': 'confirm_purchase'}
                            ]
                        ]
                        inline_keyboard = {'inline_keyboard': inline_buttons}
                        
                        send_message(chat_id, confirmation_message, parse_mode="Markdown", reply_markup=inline_keyboard)
                        return
                        
                    except ValueError:
                        send_message(chat_id, "សូមបញ្ចូលចំនួនជាលេខ (ឧទាហរណ៍: 1, 2, 3)", reply_markup=COUPON_KEYBOARD)
                        return
            
            # For unrecognized commands, show account selection
            logger.info(f"Non-admin user {user_id} sent unrecognized command, showing account selection")
            show_account_selection()
            return
        
        # Admin-only commands
        if user_id == ADMIN_ID:
            # Handle /add_account command
            if text.strip() == '/add_account':
                user_sessions[user_id] = {'state': 'waiting_for_accounts'}
                send_message(chat_id, "*បញ្ចូល Account សម្រាប់លក់តាមទម្រង់៖*\n\n```\nលេខទូរសព្ទ | ពាក្យសម្ងាត់\n```", reply_to_message_id=message_id, parse_mode="Markdown", reply_markup=COUPON_KEYBOARD)
                return
            
            # Check if user is in a session
            if user_id in user_sessions:
                session = user_sessions[user_id]
                
                if session['state'] == 'waiting_for_accounts':
                    # Parse and store accounts
                    accounts = []
                    lines = text.strip().split('\n')
                    for line in lines:
                        if '|' in line:
                            parts = line.split('|')
                            if len(parts) >= 2:
                                phone = parts[0].strip()
                                password = parts[1].strip()
                                accounts.append({'phone': phone, 'password': password})
                    
                    if accounts:
                        session['accounts'] = accounts
                        session['state'] = 'waiting_for_account_type'
                        count = len(accounts)
                        send_message(chat_id, f"*បានបញ្ចូល Account ចំនួន {count}\n\nសូមបញ្ចូលប្រភេទ Account៖*", reply_to_message_id=message_id, parse_mode="Markdown", reply_markup=COUPON_KEYBOARD)
                    else:
                        send_message(chat_id, "*សូមបញ្ចូលតាមទម្រង់៖*\n\n```\nលេខទូរសព្ទ | ពាក្យសម្ងាត់\n```", reply_to_message_id=message_id, parse_mode="Markdown", reply_markup=COUPON_KEYBOARD)
                    return
                
                elif session['state'] == 'waiting_for_account_type':
                    session['account_type'] = text.strip()
                    session['state'] = 'waiting_for_price'
                    send_message(chat_id, f"*សូមដាក់តម្លៃក្នុងប្រភេទ Account {text.strip()}*", reply_to_message_id=message_id, parse_mode="Markdown", reply_markup=COUPON_KEYBOARD)
                    return
                
                elif session['state'] == 'waiting_for_price':
                    try:
                        price = float(text.strip().replace('$', ''))
                        # Store the data
                        account_type = session['account_type']
                        accounts = session['accounts']
                        count = len(accounts)
                        
                        # Save to storage
                        accounts_data['accounts'].extend(accounts)
                        accounts_data['account_types'][account_type] = accounts
                        accounts_data['prices'][account_type] = price
                        
                        # Clear session
                        del user_sessions[user_id]
                        
                        # Send confirmation with keyboard
                        send_message(chat_id, f"*✅ បានបញ្ចូល Account ដោយជោគជ័យ*\n\n```\n🔹 ចំនួន: {count}\n\n🔹 ប្រភេទ: {account_type}\n\n🔹 តម្លៃ: {price}$\n```", reply_to_message_id=message_id, parse_mode="Markdown", reply_markup=COUPON_KEYBOARD)
                        
                        logger.info(f"Admin {user_id} added {count} accounts of type {account_type} with price ${price}")
                        
                    except ValueError:
                        send_message(chat_id, "តម្លៃមិនត្រឹមត្រូវ។ សូមបញ្ចូលតម្លៃជាលេខ (ឧទាហរណ៍: 5.99)", reply_to_message_id=message_id, reply_markup=COUPON_KEYBOARD)
                    return
            
            # If admin sent a message but it's not a recognized command or part of workflow
            # Clear any existing session and show account selection interface
            if user_id in user_sessions:
                del user_sessions[user_id]
                logger.info(f"Cleared session for admin {user_id} due to unrecognized command")
            
            # Show account selection interface for any unrecognized admin input
            logger.info(f"Admin {user_id} sent unrecognized command, showing account selection interface")
            show_account_selection()
        
        # If not admin, ignore
        
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