import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, InlineKeyboardMarkup
import logging
from . import (
    set_user_language,
    get_charts_keyboard,
    get_language_keyboard,
    start_command,
    DB_PATH
)

class TestHabitBotFunctions(unittest.IsolatedAsyncioTestCase):

    @patch("aiosqlite.connect")
    async def test_set_user_language_executes_correct_query(self, mock_connect):
        """Проверяет, что SQL-запрос формируется правильно при установке языка."""
        mock_conn = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_conn

        await set_user_language(12345, "ru")

        mock_conn.execute.assert_awaited_once_with(
            "INSERT OR REPLACE INTO users (user_id, language) VALUES (?, ?)",
            (12345, "ru")
        )
        mock_conn.commit.assert_awaited_once()

    @patch("aiosqlite.connect")
    async def test_get_charts_keyboard_returns_markup(self, mock_connect):
        """Проверяет, что клавиатура с графиками возвращается при наличии привычек."""
        mock_conn = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_conn
        mock_conn.execute.return_value.fetchall = AsyncMock(return_value=[(1, "Exercise"), (2, "Read")])

        markup = await get_charts_keyboard("en")
        self.assertIsInstance(markup, InlineKeyboardMarkup)
        self.assertEqual(len(markup.inline_keyboard), 4)  # 2 привычки + 2 кнопки внизу

    @patch("aiosqlite.connect")
    async def test_get_charts_keyboard_returns_none_if_no_habits(self, mock_connect):
        """Проверяет, что возвращается None, если нет активных привычек."""
        mock_conn = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_conn
        mock_conn.execute.return_value.fetchall = AsyncMock(return_value=[])

        result = await get_charts_keyboard("en")
        self.assertIsNone(result)

    def test_get_language_keyboard_structure(self):
        """Проверяет, что клавиатура выбора языка содержит правильные кнопки."""
        keyboard = get_language_keyboard()
        self.assertIsInstance(keyboard, InlineKeyboardMarkup)
        self.assertEqual(len(keyboard.inline_keyboard), 2)
        self.assertIn("🇷🇺", keyboard.inline_keyboard[0][0].text)
        self.assertIn("🇬🇧", keyboard.inline_keyboard[1][0].text)

    @patch("aiosqlite.connect")
    async def test_start_command_for_new_user_shows_language_keyboard(self, mock_connect):
        """Проверяет поведение /start для нового пользователя."""
        mock_conn = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_conn
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_conn.execute.return_value = mock_cursor

        mock_update = MagicMock()
        mock_update.effective_user.id = 123
        mock_update.effective_user.first_name = "Юра"
        mock_update.message.reply_text = AsyncMock()

        mock_context = MagicMock()

        await start_command(mock_update, mock_context)

        mock_update.message.reply_text.assert_awaited_once()
        args, kwargs = mock_update.message.reply_text.call_args
        self.assertIn("🌐", args[0])  # сообщение для нового пользователя