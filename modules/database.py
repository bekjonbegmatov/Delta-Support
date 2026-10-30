"""
Модуль работы с базой данных (Tortoise ORM)
Поддерживает PostgreSQL и SQLite
"""

import logging
from typing import Optional, List
from tortoise import Tortoise, fields
from tortoise.models import Model
from tortoise.expressions import Q
from modules.config import Config

logger = logging.getLogger(__name__)

class Chat(Model):
    """Модель чата"""
    id = fields.IntField(pk=True)
    user_id = fields.BigIntField(index=True)
    user_tg_id = fields.BigIntField(index=True, null=True)
    username = fields.CharField(max_length=255, null=True)
    first_name = fields.CharField(max_length=255, null=True)
    last_name = fields.CharField(max_length=255, null=True)
    status = fields.CharField(max_length=50, default="active")  # active, waiting_manager, closed
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    manager_id = fields.BigIntField(null=True)
    assigned_admin_id = fields.IntField(null=True)
    last_message_at = fields.DatetimeField(null=True)
    topic_id = fields.BigIntField(null=True)
    topic_group_id = fields.BigIntField(null=True)
    # AI отключен менеджером для этого чата (/ai off)
    ai_disabled = fields.BooleanField(default=False)
    # Автозакрытие: когда отправлено напоминание клиенту
    reminder_sent_at = fields.DatetimeField(null=True)
    # SLA: с какого момента чат ждет менеджера
    waiting_since = fields.DatetimeField(null=True)
    sla_notified = fields.BooleanField(default=False)
    # Когда чат последний раз открывали в панели — для счётчика непрочитанных
    last_viewed_at = fields.DatetimeField(null=True)

    # Reverse relations
    messages: fields.ReverseRelation["Message"]
    notifications: fields.ReverseRelation["ManagerNotification"]

    class Meta:
        table = "chats"

class PaymentOperation(Model):
    """Durable checkout intent: retries reuse an immutable key and request body."""
    id = fields.IntField(pk=True)
    chat = fields.ForeignKeyField('models.Chat', related_name='payment_operations')
    user_id = fields.BigIntField(index=True)  # Telegram ID, never a local user record ID
    method = fields.CharField(max_length=100)
    method_title = fields.CharField(max_length=255)
    amount = fields.IntField()
    currency = fields.CharField(max_length=12, default="RUB")
    idempotency_key = fields.CharField(max_length=36, unique=True, null=True)
    state = fields.CharField(max_length=30, default="proposed")
    result = fields.JSONField(null=True)  # Creation snapshot, never current payment status
    started_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "payment_operations"


class Message(Model):
    """Модель сообщения"""
    id = fields.IntField(pk=True)
    chat = fields.ForeignKeyField('models.Chat', related_name='messages', index=True)
    user_id = fields.BigIntField()
    message_type = fields.CharField(max_length=50, default="user")  # user, ai, manager
    content = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True, index=True)
    # Новые поля по ТЗ
    source = fields.CharField(max_length=50, null=True)  # user | manager_web | manager_group | ai | ai_tool | system
    text = fields.TextField(null=True)
    media_type = fields.CharField(max_length=20, null=True)  # photo | video | document | audio | voice | sticker | none
    media_file_id = fields.CharField(max_length=255, null=True)
    tg_message_id_user = fields.BigIntField(null=True)
    tg_message_id_group = fields.BigIntField(null=True)
    admin_user_id = fields.IntField(null=True)
    client_event_id = fields.CharField(max_length=64, null=True)

    class Meta:
        table = "messages"

class ManagerNotification(Model):
    """Модель уведомления менеджера"""
    id = fields.IntField(pk=True)
    chat = fields.ForeignKeyField('models.Chat', related_name='notifications', index=True)
    manager_id = fields.BigIntField(index=True)
    status = fields.CharField(max_length=50, default="pending")  # pending, viewed, accepted
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "manager_notifications"

class AdminUser(Model):
    """Модель пользователя админ-панели"""
    id = fields.IntField(pk=True)
    username = fields.CharField(max_length=50, unique=True)
    password_hash = fields.CharField(max_length=255)
    role = fields.CharField(max_length=20, default="manager")  # admin, manager
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    last_login = fields.DatetimeField(null=True)
    access_start_hour = fields.IntField(null=True)
    access_end_hour = fields.IntField(null=True)

    class Meta:
        table = "admin_users"

class SystemConfig(Model):
    """Модель системных настроек"""
    key = fields.CharField(max_length=100, pk=True)
    value = fields.TextField()
    description = fields.CharField(max_length=255, null=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "system_config"

class ProjectDatabase(Model):
    """Модель подключенных баз данных проектов"""
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=255, null=True)
    connection_string = fields.CharField(max_length=500, null=True)
    db_type = fields.CharField(max_length=50, null=True)  # postgresql, sqlite
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "project_databases"

class KnowledgeBaseEntry(Model):
    id = fields.IntField(pk=True)
    title = fields.CharField(max_length=255)
    content = fields.TextField()
    is_active = fields.BooleanField(default=True)
    updated_at = fields.DatetimeField(auto_now=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "knowledge_base"

class KnowledgeGap(Model):
    """Темы, которых AI не нашёл в базе знаний; ответы публикует только администратор."""
    id = fields.IntField(pk=True)
    fingerprint = fields.CharField(max_length=64, unique=True)
    topic = fields.CharField(max_length=255)
    reason = fields.TextField()
    example_question = fields.TextField()
    status = fields.CharField(max_length=20, default="open", index=True)
    mention_count = fields.IntField(default=0)
    last_chat = fields.ForeignKeyField('models.Chat', related_name='knowledge_gaps', null=True, on_delete=fields.SET_NULL)
    entry = fields.ForeignKeyField('models.KnowledgeBaseEntry', related_name='suggestions', null=True, on_delete=fields.SET_NULL)
    created_at = fields.DatetimeField(auto_now_add=True)
    last_seen_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "knowledge_gaps"


class KnowledgeGapObservation(Model):
    """Один голос за недостающую тему на чат, независимо от повторов tool call."""
    id = fields.IntField(pk=True)
    gap = fields.ForeignKeyField('models.KnowledgeGap', related_name='observations')
    chat = fields.ForeignKeyField('models.Chat', related_name='knowledge_gap_observations')

    class Meta:
        table = "knowledge_gap_observations"
        unique_together = (("gap", "chat"),)


class AIProvider(Model):
    """Модель провайдера AI"""
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=100)
    api_type = fields.CharField(max_length=50, default="openai")  # openai, groq, anthropic, gemini, deepseek, mistral, claude
    api_key = fields.CharField(max_length=255)  # Основной API ключ
    api_keys = fields.JSONField(null=True)  # Дополнительные API ключи (для балансировки/резерва)
    base_url = fields.CharField(max_length=255, null=True)
    model_name = fields.CharField(max_length=100)
    is_active = fields.BooleanField(default=True)
    priority = fields.IntField(default=10)  # Чем меньше, тем выше приоритет
    max_requests_per_minute = fields.IntField(default=60)  # Лимит запросов в минуту
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "ai_providers"

class Database:
    """Класс для работы с базой данных (Wrapper для Tortoise ORM)"""
    
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
    
    async def initialize(self):
        """Инициализация подключения к базе данных"""
        db_url = self.config.database_url
        
        # Корректировка URL для Tortoise
        # Tortoise использует postgres:// вместо postgresql://
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgres://")
        
        # Для SQLite формат sqlite://path/to/db.sqlite3
        
        logger.info(f"Initializing Database with URL type: {db_url.split(':')[0]}")
        
        await Tortoise.init(
            db_url=db_url,
            modules={'models': ['modules.database']}
        )
        
        # Генерируем схему (создаем таблицы)
        await Tortoise.generate_schemas()
        logger.info("Database initialized and schemas generated")
        
        # Пытаться апгрейдить схему для существующих таблиц (SQLite без мигратора)
        try:
            if db_url.startswith("sqlite"):
                conn = Tortoise.get_connection("default")
                
                # Функция для безопасного добавления колонки
                async def safe_add_column(table: str, column: str):
                    try:
                        # Проверяем существует ли колонка
                        result = await conn.execute_query(f"PRAGMA table_info({table})")
                        existing_cols = [row[1] for row in result[1]] if result[1] else []
                        col_name = column.split()[0]  # Имя колонки до типа данных
                        if col_name not in existing_cols:
                            await conn.execute_script(f"ALTER TABLE {table} ADD COLUMN {column}")
                            logger.info(f"Added column {column} to {table}")
                    except Exception as e:
                        logger.debug(f"Column check/add error for {table}.{column}: {e}")
                
                # Chat доп. колонки
                for column in [
                    "user_tg_id INTEGER",
                    "assigned_admin_id INTEGER",
                    "last_message_at TEXT",
                    "topic_id INTEGER",
                    "topic_group_id INTEGER",
                    "ai_disabled INTEGER DEFAULT 0",
                    "reminder_sent_at TEXT",
                    "waiting_since TEXT",
                    "sla_notified INTEGER DEFAULT 0",
                    "last_viewed_at TEXT",
                ]:
                    await safe_add_column("chats", column)
                
                # Message доп. колонки
                for column in [
                    "source TEXT",
                    "text TEXT",
                    "media_type TEXT",
                    "media_file_id TEXT",
                    "tg_message_id_user INTEGER",
                    "tg_message_id_group INTEGER",
                    "admin_user_id INTEGER",
                    "client_event_id TEXT",
                ]:
                    await safe_add_column("messages", column)
                
                # AdminUser доп. колонки
                for column in [
                    "access_start_hour INTEGER",
                    "access_end_hour INTEGER",
                ]:
                    await safe_add_column("admin_users", column)
                
                # AIProvider доп. колонки (новые)
                for column in [
                    "api_keys TEXT",  # JSON для дополнительных ключей
                    "max_requests_per_minute INTEGER DEFAULT 60",
                ]:
                    await safe_add_column("ai_providers", column)
                
                # AIProvider table (manual creation if not exists)
                try:
                    result = await conn.execute_query("""
                        SELECT name FROM sqlite_master WHERE type='table' AND name='ai_providers'
                    """)
                    if not result[1]:
                        await conn.execute_script("""
                            CREATE TABLE IF NOT EXISTS ai_providers (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                name VARCHAR(100) NOT NULL,
                                api_type VARCHAR(50) NOT NULL DEFAULT 'openai',
                                api_key VARCHAR(255) NOT NULL,
                                api_keys TEXT,
                                base_url VARCHAR(255),
                                model_name VARCHAR(100) NOT NULL,
                                is_active BOOLEAN NOT NULL DEFAULT 1,
                                priority INTEGER NOT NULL DEFAULT 10,
                                max_requests_per_minute INTEGER DEFAULT 60,
                                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                            );
                        """)
                        logger.info("Created ai_providers table")
                except Exception as e:
                    logger.warning(f"Error checking/creating ai_providers table: {e}")
                    
        except Exception as e:
            logger.warning(f"SQLite schema upgrade error: {e}")

        # Апгрейд схемы для PostgreSQL (новые колонки, generate_schemas их не добавляет)
        try:
            if db_url.startswith("postgres"):
                conn = Tortoise.get_connection("default")
                for table, column in [
                    ("chats", "topic_group_id BIGINT"),
                    ("chats", "ai_disabled BOOLEAN DEFAULT FALSE"),
                    ("chats", "reminder_sent_at TIMESTAMPTZ"),
                    ("chats", "waiting_since TIMESTAMPTZ"),
                    ("chats", "sla_notified BOOLEAN DEFAULT FALSE"),
                    ("chats", "last_viewed_at TIMESTAMPTZ"),
                ]:
                    try:
                        await conn.execute_script(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column}")
                    except Exception as e:
                        logger.debug(f"PG column add error {table}: {e}")
        except Exception as e:
            logger.warning(f"Postgres schema upgrade error: {e}")

    async def create_chat(self, user_id: int, username: str = None, 
                         first_name: str = None, last_name: str = None) -> Chat:
        """Создать новый чат"""
        chat = await Chat.create(
            user_id=user_id,
            user_tg_id=user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            status="active"
        )
        return chat
    
    async def get_chat_by_user_id(self, user_id: int) -> Optional[Chat]:
        """Вернуть текущий диалог, включая закрытый до истечения срока хранения."""
        chat = await Chat.filter(
            user_id=user_id, status__in=["active", "waiting_manager"]
        ).order_by("-updated_at", "-id").first()
        return chat or await Chat.filter(user_id=user_id).order_by("-updated_at", "-id").first()

    async def reopen_chat(self, chat: Chat) -> Chat:
        """Новый вопрос продолжает тот же чат и привязанный Telegram-топик."""
        if chat.status == "closed":
            from datetime import datetime, timezone
            updates = dict(status="active", manager_id=None, assigned_admin_id=None,
                           ai_disabled=False, reminder_sent_at=None, waiting_since=None,
                           sla_notified=False, updated_at=datetime.now(timezone.utc))
            await Chat.filter(id=chat.id, status="closed").update(**updates)
            await chat.refresh_from_db()
        return chat

    async def get_chat_by_id(self, chat_id: int) -> Optional[Chat]:
        """Получить чат по ID"""
        return await Chat.get_or_none(id=chat_id)
    
    async def add_message(self, chat_id: int, user_id: int, 
                         content: str, message_type: str = "user", source: Optional[str] = None) -> Message:
        """Добавить сообщение в чат"""
        # Маппинг старой схемы в новую
        source = source or {
            "user": "user",
            "ai": "ai",
            "manager": "manager_web"
        }.get(message_type, "system")
        message = await Message.create(
            chat_id=chat_id,
            user_id=user_id,
            message_type=message_type,
            content=content,
            source=source,
            text=content
        )
        # Обновляем last_message_at; ответ клиента сбрасывает таймер напоминания.
        # updated_at обновляем явно — bulk .update() не триггерит Tortoise auto_now,
        # а именно по updated_at список чатов в панели сортируется "новые сверху"
        chat_update = {"updated_at": message.created_at}
        if message_type in {"user", "ai", "manager"} and source not in {"system", "ai_tool"}:
            chat_update["last_message_at"] = message.created_at
        if message_type == "user":
            chat_update["reminder_sent_at"] = None
        await Chat.filter(id=chat_id).update(**chat_update)
        return message
    
    async def get_chat_messages(self, chat_id: int, limit: int = 50) -> List[Message]:
        """Последние сообщения в хронологическом порядке, без потери нового контекста."""
        messages = await Message.filter(chat_id=chat_id).order_by("-created_at", "-id").limit(limit).all()
        return list(reversed(messages))
    
    async def update_chat_status(self, chat_id: int, status: str, manager_id: int = None):
        """Обновить статус чата"""
        from datetime import datetime, timezone
        update_data = {"status": status, "updated_at": datetime.now(timezone.utc), "reminder_sent_at": None}
        if manager_id is not None:
            update_data["manager_id"] = manager_id
        # Таймеры SLA и автозакрытия
        if status == "waiting_manager":
            update_data["waiting_since"] = datetime.now(timezone.utc)
            update_data["sla_notified"] = False
        else:
            update_data["manager_id"] = None
            update_data["assigned_admin_id"] = None
            update_data["waiting_since"] = None
            update_data["sla_notified"] = False
            update_data["reminder_sent_at"] = None

        await Chat.filter(id=chat_id).update(**update_data)
    
    async def create_manager_notification(self, chat_id: int, manager_id: int) -> ManagerNotification:
        """Создать уведомление для менеджера"""
        return await ManagerNotification.create(
            chat_id=chat_id,
            manager_id=manager_id,
            status="pending"
        )
    
    async def get_all_chats(self, status: str = None) -> List[Chat]:
        """Получить все чаты (для админов/менеджеров)"""
        query = Chat.all().order_by("-updated_at")
        if status:
            query = query.filter(status=status)
        return await query
    
    async def close(self):
        """Закрыть подключение к базе данных"""
        await Tortoise.close_connections()
