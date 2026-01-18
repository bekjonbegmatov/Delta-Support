#!/usr/bin/env python3
"""
DELTA-Support - AI-powered support bot for VPN projects
Main entry point
"""

import asyncio
import logging
from loguru import logger
from dotenv import load_dotenv
import os
import uvicorn
from tortoise import Tortoise

from modules.bot import SupportBot
from modules.database import Database, AdminUser
from modules.config import Config
from web.app import app  # Import FastAPI app
from web.utils import get_password_hash

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
        
        # Инициализация базы данных (объект-обертка)
        db = Database(config)
        
        # Инициализация бота
        bot = SupportBot(config, db)
        await bot.initialize() # Initialize handlers
        
        # Inject dependencies into FastAPI app
        app.state.bot = bot
        app.state.db = db
        app.state.config = config
        bot.ws_manager = getattr(app.state, "ws_manager", None)
        
        # Setup startup/shutdown events
        @app.on_event("startup")
        async def startup_event():
            logger.info("Startup: Initializing Database...")
            await db.initialize()
            try:
                await bot.refresh_runtime_settings(force=True)
            except Exception as e:
                logger.warning(f"Runtime settings refresh failed: {e}")
            try:
                admin = await AdminUser.get_or_none(username="admin")
                if not admin:
                    hashed = get_password_hash("admin123")
                    await AdminUser.create(username="admin", password_hash=hashed, role="admin", is_active=True)
                    logger.info("Seeded default admin: admin/admin123")
                else:
                    if admin.role == "admin" and not admin.is_active:
                        admin.is_active = True
                        await admin.save()
            except Exception as e:
                logger.warning(f"Admin seed failed: {e}")
            try:
                await AdminUser.filter(role="admin").update(is_active=True)
            except Exception:
                pass
            logger.info("Startup: Starting Bot Polling...")
            await bot.start_polling()
            
        @app.on_event("shutdown")
        async def shutdown_event():
            logger.info("Shutdown: Stopping Bot...")
            await bot.stop()
            logger.info("Shutdown: Closing Database...")
            await Tortoise.close_connections()
            
        # Запуск сервера
        logger.info(f"Starting Web Admin on port {config.app_port}...")
        
        # Use uvicorn config
        uvicorn_config = uvicorn.Config(
            app, 
            host="0.0.0.0", 
            port=int(config.app_port), 
            log_level=log_level.lower()
        )
        server = uvicorn.Server(uvicorn_config)
        await server.serve()
        
    except asyncio.CancelledError:
        logger.info("Cancelled")
    except KeyboardInterrupt:
        logger.info("Stopped")
    except Exception as e:
        logger.error(f"Error starting app: {e}")
        return
    finally:
        logger.info("Shutting down...")

if __name__ == "__main__":
    asyncio.run(main())
