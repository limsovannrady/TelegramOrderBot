#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import time
import logging
import sys
import json
import os
import io
import threading
import hashlib
import fcntl
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from urllib.parse import quote as url_quote
from bakong_khqr import KHQR

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
KHMER_MESSAGE = "ជ្រើសរើស Account ដើម្បីបញ្ជាទិញ"
ADMIN_ID = 5002402843
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Persistent HTTP session — reuses TCP connections for faster Telegram API calls
http = requests.Session()
http.headers.update({'Connection': 'keep-alive'})
worker_pool = ThreadPoolExecutor(max_workers=12)
background_pool = ThreadPoolExecutor(max_workers=4)
_data_lock = threading.RLock()

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
                resp = http.get(qr_api_url, timeout=10)
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
    """Check payment directly against Bakong relay API — no library dependency.
    Returns (is_paid: bool, payment_data: dict or None)."""
    try:
        base = _bakong_api_url()
        resp = http.post(
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
        if data.get("responseCode") == 0:
            return True, data.get("data", {})
        return False, None
    except Exception as e:
        logger.error(f"Failed to check payment status: {type(e).__name__}: {e}")
    return False, None

NEON_DATABASE_URL = os.environ.get("NEON_DATABASE_URL", "")
_neon_host = urlparse(NEON_DATABASE_URL).hostname if NEON_DATABASE_URL else ""
_neon_api_url = f"https://{_neon_host}/sql"
_neon_headers = {
    'Neon-Connection-String': NEON_DATABASE_URL,
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}

def _neon_query(query, params=None):
    """Execute a SQL query via Neon HTTP API."""
    body = {'query': query}
    if params:
        body['params'] = params
    resp = http.post(_neon_api_url, headers=_neon_headers, json=body, timeout=10)
    resp.raise_for_status()
    return resp.json()

def _init_db():
    """Create tables if they don't exist."""
    try:
        _neon_query("""
            CREATE TABLE IF NOT EXISTS bot_accounts (
                id SERIAL PRIMARY KEY,
                data JSONB NOT NULL DEFAULT '{}'
            )
        """)
        _neon_query("""
            CREATE TABLE IF NOT EXISTS bot_sessions (
                id SERIAL PRIMARY KEY,
                data JSONB NOT NULL DEFAULT '{}'
            )
        """)
        _neon_query("""
            CREATE TABLE IF NOT EXISTS bot_pending_payments (
                user_id BIGINT PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                account_type TEXT,
                quantity INT,
                total_price NUMERIC,
                md5_hash TEXT,
                qr_message_id BIGINT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        r = _neon_query("SELECT COUNT(*) as cnt FROM bot_accounts")
        if int(r['rows'][0]['cnt']) == 0:
            _neon_query("INSERT INTO bot_accounts (data) VALUES ($1)",
                        [json.dumps({'accounts': [], 'account_types': {}, 'prices': {}})])
        r = _neon_query("SELECT COUNT(*) as cnt FROM bot_sessions")
        if int(r['rows'][0]['cnt']) == 0:
            _neon_query("INSERT INTO bot_sessions (data) VALUES ($1)", [json.dumps({})])
        logger.info("Neon DB initialized via HTTP API")
    except Exception as e:
        logger.error(f"DB init failed: {e}")

def load_data():
    """Load accounts data from Neon via HTTP API."""
    try:
        r = _neon_query("SELECT data FROM bot_accounts LIMIT 1")
        if r['rows']:
            data = r['rows'][0]['data']
            if isinstance(data, str):
                data = json.loads(data)
            logger.info("Loaded accounts data from Neon DB")
            return data
    except Exception as e:
        logger.error(f"Failed to load data from DB: {e}")
    return {'accounts': [], 'account_types': {}, 'prices': {}}

def save_data():
    """Save accounts data to Neon via HTTP API."""
    try:
        _neon_query("UPDATE bot_accounts SET data = $1",
                    [json.dumps(accounts_data, ensure_ascii=False)])
        logger.info("Saved accounts data to Neon DB")
    except Exception as e:
        logger.error(f"Failed to save data to DB: {e}")

def load_sessions():
    """Load user sessions from Neon via HTTP API."""
    global user_sessions
    try:
        r = _neon_query("SELECT data FROM bot_sessions LIMIT 1")
        if r['rows']:
            data = r['rows'][0]['data']
            if isinstance(data, str):
                data = json.loads(data)
            user_sessions = {int(k): v for k, v in data.items()}
            logger.info("Loaded sessions from Neon DB")
    except Exception as e:
        logger.error(f"Failed to load sessions from DB: {e}")

def save_sessions():
    """Save user sessions to Neon via HTTP API."""
    try:
        with _data_lock:
            payload = {str(k): v for k, v in user_sessions.items()}
        _neon_query("UPDATE bot_sessions SET data = $1",
                    [json.dumps(payload, ensure_ascii=False)])
    except Exception as e:
        logger.error(f"Failed to save sessions to DB: {e}")

def _run_background(name, func, *args, **kwargs):
    def runner():
        try:
            func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Background task {name} failed: {type(e).__name__}: {e}")
    background_pool.submit(runner)

def save_sessions_async():
    _run_background("save_sessions", save_sessions)

def delete_message_async(chat_id, message_id):
    if not message_id:
        return
    _run_background(
        "delete_message",
        http.post,
        f"{API_URL}/deleteMessage",
        data={'chat_id': chat_id, 'message_id': message_id},
        timeout=4
    )

def save_pending_payment(user_id, chat_id, session):
    """Save a pending payment to Neon DB so it persists across sessions."""
    try:
        _neon_query("""
            INSERT INTO bot_pending_payments
                (user_id, chat_id, account_type, quantity, total_price, md5_hash, qr_message_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (user_id) DO UPDATE SET
                chat_id = EXCLUDED.chat_id,
                account_type = EXCLUDED.account_type,
                quantity = EXCLUDED.quantity,
                total_price = EXCLUDED.total_price,
                md5_hash = EXCLUDED.md5_hash,
                qr_message_id = EXCLUDED.qr_message_id,
                created_at = NOW()
        """, [
            str(user_id), str(chat_id),
            session.get('account_type'), str(session.get('quantity', 1)),
            str(session.get('total_price', 0)), session.get('md5_hash'),
            str(session.get('qr_message_id', 0))
        ])
        logger.info(f"Saved pending payment for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to save pending payment: {e}")

def get_pending_payment(user_id):
    """Get a pending payment from Neon DB."""
    try:
        r = _neon_query("SELECT * FROM bot_pending_payments WHERE user_id = $1", [str(user_id)])
        if r['rows']:
            row = r['rows'][0]
            return {
                'state': 'payment_pending',
                'account_type': row.get('account_type'),
                'quantity': int(row.get('quantity') or 1),
                'total_price': float(row.get('total_price') or 0),
                'md5_hash': row.get('md5_hash'),
                'qr_message_id': int(row.get('qr_message_id') or 0),
                'chat_id': int(row.get('chat_id') or 0)
            }
    except Exception as e:
        logger.error(f"Failed to get pending payment: {e}")
    return None

def delete_pending_payment(user_id):
    """Delete a pending payment from Neon DB."""
    try:
        _neon_query("DELETE FROM bot_pending_payments WHERE user_id = $1", [str(user_id)])
        logger.info(f"Deleted pending payment for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to delete pending payment: {e}")

_init_db()

# User session storage for tracking conversation state
user_sessions = {}

# Account storage - loaded from file for persistence across restarts
accounts_data = load_data()

# Always load persisted sessions on startup
load_sessions()

# Tracks the current user message_id per worker so replies never cross between users
_reply_context = threading.local()
START_BANNER_FILE_ID = os.environ.get("START_BANNER_FILE_ID", "")

def _set_reply_to_id(message_id):
    _reply_context.message_id = message_id

def _get_reply_to_id():
    return getattr(_reply_context, 'message_id', None)

def _type_callback_id(account_type):
    return hashlib.sha1(account_type.encode('utf-8')).hexdigest()[:12]

def _account_type_from_callback_id(callback_id):
    for account_type in accounts_data.get('account_types', {}):
        if _type_callback_id(account_type) == callback_id:
            return account_type
    return None

def _short_label(text, limit=36):
    clean = " ".join(str(text).split())
    return clean if len(clean) <= limit else clean[:limit - 1] + "…"

def send_message(chat_id, text, reply_to_message_id=None, parse_mode=None, reply_markup=None, message_effect_id=None):
    """Send a message to a specific chat."""
    url = f"{API_URL}/sendMessage"
    data = {
        'chat_id': chat_id,
        'text': text
    }
    
    effective_reply_to = _get_reply_to_id() if reply_to_message_id is None else reply_to_message_id
    if effective_reply_to:
        data['reply_to_message_id'] = effective_reply_to
    
    if parse_mode:
        data['parse_mode'] = parse_mode
    
    if reply_markup:
        data['reply_markup'] = json.dumps(reply_markup)
    
    if message_effect_id:
        data['message_effect_id'] = message_effect_id
    
    try:
        response = http.post(url, data=data, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        body = ''
        if hasattr(e, 'response') and e.response is not None:
            body = e.response.text
        logger.error(f"Failed to send message: {e} | body: {body}")
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
            response = http.post(url, data=data, files=files, timeout=10)
            response.raise_for_status()
            return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to send photo: {e}")
        return None

def send_start_banner(chat_id, caption=None, parse_mode=None, message_effect_id=None):
    global START_BANNER_FILE_ID
    url = f"{API_URL}/sendPhoto"
    data = {'chat_id': chat_id}
    if caption:
        data['caption'] = caption
    if parse_mode:
        data['parse_mode'] = parse_mode
    if message_effect_id:
        data['message_effect_id'] = message_effect_id

    if START_BANNER_FILE_ID:
        try:
            data['photo'] = START_BANNER_FILE_ID
            response = http.post(url, data=data, timeout=6)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.warning(f"Cached start banner failed, uploading again: {e}")
            START_BANNER_FILE_ID = ""
            data.pop('photo', None)

    try:
        with open('start_banner.jpg', 'rb') as photo:
            files = {'photo': photo}
            response = http.post(url, data=data, files=files, timeout=10)
            response.raise_for_status()
            result = response.json()
            photos = result.get('result', {}).get('photo', [])
            if photos:
                START_BANNER_FILE_ID = photos[-1].get('file_id', "")
            return result
    except requests.RequestException as e:
        logger.error(f"Failed to send start banner: {e}")
        return None

def answer_callback(callback_query_id, text=None, show_alert=False):
    data = {'callback_query_id': callback_query_id}
    if text:
        data['text'] = text
    if show_alert:
        data['show_alert'] = True
    try:
        return http.post(f"{API_URL}/answerCallbackQuery", data=data, timeout=4)
    except requests.RequestException as e:
        logger.warning(f"Failed to answer callback quickly: {e}")
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
        response = http.post(url, data=data, files=files, timeout=15)
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
        response = http.post(url, data=data, timeout=10)
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
    response = http.get(url, params=params, timeout=35)
    response.raise_for_status()
    return response.json()

def show_account_selection(chat_id):
    """Send the account selection inline keyboard to the given chat."""
    inline_buttons = []
    for account_type, accounts in accounts_data['account_types'].items():
        count = len(accounts)
        if count > 0:
            button_text = f"ទិញ {_short_label(account_type)} - ស្តុក {count}"
            inline_buttons.append([{'text': button_text, 'callback_data': f"buy:{_type_callback_id(account_type)}"}])
    if not inline_buttons:
        send_message(chat_id, "_សូមអភ័យទោស អស់ពីស្តុក 🪤_", parse_mode="Markdown", reply_to_message_id=False)
        return
    send_message(chat_id, "សូមជ្រើសរើស Account ដើម្បីទិញ៖",
                 reply_to_message_id=False, reply_markup={'inline_keyboard': inline_buttons})


def handle_callback_query(update):
    """Handle callback query (inline button clicks)."""
    _set_reply_to_id(None)
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
        if callback_data.startswith('buy:') or callback_data.startswith('buy_'):
            if callback_data.startswith('buy:'):
                account_type = _account_type_from_callback_id(callback_data[4:])
            else:
                account_type = callback_data.replace('buy_', '')
            if not account_type:
                answer_callback(callback_query['id'], 'ប្រភេទនេះមិនមានទៀតហើយ។ សូមចាប់ផ្តើមម្តងទៀត។', True)
                return
            answer_callback(callback_query['id'])
            
            # Check if account type exists and has stock
            if account_type in accounts_data['account_types']:
                with _data_lock:
                    accounts = accounts_data['account_types'][account_type]
                    count = len(accounts)
                    price = accounts_data['prices'].get(account_type, 0)
                
                if count > 0:
                    # Always allow user to select account type (reset any existing session)
                    with _data_lock:
                        user_sessions[user_id] = {
                            'state': 'waiting_for_quantity',
                            'account_type': account_type,
                            'price': price,
                            'available_count': count
                        }
                    save_sessions_async()
                    
                    # Create regular message without reply quote
                    reply_message = f"មាន {count} នៅក្នុងស្តុក\n"
                    reply_message += f"តម្លៃ ${price} ក្នុងមួយ Account\n\n"
                    reply_message += "*សូមបញ្ចូលចំនួន Accounts ដែលចង់ទិញ៖*"
                    
                    send_message(chat_id, reply_message, parse_mode="Markdown")
                    
                    # Delete the original message with inline buttons
                    delete_message_async(chat_id, callback_query['message']['message_id'])
                    
                    logger.info(f"User {user_id} selected account type {account_type}, waiting for quantity input")
                else:
                    send_message(chat_id, f"សុំទោស! Account {account_type} អស់ស្តុកហើយ។")
        
        # Handle out-of-stock button clicks
        elif callback_data.startswith('out_of_stock:') or callback_data.startswith('out_of_stock_'):
            answer_callback(callback_query['id'])
            if callback_data.startswith('out_of_stock:'):
                account_type = _account_type_from_callback_id(callback_data[13:]) or "នេះ"
            else:
                account_type = callback_data.replace('out_of_stock_', '')
            send_message(chat_id, f"សូមអភ័យទោស Account {account_type} អស់ពីស្តុក 🪤")

        # Handle confirm buy — generate QR and proceed to payment
        elif callback_data == 'confirm_buy':
            session = user_sessions.get(user_id)
            if not session or session.get('state') != 'waiting_for_confirmation':
                answer_callback(callback_query['id'], 'មិនមានការទិញដែលកំពុងរង់ចាំ។', True)
                return
            answer_callback(callback_query['id'], 'កំពុងបង្កើត QR...')
            with _data_lock:
                session['state'] = 'payment_pending'
            # Delete the summary message
            summary_message_id = callback_query['message']['message_id']
            delete_message_async(chat_id, summary_message_id)
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
                    with _data_lock:
                        if user_id in user_sessions:
                            del user_sessions[user_id]
                    save_sessions_async()
                    return
                md5_hash = md5_or_err
                session['md5_hash'] = md5_hash
                session['qr_sent_at'] = time.time()
                qr_response = send_photo_bytes(
                    chat_id, img_bytes,
                    reply_markup=CHECK_PAYMENT_KEYBOARD
                )
                if qr_response and qr_response.get('result'):
                    session['qr_message_id'] = qr_response['result']['message_id']
                save_sessions_async()
                save_pending_payment(user_id, chat_id, session)
                logger.info(f"Generated QR for user {user_id}: Amount ${session['total_price']}, MD5: {md5_hash}")
            except Exception as e:
                logger.error(f"Error generating KHQR: {type(e).__name__}: {e}")
                send_message(chat_id, "❌ *មានបញ្ហាក្នុងការបង្កើត QR Code*\n\nសូមព្យាយាមម្តងទៀត។", parse_mode="Markdown")
                with _data_lock:
                    if user_id in user_sessions:
                        del user_sessions[user_id]
                save_sessions_async()
            return

        # Admin: delete type — step 1: show confirmation
        elif callback_data.startswith('dts:') and user_id == ADMIN_ID:
            type_name = _account_type_from_callback_id(callback_data[4:]) or callback_data[4:]
            if type_name not in accounts_data.get('account_types', {}):
                answer_callback(callback_query['id'], 'ប្រភេទនេះមិនមានទៀតហើយ!', True)
                return
            answer_callback(callback_query['id'])
            count = len(accounts_data['account_types'].get(type_name, []))
            price = accounts_data.get('prices', {}).get(type_name, 0)
            confirm_cb = f"dtc:{_type_callback_id(type_name)}"
            keyboard = {'inline_keyboard': [[
                {'text': '✅ បញ្ជាក់លុប', 'callback_data': confirm_cb},
                {'text': '❌ បោះបង់', 'callback_data': 'dtcancel'}
            ]]}
            send_message(chat_id,
                f"⚠️ <b>តើអ្នកពិតជាចង់លុបប្រភេទ Account នេះមែនទេ?</b>\n\n"
                f"<blockquote>🔹 ប្រភេទ: {type_name}\n🔹 ចំនួន Account: {count}\n🔹 តម្លៃ: ${price}</blockquote>\n\n"
                f"Account ទាំងអស់ក្នុងប្រភេទនេះនឹងត្រូវបានលុបចោលជាអចិន្ត្រៃយ៍!",
                parse_mode="HTML", reply_to_message_id=None, reply_markup=keyboard)
            return

        # Admin: delete type — step 2: confirmed, perform deletion
        elif callback_data.startswith('dtc:') and user_id == ADMIN_ID:
            type_name = _account_type_from_callback_id(callback_data[4:]) or callback_data[4:]
            if type_name not in accounts_data.get('account_types', {}):
                answer_callback(callback_query['id'], 'ប្រភេទនេះមិនមានទៀតហើយ!', True)
                return
            answer_callback(callback_query['id'])
            count = len(accounts_data['account_types'].pop(type_name, []))
            accounts_data.get('prices', {}).pop(type_name, None)
            accounts_data['accounts'] = [
                a for a in accounts_data.get('accounts', [])
                if a.get('type') != type_name
            ]
            save_data()
            delete_message_async(chat_id, callback_query['message']['message_id'])
            send_message(chat_id,
                f"✅ <b>បានលុបប្រភេទ Account <code>{type_name}</code> ចំនួន {count} records ដោយជោគជ័យ!</b>",
                parse_mode="HTML", reply_to_message_id=None)
            logger.info(f"Admin {user_id} deleted account type '{type_name}' ({count} records)")
            return

        # Admin: delete type — cancelled
        elif callback_data == 'dtcancel' and user_id == ADMIN_ID:
            answer_callback(callback_query['id'])
            delete_message_async(chat_id, callback_query['message']['message_id'])
            send_message(chat_id, "🚫 <b>បានបោះបង់ការលុបប្រភេទ Account</b>",
                         parse_mode="HTML", reply_to_message_id=None)
            return

        # Handle cancel buy — cancel from summary screen (before QR)
        elif callback_data == 'cancel_buy':
            answer_callback(callback_query['id'])
            with _data_lock:
                if user_id in user_sessions:
                    del user_sessions[user_id]
            save_sessions_async()
            summary_message_id = callback_query['message']['message_id']
            delete_message_async(chat_id, summary_message_id)
            send_message(chat_id, "🚫 *បានបោះបង់ការទិញ*", parse_mode="Markdown")
            show_account_selection(chat_id)
            return

        # Handle check payment button
        elif callback_data == 'check_payment':
            session = user_sessions.get(user_id)
            if not session or session.get('state') != 'payment_pending':
                session = get_pending_payment(user_id)
            if not session:
                answer_callback(callback_query['id'], 'មិនមានការទិញដែលកំពុងរង់ចាំ។', True)
                return

            # Check payment status
            md5 = session.get('md5_hash')
            if not md5:
                answer_callback(callback_query['id'], 'មានបញ្ហាក្នុងការស្វែងរក QR។ សូមចាប់ផ្តើមម្តងទៀត។', True)
                return
            is_paid, payment_data = check_payment_status(md5)
            if is_paid:
                answer_callback(callback_query['id'], '✅ ការបង់ប្រាក់បានបញ្ជាក់!')
                user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
                deliver_accounts(chat_id, user_id, session, payment_data=payment_data, user_name=user_name)
                delete_pending_payment(user_id)
                save_sessions_async()
            else:
                answer_callback(callback_query['id'], '⏳ មិនទាន់បានទទួលការបង់ប្រាក់។ សូមព្យាយាមម្តងទៀត។', True)
            return

        # Handle cancel purchase
        elif callback_data == 'cancel_purchase':
            answer_callback(callback_query['id'])
            session = user_sessions.get(user_id)
            qr_message_id = session.get('qr_message_id') if session else None
            if qr_message_id:
                delete_message_async(chat_id, qr_message_id)
            with _data_lock:
                if user_id in user_sessions:
                    del user_sessions[user_id]
            save_sessions_async()
            delete_pending_payment(user_id)
            send_message(chat_id, "🚫 *បានបោះបង់ការទិញ*", parse_mode="Markdown")
            show_account_selection(chat_id)

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
        
        # Set reply-quote context for all send_message calls in this handler
        _set_reply_to_id(message_id)
        
        logger.info(f"Received message from user {user.get('first_name', 'Unknown')} (ID: {user_id}): {text}")
        
        # Function to show account selection interface
        def show_account_selection_local():
            show_account_selection(chat_id)

        if text.strip() == '/start':
            logger.info(f"User {user_id} triggered account selection interface")
            with _data_lock:
                had_session = user_id in user_sessions
                if had_session:
                    del user_sessions[user_id]
            if had_session:
                save_sessions_async()
            try:
                user_display_name = user.get('last_name') or user.get('first_name', '')
                welcome_caption = f'<tg-emoji emoji-id="5967385500447675533">🎉</tg-emoji> <b>សូមស្វាគមន៍ {user_display_name}</b>'
                send_start_banner(chat_id, caption=welcome_caption, parse_mode='HTML', message_effect_id='5046509860389126442')
            except Exception as e:
                logger.error(f"Failed to send banner image: {e}")
            show_account_selection_local()
            return
        
        # Check if user is in a purchase session (for all users including admin)
        if user_id in user_sessions:
            session = user_sessions[user_id]

            # Handle stale payment_pending session — silently clear and show menu
            if session.get('state') == 'payment_pending':
                with _data_lock:
                    del user_sessions[user_id]
                save_sessions_async()
                show_account_selection(chat_id)
                return

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
                    with _data_lock:
                        session['quantity'] = quantity
                        session['total_price'] = total_price
                        session['state'] = 'waiting_for_confirmation'
                    save_sessions_async()
                    
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

        # Handle non-admin users
        if user_id != ADMIN_ID:
            # For unrecognized commands, show account selection
            logger.info(f"Non-admin user {user_id} sent unrecognized command, showing account selection")
            show_account_selection_local()
            return
        
        # Admin-only commands
        if user_id == ADMIN_ID:
            # Handle /delete_type command
            if text.strip() == '/delete_type':
                types = list(accounts_data.get('account_types', {}).keys())
                if not types:
                    send_message(chat_id, "⚠️ <b>មិនមានប្រភេទ Account ណាមួយទេ!</b>",
                                 parse_mode="HTML", reply_to_message_id=None)
                    return
                rows = []
                for t in types:
                    count = len(accounts_data['account_types'].get(t, []))
                    price = accounts_data.get('prices', {}).get(t, 0)
                    label = f"{_short_label(t)} ({count} pcs · ${price})"
                    cb = f"dts:{_type_callback_id(t)}"
                    rows.append([{'text': label, 'callback_data': cb}])
                keyboard = {'inline_keyboard': rows}
                send_message(chat_id, "🗑 <b>ជ្រើសរើសប្រភេទ Account ដែលចង់លុប៖</b>",
                             parse_mode="HTML", reply_to_message_id=None, reply_markup=keyboard)
                return

            # Handle /add_account command
            if text.strip() == '/add_account':
                with _data_lock:
                    user_sessions[user_id] = {'state': 'waiting_for_accounts'}
                save_sessions_async()
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
                        with _data_lock:
                            session['accounts'] = accounts
                            session['state'] = 'waiting_for_account_type'
                        save_sessions_async()
                        count = len(accounts)
                        send_message(chat_id, f"*បានបញ្ចូល Account ចំនួន {count}\n\nសូមបញ្ចូលប្រភេទ Account៖*", reply_to_message_id=message_id, parse_mode="Markdown")
                    else:
                        send_message(chat_id, "*មិនរកឃើញអ៊ីមែលត្រឹមត្រូវ! សូមបញ្ចូលតាមទម្រង់៖*\n\n```\nl1jebywyzos2@10mail.info\nabc123@gmail.com\n```", reply_to_message_id=message_id, parse_mode="Markdown")
                    return
                
                elif session['state'] == 'waiting_for_account_type':
                    account_type_input = text.strip()
                    with _data_lock:
                        existing_price = accounts_data.get('prices', {}).get(account_type_input)
                        session['account_type'] = account_type_input
                        session['state'] = 'waiting_for_price'
                    save_sessions_async()
                    if existing_price is not None:
                        send_message(chat_id,
                            f"*ប្រភេទ Account `{account_type_input}` មានស្រាប់ ដែលមានតម្លៃ {existing_price}$\n\nតម្លៃត្រូវតែដូចគ្នា ({existing_price}$) ដើម្បីបន្ថែម Account បាន៖*",
                            reply_to_message_id=message_id, parse_mode="Markdown")
                    else:
                        send_message(chat_id, f"*សូមដាក់តម្លៃក្នុងប្រភេទ Account {account_type_input}*", reply_to_message_id=message_id, parse_mode="Markdown")
                    return
                
                elif session['state'] == 'waiting_for_price':
                    try:
                        price = float(text.strip().replace('$', ''))
                        account_type = session['account_type']
                        accounts = session['accounts']
                        count = len(accounts)

                        # Validate price matches existing price for this account type
                        with _data_lock:
                            existing_price = accounts_data.get('prices', {}).get(account_type)

                        if existing_price is not None and round(existing_price, 4) != round(price, 4):
                            send_message(chat_id,
                                f"❌ *មិនអាចបញ្ចូលបាន!*\n\nប្រភេទ `{account_type}` មានតម្លៃ *{existing_price}$* ស្រាប់។\nតម្លៃដែលអ្នកបញ្ចូល *{price}$* មិនដូចគ្នា។\n\nសូមបញ្ចូលឡើងវិញដោយប្រើតម្លៃ *{existing_price}$*",
                                reply_to_message_id=message_id, parse_mode="Markdown")
                            return

                        # Save to storage
                        with _data_lock:
                            accounts_data['accounts'].extend(accounts)
                            if account_type in accounts_data['account_types']:
                                accounts_data['account_types'][account_type].extend(accounts)
                            else:
                                accounts_data['account_types'][account_type] = accounts
                            accounts_data['prices'][account_type] = price
                            if user_id in user_sessions:
                                del user_sessions[user_id]
                        save_data()
                        save_sessions_async()

                        # Send confirmation
                        send_message(chat_id, f"*✅ បានបញ្ចូល Account ដោយជោគជ័យ*\n\n```\n🔹 ចំនួន: {count}\n\n🔹 ប្រភេទ: {account_type}\n\n🔹 តម្លៃ: {price}$\n```", reply_to_message_id=message_id, parse_mode="Markdown")

                        logger.info(f"Admin {user_id} added {count} accounts of type {account_type} with price ${price}")

                    except ValueError:
                        send_message(chat_id, "តម្លៃមិនត្រឹមត្រូវ។ សូមបញ្ចូលតម្លៃជាលេខ (ឧទាហរណ៍: 5.99)", reply_to_message_id=message_id)
                    return
            
            # If admin sent a message but it's not a recognized command or part of workflow
            # Clear any existing session and show account selection interface
            if user_id in user_sessions:
                with _data_lock:
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

def deliver_accounts(chat_id, user_id, session, payment_data=None, user_name=''):
    """Deliver purchased accounts to user after confirmed payment."""
    account_type = session['account_type']
    quantity = session['quantity']

    # Delete QR code message
    qr_message_id = session.get('qr_message_id')
    if qr_message_id:
        delete_message_async(chat_id, qr_message_id)

    with _data_lock:
        if account_type not in accounts_data['account_types']:
            available_count = None
            delivered_accounts = None
        else:
            available_accounts = accounts_data['account_types'][account_type]
            available_count = len(available_accounts)
            if available_count < quantity:
                delivered_accounts = None
            else:
                delivered_accounts = available_accounts[:quantity]
                accounts_data['account_types'][account_type] = available_accounts[quantity:]
                if user_id in user_sessions:
                    del user_sessions[user_id]

    if delivered_accounts is None:
        if available_count is None:
            send_message(chat_id, f"❌ *មានបញ្ហា!*\n\nគ្មាន Account ប្រភេទ {account_type} ក្នុងស្តុក។",
                         parse_mode="Markdown")
        else:
            send_message(chat_id,
                         f"❌ *មានបញ្ហា!*\n\nសុំទោស! មានត្រឹមតែ {available_count} Accounts នៅក្នុងស្តុក។",
                         parse_mode="Markdown")
        return

    save_data()

    accounts_message = f'<tg-emoji emoji-id="5436040291507247633">🎉</tg-emoji> <b>ការទិញបានបញ្ជាក់ដោយជោគជ័យ</b>\n\n'
    accounts_message += f"<blockquote>🔹 ប្រភេទ: {account_type}\n"
    accounts_message += f"🔹 ចំនួន: {quantity}</blockquote>\n\n"
    accounts_message += "<b>Accounts របស់អ្នក៖</b>\n\n"
    for account in delivered_accounts:
        if 'email' in account:
            accounts_message += f"{account['email']}\n"
        else:
            accounts_message += f"{account.get('phone', '')} | {account.get('password', '')}\n"
    accounts_message += f"\n<i>សូមអរគុណសម្រាប់ការទិញ <tg-emoji emoji-id=\"5897474556834091884\">🙏</tg-emoji></i>"

    send_message(chat_id, accounts_message, parse_mode="HTML", message_effect_id="5046509860389126442")

    # Notify admin about successful payment
    try:
        import datetime
        cambodia_tz = datetime.timezone(datetime.timedelta(hours=7))
        now_str = datetime.datetime.now(cambodia_tz).strftime("%d/%m/%Y %I:%M:%p")
        pd = payment_data or {}
        from_account = pd.get('fromAccountId') or pd.get('hash') or 'N/A'
        memo = pd.get('memo') or 'គ្មាន'
        ref = pd.get('externalRef') or pd.get('transactionId') or pd.get('md5') or 'N/A'
        amount = session.get('total_price', 0)
        buyer_label = f"{user_name} ({user_id})" if user_name else str(user_id)
        admin_msg = (
            "🎉 <b>ទទួលបានការបង់ប្រាក់ជោគជ័យ</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>ឈ្មោះអ្នកទិញ(ID):</b> {buyer_label}\n"
            f"💵 <b>ទឹកប្រាក់:</b> {amount} USD\n"
            f"👤 <b>ពីធនាគារ:</b> <code>{from_account}</code>\n"
            f"📝 <b>ចំណាំ:</b> {memo}\n"
            f"🧾 <b>លេខយោង:</b> <code>{ref}</code>\n"
            f"⏰ <b>ម៉ោង:</b> {now_str}"
        )
        send_message(ADMIN_ID, admin_msg, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to send admin payment notification: {e}")

    save_sessions_async()

    logger.info(f"Payment confirmed and {quantity} accounts delivered to user {user_id}")

def main():
    """Main bot loop."""
    lock_file = open('/tmp/telegram_bot_simple.lock', 'w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.error("Another bot process is already running in this project. Exiting duplicate process.")
        return

    logger.info("Starting Telegram Bot...")
    logger.info(f"Bot token configured: {BOT_TOKEN[:10]}...")

    # Delete any active webhook so polling mode works without 409 conflicts
    try:
        http.post(f"{API_URL}/deleteWebhook", timeout=10)
        logger.info("Webhook deleted — polling mode active")
    except Exception as e:
        logger.warning(f"Could not delete webhook: {e}")

    # Test bot connection
    try:
        test_url = f"{API_URL}/getMe"
        response = http.get(test_url, timeout=10)
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
                offset = update['update_id'] + 1
                worker_pool.submit(handle_message, update)
                
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 409:
                consecutive_409 += 1
                if consecutive_409 % 10 == 1:
                    logger.warning(f"409 Conflict (#{consecutive_409}) — webhook active on another server. Re-deleting webhook...")
                    try:
                        http.post(f"{API_URL}/deleteWebhook", timeout=10)
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