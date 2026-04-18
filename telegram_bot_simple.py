#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import time
import logging
import sys
import json
import os

# Detect Vercel environment - use /tmp for writable storage
IS_VERCEL = os.environ.get('VERCEL') == '1'
DATA_DIR = '/tmp' if IS_VERCEL else '.'

# Configure logging
log_handlers = [logging.StreamHandler(sys.stdout)]
if not IS_VERCEL:
    log_handlers.append(logging.FileHandler('bot.log', encoding='utf-8'))

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=log_handlers
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

# File paths - use /tmp on Vercel (read-only filesystem)
DATA_FILE = os.path.join(DATA_DIR, 'accounts_data.json')
SESSIONS_FILE = os.path.join(DATA_DIR, 'sessions.json')

TG_STORAGE_MARKER = "📦BOT_STORAGE📦\n"

def _tg_load():
    """Load data from pinned message in admin's chat (Telegram as storage)."""
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{os.environ.get('TELEGRAM_BOT_TOKEN', '')}/getChat",
            params={'chat_id': ADMIN_ID}, timeout=10
        )
        result = resp.json().get('result', {})
        pinned = result.get('pinned_message')
        if pinned:
            text = pinned.get('text', '')
            if text.startswith(TG_STORAGE_MARKER):
                data = json.loads(text[len(TG_STORAGE_MARKER):])
                logger.info("Loaded data from Telegram pinned message storage")
                return data
    except Exception as e:
        logger.error(f"Failed to load from Telegram storage: {e}")
    return None

def _tg_save():
    """Save data as pinned message in admin's chat (Telegram as storage)."""
    try:
        token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        base = f"https://api.telegram.org/bot{token}"
        text = TG_STORAGE_MARKER + json.dumps(accounts_data, ensure_ascii=False, separators=(',', ':'))
        if len(text) > 4096:
            logger.warning("Data too large for Telegram text storage, truncation risk")
        msg_resp = requests.post(f"{base}/sendMessage",
                                 data={'chat_id': ADMIN_ID, 'text': text}, timeout=10)
        msg_data = msg_resp.json()
        if msg_data.get('ok'):
            msg_id = msg_data['result']['message_id']
            requests.post(f"{base}/pinChatMessage",
                          data={'chat_id': ADMIN_ID, 'message_id': msg_id,
                                'disable_notification': True}, timeout=10)
            logger.info(f"Saved data to Telegram storage (message {msg_id})")
    except Exception as e:
        logger.error(f"Failed to save to Telegram storage: {e}")

def load_data():
    """Load accounts data — Telegram storage on Vercel, file otherwise."""
    if IS_VERCEL:
        tg_data = _tg_load()
        if tg_data:
            # Also write to /tmp so within-instance calls are fast
            try:
                with open(DATA_FILE, 'w', encoding='utf-8') as f:
                    json.dump(tg_data, f, ensure_ascii=False)
            except Exception:
                pass
            return tg_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"Loaded accounts data from {DATA_FILE}")
                return data
        except Exception as e:
            logger.error(f"Failed to load data: {e}")
    return {'accounts': [], 'account_types': {}, 'prices': {}}

def save_data():
    """Save accounts data — both file and Telegram storage on Vercel."""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(accounts_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved accounts data to {DATA_FILE}")
    except Exception as e:
        logger.error(f"Failed to save data to file: {e}")
    if IS_VERCEL:
        _tg_save()

def load_sessions():
    """Load user sessions from file (needed for Vercel stateless env)."""
    global user_sessions
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                user_sessions = {int(k): v for k, v in data.items()}
        except Exception as e:
            logger.error(f"Failed to load sessions: {e}")

def save_sessions():
    """Save user sessions to file."""
    try:
        with open(SESSIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump({str(k): v for k, v in user_sessions.items()}, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to save sessions: {e}")

# User session storage for tracking conversation state
user_sessions = {}

# Account storage - loaded from file for persistence across restarts
accounts_data = load_data()

# On Vercel load persisted sessions on module init
if IS_VERCEL:
    load_sessions()

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

def send_photo(chat_id, photo_path, caption=None, parse_mode=None, reply_markup=None, message_effect_id=None):
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
    
    if message_effect_id:
        data['message_effect_id'] = message_effect_id
    
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
                    
                    send_message(chat_id, reply_message, parse_mode="Markdown")
                    
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
                    send_message(chat_id, f"សុំទោស! Account {account_type} អស់ស្តុកហើយ។")
        
        # Handle out-of-stock button clicks
        elif callback_data.startswith('out_of_stock_'):
            account_type = callback_data.replace('out_of_stock_', '')
            send_message(chat_id, f"សូមអភ័យទោស Account {account_type} អស់ពីស្តុក 🪤")
        
        # Handle check payment button
        elif callback_data == 'check_payment':
            if IS_VERCEL:
                load_sessions()
            session = user_sessions.get(user_id)
            if not session or session.get('state') != 'payment_pending':
                requests.post(f"{API_URL}/answerCallbackQuery",
                              data={'callback_query_id': callback_query['id'],
                                    'text': 'មិនមានការទិញដែលកំពុងរង់ចាំ។', 'show_alert': True}, timeout=5)
                return

            # Check 3-minute timeout
            elapsed = time.time() - session.get('qr_sent_at', time.time())
            if elapsed > PAYMENT_TIMEOUT:
                qr_message_id = session.get('qr_message_id')
                if qr_message_id:
                    requests.post(f"{API_URL}/deleteMessage",
                                  data={'chat_id': chat_id, 'message_id': qr_message_id}, timeout=5)
                del user_sessions[user_id]
                if IS_VERCEL:
                    save_sessions()
                send_message(chat_id,
                             "⏰ *ការបង់ប្រាក់ហួសពេល*\n\nការទិញត្រូវបានលុបចោលដោយស្វ័យប្រវត្តិ ព្រោះហួសពេល *3 នាទី*។\n\nសូមធ្វើការទិញម្តងទៀត។",
                             parse_mode="Markdown")
                requests.post(f"{API_URL}/answerCallbackQuery",
                              data={'callback_query_id': callback_query['id']}, timeout=5)
                return

            # Check payment status instantly
            is_paid = check_payment_status(session['md5_hash'])
            if is_paid:
                requests.post(f"{API_URL}/answerCallbackQuery",
                              data={'callback_query_id': callback_query['id'],
                                    'text': '✅ ការបង់ប្រាក់បានបញ្ជាក់!'}, timeout=5)
                deliver_accounts(chat_id, user_id, session)
                if IS_VERCEL:
                    save_sessions()
            else:
                remaining = int((PAYMENT_TIMEOUT - elapsed) / 60)
                requests.post(f"{API_URL}/answerCallbackQuery",
                              data={'callback_query_id': callback_query['id'],
                                    'text': f'⏳ មិនទាន់បានទទួលការបង់ប្រាក់ (នៅសល់ ~{remaining} នាទី)។',
                                    'show_alert': True}, timeout=5)
            return

        # Handle cancel purchase
        elif callback_data == 'cancel_purchase':
            session = user_sessions.get(user_id)
            qr_message_id = session.get('qr_message_id') if session else None
            if qr_message_id:
                requests.post(f"{API_URL}/deleteMessage",
                              data={'chat_id': chat_id, 'message_id': qr_message_id}, timeout=5)
            if user_id in user_sessions:
                del user_sessions[user_id]
            if IS_VERCEL:
                save_sessions()
            send_message(chat_id, "🚫 *បានបោះបង់ការទិញ*", parse_mode="Markdown")

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
            # Only show account types that have stock
            inline_buttons = []
            
            for account_type, accounts in accounts_data['account_types'].items():
                count = len(accounts)
                if count > 0:
                    button_text = f"ទិញ {account_type} - មានក្នុងស្តុក {count}"
                    inline_buttons.append([{'text': button_text, 'callback_data': f"buy_{account_type}"}])
            
            # If no account types available, show out of stock message
            if not inline_buttons:
                send_message(chat_id, "_សូមអភ័យទោស អស់ពីស្តុក 🪤_", parse_mode="Markdown")
                return
            
            send_message(chat_id, "សូមជ្រើសរើស Account ដើម្បីទិញ៖",
                         reply_markup={'inline_keyboard': inline_buttons})
        
        # Check if user is in a purchase session (for all users including admin)
        if user_id in user_sessions:
            session = user_sessions[user_id]
            
            # Handle quantity input for purchase
            if session['state'] == 'waiting_for_quantity':
                try:
                    quantity = int(text.strip())
                    if quantity <= 0:
                        send_message(chat_id, "សូមបញ្ចូលចំនួនធំជាង 0")
                        return
                    
                    if quantity > session['available_count']:
                        send_message(chat_id, f"សុំទោស! មានត្រឹមតែ {session['available_count']} នៅក្នុងស្តុក")
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
                        session['qr_sent_at'] = time.time()
                        
                        # Send QR code image with check-payment button
                        qr_response = send_photo_url(
                            chat_id, qr_url,
                            caption=f"_បន្ទាប់ពីបង់ប្រាក់រួច សូមចុចប៊ូតុង ✅ ពិនិត្យការបង់ប្រាក់។_",
                            parse_mode="Markdown",
                            reply_markup=CHECK_PAYMENT_KEYBOARD
                        )
                        if qr_response and qr_response.get('result'):
                            session['qr_message_id'] = qr_response['result']['message_id']
                        
                        logger.info(f"Generated QR for user {user_id}: Amount ${session['total_price']}, MD5: {md5_hash}")
                        
                    except Exception as e:
                        logger.error(f"Error generating KHQR: {e}")
                        send_message(chat_id, "❌ *មានបញ្ហាក្នុងការបង្កើត QR Code*\n\nសូមព្យាយាមម្តងទៀត។", parse_mode="Markdown")
                        del user_sessions[user_id]
                    
                    return
                    
                except ValueError:
                    send_message(chat_id, "សូមបញ្ចូលចំនួនជាលេខ (ឧទាហរណ៍: 1, 2, 3)")
                    return

        # Handle /start command, keyboard button, and invalid commands for all users
        if text.strip() == '/start':
            logger.info(f"User {user_id} triggered account selection interface")
            if text.strip() == '/start':
                try:
                    last_name = user.get('last_name', '')
                    welcome_caption = f'<tg-emoji emoji-id="5967385500447675533">🎉</tg-emoji> <b>សូមស្វាគមន៍ {last_name}</b>'.strip()
                    send_photo(chat_id, 'start_banner.jpg', caption=welcome_caption, parse_mode='HTML', message_effect_id='5046509860389126442')
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
                send_message(chat_id, "*បញ្ចូល Account សម្រាប់លក់ (អ៊ីមែលម្តងមួយបន្ទាត់)៖*\n\n```\nl1jebywyzos2@10mail.info\nabc123@gmail.com\n```", reply_to_message_id=message_id, parse_mode="Markdown")
                return
            
            # Check if user is in a session
            if user_id in user_sessions:
                session = user_sessions[user_id]
                
                if session['state'] == 'waiting_for_accounts':
                    # Parse email-only accounts (one per line)
                    import re
                    email_pattern = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
                    accounts = []
                    lines = text.strip().split('\n')
                    for line in lines:
                        email = line.strip()
                        if email and email_pattern.match(email):
                            accounts.append({'email': email})
                    
                    if accounts:
                        session['accounts'] = accounts
                        session['state'] = 'waiting_for_account_type'
                        count = len(accounts)
                        send_message(chat_id, f"*បានបញ្ចូល Account ចំនួន {count}\n\nសូមបញ្ចូលប្រភេទ Account៖*", reply_to_message_id=message_id, parse_mode="Markdown")
                    else:
                        send_message(chat_id, "*មិនរកឃើញអ៊ីមែលត្រឹមត្រូវ! សូមបញ្ចូលតាមទម្រង់៖*\n\n```\nl1jebywyzos2@10mail.info\nabc123@gmail.com\n```", reply_to_message_id=message_id, parse_mode="Markdown")
                    return
                
                elif session['state'] == 'waiting_for_account_type':
                    session['account_type'] = text.strip()
                    session['state'] = 'waiting_for_price'
                    send_message(chat_id, f"*សូមដាក់តម្លៃក្នុងប្រភេទ Account {text.strip()}*", reply_to_message_id=message_id, parse_mode="Markdown")
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
                        send_message(chat_id, f"*✅ បានបញ្ចូល Account ដោយជោគជ័យ*\n\n```\n🔹 ចំនួន: {count}\n\n🔹 ប្រភេទ: {account_type}\n\n🔹 តម្លៃ: {price}$\n```", reply_to_message_id=message_id, parse_mode="Markdown")
                        
                        logger.info(f"Admin {user_id} added {count} accounts of type {account_type} with price ${price}")
                        
                    except ValueError:
                        send_message(chat_id, "តម្លៃមិនត្រឹមត្រូវ។ សូមបញ្ចូលតម្លៃជាលេខ (ឧទាហរណ៍: 5.99)", reply_to_message_id=message_id)
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

PAYMENT_TIMEOUT = 3 * 60  # 3 minutes in seconds

CHECK_PAYMENT_KEYBOARD = {
    'inline_keyboard': [
        [{'text': '✅ ពិនិត្យការបង់ប្រាក់', 'callback_data': 'check_payment'}],
        [{'text': '🚫 បោះបង់', 'callback_data': 'cancel_purchase'}]
    ]
}

def deliver_accounts(chat_id, user_id, session):
    """Deliver purchased accounts to user after confirmed payment."""
    account_type = session['account_type']
    quantity = session['quantity']

    # Delete QR code message
    qr_message_id = session.get('qr_message_id')
    if qr_message_id:
        requests.post(f"{API_URL}/deleteMessage",
                      data={'chat_id': chat_id, 'message_id': qr_message_id}, timeout=5)

    if account_type not in accounts_data['account_types']:
        send_message(chat_id, f"❌ *មានបញ្ហា!*\n\nគ្មាន Account ប្រភេទ {account_type} ក្នុងស្តុក។",
                     parse_mode="Markdown")
        return

    available_accounts = accounts_data['account_types'][account_type]
    if len(available_accounts) < quantity:
        send_message(chat_id,
                     f"❌ *មានបញ្ហា!*\n\nសុំទោស! មានត្រឹមតែ {len(available_accounts)} Accounts នៅក្នុងស្តុក។",
                     parse_mode="Markdown")
        return

    delivered_accounts = available_accounts[:quantity]
    accounts_data['account_types'][account_type] = available_accounts[quantity:]
    save_data()

    accounts_message = f"🎉 *ការទិញបានបញ្ជាក់ដោយជោគជ័យ*\n\n"
    accounts_message += f"```\n🔹 ប្រភេទ: {account_type}\n"
    accounts_message += f"🔹 ចំនួន: {quantity}\n```\n\n"
    accounts_message += "*Accounts របស់អ្នក៖*\n\n"
    for account in delivered_accounts:
        if 'email' in account:
            accounts_message += f"{account['email']}\n"
        else:
            accounts_message += f"{account.get('phone', '')} | {account.get('password', '')}\n"
    accounts_message += f"\n_សូមអរគុណសម្រាប់ការទិញ! 🙏_"

    send_message(chat_id, accounts_message, parse_mode="Markdown")

    if user_id in user_sessions:
        del user_sessions[user_id]

    logger.info(f"Payment confirmed and {quantity} accounts delivered to user {user_id}")

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