"""
Модуль Telegram бота поддержки
"""

import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from loguru import logger
from typing import Optional

from modules.config import Config
from modules.database import Database
from modules.ai_support import AISupport


class SupportBot:
    """Класс бота поддержки"""
    
    def __init__(self, config: Config, database: Database):
        self.config = config
        self.db = database
        self.ai = AISupport(config)
        self.application = None
    
    async def initialize(self):
        """Инициализация бота"""
        self.application = Application.builder().token(self.config.telegram_bot_token).build()
        
        # Регистрация обработчиков
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("chats", self.chats_command))
        self.application.add_handler(CommandHandler("close", self.close_chat_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        logger.info("Bot handlers registered")
    
    async def start(self):
        """Запуск бота"""
        logger.info("Bot started polling")
        
        # Инициализируем и запускаем polling вручную
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(drop_pending_updates=True)
        
        # Держим бота работающим
        try:
            # Создаем событие, которое никогда не произойдет
            stop_event = asyncio.Event()
            await stop_event.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Stopping bot...")
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user = update.effective_user
        user_id = user.id
        
        # Проверяем, является ли пользователь админом или менеджером
        if user_id in self.config.get_all_staff_ids():
            keyboard = [
                [InlineKeyboardButton("📋 Все чаты", callback_data="admin_chats")],
                [InlineKeyboardButton("🟡 Ожидают менеджера", callback_data="admin_waiting")],
                [InlineKeyboardButton("🟢 Активные чаты", callback_data="admin_active")],
                [InlineKeyboardButton("❓ Помощь", callback_data="admin_help")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "👋 Добро пожаловать в панель управления поддержкой!\n\n"
                "Выберите действие:",
                reply_markup=reply_markup
            )
            return
        
        # Обычный пользователь
        # Проверяем, есть ли активный чат
        chat = await self.db.get_chat_by_user_id(user_id)
        
        if not chat:
            # Создаем новый чат
            chat = await self.db.create_chat(
                user_id=user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name
            )
            logger.info(f"Created new chat for user {user_id}")
        
        welcome_message = (
            f"👋 Здравствуйте, {user.first_name or 'пользователь'}!\n\n"
            f"Я бот поддержки проекта {self.config.project_name or 'STELS-Support'}.\n"
            "Я помогу вам с вопросами и проблемами.\n\n"
            "Просто напишите ваш вопрос, и я постараюсь помочь!"
        )
        
        # Если есть описание проекта, добавляем его
        if self.config.project_description:
            welcome_message += f"\n\n{self.config.project_description}"
        
        await update.message.reply_text(welcome_message)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /help"""
        user_id = update.effective_user.id
        
        # Для админов/менеджеров
        if user_id in self.config.get_all_staff_ids():
            help_text = (
                "📋 Команды для администраторов и менеджеров:\n\n"
                "/chats - Просмотр всех чатов\n"
                "/close <chat_id> - Закрыть чат\n"
                "/help - Эта справка"
            )
        else:
            help_text = (
                "📋 Как я могу помочь:\n\n"
                "Просто напишите ваш вопрос, и я постараюсь на него ответить.\n"
                "Если я не смогу решить ваш вопрос, я предложу пригласить менеджера в чат.\n\n"
                "Доступные команды:\n"
                "/start - Начать чат\n"
                "/help - Эта справка"
            )
        
        await update.message.reply_text(help_text)
    
    async def chats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /chats (для админов/менеджеров)"""
        user_id = update.effective_user.id
        
        if user_id not in self.config.get_all_staff_ids():
            await update.message.reply_text("❌ У вас нет доступа к этой команде.")
            return
        
        chats = await self.db.get_all_chats()
        
        if not chats:
            await update.message.reply_text("📭 Нет активных чатов.")
            return
        
        message_text = "📋 Список чатов:\n\n"
        keyboard_buttons = []
        
        for chat in chats[:10]:  # Показываем первые 10
            status_emoji = {
                "active": "🟢",
                "waiting_manager": "🟡",
                "closed": "🔴"
            }.get(chat.status, "⚪")
            
            user_info = f"@{chat.username}" if chat.username else f"ID: {chat.user_id}"
            message_text += (
                f"{status_emoji} Чат #{chat.id} - {user_info}\n"
                f"   Статус: {chat.status}\n"
                f"   Создан: {chat.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
            )
            
            keyboard_buttons.append([
                InlineKeyboardButton(
                    f"Чат #{chat.id} - {user_info}",
                    callback_data=f"view_chat_{chat.id}"
                )
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard_buttons) if keyboard_buttons else None
        
        await update.message.reply_text(message_text, reply_markup=reply_markup)
    
    async def close_chat_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /close"""
        user_id = update.effective_user.id
        
        if user_id not in self.config.get_all_staff_ids():
            await update.message.reply_text("❌ У вас нет доступа к этой команде.")
            return
        
        if not context.args:
            await update.message.reply_text("❌ Использование: /close <chat_id>")
            return
        
        try:
            chat_id = int(context.args[0])
            await self.close_chat_from_message(update.message, chat_id, user_id)
        except ValueError:
            await update.message.reply_text("❌ Неверный формат chat_id.")
    
    async def close_chat_from_button(self, query, chat_id: int, user_id: int):
        """Закрыть чат из кнопки"""
        try:
            await self._close_chat(chat_id, user_id)
            keyboard = [[InlineKeyboardButton("◀️ Назад к списку", callback_data="admin_chats")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                f"✅ Чат #{chat_id} закрыт.",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error closing chat from button: {e}")
            await query.edit_message_text(f"❌ Ошибка при закрытии чата: {str(e)}")
    
    async def close_chat_from_message(self, message, chat_id: int, user_id: int):
        """Закрыть чат из сообщения"""
        try:
            await self._close_chat(chat_id, user_id)
            await message.reply_text(f"✅ Чат #{chat_id} закрыт.")
        except Exception as e:
            logger.error(f"Error closing chat from message: {e}")
            await message.reply_text(f"❌ Ошибка при закрытии чата: {str(e)}")
    
    async def _close_chat(self, chat_id: int, user_id: int):
        """Внутренняя функция закрытия чата"""
        chat = await self.db.get_chat_by_id(chat_id)
        
        if not chat:
            raise ValueError(f"Чат #{chat_id} не найден.")
        
        await self.db.update_chat_status(chat_id, "closed")
        
        # Уведомляем пользователя
        try:
            await self.application.bot.send_message(
                chat_id=chat.user_id,
                text="💬 Ваш чат с поддержкой был закрыт. Если у вас есть еще вопросы, напишите /start"
            )
        except Exception as e:
            logger.error(f"Error notifying user about closed chat: {e}")
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data
        
        # request_manager доступен всем пользователям
        if data.startswith("request_manager_"):
            chat_id = int(data.replace("request_manager_", ""))
            # Проверяем, что пользователь является владельцем чата
            chat = await self.db.get_chat_by_id(chat_id)
            if chat and chat.user_id == user_id:
                await self.request_manager(query, chat_id)
            else:
                await query.edit_message_text("❌ Вы не можете запросить менеджера для этого чата.")
            return
        
        # Остальные функции только для админов/менеджеров
        if user_id not in self.config.get_all_staff_ids():
            await query.edit_message_text("❌ У вас нет доступа к этой функции.")
            return
        
        if data == "admin_chats":
            await self.show_all_chats(query, user_id)
        elif data == "admin_waiting":
            await self.show_chats_by_status(query, user_id, "waiting_manager")
        elif data == "admin_active":
            await self.show_chats_by_status(query, user_id, "active")
        elif data == "admin_help":
            await self.show_admin_help(query)
        elif data == "admin_back":
            await self.show_admin_menu(query)
        elif data.startswith("view_chat_"):
            chat_id = int(data.replace("view_chat_", ""))
            await self.show_chat_details(query, chat_id)
        elif data.startswith("join_chat_"):
            chat_id = int(data.replace("join_chat_", ""))
            await self.join_chat(query, chat_id, user_id)
        elif data.startswith("close_chat_"):
            chat_id = int(data.replace("close_chat_", ""))
            await self.close_chat_from_button(query, chat_id, user_id)
    
    async def show_chat_details(self, query, chat_id: int):
        """Показать детали чата"""
        chat = await self.db.get_chat_by_id(chat_id)
        
        if not chat:
            await query.edit_message_text("❌ Чат не найден.")
            return
        
        messages = await self.db.get_chat_messages(chat_id, limit=20)
        
        message_text = (
            f"💬 Чат #{chat_id}\n\n"
            f"Пользователь: {chat.first_name or ''} {chat.last_name or ''}\n"
            f"Username: @{chat.username or 'N/A'}\n"
            f"User ID: {chat.user_id}\n"
            f"Статус: {chat.status}\n"
            f"Создан: {chat.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"Сообщения ({len(messages)}):\n\n"
        )
        
        for msg in messages[-10:]:  # Последние 10 сообщений
            role_emoji = {
                "user": "👤",
                "ai": "🤖",
                "manager": "👨‍💼"
            }.get(msg.message_type, "❓")
            
            message_text += f"{role_emoji} {msg.content[:100]}\n\n"
        
        # Кнопки управления чатом
        keyboard_buttons = []
        
        if chat.status != "closed":
            if chat.status != "waiting_manager" or chat.manager_id is None:
                keyboard_buttons.append([
                    InlineKeyboardButton("👨‍💼 Присоединиться к чату", callback_data=f"join_chat_{chat_id}")
                ])
            else:
                keyboard_buttons.append([
                    InlineKeyboardButton("💬 Открыть чат", callback_data=f"join_chat_{chat_id}")
                ])
        
        if chat.status != "closed":
            keyboard_buttons.append([
                InlineKeyboardButton("🔴 Закрыть чат", callback_data=f"close_chat_{chat_id}")
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton("◀️ Назад к списку", callback_data="admin_chats")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard_buttons)
        
        await query.edit_message_text(message_text, reply_markup=reply_markup)
    
    async def join_chat(self, query, chat_id: int, manager_id: int):
        """Присоединиться к чату"""
        chat = await self.db.get_chat_by_id(chat_id)
        
        if not chat:
            await query.edit_message_text("❌ Чат не найден.")
            return
        
        await self.db.update_chat_status(chat_id, "waiting_manager", manager_id)
        
        # Уведомляем пользователя
        try:
            await self.application.bot.send_message(
                chat_id=chat.user_id,
                text="👨‍💼 Менеджер подключился к вашему чату. Теперь вы можете общаться напрямую!"
            )
        except Exception as e:
            logger.error(f"Error notifying user about manager join: {e}")
        
        await query.edit_message_text(f"✅ Вы подключились к чату #{chat_id}.")
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик обычных сообщений"""
        user = update.effective_user
        user_id = user.id
        message_text = update.message.text
        
        # Проверяем, является ли пользователь админом/менеджером
        if user_id in self.config.get_all_staff_ids():
            # Проверяем, подключен ли менеджер к какому-то чату
            chats = await self.db.get_all_chats(status="waiting_manager")
            manager_chat = None
            
            for chat in chats:
                if chat.manager_id == user_id:
                    manager_chat = chat
                    break
            
            if manager_chat:
                # Менеджер отвечает в чате - пересылаем сообщение пользователю
                try:
                    # Сохраняем сообщение менеджера
                    await self.db.add_message(manager_chat.id, user_id, message_text, "manager")
                    
                    # Отправляем сообщение пользователю
                    await self.application.bot.send_message(
                        chat_id=manager_chat.user_id,
                        text=f"👨‍💼 Менеджер: {message_text}"
                    )
                    
                    await update.message.reply_text(
                        f"✅ Сообщение отправлено пользователю (Чат #{manager_chat.id})"
                    )
                except Exception as e:
                    logger.error(f"Error sending message from manager to user: {e}")
                    await update.message.reply_text(
                        f"❌ Ошибка при отправке сообщения: {str(e)}"
                    )
            else:
                # Менеджер не подключен к чату
                await update.message.reply_text(
                    "💬 Используйте команду /chats для просмотра и управления чатами.\n"
                    "Чтобы ответить пользователю, сначала подключитесь к чату через /chats"
                )
            return
        
        # Обычный пользователь
        chat = await self.db.get_chat_by_user_id(user_id)
        
        if not chat:
            chat = await self.db.create_chat(
                user_id=user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name
            )
        
        # Сохраняем сообщение пользователя
        await self.db.add_message(chat.id, user_id, message_text, "user")
        
        # Если чат уже с менеджером, пересылаем сообщение менеджеру
        if chat.status == "waiting_manager" and chat.manager_id:
            try:
                # Пересылаем сообщение менеджеру
                user_info = f"@{user.username}" if user.username else f"{user.first_name or 'Пользователь'}"
                await self.application.bot.send_message(
                    chat_id=chat.manager_id,
                    text=f"💬 {user_info} (Чат #{chat.id}):\n{message_text}"
                )
                await update.message.reply_text(
                    "✅ Ваше сообщение отправлено менеджеру. Ожидайте ответа."
                )
            except Exception as e:
                logger.error(f"Error forwarding message to manager: {e}")
                await update.message.reply_text(
                    "✅ Ваше сообщение сохранено. Менеджер скоро ответит."
                )
            return
        
        # Получаем историю чата
        chat_messages = await self.db.get_chat_messages(chat.id, limit=20)
        chat_history = [
            {
                "role": "user" if msg.message_type == "user" else "assistant",
                "message": msg.content
            }
            for msg in chat_messages
        ]
        
        # Получаем ответ от AI
        context = {
            "user_id": user_id,
            "username": user.username,
            "first_name": user.first_name
        }
        
        ai_response = await self.ai.get_ai_answer(message_text, context, chat_history)
        
        if ai_response:
            # Сохраняем ответ AI
            await self.db.add_message(chat.id, user_id, ai_response, "ai")
            
            # Проверяем, нужно ли предложить менеджера
            if any(keyword in ai_response.lower() for keyword in ["менеджер", "пригласить", "подключить"]):
                keyboard = [
                    [InlineKeyboardButton("Да, пригласите менеджера", callback_data=f"request_manager_{chat.id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(ai_response, reply_markup=reply_markup)
            else:
                await update.message.reply_text(ai_response)
        else:
            # Если AI не ответил, предлагаем менеджера
            keyboard = [
                [InlineKeyboardButton("Пригласить менеджера", callback_data=f"request_manager_{chat.id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "Извините, я не смог обработать ваш вопрос. Хотите пригласить менеджера в чат?",
                reply_markup=reply_markup
            )
    
    async def request_manager(self, query, chat_id: int):
        """Запросить менеджера для чата"""
        chat = await self.db.get_chat_by_id(chat_id)
        
        if not chat:
            try:
                await query.edit_message_text("❌ Чат не найден.")
            except:
                pass
            return
        
        # Обновляем статус чата
        try:
            await self.db.update_chat_status(chat_id, "waiting_manager")
        except Exception as e:
            logger.error(f"Error updating chat status: {e}")
            try:
                await query.edit_message_text("❌ Ошибка при обновлении статуса чата.")
            except:
                pass
            return
        
        # Отправляем уведомления всем менеджерам и админам
        staff_ids = self.config.get_all_staff_ids()
        
        if not staff_ids:
            logger.warning("No staff IDs configured! Check TELEGRAM_ADMIN_IDS and TELEGRAM_MANAGER_IDS in .env")
            try:
                await query.edit_message_text(
                    "✅ Запрос отправлен, но администраторы не настроены. Обратитесь в поддержку другим способом."
                )
            except:
                pass
            return
        
        notification_sent = False
        for staff_id in staff_ids:
            try:
                # Создаем уведомление
                await self.db.create_manager_notification(chat_id, staff_id)
                
                # Отправляем уведомление
                keyboard = [
                    [InlineKeyboardButton("Просмотреть чат", callback_data=f"view_chat_{chat_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                user_info = f"@{chat.username}" if chat.username else f"ID: {chat.user_id}"
                notification_text = (
                    f"🔔 Новый запрос на поддержку!\n\n"
                    f"Чат #{chat_id}\n"
                    f"Пользователь: {user_info}\n"
                    f"Имя: {chat.first_name or 'N/A'}"
                )
                
                await self.application.bot.send_message(
                    chat_id=staff_id,
                    text=notification_text,
                    reply_markup=reply_markup
                )
                notification_sent = True
                logger.info(f"Notification sent to staff {staff_id} for chat {chat_id}")
            except Exception as e:
                logger.error(f"Error sending notification to staff {staff_id}: {e}")
        
        # Уведомляем пользователя
        try:
            if notification_sent:
                await query.edit_message_text(
                    "✅ Запрос на подключение менеджера отправлен. Ожидайте, менеджер скоро подключится к чату."
                )
            else:
                await query.edit_message_text(
                    "⚠️ Запрос отправлен, но не удалось уведомить менеджеров. Попробуйте позже."
                )
        except Exception as e:
            logger.error(f"Error editing message: {e}")
