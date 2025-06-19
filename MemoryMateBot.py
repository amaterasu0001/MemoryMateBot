import sqlite3
import asyncio
from datetime import datetime
import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    ContextTypes, MessageHandler, filters
)
from apscheduler.schedulers.background import BackgroundScheduler
from stay_alive import keep_alive

# === KEEP ALIVE ===
keep_alive()

# === API KEYS ===
TELEGRAM_BOT_TOKEN = "7795080227:AAF_W8T0B28ti7hA0JJVX8hJP2efI-Sm6o0"
OPENROUTER_API_KEY = "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"

# === Active AI users ===
active_ai_users = {}

# === SQLite Setup ===
conn = sqlite3.connect("reminders.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    message TEXT,
    remind_time TEXT
)
''')
conn.commit()

# === Scheduler ===
scheduler = BackgroundScheduler()
scheduler.start()

loop = None
app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

async def check_missed_reminders():
    now = datetime.now()
    cursor.execute("SELECT id, user_id, message, remind_time FROM reminders")
    rows = cursor.fetchall()
    for reminder_id, user_id, message, remind_time_str in rows:
        try:
            remind_time = datetime.strptime(remind_time_str, "%Y-%m-%d %I:%M %p")
            if remind_time <= now:
                await app.bot.send_message(chat_id=user_id, text=f"🔔 [Missed] Reminder: {message}")
                cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
                conn.commit()
        except Exception as e:
            print(f"⛔ Error checking missed reminder: {e}")

async def reschedule_all_reminders():
    cursor.execute("SELECT id, user_id, message, remind_time FROM reminders")
    for reminder_id, user_id, message, remind_time_str in cursor.fetchall():
        try:
            remind_time = datetime.strptime(remind_time_str, "%Y-%m-%d %I:%M %p")
            if remind_time > datetime.now():
                schedule_reminder(reminder_id, user_id, message, remind_time)
        except Exception as e:
            print(f"Error rescheduling: {e}")

async def on_startup(app_obj):
    global loop
    loop = asyncio.get_running_loop()
    await check_missed_reminders()
    await reschedule_all_reminders()

def send_reminder_job(reminder_id, user_id, message):
    global loop
    if loop:
        asyncio.run_coroutine_threadsafe(
            app.bot.send_message(chat_id=user_id, text=f"🔔 Reminder: {message}"),
            loop
        )
        cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()

def schedule_reminder(reminder_id, user_id, message, remind_time: datetime):
    scheduler.add_job(
        send_reminder_job,
        'date',
        run_date=remind_time,
        args=[reminder_id, user_id, message],
        id=f"reminder_{reminder_id}"
    )

# === Commands ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to **MemoryMateBot** 🧠\n\n"
        "📝 /remember <msg> at YYYY-MM-DD HH:MM AM/PM\n"
        "📋 /list – show your reminders\n"
        "🗑️ /delete <id>\n"
        "🤖 /ask – start AI mode\n"
        "🛑 /stop – stop AI mode",
        parse_mode="Markdown"
    )

async def remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.message.chat_id
        input_text = " ".join(context.args)
        message_part, time_part = input_text.rsplit(" at ", 1)
        remind_time = datetime.strptime(time_part.strip(), "%Y-%m-%d %I:%M %p")
        cursor.execute(
            "SELECT * FROM reminders WHERE user_id = ? AND message = ? AND remind_time = ?",
            (user_id, message_part.strip(), remind_time.strftime("%Y-%m-%d %I:%M %p"))
        )
        if cursor.fetchone():
            await update.message.reply_text("⚠️ Reminder already exists.")
            return
        cursor.execute(
            "INSERT INTO reminders (user_id, message, remind_time) VALUES (?, ?, ?)",
            (user_id, message_part.strip(), remind_time.strftime("%Y-%m-%d %I:%M %p"))
        )
        reminder_id = cursor.lastrowid
        conn.commit()
        schedule_reminder(reminder_id, user_id, message_part.strip(), remind_time)
        await update.message.reply_text("✅ Reminder saved!")
    except Exception as e:
        print("Error in /remember:", e)
        await update.message.reply_text("❌ Usage: /remember Buy Milk at 2025-06-20 06:00 PM")

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    cursor.execute("SELECT id, message, remind_time FROM reminders WHERE user_id = ?", (user_id,))
    reminders = cursor.fetchall()
    if not reminders:
        await update.message.reply_text("📭 No reminders found.")
    else:
        msg = "\n".join([f"{rid}. {msg} at {time}" for rid, msg, time in reminders])
        await update.message.reply_text(f"📝 Your reminders:\n{msg}")

async def delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        reminder_id = int(context.args[0])
        cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()
        if cursor.rowcount == 0:
            await update.message.reply_text("⚠️ No reminder found with that ID.")
        else:
            await update.message.reply_text("🗑️ Reminder deleted.")
    except:
        await update.message.reply_text("❌ Usage: /delete <reminder_id>")

async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    active_ai_users[user_id] = [{"role": "system", "content": "You are a helpful assistant."}]
    await update.message.reply_text("🤖 AI chat mode ON. Send /stop to turn off.")

async def stop_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    if user_id in active_ai_users:
        del active_ai_users[user_id]
        await update.message.reply_text("🛑 AI mode OFF.")
    else:
        await update.message.reply_text("ℹ️ You're not in AI mode.")

async def ai_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    if user_id not in active_ai_users:
        return
    user_msg = update.message.text
    history = active_ai_users[user_id]
    history.append({"role": "user", "content": user_msg})
    try:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "openai/gpt-3.5-turbo",
            "messages": history
        }
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data)
        response.raise_for_status()
        reply = response.json()['choices'][0]['message']['content']
        history.append({"role": "assistant", "content": reply})
        await update.message.reply_text(reply)
    except Exception as e:
        print("AI error:", e)
        await update.message.reply_text("⚠️ AI error occurred.")

# === Register Handlers ===
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("remember", remember))
app.add_handler(CommandHandler("list", list_reminders))
app.add_handler(CommandHandler("delete", delete_reminder))
app.add_handler(CommandHandler("ask", ask))
app.add_handler(CommandHandler("stop", stop_ai))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_message_handler))

app.post_init = on_startup
print("🤖 MemoryMateBot is running...")
app.run_polling()


