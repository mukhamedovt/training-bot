import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text(
        '🏋️‍♂️ Привет! Я бот для тренировок!\n\n'
        'Используй /help для списка команд'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    await update.message.reply_text(
        '📋 Доступные команды:\n'
        '/start - Начало работы\n'
        '/help - Эта справка\n'
        '/program - Программа тренировок'
    )

async def program(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /program"""
    await update.message.reply_text(
        '📅 Программа тренировок:\n\n'
        '1. Жим штанги лежа\n'
        '2. Подтягивания\n'
        '3. Приседания\n\n'
        'Больше функций скоро!'
    )

def main():
    """Запуск бота"""
    # Получаем токен из переменных окружения
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        logger.error("❌ Токен не найден! Установи переменную TELEGRAM_BOT_TOKEN")
        return
    
    # Создаем приложение
    application = Application.builder().token(TOKEN).build()
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("program", program))
    
    # Запускаем бота
    logger.info("✅ Бот запускается...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()