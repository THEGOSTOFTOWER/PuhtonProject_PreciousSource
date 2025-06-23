from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, date, timedelta
from typing import List, Optional
import os
import uuid
import aiosqlite
import asyncio
from telegram import Bot
from telegram.ext import Application, CommandHandler
import threading
import logging

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
DB_PATH = os.getenv("DB_PATH", "habits.db")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Database setup
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
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
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS completions (
                id TEXT PRIMARY KEY,
                habit_id TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                notes TEXT,
                FOREIGN KEY (habit_id) REFERENCES habits(id)
            )
        ''')
        await db.commit()
    logger.info("SQLite database initialized")

# Initialize database
asyncio.run(init_db())

# Pydantic Models
class HabitBase(BaseModel):
    name: str
    description: Optional[str] = None
    frequency: str = "daily"  # daily, weekly, monthly
    goal: Optional[str] = None
    category: Optional[str] = None

class HabitCreate(HabitBase):
    pass

class Habit(HabitBase):
    id: str
    created_at: datetime
    is_active: bool = True

class CompletionCreate(BaseModel):
    habit_id: str
    notes: Optional[str] = None

class Completion(BaseModel):
    id: str
    habit_id: str
    completed_at: datetime
    notes: Optional[str] = None

class HabitStats(BaseModel):
    habit_id: str
    habit_name: str
    total_completions: int
    completion_rate: float
    current_streak: int
    longest_streak: int
    last_completion: Optional[datetime] = None

# FastAPI app
app = FastAPI(title="Habit Tracker API", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Telegram Bot Setup
telegram_bot = None
if TELEGRAM_BOT_TOKEN:
    telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Helper functions
async def calculate_habit_stats(habit_id: str, habit_name: str) -> HabitStats:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT completed_at FROM completions WHERE habit_id = ?', (habit_id,))
        completions = await cursor.fetchall()

    total_completions = len(completions)

    # Calculate completion rate (last 30 days)
    thirty_days_ago = datetime.now() - timedelta(days=30)
    recent_completions = [c for c in completions if datetime.fromisoformat(c[0]) > thirty_days_ago]
    completion_rate = len(recent_completions) / 30.0 * 100

    # Calculate streaks
    completion_dates = sorted([datetime.fromisoformat(c[0]).date() for c in completions])

    current_streak = 0
    longest_streak = 0
    temp_streak = 0

    if completion_dates:
        last_date = completion_dates[-1]
        today = date.today()

        # Current streak
        current_date = today
        for completion_date in reversed(completion_dates):
            if completion_date == current_date or completion_date == current_date - timedelta(days=1):
                current_streak += 1
                current_date = completion_date - timedelta(days=1)
            else:
                break

        # Longest streak
        prev_date = None
        for completion_date in completion_dates:
            if prev_date is None or completion_date == prev_date + timedelta(days=1):
                temp_streak += 1
                longest_streak = max(longest_streak, temp_streak)
            else:
                temp_streak = 1
            prev_date = completion_date

    last_completion = datetime.fromisoformat(completions[-1][0]) if completions else None

    return HabitStats(
        habit_id=habit_id,
        habit_name=habit_name,
        total_completions=total_completions,
        completion_rate=completion_rate,
        current_streak=current_streak,
        longest_streak=longest_streak,
        last_completion=last_completion
    )

# API Routes
@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now()}

@app.post("/api/habits", response_model=Habit)
async def create_habit(habit: HabitCreate):
    habit_dict = habit.dict()
    habit_dict["id"] = str(uuid.uuid4())
    habit_dict["created_at"] = datetime.now().isoformat()
    habit_dict["is_active"] = 1

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            '''INSERT INTO habits (id, name, description, frequency, goal, category, created_at, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (habit_dict['id'], habit_dict['name'], habit_dict['description'],
             habit_dict['frequency'], habit_dict['goal'], habit_dict['category'],
             habit_dict['created_at'], habit_dict['is_active'])
        )
        await db.commit()

    return Habit(**habit_dict)

@app.get("/api/habits", response_model=List[Habit])
async def get_habits():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT id, name, description, frequency, goal, category, created_at, is_active FROM habits WHERE is_active = 1')
        habits = await cursor.fetchall()

    return [
        Habit(
            id=habit[0],
            name=habit[1],
            description=habit[2],
            frequency=habit[3],
            goal=habit[4],
            category=habit[5],
            created_at=datetime.fromisoformat(habit[6]),
            is_active=bool(habit[7])
        ) for habit in habits
    ]

@app.get("/api/habits/{habit_id}", response_model=Habit)
async def get_habit(habit_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            'SELECT id, name, description, frequency, goal, category, created_at, is_active FROM habits WHERE id = ?',
            (habit_id,)
        )
        habit = await cursor.fetchone()

    if not habit:
        raise HTTPException(status_code=404, detail="Habit not found")

    return Habit(
        id=habit[0],
        name=habit[1],
        description=habit[2],
        frequency=habit[3],
        goal=habit[4],
        category=habit[5],
        created_at=datetime.fromisoformat(habit[6]),
        is_active=bool(habit[7])
    )

@app.put("/api/habits/{habit_id}", response_model=Habit)
async def update_habit(habit_id: str, habit_update: HabitBase):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT 1 FROM habits WHERE id = ?', (habit_id,))
        exists = await cursor.fetchone()

        if not exists:
            raise HTTPException(status_code=404, detail="Habit not found")

        await db.execute(
            '''UPDATE habits SET name = ?, description = ?, frequency = ?, goal = ?, category = ?
               WHERE id = ?''',
            (habit_update.name, habit_update.description, habit_update.frequency,
             habit_update.goal, habit_update.category, habit_id)
        )
        await db.commit()

        cursor = await db.execute(
            'SELECT id, name, description, frequency, goal, category, created_at, is_active FROM habits WHERE id = ?',
            (habit_id,)
        )
        updated_habit = await cursor.fetchone()

    return Habit(
        id=updated_habit[0],
        name=updated_habit[1],
        description=updated_habit[2],
        frequency=updated_habit[3],
        goal=updated_habit[4],
        category=updated_habit[5],
        created_at=datetime.fromisoformat(updated_habit[6]),
        is_active=bool(updated_habit[7])
    )

@app.delete("/api/habits/{habit_id}")
async def delete_habit(habit_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT 1 FROM habits WHERE id = ?', (habit_id,))
        exists = await cursor.fetchone()

        if not exists:
            raise HTTPException(status_code=404, detail="Habit not found")

        await db.execute('UPDATE habits SET is_active = 0 WHERE id = ?', (habit_id,))
        await db.commit()

    return {"message": "Habit deleted successfully"}

@app.post("/api/habits/{habit_id}/complete", response_model=Completion)
async def complete_habit(habit_id: str, completion: CompletionCreate):
    async with aiosqlite.connect(DB_PATH) as db:
        # Check if habit exists
        cursor = await db.execute('SELECT name FROM habits WHERE id = ?', (habit_id,))
        habit = await cursor.fetchone()
        if not habit:
            raise HTTPException(status_code=404, detail="Habit not found")

        # Check if already completed today
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        cursor = await db.execute(
            'SELECT 1 FROM completions WHERE habit_id = ? AND completed_at >= ? AND completed_at < ?',
            (habit_id, today_start.isoformat(), today_end.isoformat())
        )
        existing_completion = await cursor.fetchone()

        if existing_completion:
            raise HTTPException(status_code=400, detail="Habit already completed today")

        completion_dict = {
            "id": str(uuid.uuid4()),
            "habit_id": habit_id,
            "completed_at": datetime.now().isoformat(),
            "notes": completion.notes
        }

        await db.execute(
            '''INSERT INTO completions (id, habit_id, completed_at, notes)
               VALUES (?, ?, ?, ?)''',
            (completion_dict['id'], completion_dict['habit_id'],
             completion_dict['completed_at'], completion_dict['notes'])
        )
        await db.commit()

    return Completion(**completion_dict)

@app.get("/api/habits/{habit_id}/completions", response_model=List[Completion])
async def get_habit_completions(habit_id: str, days: int = 30):
    start_date = datetime.now() - timedelta(days=days)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            'SELECT id, habit_id, completed_at, notes FROM completions WHERE habit_id = ? AND completed_at >= ?',
            (habit_id, start_date.isoformat())
        )
        completions = await cursor.fetchall()

    return [
        Completion(
            id=completion[0],
            habit_id=completion[1],
            completed_at=datetime.fromisoformat(completion[2]),
            notes=completion[3]
        ) for completion in completions
    ]

@app.get("/api/habits/{habit_id}/stats", response_model=HabitStats)
async def get_habit_stats(habit_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT name FROM habits WHERE id = ?', (habit_id,))
        habit = await cursor.fetchone()
        if not habit:
            raise HTTPException(status_code=404, detail="Habit not found")
        habit_name = habit[0]

    return await calculate_habit_stats(habit_id, habit_name)

@app.get("/api/dashboard")
async def get_dashboard():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT id, name FROM habits WHERE is_active = 1')
        habits = await cursor.fetchall()

    dashboard_data = {
        "total_habits": len(habits),
        "habits_completed_today": 0,
        "current_streak_total": 0,
        "habit_stats": []
    }

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    async with aiosqlite.connect(DB_PATH) as db:
        for habit_id, name in habits:
            stats = await calculate_habit_stats(habit_id, name)
            dashboard_data["habit_stats"].append(stats.dict())
            dashboard_data["current_streak_total"] += stats.current_streak

            # Check if completed today
            cursor = await db.execute(
                'SELECT 1 FROM completions WHERE habit_id = ? AND completed_at >= ? AND completed_at < ?',
                (habit_id, today_start.isoformat(), today_end.isoformat())
            )
            today_completion = await cursor.fetchone()

            if today_completion:
                dashboard_data["habits_completed_today"] += 1

    return dashboard_data

@app.get("/api/analytics/{habit_id}")
async def get_habit_analytics(habit_id: str, days: int = 30):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT name FROM habits WHERE id = ?', (habit_id,))
        habit = await cursor.fetchone()
        if not habit:
            raise HTTPException(status_code=404, detail="Habit not found")
        habit_name = habit[0]

        start_date = datetime.now() - timedelta(days=days)
        cursor = await db.execute(
            'SELECT completed_at FROM completions WHERE habit_id = ? AND completed_at >= ?',
            (habit_id, start_date.isoformat())
        )
        completions = await cursor.fetchall()

    # Create daily completion data
    completion_dates = [datetime.fromisoformat(c[0]).date() for c in completions]
    daily_data = []

    current_date = start_date.date()
    end_date = datetime.now().date()

    while current_date <= end_date:
        completed = 1 if current_date in completion_dates else 0
        daily_data.append({
            "date": current_date.isoformat(),
            "completed": completed
        })
        current_date += timedelta(days=1)

    return {
        "habit_name": habit_name,
        "daily_data": daily_data,
        "total_completions": len(completions),
        "completion_rate": len(completions) / days * 100
    }

# Telegram Bot Handlers
async def start_command(update, context):
    """Handle /start command"""
    welcome_message = """
🎯 Добро пожаловать в Habit Tracker!

Доступные команды:
/habits - Список ваших привычек  
/complete [номер] - Отметить привычку как выполненную
/stats - Статистика по всем привычкам
/help - Помощь
    """
    await update.message.reply_text(welcome_message)

async def habits_command(update, context):
    """Show all habits"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('SELECT id, name, description, frequency FROM habits WHERE is_active = 1')
            habits = await cursor.fetchall()

        if not habits:
            await update.message.reply_text("У вас пока нет активных привычек.")
            return

        message = "📋 Ваши привычки:\n\n"
        for i, (habit_id, name, description, frequency) in enumerate(habits, 1):
            message += f"{i}. {name}\n"
            if description:
                message += f"   📝 {description}\n"
            message += f"   🔄 {frequency}\n\n"

        await update.message.reply_text(message)
    except Exception as e:
        logger.error(f"Error in habits_command: {e}")
        await update.message.reply_text(f"Ошибка: {str(e)}")

async def complete_command(update, context):
    """Complete a habit by number"""
    try:
        if not context.args:
            await update.message.reply_text("Укажите номер привычки: /complete 1")
            return

        habit_number = int(context.args[0])
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('SELECT id, name FROM habits WHERE is_active = 1')
            habits = await cursor.fetchall()

        if habit_number < 1 or habit_number > len(habits):
            await update.message.reply_text("Неверный номер привычки.")
            return

        habit_id, habit_name = habits[habit_number - 1]

        # Check if already completed today
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                'SELECT 1 FROM completions WHERE habit_id = ? AND completed_at >= ? AND completed_at < ?',
                (habit_id, today_start.isoformat(), today_end.isoformat())
            )
            existing_completion = await cursor.fetchone()

        if existing_completion:
            await update.message.reply_text(f"✅ Привычка '{habit_name}' уже выполнена сегодня!")
            return

        # Create completion
        completion_dict = {
            "id": str(uuid.uuid4()),
            "habit_id": habit_id,
            "completed_at": datetime.now().isoformat(),
            "notes": f"Completed via Telegram by {update.effective_user.first_name}"
        }

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                '''INSERT INTO completions (id, habit_id, completed_at, notes)
                   VALUES (?, ?, ?, ?)''',
                (completion_dict['id'], completion_dict['habit_id'],
                 completion_dict['completed_at'], completion_dict['notes'])
            )
            await db.commit()

        await update.message.reply_text(f"🎉 Отлично! Привычка '{habit_name}' отмечена как выполненная!")

    except ValueError:
        await update.message.reply_text("Укажите корректный номер привычки.")
    except Exception as e:
        logger.error(f"Error in complete_command: {e}")
        await update.message.reply_text(f"Ошибка: {str(e)}")

async def stats_command(update, context):
    """Show habit statistics"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('SELECT id, name FROM habits WHERE is_active = 1')
            habits = await cursor.fetchall()

        if not habits:
            await update.message.reply_text("У вас пока нет активных привычек.")
            return

        message = "📊 Статистика привычек:\n\n"

        for habit_id, name in habits:
            stats = await calculate_habit_stats(habit_id, name)
            message += f"🎯 {stats['habit_name']}\n"
            message += f"   ✅ Выполнений: {stats['total_completions']}\n"
            message += f"   🔥 Текущая серия: {stats['current_streak']} дней\n"
            message += f"   🏆 Лучшая серия: {stats['longest_streak']} дней\n"
            message += f"   📈 Успешность: {stats['completion_rate']:.1f}%\n\n"

        await update.message.reply_text(message)
    except Exception as e:
        logger.error(f"Error in stats_command: {e}")
        await update.message.reply_text(f"Ошибка: {str(e)}")

# Start Telegram Bot in background
def start_telegram_bot():
    if not TELEGRAM_BOT_TOKEN:
        print("Telegram Bot Token not provided. Skipping bot initialization.")
        return

    try:
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        # Add handlers
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("habits", habits_command))
        application.add_handler(CommandHandler("complete", complete_command))
        application.add_handler(CommandHandler("stats", stats_command))

        # Start the bot in a new event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        application.run_polling()
    except Exception as e:
        logger.error(f"Error starting Telegram bot: {e}")

# Start Telegram bot in background thread
if TELEGRAM_BOT_TOKEN:
    bot_thread = threading.Thread(target=start_telegram_bot)
    bot_thread.daemon = True
    bot_thread.start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)