"""
Модуль Telegram бота поддержки
"""

import asyncio
import json
import redis
import html
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
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
from modules.database import Database, SystemConfig
from modules.ai_support import AISupport


class SupportBot:
    """Класс бота поддержки"""
    
    def __init__(self, config: Config, database: Database):
        self.config = config
        self.db = database
        self.ai = AISupport(config)
        self.application = None
        self.redis = None
        self.ws_manager = None
        self._group_id = None
        self._group_mode_enabled = bool(config.telegram_group_mode)
        self._topic_title_template = "{emoji} {first_name} ({user_id}) {status_label}"
        self._emoji_by_role = {"default": "🟢", "client": "🔴", "manager": "🟡", "ai": "🤖"}
        self._emoji_by_status = {"active": "🟢", "waiting_manager": "🟡", "closed": "🔴"}
        self._project_name = config.project_name or "DELTA-Support"
        self._project_description = config.project_description or ""
        self._project_website = config.project_website or ""
        self._project_bot_link = config.project_bot_link or ""
        self._project_owner_contacts = config.project_owner_contacts or ""
        self._welcome_template = (
            "👋 Здравствуйте, {first_name}!\n\n"
            "Я бот поддержки проекта {project_name}.\n"
            "Я помогу вам с вопросами и проблемами.\n\n"
            "Просто напишите ваш вопрос, и я постараюсь помочь!\n\n"
            "{project_description}"
        )
        self._runtime_settings_ts = 0.0
        try:
            from chatgpt_md_converter import telegram_format as _md_to_html
            self._md_to_html = _md_to_html
        except Exception:
            self._md_to_html = lambda t: html.escape(t or "")

    async def refresh_runtime_settings(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self._runtime_settings_ts < 3.0:
            return
        keys = [
            "telegram_group_mode",
            "telegram_support_group_id",
            "telegram_topic_title_template",
            "telegram_emoji_default",
            "telegram_emoji_client",
            "telegram_emoji_manager",
            "telegram_emoji_ai",
            "telegram_status_emoji_active",
            "telegram_status_emoji_waiting_manager",
            "telegram_status_emoji_closed",
            "project_name",
            "project_description",
            "project_website",
            "project_bot_link",
            "project_owner_contacts",
            "bot_welcome_message",
        ]
        rows = await SystemConfig.filter(key__in=keys).all()
        values = {r.key: (r.value or "") for r in rows}

        if "telegram_group_mode" in values:
            self._group_mode_enabled = str(values.get("telegram_group_mode") or "").lower() in ["1", "true", "yes", "y", "on"]
        else:
            self._group_mode_enabled = bool(self.config.telegram_group_mode)

        if "telegram_support_group_id" in values:
            group_id_raw = (values.get("telegram_support_group_id") or "").strip()
            if group_id_raw and group_id_raw.lstrip("-").isdigit():
                self._group_id = int(group_id_raw)
            else:
                self._group_id = None
        else:
            self._group_id = self.config.telegram_support_group_id if self._group_mode_enabled else None

        tpl = (values.get("telegram_topic_title_template") or "").strip()
        if tpl:
            self._topic_title_template = tpl

        self._emoji_by_role["default"] = (values.get("telegram_emoji_default") or self._emoji_by_role["default"]).strip() or self._emoji_by_role["default"]
        self._emoji_by_role["client"] = (values.get("telegram_emoji_client") or self._emoji_by_role["client"]).strip() or self._emoji_by_role["client"]
        self._emoji_by_role["manager"] = (values.get("telegram_emoji_manager") or self._emoji_by_role["manager"]).strip() or self._emoji_by_role["manager"]
        self._emoji_by_role["ai"] = (values.get("telegram_emoji_ai") or self._emoji_by_role["ai"]).strip() or self._emoji_by_role["ai"]

        self._emoji_by_status["active"] = (values.get("telegram_status_emoji_active") or self._emoji_by_status["active"]).strip() or self._emoji_by_status["active"]
        self._emoji_by_status["waiting_manager"] = (values.get("telegram_status_emoji_waiting_manager") or self._emoji_by_status["waiting_manager"]).strip() or self._emoji_by_status["waiting_manager"]
        self._emoji_by_status["closed"] = (values.get("telegram_status_emoji_closed") or self._emoji_by_status["closed"]).strip() or self._emoji_by_status["closed"]

        self._project_name = (values.get("project_name") or self.config.project_name or self._project_name).strip() or self._project_name
        self._project_description = (values.get("project_description") or self.config.project_description or "").strip()
        self._project_website = (values.get("project_website") or self.config.project_website or "").strip()
        self._project_bot_link = (values.get("project_bot_link") or self.config.project_bot_link or "").strip()
        self._project_owner_contacts = (values.get("project_owner_contacts") or self.config.project_owner_contacts or "").strip()

        welcome = values.get("bot_welcome_message")
        if welcome and welcome.strip():
            self._welcome_template = welcome

        if not self._group_mode_enabled:
            self._group_id = None

        self._runtime_settings_ts = now
    
    async def initialize(self):
        """Инициализация бота"""
        self.application = Application.builder().token(self.config.telegram_bot_token).build()
        try:
            self.redis = redis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password,
                decode_responses=True,
                socket_keepalive=True,
            )
            self.redis.ping()
            logger.info("Redis connected")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}")
        if self.config.telegram_group_mode and self.config.telegram_support_group_id:
            self._group_id = self.config.telegram_support_group_id
            logger.info(f"Group mode enabled. Support group: {self._group_id}")
        
        # Регистрация обработчиков
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("chats", self.chats_command))
        self.application.add_handler(CommandHandler("close", self.close_chat_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        self.application.add_handler(MessageHandler(filters.StatusUpdate.ALL, self.handle_service_update))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_any_message))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_any_message))
        self.application.add_handler(MessageHandler(filters.VIDEO, self.handle_any_message))
        self.application.add_handler(MessageHandler(filters.AUDIO, self.handle_any_message))
        self.application.add_handler(MessageHandler(filters.VOICE, self.handle_any_message))
        self.application.add_handler(MessageHandler(filters.Document.ALL, self.handle_any_message))
        self.application.add_handler(MessageHandler(filters.VIDEO_NOTE, self.handle_any_message))
        self.application.add_handler(MessageHandler(filters.Sticker.ALL, self.handle_any_message))
        self.application.add_handler(MessageHandler(filters.ANIMATION, self.handle_any_message))
        
        logger.info("Bot handlers registered")
    
    async def start_polling(self):
        """Запуск поллинга без блокировки"""
        logger.info("Bot starting polling...")
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(drop_pending_updates=True)
    
    async def stop(self):
        """Остановка бота"""
        logger.info("Stopping bot...")
        if self.application.updater:
            await self.application.updater.stop()
        if self.application:
            await self.application.stop()
            await self.application.shutdown()

    async def start(self):
        """Запуск бота (блокирующий)"""
        logger.info("Bot started polling")
        
        await self.start_polling()
        
        # Держим бота работающим
        try:
            # Создаем событие, которое никогда не произойдет
            stop_event = asyncio.Event()
            await stop_event.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            await self.stop()
    
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

        await self.refresh_runtime_settings()

        class _SafeDict(dict):
            def __missing__(self, key):
                return "{" + key + "}"

        ctx = {
            "first_name": user.first_name or "пользователь",
            "last_name": user.last_name or "",
            "username": user.username or "",
            "user_id": user_id,
            "project_name": self._project_name,
            "project_description": self._project_description,
            "project_website": self._project_website,
            "project_bot_link": self._project_bot_link,
            "project_owner_contacts": self._project_owner_contacts,
        }
        try:
            welcome_message = (self._welcome_template or "").format_map(_SafeDict(ctx)).strip()
        except Exception:
            welcome_message = ""
        if not welcome_message:
            welcome_message = f"👋 Здравствуйте, {ctx['first_name']}!\n\nЯ бот поддержки проекта {self._project_name}."
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
    
    async def show_admin_menu(self, query):
        """Показать главное меню админа"""
        keyboard = [
            [InlineKeyboardButton("📋 Все чаты", callback_data="admin_chats")],
            [InlineKeyboardButton("🟡 Ожидают менеджера", callback_data="admin_waiting")],
            [InlineKeyboardButton("🟢 Активные чаты", callback_data="admin_active")],
            [InlineKeyboardButton("❓ Помощь", callback_data="admin_help")],
            # Постоянные кнопки внизу
            [
                InlineKeyboardButton("🔄 Обновить", callback_data="admin_back"),
                InlineKeyboardButton("📋 Все чаты", callback_data="admin_chats")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(
                "👋 Панель управления поддержкой\n\nВыберите действие:",
                reply_markup=reply_markup
            )
        except BadRequest as e:
            if "not modified" in str(e).lower():
                pass  # Сообщение не изменилось
            else:
                raise
    
    async def show_all_chats(self, query, user_id: int):
        """Показать все чаты через callback"""
        chats = await self.db.get_all_chats()
        await self._display_chats_list(query, chats, "Все чаты")
    
    async def show_chats_by_status(self, query, user_id: int, status: str):
        """Показать чаты по статусу"""
        chats = await self.db.get_all_chats(status=status)
        status_name = {
            "active": "Активные",
            "waiting_manager": "Ожидают менеджера",
            "closed": "Закрытые"
        }.get(status, status)
        await self._display_chats_list(query, chats, f"Чаты: {status_name}")
    
    async def _display_chats_list(self, query, chats, title: str):
        """Отобразить список чатов"""
        if not chats:
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            try:
                await query.edit_message_text(
                    f"📭 Нет чатов для отображения.",
                    reply_markup=reply_markup
                )
            except BadRequest:
                pass  # Сообщение не изменилось
            return
        
        message_text = f"📋 {title} ({len(chats)}):\n\n"
        keyboard_buttons = []
        
        for chat in chats[:15]:  # Показываем первые 15
            status_emoji = {
                "active": "🟢",
                "waiting_manager": "🟡",
                "closed": "🔴"
            }.get(chat.status, "⚪")
            
            user_info = f"@{chat.username}" if chat.username else f"ID: {chat.user_id}"
            name = chat.first_name or "Пользователь"
            message_text += (
                f"{status_emoji} Чат #{chat.id} - {name} ({user_info})\n"
                f"   Создан: {chat.created_at.strftime('%d.%m %H:%M')}\n\n"
            )
            
            keyboard_buttons.append([
                InlineKeyboardButton(
                    f"{status_emoji} Чат #{chat.id} - {name}",
                    callback_data=f"view_chat_{chat.id}"
                )
            ])
        
        # Добавляем кнопку "Назад"
        keyboard_buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_back")])
        reply_markup = InlineKeyboardMarkup(keyboard_buttons)
        
        try:
            await query.edit_message_text(message_text, reply_markup=reply_markup)
        except BadRequest as e:
            if "not modified" in str(e).lower():
                pass  # Сообщение не изменилось, это нормально
            else:
                raise
    
    async def show_admin_help(self, query):
        """Показать справку для админа"""
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        help_text = (
            "📋 Команды для администраторов:\n\n"
            "• /start - Главное меню\n"
            "• /chats - Просмотр всех чатов\n"
            "• /close <chat_id> - Закрыть чат\n"
            "• /help - Эта справка\n\n"
            "💡 Используйте кнопки для быстрого доступа к функциям."
        )
        
        try:
            await query.edit_message_text(help_text, reply_markup=reply_markup)
        except BadRequest as e:
            if "not modified" in str(e).lower():
                pass  # Сообщение не изменилось
            else:
                raise
    
    async def close_chat_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /close"""
        user_id = update.effective_user.id
        
        if user_id not in self.config.get_all_staff_ids():
            return
        
        # Режим группы: /close без аргументов — закрыть текущий топик и вернуть ИИ
        if self._group_id and update.effective_chat and update.effective_chat.id == self._group_id and not context.args:
            thread_id = update.message.message_thread_id if update.message else None
            if not thread_id:
                await update.message.reply_text("❌ Команда должна выполняться внутри топика форума.")
                return
            chat_id = None
            if self.redis:
                cid = self.redis.get(f"group_topic:thread:{thread_id}")
                if cid:
                    try:
                        chat_id = int(cid)
                    except:
                        chat_id = None
            if not chat_id:
                await update.message.reply_text("❌ Топик не привязан к чату клиента.")
                return
            await self._reopen_chat_ai(chat_id, user_id, thread_id, update)
            return
        
        # Обычный режим: /close <chat_id> — полностью закрыть чат
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
            
            # Кнопки управления после закрытия
            keyboard = [
                [
                    InlineKeyboardButton("📋 Все чаты", callback_data="admin_chats"),
                    InlineKeyboardButton("🔄 Обновить", callback_data=f"view_chat_{chat_id}")
                ],
                [
                    InlineKeyboardButton("◀️ Назад", callback_data="admin_chats")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                await query.edit_message_text(
                    f"✅ Чат #{chat_id} закрыт.\n\n"
                    f"Пользователь получил уведомление о закрытии чата.",
                    reply_markup=reply_markup
                )
            except BadRequest:
                await query.answer("✅ Чат закрыт", show_alert=True)
        except Exception as e:
            logger.error(f"Error closing chat from button: {e}")
            
            # Кнопки даже при ошибке
            keyboard = [
                [InlineKeyboardButton("◀️ Назад к списку", callback_data="admin_chats")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                await query.edit_message_text(
                    f"❌ Ошибка при закрытии чата: {str(e)}\n\n"
                    f"Попробуйте еще раз или используйте команду /close {chat_id}",
                    reply_markup=reply_markup
                )
            except:
                await query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
    
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
        try:
            chat.status = "closed"
        except Exception:
            pass

        sysmsg = None
        try:
            sysmsg = await self.db.add_message(chat_id, chat.user_id, "Чат закрыт. AI активирован", "system")
        except Exception as e:
            logger.warning(f"Failed to save system message on close: {e}")

        try:
            if self.ws_manager:
                await self.ws_manager.broadcast("status_changed", {"chat_id": chat_id, "status": "closed"})
                if sysmsg:
                    await self.ws_manager.broadcast(
                        "new_message",
                        {
                            "chat_id": chat_id,
                            "message": {
                                "id": sysmsg.id,
                                "text": getattr(sysmsg, "text", None) or sysmsg.content,
                                "source": getattr(sysmsg, "source", None) or sysmsg.message_type,
                                "created_at": sysmsg.created_at.isoformat() if sysmsg.created_at else None,
                                "media_type": getattr(sysmsg, "media_type", None),
                                "media_file_id": getattr(sysmsg, "media_file_id", None),
                            },
                        },
                    )
        except Exception as e:
            logger.warning(f"Failed to broadcast ws updates on close: {e}")
        
        # Уведомляем пользователя
        try:
            await self.application.bot.send_message(
                chat_id=chat.user_id,
                text="💬 Ваш чат с поддержкой был закрыт. Если у вас есть еще вопросы, напишите /start"
            )
        except Exception as e:
            logger.error(f"Error notifying user about closed chat: {e}")

    async def _reopen_chat_ai(self, chat_id: int, user_id: int, thread_id: Optional[int], update: Update):
        """Завершить сессию менеджера в топике группы и вернуть ИИ в активный режим"""
        chat = await self.db.get_chat_by_id(chat_id)
        if not chat:
            await update.message.reply_text("❌ Чат не найден.")
            return
        await self.db.update_chat_status(chat_id, "active", manager_id=None)
        try:
            chat.status = "active"
        except Exception:
            pass

        sysmsg = None
        try:
            sysmsg = await self.db.add_message(chat_id, chat.user_id, "Менеджер завершил сессию. AI активирован", "system")
        except Exception as e:
            logger.warning(f"Failed to save system message on AI reopen: {e}")

        try:
            if self.ws_manager:
                await self.ws_manager.broadcast("status_changed", {"chat_id": chat_id, "status": "active"})
                if sysmsg:
                    await self.ws_manager.broadcast(
                        "new_message",
                        {
                            "chat_id": chat_id,
                            "message": {
                                "id": sysmsg.id,
                                "text": getattr(sysmsg, "text", None) or sysmsg.content,
                                "source": getattr(sysmsg, "source", None) or sysmsg.message_type,
                                "created_at": sysmsg.created_at.isoformat() if sysmsg.created_at else None,
                                "media_type": getattr(sysmsg, "media_type", None),
                                "media_file_id": getattr(sysmsg, "media_file_id", None),
                            },
                        },
                    )
        except Exception as e:
            logger.warning(f"Failed to broadcast ws updates on AI reopen: {e}")
        try:
            await self.application.bot.send_message(
                chat_id=chat.user_id,
                text="👨‍💼 Менеджер завершил сессию. Теперь вам помогает 🤖 AI-поддержка."
            )
        except Exception as e:
            logger.warning(f"Failed to notify user about AI reactivation: {e}")
        try:
            await update.message.reply_text(f"✅ Чат #{chat_id}: ИИ активирован, тема зелёная до нового сообщения клиента.")
        except Exception:
            pass
        if self.redis:
            try:
                self.redis.delete(f"manager_active_chat:{user_id}")
            except Exception:
                pass
        await self._edit_group_topic_status(chat, role_hint=None)
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data
        
        # Обработка действий пользователей
        if data == "user_faq":
            await self.show_user_faq(query)
            return
        elif data == "user_instructions":
            await self.show_user_instructions(query)
            return
        elif data == "user_ask":
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="user_back")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            try:
                await query.edit_message_text(
                    "💬 Напишите ваш вопрос, и я постараюсь помочь!\n\n"
                    "Просто отправьте сообщение с вашим вопросом.",
                    reply_markup=reply_markup
                )
            except BadRequest:
                pass
            return
        elif data == "user_back":
            await self.show_user_back(query)
            return
        
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
        
        # Кнопки управления чатом (всегда внизу)
        keyboard_buttons = []
        
        # Основные действия с чатом
        action_buttons = []
        if chat.status != "closed":
            if chat.status != "waiting_manager" or chat.manager_id is None:
                action_buttons.append(
                    InlineKeyboardButton("👨‍💼 Присоединиться", callback_data=f"join_chat_{chat_id}")
                )
            else:
                action_buttons.append(
                    InlineKeyboardButton("💬 Открыть чат", callback_data=f"join_chat_{chat_id}")
                )
            
            action_buttons.append(
                InlineKeyboardButton("🔴 Закрыть", callback_data=f"close_chat_{chat_id}")
            )
        
        if action_buttons:
            keyboard_buttons.append(action_buttons)
        
        # Кнопка обновления информации о чате
        keyboard_buttons.append([
            InlineKeyboardButton("🔄 Обновить", callback_data=f"view_chat_{chat_id}")
        ])
        
        # Навигационные кнопки (всегда внизу)
        nav_buttons = [
            InlineKeyboardButton("◀️ Назад", callback_data="admin_chats"),
            InlineKeyboardButton("📋 Все чаты", callback_data="admin_chats")
        ]
        keyboard_buttons.append(nav_buttons)
        
        reply_markup = InlineKeyboardMarkup(keyboard_buttons)
        
        try:
            await query.edit_message_text(message_text, reply_markup=reply_markup)
        except BadRequest as e:
            if "not modified" in str(e).lower():
                pass  # Сообщение не изменилось
            else:
                raise
    
    async def join_chat(self, query, chat_id: int, manager_id: int):
        """Присоединиться к чату"""
        chat = await self.db.get_chat_by_id(chat_id)
        
        if not chat:
            await query.edit_message_text("❌ Чат не найден.")
            return
        
        await self.db.update_chat_status(chat_id, "waiting_manager", manager_id)
        try:
            chat.status = "waiting_manager"
            chat.manager_id = manager_id
        except Exception:
            pass

        sysmsg = None
        try:
            sysmsg = await self.db.add_message(chat_id, chat.user_id, "Менеджер подключился", "system")
        except Exception as e:
            logger.warning(f"Failed to save system message on join: {e}")

        try:
            if self.ws_manager:
                await self.ws_manager.broadcast("status_changed", {"chat_id": chat_id, "status": "waiting_manager"})
                if sysmsg:
                    await self.ws_manager.broadcast(
                        "new_message",
                        {
                            "chat_id": chat_id,
                            "message": {
                                "id": sysmsg.id,
                                "text": getattr(sysmsg, "text", None) or sysmsg.content,
                                "source": getattr(sysmsg, "source", None) or sysmsg.message_type,
                                "created_at": sysmsg.created_at.isoformat() if sysmsg.created_at else None,
                                "media_type": getattr(sysmsg, "media_type", None),
                                "media_file_id": getattr(sysmsg, "media_file_id", None),
                            },
                        },
                    )
        except Exception as e:
            logger.warning(f"Failed to broadcast ws updates on join: {e}")
        
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
            "first_name": user.first_name,
            "last_name": user.last_name
        }
        
        ai_response = await self.ai.get_ai_answer(message_text, context, chat_history)
        
        if ai_response:
            # Сохраняем ответ AI
            await self._save_message_to_db(chat.id, user_id, {"kind": "text", "text": ai_response}, "ai")
            
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
            try:
                chat.status = "waiting_manager"
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Error updating chat status: {e}")
            try:
                await query.edit_message_text("❌ Ошибка при обновлении статуса чата.")
            except:
                pass
            return

        summary = None
        try:
            messages = await self.db.get_chat_messages(chat_id, limit=10)
            last_user = next((m.content for m in reversed(messages) if m.message_type == "user"), None)
            if last_user and self.config.ai_support_enabled:
                prompt = f"Кратко, одной фразой опиши, чего хочет пользователь: {last_user}"
                ctx = {"user_id": chat.user_id, "username": chat.username, "first_name": chat.first_name, "last_name": chat.last_name}
                hist = [{"role":"user" if m.message_type=="user" else "assistant","message":m.content} for m in messages]
                summary = await self.ai.get_ai_answer(prompt, ctx, hist)
        except Exception as e:
            logger.warning(f"Failed to build AI summary: {e}")

        sys_text = "🟡 Клиент запросил подключение менеджера"
        if summary:
            sys_text = f"{sys_text}\nКратко: {summary}"
        sysmsg = None
        try:
            sysmsg = await self.db.add_message(chat_id, chat.user_id, sys_text, "system")
        except Exception as e:
            logger.warning(f"Failed to save system message on manager request: {e}")

        try:
            ws = getattr(self, "ws_manager", None)
            if ws:
                await ws.broadcast("status_changed", {"chat_id": chat_id, "status": "waiting_manager"})
                if sysmsg:
                    await ws.broadcast(
                        "new_message",
                        {
                            "chat_id": chat_id,
                            "message": {
                                "id": sysmsg.id,
                                "text": getattr(sysmsg, "text", None) or sysmsg.content,
                                "source": getattr(sysmsg, "source", None) or "system",
                                "created_at": sysmsg.created_at.isoformat() if sysmsg.created_at else None,
                                "media_type": getattr(sysmsg, "media_type", None),
                                "media_file_id": getattr(sysmsg, "media_file_id", None),
                            },
                        },
                    )
        except Exception as e:
            logger.warning(f"Failed to broadcast ws updates on manager request: {e}")
        
        if self._group_id:
            try:
                thread_id = await self._ensure_group_topic(chat)
                if thread_id:
                    name = self._format_topic_title(chat, "client")
                    try:
                        await self.application.bot.edit_forum_topic(chat_id=self._group_id, message_thread_id=thread_id, name=name)
                    except Exception as e:
                        if any(s in str(e).lower() for s in ["topic_deleted", "thread not found", "invalid thread"]):
                            thread_id = await self._recreate_group_topic(chat, "client")
                            if thread_id:
                                await self.application.bot.edit_forum_topic(chat_id=self._group_id, message_thread_id=thread_id, name=name)
                        else:
                            raise
                    base = f"🟡 Пользователь запросил подключение менеджера\nПользователь: @{chat.username or 'N/A'}\nID: {chat.user_id}\nЧат: #{chat.id}"
                    text = base if not summary else f"{base}\nКратко: {summary}"
                    await self.application.bot.send_message(chat_id=self._group_id, text=self._md_to_html(text), message_thread_id=thread_id, disable_notification=False, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.warning(f"Failed to update group topic on manager request: {e}")
        
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
    
    async def show_user_faq(self, query):
        """Показать FAQ для пользователя"""
        keyboard = [
            [InlineKeyboardButton("◀️ Назад", callback_data="user_back")],
            [InlineKeyboardButton("💬 Задать вопрос", callback_data="user_ask")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if self.config.service_faq:
            faq_text = f"❓ Часто задаваемые вопросы:\n\n{self.config.service_faq}"
        else:
            faq_text = (
                "❓ Часто задаваемые вопросы:\n\n"
                "• Как подключиться к VPN?\n"
                "  Используйте subscription URL в настройках VPN клиента\n\n"
                "• Как оплатить подписку?\n"
                "  Оплата доступна в личном кабинете\n\n"
                "• Какие устройства поддерживаются?\n"
                "  Поддержка всех популярных платформ\n\n"
                "Если ваш вопрос не найден, напишите его мне!"
            )
        
        try:
            await query.edit_message_text(faq_text, reply_markup=reply_markup)
        except BadRequest:
            await query.answer("FAQ", show_alert=False)
    
    async def show_user_instructions(self, query):
        """Показать инструкции для пользователя"""
        keyboard = [
            [InlineKeyboardButton("◀️ Назад", callback_data="user_back")],
            [InlineKeyboardButton("💬 Задать вопрос", callback_data="user_ask")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if self.config.service_instructions:
            instructions_text = f"📖 Инструкции по использованию:\n\n{self.config.service_instructions}"
        else:
            instructions_text = (
                "📖 Инструкции по использованию:\n\n"
                "1. Скачайте VPN клиент для вашего устройства\n"
                "2. Получите subscription URL в личном кабинете\n"
                "3. Добавьте subscription URL в настройки VPN клиента\n"
                "4. Подключитесь к серверу\n\n"
                "Если нужна помощь - напишите мне!"
            )
        
        try:
            await query.edit_message_text(instructions_text, reply_markup=reply_markup)
        except BadRequest:
            await query.answer("Инструкции", show_alert=False)
    
    async def show_user_back(self, query):
        """Вернуться в главное меню пользователя"""
        keyboard = [
            [InlineKeyboardButton("❓ Частые вопросы", callback_data="user_faq")],
            [InlineKeyboardButton("📖 Инструкции", callback_data="user_instructions")],
            [InlineKeyboardButton("💬 Задать вопрос", callback_data="user_ask")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = (
            f"👋 Добро пожаловать!\n\n"
            f"Я бот поддержки проекта {self.config.project_name or 'DELTA-Support'}.\n"
            "Выберите действие или просто напишите ваш вопрос:"
        )
        
        try:
            await query.edit_message_text(welcome_text, reply_markup=reply_markup)
        except BadRequest:
            pass
    
    async def handle_service_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message
        if not msg or not self._group_id or not update.effective_chat or update.effective_chat.id != self._group_id:
            return
        if (getattr(msg, "is_topic_message", False) and (
            getattr(msg, "forum_topic_created", None) is not None or
            getattr(msg, "forum_topic_edited", None) is not None or
            getattr(msg, "forum_topic_closed", None) is not None or
            getattr(msg, "forum_topic_reopened", None) is not None
        )) or getattr(msg, "new_chat_title", None) is not None:
            try:
                await self.application.bot.delete_message(chat_id=self._group_id, message_id=msg.message_id)
                logger.info(f"Deleted forum service message {msg.message_id}")
            except Exception as e:
                logger.warning(f"Failed to delete forum service message: {e}")

    def _extract_message_info(self, update: Update):
        msg = update.message
        if msg is None:
            return None
        if msg.sticker:
            return {"kind": "sticker", "text": msg.caption or "", "file_id": msg.sticker.file_id, "message_id": msg.message_id}
        if msg.animation:
            return {"kind": "animation", "text": msg.caption or "", "file_id": msg.animation.file_id, "message_id": msg.message_id}
        if msg.text and not msg.caption:
            return {"kind": "text", "text": msg.text, "file_id": None, "message_id": msg.message_id}
        if msg.photo:
            return {"kind": "photo", "text": msg.caption or "", "file_id": msg.photo[-1].file_id, "message_id": msg.message_id}
        if msg.video:
            return {"kind": "video", "text": msg.caption or "", "file_id": msg.video.file_id, "message_id": msg.message_id}
        if msg.audio:
            return {"kind": "audio", "text": msg.caption or "", "file_id": msg.audio.file_id, "message_id": msg.message_id}
        if msg.voice:
            return {"kind": "voice", "text": msg.caption or "", "file_id": msg.voice.file_id, "message_id": msg.message_id}
        if msg.document:
            return {"kind": "document", "text": msg.caption or "", "file_id": msg.document.file_id, "message_id": msg.message_id}
        if msg.video_note:
            return {"kind": "video_note", "text": "", "file_id": msg.video_note.file_id, "message_id": msg.message_id}
        return {"kind": "unknown", "text": msg.caption or msg.text or "", "file_id": None, "message_id": msg.message_id}

    def _store_reply_map(self, manager_id: int, manager_message_id: int, client_chat_id: int, client_message_id: int, chat_id: int):
        if not self.redis:
            return
        key = f"reply_map:{manager_id}:{manager_message_id}"
        self.redis.setex(key, 7 * 24 * 3600, json.dumps({"client_chat_id": client_chat_id, "client_message_id": client_message_id, "chat_id": chat_id}))

    def _get_reply_map(self, manager_id: int, replied_message_id: int):
        if not self.redis:
            return None
        key = f"reply_map:{manager_id}:{replied_message_id}"
        val = self.redis.get(key)
        if not val:
            return None
        try:
            return json.loads(val)
        except Exception:
            return None

    def _set_manager_active_chat(self, manager_id: int, chat_id: int):
        if not self.redis:
            return
        self.redis.setex(f"manager_active_chat:{manager_id}", 24 * 3600, str(chat_id))

    def _get_manager_active_chat(self, manager_id: int) -> Optional[int]:
        if not self.redis:
            return None
        val = self.redis.get(f"manager_active_chat:{manager_id}")
        try:
            return int(val) if val else None
        except Exception:
            return None

    async def _save_message_to_db(self, chat_id: int, user_id: int, info: dict, role: str):
        text = info.get("text") or ""
        kind = info.get("kind") or "text"
        prefix = {
            "text": "",
            "photo": "[photo] ",
            "video": "[video] ",
            "audio": "[audio] ",
            "voice": "[voice] ",
            "document": "[document] ",
            "video_note": "[video_note] ",
        }.get(kind, "")
        content = f"{prefix}{text}".strip()
        msg = await self.db.add_message(chat_id, user_id, content, role)
        file_id = info.get("file_id")
        tg_message_id = info.get("message_id")
        if file_id or kind != "text":
            try:
                from modules.database import Message as MessageModel
                await MessageModel.filter(id=msg.id).update(media_type=kind, media_file_id=file_id, tg_message_id_user=tg_message_id)
            except Exception:
                pass
        if self.ws_manager:
            try:
                await self.ws_manager.broadcast(
                    "new_message",
                    {
                        "chat_id": chat_id,
                        "message": {
                            "id": msg.id,
                            "text": getattr(msg, "text", None) or msg.content,
                            "source": getattr(msg, "source", None) or msg.message_type,
                            "created_at": msg.created_at.isoformat() if msg.created_at else None,
                            "media_type": kind if kind != "text" else None,
                            "media_file_id": file_id,
                        },
                    },
                )
            except Exception:
                pass

    async def _send_to_client(self, client_chat_id: int, info: dict):
        header = await self.application.bot.send_message(chat_id=client_chat_id, text="👨‍💼 Менеджер поддержки")
        kind = info["kind"]
        text = info.get("text") or ""
        file_id = info.get("file_id")
        if kind == "text":
            await self.application.bot.send_message(chat_id=client_chat_id, text=self._md_to_html(text), reply_to_message_id=header.message_id, parse_mode=ParseMode.HTML)
        elif kind == "photo":
            await self.application.bot.send_photo(chat_id=client_chat_id, photo=file_id, caption=(self._md_to_html(text) if text else None), reply_to_message_id=header.message_id, parse_mode=ParseMode.HTML)
        elif kind == "video":
            await self.application.bot.send_video(chat_id=client_chat_id, video=file_id, caption=(self._md_to_html(text) if text else None), reply_to_message_id=header.message_id, parse_mode=ParseMode.HTML)
        elif kind == "audio":
            await self.application.bot.send_audio(chat_id=client_chat_id, audio=file_id, caption=(self._md_to_html(text) if text else None), reply_to_message_id=header.message_id, parse_mode=ParseMode.HTML)
        elif kind == "voice":
            await self.application.bot.send_voice(chat_id=client_chat_id, voice=file_id, caption=(self._md_to_html(text) if text else None), reply_to_message_id=header.message_id, parse_mode=ParseMode.HTML)
        elif kind == "document":
            await self.application.bot.send_document(chat_id=client_chat_id, document=file_id, caption=(self._md_to_html(text) if text else None), reply_to_message_id=header.message_id, parse_mode=ParseMode.HTML)
        elif kind == "video_note":
            await self.application.bot.send_video_note(chat_id=client_chat_id, video_note=file_id, reply_to_message_id=header.message_id)
        elif kind == "sticker":
            await self.application.bot.send_sticker(chat_id=client_chat_id, sticker=file_id, reply_to_message_id=header.message_id)
        elif kind == "animation":
            await self.application.bot.send_animation(chat_id=client_chat_id, animation=file_id, caption=(self._md_to_html(text) if text else None), reply_to_message_id=header.message_id, parse_mode=ParseMode.HTML)
        else:
            await self.application.bot.send_message(chat_id=client_chat_id, text=self._md_to_html(text or "Сообщение"), reply_to_message_id=header.message_id, parse_mode=ParseMode.HTML)

    def _status_emoji(self, status: str, role_hint: Optional[str] = None) -> str:
        if role_hint and role_hint in self._emoji_by_role:
            return self._emoji_by_role.get(role_hint) or self._emoji_by_role.get("default") or "🟢"
        if status in self._emoji_by_status:
            return self._emoji_by_status.get(status) or self._emoji_by_role.get("default") or "�"
        return self._emoji_by_role.get("default") or "🟢"

    def _status_label(self, status: str) -> str:
        return {
            "active": "AI",
            "waiting_manager": "Ожидает менеджера",
            "closed": "Закрыт",
        }.get(status, status)

    def _format_topic_title(self, chat, role_hint: Optional[str] = None) -> str:
        emoji = self._status_emoji(getattr(chat, "status", ""), role_hint)
        data = {
            "emoji": emoji,
            "status": getattr(chat, "status", ""),
            "status_label": self._status_label(getattr(chat, "status", "")),
            "first_name": getattr(chat, "first_name", None) or "Пользователь",
            "last_name": getattr(chat, "last_name", None) or "",
            "username": getattr(chat, "username", None) or "",
            "user_id": getattr(chat, "user_id", ""),
            "chat_id": getattr(chat, "id", ""),
        }

        class _SafeDict(dict):
            def __missing__(self, key):
                return "{" + key + "}"

        try:
            return (self._topic_title_template or "{emoji} {first_name} ({user_id}) {status_label}").format_map(_SafeDict(data)).strip()
        except Exception:
            return f"{emoji} {data['first_name']} ({data['user_id']}) {data['status_label']}".strip()

    async def _ensure_group_topic(self, chat) -> Optional[int]:
        await self.refresh_runtime_settings()
        if not self._group_id:
            return None
        thread_key = f"group_topic:chat:{chat.id}"
        thread_id = self.redis.get(thread_key) if self.redis else None
        if thread_id:
            try:
                return int(thread_id)
            except:
                pass
        name = self._format_topic_title(chat, None)
        try:
            topic = await self.application.bot.create_forum_topic(chat_id=self._group_id, name=name)
            thread_id = getattr(topic, "message_thread_id", None)
        except Exception as e:
            logger.error(f"Failed to create forum topic: {e}")
            thread_id = None
        if thread_id and self.redis:
            self.redis.set(f"group_topic:chat:{chat.id}", str(thread_id))
            self.redis.set(f"group_topic:thread:{thread_id}", str(chat.id))
        if thread_id:
            try:
                from modules.database import Chat as ChatModel
                await ChatModel.filter(id=chat.id).update(topic_id=thread_id)
            except Exception:
                pass
        return thread_id
    
    async def _recreate_group_topic(self, chat, role_hint: Optional[str] = None) -> Optional[int]:
        await self.refresh_runtime_settings()
        name = self._format_topic_title(chat, role_hint)
        try:
            topic = await self.application.bot.create_forum_topic(chat_id=self._group_id, name=name)
            thread_id = getattr(topic, "message_thread_id", None)
        except Exception as e:
            logger.error(f"Failed to recreate forum topic: {e}")
            return None
        if self.redis and thread_id:
            self.redis.set(f"group_topic:chat:{chat.id}", str(thread_id))
            self.redis.set(f"group_topic:thread:{thread_id}", str(chat.id))
            self.redis.delete(f"group_topic:pin:{thread_id}")
        if thread_id:
            try:
                from modules.database import Chat as ChatModel
                await ChatModel.filter(id=chat.id).update(topic_id=thread_id)
            except Exception:
                pass
        return thread_id

    async def _edit_group_topic_status(self, chat, role_hint: Optional[str] = None):
        await self.refresh_runtime_settings()
        if not self._group_id or not self.redis:
            return
        thread_id = self.redis.get(f"group_topic:chat:{chat.id}")
        if not thread_id:
            return
        try:
            name = self._format_topic_title(chat, role_hint)
            await self.application.bot.edit_forum_topic(chat_id=self._group_id, message_thread_id=int(thread_id), name=name)
        except Exception as e:
            if any(s in str(e).lower() for s in ["topic_deleted", "thread not found", "invalid thread"]):
                new_thread = await self._recreate_group_topic(chat, role_hint)
                if new_thread:
                    try:
                        name = self._format_topic_title(chat, role_hint)
                        await self.application.bot.edit_forum_topic(chat_id=self._group_id, message_thread_id=int(new_thread), name=name)
                    except Exception as e2:
                        logger.warning(f"Failed to edit recreated forum topic: {e2}")
            else:
                logger.warning(f"Failed to edit forum topic: {e}")

    async def _duplicate_to_group(self, chat, user, info: dict, update: Update, role_hint: Optional[str] = None):
        thread_id = await self._ensure_group_topic(chat)
        if not thread_id:
            return
        try:
            mute = chat.status != "waiting_manager"
            pin_key = f"group_topic:pin:{thread_id}"
            pinned_id = self.redis.get(pin_key) if self.redis else None
            reply_to_id = None
            if not pinned_id:
                full = []
                full.append(f"👤 Клиент: @{user.username}" if user.username else f"👤 Клиент: {user.first_name or 'Клиент'}")
                full.append(f"🆔 ID: {chat.id}")
                full.append(f"UID: {chat.user_id}")
                text_full = " | ".join(full)
                header = await self.application.bot.send_message(chat_id=self._group_id, text=text_full, message_thread_id=thread_id, disable_notification=True)
                try:
                    await self.application.bot.pin_chat_message(chat_id=self._group_id, message_id=header.message_id, disable_notification=True)
                except Exception as e:
                    logger.warning(f"Failed to pin header: {e}")
                if self.redis:
                    self.redis.set(pin_key, str(header.message_id))
                reply_to_id = header.message_id
            else:
                try:
                    reply_to_id = int(pinned_id)
                except Exception:
                    reply_to_id = None
            copied = await self.application.bot.copy_message(chat_id=self._group_id, from_chat_id=user.id, message_id=info["message_id"], message_thread_id=thread_id, reply_to_message_id=reply_to_id, disable_notification=mute)
            if self.redis:
                self.redis.setex(f"group_reply:{self._group_id}:{copied.message_id}", 7 * 24 * 3600, json.dumps({"client_chat_id": chat.user_id, "client_message_id": info["message_id"], "chat_id": chat.id}))
                if reply_to_id:
                    self.redis.setex(f"group_reply:{self._group_id}:{reply_to_id}", 7 * 24 * 3600, json.dumps({"client_chat_id": chat.user_id, "client_message_id": info["message_id"], "chat_id": chat.id}))
            await self._edit_group_topic_status(chat, role_hint)
        except Exception as e:
            if any(s in str(e).lower() for s in ["topic_deleted", "message thread", "thread not found", "invalid thread"]):
                new_thread = await self._recreate_group_topic(chat, role_hint)
                if not new_thread:
                    logger.error(f"Failed to duplicate to group: {e}")
                    return
                try:
                    mute = chat.status != "waiting_manager"
                    pin_key = f"group_topic:pin:{new_thread}"
                    pinned_id = self.redis.get(pin_key) if self.redis else None
                    reply_to_id = None
                    if not pinned_id:
                        full = []
                        full.append(f"👤 Клиент: @{user.username}" if user.username else f"👤 Клиент: {user.first_name or 'Клиент'}")
                        full.append(f"🆔 ID: {chat.id}")
                        full.append(f"UID: {chat.user_id}")
                        text_full = " | ".join(full)
                        header = await self.application.bot.send_message(chat_id=self._group_id, text=text_full, message_thread_id=new_thread, disable_notification=True)
                        try:
                            await self.application.bot.pin_chat_message(chat_id=self._group_id, message_id=header.message_id, disable_notification=True)
                        except Exception as e2:
                            logger.warning(f"Failed to pin header: {e2}")
                        if self.redis:
                            self.redis.set(pin_key, str(header.message_id))
                        reply_to_id = header.message_id
                    else:
                        try:
                            reply_to_id = int(pinned_id)
                        except Exception:
                            reply_to_id = None
                    copied = await self.application.bot.copy_message(chat_id=self._group_id, from_chat_id=user.id, message_id=info["message_id"], message_thread_id=new_thread, reply_to_message_id=reply_to_id, disable_notification=mute)
                    if self.redis:
                        self.redis.setex(f"group_reply:{self._group_id}:{copied.message_id}", 7 * 24 * 3600, json.dumps({"client_chat_id": chat.user_id, "client_message_id": info["message_id"], "chat_id": chat.id}))
                        if reply_to_id:
                            self.redis.setex(f"group_reply:{self._group_id}:{reply_to_id}", 7 * 24 * 3600, json.dumps({"client_chat_id": chat.user_id, "client_message_id": info["message_id"], "chat_id": chat.id}))
                    await self._edit_group_topic_status(chat, role_hint)
                except Exception as e3:
                    logger.error(f"Failed to duplicate to group after recreate: {e3}")
            else:
                logger.error(f"Failed to duplicate to group: {e}")

    async def _forward_to_manager(self, chat, user, info: dict, update: Update):
        if self._group_id:
            await self._duplicate_to_group(chat, user, info, update, role_hint="client")
            try:
                await update.message.reply_text("✅ Ваше сообщение отправлено в поддержку. Ожидайте ответа.")
            except Exception:
                pass
            return True
        manager_id = chat.manager_id
        if not manager_id:
            try:
                await update.message.reply_text("✅ Ваше сообщение сохранено. Менеджер скоро ответит.")
            except Exception:
                pass
            return True
        signature = f"👤 Клиент: @{user.username}" if user.username else f"👤 Клиент: {user.first_name or 'Клиент'}"
        signature += f" 🆔 ID: {chat.id}"
        header = await self.application.bot.send_message(chat_id=manager_id, text=signature)
        copied = await self.application.bot.copy_message(chat_id=manager_id, from_chat_id=user.id, message_id=info["message_id"], reply_to_message_id=header.message_id)
        self._store_reply_map(manager_id, copied.message_id, chat.user_id, info["message_id"], chat.id)
        self._store_reply_map(manager_id, header.message_id, chat.user_id, info["message_id"], chat.id)
        self._set_manager_active_chat(manager_id, chat.id)
        try:
            await update.message.reply_text("✅ Ваше сообщение отправлено менеджеру. Ожидайте ответа.")
        except Exception:
            pass
        return True

    async def handle_any_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = user.id
        info = self._extract_message_info(update)
        if not info:
            return
        if self._group_id and update.effective_chat and update.effective_chat.id == self._group_id:
            thread_id = update.message.message_thread_id if update.message else None
            if not thread_id:
                return
            msg = update.message
            if (getattr(msg, "is_topic_message", False) and (
                getattr(msg, "forum_topic_created", None) is not None or
                getattr(msg, "forum_topic_edited", None) is not None or
                getattr(msg, "forum_topic_closed", None) is not None or
                getattr(msg, "forum_topic_reopened", None) is not None
            )) or getattr(msg, "new_chat_title", None):
                try:
                    await self.application.bot.delete_message(chat_id=self._group_id, message_id=msg.message_id)
                except Exception as e:
                    logger.warning(f"Failed to delete forum service message: {e}")
                return
            if user_id not in self.config.get_all_staff_ids():
                return
            replied = update.message.reply_to_message.message_id if update.message and update.message.reply_to_message else None
            route = None
            if replied and self.redis:
                val = self.redis.get(f"group_reply:{self._group_id}:{replied}")
                if val:
                    try:
                        route = json.loads(val)
                    except:
                        route = None
            client_chat_id = None
            chat_id = None
            if route:
                client_chat_id = route["client_chat_id"]
                chat_id = route["chat_id"]
            else:
                if self.redis:
                    cid = self.redis.get(f"group_topic:thread:{thread_id}")
                    chat_id = int(cid) if cid else None
                if chat_id:
                    chat = await self.db.get_chat_by_id(chat_id)
                    client_chat_id = chat.user_id if chat else None
            if not client_chat_id or not chat_id:
                await update.message.reply_text("❌ Не найден связанный клиент для этого топика.")
                return
            try:
                await self._send_to_client(client_chat_id, info)
                await self._save_message_to_db(chat_id, user_id, info, "manager")
                await self._edit_group_topic_status(await self.db.get_chat_by_id(chat_id), role_hint="manager")
            except Exception as e:
                logger.error(f"Error routing manager message from group: {e}")
                await update.message.reply_text("❌ Ошибка при отправке клиенту")
            return
        if user_id in self.config.get_all_staff_ids():
            replied = update.message.reply_to_message.message_id if update.message and update.message.reply_to_message else None
            route = self._get_reply_map(user_id, replied) if replied else None
            if route:
                client_chat_id = route["client_chat_id"]
                chat_id = route["chat_id"]
                try:
                    await self._send_to_client(client_chat_id, info)
                    await self._save_message_to_db(chat_id, user_id, info, "manager")
                    await update.message.reply_text(f"✅ Сообщение отправлено пользователю (Чат #{chat_id})")
                except Exception as e:
                    logger.error(f"Error routing manager reply: {e}")
                    await update.message.reply_text("❌ Ошибка при отправке сообщения")
                return
            active_chat_id = self._get_manager_active_chat(user_id)
            manager_chat = None
            if not active_chat_id:
                chats = await self.db.get_all_chats(status="waiting_manager")
                for c in chats:
                    if c.manager_id == user_id:
                        manager_chat = c
                        break
            else:
                manager_chat = await self.db.get_chat_by_id(active_chat_id)
            if manager_chat:
                try:
                    await self._send_to_client(manager_chat.user_id, info)
                    await self._save_message_to_db(manager_chat.id, user_id, info, "manager")
                    await update.message.reply_text(f"✅ Сообщение отправлено пользователю (Чат #{manager_chat.id})")
                except Exception as e:
                    logger.error(f"Error sending message from manager to user: {e}")
                    await update.message.reply_text("❌ Ошибка при отправке сообщения")
            else:
                await update.message.reply_text("💬 Используйте команду /chats и подключитесь к чату, затем ответьте на пересланное сообщение.")
            return
        chat = await self.db.get_chat_by_user_id(user_id)
        if not chat:
            chat = await self.db.create_chat(user_id=user_id, username=user.username, first_name=user.first_name, last_name=user.last_name)
        await self._save_message_to_db(chat.id, user_id, info, "user")
        if chat.status == "waiting_manager":
            try:
                await self._forward_to_manager(chat, user, info, update)
            except Exception as e:
                logger.error(f"Error forwarding message to manager: {e}")
                try:
                    await update.message.reply_text("✅ Ваше сообщение сохранено. Менеджер скоро ответит.")
                except Exception:
                    pass
            return
        if self._group_id:
            try:
                await self._duplicate_to_group(chat, user, info, update, role_hint="client")
            except Exception as e:
                logger.error(f"Failed to duplicate client message to group: {e}")
        if info["kind"] == "text":
            chat_messages = await self.db.get_chat_messages(chat.id, limit=20)
            chat_history = [{"role": "user" if msg.message_type == "user" else "assistant", "message": msg.content} for msg in chat_messages]
            context_info = {"user_id": user_id, "username": user.username, "first_name": user.first_name, "last_name": user.last_name}
            ai_response = await self.ai.get_ai_answer(info["text"], context_info, chat_history)
            if ai_response:
                await self._save_message_to_db(chat.id, user_id, {"kind": "text", "text": ai_response}, "ai")
                if any(keyword in ai_response.lower() for keyword in ["менеджер", "пригласить", "подключить"]):
                    keyboard = [[InlineKeyboardButton("Да, пригласите менеджера", callback_data=f"request_manager_{chat.id}")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await update.message.reply_text(self._md_to_html(ai_response), reply_markup=reply_markup, parse_mode=ParseMode.HTML)
                else:
                    await update.message.reply_text(self._md_to_html(ai_response), parse_mode=ParseMode.HTML)
                if self._group_id:
                    try:
                        thread_id = await self._ensure_group_topic(chat)
                        if thread_id:
                            mute = chat.status != "waiting_manager"
                            header = await self.application.bot.send_message(chat_id=self._group_id, text="🤖 Ответ ИИ", message_thread_id=thread_id, disable_notification=mute)
                            await self.application.bot.send_message(chat_id=self._group_id, text=self._md_to_html(ai_response), message_thread_id=thread_id, reply_to_message_id=header.message_id, disable_notification=mute, parse_mode=ParseMode.HTML)
                            await self._edit_group_topic_status(chat, role_hint="ai")
                    except Exception as e:
                        logger.error(f"Failed to duplicate AI response to group: {e}")
            else:
                keyboard = [[InlineKeyboardButton("Пригласить менеджера", callback_data=f"request_manager_{chat.id}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text("Извините, я не смог обработать ваш вопрос. Хотите пригласить менеджера в чат?", reply_markup=reply_markup)
        else:
            keyboard = [[InlineKeyboardButton("Пригласить менеджера", callback_data=f"request_manager_{chat.id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Сообщение сохранено. Для обработки медиа подключите менеджера.", reply_markup=reply_markup)
        if self._group_id and info["kind"] != "text":
            try:
                await self._duplicate_to_group(chat, user, info, update, role_hint="client")
            except Exception as e:
                logger.error(f"Failed to duplicate client media to group: {e}")
