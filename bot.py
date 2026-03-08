import telebot
# Trigger redeploy - Cloned from Grizzly 100%
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import sqlite3
import json
import os
import threading
import time

# =============================================
# KONFIGURASI (HERO-SMS VERSION)
# =============================================
TOKEN = os.environ.get("BOT_TOKEN", "8766843422:AAGt3yP_3fwOO0Y-w7066-N-p0LRy8iqZKU")
bot = telebot.TeleBot(TOKEN)

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
    },
    "philipina": {
        "name": "Philipina",
        "flag": "🇵🇭",
        "country_id": "3",  # User requested ID 3
        "country_code": "63",
    },
    "colombia": {
        "name": "Colombia",
        "flag": "🇨🇴",
        "country_id": "33",
        "country_code": "57",
    },
}

# Menyimpan data order aktif per chat_id agar callback bisa akses
active_orders = {}

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
    
    # Masukkan otomatis semua ID dari environment variable ke dalam sqlite database
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
    """Cek apakah user ada di whitelist"""
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
    """Tambahkan user ke whitelist"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO whitelist (user_id, added_by) VALUES (?, ?)", (user_id, added_by))
    conn.commit()
    conn.close()

def remove_from_whitelist(user_id):
    """Hapus user dari whitelist"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM whitelist WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_all_whitelisted():
    """Dapatkan semua user yang ada di whitelist"""
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
    """Simpan/update info user (nama, username)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO user_info (user_id, first_name, last_name, username, last_seen)
                 VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
              (user.id, user.first_name, user.last_name or '', user.username or ''))
    conn.commit()
    conn.close()

def get_user_info(user_id):
    """Dapatkan info user dari DB"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT first_name, last_name, username, last_seen FROM user_info WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res

def log_activity(user_id, action, detail=""):
    """Catat aktivitas user"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO activity_log (user_id, action, detail) VALUES (?, ?, ?)",
              (user_id, action, detail))
    conn.commit()
    conn.close()

def get_active_users():
    """Dapatkan user yang terakhir aktif beserta info-nya"""
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
    """Dapatkan statistik penggunaan per user"""
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
    """Format label user dengan nama dan username"""
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
    """Hapus country code dari nomor, sisakan nomor lokal saja"""
    number = str(number).strip()
    if number.startswith("+"):
        number = number[1:]
    if number.startswith(str(country_code)):
        number = number[len(str(country_code)):]
    return number

def get_country_label(country_key):
    """Dapatkan label negara dengan flag"""
    c = COUNTRIES.get(country_key, COUNTRIES["vietnam"])
    return f"{c['name']} {c['flag']}"

# =============================================
# FORMAT PESAN ORDER
# =============================================
def format_order_message(orders, title="", country_key="vietnam", start_index=1, show_progress=True):
    """Format pesan daftar order dengan status OTP"""
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
        # Format harga: [💰 0.203 USD]
        price_str = f" [💰 {order['price']} USD]" if order.get('price') else ""

        if status == 'waiting':
            elapsed = now - order.get('order_time', now)
            remaining = max(0, OTP_TIMEOUT - elapsed)
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            # Minimalist: i. Nomor ⏳ 05:20
            lines.append(f"{i}. `{number_local}` ⏳ *{mins:02d}:{secs:02d}*{price_str}")
        elif status == 'got_otp':
            code = order.get('code', '???')
            # Minimalist: i. Nomor ✅ 123456
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
    """Edit pesan dengan handling rate limit dan error"""
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
    """Background thread yang otomatis cek OTP untuk semua order"""
    country = COUNTRIES.get(country_key, COUNTRIES["vietnam"])
    country_label = get_country_label(country_key)
    now = time.time()
    last_edit_time = 0
    EDIT_COOLDOWN = 3
    last_timer_update = 0

    try:
        while True:
            waiting_orders = [o for o in orders if o['status'] == 'waiting']
            if not waiting_orders:
                if is_autobuy_mode and autobuy_active.get(chat_id, False):
                    # Jika di mode autobuy, tetap hidup karena order baru bisa saja masuk ke list ini
                    time.sleep(CHECK_INTERVAL)
                    continue
                else:
                    text_title = "" if is_autobuy_mode else f"🛒 *Order WA {country_label} — Selesai*"
                    text = format_order_message(orders, text_title, country_key, start_index=s_idx, show_progress=(not is_autobuy_mode))
                    safe_edit_message(text, chat_id, message_id)
                    break

            now = time.time()
            # Cek timeout per order
            for o in orders:
                if o['status'] == 'waiting':
                    o_elapsed = now - o.get('order_time', now)
                    if o_elapsed > OTP_TIMEOUT:
                        o['status'] = 'timeout'
                        try:
                            req_api(api_key, 'setStatus', status='8', id=o['id'])
                        except:
                            pass

            changed = False
            for o in orders:
                if o['status'] != 'waiting':
                    continue
                try:
                    res = req_api(api_key, 'getStatus', id=o['id'])
                    if res.startswith('STATUS_OK'):
                        code = res.split(':')[1] if ':' in res else '???'
                        o['status'] = 'got_otp'
                        o['code'] = code
                        changed = True
                        try:
                            req_api(api_key, 'setStatus', status='6', id=o['id'])
                        except:
                            pass
                    elif res == 'STATUS_CANCEL':
                        o['status'] = 'cancelled'
                        changed = True
                except:
                    pass
                time.sleep(0.3)

            now = time.time()
            # In individual message mode, we update timer less frequently to avoid global rate limits
            # across many active messages. Status change (changed=True) always updates immediately.
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

            time.sleep(CHECK_INTERVAL + 1) # Extra breath for rate limits

    except Exception as e:
        print(f"Auto-check OTP thread error: {e}")
        try:
            country_label = get_country_label(country_key)
            text_title = "🎯 *TARGET DIDAPATKAN (AUTO BUY)*" if is_autobuy_mode else f"🛒 *Order WA {country_label} — Error*"
            text = format_order_message(orders, text_title, country_key)
            if not is_autobuy_mode:
                text += f"\n\n⚠️ Bot error: cek ulang dengan /start"
            safe_edit_message(text, chat_id, message_id)
        except:
            pass
    finally:
        try:
            if chat_id in active_orders and message_id in active_orders[chat_id]:
                del active_orders[chat_id][message_id]
        except:
            pass

# =============================================
# COMMAND HANDLERS
# =============================================

# --- ADMIN COMMANDS ---
@bot.message_handler(commands=['adduser'])
def adduser_cmd(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Hanya admin yang bisa menggunakan perintah ini.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Format: `/adduser USER_ID`", parse_mode="Markdown")
        return
    try:
        target_id = int(parts[1].strip())
    except ValueError:
        bot.reply_to(message, "❌ User ID harus berupa angka.")
        return
    add_to_whitelist(target_id, message.from_user.id)
    bot.reply_to(message, f"✅ User `{target_id}` berhasil ditambahkan.", parse_mode="Markdown")

@bot.message_handler(commands=['removeuser'])
def removeuser_cmd(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Hanya admin yang bisa menggunakan perintah ini.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Format: `/removeuser USER_ID`", parse_mode="Markdown")
        return
    try:
        target_id = int(parts[1].strip())
    except ValueError:
        bot.reply_to(message, "❌ User ID harus berupa angka.")
        return
    remove_from_whitelist(target_id)
    bot.reply_to(message, f"✅ User `{target_id}` dihapus.", parse_mode="Markdown")

@bot.message_handler(commands=['listusers'])
def listusers_cmd(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Hanya admin yang bisa menggunakan perintah ini.")
        return
    users = get_all_whitelisted()
    lines = ["📋 *Daftar Whitelist:*\n"]
    for uid, added_at in users:
        lines.append(f"👤 `{uid}` | Ditambahkan: {added_at}")
    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")

@bot.message_handler(commands=['activeusers'])
def activeusers_cmd(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Hanya admin yang bisa menggunakan perintah ini.")
        return
    active = get_active_users()
    lines = ["📊 *User Aktif Terakhir:*\n"]
    for i, (uid, fname, lname, uname, action, detail, ts) in enumerate(active, 1):
        lines.append(f"{i}. `{uid}` — {action} — {ts}")
    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")

@bot.message_handler(commands=['stats'])
def stats_cmd(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Hanya admin yang bisa menggunakan perintah ini.")
        return
    stats = get_user_stats()
    lines = ["📈 *Statistik Penggunaan Bot:*\n"]
    for uid, fname, lname, uname, total, orders, balance, last_active in stats:
        lines.append(f"👤 `{uid}` | 📦 Order: {orders} | 📊 Total: {total}")
    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")

# --- USER COMMANDS ---
@bot.message_handler(commands=['start'])
def start_cmd(message):
    user_id = message.from_user.id
    if not is_whitelisted(user_id):
        bot.send_message(message.chat.id,
            "🔒 *Akses Ditolak*\n\n"
            "Bot ini diproteksi. Hanya ID yang terdaftar yang bisa mengaksesnya.\n"
            f"ID Telegram Anda: `{message.from_user.id}`\n"
            "Kirimkan angka ID di atas ke Admin @hesssxb.",
            parse_mode="Markdown")
        return

    update_user_info(message.from_user)
    log_activity(user_id, "start")
    api_key = get_user_api(user_id)

    text = (
        "🐻 *Bot OTP WhatsApp (Hero-SMS)* \n\n"
        "Bot ini untuk order nomor WhatsApp dengan OTP otomatis.\n"
        "Pilih negara, lalu pilih jumlah nomor yang ingin di-order.\n\n"
        "🌍 *Negara tersedia:*\n"
        "🇻🇳 Vietnam (Country ID: 10)\n"
        "🇵🇭 Philipina (Country ID: 3)\n"
        "🇨🇴 Colombia (Country ID: 33)\n\n"
        "📋 *Perintah:*\n"
        "`/setapi API_KEY` — Daftarkan API Key Hero-SMS\n"
        "`/order N` — Order N nomor (pilih negara dulu)\n"
        "`/balance` — Cek saldo\n"
        "`/autobuy` — Auto buy WA sampai saldo habis\n"
        "`/stopauto` — Hentikan auto buy\n"
        "`/help` — Bantuan\n\n"
    )

    if api_key:
        bal_res = req_api(api_key, 'getBalance')
        if 'ACCESS_BALANCE' in bal_res:
            bal = bal_res.split(':')[1]
            text += f"✅ API Key: Terdaftar\n💰 Saldo: *{bal} USD*"
        else:
            text += "⚠️ API Key terdaftar tapi tidak valid.\nGunakan `/setapi API_KEY` untuk mengganti."
    else:
        text += "❌ Belum ada API Key.\nGunakan `/setapi API_KEY` untuk mendaftar."

    markup = InlineKeyboardMarkup()
    if api_key:
        # Baris 1: Negara
        markup.row(
            InlineKeyboardButton("🇻🇳 Vietnam", callback_data="country_vietnam"),
            InlineKeyboardButton("🇵🇭 Philipina", callback_data="country_philipina"),
            InlineKeyboardButton("🇨🇴 Colombia", callback_data="country_colombia")
        )
        # Baris 2: Order & Cek Saldo
        markup.row(
            InlineKeyboardButton("🛒 Order Baru", callback_data="nav_order"),
            InlineKeyboardButton("💰 Cek Saldo", callback_data="nav_balance")
        )
        # Baris 3: Fitur Auto
        markup.row(
            InlineKeyboardButton("🔥 Auto Buy", callback_data="nav_autobuy"),
            InlineKeyboardButton("🛑 Stop Auto", callback_data="nav_stopauto")
        )
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(commands=['help'])
def help_cmd(message):
    if not is_whitelisted(message.from_user.id): return
    text = (
        "📖 *Panduan Penggunaan*\n\n"
        "1️⃣ Daftarkan API Key dari akun Hero-SMS Anda:\n"
        "   `/setapi API_KEY_ANDA`\n\n"
        "2️⃣ Ketik `/start` lalu pilih negara.\n\n"
        "3️⃣ Pilih jumlah nomor (1-5).\n\n"
        "4️⃣ Bot akan otomatis cek OTP.\n\n"
        "⏱ Timeout: 20 menit"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=['setapi'])
def setapi_cmd(message):
    if not is_whitelisted(message.from_user.id): return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Format: `/setapi API_KEY_KAMU`", parse_mode="Markdown")
        return
    api_key = parts[1].strip()
    bal_res = req_api(api_key, 'getBalance')
    if 'ACCESS_BALANCE' in bal_res:
        set_user_api(message.from_user.id, api_key)
        bot.send_message(message.chat.id, "✅ API Key valid & tersimpan!", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "❌ API Key tidak valid.")

@bot.message_handler(commands=['balance'])
def balance_cmd(message):
    if not is_whitelisted(message.from_user.id): return
    api_key = get_user_api(message.from_user.id)
    if not api_key: return
    bal_res = req_api(api_key, 'getBalance')
    if 'ACCESS_BALANCE' in bal_res:
        bot.reply_to(message, f"💰 Saldo: *{bal_res.split(':')[1]} USD*", parse_mode="Markdown")

@bot.message_handler(commands=['order'])
def order_cmd(message):
    if not is_whitelisted(message.from_user.id): return
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🇻🇳 Vietnam", callback_data="country_vietnam"),
        InlineKeyboardButton("🇵🇭 Philipina", callback_data="country_philipina"),
        InlineKeyboardButton("🇨🇴 Colombia", callback_data="country_colombia")
    )
    bot.send_message(message.chat.id, "🌍 *Pilih negara untuk order:*", parse_mode="Markdown", reply_markup=markup)

def process_bulk_order(chat_id, api_key, count, country_key="vietnam"):
    country = COUNTRIES.get(country_key, COUNTRIES["vietnam"])
    # Get Price
    price_val = None
    try:
        res_p = req_api(api_key, 'getPrices', service=SERVICE, country=country['country_id'])
        if res_p.startswith("{"):
            d = json.loads(res_p)
            inner = d.get(country['country_id'], {}).get(SERVICE) or d.get(SERVICE, {}).get(country['country_id'])
            if inner:
                if 'cost' in inner: price_val = inner['cost']
                else: 
                     nk = [float(k) for k in inner.keys() if k.replace('.','').isdigit()]
                     if nk: price_val = min(nk)
    except: pass

    msg = bot.send_message(chat_id, f"⏳ Sedang memesan {count} nomor WA...", parse_mode="Markdown")
    orders = []
    for i in range(count):
        res = req_api(api_key, 'getNumber', service=SERVICE, country=country['country_id'])
        if 'ACCESS_NUMBER' in res:
            p = res.split(':')
            orders.append({'id':p[1], 'number':p[2], 'status':'waiting', 'order_time':time.time(), 'price':price_val})
        time.sleep(0.5)

    if orders:
        bot.edit_message_text(format_order_message(orders, f"🛒 *Order WA {get_country_label(country_key)}*", country_key), chat_id, msg.message_id, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().row(InlineKeyboardButton("⏳ Wait...", callback_data="cancel_wait")))
        if chat_id not in active_orders: active_orders[chat_id] = {}
        active_orders[chat_id][msg.message_id] = orders
        threading.Thread(target=auto_check_otp, args=(chat_id, msg.message_id, orders, api_key, country_key)).start()
    else: bot.edit_message_text("❌ Gagal memesan nomor.", chat_id, msg.message_id)

@bot.callback_query_handler(func=lambda call: True)
def callback_q(call):
    uid = call.from_user.id
    if not is_whitelisted(uid): return
    api_key = get_user_api(uid)
    data = call.data
    
    if data.startswith("country_"):
        ck = data.replace("country_", "")
        m = InlineKeyboardMarkup()
        m.row(*[InlineKeyboardButton(str(i), callback_data=f"quick_{ck}_{i}") for i in range(1, 6)])
        bot.edit_message_text(f"🌍 *Negara: {get_country_label(ck)}*\nPilih jumlah:", call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=m)
    
    elif data.startswith("quick_"):
        parts = data.split("_")
        process_bulk_order(call.message.chat.id, api_key, int(parts[2]), parts[1])

    elif data == "nav_balance":
        bal_res = req_api(api_key, 'getBalance')
        if 'ACCESS_BALANCE' in bal_res:
            bot.answer_callback_query(call.id, f"💰 Saldo: {bal_res.split(':')[1]} USD", show_alert=True)

    elif data == "nav_autobuy":
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🇻🇳 VN", callback_data="auto_vietnam"), InlineKeyboardButton("🇵🇭 PH", callback_data="auto_philipina"), InlineKeyboardButton("🇨🇴 CO", callback_data="auto_colombia"))
        bot.edit_message_text("🔥 *Pilih negara Auto Buy:*", call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=m)

    elif data.startswith("auto_"):
        ck = data.replace("auto_", "")
        autobuy_active[call.message.chat.id] = ck
        threading.Thread(target=autobuy_worker, args=(call.message.chat.id, api_key, ck)).start()

    elif data == "nav_stopauto":
        autobuy_active[call.message.chat.id] = False
        bot.answer_callback_query(call.id, "🛑 Auto Buy Berhenti")

    elif data == "cancel_wait":
        bot.answer_callback_query(call.id, "⏳ Belum bisa cancel. Tunggu 2 menit.", show_alert=True)

    elif data.startswith("cancelall_"):
        ids = data.split("_")[1].split(",")
        for t_id in ids: req_api(api_key, 'setStatus', status='8', id=t_id)
        bot.answer_callback_query(call.id, "✅ Dibatalkan")

# =============================================
# AUTO-BUY (BRUTAL)
# =============================================
autobuy_active = {}
def autobuy_worker(chat_id, api_key, country_key):
    try: status_msg = bot.send_message(chat_id, f"🔥 *AUTO BUY {country_key.upper()} AKTIF*", parse_mode="Markdown")
    except: status_msg = None
    country = COUNTRIES[country_key]
    order_cnt = 0
    while autobuy_active.get(chat_id) == country_key:
        res = req_api(api_key, 'getNumber', service=SERVICE, country=country['country_id'])
        if 'ACCESS_NUMBER' in res:
            parts = res.split(':')
            order_cnt += 1
            # Get Price
            pr = None
            try:
                res_p = req_api(api_key, 'getPrices', service=SERVICE, country=country['country_id'])
                if res_p.startswith("{"):
                    d = json.loads(res_p)
                    inn = d.get(country['country_id'], {}).get(SERVICE) or d.get(SERVICE, {}).get(country['country_id'])
                    if inn: pr = inn.get('cost') or min([float(k) for k in inn.keys() if k.replace('.','').isdigit()])
            except: pass
            order = {'id':parts[1], 'number':parts[2], 'status':'waiting', 'order_time':time.time(), 'price':pr}
            try:
                msg = bot.send_message(chat_id, format_order_message([order], "", country_key, order_cnt, False), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().row(InlineKeyboardButton("⏳ Wait...", callback_data="cancel_wait")))
                threading.Thread(target=auto_check_otp, args=(chat_id, msg.message_id, [order], api_key, country_key, True, order_cnt)).start()
            except: pass
        elif res == 'NO_BALANCE': break
        time.sleep(1)

if __name__ == '__main__':
    init_db()
    bot.infinity_polling()
