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
# Token baru dari USER
TOKEN = os.environ.get("BOT_TOKEN", "8766843422:AAGt3yP_3fwOO0Y-w7066-N-p0LRy8iqZKU")
bot = telebot.TeleBot(TOKEN)

# API Hero-SMS (diambil dari ahessherosms)
API_BASE = "https://hero-sms.com/stubs/handler_api.php"
DB_PATH = os.environ.get("DB_PATH", "database.db")

# ADMIN — hanya admin yang bisa add/remove user
ADMIN_ID = 940475417

MAX_ORDER = 20         # Maksimal order sekaligus
OTP_TIMEOUT = 1500     # Timeout 25 menit (1500 detik)
CHECK_INTERVAL = 3     # Cek OTP setiap 3 detik (DICEPATKAN)
CANCEL_DELAY = 120     # Baru bisa cancel setelah 2 menit (120 detik)
SERVICE = "wa"         # WhatsApp service

# ENV BASED PERMANENT WHITELIST
env_whitelist = os.environ.get("WHITELIST_IDS", "")
PERMANENT_WHITELIST = [int(x.strip()) for x in env_whitelist.split(",") if x.strip().replace('-', '').isdigit()]

# =============================================
# KONFIGURASI NEGARA
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

# Menyimpan data order aktif per chat_id agar callback bisa akses
active_orders = {}

# Status Autobuy Antar User (chat_id: boolean)
autobuy_active = {}

# =============================================
# DATABASE
# =============================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        api_key TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS whitelist (
        user_id INTEGER PRIMARY KEY,
        added_by INTEGER,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_info (
        user_id INTEGER PRIMARY KEY,
        first_name TEXT,
        last_name TEXT,
        username TEXT,
        last_seen TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        detail TEXT,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    # Admin & ENV whitelist injection
    c.execute("INSERT OR IGNORE INTO whitelist (user_id, added_by) VALUES (?, ?)", (ADMIN_ID, ADMIN_ID))
    env_wl = os.environ.get("WHITELIST_IDS", "")
    for x in env_wl.split(","):
        x_clean = "".join(filter(str.isdigit, x))
        if x_clean:
            c.execute("INSERT OR IGNORE INTO whitelist (user_id, added_by) VALUES (?, ?)", (int(x_clean), ADMIN_ID))
    conn.commit()
    conn.close()

# =============================================
# WHITELIST / ACCESS CONTROL
# =============================================
def is_whitelisted(user_id):
    if user_id == ADMIN_ID or user_id in PERMANENT_WHITELIST: return True
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM whitelist WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res is not None

def add_to_whitelist(user_id, added_by):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO whitelist (user_id, added_by) VALUES (?, ?)", (user_id, added_by))
    conn.commit()
    conn.close()

def remove_from_whitelist(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM whitelist WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_all_whitelisted():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, added_at FROM whitelist")
    res = c.fetchall()
    conn.close()
    return res

# =============================================
# USER INFO & ACTIVITY LOGGING
# =============================================
def update_user_info(user):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO user_info (user_id, first_name, last_name, username, last_seen) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)", (user.id, user.first_name, user.last_name or '', user.username or ''))
    conn.commit()
    conn.close()

def get_user_info(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT first_name, last_name, username, last_seen FROM user_info WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res

def log_activity(user_id, action, detail=""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO activity_log (user_id, action, detail) VALUES (?, ?, ?)", (user_id, action, detail))
    conn.commit()
    conn.close()

def get_active_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT a.user_id, u.first_name, u.last_name, u.username, a.action, a.detail, a.timestamp FROM activity_log a LEFT JOIN user_info u ON a.user_id = u.user_id WHERE a.id IN (SELECT MAX(id) FROM activity_log GROUP BY user_id) ORDER BY a.timestamp DESC LIMIT 20")
    res = c.fetchall()
    conn.close()
    return res

def get_user_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT a.user_id, u.first_name, u.last_name, u.username, COUNT(*) as total_actions, SUM(CASE WHEN a.action = 'order' THEN 1 ELSE 0 END) as total_orders, SUM(CASE WHEN a.action = 'balance' THEN 1 ELSE 0 END) as total_balance, MAX(a.timestamp) as last_active FROM activity_log a LEFT JOIN user_info u ON a.user_id = u.user_id GROUP BY a.user_id ORDER BY last_active DESC")
    res = c.fetchall()
    conn.close()
    return res

def format_user_label(uid, fname, lname, uname):
    name = fname or "Unknown"
    if lname: name += f" {lname}"
    if uname: name += f" (@{uname})"
    return name

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
# API HELPER
# =============================================
def req_api(api_key, action, **kwargs):
    params = {'api_key': api_key, 'action': action}
    params.update(kwargs)
    try:
        r = requests.get(API_BASE, params=params, timeout=15)
        return r.text.strip()
    except Exception as e: return f"ERROR: {str(e)}"

def strip_country_code(number, country_code="84"):
    number = number.strip()
    if number.startswith("+"): number = number[1:]
    if number.startswith(country_code): number = number[len(country_code):]
    return number

def get_country_label(country_key):
    c = COUNTRIES.get(country_key, COUNTRIES["vietnam"])
    return f"{c['name']} {c['flag']}"

# =============================================
# FORMAT PESAN ORDER (MINIMALIST GRIZZLY STYLE)
# =============================================
def format_order_message(orders, title="", country_key="vietnam", start_index=1, show_progress=True):
    country = COUNTRIES.get(country_key, COUNTRIES["vietnam"])
    lines = []
    if title:
        lines.append(title)
        lines.append("")

    done_count = 0
    total = len(orders)
    now = time.time()

    for i, order in enumerate(orders, start_index):
        number_local = strip_country_code(order['number'], country['country_code'])
        status = order.get('status', 'waiting')
        price_str = f" [💰 {order['price']} USD]" if order.get('price') else ""

        if status == 'waiting':
            elapsed = now - order.get('order_time', now)
            remaining = max(0, OTP_TIMEOUT - elapsed)
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            lines.append(f"{i}. `{number_local}` ⏳ *{mins:02d}:{secs:02d}*{price_str}")
        elif status == 'got_otp':
            code = order.get('code', '???')
            lines.append(f"{i}. `{number_local}` ✅ `{code}`{price_str}")
            done_count += 1
        elif status == 'cancelled':
            lines.append(f"{i}. `{number_local}` 🚫 *Dibatalkan*")
            done_count += 1
        elif status == 'timeout':
            lines.append(f"{i}. `{number_local}` ⏰ *Exp*")
            done_count += 1
        elif status == 'error':
            lines.append(f"{i}. `{number_local}` ❌ *Error*")
            done_count += 1

    if show_progress:
        lines.append("")
        lines.append(f"📊 Progress: {done_count}/{total}")
        if done_count >= total: lines.append("\n✅ *Semua order selesai!*")

    return "\n".join(lines)

def safe_edit_message(text, chat_id, message_id, markup=None):
    try:
        if markup: bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown", reply_markup=markup)
        else: bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown")
        return True
    except Exception as e:
        err_str = str(e).lower()
        if "retry after" in err_str or "too many requests" in err_str: time.sleep(5)
        elif "message is not modified" in err_str: return True
        return False

# =============================================
# AUTO-CHECK OTP
# =============================================
def auto_check_otp(chat_id, message_id, orders, api_key, country_key="vietnam", is_autobuy_mode=False, s_idx=1):
    country_label = get_country_label(country_key)
    last_edit_time = 0
    EDIT_COOLDOWN = 3
    last_timer_update = 0

    try:
        while True:
            waiting_orders = [o for o in orders if o['status'] == 'waiting']
            if not waiting_orders:
                if is_autobuy_mode and autobuy_active.get(chat_id, False):
                    time.sleep(CHECK_INTERVAL)
                    continue
                else:
                    text = format_order_message(orders, "", country_key, start_index=s_idx, show_progress=False)
                    safe_edit_message(text, chat_id, message_id)
                    break

            now = time.time()
            changed = False
            for o in orders:
                if o['status'] != 'waiting': continue
                try:
                    res = req_api(api_key, 'getStatus', id=o['id'])
                    if res.startswith('STATUS_OK'):
                        o['status'] = 'got_otp'
                        o['code'] = res.split(':')[1] if ':' in res else '???'
                        changed = True
                        req_api(api_key, 'setStatus', status='6', id=o['id'])
                    elif res == 'STATUS_CANCEL':
                        o['status'] = 'cancelled'
                        changed = True
                except: pass
                time.sleep(0.3)

            now = time.time()
            if changed or (now - last_timer_update >= 20):
                if now - last_edit_time >= EDIT_COOLDOWN:
                    text = format_order_message(orders, "", country_key, start_index=s_idx, show_progress=False)
                    remaining = [o for o in orders if o['status'] == 'waiting']
                    if remaining:
                        markup = InlineKeyboardMarkup()
                        oldest = min(o.get('order_time', now) for o in remaining)
                        if (now - oldest) >= CANCEL_DELAY:
                            ids = ",".join([o['id'] for o in remaining])
                            markup.row(InlineKeyboardButton("🚫 Batalkan", callback_data=f"cancelall_{ids}"))
                        else:
                            wait_mins = int((CANCEL_DELAY - (now - oldest)) / 60) + 1
                            markup.row(InlineKeyboardButton(f"⏳ Cancel in ~{wait_mins}m", callback_data="cancel_wait"))
                        safe_edit_message(text, chat_id, message_id, markup)
                    else: safe_edit_message(text, chat_id, message_id)
                    last_edit_time = now
                    last_timer_update = now
            time.sleep(CHECK_INTERVAL)
    except: pass
    finally:
        try:
            if chat_id in active_orders and message_id in active_orders[chat_id]: del active_orders[chat_id][message_id]
        except: pass

# =============================================
# AUTOBUY (BRUTAL MODE)
# =============================================
def autobuy_worker(chat_id, api_key):
    try:
        status_msg = bot.send_message(chat_id, "🔥 *AUTO BUY HERO-SMS START*\n\n"
                                             "Brutal Mode Active...\n"
                                             "Target: Vietnam (Vietnam is default)\n"
                                             "Max Price: 0.2", parse_mode="Markdown")
    except: status_msg = None
    
    country_key = "vietnam"
    country = COUNTRIES[country_key]
    attempts = 0
    orders_list = []
    order_counter = 0
    start_time = time.time()
    
    while autobuy_active.get(chat_id, False):
        attempts += 1
        kwargs = {'service': SERVICE, 'country': country['country_id'], 'maxPrice': country['maxPrice']}
        res = req_api(api_key, 'getNumber', **kwargs)
        
        if 'ACCESS_NUMBER' in res:
            parts = res.split(':')
            if len(parts) >= 3:
                t_id, number = parts[1], parts[2]
                order_counter += 1
                # Fetch price for display
                price_val = None
                try: 
                   params = {'api_key': api_key, 'action': 'getPrices', 'service': SERVICE, 'country': country['country_id']}
                   r_p = requests.get(API_BASE, params=params, timeout=3)
                   d = json.loads(r_p.text)
                   # Logic to find price in Hero-SMS JSON
                   cid = country['country_id']
                   inner = d.get(cid, {}).get(SERVICE) or d.get(SERVICE, {}).get(cid)
                   if isinstance(inner, dict): price_val = inner.get('cost') or min([float(k) for k in inner.keys() if k.replace('.','',1).isdigit()])
                except: pass

                order = {'id': t_id, 'number': number, 'status': 'waiting', 'order_time': time.time(), 'price': price_val}
                orders_list.append(order)
                text = format_order_message([order], "", country_key, start_index=order_counter, show_progress=False)
                markup = InlineKeyboardMarkup().row(InlineKeyboardButton("⏳ Wait", callback_data="cancel_wait"))
                try:
                    msg = bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)
                    threading.Thread(target=auto_check_otp, args=(chat_id, msg.message_id, [order], api_key, country_key, True, order_counter)).start()
                except: pass

        elif res == 'NO_BALANCE':
            bot.send_message(chat_id, "❌ No balance.")
            break
        elif res == 'NO_NUMBERS': time.sleep(0.1)
        else: time.sleep(0.2)
        time.sleep(0.5)

# =============================================
# HANDLERS
# =============================================
@bot.message_handler(commands=['adduser'])
def adduser(message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    if len(parts) > 1:
        add_to_whitelist(int(parts[1]), ADMIN_ID)
        bot.reply_to(message, "✅ Added.")

@bot.message_handler(commands=['start'])
def start(message):
    if not is_whitelisted(message.from_user.id):
        bot.send_message(message.chat.id, f"🔒 Locked. ID: `{message.from_user.id}`", parse_mode="Markdown")
        return
    update_user_info(message.from_user)
    api_key = get_user_api(message.from_user.id)
    text = "👑 *Hero-SMS v1 Bot*\n\nChoose country below:"
    markup = InlineKeyboardMarkup()
    if api_key:
        markup.row(InlineKeyboardButton("🇻🇳 Vietnam", callback_data="country_vietnam"), InlineKeyboardButton("🇵🇭 Philippines", callback_data="country_philipina"))
        markup.row(InlineKeyboardButton("🇨🇴 Colombia", callback_data="country_colombia"), InlineKeyboardButton("💰 Balance", callback_data="nav_balance"))
        markup.row(InlineKeyboardButton("🔥 Auto Buy", callback_data="nav_autobuy"), InlineKeyboardButton("🛑 Stop", callback_data="nav_stopauto"))
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(commands=['setapi'])
def setapi(message):
    if not is_whitelisted(message.from_user.id): return
    parts = message.text.split()
    if len(parts) > 1:
        api = parts[1].strip()
        if 'ACCESS_BALANCE' in req_api(api, 'getBalance'):
            set_user_api(message.from_user.id, api)
            bot.reply_to(message, "✅ Saved.")
        else: bot.reply_to(message, "❌ Invalid.")

@bot.message_handler(commands=['autobuy'])
def autobuy(message):
    if not is_whitelisted(message.from_user.id): return
    api = get_user_api(message.from_user.id)
    if api:
        autobuy_active[message.chat.id] = True
        threading.Thread(target=autobuy_worker, args=(message.chat.id, api)).start()

@bot.message_handler(commands=['stopauto'])
def stop(message):
    autobuy_active[message.chat.id] = False
    bot.reply_to(message, "🛑 Stop.")

@bot.callback_query_handler(func=lambda call: True)
def calls(call):
    if not is_whitelisted(call.from_user.id): return
    api = get_user_api(call.from_user.id)
    if call.data.startswith("country_"):
        c = call.data.split("_")[1]
        m = InlineKeyboardMarkup()
        for i in range(1, 6): m.add(InlineKeyboardButton(str(i), callback_data=f"buy_{c}_{i}"))
        bot.edit_message_text(f"Quantity for {c}:", call.message.chat.id, call.message.message_id, reply_markup=m)
    elif call.data.startswith("buy_"):
        p = call.data.split("_")
        process_bulk(call.message.chat.id, api, int(p[2]), p[1])
    elif call.data == "nav_balance":
        res = req_api(api, 'getBalance')
        bot.answer_callback_query(call.id, f"Saldo: {res.split(':')[1]} USD", show_alert=True)
    elif call.data == "nav_autobuy": autobuy(call.message)
    elif call.data == "nav_stopauto": stop(call.message)
    elif call.data == "cancel_wait": bot.answer_callback_query(call.id, "Wait 2m", show_alert=True)

def process_bulk(chat_id, api, count, country_key):
    country = COUNTRIES[country_key]
    msg = bot.send_message(chat_id, f"⏳ Ordering {count}...")
    orders = []
    for _ in range(count):
        res = req_api(api, 'getNumber', service=SERVICE, country=country['country_id'], maxPrice=country['maxPrice'])
        if 'ACCESS_NUMBER' in res:
            parts = res.split(':')
            orders.append({'id': parts[1], 'number': parts[2], 'status': 'waiting', 'order_time': time.time(), 'price': None})
        time.sleep(0.5)
    if orders:
        text = format_order_message(orders, f"🛒 {get_country_label(country_key)}", country_key)
        markup = InlineKeyboardMarkup().row(InlineKeyboardButton("⏳ Wait", callback_data="cancel_wait"))
        bot.edit_message_text(text, chat_id, msg.message_id, parse_mode="Markdown", reply_markup=markup)
        threading.Thread(target=auto_check_otp, args=(chat_id, msg.message_id, orders, api, country_key)).start()
    else: bot.edit_message_text("❌ No numbers.", chat_id, msg.message_id)

if __name__ == '__main__':
    init_db()
    bot.infinity_polling()
