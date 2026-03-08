import telebot
# Trigger redeploy - Persistence Test Final Check
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

# =============================================
# KONFIGURASI PERSISTENCE (RAILWAY VOLUME)
# =============================================
VOL_PATH = "/data"
DEFAULT_DB = "database.db"
# Jika folder /data (Volume Railway) ada, gunakan otomatis
if os.path.exists(VOL_PATH) and os.path.isdir(VOL_PATH):
    DEFAULT_DB = os.path.join(VOL_PATH, "database.db")

DB_PATH = os.environ.get("DB_PATH", DEFAULT_DB)
ADMIN_ID = 940475417
MAX_ORDER = 20         
OTP_TIMEOUT = 1200     # 20 Menit
CHECK_INTERVAL = 4     # Jeda antar cek
CANCEL_DELAY = 120     # 2 Menit
SERVICE = "wa"         

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
    conn.commit()
    conn.close()

def is_whitelisted(user_id):
    # Cek Environment Variable (Diberi prioritas agar tidak terhapus)
    env_wl = os.environ.get("WHITELIST_IDS", "")
    perm_wl = [int(x.strip()) for x in env_wl.split(",") if x.strip().replace('-', '').isdigit()]
    if user_id == ADMIN_ID or user_id in perm_wl:
        return True
        
    # Cek Database
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM whitelist WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res is not None

def set_user_api(user_id, api_key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, api_key) VALUES (?, ?)", (user_id, api_key))
    conn.commit()
    conn.close()

def get_user_api(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT api_key FROM users WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else None

# =============================================
# API HELPER (HERO-SMS)
# =============================================
def req_api(api_key, action, **kwargs):
    params = {'api_key': api_key, 'action': action}
    params.update(kwargs)
    try:
        r = requests.get(API_BASE, params=params, timeout=12)
        return r.text.strip()
    except Exception as e: return f"ERROR: {str(e)}"

def strip_country_code(number, country_code="84"):
    number = str(number).strip()
    if number.startswith("+"): number = number[1:]
    if number.startswith(str(country_code)): number = number[len(str(country_code)):]
    return number

# =============================================
# FORMAT PESAN (TIMER FIXED)
# =============================================
def format_order_message(orders, title="", country_key="vietnam", start_index=1, show_progress=True):
    country = COUNTRIES.get(country_key, COUNTRIES["vietnam"])
    lines = []
    if title: lines.append(title); lines.append("")
    done_count = 0
    now = time.time()
    for i, order in enumerate(orders, start_index):
        number_local = strip_country_code(order['number'], country['country_code'])
        status = order.get('status', 'waiting')
        price_str = f" [💰 {order['price']} USD]" if order.get('price') else ""
        if status == 'waiting':
            elapsed = now - order['order_time']
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
        lines.append(""); lines.append(f"📊 Progress: {done_count}/{len(orders)}")
        if done_count >= len(orders): lines.append("\n✅ *Semua order selesai!*")
    return "\n".join(lines)

def safe_edit_message(text, chat_id, message_id, markup=None):
    try:
        bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown", reply_markup=markup)
        return True
    except Exception as e:
        if "message is not modified" in str(e).lower(): return True
        return False

# =============================================
# MONITORING OTP THREAD (FIXED STUCK)
# =============================================
def auto_check_otp(chat_id, message_id, orders, api_key, country_key="vietnam", is_autobuy=False, s_idx=1):
    last_ui_update = 0
    while True:
        waiting = [o for o in orders if o['status'] == 'waiting']
        if not waiting:
            if is_autobuy and autobuy_active.get(chat_id) == country_key: time.sleep(CHECK_INTERVAL); continue
            else:
                title = "" if is_autobuy else f"🛒 *Order WA {COUNTRIES[country_key]['name']} — Selesai*"
                safe_edit_message(format_order_message(orders, title, country_key, s_idx, not is_autobuy), chat_id, message_id)
                break
        
        now = time.time()
        # Timer check & OTP check
        changed_status = False
        for o in orders:
            if o['status'] != 'waiting': continue
            if (now - o['order_time']) > OTP_TIMEOUT: 
                o['status'] = 'timeout'; changed_status = True; req_api(api_key, 'setStatus', status='8', id=o['id'])
                continue
            
            # API Request OTP
            res = req_api(api_key, 'getStatus', id=o['id'])
            if res.startswith('STATUS_OK'):
                o['status'] = 'got_otp'; o['code'] = res.split(':')[1] if ':' in res else '???'
                changed_status = True; req_api(api_key, 'setStatus', status='6', id=o['id'])
            elif res == 'STATUS_CANCEL': o['status'] = 'cancelled'; changed_status = True
            time.sleep(0.5) # Jeda antar request dalam loop

        now = time.time()
        # FORCE UPDATE UI TIAP 8 DETIK AGAR TIMER JELAS JALAN
        if changed_status or (now - last_ui_update >= 8):
            title = "" if is_autobuy else f"🛒 *Order WA {COUNTRIES[country_key]['name']}*"
            text = format_order_message(orders, title, country_key, s_idx, not is_autobuy)
            markup = InlineKeyboardMarkup()
            active_rem = [o for o in orders if o['status'] == 'waiting']
            if active_rem:
                oldest = min(o['order_time'] for o in active_rem)
                if (now - oldest) >= CANCEL_DELAY:
                    markup.row(InlineKeyboardButton("🚫 Batalkan Order", callback_data=f"cancelall_{','.join([o['id'] for o in active_rem])}"))
                else: markup.row(InlineKeyboardButton(f"⏳ Cancel tersedia ~{int((CANCEL_DELAY-(now-oldest))/60)+1}m", callback_data="cancel_wait"))
            
            if safe_edit_message(text, chat_id, message_id, markup): last_ui_update = now
        
        time.sleep(CHECK_INTERVAL)

# =============================================
# AUTO BUY (SUPER BRUTAL)
# =============================================
def autobuy_worker(chat_id, api_key, country_key):
    try:
        st_msg = bot.send_message(chat_id, f"🚀 *SUPER BRUTAL AUTO BUY {country_key.upper()}*\n\nMode: Super Brutal (No Sleep)\n🔄 Percobaan: 0", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().row(InlineKeyboardButton("🛑 STOP", callback_data="nav_stopauto")))
    except: st_msg = None
    att, count, orders_list = 0, 0, []
    st_time, last_ui = time.time(), time.time()
    while autobuy_active.get(chat_id) == country_key:
        att += 1; now = time.time()
        if st_msg and (now - last_ui > 7):
            el = int(now - st_time)
            try: bot.edit_message_text(f"🚀 *SUPER BRUTAL AUTO BUY {country_key.upper()}*\n\n🔄 Percobaan: `{att}`x\n🎯 Dapat: `{len(orders_list)}` nomor\n⏱ Waktu: {el//60}m {el%60}s", chat_id, st_msg.message_id, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().row(InlineKeyboardButton("🛑 STOP", callback_data="nav_stopauto"))); last_ui = now
            except: pass
        res = req_api(api_key, 'getNumber', service=SERVICE, country=COUNTRIES[country_key]['country_id'])
        if 'ACCESS_NUMBER' in res:
            p = res.split(':'); count += 1
            # price
            pr = None
            try:
                res_p = req_api(api_key, 'getPrices', service=SERVICE, country=COUNTRIES[country_key]['country_id'])
                if res_p.startswith("{"):
                    d = json.loads(res_p); cid = COUNTRIES[country_key]['country_id']
                    inn = d.get(cid, {}).get(SERVICE) or d.get(SERVICE, {}).get(cid)
                    if inn: pr = inn.get('cost') or min([float(k) for k in inn.keys() if k.replace('.','').isdigit()])
            except: pass
            o = {'id': p[1], 'number': p[2], 'status': 'waiting', 'order_time': time.time(), 'price': pr}
            orders_list.append(o)
            try:
                m = bot.send_message(chat_id, format_order_message([o], "", country_key, count, False), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().row(InlineKeyboardButton("⏳ Wait...", callback_data="cancel_wait")))
                threading.Thread(target=auto_check_otp, args=(chat_id, m.message_id, [o], api_key, country_key, True, count)).start()
            except: pass
            time.sleep(1)
        elif res == 'NO_BALANCE': break
        elif res == 'NO_NUMBERS': pass
        else: time.sleep(0.3)
    if st_msg: bot.edit_message_text(f"🛑 *AUTO BUY SELESAI*\nTotal: {len(orders_list)}", chat_id, st_msg.message_id)

# =============================================
# HANDLERS
# =============================================
@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    if not is_whitelisted(uid): bot.send_message(message.chat.id, "🔒 Whitelist Required."); return
    key = get_user_api(uid)
    text = "🐻 *Bot OTP WhatsApp (Hero-SMS)* \n\nPilih negara di bawah:\n\n"
    if key:
        bal = req_api(key, 'getBalance')
        if 'ACCESS_BALANCE' in bal: text += f"✅ API OK | 💰 Saldo: *{bal.split(':')[1]} USD*"
    else: text += "❌ Belum ada API."
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🇻🇳 Vietnam", callback_data="country_vietnam"), InlineKeyboardButton("🇵🇭 Philipina", callback_data="country_philipina"), InlineKeyboardButton("🇨🇴 Colombia", callback_data="country_colombia"))
    markup.row(InlineKeyboardButton("🛒 Order", callback_data="nav_order"), InlineKeyboardButton("💰 Saldo", callback_data="nav_balance"))
    markup.row(InlineKeyboardButton("🚀 AUTO BUY SUPER BRUTAL", callback_data="nav_autobuy"), InlineKeyboardButton("🛑 Stop", callback_data="nav_stopauto"))
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_q(call):
    uid, cid, mid = call.from_user.id, call.message.chat.id, call.message.message_id
    if not is_whitelisted(uid): return
    key = get_user_api(uid); data = call.data
    if data.startswith("country_"):
        ck = data.split("_")[1]; m = InlineKeyboardMarkup().row(*[InlineKeyboardButton(str(i), callback_data=f"quick_{ck}_{i}") for i in range(1,6)])
        bot.send_message(cid, f"🌍 *{ck.upper()}* | Pilih jumlah:", parse_mode="Markdown", reply_markup=m)
    elif data.startswith("quick_"):
        bot.answer_callback_query(call.id); p = data.split("_"); process_bulk(cid, key, int(p[2]), p[1])
    elif data == "nav_autobuy":
        m = InlineKeyboardMarkup().row(InlineKeyboardButton("🇻🇳 VN", callback_data="auto_vietnam"), InlineKeyboardButton("🇵🇭 PH", callback_data="auto_philipina"), InlineKeyboardButton("🇨🇴 CO", callback_data="auto_colombia"))
        bot.send_message(cid, "🚀 *Pilih negara Auto Buy:*", parse_mode="Markdown", reply_markup=m)
    elif data.startswith("auto_"):
        ck = data.split("_")[1]; autobuy_active[cid] = ck; threading.Thread(target=autobuy_worker, args=(cid, key, ck)).start()
    elif data == "nav_stopauto":
        autobuy_active[cid] = False; bot.answer_callback_query(call.id, "🛑 Stop.")
    elif data.startswith("cancelall_"):
        ids = data.split("_")[1].split(","); [req_api(key, 'setStatus', status='8', id=i) for i in ids]
        bot.answer_callback_query(call.id, "✅")

def process_bulk(cid, api, count, country_key):
    cntry = COUNTRIES[country_key]; msg = bot.send_message(cid, f"⏳ Pesan {count} nomor...")
    orders = []
    # (Simplified price fetch for speed)
    pr = None
    for _ in range(count):
        res = req_api(api, 'getNumber', service=SERVICE, country=cntry['country_id'])
        if 'ACCESS_NUMBER' in res:
            p = res.split(':'); orders.append({'id':p[1], 'number':p[2], 'status':'waiting', 'order_time':time.time(), 'price':pr})
        time.sleep(0.5)
    if orders:
        bot.edit_message_text(format_order_message(orders, f"🛒 *Order {cntry['name']}*", country_key), cid, msg.message_id, parse_mode="Markdown")
        threading.Thread(target=auto_check_otp, args=(cid, msg.message_id, orders, api, country_key)).start()
    else: bot.edit_message_text("❌ Gagal.", cid, msg.message_id)

@bot.message_handler(commands=['setapi'])
def setapi(message):
    p = message.text.split()
    if len(p) > 1: set_user_api(message.from_user.id, p[1]); bot.reply_to(message, "✅ OK.")

if __name__ == '__main__':
    init_db(); bot.infinity_polling()
