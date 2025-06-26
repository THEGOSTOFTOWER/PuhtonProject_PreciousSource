"""
Telegram bot for Habit Tracker with charts and language selection.

Authors: Kirnev Iurii and Vargin Artem
"""

import asyncio
import os
import sys
import io
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import uuid
import gettext
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.error import BadRequest, TelegramError
from dotenv import load_dotenv
import aiosqlite

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.CRITICAL)

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN='7648041706:AAHC30DqMZ5XKCGpXin3m10dDtcMFYCeG_Y'

DB_PATH='habits.db'

DEFAULT_LANGUAGE='ru'

BASE_DIR = Path(__file__).parent.parent

if not TELEGRAM_BOT_TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN not found")
    print("❌ TELEGRAM_BOT_TOKEN not found in .env")
    sys.exit(1)

logger.info(f"Starting bot with token: {TELEGRAM_BOT_TOKEN[:10]}...")

async def init_db() -> None:
    """Initialize SQLite database."""
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS habits (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    frequency TEXT NOT NULL,
                    goal TEXT,
                    category TEXT,
                    created_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS completions (
                    id TEXT PRIMARY KEY,
                    habit_id TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    notes TEXT,
                    FOREIGN KEY (habit_id) REFERENCES habits(id)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    language TEXT NOT NULL DEFAULT 'ru'
                )
                """
            )
            await conn.commit()
        logger.info("SQLite database initialized")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise

# Global state for habit creation
user_states: Dict[int, Dict[str, str]] = {}

def get_main_menu_keyboard(lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    """Create main menu keyboard."""
    _ = get_translation(lang)
    keyboard = [
        [
            InlineKeyboardButton(_("My Habits"), callback_data="show_habits"),
            InlineKeyboardButton(_("Create Habit"), callback_data="create_habit"),
        ],
        [
            InlineKeyboardButton(_("Statistics"), callback_data="show_stats"),
            InlineKeyboardButton(_("Charts"), callback_data="show_charts"),
        ],
        [InlineKeyboardButton(_("Help"), callback_data="show_help")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_language_keyboard() -> InlineKeyboardMarkup:
    """Create language selection keyboard."""
    _ = get_translation(DEFAULT_LANGUAGE)
    keyboard = [
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
    ]
    logger.info(f"Language keyboard created: {keyboard}")
    return InlineKeyboardMarkup(keyboard)

def get_frequency_keyboard(lang: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    """Create frequency selection keyboard."""
    _ = get_translation(lang)
    keyboard = [
        [InlineKeyboardButton(_("Daily"), callback_data="freq_daily")],
        [InlineKeyboardButton(_("Weekly"), callback_data="freq_weekly")],
        [InlineKeyboardButton(_("Monthly"), callback_data="freq_monthly")],
        [InlineKeyboardButton(_("Cancel"), callback_data="main_menu")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def get_habits_keyboard(lang: str = DEFAULT_LANGUAGE) -> Optional[InlineKeyboardMarkup]:
    """Create keyboard with active habits."""
    _ = get_translation(lang)
    async with aiosqlite.connect(DB_PATH) as connection:
        cursor = await connection.execute("SELECT id, name FROM habits WHERE is_active = 1")
        habits = await cursor.fetchall()

    if not habits:
        return None

    keyboard = [[InlineKeyboardButton(f"✅ {name}", callback_data=f"complete_{id}")] for id, name in habits]
    keyboard.append([InlineKeyboardButton(_("Main Menu"), callback_data="main_menu")])
    return InlineKeyboardMarkup(keyboard)

async def get_charts_keyboard(lang: Optional[str] = DEFAULT_LANGUAGE) -> Optional[InlineKeyboardMarkup]:
    """Create keyboard for chart selection."""
    _ = get_translation(lang)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, name FROM habits WHERE is_active = 1")
        habits = await cursor.fetchall()

    if not habits:
        return None

    keyboard = [[InlineKeyboardButton(f"📈 {name}", callback_data=f"chart_{id}")] for id, name in habits]
    keyboard.append([InlineKeyboardButton(_("Overview Chart"), callback_data="chart_all")])
    keyboard.append([InlineKeyboardButton(_("Main Menu"), callback_data="main_menu")])
    return InlineKeyboardMarkup(keyboard)

async def get_user_language(user_id: int) -> str:
    """Retrieve user's language."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT language FROM users WHERE user_id = ?", (user_id,))
            result = await cursor.fetchone()
            return result[0] if result else DEFAULT_LANGUAGE
    except Exception as e:
        logger.error(f"Error retrieving language for user {user_id}: {e}")
        return DEFAULT_LANGUAGE

async def set_user_language(user_id: int, lang: str) -> None:
    """Set user's language."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO users (user_id, language) VALUES (?, ?)",
                (user_id, lang)
            )
            await db.commit()
        logger.info(f"Set language for user {user_id} to {lang}")
    except Exception as e:
        logger.error(f"Error setting language for user {user_id}: {e}")

async def calculate_habit_stats(habit_id: str, habit_name: str, lang: str = DEFAULT_LANGUAGE) -> Dict:
    """Calculate habit statistics."""
    _ = get_translation(lang)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT completed_at FROM completions WHERE habit_id = ?", (habit_id,))
        completions = await cursor.fetchall()

    total_completions = len(completions)
    thirty_days_ago = datetime.now() - timedelta(days=30)
    recent_completions = [c for c in completions if datetime.fromisoformat(c[0]) > thirty_days_ago]
    completion_rate = (len(recent_completions) / 30.0 * 100) if recent_completions else 0

    completion_dates = sorted([datetime.fromisoformat(c[0]).date() for c in completions])
    current_streak = longest_streak = 0

    if completion_dates:
        today = datetime.now().date()
        current_date = today
        for completion_date in reversed(completion_dates):
            if completion_date == current_date or completion_date == current_date - timedelta(days=1):
                current_streak += 1
                current_date = completion_date
            else:
                break

        temp_streak = 1
        prev_date = completion_dates[0]
        for completion_date in completion_dates[1:]:
            if completion_date == prev_date + timedelta(days=1):
                temp_streak += 1
                longest_streak = max(longest_streak, temp_streak)
            else:
                temp_streak = 1
            prev_date = completion_date

    last_completion = datetime.fromisoformat(completions[-1][0]) if completions else None

    return {
        "habit_id": habit_id,
        "habit_name": habit_name,
        "total_completions": total_completions,
        "completion_rate": completion_rate,
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "last_completion": last_completion,
    }

async def generate_habit_chart(habit_id: str, days: int = 30, lang: str = DEFAULT_LANGUAGE) -> Optional[io.BytesIO]:
    """Generate a chart for a habit's progress."""
    _ = get_translation(lang)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT name FROM habits WHERE id = ?", (habit_id,))
        habit = await cursor.fetchone()
        if not habit:
            return None
        habit_name = habit[0]

        start_date = datetime.now() - timedelta(days=days)
        cursor = await db.execute(
            "SELECT completed_at FROM completions WHERE habit_id = ? AND completed_at >= ?",
            (habit_id, start_date.isoformat())
        )
        completions = await cursor.fetchall()

    date_range = []
    completion_data = []
    current_date = start_date.date()
    end_date = datetime.now().date()
    completion_dates = {datetime.fromisoformat(c[0]).date() for c in completions}

    while current_date <= end_date:
        date_range.append(current_date)
        completion_data.append(1 if current_date in completion_dates else 0)
        current_date += timedelta(days=1)

    plt.figure(figsize=(10, 5))
    colors = ["#4CAF50" if x else "#f44336" for x in completion_data]
    plt.bar(date_range, completion_data, color=colors, alpha=0.7)
    plt.title(_("Progress for {}").format(habit_name), fontsize=14, pad=10)
    plt.xlabel(_("Date"))
    plt.ylabel(_("Completion"))
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    plt.gca().xaxis.set_major_locator(mdates.DayLocator(interval=max(1, days // 10)))
    plt.xticks(rotation=45)
    plt.yticks([0, 1], ["❌", "✅"])
    plt.grid(True, alpha=0.3)
    total_completions = sum(completion_data)
    completion_rate = (total_completions / len(completion_data)) * 100
    plt.text(0.02, 0.98, _("Completed: {}/{} days ({:.1f}%)").format(
        total_completions, len(completion_data), completion_rate),
        transform=plt.gca().transAxes, fontsize=10, verticalalignment="top",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.8)
    )
    plt.tight_layout()

    img_buffer = io.BytesIO()
    plt.savefig(img_buffer, format="PNG", dpi=200)
    img_buffer.seek(0)
    plt.close()
    return img_buffer

async def generate_overview_chart(lang: str = DEFAULT_LANGUAGE) -> Optional[io.BytesIO]:
    """Generate overview chart for all habits."""
    _ = get_translation(lang)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, name FROM habits WHERE is_active = 1")
        habits = await cursor.fetchall()

    if not habits:
        return None

    habit_names = []
    completion_rates = []
    current_streaks = []

    for habit_id, name in habits:
        stats = await calculate_habit_stats(habit_id, name, lang)
        habit_names.append(name[:15] + "..." if len(name) > 15 else name)
        completion_rates.append(stats["completion_rate"])
        current_streaks.append(stats["current_streak"])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    ax1.bar(habit_names, completion_rates, color="#2196F3", alpha=0.7)
    ax1.set_title(_("Completion Rate (30 days)"))
    ax1.set_ylabel(_("Percentage (%)"))
    ax1.set_ylim(0, 100)
    ax1.tick_params(axis="x", rotation=45)
    for i, rate in enumerate(completion_rates):
        ax1.text(i, rate + 2, f"{rate:.1f}%", ha="center", fontsize=8)

    ax2.bar(habit_names, current_streaks, color="#FF9800", alpha=0.7)
    ax2.set_title(_("Current Streaks"))
    ax2.set_ylabel(_("Days"))
    ax2.tick_params(axis="x", rotation=45)
    for i, streak in enumerate(current_streaks):
        if streak > 0:
            ax2.text(i, streak + 0.5, f"{streak}", ha="center", fontsize=8)

    plt.tight_layout()
    img_buffer = io.BytesIO()
    plt.savefig(img_buffer, format="PNG", dpi=200)
    img_buffer.seek(0)
    plt.close()
    return img_buffer

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user_id = update.effective_user.id
    lang = await get_user_language(user_id)
    _ = get_translation(lang)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT language FROM users WHERE user_id = ?", (user_id,))
        user_exists = await cursor.fetchone()

    if not user_exists:
        message = _(
            "🌐 Welcome to Habit Tracker!\n\n"
            "Please select your preferred language:"
        )
        reply_markup = get_language_keyboard()
        await update.message.reply_text(message, reply_markup=reply_markup)
        logger.info(f"Displayed language selection for new user {user_id}")
    else:
        user_name = update.effective_user.first_name or _("Friend")
        message = _(
            "🎯 Hello, {}! Welcome to Habit Tracker!\n\n"
            "Track your habits with analytics and charts.\n"
            "Choose an action:"
        ).format(user_name)
        reply_markup = get_main_menu_keyboard(lang)
        await update.message.reply_text(message, reply_markup=reply_markup)
        logger.info(f"Displayed main menu for user {user_id}")

_translations: Dict[str, gettext.GNUTranslations] = {}

def get_translation(lang: str) -> callable:
    """Get translation function for the specified language."""
    # Clear cache for the language to force reload (for debugging)
    if lang in _translations:
        logger.info(f"Clearing translation cache for language: {lang}")
        del _translations[lang]

    try:
        logger.info(f"Attempting to load translation for language: {lang}")
        _translations[lang] = gettext.translation(
            "messages", f"{BASE_DIR}/locale", languages=[lang]
        )
        logger.info(f"Successfully loaded translation for {lang}")
    except FileNotFoundError as e:
        logger.error(f"Translation file for {lang} not found: {e}")
        logger.warning(f"Falling back to English for language: {lang}")
        _translations[lang] = gettext.translation(
            "messages", f"{BASE_DIR}/locale", languages=["en"], fallback=True
        )
    except Exception as e:
        logger.error(f"Error loading translation for {lang}: {e}")
        logger.warning(f"Falling back to English for language: {lang}")
        _translations[lang] = gettext.translation(
            "messages", f"{BASE_DIR}/locale", languages=["en"], fallback=True
        )
    return _translations[lang].gettext

async def handle_language_selection(query: Update.callback_query, user_id: int, lang: str) -> None:
    """Handle language selection."""
    new_lang = query.data.replace("lang_", "")
    await set_user_language(user_id, new_lang)
    _ = get_translation(new_lang)  # Force reload translation
    user_name = query.from_user.first_name or _("Friend")
    message = _(
        "🎯 Hello, {}! Language set to {}.\n\n"
        "Track your habits with analytics and charts.\n"
        "Choose an action:"
    ).format(user_name, "Русский" if new_lang == "ru" else "English")
    reply_markup = get_main_menu_keyboard(new_lang)
    await query.edit_message_text(message, reply_markup=reply_markup)
    logger.info(f"User {user_id} selected language: {new_lang}")
    logger.info(f"Main menu buttons for {new_lang}: {[button.text for row in reply_markup.inline_keyboard for button in row]}")
    logger.info(f"Test translation for 'Welcome to Habit Tracker!': {_('Welcome to Habit Tracker!')}")

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /language command."""
    user_id = update.effective_user.id
    lang = await get_user_language(user_id)
    _ = get_translation(lang)
    message = _("🌐 Select your preferred language:")
    reply_markup = get_language_keyboard()
    await update.message.reply_text(message, reply_markup=reply_markup)
    logger.info(f"Displayed language selection for user {user_id}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button callbacks."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    lang = await get_user_language(user_id)
    _ = get_translation(lang)

    logger.info(f"Callback query: {query.data} from user {user_id}")

    try:
        if query.data.startswith("lang_"):
            await handle_language_selection(query, user_id, lang)
        elif query.data == "main_menu":
            await show_main_menu(query, lang)
        elif query.data == "show_habits":
            await show_habits(query, lang)
        elif query.data == "create_habit":
            await start_create_habit(query, user_id, lang)
        elif query.data == "show_stats":
            await show_stats(query, lang)
        elif query.data == "show_charts":
            await show_charts_menu(query, lang)
        elif query.data == "show_help":
            await show_help(query, lang)
        elif query.data.startswith("complete_"):
            await complete_habit(query, lang)
        elif query.data.startswith("chart_"):
            await send_chart(query, lang)
        elif query.data.startswith("freq_"):
            await handle_frequency_selection(query, user_id, lang)
        elif query.data == "skip_description":
            await skip_description(query, user_id, lang)
    except Exception as e:
        logger.error(f"Error in button_callback: {e}")
        await query.message.reply_text(_("❌ Error: {}").format(str(e)))

async def show_main_menu(query: Update.callback_query, lang: str) -> None:
    """Show main menu."""
    _ = get_translation(lang)
    message = _("🎯 Habit Tracker - Main Menu\n\nChoose an action:")
    reply_markup = get_main_menu_keyboard(lang)
    await query.edit_message_text(message, reply_markup=reply_markup)

async def show_habits(query: Update.callback_query, lang: str) -> None:
    """Show active habits."""
    _ = get_translation(lang)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, name, description, frequency, created_at FROM habits WHERE is_active = 1"
        )
        habits = await cursor.fetchall()

    if not habits:
        message = _("📝 No active habits.\n\nCreate your first habit!")
        keyboard = [[InlineKeyboardButton(_("Main Menu"), callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup)
        return

    message = _("📋 Your active habits:\n\n")
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    async with aiosqlite.connect(DB_PATH) as db:
        for habit_id, name, description, frequency, created_at in habits:
            cursor = await db.execute(
                "SELECT 1 FROM completions WHERE habit_id = ? AND completed_at >= ? AND completed_at < ?",
                (habit_id, today_start.isoformat(), today_end.isoformat())
            )
            completed_today = await cursor.fetchone()
            status = "✅" if completed_today else "⏳"
            message += f"{status} **{name}**\n"
            if description:
                message += f"📝 {description}\n"
            message += f"🔄 {_(frequency.capitalize())}\n"
            message += f"📆 {_('Created')}: {datetime.fromisoformat(created_at).strftime('%d.%m.%Y')}\n\n"

    message += _("Click a habit to mark it as completed:")
    reply_markup = await get_habits_keyboard(lang)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def start_create_habit(query: Update.callback_query, user_id: int, lang: str) -> None:
    """Start habit creation process."""
    _ = get_translation(lang)
    user_states[user_id] = {"step": "name", "lang": lang}
    message = _(
        "➕ Create a new habit\n\n"
        "📖 **Step 1/3**: Enter the habit name\n\n"
        "Examples:\n• Morning exercise\n• Reading\n• Meditation"
    )
    keyboard = [[InlineKeyboardButton(_("Cancel"), callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages during habit creation."""
    user_id = update.effective_user.id
    if user_id not in user_states:
        return

    state = user_states[user_id]
    lang = state.get("lang", DEFAULT_LANGUAGE)
    _ = get_translation(lang)
    text = update.message.text.strip()

    if state["step"] == "name":
        if len(text) > 100:
            await update.message.reply_text(_("❌ Name too long (max 100 chars). Try again:"))
            return
        state["name"] = text
        state["step"] = "frequency"
        message = _(
            "✅ Name: {}\n\n"
            "📖 **Step 2/3**: Choose the frequency:"
        ).format(text)
        reply_markup = get_frequency_keyboard(lang)
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")

    elif state["step"] == "description":
        if len(text) > 500:
            await update.message.reply_text(_("❌ Description too long (max 500 chars). Try again:"))
            return
        state["description"] = text
        await create_habit_final(update, user_id, lang)

async def handle_frequency_selection(query: Update.callback_query, user_id: int, lang: str) -> None:
    """Handle frequency selection."""
    _ = get_translation(lang)
    if user_id not in user_states:
        return

    freq_map = {
        "freq_daily": "daily",
        "freq_weekly": "weekly",
        "freq_monthly": "monthly"
    }
    frequency = freq_map.get(query.data)
    if not frequency:
        return

    state = user_states[user_id]
    state["frequency"] = frequency
    state["step"] = "description"
    message = _(
        "✅ Name: {}\n"
        "✅ Frequency: {}\n\n"
        "📖 **Step 3/3**: Enter a description (optional)"
    ).format(state["name"], _(frequency.capitalize()))
    keyboard = [
        [InlineKeyboardButton(_("Skip"), callback_data="skip_description")],
        [InlineKeyboardButton(_("Cancel"), callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def skip_description(query: Update.callback_query, user_id: int, lang: str) -> None:
    """Skip description step."""
    _ = get_translation(lang)
    if user_id not in user_states:
        return
    state = user_states[user_id]
    state["description"] = ""
    await create_habit_final_callback(query, user_id, lang)

async def create_habit_final(update: Update, user_id: int, lang: str) -> None:
    """Finalize habit creation from text."""
    _ = get_translation(lang)
    try:
        state = user_states[user_id]
        habit_dict = {
            "id": str(uuid.uuid4()),
            "name": state["name"],
            "description": state.get("description", ""),
            "frequency": state["frequency"],
            "goal": "",
            "category": "",
            "created_at": datetime.now().isoformat(),
            "is_active": 1,
        }
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO habits (id, name, description, frequency, goal, category, created_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    habit_dict["id"], habit_dict["name"], habit_dict["description"],
                    habit_dict["frequency"], habit_dict["goal"], habit_dict["category"],
                    habit_dict["created_at"], habit_dict["is_active"]
                )
            )
            await db.commit()

        message = _(
            "🎉 Habit created!\n\n"
            "✅ Name: {}\n"
            "✅ Description: {}\n"
            "✅ Frequency: {}\n\n"
            "Start tracking now!"
        ).format(
            state["name"],
            state.get("description") or _("None"),
            _(state["frequency"].capitalize())
        )
        keyboard = [
            [InlineKeyboardButton(_("My Habits"), callback_data="show_habits")],
            [InlineKeyboardButton(_("Main Menu"), callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")
        del user_states[user_id]
    except Exception as e:
        logger.error(f"Error creating habit for user {user_id}: {e}")
        await update.message.reply_text(_("❌ Error creating habit: {}").format(str(e)))

async def create_habit_final_callback(query: Update.callback_query, user_id: int, lang: str) -> None:
    """Finalize habit creation from callback."""
    _ = get_translation(lang)
    try:
        state = user_states[user_id]
        habit_dict = {
            "id": str(uuid.uuid4()),
            "name": state["name"],
            "description": state.get("description", ""),
            "frequency": state["frequency"],
            "goal": "",
            "category": "",
            "created_at": datetime.now().isoformat(),
            "is_active": 1,
        }
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO habits (id, name, description, frequency, goal, category, created_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    habit_dict["id"], habit_dict["name"], habit_dict["description"],
                    habit_dict["frequency"], habit_dict["goal"], habit_dict["category"],
                    habit_dict["created_at"], habit_dict["is_active"]
                )
            )
            await db.commit()

        message = _(
            "🎉 Habit created!\n\n"
            "✅ Name: {}\n"
            "✅ Description: {}\n"
            "✅ Frequency: {}\n\n"
            "Start tracking now!"
        ).format(
            state["name"],
            state.get("description") or _("None"),
            _(state["frequency"].capitalize())
        )
        keyboard = [
            [InlineKeyboardButton(_("My Habits"), callback_data="show_habits")],
            [InlineKeyboardButton(_("Main Menu"), callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
        del user_states[user_id]
    except Exception as e:
        logger.error(f"Error creating habit for user {user_id}: {e}")
        await query.edit_message_text(_("❌ Error creating habit: {}").format(str(e)))

async def complete_habit(query: Update.callback_query, lang: str) -> None:
    """Mark habit as completed."""
    _ = get_translation(lang)
    habit_id = query.data.replace("complete_", "")
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT name FROM habits WHERE id = ?", (habit_id,))
            habit = await cursor.fetchone()
            if not habit:
                await query.edit_message_text(_("❌ Habit not found"))
                return
            habit_name = habit[0]

            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)
            cursor = await db.execute(
                "SELECT 1 FROM completions WHERE habit_id = ? AND completed_at >= ? AND completed_at < ?",
                (habit_id, today_start.isoformat(), today_end.isoformat())
            )
            completed_exists = await cursor.fetchone()

            if completed_exists:
                message = _(
                    "✅ {} already completed today!\n\n"
                    "⏰ Time: {}\n"
                    "Keep it up!"
                ).format(habit_name, datetime.now().strftime("%H:%M"))
                keyboard = [[InlineKeyboardButton(_("Back to Habits"), callback_data="show_habits")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
                return

            completion_dict = {
                "id": str(uuid.uuid4()),
                "habit_id": habit_id,
                "completed_at": datetime.now().isoformat(),
                "notes": _("Completed by {}").format(query.from_user.first_name or "user")
            }
            await db.execute(
                """
                INSERT INTO completions (id, habit_id, completed_at, notes)
                VALUES (?, ?, ?, ?)
                """,
                (
                    completion_dict["id"], completion_dict["habit_id"],
                    completion_dict["completed_at"], completion_dict["notes"]
                )
            )
            await db.commit()

        stats = await calculate_habit_stats(habit_id, habit_name, lang)
        message = _(
            "🎉 {} completed!\n\n"
            "⏰ Time: {}\n"
            "🔥 Streak: {} days\n"
            "🏆 Longest streak: {} days\n"
            "📈 Total: {} times"
        ).format(
            habit_name, datetime.now().strftime("%H:%M"),
            stats["current_streak"], stats["longest_streak"], stats["total_completions"]
        )
        keyboard = [
            [InlineKeyboardButton(_("Back to Habits"), callback_data="show_habits")],
            [InlineKeyboardButton(_("Main Menu"), callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error completing habit {habit_id}: {e}")
        await query.edit_message_text(_("❌ Error: {}").format(str(e)))

async def show_stats(query: Update.callback_query, lang: str) -> None:
    """Show habit statistics."""
    _ = get_translation(lang)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, name FROM habits WHERE is_active = 1")
            habits = await cursor.fetchall()

        if not habits:
            message = _("📊 No habits available.\n\nCreate your first habit!")
            keyboard = [[InlineKeyboardButton(_("Main Menu"), callback_data="main_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(message, reply_markup=reply_markup)
            return

        message = _("📊 Habit Statistics:\n\n")
        total_completions = total_current_streak = habits_completed_today = 0
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        async with aiosqlite.connect(DB_PATH) as db:
            for i, (habit_id, name) in enumerate(habits, 1):
                stats = await calculate_habit_stats(habit_id, name, lang)
                cursor = await db.execute(
                    "SELECT 1 FROM completions WHERE habit_id = ? AND completed_at >= ? AND completed_at < ?",
                    (habit_id, today_start.isoformat(), today_end.isoformat())
                )
                if await cursor.fetchone():
                    habits_completed_today += 1

                message += f"{i}. **{name}**\n"
                message += f"✅ {_('Completions')}: {stats['total_completions']}\n"
                message += f"🔥 {_('Streak')}: {stats['current_streak']} {_('days')}\n"
                message += f"🏆 {_('Longest')}: {stats['longest_streak']} {_('days')}\n"
                message += f"📈 {_('Rate')}: {stats['completion_rate']:.1f}% (30 {_('days')})\n"
                if stats["last_completion"]:
                    message += f"📆 {_('Last')}: {stats['last_completion'].strftime('%d.%m.%Y')}\n"
                message += "\n"
                total_completions += stats["total_completions"]
                total_current_streak += stats["current_streak"]

        message += f"📈 {_('Overall')}:\n"
        message += f"📝 {_('Habits')}: {len(habits)}\n"
        message += f"✅ {_('Today')}: {habits_completed_today}/{len(habits)}\n"
        message += f"🎯 {_('Total')}: {total_completions}\n"
        message += f"🔥 {_('Streaks')}: {total_current_streak} {_('days')}\n"

        keyboard = [[InlineKeyboardButton(_("Main Menu"), callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error showing stats: {e}")
        await query.edit_message_text(_("❌ Error: {}").format(str(e)))

async def show_charts_menu(query: Update.callback_query, lang: str) -> None:
    """Show charts menu."""
    _ = get_translation(lang)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, name FROM habits WHERE is_active = 1")
        habits = await cursor.fetchall()

    if not habits:
        message = _("📈 No habits for charts.\n\nCreate your first habit!")
        keyboard = [[InlineKeyboardButton(_("Main Menu"), callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup)
        return

    message = _("📈 Progress Charts\n\nSelect a habit or view overview:")
    reply_markup = await get_charts_keyboard(lang)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def send_chart(query: Update.callback_query, lang: str) -> None:
    """Send habit or overview chart."""
    _ = get_translation(lang)
    try:
        if query.data == "chart_all":
            chart_buffer = await generate_overview_chart(lang)
            if chart_buffer:
                await query.message.reply_photo(
                    photo=chart_buffer,
                    caption=_("📊 Overview of all habits\n\nShows completion rates and streaks.")
                )
            else:
                await query.edit_message_text(_("❌ Unable to generate chart"))
        else:
            habit_id = query.data.replace("chart_", "")
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT name FROM habits WHERE id = ?", (habit_id,))
                habit = await cursor.fetchone()
            if not habit:
                await query.edit_message_text(_("❌ Habit not found"))
                return

            chart_buffer = await generate_habit_chart(habit_id, lang=lang)
            if chart_buffer:
                stats = await calculate_habit_stats(habit_id, habit[0], lang)
                caption = _(
                    "📈 Chart for {}\n\n"
                    "✅ Completions: {}\n"
                    "🔥 Streak: {} days\n"
                    "🏆 Longest: {} days\n"
                    "📈 Rate: {:.1f}%"
                ).format(
                    habit[0], stats["total_completions"], stats["current_streak"],
                    stats["longest_streak"], stats["completion_rate"]
                )
                await query.message.reply_photo(photo=chart_buffer, caption=caption, parse_mode="Markdown")
            else:
                await query.edit_message_text(_("❌ Unable to generate chart"))

        keyboard = [[InlineKeyboardButton(_("Back to Charts"), callback_data="show_charts")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(_("📈 Chart sent above"), reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error generating chart: {e}")
        await query.edit_message_text(_("❌ Error: {}").format(str(e)))

async def show_help(query: Update.callback_query, lang: str) -> None:
    """Show help message."""
    _ = get_translation(lang)
    message = _(
        "🆘 Habit Tracker Help\n\n"
        "📖 Track habits with analytics and charts.\n\n"
        "🚀 Features:\n"
        "• Create habits\n"
        "• Track completions\n"
        "• View stats\n"
        "• Generate charts\n\n"
        "📱 Commands:\n"
        "/start - Start bot\n"
        "/language - Change language\n\n"
        "💡 Tips:\n"
        "• Mark habits daily\n"
        "• Use charts for motivation\n\n"
        "Good luck! 🚀"
    )
    keyboard = [[InlineKeyboardButton(_("Main Menu"), callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors."""
    logger.error(f"Update {update} caused error: {context.error}")
    if isinstance(context.error, BadRequest) and "stranded" in str(context.error).lower():
        logger.info("Ignoring old callback query")
        return
    if isinstance(context.error, TelegramError):
        logger.error(f"Telegram API error: {context.error}")
    if update and update.effective_message:
        _ = get_translation(DEFAULT_LANGUAGE)
        await update.effective_message.reply_text(_("❌ Bot error. Try again later."))

async def main() -> None:
    """Run main function in the bot."""
    _ = get_translation(DEFAULT_LANGUAGE)
    logger.info(_("Starting bot..."))
    try:
        await init_db()
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("language", language_command))
        application.add_handler(CallbackQueryHandler(button_callback))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
        application.add_error_handler(error_handler)

        logger.info("Bot configured successfully")
        logger.info("Starting polling...")
        try:
            await application.initialize()
            await application.start()
            await application.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
                timeout=30
            )
            logger.info("Bot polling started successfully")
            # Keep the application running
            while True:
                await asyncio.sleep(3600)  # Sleep to keep the loop alive
        except TelegramError as te:
            logger.error(f"Telegram API error during polling: {te}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during polling: {e}")
            raise
        finally:
            logger.info("Shutting down bot...")
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
            logger.info("Bot shutdown completed")
    except Exception as e:
        logger.error(f"Failed to run bot: {e}")
        raise

def run_bot():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        print(f"Fatal error: {e}")
        sys.exit(1)