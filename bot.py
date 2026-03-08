import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import sqlite3
import json
import os
import threading
import time

# =============================================
# KONFIGURASI BOT
# =============================================
TOKEN = os.environ.get("BOT_TOKEN", "8766843422:AAGt3yP_3fwOO0Y-w7066-N-p0LRy8iqZKU")
bot = telebot.TeleBot(TOKEN)

API_BASE = "https://hero-sms.com/stubs/handler_api.php"
DB_PATH = os.environ.get("DB_PATH", "database.db")
ADMIN_ID = 940475417

MAX_ORDER = 20         
OTP_TIMEOUT = 1200     
CHECK_INTERVAL = 4     
CANCEL_DELAY = 120     
SERVICE = "wa"         

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
        "country_id": 10,
        "country_code": "84",
        "maxPrice": "0.2"
    },
    "philipina": {
        "name": "Philipina",
        "flag": "🇵🇭",
        "country_id": 3,
        "country_code": "63",
        "maxPrice": "0.2"
    },
    "colombia": {
        "name": "Colombia",
        "flag": "🇨🇴",
        "country_id": 33,
        "country_code": "57",
        "maxPrice": "0.2"
    },
}

active_orders = {}
autobuy_active = {} 

# =============================================
# DATABASE
# =============================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, api_key TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY, added_by INTEGER, added_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_info (user_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, username TEXT, last_seen TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute("INSERT OR IGNORE INTO whitelist (user_id, added_by) VALUES (?, ?)", (ADMIN_ID, ADMIN_ID))
    for x in env_whitelist.split(","):
        x_clean = "".join(filter(str.isdigit, x))
        if x_clean: c.execute("INSERT OR IGNORE INTO whitelist (user_id, added_by) VALUES (?, ?)", (int(x_clean), ADMIN_ID))
    conn.commit()
    conn.close()

def is_whitelisted(user_id):
    if user_id == ADMIN_ID or user_id in PERMANENT_WHITELIST: return True
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM whitelist WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res is not None

def update_user_info(user):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO user_info (user_id, first_name, last_name, username) VALUES (?, ?, ?, ?)",
              (user.id, user.first_name, user.last_name, user.username))
    conn.commit()
    conn.close()

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
# API HELPERS
# =============================================
def req_api(api_key, action, **kwargs):
    params = {'api_key': api_key, 'action': action}
    params.update(kwargs)
    try:
        r = requests.get(API_BASE, params=params, timeout=12)
        return r.text.strip()
    except Exception as e: return f"ERROR: {str(e)}"

def get_actual_price(api_key, cid_str):
    try:
        params = {'api_key': api_key, 'action': 'getPrices', 'service': SERVICE, 'country': str(cid_str)}
        r = requests.get(API_BASE, params=params, timeout=5)
        txt = r.text.strip()
        if txt.startswith("{"):
            d = json.loads(txt)
            inner = d.get(str(cid_str), {}).get(SERVICE) or d.get(SERVICE, {}).get(str(cid_str))
            if inner and isinstance(inner, dict):
                if "cost" in inner: return inner["cost"]
                nums = [float(k) for k in inner.keys() if k.replace('.', '', 1).isdigit()]
                if nums: return min(nums)
    except: pass
    return None

def strip_country_code(number, country_code):
    number = str(number).strip()
    if number.startswith("+"): number = number[1:]
    if number.startswith(str(country_code)): number = number[len(str(country_code)):]
    return number

# =============================================
# UI FORMATTER (GRIZZLY STYLE)
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
def auto_check_otp(cid, mid, orders, api_key, country_key, is_auto, s_idx):
    last_edit = 0
    while True:
        target = [o for o in orders if o['status'] == 'waiting']
        if not target: break
        
        now = time.time()
        changed = False
        
        # Check expired
        for o in orders:
            if o['status'] == 'waiting' and (now - o['order_time'] > OTP_TIMEOUT):
                o['status'] = 'timeout'
                req_api(api_key, 'setStatus', status='8', id=o['id'])
                changed = True

        # Check status from API
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

        if changed or (now - last_edit > 10):
            text = format_order_message(orders, "", country_key, s_idx)
            markup = InlineKeyboardMarkup()
            rem = [o for o in orders if o['status'] == 'waiting']
            if rem:
                oldest = min(o['order_time'] for o in rem)
                if (now - oldest) >= CANCEL_DELAY:
                    m_ids = ",".join([o['id'] for o in rem])
                    markup.row(InlineKeyboardButton("🚫 Batalkan Sisa", callback_data=f"cancelall_{m_ids}"))
                else:
                    markup.row(InlineKeyboardButton("⏳ Cancel tersedia nanti", callback_data="cancel_wait"))
                if safe_edit(text, cid, mid, markup): last_edit = now
            else:
                if safe_edit(text, cid, mid): last_edit = now
        
        time.sleep(CHECK_INTERVAL)

def autobuy_worker(cid, api_key, country_key):
    country = COUNTRIES[country_key]
    status_msg = bot.send_message(cid, f"🔥 *AUTO BUY {country['name'].upper()} AKTIF*\n\nMencari nomor nonstop...\nKetik /stopauto untuk berhenti.", parse_mode="Markdown")
    cnt = 0
    attempts = 0
    start_time = time.time()
    last_status_update = 0

    while autobuy_active.get(cid) == country_key:
        attempts += 1
        now = time.time()
        
        # Update Main Log Message
        if now - last_status_update > 8:
            elapsed_m = int((now - start_time) // 60)
            elapsed_s = int((now - start_time) % 60)
            try:
                bot.edit_message_text(
                    f"🔥 *AUTO BUY {country['name'].upper()} AKTIF (BRUTAL)*\n\n"
                    f"🔄 *Percobaan API:* {attempts}x\n"
                    f"📈 *Berhasil:* {cnt} nomor\n"
                    f"⏱ *Waktu:* {elapsed_m}m {elapsed_s}s\n"
                    f"🛑 Klik tombol stop di Menu Utama.",
                    cid, status_msg.message_id, parse_mode="Markdown"
                )
                last_status_update = now
            except: pass

        res = req_api(api_key, 'getNumber', service=SERVICE, country=country['country_id'], maxPrice=country['maxPrice'])
        if 'ACCESS_NUMBER' in res:
            parts = res.split(':')
            t_id, number = parts[1], parts[2]
            cnt += 1
            pr = get_actual_price(api_key, country['country_id'])
            order = {'id': t_id, 'number': number, 'status': 'waiting', 'order_time': time.time(), 'price': pr}
            
            # Send separate bubble
            order_text = format_order_message([order], "", country_key, cnt)
            try:
                m_btn = InlineKeyboardMarkup().row(InlineKeyboardButton("⏳ Cancel ~2m", callback_data="cancel_wait"))
                msg = bot.send_message(cid, order_text, parse_mode="Markdown", reply_markup=m_btn)
                threading.Thread(target=auto_check_otp, args=(cid, msg.message_id, [order], api_key, country_key, True, cnt)).start()
            except: pass
        elif res == 'NO_BALANCE': 
            bot.send_message(cid, "❌ *Saldo Habis!* Auto buy dihentikan.", parse_mode="Markdown")
            autobuy_active[cid] = False
            break
        time.sleep(0.5)

# =============================================
# HANDLERS
# =============================================
@bot.message_handler(commands=['start'])
def start_cmd(message):
    uid = message.from_user.id
    if not is_whitelisted(uid):
        bot.send_message(message.chat.id,
            "🔒 *Akses Ditolak*\n\n"
            "Bot ini diproteksi. Hanya ID terdaftar yang bisa mengakses.\n"
            f"ID Telegram Anda: `{uid}`\n"
            "Kirimkan ID di atas ke Admin @hesssxb.", parse_mode="Markdown")
        return
    
    update_user_info(message.from_user)
    api_key = get_user_api(uid)
    text = (
        "🐻 *Bot OTP WhatsApp (Hero-SMS)*\n\n"
        "Bot ini untuk order nomor WA dengan fitur auto-buy brutal.\n"
        "Pilih negara atau gunakan menu di bawah.\n\n"
        "🌍 *Negara:* VN (10), PH (3), CO (33)\n"
        "📋 `/setapi API_KEY` — Daftarkan Akun\n"
        "📋 `/order N` — Bulk Order\n"
        "📋 `/help` — Bantuan\n\n"
    )
    
    if api_key:
        bal_res = req_api(api_key, 'getBalance')
        if 'ACCESS_BALANCE' in bal_res:
            bal = bal_res.split(':')[1]
            text += f"✅ API: Terdaftar\n💰 Saldo: *{bal} USD*"
        else: text += "⚠️ API terdaftar tapi tidak valid."
    else: text += "❌ Belum ada API Key. Gunakan `/setapi API`"

    markup = InlineKeyboardMarkup()
    if api_key:
        markup.row(
            InlineKeyboardButton("🇻🇳 Vietnam", callback_data="country_vietnam"),
            InlineKeyboardButton("🇵🇭 Philipina", callback_data="country_philipina"),
            InlineKeyboardButton("🇨🇴 Colombia", callback_data="country_colombia")
        )
        markup.row(
            InlineKeyboardButton("🛒 Order Baru", callback_data="nav_order"),
            InlineKeyboardButton("💰 Cek Saldo", callback_data="nav_balance")
        )
        current_auto = autobuy_active.get(message.chat.id)
        if current_auto:
            markup.row(InlineKeyboardButton("🛑 STOP AUTO BUY", callback_data="nav_stop"))
        else:
            markup.row(InlineKeyboardButton("🔥 MULTI AUTO BUY", callback_data="nav_autobuy"))
            
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(commands=['setapi'])
def setapi(message):
    if not is_whitelisted(message.from_user.id): return
    p = message.text.split()
    if len(p) > 1:
        api_key = p[1].strip()
        res = req_api(api_key, 'getBalance')
        if 'ACCESS_BALANCE' in res:
            set_user_api(message.from_user.id, api_key)
            bot.reply_to(message, "✅ *API Key Valid & Tersimpan!*", parse_mode="Markdown")
        else: bot.reply_to(message, "❌ API Key tidak valid.")

@bot.message_handler(commands=['stopauto'])
def stopauto(message):
    autobuy_active[message.chat.id] = False
    bot.reply_to(message, "🛑 Auto Buy dihentikan.")

@bot.callback_query_handler(func=lambda call: True)
def calls(call):
    uid = call.from_user.id
    if not is_whitelisted(uid): return
    api = get_user_api(uid)
    cid = call.message.chat.id
    mid = call.message.message_id

    if call.data.startswith("country_"):
        ck = call.data.split("_")[1]
        m = InlineKeyboardMarkup()
        # Row 1,2,3...5
        btn_row = [InlineKeyboardButton(str(i), callback_data=f"buy_{ck}_{i}") for i in range(1, 6)]
        m.row(*btn_row)
        bot.edit_message_text(f"Pilih jumlah order {COUNTRIES[ck]['name']}:", cid, mid, reply_markup=m)
    
    elif call.data.startswith("buy_"):
        _, ck, count = call.data.split("_")
        process_bulk(cid, api, int(count), ck)
        
    elif call.data == "nav_balance":
        res = req_api(api, 'getBalance')
        if 'ACCESS_BALANCE' in res:
            bot.answer_callback_query(call.id, f"💰 Saldo: {res.split(':')[1]} USD", show_alert=True)
        else: bot.answer_callback_query(call.id, "❌ Gagal cek saldo")

    elif call.data == "nav_autobuy":
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🇻🇳 Vietnam", callback_data="auto_vietnam"))
        m.row(InlineKeyboardButton("🇵🇭 Philipina", callback_data="auto_philipina"))
        m.row(InlineKeyboardButton("🇨🇴 Colombia", callback_data="auto_colombia"))
        bot.edit_message_text("🔥 *Pilih negara untuk MULTI AUTO BUY:*", cid, mid, parse_mode="Markdown", reply_markup=m)

    elif call.data.startswith("auto_"):
        ck = call.data.replace("auto_", "")
        autobuy_active[cid] = ck
        threading.Thread(target=autobuy_worker, args=(cid, api, ck)).start()
        bot.answer_callback_query(call.id, f"🚀 Auto Buy {ck.upper()} Dimulai!")
        
    elif call.data == "nav_stop":
        autobuy_active[cid] = False
        bot.answer_callback_query(call.id, "🛑 Auto Buy Berhenti.")
        # Refresh Start
        start_cmd(call.message)

    elif call.data == "cancel_wait":
        bot.answer_callback_query(call.id, "⏳ Belum bisa cancel. Tunggu minimal 2 menit.", show_alert=True)

    elif call.data.startswith("cancelall_"):
        ids = call.data.split("_")[1].split(",")
        for i in ids: req_api(api, 'setStatus', status='8', id=i)
        bot.answer_callback_query(call.id, "✅ Sisa order dibatalkan.")

def process_bulk(cid, api, count, country_key):
    cntry = COUNTRIES[country_key]
    msg = bot.send_message(cid, f"⏳ Memesan {count} nomor {cntry['name']}...")
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
        threading.Thread(target=auto_check_otp, args=(cid, msg.message_id, orders, api, country_key, False, 1)).start()
    else: bot.edit_message_text(f"❌ Nomor {cntry['name']} tidak tersedia.", cid, msg.message_id)

if __name__ == '__main__':
    init_db()
    print("Bot Hero-SMS Grizzly Clone Running...")
    bot.infinity_polling()
