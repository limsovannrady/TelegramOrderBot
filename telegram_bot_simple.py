#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import time
import logging
import sys
import json
import os
import io
from urllib.parse import quote as url_quote
from bakong_khqr import KHQR

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

# Bakong KHQR configuration — token loaded from secret
BAKONG_TOKEN = os.environ.get("BAKONG_TOKEN", "")
khqr_client = KHQR(BAKONG_TOKEN)

# ── Manual KHQR builder (fallback when library generates invalid strings) ──
def _crc16_ccitt(data: str) -> str:
    """CRC16-CCITT-FALSE: poly=0x1021, init=0xFFFF, no reflection."""
    crc = 0xFFFF
    for ch in data:
        crc ^= ord(ch) << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"

def _tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"

def _build_khqr_manual(bank_account, merchant_name, merchant_city,
                        amount, bill_number, phone, store_label, terminal_label):
    """Build a valid KHQR EMV string with correct CRC16, bypassing the library."""
    # Phone: 85593330905 → 093330905
    if phone.startswith('855'):
        phone_local = '0' + phone[3:]
    else:
        phone_local = phone[-9:] if len(phone) > 9 else phone

    # Additional data (tag 62)
    add_data = (
        _tlv("03", store_label) +
        _tlv("02", phone_local) +
        _tlv("01", bill_number) +
        _tlv("07", terminal_label)
    )

    # Merchant info (tag 99): current time + expiry in milliseconds
    now_ms  = str(int(time.time() * 1000))
    exp_ms  = str(int((time.time() + 86400) * 1000))   # +1 day
    info_data = _tlv("00", now_ms) + _tlv("01", exp_ms)

    body = (
        _tlv("00", "01") +
        _tlv("01", "12") +
        _tlv("29", _tlv("00", bank_account)) +
        _tlv("52", "5999") +
        _tlv("53", "840") +
        _tlv("54", f"{amount:.2f}") +
        _tlv("58", "KH") +
        _tlv("59", merchant_name) +
        _tlv("60", merchant_city) +
        _tlv("62", add_data) +
        _tlv("99", info_data) +
        "6304"
    )
    return body + _crc16_ccitt(body)

def generate_payment_qr(amount):
    """Generate QR code using bakong-khqr library. Returns (img_bytes, md5) or (None, error_msg) on failure."""
    # Check token is present
    if not BAKONG_TOKEN:
        msg = "BAKONG_TOKEN មិនមានក្នុង environment"
        logger.error(msg)
        return None, msg, None
    try:
        bill_number = f"TRX{int(time.time())}"
        # Step 1: generate the KHQR string (local, no network)
        try:
            try:
                qr = khqr_client.create_qr(
                    bank_account='sovannrady@aclb',
                    merchant_name='RADY',
                    merchant_city='KPS',
                    amount=amount,
                    currency='USD',
                    store_label='RADY',
                    phone_number='85593330905',
                    bill_number=bill_number,
                    terminal_label='Cashier-01',
                    static=False,
                    expiration=1
                )
                logger.info("create_qr with expiration=1 succeeded")
            except TypeError:
                qr = khqr_client.create_qr(
                    bank_account='sovannrady@aclb',
                    merchant_name='RADY',
                    merchant_city='KPS',
                    amount=amount,
                    currency='USD',
                    store_label='RADY',
                    phone_number='85593330905',
                    bill_number=bill_number,
                    terminal_label='Cashier-01',
                    static=False
                )
                logger.info("create_qr without expiration succeeded (older library)")
            logger.info(f"KHQR string created, length={len(qr)}, start={qr[:40]}")
            # Validate required EMV fields: currency (5303840) and amount (5404)
            if '5303840' not in qr or '5404' not in qr:
                logger.warning(f"Library KHQR missing currency/amount — using manual builder")
                qr = _build_khqr_manual(
                    bank_account='sovannrady@aclb',
                    merchant_name='RADY',
                    merchant_city='KPS',
                    amount=amount,
                    bill_number=bill_number,
                    phone='85593330905',
                    store_label='RADY',
                    terminal_label='Cashier-01'
                )
                logger.info(f"Manual KHQR built, length={len(qr)}, start={qr[:40]}")
        except Exception as e:
            msg = f"create_qr failed: {type(e).__name__}: {e}"
            logger.error(msg)
            return None, msg, None
        # Step 2: compute MD5 locally (hashlib.md5 of the QR string — same as the library)
        md5 = compute_md5(qr)
        logger.info(f"MD5 computed: {md5}")
        # Step 3: generate image with 3-layer fallback
        img_bytes = None
        # Layer 1: bakong-khqr library's styled image (requires Pillow)
        try:
            img_bytes = khqr_client.qr_image(qr, format='bytes')
            logger.info("QR image generated via bakong-khqr library")
        except Exception as e1:
            logger.warning(f"bakong-khqr image failed ({type(e1).__name__}: {e1}), trying qrcode library")
        # Layer 2: qrcode library directly
        if not img_bytes:
            try:
                import qrcode
                qr_img = qrcode.make(qr)
                buf = io.BytesIO()
                qr_img.save(buf, format='PNG')
                img_bytes = buf.getvalue()
                logger.info("QR image generated via qrcode library")
            except Exception as e2:
                logger.warning(f"qrcode library failed ({type(e2).__name__}: {e2}), trying API fallback")
        # Layer 3: free online QR API (no libraries needed)
        if not img_bytes:
            try:
                qr_api_url = f"https://api.qrserver.com/v1/create-qr-code/?size=500x500&data={url_quote(qr)}"
                resp = requests.get(qr_api_url, timeout=10)
                resp.raise_for_status()
                img_bytes = resp.content
                logger.info("QR image generated via qrserver.com API")
            except Exception as e3:
                msg = f"all 3 QR image methods failed. Last: {type(e3).__name__}: {e3}"
                logger.error(msg)
                return None, msg, None
        logger.info(f"Generated KHQR for amount ${amount}, bill {bill_number}, md5 {md5}, size {len(img_bytes)}b")
        return img_bytes, md5, qr
    except Exception as e:
        msg = f"Unexpected: {type(e).__name__}: {e}"
        logger.error(f"Failed to generate payment QR: {msg}")
        return None, msg, None

def _bakong_api_url():
    """Return correct Bakong API base URL based on token prefix."""
    if BAKONG_TOKEN and BAKONG_TOKEN.startswith("rbk"):
        return "https://api.bakongrelay.com/v1"
    return "https://api-bakong.nbc.gov.kh/v1"

def compute_md5(qr: str) -> str:
    """Compute MD5 of KHQR string locally (same algorithm the library uses)."""
    import hashlib
    return hashlib.md5(qr.encode('utf-8')).hexdigest()

def check_payment_status(md5):
    """Check payment directly against Bakong relay API — no library dependency."""
    try:
        base = _bakong_api_url()
        resp = requests.post(
            f"{base}/check_transaction_by_md5",
            json={"md5": md5},
            headers={
                "Authorization": f"Bearer {BAKONG_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=10
        )
        data = resp.json()
        logger.info(f"check_payment response: status={resp.status_code} body={data}")
        return data.get("responseCode") == 0
    except Exception as e:
        logger.error(f"Failed to check payment status: {type(e).__name__}: {e}")
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
                rest = text[len(TG_STORAGE_MARKER):]
                # Try legacy JSON format first (only if content starts with { not [type|$price])
                json_start = next((i for i, c in enumerate(rest) if c == '{'), None)
                if json_start is not None:
                    try:
                        data = json.loads(rest[json_start:])
                        logger.info("Loaded data from Telegram pinned message storage (JSON format)")
                        return data
                    except (json.JSONDecodeError, ValueError):
                        logger.info("JSON parse failed, falling through to text format parser")
                # Parse new grouped text format
                data = {'accounts': [], 'account_types': {}, 'prices': {}}
                current_type = None
                # Find content after second separator line
                lines = rest.splitlines()
                sep_count = 0
                content_lines = []
                for line in lines:
                    if line.startswith('━'):
                        sep_count += 1
                        continue
                    if sep_count >= 2:
                        content_lines.append(line)
                for line in content_lines:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith('[') and '|$' in line and line.endswith(']'):
                        inner = line[1:-1]
                        type_name, price_str = inner.rsplit('|$', 1)
                        current_type = type_name
                        try:
                            price = float(price_str)
                        except ValueError:
                            price = 0.0
                        data['account_types'][current_type] = []
                        data['prices'][current_type] = price
                    elif current_type and '@' in line:
                        acc = {'email': line}
                        data['accounts'].append(acc)
                        data['account_types'][current_type].append(acc)
                if data['account_types']:
                    logger.info("Loaded data from Telegram pinned message storage (text format)")
                    return data
    except Exception as e:
        logger.error(f"Failed to load from Telegram storage: {e}")
    return None

def _tg_save():
    """Save data as pinned message in admin's chat (Telegram as storage)."""
    try:
        token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        base = f"https://api.telegram.org/bot{token}"

        total_accounts = sum(len(v) for v in accounts_data.get('account_types', {}).values())
        total_types = len(accounts_data.get('account_types', {}))
        prices = accounts_data.get('prices', {})
        prices_lines = "\n".join(f"   • {t}: ${p}" for t, p in prices.items()) or "   • មិនមាន"

        summary = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Account សរុប: {total_accounts}\n"
            f"🗂 ប្រភេទ: {total_types}\n"
            f"💰 តម្លៃ:\n{prices_lines}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )
        lines = []
        for type_name, accs in accounts_data.get('account_types', {}).items():
            price = accounts_data.get('prices', {}).get(type_name, 0)
            lines.append(f"[{type_name}|${price}]")
            for acc in accs:
                lines.append(acc.get('email', ''))
        accounts_text = "\n".join(lines)
        text = TG_STORAGE_MARKER + summary + accounts_text

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
    """Save accounts data — both file and Telegram storage."""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(accounts_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved accounts data to {DATA_FILE}")
    except Exception as e:
        logger.error(f"Failed to save data to file: {e}")
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

# Always load persisted sessions on startup
load_sessions()

# Tracks the current user message_id so all send_message calls auto-reply-quote it
_reply_to_id = None

def send_message(chat_id, text, reply_to_message_id=None, parse_mode=None, reply_markup=None):
    """Send a message to a specific chat."""
    url = f"{API_URL}/sendMessage"
    data = {
        'chat_id': chat_id,
        'text': text
    }
    
    effective_reply_to = reply_to_message_id or _reply_to_id
    if effective_reply_to:
        data['reply_to_message_id'] = effective_reply_to
    
    if parse_mode:
        data['parse_mode'] = parse_mode
    
    # Always show stock reply keyboard for plain messages; inline keyboards keep their own markup.
    effective_markup = reply_markup if reply_markup is not None else STOCK_REPLY_KEYBOARD
    data['reply_markup'] = json.dumps(effective_markup)
    
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

def send_photo_bytes(chat_id, photo_bytes, caption=None, parse_mode=None, reply_markup=None):
    """Send a photo from raw bytes to a specific chat (no filesystem needed)."""
    url = f"{API_URL}/sendPhoto"
    data = {'chat_id': chat_id}
    if caption:
        data['caption'] = caption
    if parse_mode:
        data['parse_mode'] = parse_mode
    if reply_markup:
        data['reply_markup'] = json.dumps(reply_markup)
    try:
        files = {'photo': ('qr.png', photo_bytes, 'image/png')}
        response = requests.post(url, data=data, files=files, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to send photo bytes: {e}")
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
    """Get updates from Telegram API. Raises HTTPError on 4xx/5xx so caller can handle 409."""
    url = f"{API_URL}/getUpdates"
    params = {'timeout': 30, 'limit': 100}
    if offset:
        params['offset'] = offset
    response = requests.get(url, params=params, timeout=35)
    response.raise_for_status()
    return response.json()

def show_account_selection(chat_id):
    """Send the account selection inline keyboard to the given chat."""
    inline_buttons = []
    for account_type, accounts in accounts_data['account_types'].items():
        count = len(accounts)
        if count > 0:
            button_text = f"ទិញ {account_type} - មានក្នុងស្តុក {count}"
            inline_buttons.append([{'text': button_text, 'callback_data': f"buy_{account_type}"}])
    if not inline_buttons:
        send_message(chat_id, "_សូមអភ័យទោស អស់ពីស្តុក 🪤_", parse_mode="Markdown",
                     reply_markup=STOCK_REPLY_KEYBOARD)
        return
    send_message(chat_id, "សូមជ្រើសរើស Account ដើម្បីទិញ៖",
                 reply_markup={'inline_keyboard': inline_buttons})


def handle_callback_query(update):
    """Handle callback query (inline button clicks)."""
    global _reply_to_id
    _reply_to_id = None
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
                    save_sessions()
                    
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

        # Handle confirm buy — generate QR and proceed to payment
        elif callback_data == 'confirm_buy':
            load_sessions()
            session = user_sessions.get(user_id)
            if not session or session.get('state') != 'waiting_for_confirmation':
                requests.post(f"{API_URL}/answerCallbackQuery",
                              data={'callback_query_id': callback_query['id'],
                                    'text': 'មិនមានការទិញដែលកំពុងរង់ចាំ។', 'show_alert': True}, timeout=5)
                return
            session['state'] = 'payment_pending'
            # Delete the summary message
            summary_message_id = callback_query['message']['message_id']
            requests.post(f"{API_URL}/deleteMessage",
                          data={'chat_id': chat_id, 'message_id': summary_message_id}, timeout=5)
            try:
                img_bytes, md5_or_err, qr_string = generate_payment_qr(session['total_price'])
                if not img_bytes:
                    err_detail = md5_or_err or "មិនដឹងមូលហេតុ"
                    logger.error(f"QR generation returned None: {err_detail}")
                    # Notify admin with the actual error
                    if str(user_id) == str(ADMIN_ID):
                        send_message(chat_id,
                            f"❌ *QR បរាជ័យ (Admin Debug):*\n`{err_detail}`",
                            parse_mode="Markdown")
                    else:
                        send_message(chat_id,
                            "❌ *មានបញ្ហាក្នុងការបង្កើត QR Code*\n\nសូមព្យាយាមម្តងទៀត។",
                            parse_mode="Markdown")
                        send_message(ADMIN_ID,
                            f"⚠️ *QR Error (user {user_id}):*\n`{err_detail}`",
                            parse_mode="Markdown")
                    del user_sessions[user_id]
                    save_sessions()
                    requests.post(f"{API_URL}/answerCallbackQuery",
                                  data={'callback_query_id': callback_query['id']}, timeout=5)
                    return
                md5_hash = md5_or_err
                session['md5_hash'] = md5_hash
                session['qr_sent_at'] = time.time()
                # Send caption as a plain message so STOCK_REPLY_KEYBOARD appears automatically
                qr_caption = "_បន្ទាប់ពីបង់ប្រាក់រួច សូមចុចប៊ូតុង ✅ ពិនិត្យការបង់ប្រាក់។_"
                if str(user_id) == str(ADMIN_ID) and qr_string:
                    qr_caption += f"\n\n`[DEBUG] {qr_string[:60]}...`"
                send_message(chat_id, qr_caption, parse_mode="Markdown")
                qr_response = send_photo_bytes(
                    chat_id, img_bytes,
                    reply_markup=CHECK_PAYMENT_KEYBOARD
                )
                if qr_response and qr_response.get('result'):
                    session['qr_message_id'] = qr_response['result']['message_id']
                save_sessions()
                logger.info(f"Generated QR for user {user_id}: Amount ${session['total_price']}, MD5: {md5_hash}")
            except Exception as e:
                logger.error(f"Error generating KHQR: {type(e).__name__}: {e}")
                send_message(chat_id, "❌ *មានបញ្ហាក្នុងការបង្កើត QR Code*\n\nសូមព្យាយាមម្តងទៀត។", parse_mode="Markdown")
                del user_sessions[user_id]
                save_sessions()
            requests.post(f"{API_URL}/answerCallbackQuery",
                          data={'callback_query_id': callback_query['id']}, timeout=5)
            return

        # Handle cancel buy — cancel from summary screen (before QR)
        elif callback_data == 'cancel_buy':
            if user_id in user_sessions:
                del user_sessions[user_id]
            save_sessions()
            summary_message_id = callback_query['message']['message_id']
            requests.post(f"{API_URL}/deleteMessage",
                          data={'chat_id': chat_id, 'message_id': summary_message_id}, timeout=5)
            send_message(chat_id, "🚫 *បានបោះបង់ការទិញ*", parse_mode="Markdown")
            show_account_selection(chat_id)
            requests.post(f"{API_URL}/answerCallbackQuery",
                          data={'callback_query_id': callback_query['id']}, timeout=5)
            return

        # Handle check payment button
        elif callback_data == 'check_payment':
            load_sessions()
            session = user_sessions.get(user_id)
            if not session or session.get('state') != 'payment_pending':
                requests.post(f"{API_URL}/answerCallbackQuery",
                              data={'callback_query_id': callback_query['id'],
                                    'text': 'មិនមានការទិញដែលកំពុងរង់ចាំ។', 'show_alert': True}, timeout=5)
                return

            # Check payment status
            md5 = session.get('md5_hash')
            if not md5:
                requests.post(f"{API_URL}/answerCallbackQuery",
                              data={'callback_query_id': callback_query['id'],
                                    'text': 'មានបញ្ហាក្នុងការស្វែងរក QR។ សូមចាប់ផ្តើមម្តងទៀត។', 'show_alert': True}, timeout=5)
                return
            is_paid = check_payment_status(md5)
            if is_paid:
                requests.post(f"{API_URL}/answerCallbackQuery",
                              data={'callback_query_id': callback_query['id'],
                                    'text': '✅ ការបង់ប្រាក់បានបញ្ជាក់!'}, timeout=5)
                deliver_accounts(chat_id, user_id, session)
                save_sessions()
            else:
                requests.post(f"{API_URL}/answerCallbackQuery",
                              data={'callback_query_id': callback_query['id'],
                                    'text': '⏳ មិនទាន់បានទទួលការបង់ប្រាក់។ សូមព្យាយាមម្តងទៀត។',
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
            save_sessions()
            send_message(chat_id, "🚫 *បានបោះបង់ការទិញ*", parse_mode="Markdown")
            show_account_selection(chat_id)

        # Answer callback query to remove loading state
        answer_url = f"{API_URL}/answerCallbackQuery"
        requests.post(answer_url, data={'callback_query_id': callback_query['id']}, timeout=5)
        
    except Exception as e:
        logger.error(f"Error handling callback query: {e}")

def handle_message(update):
    """Handle incoming message."""
    global _reply_to_id
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
        
        # Set reply-quote context for all send_message calls in this handler
        _reply_to_id = message_id
        
        logger.info(f"Received message from user {user.get('first_name', 'Unknown')} (ID: {user_id}): {text}")
        
        # Function to show account selection interface
        def show_account_selection_local():
            show_account_selection(chat_id)
        
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
                    
                    # Update session with purchase details, wait for confirmation
                    session['quantity'] = quantity
                    session['total_price'] = total_price
                    session['state'] = 'waiting_for_confirmation'
                    save_sessions()
                    
                    # Show order summary with confirm/cancel buttons
                    confirm_keyboard = {
                        'inline_keyboard': [
                            [
                                {'text': '❌ បោះបង់', 'callback_data': 'cancel_buy'},
                                {'text': '✅ ទិញ', 'callback_data': 'confirm_buy'}
                            ]
                        ]
                    }
                    summary = (
                        f"<b>សូមបញ្ជាក់ការបញ្ជាទិញ</b>\n\n"
                        f"<blockquote>🔹 ចំនួន: {quantity}\n\n"
                        f"🔹 ប្រភេទ: {session['account_type']}\n\n"
                        f"🔹 តម្លៃ: {total_price}$</blockquote>"
                    )
                    send_message(chat_id, summary, parse_mode="HTML", reply_markup=confirm_keyboard)
                    return
                    
                except ValueError:
                    send_message(chat_id, "សូមបញ្ចូលចំនួនជាលេខ (ឧទាហរណ៍: 1, 2, 3)")
                    return

        # Handle /start command, keyboard button, and invalid commands for all users
        if text.strip() == '/start':
            logger.info(f"User {user_id} triggered account selection interface")
            try:
                last_name = user.get('last_name', '')
                welcome_caption = f'<tg-emoji emoji-id="5967385500447675533">🎉</tg-emoji> <b>សូមស្វាគមន៍ {last_name}</b>'.strip()
                send_photo(chat_id, 'start_banner.jpg', caption=welcome_caption, parse_mode='HTML', message_effect_id='5046509860389126442', reply_markup=STOCK_REPLY_KEYBOARD)
            except Exception as e:
                logger.error(f"Failed to send banner image: {e}")
                send_message(chat_id, ".", reply_markup=STOCK_REPLY_KEYBOARD)
            show_account_selection_local()
            return
        
        # Handle "ឆែកស្តុក" reply keyboard button
        if text.strip() in ('📦 ឆែកស្តុក', 'ឆែកស្តុក'):
            logger.info(f"User {user_id} pressed ឆែកស្តុក keyboard button")
            show_account_selection_local()
            return
        
        # Handle non-admin users
        if user_id != ADMIN_ID:
            # For unrecognized commands, show account selection
            logger.info(f"Non-admin user {user_id} sent unrecognized command, showing account selection")
            show_account_selection_local()
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
            show_account_selection_local()
        
        # If not admin, ignore
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")

CHECK_PAYMENT_KEYBOARD = {
    'inline_keyboard': [
        [{'text': '✅ ពិនិត្យការបង់ប្រាក់', 'callback_data': 'check_payment'}],
        [{'text': '🚫 បោះបង់', 'callback_data': 'cancel_purchase'}]
    ]
}

STOCK_REPLY_KEYBOARD = {
    'keyboard': [[{'text': '📦 ឆែកស្តុក'}]],
    'resize_keyboard': True,
    'persistent': True
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

    # Delete any active webhook so polling mode works without 409 conflicts
    try:
        requests.post(f"{API_URL}/deleteWebhook", timeout=10)
        logger.info("Webhook deleted — polling mode active")
    except Exception as e:
        logger.warning(f"Could not delete webhook: {e}")

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
    consecutive_409 = 0
    logger.info("Bot is now polling for updates...")
    
    while True:
        try:
            updates = get_updates(offset)
            
            if not updates or not updates.get('ok'):
                time.sleep(1)
                continue
            
            consecutive_409 = 0  # reset on success
            for update in updates.get('result', []):
                handle_message(update)
                offset = update['update_id'] + 1
            
            if not updates.get('result'):
                time.sleep(1)
                
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 409:
                consecutive_409 += 1
                if consecutive_409 % 10 == 1:
                    logger.warning(f"409 Conflict (#{consecutive_409}) — webhook active on another server. Re-deleting webhook...")
                    try:
                        requests.post(f"{API_URL}/deleteWebhook", timeout=10)
                        logger.info("Webhook re-deleted, resuming polling")
                    except Exception as we:
                        logger.warning(f"Could not re-delete webhook: {we}")
                time.sleep(3)
            else:
                logger.error(f"HTTP error in main loop: {e}")
                time.sleep(5)
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