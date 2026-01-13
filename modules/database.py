"""
Модуль работы с базой данных
"""

import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, BigInteger, String, Text, DateTime, Boolean, ForeignKey, JSON, select, update
from datetime import datetime
from typing import Optional, List, Dict
import json

from modules.config import Config

Base = declarative_base()


class Chat(Base):
    """Модель чата"""
    __tablename__ = "chats"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    username = Column(String(255))
    first_name = Column(String(255))
    last_name = Column(String(255))
    status = Column(String(50), default="active")  # active, waiting_manager, closed
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    manager_id = Column(BigInteger, nullable=True)  # ID менеджера, подключенного к чату


class Message(Base):
    """Модель сообщения"""
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False)
    message_type = Column(String(50), default="user")  # user, ai, manager
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ManagerNotification(Base):
    """Модель уведомления менеджера"""
    __tablename__ = "manager_notifications"
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False, index=True)
    manager_id = Column(BigInteger, nullable=False, index=True)
    status = Column(String(50), default="pending")  # pending, viewed, accepted
    created_at = Column(DateTime, default=datetime.utcnow)


class ProjectDatabase(Base):
    """Модель подключенных баз данных проектов"""
    __tablename__ = "project_databases"
    
    id = Column(Integer, primary_key=True)
    name = Column(String(255))
    connection_string = Column(String(500))
    db_type = Column(String(50))  # postgresql, sqlite
    created_at = Column(DateTime, default=datetime.utcnow)


class Database:
    """Класс для работы с базой данных"""
    
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.engine = None
        self.session_factory = None
    
    async def initialize(self):
        """Инициализация подключения к базе данных"""
        # Конвертируем postgresql:// в postgresql+asyncpg:// для async
        database_url = self.config.database_url
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        
        self.engine = create_async_engine(
            database_url,
            echo=self.config.debug,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20
        )
        
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        
        # Создание таблиц
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    
    def get_session(self):
        """Получить сессию базы данных (async context manager)"""
        return self.session_factory()
    
    async def create_chat(self, user_id: int, username: str = None, 
                         first_name: str = None, last_name: str = None) -> Chat:
        """Создать новый чат"""
        async with self.get_session() as session:
            chat = Chat(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                status="active"
            )
            session.add(chat)
            await session.commit()
            await session.refresh(chat)
            return chat
    
    async def get_chat_by_user_id(self, user_id: int) -> Optional[Chat]:
        """Получить активный чат пользователя"""
        async with self.get_session() as session:
            result = await session.execute(
                select(Chat).where(
                    Chat.user_id == user_id,
                    Chat.status.in_(["active", "waiting_manager"])
                ).order_by(Chat.created_at.desc())
            )
            return result.scalar_one_or_none()
    
    async def get_chat_by_id(self, chat_id: int) -> Optional[Chat]:
        """Получить чат по ID"""
        async with self.get_session() as session:
            result = await session.execute(
                select(Chat).where(Chat.id == chat_id)
            )
            return result.scalar_one_or_none()
    
    async def add_message(self, chat_id: int, user_id: int, 
                         content: str, message_type: str = "user") -> Message:
        """Добавить сообщение в чат"""
        async with self.get_session() as session:
            message = Message(
                chat_id=chat_id,
                user_id=user_id,
                message_type=message_type,
                content=content
            )
            session.add(message)
            await session.commit()
            await session.refresh(message)
            return message
    
    async def get_chat_messages(self, chat_id: int, limit: int = 50) -> List[Message]:
        """Получить сообщения чата"""
        async with self.get_session() as session:
            result = await session.execute(
                select(Message)
                .where(Message.chat_id == chat_id)
                .order_by(Message.created_at.asc())
                .limit(limit)
            )
            return list(result.scalars().all())
    
    async def update_chat_status(self, chat_id: int, status: str, manager_id: int = None):
        """Обновить статус чата"""
        async with self.get_session() as session:
            await session.execute(
                update(Chat)
                .where(Chat.id == chat_id)
                .values(status=status, manager_id=manager_id, updated_at=datetime.utcnow())
            )
            await session.commit()
    
    async def create_manager_notification(self, chat_id: int, manager_id: int) -> ManagerNotification:
        """Создать уведомление для менеджера"""
        async with self.get_session() as session:
            notification = ManagerNotification(
                chat_id=chat_id,
                manager_id=manager_id,
                status="pending"
            )
            session.add(notification)
            await session.commit()
            await session.refresh(notification)
            return notification
    
    async def get_all_chats(self, status: str = None) -> List[Chat]:
        """Получить все чаты (для админов/менеджеров)"""
        async with self.get_session() as session:
            query = select(Chat)
            if status:
                query = query.where(Chat.status == status)
            query = query.order_by(Chat.updated_at.desc())
            result = await session.execute(query)
            return list(result.scalars().all())
    
    async def close(self):
        """Закрыть подключение к базе данных"""
        if self.engine:
            await self.engine.dispose()
