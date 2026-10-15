"""
Модуль Telegram бота поддержки
"""

import asyncio
import json
import redis
import html
import re
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode
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
from modules.user_info import UserInfoService, PendingAction


class SupportBot:
    """Класс бота поддержки"""
    
    def __init__(self, config: Config, database: Database):
        self.config = config
        self.db = database
        self.ai = AISupport(config)
        self.user_info = UserInfoService(config)
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
        # Бан-лист клиентов (tg id), управляется /ban и /unban
        self._banned_users = set()
        # Автоматизация жизненного цикла чатов
        self._auto_close_enabled = False
        self._auto_close_reminder_minutes = 360   # напоминание клиенту после N минут тишины
        self._auto_close_after_minutes = 720      # закрытие через M минут после напоминания
        self._auto_close_reminder_text = (
            "👋 Ваш вопрос ещё актуален? Если да — просто ответьте на это сообщение. "
            "Если ответа не будет, чат будет автоматически закрыт."
        )
        self._auto_close_text = (
            "💬 Чат закрыт автоматически из-за отсутствия активности. "
            "Если у вас остались вопросы — просто напишите нам снова."
        )
        self._sla_ping_enabled = True
        self._sla_ping_minutes = 15               # повторный пинг, если запрос никто не взял
        self._lifecycle_task = None
        # Разрушающие действия (сброс устройств, перевыпуск подписки), ожидающие
        # подтверждения клиента: token -> {"expires": ts, "data": {...}}.
        # Fallback на память процесса, если Redis недоступен (TTL 5 минут).
        self._pending_actions_mem = {}
        # Текст-префикс перед сообщением менеджера клиенту и режим его показа:
        #   "combined"      — префикс встроен в текст/подпись каждого сообщения
        #   "session_header" — префикс отправляется один раз за сессию, дальше
        #                      сообщения менеджера идут ответом (reply) на него
        self._manager_reply_prefix = "👨‍💼 Менеджер поддержки"
        self._manager_reply_style = "combined"
        # Fallback-хранилище message_id заголовка сессии, если Redis недоступен
        self._manager_header_mem = {}
        # Подтверждение клиенту "✅ Ваше сообщение отправлено..." при каждом
        # сообщении, пока чат ждёт менеджера — можно отключить, если раздражает
        self._client_ack_enabled = True
        # message_id последнего такого подтверждения на чат — чтобы можно было
        # удалить его, когда придёт реальный ответ менеджера (best-effort)
        self._waiting_ack_mem = {}
        # AI-обработка медиа и отчеты
        self._ai_voice_enabled = True             # расшифровка голосовых через Whisper
        self._ai_vision_enabled = True            # разбор фото/скриншотов vision-моделью
        self._weekly_report_enabled = True        # еженедельный отчет админам
        try:
            from chatgpt_md_converter import telegram_format as _md_to_html
            self._md_to_html = _md_to_html
        except Exception:
            self._md_to_html = lambda t: html.escape(t or "")

    async def _typing_loop(self, chat_id: int, stop_event: asyncio.Event):
        while not stop_event.is_set():
            try:
                await self.application.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            except Exception as e:
                logger.debug(f"Typing action failed for chat {chat_id}: {e}")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                pass

    def _start_typing(self, chat_id: Optional[int]):
        if not chat_id or not self.application:
            return None, None
        stop_event = asyncio.Event()
        task = asyncio.create_task(self._typing_loop(int(chat_id), stop_event))
        return stop_event, task

    async def _stop_typing(self, stop_event, task):
        if not stop_event or not task:
            return
        stop_event.set()
        try:
            await task
        except Exception:
            pass

    @staticmethod
    def _plain_match_text(value) -> str:
        return " ".join(re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", str(value or "").lower()))

    def _is_single_device_delete_request(self, text: str) -> bool:
        normalized = self._plain_match_text(text)
        if not normalized:
            return False
        has_delete = any(
            word in normalized.split()
            for word in ("удали", "удалить", "удалите", "убери", "убрать", "уберите", "отвяжи", "отвязать", "отвяжите")
        )
        if not has_delete:
            return False
        return not any(word in normalized.split() for word in ("все", "всё", "всех"))

    @staticmethod
    def _device_hwid(device: dict) -> str:
        for key in ("hwid", "HWID", "deviceHwid", "device_hwid", "id"):
            value = str((device or {}).get(key) or "").strip()
            if value:
                return value
        return ""

    def _device_label(self, device: dict) -> str:
        if not device:
            return "устройство"
        direct_keys = (
            "deviceName",
            "device_name",
            "name",
            "model",
            "deviceModel",
            "device_model",
            "platform",
            "os",
            "app",
            "appName",
        )
        parts = []
        for key in direct_keys:
            value = str(device.get(key) or "").strip()
            if value and value not in parts:
                parts.append(value)
        return " / ".join(parts[:3]) or "устройство"

    def _device_search_text(self, device: dict) -> str:
        values = []
        for key, value in (device or {}).items():
            if key.lower() in {"hwid", "id", "userid", "user_id", "useruuid", "user_uuid"}:
                continue
            if isinstance(value, (str, int, float)):
                values.append(str(value))
        return self._plain_match_text(" ".join(values))

    def _device_match_score(self, query: str, device: dict) -> int:
        query_terms = [
            t for t in self._plain_match_text(query).split()
            if t not in {"удали", "удалить", "убери", "убрать", "отвяжи", "отвязать", "устройство", "девайс"}
        ]
        if not query_terms:
            return 0
        haystack = self._device_search_text(device)
        return sum(1 for term in query_terms if term in haystack)

    async def _maybe_send_device_delete_confirmation(self, update: Update, chat, user_id: int, question_text: str) -> bool:
        if not self._is_single_device_delete_request(question_text):
            return False
        try:
            subscriptions = await self.user_info.get_own_subscriptions(user_id)
            candidates = []
            for sub in subscriptions:
                username = sub.get("username")
                if not username:
                    continue
                devices_data = await self.user_info.get_subscription_devices(username)
                for device in (devices_data or {}).get("devices") or []:
                    hwid = self._device_hwid(device)
                    if not hwid:
                        continue
                    candidates.append((sub, device, self._device_match_score(question_text, device)))
            if not candidates:
                return False
            candidates.sort(key=lambda item: item[2], reverse=True)
            best_sub, best_device, best_score = candidates[0]
            if len(candidates) > 1 and best_score <= 0:
                return False
            if len(candidates) > 1 and best_score == candidates[1][2]:
                return False
            label = self._device_label(best_device)
            username = best_sub.get("username")
            action = PendingAction(
                action="delete_device",
                params={"subscription_username": username, "hwid": self._device_hwid(best_device), "device_label": label},
                confirm_text=f"Подтвердите удаление устройства «{label}» из подписки «{best_sub.get('tarif') or username}».",
            )
            await self._send_pending_action_confirmation(update, chat, action)
            return True
        except Exception as e:
            logger.warning(f"Device delete intent handling failed: {e}")
            return False

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
            "banned_users",
            "auto_close_enabled",
            "auto_close_reminder_minutes",
            "auto_close_after_minutes",
            "auto_close_reminder_text",
            "auto_close_text",
            "sla_ping_enabled",
            "sla_ping_minutes",
            "ai_voice_enabled",
            "ai_vision_enabled",
            "weekly_report_enabled",
            "manager_reply_prefix",
            "manager_reply_style",
            "client_ack_enabled",
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

        # Бан-лист (JSON-массив tg id)
        try:
            banned_raw = (values.get("banned_users") or "").strip()
            self._banned_users = set(int(x) for x in json.loads(banned_raw)) if banned_raw else set()
        except Exception:
            self._banned_users = set()

        # Автоматизация
        def _as_bool(key, default):
            raw = (values.get(key) or "").strip()
            return raw.lower() in ["1", "true", "yes", "y", "on"] if raw else default

        def _as_int(key, default, minimum=1):
            raw = (values.get(key) or "").strip()
            return max(minimum, int(raw)) if raw.lstrip("-").isdigit() else default

        self._auto_close_enabled = _as_bool("auto_close_enabled", False)
        self._auto_close_reminder_minutes = _as_int("auto_close_reminder_minutes", 360, minimum=5)
        self._auto_close_after_minutes = _as_int("auto_close_after_minutes", 720, minimum=5)
        self._sla_ping_enabled = _as_bool("sla_ping_enabled", True)
        self._sla_ping_minutes = _as_int("sla_ping_minutes", 15, minimum=1)
        self._ai_voice_enabled = _as_bool("ai_voice_enabled", True)
        self._ai_vision_enabled = _as_bool("ai_vision_enabled", True)
        self._weekly_report_enabled = _as_bool("weekly_report_enabled", True)
        if (values.get("auto_close_reminder_text") or "").strip():
            self._auto_close_reminder_text = values["auto_close_reminder_text"].strip()
        if (values.get("auto_close_text") or "").strip():
            self._auto_close_text = values["auto_close_text"].strip()
        if (values.get("manager_reply_prefix") or "").strip():
            self._manager_reply_prefix = values["manager_reply_prefix"].strip()
        style_raw = (values.get("manager_reply_style") or "").strip()
        if style_raw in ("combined", "session_header"):
            self._manager_reply_style = style_raw
        self._client_ack_enabled = _as_bool("client_ack_enabled", True)

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
        self.application.add_handler(CommandHandler("info", self.info_command))
        self.application.add_handler(CommandHandler("ai", self.ai_command))
        self.application.add_handler(CommandHandler("summary", self.summary_command))
        self.application.add_handler(CommandHandler("ban", self.ban_command))
        self.application.add_handler(CommandHandler("unban", self.unban_command))
        self.application.add_handler(CommandHandler("note", self.note_command))
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
        self.application.add_error_handler(self.error_handler)

        logger.info("Bot handlers registered")

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Глобальный обработчик необработанных исключений PTB.
        Без него ошибки внутри хендлеров (в т.ч. button_callback) проглатываются
        библиотекой молча — в логах не остаётся ни следа, а пользователь видит
        "зависшую" кнопку/отсутствие ответа."""
        logger.opt(exception=context.error).error(f"Unhandled exception while processing update {update}")
        try:
            if isinstance(update, Update) and update.callback_query:
                await update.callback_query.answer(
                    "❌ Произошла ошибка. Попробуйте ещё раз или обратитесь к менеджеру.",
                    show_alert=True,
                )
        except Exception:
            pass

    async def start_polling(self):
        """Запуск поллинга без блокировки"""
        logger.info("Bot starting polling...")
        await self.application.initialize()
        await self.application.start()
        # allowed_updates нужно указывать явно: Telegram запоминает последнее
        # значение этого параметра (в т.ч. установленное прежним setWebhook) и
        # применяет его и к getUpdates — если не передать его здесь, сервер может
        # молча перестать присылать боту callback_query (нажатия инлайн-кнопок),
        # сохранив только, например, "message". Из-за этого кнопки выглядели
        # "зависшими": апдейт с нажатием кнопки до бота попросту не долетал.
        await self.application.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )
        # Фоновый цикл автоматизации (автозакрытие, SLA-пинги)
        self._lifecycle_task = asyncio.create_task(self._lifecycle_loop())

    async def stop(self):
        """Остановка бота"""
        logger.info("Stopping bot...")
        if self._lifecycle_task:
            self._lifecycle_task.cancel()
            self._lifecycle_task = None
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

        await self.refresh_runtime_settings()
        if user_id in self._banned_users and user_id not in self.config.get_all_staff_ids():
            return

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
        
        # Режим группы: /close без аргументов — закрыть текущий топик (чат закрыт полностью)
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
            await self.close_chat_from_message(update.message, chat_id, user_id)
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
    
    async def _close_chat(self, chat_id: int, user_id: int, notify_text: str = None, reason: str = None):
        """Внутренняя функция закрытия чата"""
        chat = await self.db.get_chat_by_id(chat_id)

        if not chat:
            raise ValueError(f"Чат #{chat_id} не найден.")

        await self.db.update_chat_status(chat_id, "closed")
        try:
            chat.status = "closed"
        except Exception:
            pass
        self._clear_manager_header(chat_id)
        try:
            await self._edit_group_topic_status(chat, role_hint=None)
        except Exception as e:
            logger.warning(f"Failed to update group topic on close: {e}")

        sysmsg = None
        try:
            sys_text = f"Чат закрыт ({reason}). AI активирован" if reason else "Чат закрыт. AI активирован"
            sysmsg = await self.db.add_message(chat_id, chat.user_id, sys_text, "system")
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
                text=notify_text or "💬 Ваш чат с поддержкой был закрыт. Если у вас есть еще вопросы, напишите /start"
            )
        except Exception as e:
            logger.error(f"Error notifying user about closed chat: {e}")

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        try:
            await query.answer()
        except BadRequest as e:
            # Устаревший/невалидный callback query (например, бот перезапускался
            # или пользователь нажал кнопку спустя долгое время) не должен обрывать
            # обработку — иначе кнопка "зависает" у пользователя без какой-либо реакции.
            logger.warning(f"query.answer() failed for callback_data={query.data!r}: {e}")

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

        # Подтверждение/отмена разрушающих действий (сброс устройств, перевыпуск подписки)
        # доступно клиенту напрямую, без прав персонала — это его собственный аккаунт
        if data.startswith("confirm_action_"):
            token = data.replace("confirm_action_", "")
            await self._confirm_pending_action(query, user_id, token)
            return
        if data.startswith("cancel_action_"):
            token = data.replace("cancel_action_", "")
            await self._cancel_pending_action(query, user_id, token)
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
        elif data.startswith("take_chat_"):
            chat_id = int(data.replace("take_chat_", ""))
            await self.take_chat(query, chat_id, user_id)
        elif data.startswith("info_chat_"):
            chat_id = int(data.replace("info_chat_", ""))
            await self.send_client_card(query, chat_id)
    
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
        
        typing_stop, typing_task = self._start_typing(update.effective_chat.id if update.effective_chat else user_id)
        try:
            ai_response = await self.ai.get_ai_answer(message_text, context, chat_history)
        finally:
            await self._stop_typing(typing_stop, typing_task)
        
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

        # AI-сводка проблемы по истории диалога
        summary = await self._build_chat_summary(chat)

        # Карточка клиента из Support API (баланс, подписки, ключи)
        client_card = None
        try:
            api_data = await self.user_info.get_user_info(chat.user_id, force=True)
            if api_data:
                client_card = self.user_info.format_for_manager(api_data)
        except Exception as e:
            logger.warning(f"Support API card failed for user {chat.user_id}: {e}")

        sys_text = "🟡 Клиент запросил подключение менеджера"
        if summary:
            sys_text = f"{sys_text}\n\n📝 Сводка: {summary}"
        if client_card:
            sys_text = f"{sys_text}\n\n{client_card}"
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
                    if summary:
                        base = f"{base}\n\n📝 Сводка: {summary}"
                    if client_card:
                        base = f"{base}\n\n{client_card}"
                    text = base
                    take_keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("🙋 Взять в работу", callback_data=f"take_chat_{chat.id}"),
                        InlineKeyboardButton("👤 Инфо", callback_data=f"info_chat_{chat.id}"),
                    ]])
                    await self.application.bot.send_message(chat_id=self._group_id, text=self._md_to_html(text), message_thread_id=thread_id, disable_notification=False, parse_mode=ParseMode.HTML, reply_markup=take_keyboard)
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
                    [
                        InlineKeyboardButton("🙋 Взять в работу", callback_data=f"take_chat_{chat_id}"),
                        InlineKeyboardButton("👤 Инфо", callback_data=f"info_chat_{chat_id}"),
                    ],
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
                if summary:
                    notification_text += f"\n\n📝 Сводка: {summary}"
                if client_card:
                    notification_text += f"\n\n{client_card}"
                
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

    # ------------------------------------------------------------------
    # Управление из группы: команды и кнопки
    # ------------------------------------------------------------------

    async def _build_chat_summary(self, chat) -> Optional[str]:
        """AI-сводка диалога для менеджера"""
        try:
            messages = await self.db.get_chat_messages(chat.id, limit=20)
            last_user = next((m.content for m in reversed(messages) if m.message_type == "user"), None)
            if not last_user:
                return None
            prompt = (
                "Составь краткую сводку для менеджера поддержки по этому диалогу. "
                "2-3 предложения: какая у клиента проблема, что уже пробовали/отвечал бот, что вероятно нужно сделать менеджеру. "
                "Пиши сухо и по делу, без приветствий и обращений к клиенту."
            )
            ctx = {"user_id": chat.user_id, "username": chat.username, "first_name": chat.first_name, "last_name": chat.last_name}
            hist = [{"role": "user" if m.message_type == "user" else "assistant", "message": m.content} for m in messages]
            return await self.ai.get_ai_answer(prompt, ctx, hist)
        except Exception as e:
            logger.warning(f"Failed to build AI summary: {e}")
            return None

    async def _chat_from_topic(self, update: Update):
        """Определить чат клиента по топику группы (для команд внутри топика)"""
        await self.refresh_runtime_settings()
        if not (self._group_id and update.effective_chat and update.effective_chat.id == self._group_id):
            return None
        thread_id = update.message.message_thread_id if update.message else None
        if not thread_id:
            return None
        chat_id = None
        if self.redis:
            try:
                cid = self.redis.get(f"group_topic:thread:{thread_id}")
                chat_id = int(cid) if cid else None
            except Exception:
                chat_id = None
        if chat_id:
            return await self.db.get_chat_by_id(chat_id)
        # Fallback: привязка топика хранится и в БД
        from modules.database import Chat as ChatModel
        return await ChatModel.filter(topic_id=thread_id).order_by("-id").first()

    async def _resolve_command_chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Чат для команды: топик группы или аргумент <chat_id> в личке"""
        chat = await self._chat_from_topic(update)
        if chat:
            return chat
        if context.args and str(context.args[0]).isdigit():
            return await self.db.get_chat_by_id(int(context.args[0]))
        return None

    async def info_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/info — карточка клиента из Support API (в топике или /info <chat_id>)"""
        if update.effective_user.id not in self.config.get_all_staff_ids():
            return
        chat = await self._resolve_command_chat(update, context)
        if not chat:
            await update.message.reply_text("❌ Используйте команду в топике клиента или укажите ID: /info <chat_id>")
            return
        card = None
        try:
            data = await self.user_info.get_user_info(chat.user_id, force=True)
            if data:
                card = self.user_info.format_for_manager(data)
        except Exception as e:
            logger.warning(f"/info failed for chat {chat.id}: {e}")
        if card:
            await update.message.reply_text(card)
        else:
            await update.message.reply_text(
                "❌ Данные не получены: интеграция Support API выключена или клиент не найден в системе."
            )

    async def ai_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/ai on|off — включить/отключить AI для чата"""
        if update.effective_user.id not in self.config.get_all_staff_ids():
            return
        args = [a.lower() for a in (context.args or [])]
        mode = args[0] if args and args[0] in ("on", "off") else None
        if mode is None:
            await update.message.reply_text("Использование: /ai on|off — включить/отключить AI в этом чате")
            return
        chat = await self._chat_from_topic(update)
        if not chat and len(args) > 1 and args[1].isdigit():
            chat = await self.db.get_chat_by_id(int(args[1]))
        if not chat:
            await update.message.reply_text("❌ Используйте команду в топике клиента или укажите ID: /ai on|off <chat_id>")
            return
        disabled = mode == "off"
        from modules.database import Chat as ChatModel
        await ChatModel.filter(id=chat.id).update(ai_disabled=disabled)
        note = "🔇 AI отключен менеджером для этого чата" if disabled else "🔔 AI снова включен для этого чата"
        try:
            await self.db.add_message(chat.id, chat.user_id, note, "system")
        except Exception:
            pass
        await update.message.reply_text(f"{note} (чат #{chat.id})")

    async def summary_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/summary — AI-сводка диалога"""
        if update.effective_user.id not in self.config.get_all_staff_ids():
            return
        chat = await self._resolve_command_chat(update, context)
        if not chat:
            await update.message.reply_text("❌ Используйте команду в топике клиента или укажите ID: /summary <chat_id>")
            return
        summary = await self._build_chat_summary(chat)
        if summary:
            await update.message.reply_text(f"📝 Сводка по чату #{chat.id}:\n{summary}")
        else:
            await update.message.reply_text("❌ Не удалось построить сводку (нет сообщений клиента или AI недоступен).")

    async def _save_banned_users(self):
        try:
            await SystemConfig.update_or_create(
                key="banned_users",
                defaults={"value": json.dumps(sorted(self._banned_users)), "description": "Забаненные клиенты (tg id)"},
            )
        except Exception as e:
            logger.warning(f"Failed to save banned users: {e}")

    async def _resolve_ban_target(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Целевой tg id для /ban и /unban: из топика или аргумента"""
        chat = await self._chat_from_topic(update)
        if chat:
            return chat, chat.user_id
        if context.args and str(context.args[0]).lstrip("-").isdigit():
            return None, int(context.args[0])
        return None, None

    async def ban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/ban — заблокировать клиента (бот игнорирует его сообщения)"""
        if update.effective_user.id not in self.config.get_all_staff_ids():
            return
        chat, target = await self._resolve_ban_target(update, context)
        if not target:
            await update.message.reply_text("❌ Используйте команду в топике клиента или укажите ID: /ban <tg_id>")
            return
        if target in self.config.get_all_staff_ids():
            await update.message.reply_text("❌ Нельзя заблокировать сотрудника.")
            return
        self._banned_users.add(target)
        await self._save_banned_users()
        if chat:
            try:
                await self.db.add_message(chat.id, chat.user_id, "🚫 Клиент заблокирован менеджером", "system")
            except Exception:
                pass
        await update.message.reply_text(f"🚫 Клиент {target} заблокирован. Сообщения игнорируются. Разблокировать: /unban")

    async def unban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/unban — разблокировать клиента"""
        if update.effective_user.id not in self.config.get_all_staff_ids():
            return
        chat, target = await self._resolve_ban_target(update, context)
        if not target:
            await update.message.reply_text("❌ Используйте команду в топике клиента или укажите ID: /unban <tg_id>")
            return
        self._banned_users.discard(target)
        await self._save_banned_users()
        if chat:
            try:
                await self.db.add_message(chat.id, chat.user_id, "✅ Клиент разблокирован менеджером", "system")
            except Exception:
                pass
        await update.message.reply_text(f"✅ Клиент {target} разблокирован.")

    async def note_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/note <текст> — внутренняя заметка в чат (клиенту не отправляется)"""
        if update.effective_user.id not in self.config.get_all_staff_ids():
            return
        args = context.args or []
        chat = await self._chat_from_topic(update)
        if chat:
            note_text = " ".join(args).strip()
        elif len(args) >= 2 and str(args[0]).isdigit():
            chat = await self.db.get_chat_by_id(int(args[0]))
            note_text = " ".join(args[1:]).strip()
        else:
            chat, note_text = None, ""
        if not chat or not note_text:
            await update.message.reply_text("❌ Использование: /note <текст> (в топике) или /note <chat_id> <текст>")
            return
        author = update.effective_user
        author_name = f"@{author.username}" if author.username else (author.first_name or str(author.id))
        msg_text = f"📝 Заметка от {author_name}: {note_text}"
        sysmsg = None
        try:
            sysmsg = await self.db.add_message(chat.id, chat.user_id, msg_text, "system")
        except Exception as e:
            logger.warning(f"Failed to save note: {e}")
        if sysmsg and self.ws_manager:
            try:
                await self.ws_manager.broadcast(
                    "new_message",
                    {
                        "chat_id": chat.id,
                        "message": {
                            "id": sysmsg.id,
                            "text": msg_text,
                            "source": "system",
                            "created_at": sysmsg.created_at.isoformat() if sysmsg.created_at else None,
                            "media_type": None,
                            "media_file_id": None,
                        },
                    },
                )
            except Exception:
                pass
        await update.message.reply_text(f"✅ Заметка сохранена в чат #{chat.id} (клиенту не отправлена)")

    async def take_chat(self, query, chat_id: int, manager_id: int):
        """Кнопка «Взять в работу»: закрепить менеджера за чатом"""
        chat = await self.db.get_chat_by_id(chat_id)
        if not chat:
            try:
                await query.message.reply_text("❌ Чат не найден.")
            except Exception:
                pass
            return
        if chat.status != "waiting_manager":
            try:
                await query.message.reply_text(f"ℹ️ Чат #{chat_id} не ожидает менеджера (статус: {chat.status}).")
            except Exception:
                pass
            return
        if chat.manager_id and chat.manager_id != manager_id:
            try:
                await query.message.reply_text(f"⚠️ Чат #{chat_id} уже в работе у другого менеджера (ID {chat.manager_id}).")
            except Exception:
                pass
            return

        await self.db.update_chat_status(chat_id, "waiting_manager", manager_id=manager_id)
        self._set_manager_active_chat(manager_id, chat_id)

        manager = query.from_user
        mname = f"@{manager.username}" if manager.username else (manager.first_name or str(manager_id))
        sys_text = f"🙋 Менеджер {mname} взял чат в работу"
        sysmsg = None
        try:
            sysmsg = await self.db.add_message(chat_id, chat.user_id, sys_text, "system")
        except Exception:
            pass
        if self.ws_manager and sysmsg:
            try:
                await self.ws_manager.broadcast(
                    "new_message",
                    {
                        "chat_id": chat_id,
                        "message": {
                            "id": sysmsg.id,
                            "text": sys_text,
                            "source": "system",
                            "created_at": sysmsg.created_at.isoformat() if sysmsg.created_at else None,
                            "media_type": None,
                            "media_file_id": None,
                        },
                    },
                )
            except Exception:
                pass

        # Сообщение в топик + статус темы
        if self._group_id:
            try:
                thread_id = await self._ensure_group_topic(chat)
                if thread_id:
                    await self.application.bot.send_message(
                        chat_id=self._group_id, text=sys_text, message_thread_id=thread_id, disable_notification=True
                    )
                chat.status = "waiting_manager"
                chat.manager_id = manager_id
                await self._edit_group_topic_status(chat, role_hint="manager")
            except Exception as e:
                logger.warning(f"Failed to notify group on take_chat: {e}")

        # Уведомляем клиента
        try:
            await self.application.bot.send_message(
                chat_id=chat.user_id, text="👨‍💼 Менеджер подключился к чату и скоро ответит."
            )
        except Exception as e:
            logger.warning(f"Failed to notify client on take_chat: {e}")

        # Помечаем в исходном сообщении, кто взял чат
        try:
            base_text = query.message.text or ""
            if "🙋 В работе:" not in base_text:
                await query.edit_message_text(f"{base_text}\n\n🙋 В работе: {mname}")
        except Exception:
            pass

    async def send_client_card(self, query, chat_id: int):
        """Кнопка «Инфо»: карточка клиента из Support API"""
        chat = await self.db.get_chat_by_id(chat_id)
        if not chat:
            try:
                await query.message.reply_text("❌ Чат не найден.")
            except Exception:
                pass
            return
        card = None
        try:
            data = await self.user_info.get_user_info(chat.user_id, force=True)
            if data:
                card = self.user_info.format_for_manager(data)
        except Exception as e:
            logger.warning(f"Info button failed for chat {chat_id}: {e}")
        try:
            await query.message.reply_text(card or "❌ Данные не получены: интеграция выключена или клиент не найден.")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Разрушающие действия AI (сброс устройств, перевыпуск подписки):
    # подтверждение клиентом перед выполнением
    # ------------------------------------------------------------------

    def _store_pending_action(self, token: str, data: dict, ttl: int = 300):
        payload = json.dumps(data, ensure_ascii=False)
        if self.redis:
            try:
                self.redis.set(f"pending_action:{token}", payload, ex=ttl)
                return
            except Exception as e:
                logger.warning(f"Redis pending_action set failed, falling back to memory: {e}")
        self._pending_actions_mem[token] = (time.time() + ttl, data)

    def _peek_pending_action(self, token: str) -> Optional[dict]:
        """Прочитать без удаления — чтобы проверить владение до изъятия токена"""
        if self.redis:
            try:
                raw = self.redis.get(f"pending_action:{token}")
                if raw:
                    return json.loads(raw)
            except Exception as e:
                logger.warning(f"Redis pending_action peek failed: {e}")
            return None
        entry = self._pending_actions_mem.get(token)
        if entry and entry[0] > time.time():
            return entry[1]
        return None

    def _pop_pending_action(self, token: str) -> Optional[dict]:
        if self.redis:
            try:
                raw = self.redis.get(f"pending_action:{token}")
                if raw:
                    self.redis.delete(f"pending_action:{token}")
                    return json.loads(raw)
            except Exception as e:
                logger.warning(f"Redis pending_action get failed: {e}")
        entry = self._pending_actions_mem.pop(token, None)
        if entry and entry[0] > time.time():
            return entry[1]
        return None

    async def _send_pending_action_confirmation(self, update: Update, chat, action: PendingAction):
        """AI предложило разрушающее действие — просим клиента подтвердить кнопкой,
        реальный вызов Support API происходит только после клика"""
        import secrets
        token = secrets.token_urlsafe(8)
        self._store_pending_action(token, {
            "chat_id": chat.id,
            "user_id": chat.user_id,
            "action": action.action,
            "params": action.params,
        })
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Да, подтверждаю", callback_data=f"confirm_action_{token}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"cancel_action_{token}"),
        ]])
        try:
            await update.message.reply_text(action.confirm_text, reply_markup=keyboard)
        except Exception as e:
            logger.warning(f"Failed to send pending action confirmation: {e}")
        try:
            await self._save_message_to_db(chat.id, chat.user_id, {"kind": "text", "text": action.confirm_text}, "ai")
        except Exception:
            pass

    async def _confirm_pending_action(self, query, user_id: int, token: str):
        """Клиент нажал «Подтверждаю» — повторно проверяем владение и выполняем реальный вызов.
        Токен изымается (pop) только ПОСЛЕ проверки владения — иначе чужой/ошибочный клик
        по кнопке удалил бы токен и лишил настоящего владельца возможности подтвердить."""
        entry = self._peek_pending_action(token)
        if not entry:
            try:
                await query.edit_message_text("⏱ Действие устарело или уже обработано. Напишите вопрос ещё раз.")
            except Exception:
                pass
            return
        if entry.get("user_id") != user_id:
            try:
                await query.answer("Это подтверждение не для вас", show_alert=True)
            except Exception:
                pass
            return

        entry = self._pop_pending_action(token)
        if not entry:
            try:
                await query.edit_message_text("⏱ Действие уже обработано.")
            except Exception:
                pass
            return

        chat_id = entry.get("chat_id")
        action_name = entry.get("action")
        params = entry.get("params") or {}

        result_text = "❌ Неизвестное действие."
        try:
            if action_name == "reset_devices":
                username = params.get("subscription_username")
                owned = await self.user_info.find_own_subscription(user_id, username=username)
                if not owned:
                    result_text = "❌ Подписка больше не найдена среди ваших — действие отменено."
                else:
                    res = await self.user_info.reset_devices(params.get("short_id") or owned.get("short_id"))
                    result_text = (
                        "✅ Все устройства подписки сброшены."
                        if res and res.get("ok")
                        else "❌ Не удалось сбросить устройства. Попробуйте позже или обратитесь к менеджеру."
                    )
            elif action_name == "revoke_subscription":
                username = params.get("subscription_username")
                owned = await self.user_info.find_own_subscription(user_id, username=username)
                if not owned:
                    result_text = "❌ Подписка больше не найдена среди ваших — действие отменено."
                else:
                    res = await self.user_info.revoke_subscription(username)
                    if res and res.get("ok"):
                        result_text = "✅ Подписка перевыпущена. Зайдите в личный кабинет за новой ссылкой."
                    elif res and res.get("error") == "subscription_too_new":
                        remaining = max(res.get("required_age_days", 21) - res.get("age_days", 0), 1)
                        result_text = f"❌ Перевыпуск пока недоступен — подождите ещё примерно {remaining} дн."
                    else:
                        result_text = "❌ Не удалось перевыпустить подписку. Попробуйте позже или обратитесь к менеджеру."
            elif action_name == "delete_device":
                username = params.get("subscription_username")
                hwid = params.get("hwid")
                owned = await self.user_info.find_own_subscription(user_id, username=username)
                if not owned or not hwid:
                    result_text = "❌ Устройство или подписка больше не найдены — действие отменено."
                else:
                    res = await self.user_info.delete_device(username, hwid)
                    result_text = "✅ Устройство удалено." if res and res.get("ok") else "❌ Не удалось удалить устройство. Попробуйте позже."
        except Exception as e:
            logger.error(f"Pending action execution failed ({action_name}): {e}")
            result_text = "❌ Произошла ошибка при выполнении. Обратитесь к менеджеру."

        try:
            await query.edit_message_text(result_text)
        except Exception:
            try:
                await query.message.reply_text(result_text)
            except Exception:
                pass
        if chat_id:
            try:
                await self._save_message_to_db(chat_id, user_id, {"kind": "text", "text": result_text}, "system")
            except Exception:
                pass

    async def _cancel_pending_action(self, query, user_id: int, token: str):
        entry = self._peek_pending_action(token)
        if entry and entry.get("user_id") != user_id:
            try:
                await query.answer("Это не ваше подтверждение", show_alert=True)
            except Exception:
                pass
            return
        self._pop_pending_action(token)
        try:
            await query.edit_message_text("Отменено.")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Автоматизация: автозакрытие неактивных чатов и SLA-пинги
    # ------------------------------------------------------------------

    @staticmethod
    def _as_utc(dt):
        from datetime import timezone as _tz
        if dt is None:
            return None
        return dt.replace(tzinfo=_tz.utc) if dt.tzinfo is None else dt

    async def _lifecycle_loop(self):
        """Фоновый цикл: раз в минуту проверяет неактивные чаты и зависшие запросы"""
        await asyncio.sleep(15)
        logger.info("Lifecycle loop started")
        while True:
            try:
                await self.refresh_runtime_settings()
                if self._auto_close_enabled:
                    await self._run_auto_close()
                if self._sla_ping_enabled:
                    await self._run_sla_ping()
                if self._weekly_report_enabled:
                    await self._maybe_send_weekly_report()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Lifecycle loop error: {e}")
            await asyncio.sleep(60)

    async def _run_auto_close(self):
        from datetime import datetime, timedelta, timezone as _tz
        from modules.database import Chat as ChatModel, Message as MessageModel

        now = datetime.now(_tz.utc)
        reminder_delta = timedelta(minutes=self._auto_close_reminder_minutes)
        close_delta = timedelta(minutes=self._auto_close_after_minutes)

        chats = await ChatModel.filter(status__in=["active", "waiting_manager"]).all()
        for chat in chats:
            try:
                last_at = self._as_utc(chat.last_message_at)
                if not last_at:
                    continue
                reminder_at = self._as_utc(chat.reminder_sent_at)
                if reminder_at:
                    # Напоминание было — закрываем, если клиент так и не ответил
                    if now - reminder_at >= close_delta:
                        await self._auto_close_chat(chat)
                    continue
                if now - last_at < reminder_delta:
                    continue
                # Напоминаем только если последнее слово не за клиентом
                last_msg = await MessageModel.filter(chat_id=chat.id).order_by("-created_at").first()
                if not last_msg or last_msg.message_type == "user":
                    continue
                await self._send_inactivity_reminder(chat, now)
            except Exception as e:
                logger.warning(f"Auto-close check failed for chat {chat.id}: {e}")

    async def _send_inactivity_reminder(self, chat, now):
        from modules.database import Chat as ChatModel
        try:
            await self.application.bot.send_message(chat_id=chat.user_id, text=self._auto_close_reminder_text)
        except Exception as e:
            # Клиент недоступен (заблокировал бота и т.п.) — отметку всё равно ставим, чат закроется
            logger.warning(f"Failed to send inactivity reminder to {chat.user_id}: {e}")
        await ChatModel.filter(id=chat.id).update(reminder_sent_at=now)
        try:
            await self.db.add_message(chat.id, chat.user_id, "⏰ Клиенту отправлено напоминание о неактивности", "system")
        except Exception:
            pass
        logger.info(f"Inactivity reminder sent for chat {chat.id}")

    async def _auto_close_chat(self, chat):
        logger.info(f"Auto-closing chat {chat.id} due to inactivity")
        try:
            # _close_chat уже переименовывает топик под статус "closed"
            await self._close_chat(chat.id, 0, notify_text=self._auto_close_text, reason="автозакрытие по неактивности")
        except Exception as e:
            logger.warning(f"Auto-close failed for chat {chat.id}: {e}")
            return
        try:
            if self._group_id and self.redis:
                thread_id = self.redis.get(f"group_topic:chat:{chat.id}")
                if thread_id:
                    await self.application.bot.send_message(
                        chat_id=self._group_id,
                        text=f"🟢 Чат #{chat.id} закрыт автоматически (клиент не ответил)",
                        message_thread_id=int(thread_id),
                        disable_notification=True,
                    )
        except Exception as e:
            logger.warning(f"Failed to update topic on auto-close: {e}")

    async def _maybe_send_weekly_report(self):
        """Еженедельный отчет админам: по понедельникам после 06:00 UTC (09:00 МСК)"""
        from datetime import datetime, timezone as _tz
        now = datetime.now(_tz.utc)
        if now.weekday() != 0 or now.hour < 6:
            return
        week_key = now.strftime("%G-W%V")
        try:
            row = await SystemConfig.get_or_none(key="weekly_report_last_sent")
            if row and (row.value or "").strip() == week_key:
                return
        except Exception:
            return
        admin_ids = self.config.get_admin_ids()
        if not admin_ids:
            return
        try:
            from modules import stats as stats_mod
            overview = await stats_mod.collect_overview(days=7)
            topics = await stats_mod.build_topics_summary(self.ai, days=7)
            text = stats_mod.format_report_text(overview, topics)
        except Exception as e:
            logger.warning(f"Weekly report build failed: {e}")
            return
        sent = False
        for admin_id in admin_ids:
            try:
                await self.application.bot.send_message(chat_id=admin_id, text=text)
                sent = True
            except Exception as e:
                logger.warning(f"Weekly report send to {admin_id} failed: {e}")
        if sent:
            try:
                await SystemConfig.update_or_create(
                    key="weekly_report_last_sent",
                    defaults={"value": week_key, "description": "Еженедельный отчет: последняя отправка"},
                )
                logger.info(f"Weekly report sent for {week_key}")
            except Exception as e:
                logger.warning(f"Weekly report mark failed: {e}")

    async def _run_sla_ping(self):
        from datetime import datetime, timedelta, timezone as _tz
        from modules.database import Chat as ChatModel

        now = datetime.now(_tz.utc)
        threshold = timedelta(minutes=self._sla_ping_minutes)

        chats = await ChatModel.filter(status="waiting_manager", sla_notified=False, manager_id=None).all()
        for chat in chats:
            try:
                waiting_since = self._as_utc(chat.waiting_since) or self._as_utc(chat.last_message_at)
                if not waiting_since or now - waiting_since < threshold:
                    continue
                minutes = int((now - waiting_since).total_seconds() // 60)
                text = (
                    f"⏰ Запрос #{chat.id} ждёт менеджера уже {minutes} мин!\n"
                    f"Клиент: @{chat.username or 'N/A'} (ID {chat.user_id})"
                )
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🙋 Взять в работу", callback_data=f"take_chat_{chat.id}"),
                    InlineKeyboardButton("👤 Инфо", callback_data=f"info_chat_{chat.id}"),
                ]])
                if self._group_id:
                    try:
                        thread_id = await self._ensure_group_topic(chat)
                        if thread_id:
                            await self.application.bot.send_message(
                                chat_id=self._group_id, text=text, message_thread_id=thread_id,
                                disable_notification=False, reply_markup=keyboard,
                            )
                    except Exception as e:
                        logger.warning(f"SLA ping to group failed for chat {chat.id}: {e}")
                for staff_id in self.config.get_all_staff_ids():
                    try:
                        await self.application.bot.send_message(chat_id=staff_id, text=text, reply_markup=keyboard)
                    except Exception as e:
                        logger.warning(f"SLA ping to staff {staff_id} failed: {e}")
                await ChatModel.filter(id=chat.id).update(sla_notified=True)
            except Exception as e:
                logger.warning(f"SLA ping failed for chat {chat.id}: {e}")

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
            except BadRequest as e:
                if "message can't be deleted" in str(e).lower():
                    logger.debug(f"Forum service message {msg.message_id} cannot be deleted")
                else:
                    logger.warning(f"Failed to delete forum service message: {e}")
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
            # Для AI-vision берем размер до ~800px (меньше токенов), для пересылки хватает
            photo = msg.photo[0]
            for size in msg.photo:
                if (size.width or 0) <= 800:
                    photo = size
            return {"kind": "photo", "text": msg.caption or "", "file_id": photo.file_id, "message_id": msg.message_id}
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
        return msg

    async def _download_tg_file(self, file_id: str, max_size: int = 25 * 1024 * 1024):
        """Скачать файл из Telegram (для Whisper/vision)"""
        try:
            f = await self.application.bot.get_file(file_id)
            if getattr(f, "file_size", None) and f.file_size > max_size:
                logger.warning(f"Telegram file too large for AI processing: {f.file_size}")
                return None
            data = await f.download_as_bytearray()
            return bytes(data)
        except Exception as e:
            logger.warning(f"Failed to download telegram file: {e}")
            return None

    async def _transcribe_voice_message(self, info: dict):
        """Расшифровать голосовое/аудио через Whisper"""
        if not info.get("file_id"):
            return None
        data = await self._download_tg_file(info["file_id"])
        if not data:
            return None
        filename = "voice.mp4" if info.get("kind") == "video_note" else "voice.ogg"
        return await self.ai.transcribe_audio(data, filename)

    async def _photo_to_data_url(self, info: dict):
        """Фото -> data URL для vision-модели"""
        if not info.get("file_id"):
            return None
        data = await self._download_tg_file(info["file_id"], max_size=10 * 1024 * 1024)
        if not data:
            return None
        import base64
        return "data:image/jpeg;base64," + base64.b64encode(data).decode()

    async def _get_or_create_manager_header(self, client_chat_id: int, chat_id: int) -> Optional[int]:
        """Заголовок '👨‍💼 Менеджер поддержки' для режима session_header: отправляется
        один раз за сессию, message_id кешируется (Redis, либо память процесса как
        fallback) — дальнейшие сообщения менеджера идут ответом (reply) на него."""
        key = f"manager_header:{chat_id}"
        if self.redis:
            try:
                cached = self.redis.get(key)
                if cached:
                    return int(cached)
            except Exception:
                pass
        elif chat_id in self._manager_header_mem:
            return self._manager_header_mem[chat_id]

        try:
            header = await self.application.bot.send_message(chat_id=client_chat_id, text=self._manager_reply_prefix)
        except Exception as e:
            logger.warning(f"Failed to send manager header to {client_chat_id}: {e}")
            return None

        if self.redis:
            try:
                self.redis.set(key, str(header.message_id), ex=60 * 60 * 24)
            except Exception:
                pass
        else:
            self._manager_header_mem[chat_id] = header.message_id
        return header.message_id

    def _clear_manager_header(self, chat_id: int):
        """Сбросить закешированный заголовок сессии — вызывать при завершении
        сессии менеджера (возврат к AI / закрытие чата), чтобы следующая сессия
        начиналась со свежего заголовка."""
        if self.redis:
            try:
                self.redis.delete(f"manager_header:{chat_id}")
            except Exception:
                pass
        self._manager_header_mem.pop(chat_id, None)

    async def _ensure_manager_assigned(self, chat_id: int, manager_id: int):
        """Если менеджер отвечает клиенту, но чат ещё не закреплён явно (не через
        кнопку «Взять в работу» или /info) — закрепляем автоматически по факту
        первого ответа, чтобы SLA-пинг и статистика видели, кто ведёт диалог."""
        from modules.database import Chat as ChatModel
        chat = await ChatModel.get_or_none(id=chat_id)
        if not chat or chat.manager_id == manager_id:
            return
        await ChatModel.filter(id=chat_id).update(manager_id=manager_id)
        self._set_manager_active_chat(manager_id, chat_id)

    def _store_waiting_ack(self, chat_id: int, message_id: int):
        """Запомнить message_id подтверждения "✅ Ваше сообщение отправлено..." —
        чтобы удалить его, когда придёт реальный ответ менеджера"""
        if self.redis:
            try:
                self.redis.set(f"waiting_ack:{chat_id}", str(message_id), ex=60 * 60 * 24)
                return
            except Exception:
                pass
        self._waiting_ack_mem[chat_id] = message_id

    async def _delete_waiting_ack(self, client_chat_id: int, chat_id: int):
        """Best-effort удаление подтверждения о ожидании ответа, когда менеджер
        реально ответил — если не получится (сообщение старое/уже удалено/нет
        прав), просто молча пропускаем, ничего страшного."""
        message_id = None
        if self.redis:
            try:
                raw = self.redis.get(f"waiting_ack:{chat_id}")
                if raw:
                    message_id = int(raw)
                    self.redis.delete(f"waiting_ack:{chat_id}")
            except Exception:
                pass
        else:
            message_id = self._waiting_ack_mem.pop(chat_id, None)
        if not message_id:
            return
        try:
            await self.application.bot.delete_message(chat_id=client_chat_id, message_id=message_id)
        except Exception:
            pass

    async def _send_to_client(self, client_chat_id: int, info: dict, chat_id: int):
        """Отправить сообщение менеджера клиенту с учётом выбранного стиля префикса
        (combined — префикс в каждом сообщении, session_header — один раз за сессию)"""
        await self._delete_waiting_ack(client_chat_id, chat_id)
        kind = info["kind"]
        text = info.get("text") or ""
        file_id = info.get("file_id")
        prefix = self._manager_reply_prefix
        session_mode = self._manager_reply_style == "session_header"

        reply_to = None
        if session_mode:
            reply_to = await self._get_or_create_manager_header(client_chat_id, chat_id)
            caption = self._md_to_html(text) if text else None
        else:
            combined = f"{prefix}\n\n{text}" if text else prefix
            caption = self._md_to_html(combined)

        kwargs = {"reply_to_message_id": reply_to} if reply_to else {}

        if kind == "text":
            await self.application.bot.send_message(chat_id=client_chat_id, text=caption or self._md_to_html(prefix), parse_mode=ParseMode.HTML, **kwargs)
        elif kind == "photo":
            await self.application.bot.send_photo(chat_id=client_chat_id, photo=file_id, caption=caption, parse_mode=ParseMode.HTML, **kwargs)
        elif kind == "video":
            await self.application.bot.send_video(chat_id=client_chat_id, video=file_id, caption=caption, parse_mode=ParseMode.HTML, **kwargs)
        elif kind == "audio":
            await self.application.bot.send_audio(chat_id=client_chat_id, audio=file_id, caption=caption, parse_mode=ParseMode.HTML, **kwargs)
        elif kind == "voice":
            await self.application.bot.send_voice(chat_id=client_chat_id, voice=file_id, caption=caption, parse_mode=ParseMode.HTML, **kwargs)
        elif kind == "document":
            await self.application.bot.send_document(chat_id=client_chat_id, document=file_id, caption=caption, parse_mode=ParseMode.HTML, **kwargs)
        elif kind == "video_note":
            # video_note не поддерживает caption — в combined-режиме шлём префикс отдельным сообщением
            if not session_mode:
                await self.application.bot.send_message(chat_id=client_chat_id, text=self._md_to_html(f"{prefix}\n\n{text}" if text else prefix), parse_mode=ParseMode.HTML)
            await self.application.bot.send_video_note(chat_id=client_chat_id, video_note=file_id, **kwargs)
        elif kind == "sticker":
            # sticker тоже не поддерживает caption
            if not session_mode:
                await self.application.bot.send_message(chat_id=client_chat_id, text=self._md_to_html(prefix), parse_mode=ParseMode.HTML)
            await self.application.bot.send_sticker(chat_id=client_chat_id, sticker=file_id, **kwargs)
        elif kind == "animation":
            await self.application.bot.send_animation(chat_id=client_chat_id, animation=file_id, caption=caption, parse_mode=ParseMode.HTML, **kwargs)
        else:
            await self.application.bot.send_message(chat_id=client_chat_id, text=caption or self._md_to_html(text or "Сообщение"), parse_mode=ParseMode.HTML, **kwargs)

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
            # Cache name to avoid Topic_not_modified
            cache_key = f"group_topic:name:{thread_id}"
            last_name = self.redis.get(cache_key)
            if last_name == name:
                return
            await self.application.bot.edit_forum_topic(chat_id=self._group_id, message_thread_id=int(thread_id), name=name)
            self.redis.set(cache_key, name)
        except Exception as e:
            err_msg = str(e).lower()
            if "topic_not_modified" in err_msg:
                return
            if any(s in err_msg for s in ["topic_deleted", "thread not found", "invalid thread"]):
                new_thread = await self._recreate_group_topic(chat, role_hint)
                if new_thread:
                    try:
                        name = self._format_topic_title(chat, role_hint)
                        await self.application.bot.edit_forum_topic(chat_id=self._group_id, message_thread_id=int(new_thread), name=name)
                        self.redis.set(f"group_topic:name:{new_thread}", name)
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
            
            try:
                copied = await self.application.bot.copy_message(chat_id=self._group_id, from_chat_id=user.id, message_id=info["message_id"], message_thread_id=thread_id, reply_to_message_id=reply_to_id, disable_notification=mute)
            except Exception as e:
                err_lower = str(e).lower()
                if "repl" in err_lower or "not found" in err_lower:
                    # If reply failed (message deleted), send a header first, then without reply
                    header_text = f"👤 Клиент: @{user.username}" if user.username else f"👤 Клиент: {user.first_name or 'Клиент'}"
                    await self.application.bot.send_message(chat_id=self._group_id, text=header_text, message_thread_id=thread_id, disable_notification=mute)
                    copied = await self.application.bot.copy_message(chat_id=self._group_id, from_chat_id=user.id, message_id=info["message_id"], message_thread_id=thread_id, disable_notification=mute)
                else:
                    raise e

            if self.redis:
                self.redis.setex(f"group_reply:{self._group_id}:{copied.message_id}", 7 * 24 * 3600, json.dumps({"client_chat_id": chat.user_id, "client_message_id": info["message_id"], "chat_id": chat.id}))
                if reply_to_id:
                    self.redis.setex(f"group_reply:{self._group_id}:{reply_to_id}", 7 * 24 * 3600, json.dumps({"client_chat_id": chat.user_id, "client_message_id": info["message_id"], "chat_id": chat.id}))
            await self._edit_group_topic_status(chat, role_hint)
        except Exception as e:
            err_msg = str(e).lower()
            if any(s in err_msg for s in ["topic_deleted", "message thread", "thread not found", "invalid thread"]):
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
                    
                    try:
                        copied = await self.application.bot.copy_message(chat_id=self._group_id, from_chat_id=user.id, message_id=info["message_id"], message_thread_id=new_thread, reply_to_message_id=reply_to_id, disable_notification=mute)
                    except Exception as e_copy:
                        if "reply" in str(e_copy).lower() or "not found" in str(e_copy).lower():
                            header_text = f"👤 Клиент: @{user.username}" if user.username else f"👤 Клиент: {user.first_name or 'Клиент'}"
                            await self.application.bot.send_message(chat_id=self._group_id, text=header_text, message_thread_id=new_thread, disable_notification=mute)
                            copied = await self.application.bot.copy_message(chat_id=self._group_id, from_chat_id=user.id, message_id=info["message_id"], message_thread_id=new_thread, disable_notification=mute)
                        else:
                            raise e_copy

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
            if self._client_ack_enabled:
                try:
                    ack = await update.message.reply_text("✅ Ваше сообщение отправлено в поддержку. Ожидайте ответа.")
                    self._store_waiting_ack(chat.id, ack.message_id)
                except Exception:
                    pass
            return True
        manager_id = chat.manager_id
        if not manager_id:
            if self._client_ack_enabled:
                try:
                    ack = await update.message.reply_text("✅ Ваше сообщение сохранено. Менеджер скоро ответит.")
                    self._store_waiting_ack(chat.id, ack.message_id)
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
        if self._client_ack_enabled:
            try:
                ack = await update.message.reply_text("✅ Ваше сообщение отправлено менеджеру. Ожидайте ответа.")
                self._store_waiting_ack(chat.id, ack.message_id)
            except Exception:
                pass
        return True

    async def handle_any_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = user.id
        info = self._extract_message_info(update)
        if not info:
            return
        await self.refresh_runtime_settings()
        # Забаненные клиенты игнорируются
        if user_id in self._banned_users and user_id not in self.config.get_all_staff_ids():
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
                except BadRequest as e:
                    if "message can't be deleted" not in str(e).lower():
                        logger.warning(f"Failed to delete forum service message: {e}")
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
                await self._send_to_client(client_chat_id, info, chat_id)
                await self._ensure_manager_assigned(chat_id, user_id)
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
                    await self._send_to_client(client_chat_id, info, chat_id)
                    await self._ensure_manager_assigned(chat_id, user_id)
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
                    await self._send_to_client(manager_chat.user_id, info, manager_chat.id)
                    await self._ensure_manager_assigned(manager_chat.id, user_id)
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
        saved_msg = await self._save_message_to_db(chat.id, user_id, info, "user")
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
        # AI отключен менеджером (/ai off) — бот молчит, диалог ведет менеджер через топик
        if getattr(chat, "ai_disabled", False):
            return
        # Определяем вопрос для AI: текст, расшифровка голосового или изображение
        question_text = None
        image_data_url = None
        transcript = None
        if info["kind"] == "text":
            question_text = info["text"]
        elif info["kind"] in ("voice", "audio", "video_note") and self._ai_voice_enabled:
            typing_stop, typing_task = self._start_typing(update.effective_chat.id if update.effective_chat else user_id)
            try:
                transcript = await self._transcribe_voice_message(info)
            finally:
                await self._stop_typing(typing_stop, typing_task)
            if transcript:
                question_text = transcript
        elif info["kind"] == "photo" and self._ai_vision_enabled:
            typing_stop, typing_task = self._start_typing(update.effective_chat.id if update.effective_chat else user_id)
            try:
                image_data_url = await self._photo_to_data_url(info)
            finally:
                await self._stop_typing(typing_stop, typing_task)
            if image_data_url:
                question_text = (info.get("text") or "").strip() or (
                    "Клиент прислал изображение (вероятно, скриншот ошибки или экрана приложения). "
                    "Определи, что на нём изображено, и помоги решить проблему."
                )

        if transcript:
            # Сохраняем расшифровку в историю (веб-панель и контекст AI)
            try:
                from modules.database import Message as MessageModel
                if saved_msg:
                    await MessageModel.filter(id=saved_msg.id).update(content=f"[voice] {transcript}", text=transcript)
            except Exception:
                pass
            if self._group_id:
                try:
                    thread_id = await self._ensure_group_topic(chat)
                    if thread_id:
                        await self.application.bot.send_message(
                            chat_id=self._group_id, text=f"🎙 Расшифровка: {transcript}",
                            message_thread_id=thread_id, disable_notification=True,
                        )
                except Exception as e:
                    logger.warning(f"Failed to send transcript to group: {e}")

        if question_text:
            if await self._maybe_send_device_delete_confirmation(update, chat, user_id, question_text):
                return
            typing_stop, typing_task = self._start_typing(update.effective_chat.id if update.effective_chat else user_id)
            try:
                chat_messages = await self.db.get_chat_messages(chat.id, limit=20)
                chat_history = [{"role": "user" if msg.message_type == "user" else "assistant", "message": msg.content} for msg in chat_messages]
                context_info = {"user_id": user_id, "username": user.username, "first_name": user.first_name, "last_name": user.last_name}
                # Данные аккаунта клиента из Support API (баланс, подписки, ключи)
                try:
                    account_info = await self.user_info.get_ai_context(user_id)
                    if account_info:
                        context_info["account_info"] = account_info
                except Exception as e:
                    logger.warning(f"Support API context failed for user {user_id}: {e}")
                ai_response = await self.ai.get_ai_answer(
                    question_text, context_info, chat_history, image_data_url=image_data_url,
                    execute_tools=True, user_info_service=self.user_info,
                )
            finally:
                await self._stop_typing(typing_stop, typing_task)
            if isinstance(ai_response, PendingAction):
                await self._send_pending_action_confirmation(update, chat, ai_response)
            elif ai_response:
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
