import os
import sqlite3
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

DB_FILE = "streaming.db"
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

SUPPORTED_SERVICES = ["Netflix", "Disney+", "Prime Video", "HBO Max"]

# =========================
# DATABASE SETUP & QUERIES
# =========================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service TEXT NOT NULL,
            profile TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'available',
            customer TEXT,
            device TEXT,
            expires_at TEXT,
            UNIQUE(service, profile)
        )
    """)
    conn.commit()
    conn.close()


def get_profiles(service=None):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    if service:
        cur.execute(
            "SELECT id, service, profile, status, customer, device, expires_at FROM profiles WHERE service = ? ORDER BY id",
            (service,)
        )
    else:
        cur.execute(
            "SELECT id, service, profile, status, customer, device, expires_at FROM profiles ORDER BY service, id"
        )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_profile(profile_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, service, profile, status, customer, device, expires_at FROM profiles WHERE id = ?",
        (profile_id,)
    )
    row = cur.fetchone()
    conn.close()
    return row


def add_profile(service, profile_name):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO profiles (service, profile) VALUES (?, ?)",
            (service, profile_name)
        )
        conn.commit()
        result = True
    except sqlite3.IntegrityError:
        result = False
    conn.close()
    return result


def delete_profile(profile_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    conn.commit()
    conn.close()


def update_profile(profile_id, customer, device, expires_at):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        UPDATE profiles
        SET status = 'unavailable', customer = ?, device = ?, expires_at = ?
        WHERE id = ?
    """, (customer, device, expires_at, profile_id))
    conn.commit()
    conn.close()


def make_available(profile_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        UPDATE profiles
        SET status = 'available', customer = NULL, device = NULL, expires_at = NULL
        WHERE id = ?
    """, (profile_id,))
    conn.commit()
    conn.close()

# =========================
# SECURITY
# =========================

def is_admin(update: Update):
    if not ADMIN_CHAT_ID:
        return False
    return str(update.effective_user.id) == str(ADMIN_CHAT_ID)


async def unauthorized(update: Update):
    if update.message:
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
    elif update.callback_query:
        await update.callback_query.answer("⛔ Unauthorized access.", show_alert=True)

# =========================
# BOT COMMANDS & HANDLERS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await unauthorized(update)
        return

    text = "🍿 *Streaming Service Monitor Bot*\n\nChoose an action below to manage your profiles."
    keyboard = [
        [
            InlineKeyboardButton("📋 View Profiles", callback_data="cmd:profiles"),
            InlineKeyboardButton("➕ Add Profile", callback_data="cmd:add"),
        ],
        [
            InlineKeyboardButton("🔑 Assign Profile (/use)", callback_data="cmd:use"),
            InlineKeyboardButton("🗑 Remove Profile", callback_data="cmd:remove"),
        ]
    ]
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_profiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    if not is_admin(update):
        await unauthorized(update)
        return

    rows = get_profiles()
    if not rows:
        target = query.message if query else update.message
        await target.reply_text("📭 No profiles found in database.")
        return

    text = "📋 *Streaming Accounts Overview*\n\n"
    current_service = None

    for row in rows:
        _, service, profile, status, customer, device, expires = row
        if service != current_service:
            current_service = service
            text += f"\n📺 *{service}*\n" + "─"*20 + "\n"

        if status == "available":
            text += f"🟢 *{profile}* — AVAILABLE\n"
        else:
            text += (
                f"🔴 *{profile}* — UNAVAILABLE\n"
                f"├ 👤 Customer: {customer}\n"
                f"├ 📱 Device: {device}\n"
                f"└ ⏰ Until: {expires}\n"
            )

    target = query.message if query else update.message
    await target.reply_text(text, parse_mode="Markdown")


async def add_profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(update):
        await unauthorized(update)
        return

    buttons = [[InlineKeyboardButton(s, callback_data=f"add_svc:{s}")] for s in SUPPORTED_SERVICES]
    await query.message.reply_text("Select the platform:", reply_markup=InlineKeyboardMarkup(buttons))


async def add_profile_service_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    service = query.data.split(":")[1]
    context.user_data["adding_service"] = service
    context.user_data["state"] = "WAITING_ADD_NAME"

    await query.message.reply_text(f"➕ Reply with the profile name for *{service}*.\nExample: `Profile 1`", parse_mode="Markdown")


async def use_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    if not is_admin(update):
        await unauthorized(update)
        return

    rows = get_profiles()
    available_rows = [r for r in rows if r[3] == "available"]

    if not available_rows:
        target = query.message if query else update.message
        await target.reply_text("❌ All profiles are currently occupied.")
        return

    buttons = [
        [InlineKeyboardButton(f"🟢 [{r[1]}] {r[2]}", callback_data=f"use_p:{r[0]}")]
        for r in available_rows
    ]
    
    target = query.message if query else update.message
    await target.reply_text("Choose a profile to assign:", reply_markup=InlineKeyboardMarkup(buttons))


async def choose_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    profile_id = int(query.data.split(":")[1])
    context.user_data["selected_profile"] = profile_id
    context.user_data["state"] = "WAITING_CUSTOMER_NAME"

    await query.message.reply_text("👤 Send the customer's name/handle.")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    state = context.user_data.get("state")
    text = update.message.text.strip()

    if state == "WAITING_ADD_NAME":
        service = context.user_data.get("adding_service")
        if add_profile(service, text):
            await update.message.reply_text(f"✅ Created profile *{text}* under *{service}*.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ A profile named '{text}' already exists for {service}.")
        context.user_data.clear()

    elif state == "WAITING_CUSTOMER_NAME":
        context.user_data["customer"] = text
        context.user_data["state"] = "WAITING_DEVICE_NAME"
        await update.message.reply_text("📱 Send the customer's device (e.g., `Samsung TV`).", parse_mode="Markdown")

    elif state == "WAITING_DEVICE_NAME":
        context.user_data["device"] = text
        keyboard = [
            [InlineKeyboardButton("1 Day", callback_data="dur:1"), InlineKeyboardButton("3 Days", callback_data="dur:3")],
            [InlineKeyboardButton("7 Days", callback_data="dur:7"), InlineKeyboardButton("14 Days", callback_data="dur:14")],
            [InlineKeyboardButton("30 Days", callback_data="dur:30"), InlineKeyboardButton("90 Days", callback_data="dur:90")],
        ]
        await update.message.reply_text("⏱️ Select subscription duration:", reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data["state"] = None


async def duration_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    days = int(query.data.split(":")[1])
    profile_id = context.user_data.get("selected_profile")
    customer = context.user_data.get("customer")
    device = context.user_data.get("device")

    if not all([profile_id, customer, device]):
        await query.message.reply_text("❌ Session timed out or invalid data. Use /use again.")
        context.user_data.clear()
        return

    expiry = datetime.now(timezone.utc) + timedelta(days=days)
    expiry_text = expiry.strftime("%d %b %Y, %I:%M %p UTC")

    update_profile(profile_id, customer, device, expiry_text)
    profile = get_profile(profile_id)

    await query.message.reply_text(
        f"🔴 *{profile[1]} - {profile[2]} is now UNAVAILABLE*\n\n"
        f"👤 Customer: {customer}\n"
        f"📱 Device: {device}\n"
        f"⏱️ Duration: {days} day(s)\n"
        f"⏰ Expires: {expiry_text}",
        parse_mode="Markdown"
    )
    context.user_data.clear()


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    if not is_admin(update):
        await unauthorized(update)
        return

    rows = get_profiles()
    if not rows:
        target = query.message if query else update.message
        await target.reply_text("📭 No profiles to delete.")
        return

    buttons = [[InlineKeyboardButton(f"🗑 [{r[1]}] {r[2]}", callback_data=f"del:{r[0]}")] for r in rows]
    target = query.message if query else update.message
    await target.reply_text("Select a profile to permanently remove:", reply_markup=InlineKeyboardMarkup(buttons))


async def delete_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    profile_id = int(query.data.split(":")[1])
    profile = get_profile(profile_id)

    if profile:
        delete_profile(profile_id)
        await query.message.reply_text(f"🗑 Removed *{profile[1]} ({profile[2]})*", parse_mode="Markdown")

# =========================
# BACKGROUND EXPIRY CHECKER
# =========================

async def check_expired(context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_CHAT_ID:
        return

    now = datetime.now(timezone.utc)

    for row in get_profiles():
        profile_id, service, profile, status, customer, device, expires = row
        if status != "unavailable" or not expires:
            continue

        try:
            expiry_time = datetime.strptime(expires, "%d %b %Y, %I:%M %p UTC").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if now >= expiry_time:
            make_available(profile_id)
            await context.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=(
                    f"🔔 *PROFILE AVAILABLE AGAIN*\n\n"
                    f"📺 Service: *{service}*\n"
                    f"🟢 Profile: *{profile}*\n"
                    f"👤 Previous Customer: {customer}\n"
                    f"📱 Device: {device}"
                ),
                parse_mode="Markdown"
            )

# =========================
# MAIN BOT RUNNER
# =========================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable not set.")

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profiles", show_profiles))
    app.add_handler(CommandHandler("use", use_command))
    app.add_handler(CommandHandler("remove", remove_command))

    # Callback Queries
    app.add_handler(CallbackQueryHandler(show_profiles, pattern="^cmd:profiles$"))
    app.add_handler(CallbackQueryHandler(add_profile_start, pattern="^cmd:add$"))
    app.add_handler(CallbackQueryHandler(use_command, pattern="^cmd:use$"))
    app.add_handler(CallbackQueryHandler(remove_command, pattern="^cmd:remove$"))
    
    app.add_handler(CallbackQueryHandler(add_profile_service_selected, pattern="^add_svc:"))
    app.add_handler(CallbackQueryHandler(choose_profile, pattern="^use_p:"))
    app.add_handler(CallbackQueryHandler(duration_selected, pattern="^dur:"))
    app.add_handler(CallbackQueryHandler(delete_selected, pattern="^del:"))

    # Generic Text Receiver
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Job Queue for Auto Expiry Check (Runs every 60s)
    app.job_queue.run_repeating(check_expired, interval=60, first=10)

    print("Streaming Service Monitor Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
