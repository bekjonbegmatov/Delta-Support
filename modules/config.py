"""
Модуль конфигурации приложения
"""

import os
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Config(BaseSettings):
    """Класс конфигурации приложения"""
    
    # Project info
    project_name: str = Field(default="STELS-Support", alias="PROJECT_NAME")
    project_description: str = Field(default="", alias="PROJECT_DESCRIPTION")
    project_website: str = Field(default="", alias="PROJECT_WEBSITE")
    project_bot_link: str = Field(default="", alias="PROJECT_BOT_LINK")
    project_owner_contacts: str = Field(default="", alias="PROJECT_OWNER_CONTACTS")
    
    # AI Configuration
    ai_support_enabled: bool = Field(default=True, alias="AI_SUPPORT_ENABLED")
    ai_support_api_type: str = Field(default="groq", alias="AI_SUPPORT_API_TYPE")
    ai_support_api_key: str = Field(default="", alias="AI_SUPPORT_API_KEY")
    
    # Database
    database_url: str = Field(
        default="postgresql://stels_support:stels_support_password@postgres:5432/stels_support",
        alias="DATABASE_URL"
    )
    postgres_user: str = Field(default="stels_support", alias="POSTGRES_USER")
    postgres_password: str = Field(default="stels_support_password", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="stels_support", alias="POSTGRES_DB")
    
    # Telegram Bot
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_admin_ids: str = Field(default="", alias="TELEGRAM_ADMIN_IDS")
    telegram_manager_ids: str = Field(default="", alias="TELEGRAM_MANAGER_IDS")
    
    # Project Databases
    project_db_1: Optional[str] = Field(default=None, alias="PROJECT_DB_1")
    project_db_2: Optional[str] = Field(default=None, alias="PROJECT_DB_2")
    project_db_3: Optional[str] = Field(default=None, alias="PROJECT_DB_3")
    
    # Redis
    redis_host: str = Field(default="redis", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_password: Optional[str] = Field(default=None, alias="REDIS_PASSWORD")
    
    # App Settings
    app_port: int = Field(default=8080, alias="APP_PORT")
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    
    # JWT
    jwt_secret_key: str = Field(default="", alias="JWT_SECRET_KEY")
    
    class Config:
        env_file = ".env"
        case_sensitive = False
    
    def get_admin_ids(self) -> List[int]:
        """Получить список ID администраторов"""
        if not self.telegram_admin_ids:
            return []
        return [int(id.strip()) for id in self.telegram_admin_ids.split(",") if id.strip()]
    
    def get_manager_ids(self) -> List[int]:
        """Получить список ID менеджеров"""
        if not self.telegram_manager_ids:
            return []
        return [int(id.strip()) for id in self.telegram_manager_ids.split(",") if id.strip()]
    
    def get_all_staff_ids(self) -> List[int]:
        """Получить список всех сотрудников (админы + менеджеры)"""
        return list(set(self.get_admin_ids() + self.get_manager_ids()))
    
    def get_project_databases(self) -> List[str]:
        """Получить список баз данных проектов"""
        dbs = []
        for db in [self.project_db_1, self.project_db_2, self.project_db_3]:
            if db:
                dbs.append(db)
        return dbs
