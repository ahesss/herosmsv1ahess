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
OTP_TIMEOUT = 1200     # Timeout 20 menit (1200 detik)
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
        "maxPrice": "0.2" # Permintaan user 0.2
    },
    "philipina": {
        "name": "Philipina",
        "flag": "🇵🇭",
        "country_id": "4",
        "country_code": "63",
        "maxPrice": "0.2" # Permintaan user 0.2
    },
    "colombia": {
        "name": "Colombia",
        "flag": "🇨🇴",
        "country_id": "33",
        "country_code": "57",
        "maxPrice": "0.2" # Permintaan user 0.2
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
    # Pastikan admin selalu ada di whitelist
    c.execute("INSERT OR IGNORE INTO whitelist (user_id, added_by) VALUES (?, ?)", (ADMIN_ID, ADMIN_ID))
    
    # Masukkan otomatis ID dari environment variable
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
    env_wl = os.environ.get("WHITELIST_IDS", "")
    perm_wl = []
    for x in env_wl.split(","):
        x_clean = "".join(filter(str.isdigit, x))
        if x_clean:
            perm_wl.append(int(x_clean))
    
    if user_id == ADMIN_ID or user_id in perm_wl:
        return True
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
    c.execute("INSERT INTO activity_log (user_id, action, detail) VALUES (?, ?, ?)",
              (user_id, action, detail))
    conn.commit()
    conn.close()

def get_active_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT a.user_id, u.first_name, u.last_name, u.username, 
                        a.action, a.detail, a.timestamp
                 FROM activity_log a
                 LEFT JOIN user_info u ON a.user_id = u.user_id
                 WHERE a.id IN (
                     SELECT MAX(id) FROM activity_log GROUP BY user_id
                 )
                 ORDER BY a.timestamp DESC
                 LIMIT 20""")
    res = c.fetchall()
    conn.close()
    return res

def get_user_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT a.user_id, u.first_name, u.last_name, u.username,
                        COUNT(*) as total_actions,
                        SUM(CASE WHEN a.action = 'order' THEN 1 ELSE 0 END) as total_orders,
                        SUM(CASE WHEN a.action = 'balance' THEN 1 ELSE 0 END) as total_balance,
                        MAX(a.timestamp) as last_active
                 FROM activity_log a
                 LEFT JOIN user_info u ON a.user_id = u.user_id
                 GROUP BY a.user_id
                 ORDER BY last_active DESC""")
    res = c.fetchall()
    conn.close()
    return res

def format_user_label(user_id, first_name, last_name, username):
    name = first_name or "Unknown"
    if last_name:
        name += f" {last_name}"
    if username:
        name += f" (@{username})"
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
    except Exception as e:
        return f"ERROR: {str(e)}"

def strip_country_code(number, country_code="84"):
    number = number.strip()
    if number.startswith("+"):
        number = number[1:]
    if number.startswith(country_code):
        number = number[len(country_code):]
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
        if done_count >= total:
            lines.append("\n✅ *Semua order selesai!*")

    return "\n".join(lines)

def safe_edit_message(text, chat_id, message_id, markup=None):
    try:
        if markup:
            bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown", reply_markup=markup)
        else:
            bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown")
        return True
    except Exception as e:
        err_str = str(e).lower()
        if "retry after" in err_str or "too many requests" in err_str:
            time.sleep(5)
        elif "message is not modified" in err_str:
            return True
        else:
            print(f"Edit message error: {e}")
        return False

# =============================================
# AUTO-CHECK OTP (BACKGROUND THREAD)
# =============================================
def auto_check_otp(chat_id, message_id, orders, api_key, country_key="vietnam", is_autobuy_mode=False, s_idx=1):
    country = COUNTRIES.get(country_key, COUNTRIES["vietnam"])
    country_label = get_country_label(country_key)
    start_time = time.time()
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
                    text_title = "" if is_autobuy_mode else f"🛒 *Order WA {country_label} — Selesai*"
                    text = format_order_message(orders, text_title, country_key, start_index=s_idx, show_progress=(not is_autobuy_mode))
                    safe_edit_message(text, chat_id, message_id)
                    break

            now = time.time()
            for o in orders:
                if o['status'] == 'waiting':
                    o_elapsed = now - o.get('order_time', now)
                    if o_elapsed > OTP_TIMEOUT:
                        o['status'] = 'timeout'
                        try: req_api(api_key, 'setStatus', status='8', id=o['id'])
                        except: pass

            changed = False
            for o in orders:
                if o['status'] != 'waiting': continue
                try:
                    res = req_api(api_key, 'getStatus', id=o['id'])
                    if res.startswith('STATUS_OK'):
                        code = res.split(':')[1] if ':' in res else '???'
                        o['status'] = 'got_otp'
                        o['code'] = code
                        changed = True
                        try: req_api(api_key, 'setStatus', status='6', id=o['id'])
                        except: pass
                    elif res == 'STATUS_CANCEL':
                        o['status'] = 'cancelled'
                        changed = True
                except: pass
                time.sleep(0.3)

            now = time.time()
            should_update = changed or (now - last_timer_update >= 20)
            if should_update and (now - last_edit_time >= EDIT_COOLDOWN):
                remaining = [o for o in orders if o['status'] == 'waiting']
                text_title = "" if is_autobuy_mode else f"🛒 *Order WA {country_label}*"
                text = format_order_message(orders, text_title, country_key, start_index=s_idx, show_progress=(not is_autobuy_mode))

                if remaining:
                    markup = InlineKeyboardMarkup()
                    oldest_order_time = min(o.get('order_time', now) for o in remaining)
                    can_cancel = (now - oldest_order_time) >= CANCEL_DELAY

                    if can_cancel:
                        ids_str = ",".join([o['id'] for o in remaining])
                        markup.row(InlineKeyboardButton(
                            f"🚫 Batalkan ({len(remaining)})" if len(remaining) > 1 else "🚫 Batalkan Order",
                            callback_data=f"cancelall_{ids_str}"
                        ))
                    else:
                        wait_mins = int((CANCEL_DELAY - (now - oldest_order_time)) / 60) + 1
                        markup.row(InlineKeyboardButton(
                            f"⏳ Cancel tersedia ~{wait_mins} menit lagi",
                            callback_data="cancel_wait"
                        ))

                    if safe_edit_message(text, chat_id, message_id, markup):
                        last_edit_time = now
                        last_timer_update = now
                else:
                    if safe_edit_message(text, chat_id, message_id):
                        last_edit_time = now
                        last_timer_update = now

            time.sleep(CHECK_INTERVAL + 1)
    except Exception as e:
        print(f"Auto-check OTP thread error: {e}")
    finally:
        try:
            if chat_id in active_orders and message_id in active_orders[chat_id]:
                del active_orders[chat_id][message_id]
        except: pass

# =============================================
# AUTOBUY WORKER (BRUTAL MODE)
# =============================================
def autobuy_worker(chat_id, api_key):
    try:
        status_msg = bot.send_message(
            chat_id, 
            "🔥 *AUTO BUY AKTIF (HERO-SMS BRUTAL)*\n\n"
            "Mencari nomor nonstop sampai saldo habis...\n"
            "Ketik /stopauto untuk berhenti.\n\n"
            "⏳ *Status:* Memulai pencarian (Vietnam)...", 
            parse_mode="Markdown"
        )
    except: status_msg = None
        
    country_key = "vietnam"
    country = COUNTRIES[country_key]
    attempts = 0
    start_time = time.time()
    last_ui_update = time.time()
    orders_list = []
    order_counter = 0 
    
    while autobuy_active.get(chat_id, False):
        attempts += 1
        now = time.time()
        if status_msg and (now - last_ui_update > 7):
            elapsed_m = int((now - start_time) // 60)
            elapsed_s = int((now - start_time) % 60)
            target_count = len(orders_list)
            try:
                bot.edit_message_text(
                    f"🔥 *AUTO BUY AKTIF (BRUTAL MODE)*\n\n"
                    f"🔄 *Status:* Sedang mencari...\n"
                    f"📈 *Percobaan API:* {attempts}x\n"
                    f"⏱ *Waktu berjalan:* {elapsed_m}m {elapsed_s}s\n"
                    f"🎯 *Total didapat:* {target_count} nomor",
                    chat_id, status_msg.message_id, parse_mode="Markdown"
                )
                last_ui_update = now
            except: pass

        # Paramater maxPrice dari konfigurasi negara
        kwargs = {'service': SERVICE, 'country': country['country_id']}
        if 'maxPrice' in country:
            kwargs['maxPrice'] = country['maxPrice']
            
        res = req_api(api_key, 'getNumber', **kwargs)
        
        if 'ACCESS_NUMBER' in res:
            parts = res.split(':')
            if len(parts) >= 3:
                t_id, number = parts[1], parts[2]
                order_counter += 1
                
                # Fetch price
                price_val = None
                try:
                    params = {'api_key': api_key, 'action': 'getPrices', 'service': SERVICE, 'country': str(country['country_id'])}
                    r_p = requests.get(API_BASE, params=params, timeout=3)
                    p_data = json.loads(r_p.text.strip())
                    inner = None
                    c_id_str = str(country['country_id'])
                    if c_id_str in p_data and SERVICE in p_data[c_id_str]:
                        inner = p_data[c_id_str][SERVICE]
                    elif SERVICE in p_data and c_id_str in p_data[SERVICE]:
                        inner = p_data[SERVICE][c_id_str]
                    if inner and isinstance(inner, dict):
                        if "cost" in inner: price_val = inner["cost"]
                        else:
                            nums = [float(k) for k in inner.keys() if k.replace('.', '', 1).isdigit()]
                            if nums: price_val = min(nums)
                except: pass

                order = {'id': t_id, 'number': number, 'status': 'waiting', 'code': None, 'order_time': time.time(), 'country_key': country_key, 'price': price_val}
                orders_list.append(order)
                
                text = format_order_message([order], "", country_key, start_index=order_counter, show_progress=False)
                markup = InlineKeyboardMarkup()
                markup.row(InlineKeyboardButton(f"⏳ Cancel tersedia ~2 menit lagi", callback_data="cancel_wait"))
                
                try:
                    msg = bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)
                    if chat_id not in active_orders: active_orders[chat_id] = {}
                    active_orders[chat_id][msg.message_id] = [order]
                    threading.Thread(target=auto_check_otp, args=(chat_id, msg.message_id, [order], api_key, country_key, True, order_counter)).start()
                except: pass
                
                if status_msg:
                    try:
                        bot.edit_message_text(
                            f"🔥 *AUTO BUY AKTIF (BRUTAL MODE)*\n\n"
                            f"✅ *Target {order_counter} Didapat! Lanjut cari...*\n"
                            f"📈 *Total percobaan:* {attempts}x\n"
                            f"🎯 *Total didapat:* {len(orders_list)} nomor",
                            chat_id, status_msg.message_id, parse_mode="Markdown"
                        )
                    except: pass
                time.sleep(1) 

        elif res == 'NO_BALANCE':
            bot.send_message(chat_id, "❌ *AUTO BUY BERHENTI*\nSaldo Anda habis!", parse_mode="Markdown")
            autobuy_active[chat_id] = False
            break
        elif res == 'NO_NUMBERS': time.sleep(0.1)
        else: time.sleep(0.2)
        time.sleep(0.5) 

# =============================================
# COMMAND HANDLERS
# =============================================
@bot.message_handler(commands=['adduser'])
def adduser_cmd(message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    if len(parts) < 2: return
    try:
        t_id = int(parts[1])
        add_to_whitelist(t_id, message.from_user.id)
        bot.reply_to(message, f"✅ User `{t_id}` ditambahkan.")
    except: pass

@bot.message_handler(commands=['start'])
def start_cmd(message):
    if not is_whitelisted(message.from_user.id):
        bot.send_message(message.chat.id, f"🔒 *Akses Ditolak*\nID Anda: `{message.from_user.id}`", parse_mode="Markdown")
        return
    update_user_info(message.from_user)
    api_key = get_user_api(message.from_user.id)
    
    text = (
        f"👑 *Hero-SMS v1 Bot (Grizzly Style)*\n\n"
        f"🌍 *Negara:* Vietnam, Philippines, Colombia\n"
        f"💰 *Max Price:* 0.2 USD\n\n"
        f"`/setapi API_KEY` — Daftarkan API Key Hero-SMS\n"
        f"`/autobuy` — Brutal mode Vietnam\n"
        f"`/balance` — Cek saldo"
    )
    
    markup = InlineKeyboardMarkup()
    if api_key:
        markup.row(InlineKeyboardButton("🇻🇳 Vietnam", callback_data="country_vietnam"), InlineKeyboardButton("🇵🇭 Philipina", callback_data="country_philipina"))
        markup.row(InlineKeyboardButton("🇨🇴 Colombia", callback_data="country_colombia"), InlineKeyboardButton("💰 Cek Saldo", callback_data="nav_balance"))
        markup.row(InlineKeyboardButton("🔥 Auto Buy", callback_data="nav_autobuy"), InlineKeyboardButton("🛑 Stop Auto", callback_data="nav_stopauto"))
    
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(commands=['setapi'])
def setapi_cmd(message):
    if not is_whitelisted(message.from_user.id): return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return
    api_key = parts[1].strip()
    res = req_api(api_key, 'getBalance')
    if 'ACCESS_BALANCE' in res:
        set_user_api(message.from_user.id, api_key)
        bot.reply_to(message, "✅ API Key tersimpan!")
    else: bot.reply_to(message, "❌ Invalid API Key.")

@bot.message_handler(commands=['balance'])
def balance_cmd(message):
    if not is_whitelisted(message.from_user.id): return
    api_key = get_user_api(message.from_user.id)
    if not api_key: return
    res = req_api(api_key, 'getBalance')
    if 'ACCESS_BALANCE' in res:
        bot.reply_to(message, f"💰 Saldo: *{res.split(':')[1]} USD*", parse_mode="Markdown")

@bot.message_handler(commands=['autobuy'])
def autobuy_cmd(message):
    if not is_whitelisted(message.from_user.id): return
    api_key = get_user_api(message.from_user.id)
    if not api_key: return
    autobuy_active[message.chat.id] = True
    threading.Thread(target=autobuy_worker, args=(message.chat.id, api_key)).start()

@bot.message_handler(commands=['stopauto'])
def stopauto_cmd(message):
    autobuy_active[message.chat.id] = False
    bot.reply_to(message, "🛑 Stop.")

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    data = call.data
    api_key = get_user_api(call.from_user.id)
    
    if data.startswith("country_"):
        ck = data.split("_")[1]
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("1", callback_data=f"quick_{ck}_1"), InlineKeyboardButton("2", callback_data=f"quick_{ck}_2"), InlineKeyboardButton("3", callback_data=f"quick_{ck}_3"))
        markup.row(InlineKeyboardButton("4", callback_data=f"quick_{ck}_4"), InlineKeyboardButton("5", callback_data=f"quick_{ck}_5"))
        bot.edit_message_text(f"🛒 Pilih jumlah order ({ck}):", call.message.chat.id, call.message.message_id, reply_markup=markup)
        
    elif data.startswith("quick_"):
        parts = data.split("_")
        process_bulk_order(call.message.chat.id, api_key, int(parts[2]), parts[1])
        
    elif data == "nav_balance":
        balance_cmd(call.message)
    elif data == "nav_autobuy":
        autobuy_cmd(call.message)
    elif data == "nav_stopauto":
        stopauto_cmd(call.message)
    elif data == "cancel_wait":
        bot.answer_callback_query(call.id, "⏳ Tunggu 2 menit.", show_alert=True)
    elif data.startswith("cancelall_"):
        ids = data.split("_")[1].split(",")
        for t_id in ids: req_api(api_key, 'setStatus', status='8', id=t_id)
        bot.answer_callback_query(call.id, "✅ OK")

def process_bulk_order(chat_id, api_key, count, country_key):
    country = COUNTRIES[country_key]
    msg = bot.send_message(chat_id, f"⏳ Memesan {count} nomor...")
    orders = []
    for _ in range(count):
        kwargs = {'service': SERVICE, 'country': country['country_id']}
        if 'maxPrice' in country: kwargs['maxPrice'] = country['maxPrice']
        res = req_api(api_key, 'getNumber', **kwargs)
        if 'ACCESS_NUMBER' in res:
            parts = res.split(':')
            orders.append({'id': parts[1], 'number': parts[2], 'status': 'waiting', 'order_time': time.time(), 'country_key': country_key, 'price': None})
        time.sleep(0.5)
    
    if orders:
        text = format_order_message(orders, f"🛒 Order {get_country_label(country_key)}", country_key)
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("⏳ Cancel nanti", callback_data="cancel_wait"))
        bot.edit_message_text(text, chat_id, msg.message_id, parse_mode="Markdown", reply_markup=markup)
        threading.Thread(target=auto_check_otp, args=(chat_id, msg.message_id, orders, api_key, country_key)).start()
    else: bot.edit_message_text("❌ Gagal.", chat_id, msg.message_id)

@bot.message_handler(func=lambda message: True)
def catch_all(message):
    if not is_whitelisted(message.from_user.id):
        bot.reply_to(message, f"🔒 Locked. ID: `{message.from_user.id}`", parse_mode="Markdown")

if __name__ == '__main__':
    init_db()
    print("Hero-SMS Bot running...")
    bot.infinity_polling()
