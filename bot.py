import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import sqlite3
import json
import os
import threading
import time

# =============================================
# KONFIGURASI
# =============================================
# Token dari USER
TOKEN = os.environ.get("BOT_TOKEN", "8766843422:AAGt3yP_3fwOO0Y-w7066-N-p0LRy8iqZKU")
bot = telebot.TeleBot(TOKEN)

# API Hero-SMS
API_BASE = "https://hero-sms.com/stubs/handler_api.php"
DB_PATH = os.environ.get("DB_PATH", "database.db")

# PORTAL CALLBACK (Ambil dari ahessherosms)
AUTH_PORTAL_URL = os.environ.get("AUTH_PORTAL_URL", "https://otp-hero-callback.up.railway.app")

# ADMIN
ADMIN_ID = 940475417

MAX_ORDER = 20         
OTP_TIMEOUT = 1200     
CHECK_INTERVAL = 3     
CANCEL_DELAY = 120     
SERVICE = "wa"         

# =============================================
# KONFIGURASI NEGARA (Ditambah Philipina ID 4)
# =============================================
COUNTRIES = {
    "vietnam": {
        "name": "Vietnam",
        "flag": "🇻🇳",
        "country_id": "10",
        "country_code": "84",
        "maxPrice": "0.2"
    },
    "philipina": {
        "name": "Philipina",
        "flag": "🇵🇭",
        "country_id": "4",
        "country_code": "63",
        "maxPrice": "0.2"
    },
    "colombia": {
        "name": "Colombia",
        "flag": "🇨🇴",
        "country_id": "33",
        "country_code": "57",
        "maxPrice": "0.2"
    },
}

active_orders = {}
autobuy_active = {}

# =============================================
# DATABASE logic
# =============================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, api_key TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY, added_by INTEGER, added_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_info (user_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, username TEXT, last_seen TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS activity_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT, detail TEXT, timestamp TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute("INSERT OR IGNORE INTO whitelist (user_id, added_by) VALUES (?, ?)", (ADMIN_ID, ADMIN_ID))
    env_wl = os.environ.get("WHITELIST_IDS", "")
    for x in env_wl.split(","):
        x_clean = "".join(filter(str.isdigit, x))
        if x_clean: c.execute("INSERT OR IGNORE INTO whitelist (user_id, added_by) VALUES (?, ?)", (int(x_clean), ADMIN_ID))
    conn.commit()
    conn.close()

def is_whitelisted(user_id):
    if user_id == ADMIN_ID: return True
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM whitelist WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res is not None

def send_login_prompt(chat_id):
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🔐 Login via Portal", url=AUTH_PORTAL_URL))
    text = "🔒 *Akses Ditolak*\n\nSilakan login via portal untuk verifikasi ID Anda."
    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)

def get_user_api(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT api_key FROM users WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else None

def set_user_api(user_id, api_key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, api_key) VALUES (?, ?)", (user_id, api_key))
    conn.commit()
    conn.close()

# =============================================
# API HANDLER
# =============================================
def req_api(api_key, action, **kwargs):
    params = {'api_key': api_key, 'action': action}
    params.update(kwargs)
    try:
        r = requests.get(API_BASE, params=params, timeout=15)
        return r.text.strip()
    except Exception as e: return f"ERROR: {str(e)}"

def get_actual_price(api_key, cid_str):
    try:
        params = {'api_key': api_key, 'action': 'getPrices', 'service': SERVICE, 'country': cid_str}
        r = requests.get(API_BASE, params=params, timeout=5)
        txt = r.text.strip()
        if txt.startswith("{"):
            d = json.loads(txt)
            inner = d.get(cid_str, {}).get(SERVICE) or d.get(SERVICE, {}).get(cid_str)
            if inner and isinstance(inner, dict):
                if "cost" in inner: return inner["cost"]
                nums = [float(k) for k in inner.keys() if k.replace('.', '', 1).isdigit()]
                if nums: return min(nums)
    except: pass
    return None

def strip_country_code(number, country_code="84"):
    number = number.strip()
    if number.startswith("+"): number = number[1:]
    if number.startswith(country_code): number = number[len(country_code):]
    return number

# =============================================
# FORMAT PESAN (GRIZZLY STYLE)
# =============================================
def format_order_message(orders, title="", country_key="vietnam", start_index=1):
    country = COUNTRIES.get(country_key, COUNTRIES["vietnam"])
    lines = []
    if title: lines.append(title + "\n")
    now = time.time()
    for i, order in enumerate(orders, start_index):
        num = strip_country_code(order['number'], country['country_code'])
        status = order.get('status', 'waiting')
        price_str = f" [💰 {order['price']} USD]" if order.get('price') else ""
        if status == 'waiting':
            elapsed = now - order.get('order_time', now)
            rem = max(0, OTP_TIMEOUT - elapsed)
            lines.append(f"{i}. `{num}` ⏳ *{int(rem//60):02d}:{int(rem%60):02d}*{price_str}")
        elif status == 'got_otp':
            lines.append(f"{i}. `{num}` ✅ `{order.get('code','?')}`{price_str}")
        elif status == 'cancelled':
            lines.append(f"{i}. `{num}` 🚫 *Dibatalkan*")
        elif status == 'timeout':
            lines.append(f"{i}. `{num}` ⏰ *Expired*")
    return "\n".join(lines)

def safe_edit(text, cid, mid, markup=None):
    try:
        bot.edit_message_text(text, cid, mid, parse_mode="Markdown", reply_markup=markup)
        return True
    except: return False

# =============================================
# WORKERS
# =============================================
def otp_checker(cid, mid, orders, api_key, country_key, is_auto, s_idx):
    while True:
        working = [o for o in orders if o['status'] == 'waiting']
        if not working:
            if is_auto and autobuy_active.get(cid, False):
                time.sleep(3)
                continue
            else:
                safe_edit(format_order_message(orders, "", country_key, s_idx), cid, mid)
                break
        now = time.time()
        for o in orders:
            if o['status'] == 'waiting' and (now - o['order_time'] > OTP_TIMEOUT):
                o['status'] = 'timeout'
                req_api(api_key, 'setStatus', status='8', id=o['id'])
        changed = False
        for o in orders:
            if o['status'] != 'waiting': continue
            res = req_api(api_key, 'getStatus', id=o['id'])
            if res.startswith('STATUS_OK'):
                o['status'] = 'got_otp'
                o['code'] = res.split(':')[1] if ':' in res else '???'
                changed = True
                req_api(api_key, 'setStatus', status='6', id=o['id'])
            elif res == 'STATUS_CANCEL':
                o['status'] = 'cancelled'
                changed = True
            time.sleep(0.3)
        text = format_order_message(orders, "", country_key, s_idx)
        markup = InlineKeyboardMarkup()
        rem = [o for o in orders if o['status'] == 'waiting']
        if rem:
            oldest = min(o['order_time'] for o in rem)
            if (now - oldest) >= CANCEL_DELAY:
                m_ids = ",".join([o['id'] for o in rem])
                markup.row(InlineKeyboardButton("🚫 Batalkan Semua", callback_data=f"cancelall_{m_ids}"))
            else:
                markup.row(InlineKeyboardButton("⏳ Cancel nanti", callback_data="cancel_wait"))
            safe_edit(text, cid, mid, markup)
        else: safe_edit(text, cid, mid)
        time.sleep(3)

def autobuy_worker(cid, api_key):
    bot.send_message(cid, "🔥 *AUTO BUY AKTIF (BRUTAL)*\nVietnam | Max 0.2", parse_mode="Markdown")
    cnt = 0
    while autobuy_active.get(cid, False):
        res = req_api(api_key, 'getNumber', service=SERVICE, country='10', maxPrice='0.2')
        if 'ACCESS_NUMBER' in res:
            p = res.split(':')
            cnt += 1
            pr = get_actual_price(api_key, '10')
            order = {'id': p[1], 'number': p[2], 'status': 'waiting', 'order_time': time.time(), 'price': pr}
            txt = format_order_message([order], "", "vietnam", cnt)
            msg = bot.send_message(cid, txt, parse_mode="Markdown")
            threading.Thread(target=otp_checker, args=(cid, msg.message_id, [order], api_key, "vietnam", True, cnt)).start()
        elif res == 'NO_BALANCE': break
        time.sleep(0.5)

# =============================================
# HANDLERS
# =============================================
@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    if not is_whitelisted(uid):
        send_login_prompt(message.chat.id)
        return
    api_key = get_user_api(uid)
    text = "🐻 *OTP Hero (Hero-SMS)*\n\nStatus API: " + ("✅ Aktif" if api_key else "❌ Belum Set")
    if api_key:
        bal = req_api(api_key, 'getBalance').split(':')
        if len(bal) > 1: text += f"\n💰 Saldo: *{bal[1]} USD*"
    markup = InlineKeyboardMarkup()
    if api_key:
        markup.row(InlineKeyboardButton("🇻🇳 Vietnam", callback_data="country_vietnam"), InlineKeyboardButton("🇵🇭 Philipina", callback_data="country_philipina"))
        markup.row(InlineKeyboardButton("🇨🇴 Colombia", callback_data="country_colombia"), InlineKeyboardButton("💰 Saldo", callback_data="nav_balance"))
        markup.row(InlineKeyboardButton("🔥 Auto Buy", callback_data="nav_autobuy"), InlineKeyboardButton("🛑 Stop", callback_data="nav_stop"))
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(commands=['setapi'])
def setapi(message):
    if not is_whitelisted(message.from_user.id): return
    p = message.text.split()
    if len(p) > 1:
        if 'ACCESS_BALANCE' in req_api(p[1], 'getBalance'):
            set_user_api(message.from_user.id, p[1])
            bot.reply_to(message, "✅ API Saved.")

@bot.callback_query_handler(func=lambda call: True)
def calls(call):
    if not is_whitelisted(call.from_user.id): return
    api = get_user_api(call.from_user.id)
    if call.data.startswith("country_"):
        c = call.data.split("_")[1]
        m = InlineKeyboardMarkup()
        for i in range(1, 6): m.add(InlineKeyboardButton(str(i), callback_data=f"buy_{c}_{i}"))
        bot.edit_message_text(f"Quantity {c}:", call.message.chat.id, call.message.message_id, reply_markup=m)
    elif call.data.startswith("buy_"):
        p = call.data.split("_")
        process_bulk(call.message.chat.id, api, int(p[2]), p[1])
    elif call.data == "nav_balance":
        bot.answer_callback_query(call.id, f"Saldo: {req_api(api,'getBalance').split(':')[1]} USD", show_alert=True)
    elif call.data == "nav_autobuy":
        autobuy_active[call.message.chat.id] = True
        threading.Thread(target=autobuy_worker, args=(call.message.chat.id, api)).start()
    elif call.data == "nav_stop": autobuy_active[call.message.chat.id] = False
    elif call.data.startswith("cancelall_"):
        ids = call.data.split("_")[1].split(",")
        for i in ids: req_api(api, 'setStatus', status='8', id=i)
        bot.answer_callback_query(call.id, "✅ Dibatalkan")

def process_bulk(cid, api, count, country_key):
    cntry = COUNTRIES[country_key]
    msg = bot.send_message(cid, f"⏳ Memesan {count} nomor...")
    orders = []
    pr = get_actual_price(api, cntry['country_id'])
    for _ in range(count):
        res = req_api(api, 'getNumber', service=SERVICE, country=cntry['country_id'], maxPrice=cntry['maxPrice'])
        if 'ACCESS_NUMBER' in res:
            p = res.split(':')
            orders.append({'id': p[1], 'number': p[2], 'status': 'waiting', 'order_time': time.time(), 'price': pr})
        time.sleep(0.5)
    if orders:
        bot.edit_message_text(format_order_message(orders, f"🛒 *Order {cntry['name']}*", country_key), cid, msg.message_id, parse_mode="Markdown")
        threading.Thread(target=otp_checker, args=(cid, msg.message_id, orders, api, country_key, False, 1)).start()
    else: bot.edit_message_text("❌ Gagal.", cid, msg.message_id)

if __name__ == '__main__':
    init_db()
    bot.infinity_polling()
