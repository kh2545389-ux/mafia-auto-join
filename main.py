import asyncio
import os
import logging
import threading
import time
import random
from datetime import datetime
from flask import Flask, jsonify, render_template_string
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession  # ← IMPORT THIS!

# ─── LOGGING SETUP ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ─── FLASK APP ──────────────────────────────────────────────────
app = Flask(__name__)

# ─── GLOBAL VARIABLES ──────────────────────────────────────────
clients = []
loop = asyncio.new_event_loop()
bot_start_time = time.time()
activity_log = []
MAX_LOGS = 100
total_joins = 0
total_next_sent = 0
last_next_sent = None

# ─── LOAD ACCOUNTS FROM ENVIRONMENT ───────────────────────────
def load_accounts():
    accounts = []
    i = 1
    while True:
        api_id = os.getenv(f'ACCOUNT_{i}_API_ID')
        if not api_id:
            break
        api_hash = os.getenv(f'ACCOUNT_{i}_API_HASH')
        session_name = os.getenv(f'ACCOUNT_{i}_SESSION', f'session_{i}')
        accounts.append({
            "api_id": int(api_id),
            "api_hash": api_hash,
            "session_name": session_name
        })
        i += 1
    return accounts

# ─── SETTINGS ──────────────────────────────────────────────────
notification_bot_id = int(os.getenv('NOTIFICATION_BOT_ID', '468253535'))
mafia_chat = '@truemafiaen'
SEND_NEXT_INTERVAL = int(os.getenv('SEND_NEXT_INTERVAL', '60'))

# ─── LOGGING HELPER ────────────────────────────────────────────
def add_log(message, level="INFO"):
    global activity_log
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {level}: {message}"
    activity_log.append(log_entry)
    if len(activity_log) > MAX_LOGS:
        activity_log.pop(0)
    logger.info(message)

# ─── BUTTON CLICKER ────────────────────────────────────────────
async def click_join_buttons(event):
    global total_joins
    
    try:
        msg = event.message
        if not msg or not msg.buttons:
            return

        clicked = False
        for row in msg.buttons:
            for button in row:
                text = getattr(button, 'text', '').lower()
                join_keywords = ["join", "gabung", "sertai", "masuk", "ikuti", "click", "klik"]
                if any(word in text for word in join_keywords):
                    try:
                        await button.click()
                        total_joins += 1
                        add_log(f"✅ Clicked button: '{text}' (Total joins: {total_joins})", "SUCCESS")
                        clicked = True
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        add_log(f"❌ Failed to click '{text}': {e}", "ERROR")
        
        if not clicked and msg.buttons:
            button_texts = []
            for row in msg.buttons:
                for button in row:
                    button_texts.append(getattr(button, 'text', 'Unknown'))
            if button_texts:
                add_log(f"ℹ️ Available buttons: {', '.join(button_texts[:5])}", "INFO")
                
    except Exception as e:
        add_log(f"❌ Button click error: {e}", "ERROR")

# ─── MESSAGE HANDLER ───────────────────────────────────────────
def register_handlers(client):
    @client.on(events.NewMessage)
    async def handler(event):
        if getattr(event, 'sender_id', None) == notification_bot_id:
            add_log(f"📨 Received message from notification bot", "INFO")
            await click_join_buttons(event)
        
        if getattr(event, 'chat_id', None) == mafia_chat:
            message_text = event.message.text if event.message else ""
            if message_text and any(word in message_text.lower() for word in ["join", "gabung", "sertai"]):
                add_log(f"📨 Join message detected in mafia chat", "INFO")
                await click_join_buttons(event)

# ─── SEND /NEXT EVERY 60 SECONDS ──────────────────────────────
async def send_next_periodically():
    global total_next_sent, last_next_sent
    
    while True:
        try:
            for idx, c in enumerate(clients):
                try:
                    await c.send_message(mafia_chat, "/next")
                    total_next_sent += 1
                    last_next_sent = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    add_log(f"📤 [{idx+1}/{len(clients)}] Sent /next from {c.session.filename} (#{total_next_sent})", "SUCCESS")
                    await asyncio.sleep(random.uniform(1, 3))
                    
                except errors.FloodWaitError as e:
                    wait_time = e.seconds
                    add_log(f"⏳ Flood wait for {wait_time}s on {c.session.filename}", "WARNING")
                    await asyncio.sleep(wait_time)
                except Exception as e:
                    add_log(f"❌ Error sending /next from {c.session.filename}: {e}", "ERROR")
            
            add_log(f"💤 Sleeping for {SEND_NEXT_INTERVAL} seconds until next /next", "INFO")
            await asyncio.sleep(SEND_NEXT_INTERVAL)
            
        except Exception as e:
            add_log(f"❌ Error in send_next_periodically: {e}", "ERROR")
            await asyncio.sleep(10)

# ─── AUTHORIZATION WITH SESSION STRING ──────────────────────
async def authorize_client(acc):
    # Check if session string is provided
    session_string = os.getenv(f'SESSION_STRING_{acc["session_name"]}')
    
    if session_string:
        add_log(f"🔑 Using session string for {acc['session_name']}", "INFO")
        try:
            # Create client from session string
            client = TelegramClient(
                StringSession(session_string),  # ← MAGIC!
                acc['api_id'], 
                acc['api_hash']
            )
            await client.connect()
            
            # Verify session is valid
            if await client.is_user_authorized():
                me = await client.get_me()
                add_log(f"✅ [{acc['session_name']}] Connected via session string!", "SUCCESS")
                add_log(f"✅ Logged in as: {me.first_name} (@{me.username if me.username else 'no username'})", "SUCCESS")
                return client
            else:
                add_log(f"⚠️ Session string invalid for {acc['session_name']}, falling back to file...", "WARNING")
        except Exception as e:
            add_log(f"❌ Session string failed: {e}, falling back to file...", "WARNING")
    
    # Fallback: Use session file (for first time setup)
    sessions_dir = '/opt/render/project/src/sessions'
    os.makedirs(sessions_dir, exist_ok=True)
    
    session_path = f'{sessions_dir}/{acc["session_name"]}'
    client = TelegramClient(session_path, acc['api_id'], acc['api_hash'])
    
    try:
        await client.connect()
        
        if not await client.is_user_authorized():
            add_log(f"🔐 Authorizing {acc['session_name']}...", "INFO")
            
            phone = os.getenv(f'PHONE_{acc["session_name"]}')
            if not phone:
                raise ValueError(f"PHONE_{acc['session_name']} not set in environment")
            
            await client.send_code_request(phone)
            add_log(f"📱 Code sent to {phone}", "INFO")
            
            code = os.getenv(f'CODE_{acc["session_name"]}')
            if not code:
                raise ValueError(f"CODE_{acc['session_name']} not set in environment")
            
            try:
                await client.sign_in(phone=phone, code=code)
                add_log(f"✅ Successfully signed in {acc['session_name']}", "SUCCESS")
            except errors.SessionPasswordNeededError:
                pw = os.getenv(f'PASSWORD_{acc["session_name"]}')
                if not pw:
                    raise ValueError(f"PASSWORD_{acc['session_name']} not set in environment")
                await client.sign_in(password=pw)
                add_log(f"✅ Successfully signed in with 2FA for {acc['session_name']}", "SUCCESS")
        else:
            add_log(f"✅ [{acc['session_name']}] Connected via session file!", "SUCCESS")
        
        me = await client.get_me()
        add_log(f"✅ Logged in as: {me.first_name} (@{me.username if me.username else 'no username'})", "SUCCESS")
        return client
        
    except Exception as e:
        add_log(f"❌ Authorization failed for {acc['session_name']}: {e}", "ERROR")
        raise

# ─── RUN BOT ────────────────────────────────────────────────────
async def run_bot_async():
    global clients
    
    accounts = load_accounts()
    if not accounts:
        add_log("❌ No accounts configured! Set ACCOUNT_X_API_ID/HASH environment variables.", "ERROR")
        return False

    add_log(f"📋 Found {len(accounts)} account(s) to connect", "INFO")
    
    for acc in accounts:
        try:
            client = await authorize_client(acc)
            clients.append(client)
            add_log(f"✅ Connected: {acc['session_name']}", "SUCCESS")
            await asyncio.sleep(2)
        except Exception as e:
            add_log(f"❌ Failed to connect {acc['session_name']}: {e}", "ERROR")

    if not clients:
        add_log("❌ No clients connected! Exiting.", "ERROR")
        return False

    for c in clients:
        register_handlers(c)
        add_log(f"📋 Handlers registered for {c.session.filename}", "INFO")

    tasks = [asyncio.create_task(c.run_until_disconnected()) for c in clients]
    tasks.append(asyncio.create_task(send_next_periodically()))
    
    add_log(f"🚀 Bot is now running! Sending /next every {SEND_NEXT_INTERVAL} seconds!", "SUCCESS")
    add_log(f"🎯 Monitoring for join buttons from bot {notification_bot_id}", "INFO")
    
    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        add_log(f"❌ Bot error: {e}", "ERROR")
        return False
    
    return True

def run_bot():
    global loop
    try:
        loop.run_until_complete(run_bot_async())
    except Exception as e:
        add_log(f"❌ Bot thread error: {e}", "ERROR")

# ─── HTML DASHBOARD ─────────────────────────────────────────────
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Telegram Auto-Join Bot</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            color: white;
        }
        .container {
            max-width: 1000px;
            margin: 0 auto;
            background: rgba(255,255,255,0.1);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 30px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        }
        h1 { text-align: center; margin-bottom: 30px; font-size: 2.5em; }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }
        .card {
            background: rgba(255,255,255,0.15);
            padding: 15px;
            border-radius: 15px;
            text-align: center;
        }
        .card .label { font-size: 0.8em; opacity: 0.8; }
        .card .value { font-size: 1.8em; font-weight: bold; margin-top: 5px; }
        .green { color: #4ade80; }
        .blue { color: #60a5fa; }
        .purple { color: #c084fc; }
        .orange { color: #fb923c; }
        .logs {
            background: rgba(0,0,0,0.3);
            border-radius: 15px;
            padding: 20px;
            max-height: 400px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 0.8em;
        }
        .logs::-webkit-scrollbar { width: 8px; }
        .logs::-webkit-scrollbar-track { background: rgba(255,255,255,0.1); border-radius: 10px; }
        .logs::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.3); border-radius: 10px; }
        .log-entry { padding: 3px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
        .SUCCESS { color: #4ade80; }
        .ERROR { color: #f87171; }
        .WARNING { color: #fb923c; }
        .INFO { color: #60a5fa; }
        .endpoints {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 20px;
        }
        .endpoint {
            background: rgba(255,255,255,0.1);
            padding: 8px 15px;
            border-radius: 10px;
            font-size: 0.8em;
        }
        .method { color: #4ade80; font-weight: bold; }
        .path { color: #c084fc; }
        @media (max-width: 600px) {
            h1 { font-size: 1.8em; }
            .grid { grid-template-columns: repeat(2, 1fr); }
            .container { padding: 15px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Telegram Auto-Join Bot</h1>
        <div class="grid">
            <div class="card"><div class="label">Status</div><div class="value green">🟢 Active</div></div>
            <div class="card"><div class="label">Accounts</div><div class="value blue">{{ stats.accounts }}</div></div>
            <div class="card"><div class="label">Uptime</div><div class="value purple">{{ stats.uptime }}</div></div>
            <div class="card"><div class="label">/next Sent</div><div class="value orange">{{ stats.total_next_sent }}</div></div>
            <div class="card"><div class="label">Total Joins</div><div class="value green">{{ stats.total_joins }}</div></div>
            <div class="card"><div class="label">Interval</div><div class="value blue">{{ stats.interval }}s</div></div>
        </div>
        <div class="logs">
            <h3 style="margin-top:0;">📋 Activity Log</h3>
            {% for log in logs %}
            <div class="log-entry">
                <span class="INFO">{{ log }}</span>
            </div>
            {% endfor %}
        </div>
        <div class="endpoints">
            <div class="endpoint"><span class="method">GET</span> <span class="path">/</span> Dashboard</div>
            <div class="endpoint"><span class="method">GET</span> <span class="path">/status</span> Status</div>
            <div class="endpoint"><span class="method">GET</span> <span class="path">/health</span> Health</div>
            <div class="endpoint"><span class="method">GET</span> <span class="path">/stats</span> Stats</div>
            <div class="endpoint"><span class="method">GET</span> <span class="path">/send_next</span> Manual /next</div>
        </div>
    </div>
</body>
</html>
"""

# ─── FLASK ROUTES ──────────────────────────────────────────────

@app.route('/')
def index():
    uptime_seconds = int(time.time() - bot_start_time)
    hours = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60
    
    stats = {
        "accounts": len(clients),
        "uptime": f"{hours}h {minutes}m {seconds}s",
        "total_joins": total_joins,
        "total_next_sent": total_next_sent,
        "interval": SEND_NEXT_INTERVAL,
        "last_next": last_next_sent or "Never"
    }
    
    recent_logs = activity_log[-50:] if activity_log else ["No logs yet..."]
    return render_template_string(HTML_TEMPLATE, stats=stats, logs=recent_logs)

@app.route('/status')
def status():
    return jsonify({
        "status": "active" if clients else "inactive",
        "connected_accounts": len(clients),
        "accounts": [c.session.filename for c in clients],
        "interval_seconds": SEND_NEXT_INTERVAL,
        "total_next_sent": total_next_sent,
        "total_joins": total_joins,
        "uptime_seconds": int(time.time() - bot_start_time)
    })

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "accounts": len(clients)
    }), 200

@app.route('/stats')
def stats():
    return jsonify({
        "uptime_seconds": int(time.time() - bot_start_time),
        "accounts": len(clients),
        "accounts_list": [c.session.filename for c in clients],
        "chat_id": mafia_chat,
        "notification_bot": notification_bot_id,
        "interval_seconds": SEND_NEXT_INTERVAL,
        "total_next_sent": total_next_sent,
        "total_joins": total_joins,
        "last_next_sent": last_next_sent
    })

@app.route('/send_next')
def send_next():
    if not clients:
        return jsonify({"error": "No clients connected"}), 400
    
    async def send():
        results = []
        for c in clients:
            try:
                await c.send_message(mafia_chat, "/next")
                results.append({
                    "client": c.session.filename,
                    "status": "success"
                })
                add_log(f"📤 Manual /next sent from {c.session.filename}", "SUCCESS")
            except Exception as e:
                results.append({
                    "client": c.session.filename,
                    "status": "error",
                    "error": str(e)
                })
                add_log(f"❌ Manual /next failed from {c.session.filename}: {e}", "ERROR")
        return {"results": results}
    
    result = loop.run_until_complete(send())
    return jsonify(result)

# ─── MAIN ──────────────────────────────────────────────────────
if __name__ == "__main__":
    add_log("🚀 Starting Telegram Auto-Join Bot...", "INFO")
    add_log(f"⏱️  Will send /next every {SEND_NEXT_INTERVAL} seconds", "INFO")
    
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    time.sleep(5)
    
    port = int(os.getenv('PORT', 5000))
    add_log(f"🌐 Web server running on port {port}", "INFO")
    app.run(host='0.0.0.0', port=port, debug=False)
