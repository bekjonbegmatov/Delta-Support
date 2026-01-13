"""
Модуль AI поддержки для ответов на вопросы клиентов
Поддерживает Groq API и доступ к базам данных проектов
"""

import os
import logging
import requests
import json
import asyncpg
import aiosqlite
from typing import Optional, Dict, List
from sqlalchemy import create_engine, text
from modules.config import Config

logger = logging.getLogger(__name__)


class AISupport:
    """Класс для работы с AI поддержкой"""
    
    def __init__(self, config: Config):
        self.config = config
        self.enabled = config.ai_support_enabled
        self.api_type = config.ai_support_api_type
        self.api_key = config.ai_support_api_key
        self.project_databases = config.get_project_databases()
    
    async def get_ai_answer(
        self, 
        question: str, 
        context: Optional[Dict] = None, 
        chat_history: Optional[List[Dict]] = None
    ) -> Optional[str]:
        """
        Получить ответ от AI на вопрос пользователя
        
        Args:
            question: Вопрос пользователя
            context: Контекст (информация о пользователе, проекте и т.д.)
            chat_history: История сообщений чата
        
        Returns:
            Ответ от AI или None в случае ошибки
        """
        if not self.enabled:
            return None
        
        # Проверяем API ключ только для внешних API (не для rule-based)
        if self.api_type != "rule-based" and not self.api_key:
            logger.warning("AI_SUPPORT_API_KEY не установлен для выбранного типа AI API")
            return None
        
        try:
            if self.api_type == "groq":
                return await self._get_groq_answer(question, context, chat_history)
            elif self.api_type == "rule-based":
                return self._get_rule_based_answer(question, context, chat_history)
            else:
                logger.warning(f"Неизвестный тип AI API: {self.api_type}")
                return None
        except Exception as e:
            logger.error(f"Ошибка при получении ответа от AI: {e}")
            return None
    
    def _build_service_context(self, context: Optional[Dict] = None) -> str:
        """Построить контекст о сервисе для AI"""
        service_info = []
        
        # Информация о проекте
        if self.config.project_name:
            service_info.append(f"Название проекта: {self.config.project_name}")
        if self.config.project_description:
            service_info.append(f"Описание: {self.config.project_description}")
        if self.config.project_website:
            service_info.append(f"Сайт: {self.config.project_website}")
        if self.config.project_bot_link:
            service_info.append(f"Бот: {self.config.project_bot_link}")
        if self.config.project_owner_contacts:
            service_info.append(f"Контакты владельца: {self.config.project_owner_contacts}")
        
        if context:
            # Информация о пользователе
            if context.get("user_id"):
                service_info.append(f"ID пользователя: {context.get('user_id')}")
            if context.get("username"):
                service_info.append(f"Username: {context.get('username')}")
        
        return "\n".join(service_info) if service_info else ""
    
    async def _get_project_data(self, query: str) -> Optional[str]:
        """Получить данные из баз данных проектов"""
        if not self.project_databases:
            return None
        
        results = []
        for db_url in self.project_databases:
            try:
                if db_url.startswith("postgresql://"):
                    data = await self._query_postgres(db_url, query)
                elif db_url.startswith("sqlite:///"):
                    data = await self._query_sqlite(db_url, query)
                else:
                    continue
                
                if data:
                    results.append(f"Данные из БД: {json.dumps(data, ensure_ascii=False)}")
            except Exception as e:
                logger.error(f"Ошибка запроса к БД проекта: {e}")
                continue
        
        return "\n".join(results) if results else None
    
    async def _query_postgres(self, db_url: str, query: str) -> Optional[Dict]:
        """Выполнить запрос к PostgreSQL"""
        try:
            # Парсим URL
            # postgresql://user:pass@host:port/dbname
            parts = db_url.replace("postgresql://", "").split("@")
            if len(parts) != 2:
                return None
            
            user_pass = parts[0].split(":")
            host_db = parts[1].split("/")
            host_port = host_db[0].split(":")
            
            user = user_pass[0]
            password = user_pass[1] if len(user_pass) > 1 else ""
            host = host_port[0]
            port = int(host_port[1]) if len(host_port) > 1 else 5432
            database = host_db[1] if len(host_db) > 1 else ""
            
            conn = await asyncpg.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database
            )
            
            # Простой запрос для получения информации о пользователях (пример)
            # В реальности здесь должен быть более умный анализ запроса пользователя
            rows = await conn.fetch("SELECT COUNT(*) as count FROM users LIMIT 1")
            await conn.close()
            
            if rows:
                return {"count": rows[0]["count"]}
        except Exception as e:
            logger.error(f"Ошибка запроса к PostgreSQL: {e}")
        
        return None
    
    async def _query_sqlite(self, db_url: str, query: str) -> Optional[Dict]:
        """Выполнить запрос к SQLite"""
        try:
            # sqlite:///path/to/database.db
            db_path = db_url.replace("sqlite:///", "")
            
            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute("SELECT COUNT(*) as count FROM users LIMIT 1")
                row = await cursor.fetchone()
                await db.commit()
                
                if row:
                    return {"count": row[0]}
        except Exception as e:
            logger.error(f"Ошибка запроса к SQLite: {e}")
        
        return None
    
    async def _get_groq_answer(
        self, 
        question: str, 
        context: Optional[Dict] = None, 
        chat_history: Optional[List[Dict]] = None
    ) -> Optional[str]:
        """Получить ответ от Groq API"""
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            # Формируем системный промпт с информацией о сервисе
            project_name = self.config.project_name or 'STELS-Support'
            system_prompt = f"""Ты помощник поддержки VPN проекта {project_name}. 
Отвечай на вопросы пользователей вежливо, профессионально и по делу.
Используй информацию о проекте для точных ответов.
Отвечай на русском языке, если вопрос на русском.
Будь дружелюбным и готовым помочь.

ВАЖНО: Не называй пользователя другими именами или проектами. Обращайся к нему по имени, которое он указал, или просто "вы".

Если вопрос пользователя не может быть решен тобой, предложи пригласить менеджера в чат.

Информация о проекте:
{self._build_service_context(context)}"""
            
            # Пытаемся получить данные из БД проектов, если вопрос связан с данными
            project_data = None
            if any(keyword in question.lower() for keyword in ["пользователь", "подписка", "тариф", "баланс"]):
                project_data = await self._get_project_data(question)
                if project_data:
                    system_prompt += f"\n\nДополнительная информация из баз данных проектов:\n{project_data}"
            
            # Формируем список сообщений с историей
            messages = [{"role": "system", "content": system_prompt}]
            
            # Добавляем историю чата если есть
            if chat_history:
                for msg in chat_history:
                    role = msg.get("role", "user")
                    content = msg.get("message", "") or msg.get("content", "")
                    if role in ["user", "assistant"] and content:
                        messages.append({"role": role, "content": content})
            
            # Добавляем текущий вопрос
            messages.append({"role": "user", "content": question})
            
            payload = {
                "model": "llama-3.1-8b-instant",
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 1000
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            
            if response.status_code != 200:
                error_text = response.text
                logger.error(f"Ошибка Groq API (status {response.status_code}): {error_text}")
                return None
            
            response.raise_for_status()
            data = response.json()
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            return answer.strip() if answer else None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка запроса к Groq API: {e}")
            return None
        except Exception as e:
            logger.error(f"Ошибка обработки ответа от Groq API: {e}")
            return None
    
    def _get_rule_based_answer(
        self, 
        question: str, 
        context: Optional[Dict] = None, 
        chat_history: Optional[List[Dict]] = None
    ) -> str:
        """Rule-based ответы на основе ключевых слов"""
        question_lower = question.lower()
        
        # Словарь ответов на основе ключевых слов
        responses = {
            "подключ": "Для подключения VPN:\n1. Откройте приложение VPN на вашем устройстве\n2. Добавьте конфигурацию через subscription URL\n3. Подключитесь к серверу\n\nПодробная инструкция доступна в разделе 'Конфиги'.",
            "конфиг": "Конфигурация VPN доступна в разделе 'Конфиги' в вашем личном кабинете. Там вы можете получить subscription URL для подключения.",
            "подписка": "Информацию о вашей подписке вы можете посмотреть в разделе 'Профиль'. Там отображается статус подписки, дата истечения и остаток дней.",
            "оплат": "Для оплаты тарифа:\n1. Перейдите в раздел 'Конфиги'\n2. Нажмите 'Создать конфиг'\n3. Выберите тариф и способ оплаты\n4. Оплатите через выбранный метод",
            "баланс": "Баланс вашего счета отображается в разделе 'Профиль'. Вы можете пополнить баланс через раздел 'Опции'.",
            "сервер": "Список доступных серверов находится в разделе 'Сервера'. Там вы можете увидеть все доступные локации и их статус.",
            "проблем": "Если у вас возникли проблемы, пожалуйста, опишите их подробно. Если я не смогу решить ваш вопрос, я могу пригласить менеджера в чат.",
            "скорост": "Если у вас низкая скорость подключения, попробуйте:\n1. Переключиться на другой сервер\n2. Проверить ваше интернет-соединение\n3. Перезапустить VPN клиент",
            "работа": "Если VPN не работает:\n1. Проверьте статус подписки\n2. Убедитесь, что конфигурация актуальна\n3. Попробуйте переподключиться\n4. Если проблема не решена, я могу пригласить менеджера в чат"
        }
        
        # Ищем совпадения по ключевым словам
        for keyword, answer in responses.items():
            if keyword in question_lower:
                return answer
        
        # Дефолтный ответ
        return "Спасибо за ваш вопрос! Для получения более детальной информации, пожалуйста, опишите вашу проблему подробнее. Если я не смогу решить ваш вопрос, я могу пригласить менеджера в чат."
