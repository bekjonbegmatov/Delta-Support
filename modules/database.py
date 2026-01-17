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
    username = fields.CharField(max_length=255, null=True)
    first_name = fields.CharField(max_length=255, null=True)
    last_name = fields.CharField(max_length=255, null=True)
    status = fields.CharField(max_length=50, default="active")  # active, waiting_manager, closed
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    manager_id = fields.BigIntField(null=True)
    
    # Reverse relations
    messages: fields.ReverseRelation["Message"]
    notifications: fields.ReverseRelation["ManagerNotification"]

    class Meta:
        table = "chats"

class Message(Model):
    """Модель сообщения"""
    id = fields.IntField(pk=True)
    chat = fields.ForeignKeyField('models.Chat', related_name='messages', index=True)
    user_id = fields.BigIntField()
    message_type = fields.CharField(max_length=50, default="user")  # user, ai, manager
    content = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True, index=True)

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
    
    async def create_chat(self, user_id: int, username: str = None, 
                         first_name: str = None, last_name: str = None) -> Chat:
        """Создать новый чат"""
        chat = await Chat.create(
            user_id=user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            status="active"
        )
        return chat
    
    async def get_chat_by_user_id(self, user_id: int) -> Optional[Chat]:
        """Получить активный чат пользователя"""
        return await Chat.filter(
            user_id=user_id,
            status__in=["active", "waiting_manager"]
        ).order_by("-created_at").first()
    
    async def get_chat_by_id(self, chat_id: int) -> Optional[Chat]:
        """Получить чат по ID"""
        return await Chat.get_or_none(id=chat_id)
    
    async def add_message(self, chat_id: int, user_id: int, 
                         content: str, message_type: str = "user") -> Message:
        """Добавить сообщение в чат"""
        message = await Message.create(
            chat_id=chat_id,
            user_id=user_id,
            message_type=message_type,
            content=content
        )
        return message
    
    async def get_chat_messages(self, chat_id: int, limit: int = 50) -> List[Message]:
        """Получить сообщения чата"""
        return await Message.filter(chat_id=chat_id).order_by("created_at").limit(limit).all()
    
    async def update_chat_status(self, chat_id: int, status: str, manager_id: int = None):
        """Обновить статус чата"""
        update_data = {"status": status}
        if manager_id is not None:
            update_data["manager_id"] = manager_id
        
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
