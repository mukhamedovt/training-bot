import os
import logging
import sqlite3
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== БАЗА ДАННЫХ ====================
class Database:
    def __init__(self, db_path='training_bot.db'):
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        """Создание таблиц в базе данных"""
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                current_week INTEGER DEFAULT 1,
                current_day INTEGER DEFAULT 1,
                total_workouts INTEGER DEFAULT 0,
                total_exercises INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                week INTEGER,
                day INTEGER,
                exercise_id TEXT,
                completed BOOLEAN DEFAULT 0,
                weight REAL DEFAULT NULL,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                UNIQUE(user_id, exercise_id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS timer_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                exercise_name TEXT,
                duration_seconds INTEGER,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        self.conn.commit()
        logger.info("Таблицы базы данных созданы.")

    def create_user(self, user_id: int, username: str, full_name: str):
        """Создание нового пользователя"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO users (id, username, full_name)
            VALUES (?, ?, ?)
        ''', (user_id, username, full_name))
        self.conn.commit()

    def get_user(self, user_id: int):
        """Получение информации о пользователе"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        return cursor.fetchone()

    def update_current_week_day(self, user_id: int, week: int, day: int):
        """Обновление текущей недели и дня пользователя"""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE users SET current_week = ?, current_day = ? WHERE id = ?
        ''', (week, day, user_id))
        self.conn.commit()

    def get_user_progress(self, user_id: int, week: int, day: int):
        """Получение прогресса пользователя для конкретного дня"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT exercise_id, completed, weight FROM user_progress
            WHERE user_id = ? AND week = ? AND day = ?
        ''', (user_id, week, day))
        rows = cursor.fetchall()
        progress = {}
        for row in rows:
            exercise_id, completed, weight = row
            progress[exercise_id] = {
                'completed': bool(completed),
                'weight': weight
            }
        return progress

    def update_exercise_status(self, user_id: int, week: int, day: int, exercise_id: str, completed: bool):
        """Обновление статуса выполнения упражнения"""
        cursor = self.conn.cursor()
        # Проверяем, существует ли запись
        cursor.execute('''
            SELECT completed FROM user_progress WHERE user_id = ? AND exercise_id = ?
        ''', (user_id, exercise_id))
        existing = cursor.fetchone()

        if existing is not None:
            # Обновляем существующую запись
            cursor.execute('''
                UPDATE user_progress SET completed = ?, week = ?, day = ?, completed_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND exercise_id = ?
            ''', (int(completed), week, day, user_id, exercise_id))
        else:
            # Вставляем новую запись
            cursor.execute('''
                INSERT INTO user_progress (user_id, week, day, exercise_id, completed)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, week, day, exercise_id, int(completed)))

        # Обновляем статистику пользователя
        if completed and not existing: # Только если стало выполненным и ранее не было
            cursor.execute('''
                UPDATE users SET total_exercises = total_exercises + 1 WHERE id = ?
            ''', (user_id,))
        elif not completed and existing and existing[0] == 1: # Только если стало невыполненным и ранее было выполнено
             cursor.execute('''
                UPDATE users SET total_exercises = total_exercises - 1 WHERE id = ?
            ''', (user_id,))

        self.conn.commit()

    def save_exercise_weight(self, user_id: int, week: int, day: int, exercise_id: str, weight: float):
        """Сохранение веса для упражнения"""
        cursor = self.conn.cursor()
        # Обновляем или вставляем вес
        cursor.execute('''
            INSERT OR REPLACE INTO user_progress (user_id, week, day, exercise_id, weight, completed_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, week, day, exercise_id, weight))
        self.conn.commit()

    def log_timer_usage(self, user_id: int, exercise_name: str, duration_seconds: int):
        """Логирование использования таймера"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO timer_history (user_id, exercise_name, duration_seconds)
            VALUES (?, ?, ?)
        ''', (user_id, exercise_name, duration_seconds))
        self.conn.commit()


# ==================== ПРОГРАММА ТРЕНИРОВОК ====================
class TrainingProgram:
    def __init__(self):
        # --- Полная программа ---
        self.full_program = {
            1: { # Неделя 1
                1: { # День 1
                    "name": "Высокоинтенсивный день",
                    "intensity": "Высокоинтенсивный",
                    "reps": "6-8 повторений",
                    "sets": "2 рабочих подхода",
                    "rir_scheme": "1 подход RIR2, 2 подход RIR1",
                    "warmup": "5-7 минут кардио + суставная разминка",
                    "exercises": [
                        {
                            "id": "1-1-1",
                            "name": "СКРУЧИВАНИЕ ТАЗА В ВИСЕ НА ПРЕСС",
                            "group": "Пресс",
                            "type": "Односуставное",
                            "sets_details": "2 подхода, около 15 повторений",
                            "rir": "RIR0",
                            "muscles": ["Пресс"],
                            "description": "Упражнение для нижней части пресса.",
                            "sets": [
                                {"reps": "15", "rir_text": "RIR2"},
                                {"reps": "15", "rir_text": "RIR1"}
                            ]
                        },
                        {
                            "id": "1-1-2",
                            "name": "ЖИМ ШТАНГИ ЛЕЖА",
                            "group": "Грудные",
                            "type": "Многосуставное",
                            "sets_details": "2 подхода",
                            "rir": "RIR1",
                            "muscles": ["Грудные", "Передние дельты", "Трицепсы"],
                            "description": "Классическое упражнение для развития грудных мышц.",
                            "sets": [
                                {"reps": "6-8", "rir_text": "RIR2"},
                                {"reps": "6-8", "rir_text": "RIR1"}
                            ]
                        },
                        # ... добавьте остальные упражнения для дня 1 ...
                    ]
                },
                2: { # День 2
                    "name": "Среднеинтенсивный день",
                    "intensity": "Среднеинтенсивный",
                    "reps": "10-12 повторений",
                    "sets": "3 рабочих подхода",
                    "rir_scheme": "1 и 2 подход RIR1, 3 подход RIR0",
                    "warmup": "5-7 минут кардио + суставная разминка",
                    "exercises": [
                        {
                            "id": "1-2-1",
                            "name": "МОЛИТВА",
                            "group": "Пресс",
                            "type": "Односуставное",
                            "sets_details": "2 подхода, около 15 повторений",
                            "rir": "RIR0",
                            "muscles": ["Пресс", "Нижняя часть спины"],
                            "description": "Упражнение на стабилизацию корпуса.",
                            "sets": [
                                {"reps": "10-12", "rir_text": "RIR1"},
                                {"reps": "10-12", "rir_text": "RIR0"}
                            ]
                        },
                        # ... добавьте остальные упражнения для дня 2 ...
                    ]
                },
                3: { # День 3
                    "name": "Низкоинтенсивный день",
                    "intensity": "Низкоинтенсивный",
                    "reps": "15-20 повторений",
                    "sets": "1-2 рабочих подхода",
                    "rir_scheme": "RIR2-RIR3",
                    "warmup": "5-7 минут кардио + суставная разминка",
                    "exercises": [
                         # ... упражнения для дня 3 ...
                    ]
                }
            },
            # ... Добавьте недели 2-6 по аналогии ...
             2: { # Неделя 2
                1: { # День 1
                    "name": "Высокоинтенсивный день 2",
                    "intensity": "Высокоинтенсивный",
                    "reps": "5-7 повторений",
                    "sets": "3 рабочих подхода",
                    "rir_scheme": "1 подход RIR3, 2 подход RIR2, 3 подход RIR1",
                    "warmup": "10 минут кардио + динамическая разминка",
                    "exercises": [
                        {
                            "id": "2-1-1",
                            "name": "ПРИСЕДАНИЯ СО ШТАНГОЙ",
                            "group": "Ноги",
                            "type": "Многосуставное",
                            "sets_details": "3 подхода",
                            "rir": "RIR1",
                            "muscles": ["Квадрицепсы", "Ягодицы", "Подколенные сухожилия"],
                            "description": "Базовое упражнение для ног.",
                            "sets": [
                                {"reps": "5-7", "rir_text": "RIR3"},
                                {"reps": "5-7", "rir_text": "RIR2"},
                                {"reps": "5-7", "rir_text": "RIR1"}
                            ]
                        },
                        # ... остальные упражнения ...
                    ]
                },
                # ... Дни 2 и 3 недели 2 ...
            },
            # ... Остальные недели 3-6 ...
        }

    def get_week(self, week_num: int) -> Optional[Dict]:
        """Получить данные недели"""
        return self.full_program.get(week_num)

    def get_day(self, week_num: int, day_num: int) -> Optional[Dict]:
        """Получить данные дня"""
        week_data = self.get_week(week_num)
        if week_data:
            return week_data.get(day_num)
        return None

    def get_exercise(self, week_num: int, day_num: int, exercise_index: int) -> Optional[Dict]:
        """Получить данные упражнения по индексу в дне"""
        day_data = self.get_day(week_num, day_num)
        if day_data and "exercises" in day_data:
            exercises = day_data["exercises"]
            if 0 <= exercise_index < len(exercises):
                return exercises[exercise_index]
        return None


# ==================== ТЕЛЕГРАМ БОТ ====================
class TrainingBot:
    def __init__(self, token: str):
        self.token = token
        self.db = Database()
        self.program = TrainingProgram()
        self.user_timers = {} # {user_id: {"seconds": int, "message_id": int, "exercise_name": str}}
        self.active_timers = {} # {user_id: timer_task}

        # Создаем приложение
        self.application = Application.builder().token(token).build()

        # Регистрируем обработчики
        self.setup_handlers()

    def setup_handlers(self):
        """Настройка всех обработчиков команд"""
        # Команды
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("program", self.program_command))
        self.application.add_handler(CommandHandler("progress", self.progress_command))
        self.application.add_handler(CommandHandler("timer", self.timer_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("reset", self.reset_command))

        # Обработчики callback-запросов (кнопки)
        self.application.add_handler(CallbackQueryHandler(self.button_handler))

        # Обработчики текстовых сообщений (для ввода веса)
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.message_handler))


    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user = update.effective_user
        self.db.create_user(user.id, user.username, user.full_name)
        welcome_text = (
            "🏋️‍♂️ *Добро пожаловать в Тренировочный Бот!*\n\n"
            "Я помогу вам пройти 6-недельную программу тренировок:\n"
            "• Отслеживание прогресса\n"
            "• Таймер отдыха между подходами\n"
            "• Подробные описания упражнений\n"
            "• Статистика и история\n\n"
            "Используйте /program чтобы начать тренировку!"
        )
        keyboard = [
            [InlineKeyboardButton("📋 Начать тренировку", callback_data="program_main")],
            [InlineKeyboardButton("📊 Моя статистика", callback_data="stats_main")],
            [InlineKeyboardButton("🆘 Помощь", callback_data="help_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(welcome_text, parse_mode='Markdown', reply_markup=reply_markup)


    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /help"""
        help_text = (
            "📚 *Список команд:*\n\n"
            "*/start* - Начать работу с ботом\n"
            "*/program* - Открыть программу тренировок\n"
            "*/progress* - Показать ваш прогресс\n"
            "*/timer* - Открыть таймер отдыха\n"
            "*/stats* - Показать статистику\n"
            "*/reset* - Сбросить прогресс\n\n"
            "*Как использовать:*\n"
            "1. Начните с /program\n"
            "2. Выберите неделю и день\n"
            "3. Отмечайте выполненные упражнения\n"
            "4. Используйте таймер для отдыха\n\n"
            "Удачи в тренировках! 💪"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')


    async def program_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать меню программы"""
        await self.show_week_selection(update.effective_chat.id, update.message.message_id if update.message else None)

    async def show_week_selection(self, chat_id: int, message_id: int = None):
        """Показать выбор недели"""
        keyboard = []
        for week in range(1, 7): # 6 недель
            keyboard.append([InlineKeyboardButton(f"📅 Неделя {week}", callback_data=f"week_{week}")])
        keyboard.append([
            InlineKeyboardButton("📊 Текущая тренировка", callback_data="current_training"),
            InlineKeyboardButton("🔙 Назад", callback_data="main_menu")
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = "📋 *Выберите неделю тренировок:*"
        if message_id:
            await self.application.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text, parse_mode='Markdown', reply_markup=reply_markup
            )
        else:
            await self.application.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', reply_markup=reply_markup)

    async def show_day_selection(self, chat_id: int, week: int, message_id: int):
        """Показать выбор дня для недели"""
        week_data = self.program.get_week(week)
        if not week_data:
            await self.application.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="Ошибка: неделя не найдена.")
            return

        keyboard = []
        for day in range(1, 4): # 3 дня в неделе
            day_name = week_data[day]["name"] if day in week_data else f"День {day}"
            keyboard.append([InlineKeyboardButton(f"🏋️‍♂️ {day_name}", callback_data=f"day_{week}_{day}")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_weeks")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        text = f"📅 *Выберите день для Недели {week}:*"
        await self.application.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, parse_mode='Markdown', reply_markup=reply_markup
        )

    async def show_exercise_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE, week: int, day: int):
        """Показать список упражнений для дня"""
        query = update.callback_query
        user_id = query.from_user.id
        chat_id = query.message.chat_id
        message_id = query.message.message_id

        day_data = self.program.get_day(week, day)
        if not day_data:
            await query.edit_message_text(text="Ошибка: день не найден.")
            return

        # Обновляем текущую неделю/день пользователя
        self.db.update_current_week_day(user_id, week, day)

        # Получаем прогресс пользователя
        progress = self.db.get_user_progress(user_id, week, day)

        # Формируем текст дня
        day_text = (
            f"*Неделя {week}, {day_data['name']}*\n\n"
            f"💪 *Интенсивность:* {day_data['intensity']}\n"
            f"🔢 *Повторения:* {day_data['reps']}\n"
            f"📊 *Подходы:* {day_data['sets']}\n"
            f"🎯 *Схема RIR:* {day_data['rir_scheme']}\n"
            f"🔥 *Разминка:* {day_data['warmup']}\n\n"
            f"*Упражнения:*"
        )

        keyboard = []
        for i, exercise in enumerate(day_data["exercises"]):
            exercise_id = exercise["id"]
            status = "✅" if progress.get(exercise_id, {}).get('completed', False) else "⭕"
            weight_info = f" ⚖️ {progress.get(exercise_id, {}).get('weight', '?')} кг" if progress.get(exercise_id, {}).get('weight') else ""
            keyboard.append([
                InlineKeyboardButton(
                    f"{status} {exercise['name']}{weight_info}",
                    callback_data=f"exercise_{week}_{day}_{i}"
                )
            ])

        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_days_{week}")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=day_text, parse_mode='Markdown', reply_markup=reply_markup)


    async def show_exercise_detail(self, update: Update, context: ContextTypes.DEFAULT_TYPE, week: int, day: int, exercise_index: int):
        """Показать детали упражнения"""
        query = update.callback_query
        user_id = query.from_user.id
        chat_id = query.message.chat_id
        message_id = query.message.message_id

        exercise = self.program.get_exercise(week, day, exercise_index)
        if not exercise:
            await query.edit_message_text(text="Ошибка: упражнение не найдено.")
            return

        # Получаем прогресс для этого упражнения
        progress = self.db.get_user_progress(user_id, week, day)
        exercise_progress = progress.get(exercise['id'], {})
        is_completed = exercise_progress.get('completed', False)
        saved_weight = exercise_progress.get('weight', None)

        exercise_text = (
            f"*{exercise['name']}*\n"
            f"🏋️‍♂️ *Группа мышц:* {exercise['group']}\n"
            f"⚙️ *Тип:* {exercise['type']}\n"
            f"⚡ *RIR:* {exercise['rir']}\n"
            f"*Работающие мышцы:* {', '.join(exercise['muscles'])}\n\n"
            f"*Описание:*\n{exercise['description']}\n\n"
            f"*Подходы:*\n"
        )

        # Добавляем подходы
        for i, set_data in enumerate(exercise["sets"], 1):
            exercise_text += f"{i}. {set_data['reps']} повторений ({set_data['rir_text']})\n"

        # Клавиатура упражнения
        keyboard = []

        # Кнопки подходов с вводом веса
        for i, set_data in enumerate(exercise["sets"]):
             keyboard.append([
                InlineKeyboardButton(
                    f"⚖️ Подход {i+1}: ввести вес",
                    callback_data=f"set_weight_{week}_{day}_{exercise_index}_{i}"
                )
            ])

        # Основные кнопки
        if is_completed:
            keyboard.append([InlineKeyboardButton("🔄 Отменить выполнение", callback_data=f"toggle_complete_{exercise['id']}_{week}_{day}")])
        else:
            keyboard.append([InlineKeyboardButton("✅ Отметить как выполнено", callback_data=f"toggle_complete_{exercise['id']}_{week}_{day}")])

        keyboard.append([
            InlineKeyboardButton("⏱️ Таймер отдыха", callback_data=f"timer_exercise_{exercise['name']}"),
            InlineKeyboardButton("📝 Добавить заметку", callback_data=f"add_note_{exercise['id']}")
        ])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_exercises_{week}_{day}")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=exercise_text, parse_mode='Markdown', reply_markup=reply_markup)


    async def progress_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать прогресс пользователя"""
        user = update.effective_user
        user_data = self.db.get_user(user.id)
        if not user_data:
            await update.message.reply_text("Сначала запустите /start")
            return

        progress_text = (
            f"📊 *Ваш прогресс*\n\n"
            f"👤 *Пользователь:* {user_data[2] or 'Аноним'}\n"
            f"📅 *Текущая неделя:* {user_data[3]}\n"
            f"📅 *Текущий день:* {user_data[4]}\n"
            f"🏋️‍♂️ *Всего тренировок:* {user_data[5]}\n"
            f"✅ *Завершено упражнений:* {user_data[6]}"
        )
        keyboard = [
            [InlineKeyboardButton("📋 Продолжить тренировку", callback_data="current_training")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(progress_text, parse_mode='Markdown', reply_markup=reply_markup)


    async def timer_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать таймер"""
        timer_text = "⏱️ *Таймер отдыха между подходами*\n\nВыберите время отдыха:"
        keyboard = [
            [InlineKeyboardButton("1:00", callback_data="timer_60"), InlineKeyboardButton("1:30", callback_data="timer_90")],
            [InlineKeyboardButton("2:00", callback_data="timer_120"), InlineKeyboardButton("2:30", callback_data="timer_150")],
            [InlineKeyboardButton("3:00", callback_data="timer_180"), InlineKeyboardButton("5:00", callback_data="timer_300")],
            [InlineKeyboardButton("⏱️ Отдохнуть после упражнения", callback_data="timer_after_exercise")], # Кнопка для вызова из упражнения
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(timer_text, parse_mode='Markdown', reply_markup=reply_markup)


    async def start_timer(self, chat_id: int, seconds: int, exercise_name: str = "Отдых"):
        """Запуск таймера для пользователя"""
        # Отменяем старый таймер если есть
        if chat_id in self.active_timers:
            self.active_timers[chat_id].cancel()

        # Сохраняем состояние таймера
        message = await self.application.bot.send_message(
            chat_id=chat_id,
            text=f"⏱️ *Таймер запущен:* {exercise_name}\n⏳ Время: {self.format_time(seconds)}",
            parse_mode='Markdown'
        )
        self.user_timers[chat_id] = {
            "seconds": seconds,
            "message_id": message.message_id,
            "exercise_name": exercise_name
        }

        # Запускаем асинхронный таймер
        timer_task = asyncio.create_task(self.run_timer(chat_id, seconds))
        self.active_timers[chat_id] = timer_task

    async def run_timer(self, chat_id: int, seconds: int):
        """Асинхронный таймер"""
        initial_seconds = seconds
        while seconds > 0:
            await asyncio.sleep(1)
            seconds -= 1
            if chat_id in self.user_timers and self.user_timers[chat_id]["seconds"] == initial_seconds: # Проверяем, не был ли таймер перезапущен
                try:
                    await self.application.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=self.user_timers[chat_id]["message_id"],
                        text=f"⏱️ *Таймер запущен:* {self.user_timers[chat_id]['exercise_name']}\n⏳ Осталось: {self.format_time(seconds)}",
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.warning(f"Ошибка обновления таймера: {e}")
                    break # Прерываем цикл, если сообщение удалено

        # Таймер закончился
        # Отправляем вибрацию/уведомление
        await self.application.bot.send_message(
            chat_id=chat_id,
            text=f"🔔 *Отдых завершен!* Время для следующего подхода! 💪\n(Было: {self.format_time(initial_seconds)})",
            parse_mode='Markdown'
        )

        # Логируем использование таймера
        if chat_id in self.user_timers:
            self.db.log_timer_usage(chat_id, self.user_timers[chat_id]['exercise_name'], initial_seconds)

        # Очищаем таймер
        if chat_id in self.user_timers:
            del self.user_timers[chat_id]
        if chat_id in self.active_timers:
            del self.active_timers[chat_id]

    def format_time(self, seconds: int) -> str:
        """Форматирование времени в MM:SS"""
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes:02d}:{seconds:02d}"


    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать статистику"""
        user = update.effective_user
        user_data = self.db.get_user(user.id)
        if not user_data:
            await update.message.reply_text("Сначала запустите /start")
            return

        # Получаем дополнительную статистику из базы
        cursor = self.db.conn.cursor()
        cursor.execute('''
            SELECT COUNT(DISTINCT date(completed_at)) as workout_days,
                   COUNT(*) as total_sets,
                   AVG(weight) as avg_weight
            FROM user_progress
            WHERE user_id = ? AND completed = 1
        ''', (user.id,))
        stats = cursor.fetchone()

        stats_text = (
            f"📈 *Ваша статистика*\n\n"
            f"👤 *Имя:* {user_data[2] or 'Аноним'}\n"
            f"📅 *Текущая неделя:* {user_data[3]}\n"
            f"📅 *Дата регистрации:* {user_data[7].split()[0] if user_data[7] else 'Неизвестно'}\n"
            f"🏋️‍♂️ *Всего тренировок:* {user_data[5]}\n"
            f"✅ *Завершено упражнений:* {user_data[6]}\n"
        )

        if stats and stats[0]:
            stats_text += (
                f"📊 *Дней тренировок:* {stats[0]}\n"
                f"🔢 *Всего подходов:* {stats[1]}\n"
            )
        if stats[2]:
            stats_text += f"⚖️ *Средний вес:* {stats[2]:.1f} кг\n"

        stats_text += "\n*Продолжайте в том же духе! 💪*"

        keyboard = [
            [InlineKeyboardButton("📋 Продолжить тренировку", callback_data="current_training")],
            [InlineKeyboardButton("📅 История тренировок", callback_data="workout_history")], # Пока без реализации
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(stats_text, parse_mode='Markdown', reply_markup=reply_markup)


    async def reset_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Предложить сброс прогресса"""
        keyboard = [
            [InlineKeyboardButton("✅ Да, сбросить всё", callback_data="reset_confirm")],
            [InlineKeyboardButton("❌ Нет, отмена", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("⚠️ *Вы уверены, что хотите сбросить весь прогресс?*", parse_mode='Markdown', reply_markup=reply_markup)


    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок (callback_query)"""
        query = update.callback_query
        await query.answer()
        data = query.data
        user_id = query.from_user.id
        chat_id = query.message.chat_id
        message_id = query.message.message_id

        # --- Обработка команд кнопок ---
        if data.startswith("week_"):
            week = int(data.split("_")[1])
            await self.show_day_selection(chat_id, week, message_id)
        elif data.startswith("day_"):
            parts = data.split("_")
            week, day = int(parts[1]), int(parts[2])
            await self.show_exercise_list(update, context, week, day)
        elif data.startswith("exercise_"):
            parts = data.split("_")
            week, day, ex_idx = int(parts[1]), int(parts[2]), int(parts[3])
            await self.show_exercise_detail(update, context, week, day, ex_idx)
        elif data.startswith("toggle_complete_"):
            parts = data.split("_")
            exercise_id, week, day = parts[2], int(parts[3]), int(parts[4])
            # Получаем текущий статус
            current_progress = self.db.get_user_progress(user_id, week, day)
            new_status = not current_progress.get(exercise_id, {}).get('completed', False)
            self.db.update_exercise_status(user_id, week, day, exercise_id, new_status)
            # После изменения статуса, возвращаемся к списку упражнений
            await self.show_exercise_list(update, context, week, day)
        elif data.startswith("set_weight_"):
            # Этот тип кнопки переводит бота в режим ожидания ввода веса
            parts = data.split("_")
            week, day, ex_idx, set_num = int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5])
            exercise = self.program.get_exercise(week, day, ex_idx)
            if exercise:
                context.user_data['waiting_for_weight'] = {'week': week, 'day': day, 'exercise_id': exercise['id']}
                await query.edit_message_text(
                    text=f"Введите вес для '{exercise['name']}' (Подход {set_num+1}):",
                    parse_mode='Markdown'
                )
            else:
                 await query.edit_message_text(text="Ошибка: упражнение не найдено.")
        elif data.startswith("timer_"):
            if data == "timer_after_exercise":
                 # Эта кнопка должна передавать название упражнения, но пока просто покажем выбор времени
                 # Можно модифицировать, чтобы передавать упражнение
                 await self.timer_command(update, context)
                 return
            seconds_str = data.split("_")[1]
            if seconds_str.isdigit():
                seconds = int(seconds_str)
                await self.start_timer(chat_id, seconds)
                await query.edit_message_text(text=f"⏱️ Таймер на {self.format_time(seconds)} запущен!", parse_mode='Markdown')
        elif data.startswith("timer_exercise_"):
            exercise_name = data[len("timer_exercise_"):]
            await self.start_timer(chat_id, 90, exercise_name) # Стандартное время 90 секунд
        elif data == "program_main":
            await self.show_week_selection(chat_id, message_id)
        elif data.startswith("back_to_"):
            if data == "back_to_weeks":
                await self.show_week_selection(chat_id, message_id)
            elif data.startswith("back_to_days_"):
                week = int(data.split("_")[3])
                await self.show_day_selection(chat_id, week, message_id)
            elif data.startswith("back_to_exercises_"):
                parts = data.split("_")
                week, day = int(parts[3]), int(parts[4])
                await self.show_exercise_list(update, context, week, day)
        elif data == "main_menu":
            await self.show_main_menu(chat_id, message_id)
        elif data == "current_training":
            user_data_db = self.db.get_user(user_id)
            if user_data_db:
                week, day = user_data_db[3], user_data_db[4] # current_week, current_day
                await self.show_exercise_list(update, context, week, day)
            else:
                 await self.show_week_selection(chat_id, message_id) # Если нет данных, вернем к выбору недели
        elif data == "help_main":
            await self.help_command(update, context)
        elif data == "stats_main":
            await self.stats_command(update, context)
        elif data == "reset_confirm":
            # Сброс прогресса пользователя
            cursor = self.db.conn.cursor()
            cursor.execute('DELETE FROM user_progress WHERE user_id = ?', (user_id,))
            cursor.execute('UPDATE users SET total_exercises = 0, total_workouts = 0 WHERE id = ?', (user_id,))
            self.db.conn.commit()
            await query.edit_message_text(
                text="✅ *Прогресс сброшен!\nНачните новую тренировку с /program*",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(text="Неизвестная команда кнопки.")


    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстовых сообщений"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        text = update.message.text

        # Проверяем, ожидаем ли мы ввод веса
        if context.user_data.get('waiting_for_weight'):
            weight_info = context.user_data['waiting_for_weight']
            try:
                weight = float(text.replace(',', '.')) # Заменяем запятую на точку для парсинга
                self.db.save_exercise_weight(user_id, weight_info['week'], weight_info['day'], weight_info['exercise_id'], weight)

                # Убираем флаг ожидания
                del context.user_data['waiting_for_weight']

                # Возвращаемся к деталям упражнения
                week, day = weight_info['week'], weight_info['day']
                ex_index = int(weight_info['exercise_id'].split('-')[2]) - 1 # Предполагаем формат ID 1-1-1
                await self.show_exercise_detail(update, context, week, day, ex_index)

            except ValueError:
                await update.message.reply_text("❌ Пожалуйста, введите корректное число для веса (например, 60.5).")
        else:
            # Обработка обычных текстовых сообщений
            text_lower = text.lower()
            if text_lower in ["программа", "тренировка"]:
                await self.program_command(update, context)
            elif text_lower in ["прогресс", "статистика"]:
                await self.progress_command(update, context)
            else:
                await update.message.reply_text("Используйте команды или кнопки для навигации. /help - список команд")


    async def show_main_menu(self, chat_id: int, message_id: int):
        """Показать главное меню"""
        keyboard = [
            [InlineKeyboardButton("📋 Программа тренировок", callback_data="program_main")],
            [InlineKeyboardButton("⏱️ Таймер отдыха", callback_data="timer_main")],
            [InlineKeyboardButton("📊 Моя статистика", callback_data="stats_main")],
            [InlineKeyboardButton("🆘 Помощь", callback_data="help_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = "🏋️‍♂️ *Главное меню*\n\nВыберите действие:"
        if message_id:
            await self.application.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text, parse_mode='Markdown', reply_markup=reply_markup
            )
        else:
            await self.application.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', reply_markup=reply_markup)


    def run(self):
        """Запустить бота"""
        logger.info("Бот запущен...")
        # Используем polling для Render
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)


# ==================== ЗАПУСК БОТА ====================
import signal
import sys

async def amain():
    """Асинхронная основная функция запуска для Render."""
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    if not TOKEN or TOKEN == 'YOUR_BOT_TOKEN_HERE':
        logger.error("❌ Пожалуйста, установите токен бота в переменной окружения TELEGRAM_BOT_TOKEN")
        sys.exit(1) # Завершаем скрипт с кодом ошибки

    # Создаем бота
    bot = TrainingBot(TOKEN)

    try:
        # Инициализируем приложение
        await bot.application.initialize()
        # Запускаем polling
        await bot.application.start()
        logger.info("✅ Бот запущен и работает (polling).")

        # Ожидаем сигнал остановки (например, SIGTERM от Render)
        stop_event = asyncio.Event()
        def signal_handler():
            logger.info("Получен сигнал остановки. Завершаем работу...")
            stop_event.set()

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler) # Для Ctrl+C локально

        await stop_event.wait() # Ждем сигнала остановки

    finally:
        logger.info("Останавливаем бота...")
        await bot.application.stop()
        await bot.application.shutdown()
        logger.info("Бот остановлен.")


def main():
    """Синхронная точка входа для Render."""
    # Используем asyncio.run для запуска асинхронной main функции
    asyncio.run(amain())


if __name__ == '__main__':
    main()
