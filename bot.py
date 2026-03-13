import telebot
# SUPER BRUTAL V3 - Brazil + All Countries Brutal
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
TOKEN = os.environ.get("BOT_TOKEN", "8605750682:AAG3Jl-y-LiRbuQbEfWdvsahKq2zULo-bpg")
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
API_BASE = "https://hero-sms.com/stubs/handler_api.php"

# =============================================
# KONFIGURASI NEGARA
# =============================================
COUNTRIES = {
    "vietnam": {"name": "Vietnam", "flag": "🇻🇳", "country_id": "10", "country_code": "84", "maxPrice": "0.25", "minPrice": 0.15},
    "philipina": {"name": "Philipina", "flag": "🇵🇭", "country_id": "3", "country_code": "63", "maxPrice": "0.25", "minPrice": 0.15},
    "colombia": {"name": "Colombia", "flag": "🇨🇴", "country_id": "33", "country_code": "57", "maxPrice": "0.25", "minPrice": 0.15},
    "mexico": {"name": "Mexico", "flag": "🇲🇽", "country_id": "54", "country_code": "52"},
    "brazil": {"name": "Brazil", "flag": "🇧🇷", "country_id": "73", "country_code": "55", "maxPrice": "1.50"},
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

def fetch_price_by_activation(api_key, activation_id):
    """Ambil harga ASLI dari activation yang sudah dibeli"""
    try:
        res = req_api(api_key, 'getActiveActivations')
        if res.startswith("{"):
            d = json.loads(res)
            # Format: {"status":"success","activeActivations":{"ID":{"activationCost":"1.30",...},...}}
            activations = d.get('activeActivations', d)
            if isinstance(activations, dict):
                act = activations.get(str(activation_id))
                if act and isinstance(act, dict):
                    cost = act.get('activationCost') or act.get('cost') or act.get('sum')
                    if cost:
                        return float(cost)
                # Coba cari di semua activations
                for aid, info in activations.items():
                    if str(aid) == str(activation_id) and isinstance(info, dict):
                        cost = info.get('activationCost') or info.get('cost') or info.get('sum')
                        if cost:
                            return float(cost)
    except:
        pass
    return None

def fetch_price(api_key, country_key):
    """Fallback: ambil harga dari getPrices (hanya dipakai kalau activation lookup gagal)"""
    try:
        cid = COUNTRIES[country_key]['country_id']
        res_p = req_api(api_key, 'getPrices', service=SERVICE, country=cid)
        if res_p.startswith("{"):
            d = json.loads(res_p)
            inn = d.get(cid, {}).get(SERVICE) or d.get(SERVICE, {}).get(cid)
            if inn:
                if 'cost' in inn:
                    return float(inn['cost'])
                numeric_keys = [float(k) for k in inn.keys() if k.replace('.','').isdigit()]
                if numeric_keys:
                    return max(numeric_keys)
    except:
        pass
    return None

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
        price_str = f" | 💰 {order['price']} USD" if order.get('price') else ""
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
        # FORCE UPDATE UI AGAR TIMER JELAS JALAN
        if changed_status or (now - last_ui_update >= 4):
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
# AUTO BUY (SUPER BRUTAL V3)
# =============================================
def autobuy_worker(chat_id, api_key, country_key):
    cntry = COUNTRIES[country_key]
    try:
        st_msg = bot.send_message(chat_id, f"🚀 *SUPER BRUTAL AUTO BUY {country_key.upper()}*\n\nMode: ⚡ ULTRA BRUTAL (MaxPrice: {cntry.get('maxPrice','N/A')})\n🔄 Percobaan: 0", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().row(InlineKeyboardButton("🛑 STOP", callback_data="nav_stopauto")))
    except: st_msg = None
    att, count, orders_list = 0, 0, []
    st_time, last_ui = time.time(), time.time()
    no_number_streak = 0
    err_streak = 0
    last_ui_status = "🟢 Hunting..."
    
    while autobuy_active.get(chat_id) == country_key:
        try:
            att += 1; now = time.time()
            if st_msg and (now - last_ui > 4):
                el = int(now - st_time)
                speed = att / max(el, 1)
                try: 
                    bot.edit_message_text(f"🚀 *SUPER BRUTAL AUTO BUY {country_key.upper()}*\n\n⚡ Mode: ULTRA BRUTAL\n💰 MaxPrice: `{cntry.get('maxPrice','N/A')}` USD\n🔄 Percobaan: `{att}`x ({speed:.1f}/detik)\n🎯 Dapat: `{len(orders_list)}` nomor\n⏱ Waktu: {el//60}m {el%60}s\n📡 Status: {last_ui_status}", chat_id, st_msg.message_id, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().row(InlineKeyboardButton("🛑 STOP", callback_data="nav_stopauto")))
                    last_ui = now
                except Exception as e:
                    if "Too Many Requests" in str(e): time.sleep(1.5)
                    pass
                    
            kwargs = {'service': SERVICE, 'country': cntry['country_id']}
            if 'maxPrice' in cntry: kwargs['maxPrice'] = cntry['maxPrice']
            res = req_api(api_key, 'getNumber', **kwargs)
            
            if 'ACCESS_NUMBER' in res:
                no_number_streak = 0
                err_streak = 0
                last_ui_status = "🟢 Dapat Nomor! Hunting lagi..."
                parts = res.split(':')
                if len(parts) >= 3:
                    act_id = parts[1]; number = parts[2]
                else:
                    time.sleep(0.1)
                    continue
                pr = fetch_price_by_activation(api_key, act_id) or fetch_price(api_key, country_key)
                min_pr = cntry.get('minPrice')
                if min_pr and pr and pr < min_pr:
                    last_ui_status = f"🟡 Skip nomor murahan (${pr})"
                    req_api(api_key, 'setStatus', status='8', id=act_id)
                    time.sleep(0.05)
                    continue

                count += 1
                o = {'id': act_id, 'number': number, 'status': 'waiting', 'order_time': time.time(), 'price': pr}
                orders_list.append(o)
                success_send = False
                for _ in range(5):
                    try:
                        m = bot.send_message(chat_id, format_order_message([o], "", country_key, count, False), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().row(InlineKeyboardButton("⏳ Wait...", callback_data="cancel_wait")))
                        success_send = True
                        break
                    except Exception as e:
                        if "Too Many Requests" in str(e): time.sleep(1.5)
                        else: time.sleep(1)
                
                if success_send:
                    threading.Thread(target=auto_check_otp, args=(chat_id, m.message_id, [o], api_key, country_key, True, count), daemon=True).start()
                else:
                    req_api(api_key, 'setStatus', status='8', id=act_id)
                    orders_list.remove(o)
                    
                time.sleep(0.5)
                
            elif res == 'NO_BALANCE':
                try: bot.send_message(chat_id, "💸 *SALDO HABIS!* Auto buy dihentikan.", parse_mode="Markdown")
                except: pass
                break
                
            elif res == 'NO_NUMBERS':
                no_number_streak += 1
                err_streak = 0
                if no_number_streak > 50:
                    last_ui_status = f"🟡 Menunggu stok... ({no_number_streak}x kosong)"
                    time.sleep(0.5)
                else:
                    last_ui_status = "🟢 Hunting..."
                    time.sleep(0.05)
                    
            else:
                err_streak += 1
                if 'ERROR' in res or 'ERR_HTTP' in res or not res:
                    last_ui_status = "🔴 API Error Koneksi, Jeda sejenak..."
                    time.sleep(1.0)
                else:
                    clean_res = res[:25].replace('\n', ' ')
                    last_ui_status = f"🔴 Aneh: {clean_res}"
                    time.sleep(1.0)
                
                if "BANNED" in res and err_streak > 3:
                    try: bot.send_message(chat_id, f"❌ *IP BANNED by HeroSMS!* Mode Brutal dihentikan sementera.", parse_mode="Markdown")
                    except: pass
                    break

        except Exception as ex:
            print(f"[AUTOBUY INNER ERROR] {ex}")
            time.sleep(1.0)

    autobuy_active[chat_id] = False
    if st_msg:
        el = int(time.time() - st_time)
        try: bot.edit_message_text(f"🛑 *AUTO BUY SELESAI*\n\n🎯 Total dapat: `{len(orders_list)}` nomor\n🔄 Total percobaan: `{att}`x\n⏱ Durasi: {el//60}m {el%60}s", chat_id, st_msg.message_id, parse_mode="Markdown")
        except: pass

# =============================================
# WHITELIST MANAGEMENT
# =============================================
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
# ADMIN COMMANDS
# =============================================
@bot.message_handler(commands=['adduser'])
def adduser_cmd(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Hanya admin.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Format: `/adduser USER_ID`", parse_mode="Markdown")
        return
    try:
        target_id = int(parts[1].strip())
    except ValueError:
        bot.reply_to(message, "❌ User ID harus angka.")
        return
    add_to_whitelist(target_id, message.from_user.id)
    bot.reply_to(message, f"✅ User `{target_id}` ditambahkan ke whitelist.", parse_mode="Markdown")

@bot.message_handler(commands=['removeuser'])
def removeuser_cmd(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Hanya admin.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Format: `/removeuser USER_ID`", parse_mode="Markdown")
        return
    try:
        target_id = int(parts[1].strip())
    except ValueError:
        bot.reply_to(message, "❌ User ID harus angka.")
        return
    if target_id == ADMIN_ID:
        bot.reply_to(message, "⚠️ Tidak bisa hapus admin.")
        return
    remove_from_whitelist(target_id)
    bot.reply_to(message, f"✅ User `{target_id}` dihapus dari whitelist.", parse_mode="Markdown")

@bot.message_handler(commands=['listusers'])
def listusers_cmd(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Hanya admin.")
        return
    users = get_all_whitelisted()
    if not users:
        bot.reply_to(message, "📋 Whitelist kosong.")
        return
    lines = ["📋 *Daftar Whitelist:*\n"]
    for uid, added_at in users:
        role = "👑 ADMIN" if uid == ADMIN_ID else "👤 User"
        lines.append(f"{role}: `{uid}` | Ditambahkan: {added_at}")
    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")

# =============================================
# HANDLERS
# =============================================
@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    if not is_whitelisted(uid): bot.send_message(message.chat.id, f"🔒 *Akses Ditolak*\nID Anda: `{uid}`\nHubungi admin untuk akses.", parse_mode="Markdown"); return
    key = get_user_api(uid)
    text = "🐻 *Bot OTP WhatsApp (Hero-SMS)* \n\nPilih negara di bawah:\n\n"
    if key:
        bal = req_api(key, 'getBalance')
        if 'ACCESS_BALANCE' in bal: text += f"✅ API OK | 💰 Saldo: *{bal.split(':')[1]} USD*"
    else: text += "❌ Belum ada API."
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🇻🇳 Vietnam", callback_data="country_vietnam"), InlineKeyboardButton("🇵🇭 Philipina", callback_data="country_philipina"), InlineKeyboardButton("🇨🇴 Colombia", callback_data="country_colombia"))
    markup.row(InlineKeyboardButton("🇲🇽 Mexico", callback_data="country_mexico"), InlineKeyboardButton("🇧🇷 Brazil", callback_data="country_brazil"))
    markup.row(InlineKeyboardButton("🛒 Order", callback_data="nav_order"), InlineKeyboardButton("💰 Saldo", callback_data="nav_balance"))
    markup.row(InlineKeyboardButton("🚀 AUTO BUY BRUTAL", callback_data="nav_autobuy"), InlineKeyboardButton("🛑 Stop", callback_data="nav_stopauto"))
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
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🇻🇳 VN", callback_data="auto_vietnam"), InlineKeyboardButton("🇵🇭 PH", callback_data="auto_philipina"), InlineKeyboardButton("🇨🇴 CO", callback_data="auto_colombia"))
        m.row(InlineKeyboardButton("🇲🇽 MX", callback_data="auto_mexico"), InlineKeyboardButton("🇧🇷 BR", callback_data="auto_brazil"))
        bot.send_message(cid, "🚀 *Pilih negara Auto Buy:*", parse_mode="Markdown", reply_markup=m)
    elif data.startswith("auto_"):
        ck = data.split("_")[1]
        if autobuy_active.get(cid):
            bot.answer_callback_query(call.id, "⚠️ Auto buy sudah berjalan! Stop dulu.", show_alert=True)
            return
        if not key:
            bot.answer_callback_query(call.id, "❌ Set API key dulu! /setapi", show_alert=True)
            return
        autobuy_active[cid] = ck
        threading.Thread(target=autobuy_worker, args=(cid, key, ck), daemon=True).start()
    elif data == "nav_stopauto":
        autobuy_active[cid] = False; bot.answer_callback_query(call.id, "🛑 Stop.")
    elif data.startswith("cancelall_"):
        ids = data.split("_")[1].split(","); [req_api(key, 'setStatus', status='8', id=i) for i in ids]
        bot.answer_callback_query(call.id, "✅")

def process_bulk(cid, api, count, country_key):
    cntry = COUNTRIES[country_key]; msg = bot.send_message(cid, f"⏳ Pesan {count} nomor {cntry['name']}...")
    orders = []
    min_pr = cntry.get('minPrice')
    max_retries = count * 5  # Lebih banyak retry untuk brutal buy
    attempts = 0
    while len(orders) < count and attempts < max_retries:
        attempts += 1
        kwargs = {'service': SERVICE, 'country': cntry['country_id']}
        if 'maxPrice' in cntry:
            kwargs['maxPrice'] = cntry['maxPrice']
        res = req_api(api, 'getNumber', **kwargs)
        if 'ACCESS_NUMBER' in res:
            p = res.split(':'); act_id = p[1]; number = p[2]
            pr = fetch_price_by_activation(api, act_id) or fetch_price(api, country_key)
            # Filter harga minimum
            if min_pr and pr and pr < min_pr:
                req_api(api, 'setStatus', status='8', id=act_id)
                time.sleep(0.2)
                continue
            orders.append({'id': act_id, 'number': number, 'status':'waiting', 'order_time':time.time(), 'price': pr})
        elif res == 'NO_BALANCE':
            break
        elif res == 'NO_NUMBERS':
            if not orders and attempts >= 5:
                break
            time.sleep(0.1)  # Cepat retry saat no numbers
            continue
        time.sleep(0.3)
    if orders:
        bot.edit_message_text(format_order_message(orders, f"🛒 *Order {cntry['name']}*", country_key), cid, msg.message_id, parse_mode="Markdown")
        threading.Thread(target=auto_check_otp, args=(cid, msg.message_id, orders, api, country_key), daemon=True).start()
    else: bot.edit_message_text(f"❌ Gagal order {cntry['name']}. Coba lagi.", cid, msg.message_id)

@bot.message_handler(commands=['setapi'])
def setapi(message):
    p = message.text.split()
    if len(p) > 1: set_user_api(message.from_user.id, p[1]); bot.reply_to(message, "✅ OK.")

if __name__ == '__main__':
    init_db(); bot.infinity_polling()
