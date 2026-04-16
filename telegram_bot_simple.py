#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import time
import logging
import sys
import json
import os
import threading

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
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
KHMER_MESSAGE = "ជ្រើសរើស Account ដើម្បីបញ្ជាទិញ"
ADMIN_ID = 5002402843
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Payment API configuration
PAYMENT_API_URL = "https://bakong.cambo-kh.com/api/payment"
PAYMENT_USER_TG_ID = "5002402843"

def generate_payment_qr(amount):
    """Generate QR code via payment API. Returns (qr_url, md5) or (None, None) on failure."""
    try:
        response = requests.get(PAYMENT_API_URL, params={
            'type': 'generate_qr',
            'user_tg_id': PAYMENT_USER_TG_ID,
            'amount': amount
        }, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get('status') == 'success':
            qr_url = data['data']['Url_qr_code']
            md5 = data['data']['md5']
            return qr_url, md5
    except Exception as e:
        logger.error(f"Failed to generate payment QR: {e}")
    return None, None

def check_payment_status(md5):
    """Check payment status via API. Returns True if paid, False otherwise."""
    try:
        response = requests.get(PAYMENT_API_URL, params={
            'type': 'check_md5',
            'user_tg_id': PAYMENT_USER_TG_ID,
            'md5': md5
        }, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get('status') == 'success'
    except Exception as e:
        logger.error(f"Failed to check payment status: {e}")
    return False

# User session storage for tracking conversation state
user_sessions = {}

# File path for persistent storage
DATA_FILE = 'accounts_data.json'

def load_data():
    """Load accounts data from JSON file."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"Loaded accounts data from {DATA_FILE}")
                return data
        except Exception as e:
            logger.error(f"Failed to load data from {DATA_FILE}: {e}")
    return {'accounts': [], 'account_types': {}, 'prices': {}}

def save_data():
    """Save accounts data to JSON file."""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(accounts_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved accounts data to {DATA_FILE}")
    except Exception as e:
        logger.error(f"Failed to save data to {DATA_FILE}: {e}")

# Account storage - loaded from file for persistence across restarts
accounts_data = load_data()

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

def send_photo(chat_id, photo_path, caption=None, parse_mode=None, reply_markup=None):
    """Send a photo to a specific chat."""
    url = f"{API_URL}/sendPhoto"
    data = {
        'chat_id': chat_id
    }
    
    if caption:
        data['caption'] = caption
    
    if parse_mode:
        data['parse_mode'] = parse_mode
    
    if reply_markup:
        data['reply_markup'] = json.dumps(reply_markup)
    
    try:
        with open(photo_path, 'rb') as photo:
            files = {'photo': photo}
            response = requests.post(url, data=data, files=files, timeout=10)
            response.raise_for_status()
            return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to send photo: {e}")
        return None

def send_photo_url(chat_id, photo_url, caption=None, parse_mode=None, reply_markup=None):
    """Send a photo from a URL to a specific chat."""
    url = f"{API_URL}/sendPhoto"
    data = {
        'chat_id': chat_id,
        'photo': photo_url
    }
    if caption:
        data['caption'] = caption
    if parse_mode:
        data['parse_mode'] = parse_mode
    if reply_markup:
        data['reply_markup'] = json.dumps(reply_markup)
    try:
        response = requests.post(url, data=data, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to send photo URL: {e}")
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
                    
                    # Delete the original message with inline buttons
                    original_message_id = callback_query['message']['message_id']
                    delete_url = f"{API_URL}/deleteMessage"
                    delete_data = {
                        'chat_id': chat_id,
                        'message_id': original_message_id
                    }
                    requests.post(delete_url, data=delete_data, timeout=5)
                    
                    logger.info(f"User {user_id} selected account type {account_type}, waiting for quantity input")
                else:
                    send_message(chat_id, f"សុំទោស! Account {account_type} អស់ស្តុកហើយ។", reply_markup=COUPON_KEYBOARD)
        
        # Handle out-of-stock button clicks
        elif callback_data.startswith('out_of_stock_'):
            account_type = callback_data.replace('out_of_stock_', '')
            send_message(chat_id, f"សូមអភ័យទោស Account {account_type} អស់ពីស្តុក 🪤", reply_markup=COUPON_KEYBOARD)
        
        # Handle cancel purchase (though this is no longer used since we skip confirmation)
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
            has_stock = False
            
            for account_type, accounts in accounts_data['account_types'].items():
                count = len(accounts)
                if count > 0:
                    button_text = f"ទិញ {account_type} - មានក្នុងស្តុក {count}"
                    callback_data = f"buy_{account_type}"
                    inline_buttons.append([{'text': button_text, 'callback_data': callback_data}])
                    has_stock = True
                else:
                    # Show out of stock for this specific type
                    button_text = f"{account_type} - សូមអភ័យទោស អស់ពីស្តុក 🪤"
                    callback_data = f"out_of_stock_{account_type}"
                    inline_buttons.append([{'text': button_text, 'callback_data': callback_data}])
            
            # If no account types exist at all, show general out of stock message
            if not accounts_data['account_types']:
                send_message(chat_id, "_សូមអភ័យទោស អស់ពីស្តុក 🪤_", parse_mode="Markdown", reply_markup=COUPON_KEYBOARD)
                return
            
            # Create inline keyboard markup
            inline_keyboard = {'inline_keyboard': inline_buttons}
            
            # Send message with inline buttons
            if has_stock:
                purchase_message = "សូមជ្រើសរើស Account ដើម្បីទិញ៖"
            else:
                purchase_message = "បញ្ជី Account (អស់ស្តុកទាំងអស់)៖"
            
            send_message(chat_id, purchase_message, reply_markup=inline_keyboard)
        
        # Check if user is in a purchase session (for all users including admin)
        if user_id in user_sessions:
            session = user_sessions[user_id]
            
            # Handle quantity input for purchase
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
                    
                    # Update session with purchase details
                    session['quantity'] = quantity
                    session['total_price'] = total_price
                    session['state'] = 'payment_pending'
                    
                    # Generate QR code via payment API and send to user
                    try:
                        qr_url, md5_hash = generate_payment_qr(session['total_price'])
                        
                        if not qr_url or not md5_hash:
                            raise Exception("Failed to get QR from payment API")
                        
                        # Store payment info in session for later verification
                        session['md5_hash'] = md5_hash
                        
                        # Send QR code image from URL
                        send_photo_url(chat_id, qr_url, caption=f"_បន្ទាប់ពីបង់ប្រាក់រួច នឹងផ្ញើ Account ឲ្យអ្នកក្នុងពេលឆាប់ៗ។_", parse_mode="Markdown")
                        
                        logger.info(f"Generated QR for user {user_id}: Amount ${session['total_price']}, MD5: {md5_hash}")
                        
                        # Start payment monitoring in background
                        monitor_thread = threading.Thread(target=monitor_payment, args=(chat_id, user_id, md5_hash, session))
                        monitor_thread.daemon = True
                        monitor_thread.start()
                        
                    except Exception as e:
                        logger.error(f"Error generating KHQR: {e}")
                        send_message(chat_id, "❌ *មានបញ្ហាក្នុងការបង្កើត QR Code*\n\nសូមព្យាយាមម្តងទៀត។", parse_mode="Markdown", reply_markup=COUPON_KEYBOARD)
                        del user_sessions[user_id]
                    
                    return
                    
                except ValueError:
                    send_message(chat_id, "សូមបញ្ចូលចំនួនជាលេខ (ឧទាហរណ៍: 1, 2, 3)", reply_markup=COUPON_KEYBOARD)
                    return

        # Handle /start command, keyboard button, and invalid commands for all users
        if text.strip() == '/start' or text.strip() == 'គូប៉ុង E-GetS':
            logger.info(f"User {user_id} triggered account selection interface")
            if text.strip() == '/start':
                try:
                    send_photo(chat_id, 'start_banner.jpg', reply_markup=COUPON_KEYBOARD)
                except Exception as e:
                    logger.error(f"Failed to send banner image: {e}")
            show_account_selection()
            return
        
        # Handle non-admin users
        if user_id != ADMIN_ID:
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
                        save_data()
                        
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

def monitor_payment(chat_id, user_id, md5_hash, session):
    """Monitor payment status and send accounts when payment is confirmed."""
    max_attempts = 30  # Monitor for 15 minutes (30 attempts x 30 seconds)
    attempt = 0
    
    while attempt < max_attempts:
        try:
            # Check payment status using payment API
            is_paid = check_payment_status(md5_hash)
            logger.info(f"Payment check attempt {attempt + 1} for user {user_id}: {'PAID' if is_paid else 'UNPAID'}")
            
            if is_paid:
                # Payment confirmed, send accounts
                account_type = session['account_type']
                quantity = session['quantity']
                
                # Get accounts from storage
                if account_type in accounts_data['account_types']:
                    available_accounts = accounts_data['account_types'][account_type]
                    
                    if len(available_accounts) >= quantity:
                        # Take the required number of accounts
                        delivered_accounts = available_accounts[:quantity]
                        
                        # Remove delivered accounts from storage
                        accounts_data['account_types'][account_type] = available_accounts[quantity:]
                        save_data()
                        
                        # Format accounts message
                        accounts_message = f"🎉 *ការទិញបានបញ្ជាក់ដោយជោគជ័យ!*\n\n"
                        accounts_message += f"```\n🔹 ប្រភេទ: {account_type}\n"
                        accounts_message += f"🔹 ចំនួន: {quantity}\n```\n\n"
                        accounts_message += "*Accounts របស់អ្នក៖*\n\n"
                        
                        for i, account in enumerate(delivered_accounts, 1):
                            accounts_message += f"`{i}. {account['phone']} | {account['password']}`\n"
                        
                        accounts_message += f"\n_សូមអរគុណសម្រាប់ការទិញ! 🙏_"
                        
                        # Send accounts to user
                        send_message(chat_id, accounts_message, parse_mode="Markdown", reply_markup=COUPON_KEYBOARD)
                        
                        # Clear user session
                        if user_id in user_sessions:
                            del user_sessions[user_id]
                        
                        logger.info(f"Payment confirmed and {quantity} accounts delivered to user {user_id}")
                        return
                    else:
                        # Not enough accounts available
                        error_message = f"❌ *មានបញ្ហា!*\n\nសុំទោស! មានត្រឹមតែ {len(available_accounts)} Accounts នៅក្នុងស្តុក។"
                        send_message(chat_id, error_message, parse_mode="Markdown", reply_markup=COUPON_KEYBOARD)
                        logger.error(f"Insufficient accounts for user {user_id}: requested {quantity}, available {len(available_accounts)}")
                        return
                else:
                    # Account type not found
                    error_message = f"❌ *មានបញ្ហា!*\n\nគ្មាន Account ប្រភេទ {account_type} ក្នុងស្តុក។"
                    send_message(chat_id, error_message, parse_mode="Markdown", reply_markup=COUPON_KEYBOARD)
                    logger.error(f"Account type {account_type} not found for user {user_id}")
                    return
            
            else:
                # Still waiting for payment
                attempt += 1
                time.sleep(30)  # Check every 30 seconds
                
        except Exception as e:
            logger.error(f"Error monitoring payment for user {user_id}: {e}")
            attempt += 1
            time.sleep(30)
    
    # Payment monitoring timeout
    timeout_message = f"⏰ *ការបង់ប្រាក់ហួសពេល*\n\nការទិញរបស់អ្នកត្រូវបានលុបចោលដោយសារហួសពេលកំណត់ (15 នាទី)។\n\nសូមធ្វើការទិញម្តងទៀត។"
    send_message(chat_id, timeout_message, parse_mode="Markdown", reply_markup=COUPON_KEYBOARD)
    
    # Clear session
    if user_id in user_sessions:
        del user_sessions[user_id]
    
    logger.info(f"Payment monitoring timeout for user {user_id}")

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