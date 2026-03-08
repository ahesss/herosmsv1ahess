import telebot
# Trigger redeploy for Hero-SMS version
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import sqlite3
import json
import os
import threading
import time

# =============================================
# KONFIGURASI (CLONE DARI GRIZZLY)
# =============================================
TOKEN = os.environ.get("BOT_TOKEN", "8766843422:AAGt3yP_3fwOO0Y-w7066-N-p0LRy8iqZKU")
bot = telebot.TeleBot(TOKEN)

# API BASE Hero-SMS
API_BASE = "https://hero-sms.com/stubs/handler_api.php"
DB_PATH = os.environ.get("DB_PATH", "database.db")

# ADMIN
ADMIN_ID = 940475417

MAX_ORDER = 20         
OTP_TIMEOUT = 1200     
CHECK_INTERVAL = 3     
CANCEL_DELAY = 120     
SERVICE = "wa"         

# ENV BASED PERMANENT WHITELIST
env_whitelist = os.environ.get("WHITELIST_IDS", "")
PERMANENT_WHITELIST = [int(x.strip()) for x in env_whitelist.split(",") if x.strip().replace('-', '').isdigit()]

# =============================================
# KONFIGURASI NEGARA (Ditambah PH ID 3)
# =============================================
COUNTRIES = {
    "vietnam": {
        "name": "Vietnam",
        "flag": "🇻🇳",
        "country_id": "10",
        "country_code": "84",
    },
    "philipina": {
        "name": "Philipina",
        "flag": "🇵🇭",
        "country_id": "3",
        "country_code": "63",
    },
    "colombia": {
        "name": "Colombia",
        "flag": "🇨🇴",
        "country_id": "33",
        "country_code": "57",
    },
}

active_orders = {}

# =============================================
# DATABASE
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

# =============================================
# WHITELIST / ACCESS CONTROL
# =============================================
def is_whitelisted(user_id):
    env_wl = os.environ.get("WHITELIST_IDS", "")
    perm_wl = []
    for x in env_wl.split(","):
        x_clean = "".join(filter(str.isdigit, x))
        if x_clean: perm_wl.append(int(x_clean))
    
    if user_id == ADMIN_ID or user_id in perm_wl: return True
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
    c.execute("""INSERT OR REPLACE INTO user_info (user_id, first_name, last_name, username, last_seen)
                 VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
              (user.id, user.first_name, user.last_name or '', user.username or ''))
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
    c.execute("""SELECT a.user_id, u.first_name, u.last_name, u.username, a.action, a.detail, a.timestamp
                 FROM activity_log a LEFT JOIN user_info u ON a.user_id = u.user_id
                 WHERE a.id IN (SELECT MAX(id) FROM activity_log GROUP BY user_id)
                 ORDER BY a.timestamp DESC LIMIT 20""")
    res = c.fetchall()
    conn.close()
    return res

def get_user_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT a.user_id, u.first_name, u.last_name, u.username, COUNT(*) as total_actions,
                        SUM(CASE WHEN a.action = 'order' THEN 1 ELSE 0 END) as total_orders,
                        SUM(CASE WHEN a.action = 'balance' THEN 1 ELSE 0 END) as total_balance,
                        MAX(a.timestamp) as last_active FROM activity_log a
                 LEFT JOIN user_info u ON a.user_id = u.user_id GROUP BY a.user_id ORDER BY last_active DESC""")
    res = c.fetchall()
    conn.close()
    return res

def format_user_label(user_id, first_name, last_name, username):
    name = first_name or "Unknown"
    if last_name: name += f" {last_name}"
    if username: name += f" (@{username})"
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
# API HELPER (HERO-SMS)
# =============================================
def req_api(api_key, action, **kwargs):
    params = {'api_key': api_key, 'action': action}
    params.update(kwargs)
    try:
        r = requests.get(API_BASE, params=params, timeout=15)
        return r.text.strip()
    except Exception as e: return f"ERROR: {str(e)}"

def strip_country_code(number, country_code="84"):
    number = str(number).strip()
    if number.startswith("+"): number = number[1:]
    if number.startswith(str(country_code)): number = number[len(str(country_code)):]
    return number

def get_country_label(country_key):
    c = COUNTRIES.get(country_key, COUNTRIES["vietnam"])
    return f"{c['name']} {c['flag']}"

# =============================================
# FORMAT PESAN ORDER
# =============================================
def format_order_message(orders, title="", country_key="vietnam", start_index=1, show_progress=True):
    country = COUNTRIES.get(country_key, COUNTRIES["vietnam"])
    lines = []
    if title: lines.append(title); lines.append("")

    done_count = 0
    total = len(orders)
    now = time.time()

    for i, order in enumerate(orders, start_index):
        number_local = strip_country_code(order['number'], country['country_code'])
        status = order.get('status', 'waiting')
        price_str = f" [💰 {order['price']} USD]" if order.get('price') else ""

        if status == 'waiting':
            elapsed = now - order.get('order_time', now)
            rem = max(0, OTP_TIMEOUT - elapsed)
            lines.append(f"{i}. `{number_local}` ⏳ *{int(rem//60):02d}:{int(rem%60):02d}*{price_str}")
        elif status == 'got_otp':
            lines.append(f"{i}. `{number_local}` ✅ `{order.get('code', '?')}`{price_str}")
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
        lines.append(""); lines.append(f"📊 Progress: {done_count}/{total}")
        if done_count >= total: lines.append("\n✅ *Semua order selesai!*")

    return "\n".join(lines)

def safe_edit_message(text, chat_id, message_id, markup=None):
    try:
        bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown", reply_markup=markup)
        return True
    except Exception as e:
        if "retry after" in str(e).lower() or "too many requests" in str(e).lower(): time.sleep(5)
        return "message is not modified" in str(e).lower()

# =============================================
# AUTO-CHECK OTP
# =============================================
def auto_check_otp(chat_id, message_id, orders, api_key, country_key="vietnam", is_autobuy_mode=False, s_idx=1):
    last_edit_time = 0
    last_timer_update = 0
    while True:
        waiting = [o for o in orders if o['status'] == 'waiting']
        if not waiting:
            if is_autobuy_mode and autobuy_active.get(chat_id, False): time.sleep(CHECK_INTERVAL); continue
            else:
                title = "" if is_autobuy_mode else f"🛒 *Order WA {get_country_label(country_key)} — Selesai*"
                safe_edit_message(format_order_message(orders, title, country_key, s_idx, not is_autobuy_mode), chat_id, message_id)
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
                o['status'] = 'got_otp'; o['code'] = res.split(':')[1] if ':' in res else '???'; changed = True
                req_api(api_key, 'setStatus', status='6', id=o['id'])
            elif res == 'STATUS_CANCEL': o['status'] = 'cancelled'; changed = True
            time.sleep(0.3)

        if changed or (now - last_timer_update >= 20):
            if now - last_edit_time >= 3:
                title = "" if is_autobuy_mode else f"🛒 *Order WA {get_country_label(country_key)}*"
                text = format_order_message(orders, title, country_key, s_idx, not is_autobuy_mode)
                markup = InlineKeyboardMarkup()
                rem = [o for o in orders if o['status'] == 'waiting']
                if rem:
                    oldest = min(o['order_time'] for o in rem)
                    if (now - oldest) >= CANCEL_DELAY:
                        markup.row(InlineKeyboardButton("🚫 Batalkan Order", callback_data=f"cancelall_{','.join([o['id'] for o in rem])}"))
                    else:
                        markup.row(InlineKeyboardButton(f"⏳ Cancel tersedia ~{int((CANCEL_DELAY-(now-oldest))/60)+1}m", callback_data="cancel_wait"))
                    if safe_edit_message(text, chat_id, message_id, markup): last_edit_time = last_timer_update = now
                else:
                    if safe_edit_message(text, chat_id, message_id): last_edit_time = last_timer_update = now
        time.sleep(CHECK_INTERVAL)

# =============================================
# ADMIN COMMANDS
# =============================================
@bot.message_handler(commands=['adduser'])
def adduser(message):
    if message.from_user.id != ADMIN_ID: return
    p = message.text.split()
    if len(p) > 1: add_to_whitelist(int(p[1]), message.from_user.id); bot.reply_to(message, "✅ User added.")

@bot.message_handler(commands=['removeuser'])
def removeuser(message):
    if message.from_user.id != ADMIN_ID: return
    p = message.text.split()
    if len(p) > 1: remove_from_whitelist(int(p[1])); bot.reply_to(message, "✅ User removed.")

@bot.message_handler(commands=['listusers'])
def listusers(message):
    if message.from_user.id != ADMIN_ID: return
    users = get_all_whitelisted()
    lines = [f"{('👑 ADMIN' if u[0]==ADMIN_ID else '👤 User')}: {u[0]} ({u[1]})" for u in users]
    bot.reply_to(message, "\n".join(lines) if lines else "Empty")

# =============================================
# USER COMMANDS
# =============================================
@bot.message_handler(commands=['start'])
def start_cmd(message):
    uid = message.from_user.id
    if not is_whitelisted(uid):
        bot.send_message(message.chat.id, f"🔒 *Akses Ditolak*\nID: `{uid}`\nHub Admin @hesssxb.", parse_mode="Markdown"); return
    update_user_info(message.from_user); api_key = get_user_api(uid)
    text = "🐻 *Bot OTP WhatsApp (Hero-SMS)*\n\nOrder otomatis gaya Grizzly.\n\n"
    if api_key:
        bal = req_api(api_key, 'getBalance')
        if 'ACCESS_BALANCE' in bal: text += f"✅ API OK\n💰 Saldo: *{bal.split(':')[1]} USD*"
        else: text += "⚠️ API Invalid."
    else: text += "❌ Belum ada API."
    
    markup = InlineKeyboardMarkup()
    if api_key:
        markup.row(InlineKeyboardButton("🇻🇳 Vietnam", callback_data="country_vietnam"), InlineKeyboardButton("🇵🇭 Philipina", callback_data="country_philipina"))
        markup.row(InlineKeyboardButton("🇨🇴 Colombia", callback_data="country_colombia"), InlineKeyboardButton("💰 Saldo", callback_data="nav_balance"))
        markup.row(InlineKeyboardButton("🔥 Auto Buy", callback_data="nav_autobuy"), InlineKeyboardButton("🛑 Stop Auto", callback_data="nav_stopauto"))
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(commands=['setapi'])
def setapi(message):
    if not is_whitelisted(message.from_user.id): return
    p = message.text.split()
    if len(p) > 1:
        res = req_api(p[1], 'getBalance')
        if 'ACCESS_BALANCE' in res: set_user_api(message.from_user.id, p[1]); bot.reply_to(message, "✅ Saved.")
        else: bot.reply_to(message, "❌ Invalid.")

def process_bulk_order(chat_id, api_key, count, country_key):
    country = COUNTRIES[country_key]
    msg = bot.send_message(chat_id, f"⏳ Memesan {count} nomor...")
    orders = []
    # Get Price
    price_val = None
    try:
        res_p = req_api(api_key, 'getPrices', service=SERVICE, country=country['country_id'])
        if res_p.startswith("{"):
            data = json.loads(res_p)
            inner = data.get(country['country_id'], {}).get(SERVICE) or data.get(SERVICE, {}).get(country['country_id'])
            if inner: price_val = inner.get('cost') or min([float(k) for k in inner.keys() if k.replace('.','').isdigit()])
    except: pass

    for _ in range(count):
        res = req_api(api_key, 'getNumber', service=SERVICE, country=country['country_id'])
        if 'ACCESS_NUMBER' in res:
            p = res.split(':'); orders.append({'id':p[1], 'number':p[2], 'status':'waiting', 'order_time':time.time(), 'price':price_val, 'country_key':country_key})
        time.sleep(0.5)
    
    if orders:
        bot.edit_message_text(format_order_message(orders, f"🛒 *Order {country['name']}*", country_key), chat_id, msg.message_id, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().row(InlineKeyboardButton("⏳ Menunggu...", callback_data="cancel_wait")))
        if chat_id not in active_orders: active_orders[chat_id] = {}
        active_orders[chat_id][msg.message_id] = orders
        threading.Thread(target=auto_check_otp, args=(chat_id, msg.message_id, orders, api_key, country_key)).start()
    else: bot.edit_message_text("❌ Gagal.", chat_id, msg.message_id)

@bot.callback_query_handler(func=lambda call: True)
def callback_q(call):
    if not is_whitelisted(call.from_user.id): return
    api = get_user_api(call.from_user.id); data = call.data
    if data.startswith("country_"):
        ck = data.split("_")[1]; m = InlineKeyboardMarkup()
        m.row(*[InlineKeyboardButton(str(i), callback_data=f"quick_{ck}_{i}") for i in range(1,6)])
        bot.edit_message_text(f"Jumlah {ck}:", call.message.chat.id, call.message.message_id, reply_markup=m)
    elif data.startswith("quick_"):
        bot.answer_callback_query(call.id); p = data.split("_"); process_bulk_order(call.message.chat.id, api, int(p[2]), p[1])
    elif data == "nav_balance":
        bot.answer_callback_query(call.id, f"Saldo: {req_api(api, 'getBalance').split(':')[1]} USD", show_alert=True)
    elif data == "nav_autobuy":
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🇻🇳 VN", callback_data="auto_vietnam"), InlineKeyboardButton("🇵🇭 PH", callback_data="auto_philipina"), InlineKeyboardButton("🇨🇴 CO", callback_data="auto_colombia"))
        bot.edit_message_text("🔥 Pilih negara Auto Buy:", call.message.chat.id, call.message.message_id, reply_markup=m)
    elif data.startswith("auto_"):
        ck = data.split("_")[1]; autobuy_active[call.message.chat.id] = True; threading.Thread(target=autobuy_worker, args=(call.message.chat.id, api, ck)).start()
    elif data == "nav_stopauto": autobuy_active[call.message.chat.id] = False; bot.answer_callback_query(call.id, "🛑 Stopping...")
    elif data.startswith("cancelall_"):
        ids = data.split("_")[1].split(",")
        for i in ids: req_api(api, 'setStatus', status='8', id=i)
        bot.answer_callback_query(call.id, "✅ Dibatalkan")

# =============================================
# AUTO-BUY (BRUTAL)
# =============================================
autobuy_active = {}
def autobuy_worker(chat_id, api_key, country_key):
    try: status_msg = bot.send_message(chat_id, f"🔥 *AUTO BUY {country_key.upper()} AKTIF*\nBRUTAL MODE...", parse_mode="Markdown")
    except: status_msg = None
    country = COUNTRIES[country_key]; attempts = 0; order_cnt = 0
    while autobuy_active.get(chat_id, False):
        attempts += 1
        res = req_api(api_key, 'getNumber', service=SERVICE, country=country['country_id'])
        if 'ACCESS_NUMBER' in res:
            p = res.split(':'); order_cnt += 1
            # Get Price
            pr = None
            try:
                res_p = req_api(api_key, 'getPrices', service=SERVICE, country=country['country_id'])
                if res_p.startswith("{"):
                    d = json.loads(res_p)
                    inn = d.get(country['country_id'], {}).get(SERVICE) or d.get(SERVICE, {}).get(country['country_id'])
                    if inn: pr = inn.get('cost') or min([float(k) for k in inn.keys() if k.replace('.','').isdigit()])
            except: pass
            order = {'id':p[1], 'number':p[2], 'status':'waiting', 'order_time':time.time(), 'price':pr}
            text = format_order_message([order], "", country_key, order_cnt, False)
            try:
                msg = bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().row(InlineKeyboardButton("⏳ Wait...", callback_data="cancel_wait")))
                threading.Thread(target=auto_check_otp, args=(chat_id, msg.message_id, [order], api_key, country_key, True, order_cnt)).start()
            except: pass
        elif res == 'NO_BALANCE': break
        time.sleep(1)
    if status_msg: bot.edit_message_text("🛑 *AUTO BUY SELESAI*", chat_id, status_msg.message_id, parse_mode="Markdown")

if __name__ == '__main__':
    init_db()
    bot.infinity_polling()
