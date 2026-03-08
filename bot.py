import telebot
# Trigger redeploy - SUPER BRUTAL NO-OVERWRITE VERSION
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
TOKEN = os.environ.get("BOT_TOKEN", "8766843422:AAGt3yP_3fwOO0Y-w7066-N-p0LRy8iqZKU")
bot = telebot.TeleBot(TOKEN)

API_BASE = "https://hero-sms.com/stubs/handler_api.php"
DB_PATH = os.environ.get("DB_PATH", "database.db")
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
# KONFIGURASI NEGARA
# =============================================
COUNTRIES = {
    "vietnam": {"name": "Vietnam", "flag": "🇻🇳", "country_id": "10", "country_code": "84"},
    "philipina": {"name": "Philipina", "flag": "🇵🇭", "country_id": "3", "country_code": "63"},
    "colombia": {"name": "Colombia", "flag": "🇨🇴", "country_id": "33", "country_code": "57"},
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
    c.execute('''CREATE TABLE IF NOT EXISTS activity_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT, detail TEXT, timestamp TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute("INSERT OR IGNORE INTO whitelist (user_id, added_by) VALUES (?, ?)", (ADMIN_ID, ADMIN_ID))
    env_wl = os.environ.get("WHITELIST_IDS", "")
    for x in env_wl.split(","):
        x_clean = "".join(filter(str.isdigit, x))
        if x_clean: c.execute("INSERT OR IGNORE INTO whitelist (user_id, added_by) VALUES (?, ?)", (int(x_clean), ADMIN_ID))
    conn.commit()
    conn.close()

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

def update_user_info(user):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO user_info (user_id, first_name, last_name, username, last_seen)
                 VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
              (user.id, user.first_name, user.last_name or '', user.username or ''))
    conn.commit()
    conn.close()

def log_activity(user_id, action, detail=""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO activity_log (user_id, action, detail) VALUES (?, ?, ?)", (user_id, action, detail))
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
            lines.append(f"{i}. `{number_local}` ✅ `{order.get('code', '?')}`{price_str}"); done_count += 1
        elif status == 'cancelled':
            lines.append(f"{i}. `{number_local}` 🚫 *Dibatalkan*"); done_count += 1
        elif status == 'timeout':
            lines.append(f"{i}. `{number_local}` ⏰ *Exp*"); done_count += 1
        elif status == 'error':
            lines.append(f"{i}. `{number_local}` ❌ *Error*"); done_count += 1
    if show_progress:
        lines.append(""); lines.append(f"📊 Progress: {done_count}/{total}")
        if done_count >= total: lines.append("\n✅ *Semua order selesai!*")
    return "\n".join(lines)

def safe_edit_message(text, chat_id, message_id, markup=None):
    try:
        if markup: bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown", reply_markup=markup)
        else: bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown")
        return True
    except Exception as e:
        if "retry after" in str(e).lower() or "too many requests" in str(e).lower(): time.sleep(5)
        return "message is not modified" in str(e).lower()

# =============================================
# MONITORING OTP
# =============================================
def auto_check_otp(chat_id, message_id, orders, api_key, country_key="vietnam", is_autobuy_mode=False, s_idx=1):
    last_edit_time = 0; last_timer_update = 0
    while True:
        target = [o for o in orders if o['status'] == 'waiting']
        if not target:
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
        now = time.time()
        should_update = changed or (now - last_timer_update >= 10)
        if should_update and (now - last_edit_time >= 3):
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
# AUTO-BUY SUPER BRUTAL
# =============================================
def autobuy_worker(chat_id, api_key, country_key):
    country = COUNTRIES[country_key]
    try:
        # NO OVERLAY: Kirim sebagai pesan baru
        status_msg = bot.send_message(
            chat_id, 
            f"🚀 *SUPER BRUTAL AUTO BUY {country['name'].upper()} AKTIF*\n\n"
            "Mencari nomor nonstop tanpa jeda...\n"
            "Ketik /stopauto atau tekan tombol stop.\n\n"
            "⚡ *Status:* Mode Brutal ON!", 
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup().row(InlineKeyboardButton("🛑 STOP AUTO BUY", callback_data="nav_stopauto"))
        )
    except: status_msg = None
    
    attempts = 0; order_counter = 0; orders_list = []
    start_time = time.time(); last_ui_update = time.time()
    
    while autobuy_active.get(chat_id) == country_key:
        attempts += 1; now = time.time()
        
        if status_msg and (now - last_ui_update > 10):
            el_m = int((now - start_time) // 60); el_s = int((now - start_time) % 60)
            try:
                bot.edit_message_text(
                    f"🚀 *SUPER BRUTAL AUTO BUY {country['name'].upper()}*\n\n"
                    f"🔄 *Percobaan API:* `{attempts}`x\n"
                    f"🎯 *Total didapat:* `{len(orders_list)}` nomor\n"
                    f"⏱ *Waktu berjalan:* {el_m}m {el_s}s",
                    chat_id, status_msg.message_id, parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup().row(InlineKeyboardButton("🛑 STOP AUTO BUY", callback_data="nav_stopauto"))
                )
                last_ui_update = now
            except: pass

        res = req_api(api_key, 'getNumber', service=SERVICE, country=country['country_id'])
        if 'ACCESS_NUMBER' in res:
            parts = res.split(':'); order_counter += 1
            # price
            pr = None
            try:
                res_p = req_api(api_key, 'getPrices', service=SERVICE, country=str(country['country_id']))
                if res_p.startswith("{"):
                    d = json.loads(res_p); cid_str = str(country['country_id'])
                    inn = d.get(cid_str, {}).get(SERVICE) or d.get(SERVICE, {}).get(cid_str)
                    if inn: pr = inn.get('cost') or min([float(k) for k in inn.keys() if k.replace('.','').isdigit()])
            except: pass
            
            order = {'id': parts[1], 'number': parts[2], 'status': 'waiting', 'order_time': time.time(), 'price': pr}
            orders_list.append(order)
            text = format_order_message([order], "", country_key, start_index=order_counter, show_progress=False)
            markup = InlineKeyboardMarkup().row(InlineKeyboardButton("⏳ Wait...", callback_data="cancel_wait"))
            try:
                msg = bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)
                if chat_id not in active_orders: active_orders[chat_id] = {}
                active_orders[chat_id][msg.message_id] = [order]
                threading.Thread(target=auto_check_otp, args=(chat_id, msg.message_id, [order], api_key, country_key, True, order_counter)).start()
            except: pass
            time.sleep(0.5) 
        elif res == 'NO_BALANCE': break
        elif res == 'NO_NUMBERS': pass 
        else: time.sleep(0.1)

    if status_msg:
        try: bot.edit_message_text(f"🛑 *AUTO BUY DIHENTIKAN*\nSelesai dengan {len(orders_list)} nomor.", chat_id, status_msg.message_id, parse_mode="Markdown")
        except: pass

# =============================================
# HANDLERS
# =============================================
@bot.message_handler(commands=['start'])
def start_cmd(message):
    uid = message.from_user.id
    if not is_whitelisted(uid):
        bot.send_message(message.chat.id, "🔒 *Akses Ditolak*\n\nBot ini diproteksi. Hanya ID yang terdaftar yang bisa mengaksesnya.\nf\"ID Telegram Anda: `{message.from_user.id}`\nKirimkan angka ID di atas ke Admin @hesssxb.", parse_mode="Markdown"); return
    update_user_info(message.from_user); api_key = get_user_api(uid)
    text = (
        "🐻 *Bot OTP WhatsApp (Hero-SMS)* \n\n"
        "Bot ini untuk order nomor WhatsApp dengan OTP otomatis.\n"
        "Pilih negara, lalu pilih jumlah nomor yang ingin di-order.\n\n"
        "🌍 *Negara tersedia:*\n"
        "🇻🇳 Vietnam (Country ID: 10)\n"
        "🇵🇭 Philipina (Country ID: 3)\n"
        "🇨🇴 Colombia (Country ID: 33)\n\n"
        "📋 *Perintah:*\n"
        "`/setapi API_KEY` — Daftarkan API Key\n"
        "`/order N` — Order N nomor\n"
        "`/balance` — Cek saldo\n"
        "`/autobuy` — Auto buy brutal\n"
        "`/stopauto` — Hentikan auto buy\n"
        "`/help` — Bantuan\n\n"
    )
    if api_key:
        bal = req_api(api_key, 'getBalance')
        if 'ACCESS_BALANCE' in bal: text += f"✅ API Key: Terdaftar\n💰 Saldo: *{bal.split(':')[1]} USD*"
        else: text += "⚠️ API Key Invalid."
    else: text += "❌ Belum ada API Key."

    markup = InlineKeyboardMarkup()
    if api_key:
        markup.row(InlineKeyboardButton("🇻🇳 Vietnam", callback_data="country_vietnam"), InlineKeyboardButton("🇵🇭 Philipina", callback_data="country_philipina"), InlineKeyboardButton("🇨🇴 Colombia", callback_data="country_colombia"))
        markup.row(InlineKeyboardButton("🛒 Order Baru", callback_data="nav_order"), InlineKeyboardButton("💰 Saldo", callback_data="nav_balance"))
        markup.row(InlineKeyboardButton("🚀 AUTO BUY SUPER BRUTAL", callback_data="nav_autobuy"), InlineKeyboardButton("🛑 Stop Auto", callback_data="nav_stopauto"))
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(commands=['stopauto'])
def stopauto_command(message):
    autobuy_active[message.chat.id] = False; bot.reply_to(message, "🛑 Stop.")

@bot.callback_query_handler(func=lambda call: True)
def callback_q(call):
    uid, cid, mid = call.from_user.id, call.message.chat.id, call.message.message_id
    if not is_whitelisted(uid): return
    api = get_user_api(uid); data = call.data
    
    if data.startswith("country_"):
        ck = data.split("_")[1]; m = InlineKeyboardMarkup().row(*[InlineKeyboardButton(str(i), callback_data=f"quick_{ck}_{i}") for i in range(1,6)])
        # NO OVERLAY: Kirim pesan baru
        bot.send_message(cid, f"🌍 *Negara: {get_country_label(ck)}*\nPilih jumlah:", parse_mode="Markdown", reply_markup=m)
    
    elif data.startswith("quick_"):
        bot.answer_callback_query(call.id); p = data.split("_"); process_bulk_order(cid, api, int(p[2]), p[1])
    
    elif data == "nav_balance":
        res = req_api(api, 'getBalance')
        if 'ACCESS_BALANCE' in res: bot.answer_callback_query(call.id, f"💰 Saldo: {res.split(':')[1]} USD", show_alert=True)
    
    elif data == "nav_autobuy":
        m = InlineKeyboardMarkup().row(InlineKeyboardButton("🇻🇳 VN", callback_data="auto_vietnam"), InlineKeyboardButton("🇵🇭 PH", callback_data="auto_philipina"), InlineKeyboardButton("🇨🇴 CO", callback_data="auto_colombia"))
        # NO OVERLAY: Kirim pesan baru
        bot.send_message(cid, "🚀 *Pilih negara untuk AUTO BUY SUPER BRUTAL:*", parse_mode="Markdown", reply_markup=m)
    
    elif data.startswith("auto_"):
        ck = data.split("_")[1]; autobuy_active[cid] = ck
        threading.Thread(target=autobuy_worker, args=(cid, api, ck)).start()
        bot.answer_callback_query(call.id, f"⚡ {ck.upper()} Super Brutal Start!")
    
    elif data == "nav_stopauto":
        autobuy_active[cid] = False; bot.answer_callback_query(call.id, "🛑 Auto Buy Berhenti."); bot.send_message(cid, "🛑 *Auto Buy Dihentikan.*", parse_mode="Markdown")
    
    elif data.startswith("cancelall_"):
        ids = data.split("_")[1].split(","); [req_api(api,'setStatus',status='8',id=i) for i in ids]
        bot.answer_callback_query(call.id, "✅")
    
    elif data == "cancel_wait":
        bot.answer_callback_query(call.id, "⏳ Tunggu 2 menit.", show_alert=True)

def process_bulk_order(cid, api, count, country_key):
    cntry = COUNTRIES[country_key]; msg = bot.send_message(cid, f"⏳ Memesan {count} nomor WA {get_country_label(country_key)}...", parse_mode="Markdown")
    orders = []
    try:
        res_p = req_api(api, 'getPrices', service=SERVICE, country=cntry['country_id'])
        if res_p.startswith("{"):
            d = json.loads(res_p); cid_str = str(cntry['country_id']); inn = d.get(cid_str, {}).get(SERVICE) or d.get(SERVICE, {}).get(cid_str)
            pr = inn.get('cost') or min([float(k) for k in inn.keys() if k.replace('.','').isdigit()]) if inn else None
    except: pr = None
    for _ in range(count):
        res = req_api(api, 'getNumber', service=SERVICE, country=cntry['country_id'])
        if 'ACCESS_NUMBER' in res:
            p = res.split(':'); orders.append({'id':p[1], 'number':p[2], 'status':'waiting', 'order_time':time.time(), 'price':pr})
        time.sleep(0.5)
    if orders:
        bot.edit_message_text(format_order_message(orders, f"🛒 *Order WA {get_country_label(country_key)}*", country_key), cid, msg.message_id, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().row(InlineKeyboardButton("⏳ Cancel tersedia ~2 menit lagi", callback_data="cancel_wait")))
        if cid not in active_orders: active_orders[cid] = {}
        active_orders[cid][msg.message_id] = orders
        threading.Thread(target=auto_check_otp, args=(cid, msg.message_id, orders, api, country_key)).start()
    else: bot.edit_message_text("❌ Gagal.", cid, msg.message_id)

@bot.message_handler(commands=['adduser'])
def adduser_cmd(message):
    if message.from_user.id != ADMIN_ID: return
    p = message.text.split(); 
    if len(p) > 1: add_to_whitelist(int(p[1]), message.from_user.id); bot.reply_to(message, "✅ OK.")

if __name__ == '__main__':
    init_db(); bot.infinity_polling()
