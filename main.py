#!/usr/bin/env python3
"""
STELS-Support - AI-powered support bot for VPN projects
Main entry point
"""

import asyncio
import logging
from loguru import logger
from dotenv import load_dotenv
import os

from modules.bot import SupportBot
from modules.database import Database
from modules.config import Config

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
log_level = os.getenv("LOG_LEVEL", "INFO")
logger.add(
    "logs/app.log",
    rotation="10 MB",
    retention="7 days",
    level=log_level,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}"
)


async def main():
    """Главная функция запуска приложения"""
    try:
        # Инициализация конфигурации
        config = Config()
        
        # Проверка обязательных параметров
        if not config.telegram_bot_token:
            logger.error("TELEGRAM_BOT_TOKEN не установлен в .env файле!")
            raise ValueError("TELEGRAM_BOT_TOKEN обязателен для работы бота")
        
        # Инициализация базы данных
        db = Database(config)
        await db.initialize()
        logger.info("Database initialized")
        
        # Инициализация бота
        bot = SupportBot(config, db)
        await bot.initialize()
        logger.info("Bot initialized")
        
        # Запуск бота
        logger.info("Starting STELS-Support bot...")
        
        # Запускаем бота и держим его работающим
        try:
            await bot.start()
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.error(f"Error in bot: {e}")
            raise
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        raise
    finally:
        logger.info("Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
