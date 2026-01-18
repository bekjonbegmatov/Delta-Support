"""
Модуль AI поддержки для ответов на вопросы клиентов
Поддерживает Groq API и доступ к базам данных проектов
"""

import os
import logging
import json
import time
import asyncpg
import aiosqlite
import httpx
from typing import Optional, Dict, List
from sqlalchemy import create_engine, text
from modules.config import Config
from modules.database import KnowledgeBaseEntry, SystemConfig

logger = logging.getLogger(__name__)


class AISupport:
    """Класс для работы с AI поддержкой"""
    
    def __init__(self, config: Config):
        self.config = config
        self.enabled = config.ai_support_enabled
        self.api_type = config.ai_support_api_type
        self.api_key = config.ai_support_api_key
        self.project_databases = config.get_project_databases()
        
        # Поддержка нескольких API ключей
        if config.ai_support_api_keys:
            self.api_keys = [key.strip() for key in config.ai_support_api_keys.split(",") if key.strip()]
            if self.api_key:
                # Добавляем основной ключ в начало списка
                if self.api_key not in self.api_keys:
                    self.api_keys.insert(0, self.api_key)
        else:
            self.api_keys = [self.api_key] if self.api_key else []
        
        # Список моделей для использования (fallback)
        if config.groq_models:
            self.models = [m.strip() for m in config.groq_models.split(",") if m.strip()]
        else:
            # Модели по умолчанию (оптимизированы по лимитам)
            # Порядок: сначала модели с лучшими лимитами, потом по качеству
            self.models = [
                "llama-3.1-8b-instant",  # 14.4K req/day, 500K tokens/day - лучшие дневные лимиты
                "qwen/qwen3-32b",  # 60 req/min, 500K tokens/day - лучшие минутные лимиты
                "moonshotai/kimi-k2-instruct",  # 60 req/min, 300K tokens/day
                "meta-llama/llama-4-scout-17b-16e-instruct",  # 30K tokens/min, 500K tokens/day
                "llama-3.3-70b-versatile",  # 12K tokens/min - мощная, но ограниченная
                "openai/gpt-oss-120b",  # Альтернатива
                "openai/gpt-oss-20b"  # Альтернатива
            ]
        
        # Индекс текущего ключа и модели для round-robin
        self.current_key_index = 0
        self.current_model_index = 0
        self._runtime_settings_ts = 0.0
        self._runtime_settings = {}
        self._runtime_project_name = config.project_name or "DELTA-Support"
        self._runtime_project_description = config.project_description or ""
        self._runtime_project_website = config.project_website or ""
        self._runtime_project_bot_link = config.project_bot_link or ""
        self._runtime_project_owner_contacts = config.project_owner_contacts or ""
        self._runtime_system_prompt = ""
        self._runtime_db_keywords = None

    async def _refresh_runtime_settings(self):
        now = time.monotonic()
        if now - self._runtime_settings_ts < 3.0:
            return
        keys = [
            "project_name",
            "project_description",
            "project_website",
            "project_bot_link",
            "project_owner_contacts",
            "ai_system_prompt",
            "ai_support_api_type",
            "ai_support_api_key",
            "ai_support_api_keys",
            "groq_models",
            "ai_db_keywords",
        ]
        rows = await SystemConfig.filter(key__in=keys).all()
        values = {r.key: (r.value or "") for r in rows}
        self._runtime_settings = values
        self._runtime_settings_ts = now

        self._runtime_project_name = (values.get("project_name") or self.config.project_name or "DELTA-Support").strip()
        self._runtime_project_description = (values.get("project_description") or self.config.project_description or "").strip()
        self._runtime_project_website = (values.get("project_website") or self.config.project_website or "").strip()
        self._runtime_project_bot_link = (values.get("project_bot_link") or self.config.project_bot_link or "").strip()
        self._runtime_project_owner_contacts = (values.get("project_owner_contacts") or self.config.project_owner_contacts or "").strip()
        self._runtime_system_prompt = (values.get("ai_system_prompt") or "").strip()

        api_type = (values.get("ai_support_api_type") or self.config.ai_support_api_type or "groq").strip()
        self.api_type = api_type

        primary = (values.get("ai_support_api_key") or self.config.ai_support_api_key or "").strip()
        many = (values.get("ai_support_api_keys") or self.config.ai_support_api_keys or "").strip()
        keys_list = [k.strip() for k in many.split(",") if k.strip()] if many else []
        if primary:
            if primary not in keys_list:
                keys_list.insert(0, primary)
        self.api_keys = keys_list

        groq_models = (values.get("groq_models") or self.config.groq_models or "").strip()
        if groq_models:
            self.models = [m.strip() for m in groq_models.split(",") if m.strip()]

        dbkw = (values.get("ai_db_keywords") or "").strip()
        if dbkw:
            parts = []
            for chunk in dbkw.replace("\n", ",").split(","):
                t = chunk.strip()
                if t:
                    parts.append(t.lower())
            self._runtime_db_keywords = parts or None
        else:
            self._runtime_db_keywords = None
    
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
        await self._refresh_runtime_settings()
        
        # Проверяем API ключ только для внешних API (не для rule-based)
        if self.api_type != "rule-based" and not getattr(self, "api_keys", None):
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
    
    async def _build_service_context(self, context: Optional[Dict] = None) -> str:
        """Построить контекст о сервисе для AI (с приоритетом БД)"""
        await self._refresh_runtime_settings()
        service_info = []
        
        # Базовая информация о проекте
        if self._runtime_project_name:
            service_info.append(f"Название проекта: {self._runtime_project_name}")
        if self._runtime_project_description:
            service_info.append(f"Описание: {self._runtime_project_description}")
        if self._runtime_project_website:
            service_info.append(f"Официальный сайт: {self._runtime_project_website}")
        if self._runtime_project_bot_link:
            service_info.append(f"Telegram бот: {self._runtime_project_bot_link}")
        if self._runtime_project_owner_contacts:
            service_info.append(f"Контакты владельца: {self._runtime_project_owner_contacts}")
        
        admin_info = await self._get_service_info_from_admin_db()
        db_info = await self._get_service_info_from_db()
        
        features = admin_info.get("features") or db_info.get("features") or self.config.service_features
        if features:
            service_info.append(f"\nОсобенности сервиса:\n{features}")

        tariffs = admin_info.get("tariffs") or db_info.get("tariffs") or self.config.service_tariffs
        if tariffs:
            service_info.append(f"\nТарифы и цены:\n{tariffs}")

        instructions = admin_info.get("instructions") or db_info.get("instructions") or self.config.service_instructions
        if instructions:
            service_info.append(f"\nИнструкции по использованию:\n{instructions}")

        faq = admin_info.get("faq") or db_info.get("faq") or self.config.service_faq
        if faq:
            service_info.append(f"\nЧасто задаваемые вопросы (FAQ):\n{faq}")

        support_hours = admin_info.get("support_hours") or db_info.get("support_hours") or self.config.service_support_hours
        if support_hours:
            service_info.append(f"\nЧасы работы поддержки: {support_hours}")

        kb = await self._get_knowledge_base_text()
        if kb:
            service_info.append(f"\nБаза знаний:\n{kb}")

        # Информация о пользователе
        if context:
            user_info = []
            if context.get("user_id"):
                user_info.append(f"ID пользователя: {context.get('user_id')}")
            if context.get("username"):
                user_info.append(f"Username: @{context.get('username')}")
            if context.get("first_name"):
                user_info.append(f"Имя: {context.get('first_name')}")
            
            if user_info:
                service_info.append(f"\nИнформация о пользователе:\n" + "\n".join(user_info))
        
        return "\n".join(service_info) if service_info else ""

    async def _get_service_info_from_admin_db(self) -> Dict[str, str]:
        info: Dict[str, str] = {}
        keys = {
            "service_features": "features",
            "service_tariffs": "tariffs",
            "service_instructions": "instructions",
            "service_faq": "faq",
            "service_support_hours": "support_hours",
        }
        try:
            rows = await SystemConfig.filter(key__in=list(keys.keys())).all()
            for r in rows:
                mapped = keys.get(r.key)
                if mapped:
                    info[mapped] = r.value
        except Exception:
            return info
        return info

    async def _get_knowledge_base_text(self) -> str:
        try:
            rows = await KnowledgeBaseEntry.filter(is_active=True).order_by("-updated_at").all()
        except Exception:
            return ""
        if not rows:
            return ""
        limit_chars = 8000
        parts: List[str] = []
        size = 0
        for r in rows:
            chunk = f"- {r.title}:\n{r.content}\n"
            if size + len(chunk) > limit_chars:
                break
            parts.append(chunk)
            size += len(chunk)
        return "\n".join(parts).strip()
    
    async def _get_service_info_from_db(self) -> Dict[str, str]:
        """Получить информацию о сервисе из БД проектов (приоритет над .env)"""
        info = {}
        
        if not self.project_databases:
            return info
        
        for db_url in self.project_databases:
            try:
                if db_url.startswith("postgresql://"):
                    db_info = await self._query_service_info_postgres(db_url)
                elif db_url.startswith("sqlite:///"):
                    db_info = await self._query_service_info_sqlite(db_url)
                else:
                    continue
                
                # Объединяем информацию из всех БД (первая найденная имеет приоритет)
                for key, value in db_info.items():
                    if value and key not in info:
                        info[key] = value
            except Exception as e:
                logger.debug(f"Не удалось получить информацию о сервисе из БД: {e}")
                continue
        
        return info
    
    async def _query_service_info_postgres(self, db_url: str) -> Dict[str, str]:
        """Получить информацию о сервисе из PostgreSQL"""
        info = {}
        
        try:
            parts = db_url.replace("postgresql://", "").split("@")
            if len(parts) != 2:
                return info
            
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
            
            # Пытаемся получить информацию из таблицы settings, config или service_info
            try:
                # Проверяем таблицу service_info
                result = await conn.fetchrow("""
                    SELECT faq, tariffs, instructions, features, support_hours 
                    FROM service_info 
                    LIMIT 1
                """)
                if result:
                    if result.get('faq'):
                        info['faq'] = result['faq']
                    if result.get('tariffs'):
                        info['tariffs'] = result['tariffs']
                    if result.get('instructions'):
                        info['instructions'] = result['instructions']
                    if result.get('features'):
                        info['features'] = result['features']
                    if result.get('support_hours'):
                        info['support_hours'] = result['support_hours']
            except:
                pass
            
            # Если не нашли в service_info, пробуем получить тарифы из таблицы tariffs
            if not info.get('tariffs'):
                try:
                    tariffs = await conn.fetch("""
                        SELECT name, price, description 
                        FROM tariffs 
                        ORDER BY price ASC
                        LIMIT 10
                    """)
                    if tariffs:
                        tariff_list = []
                        for t in tariffs:
                            name = t.get('name', 'N/A')
                            price = t.get('price', 'N/A')
                            desc = t.get('description', '')
                            tariff_str = f"- {name}: {price}"
                            if desc:
                                tariff_str += f" ({desc})"
                            tariff_list.append(tariff_str)
                        if tariff_list:
                            info['tariffs'] = "\n".join(tariff_list)
                except:
                    pass
            
            await conn.close()
        except Exception as e:
            logger.debug(f"Ошибка получения информации о сервисе из PostgreSQL: {e}")
        
        return info
    
    async def _query_service_info_sqlite(self, db_url: str) -> Dict[str, str]:
        """Получить информацию о сервисе из SQLite"""
        info = {}
        
        try:
            db_path = db_url.replace("sqlite:///", "")
            
            async with aiosqlite.connect(db_path) as db:
                # Пытаемся получить информацию из таблицы service_info
                try:
                    cursor = await db.execute("""
                        SELECT faq, tariffs, instructions, features, support_hours 
                        FROM service_info 
                        LIMIT 1
                    """)
                    result = await cursor.fetchone()
                    
                    if result:
                        cursor = await db.execute("PRAGMA table_info(service_info)")
                        columns = [row[1] for row in await cursor.fetchall()]
                        
                        if 'faq' in columns and result[columns.index('faq')]:
                            info['faq'] = result[columns.index('faq')]
                        if 'tariffs' in columns and result[columns.index('tariffs')]:
                            info['tariffs'] = result[columns.index('tariffs')]
                        if 'instructions' in columns and result[columns.index('instructions')]:
                            info['instructions'] = result[columns.index('instructions')]
                        if 'features' in columns and result[columns.index('features')]:
                            info['features'] = result[columns.index('features')]
                        if 'support_hours' in columns and result[columns.index('support_hours')]:
                            info['support_hours'] = result[columns.index('support_hours')]
                except:
                    pass
                
                # Если не нашли в service_info, пробуем получить тарифы из таблицы tariffs
                if not info.get('tariffs'):
                    try:
                        cursor = await db.execute("""
                            SELECT name, price, description 
                            FROM tariffs 
                            ORDER BY price ASC
                            LIMIT 10
                        """)
                        tariffs = await cursor.fetchall()
                        
                        if tariffs:
                            cursor = await db.execute("PRAGMA table_info(tariffs)")
                            columns = [row[1] for row in await cursor.fetchall()]
                            
                            tariff_list = []
                            for t in tariffs:
                                name_idx = columns.index('name') if 'name' in columns else 0
                                price_idx = columns.index('price') if 'price' in columns else 1
                                desc_idx = columns.index('description') if 'description' in columns else None
                                
                                name = t[name_idx]
                                price = t[price_idx]
                                desc = t[desc_idx] if desc_idx is not None and desc_idx < len(t) else ''
                                
                                tariff_str = f"- {name}: {price}"
                                if desc:
                                    tariff_str += f" ({desc})"
                                tariff_list.append(tariff_str)
                            
                            if tariff_list:
                                info['tariffs'] = "\n".join(tariff_list)
                    except:
                        pass
                
                await db.commit()
        except Exception as e:
            logger.debug(f"Ошибка получения информации о сервисе из SQLite: {e}")
        
        return info
    
    async def _get_project_data(self, query: str, user_id: int = None) -> Optional[str]:
        """Получить данные из баз данных проектов"""
        if not self.project_databases:
            return None
        
        results = []
        question_lower = query.lower()
        
        for db_url in self.project_databases:
            try:
                if db_url.startswith("postgresql://"):
                    data = await self._query_postgres_enhanced(db_url, question_lower, user_id)
                elif db_url.startswith("sqlite:///"):
                    data = await self._query_sqlite_enhanced(db_url, question_lower, user_id)
                else:
                    continue
                
                if data:
                    results.append(data)
            except Exception as e:
                logger.error(f"Ошибка запроса к БД проекта: {e}")
                continue
        
        return "\n\n---\n\n".join(results) if results else None
    
    async def _query_postgres(self, db_url: str, query: str) -> Optional[Dict]:
        """Выполнить запрос к PostgreSQL (legacy метод)"""
        return await self._query_postgres_enhanced(db_url, query.lower(), None)
    
    async def _query_postgres_enhanced(self, db_url: str, question: str, user_id: int = None) -> Optional[str]:
        """Улучшенный запрос к PostgreSQL с пониманием контекста"""
        try:
            # Парсим URL
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
            
            data_parts = []
            
            # Если есть user_id, пытаемся найти информацию о пользователе
            if user_id:
                try:
                    # Пытаемся найти пользователя по telegram_id или user_id
                    user_info = await conn.fetchrow("""
                        SELECT * FROM users 
                        WHERE telegram_id = $1 OR id = $1 
                        LIMIT 1
                    """, user_id)
                    
                    if user_info:
                        info = []
                        if 'username' in user_info:
                            info.append(f"Username: {user_info['username']}")
                        if 'balance' in user_info:
                            info.append(f"Баланс: {user_info['balance']}")
                        if 'subscription_expires_at' in user_info:
                            info.append(f"Подписка до: {user_info['subscription_expires_at']}")
                        if info:
                            data_parts.append("Информация о пользователе:\n" + "\n".join(info))
                except Exception as e:
                    logger.debug(f"Не удалось получить данные пользователя: {e}")
            
            # Если вопрос о тарифах
            if any(kw in question for kw in ["тариф", "цена", "стоимость", "tariff", "price"]):
                try:
                    tariffs = await conn.fetch("SELECT * FROM tariffs LIMIT 10")
                    if tariffs:
                        tariff_info = []
                        for t in tariffs:
                            name = t.get('name', 'N/A')
                            price = t.get('price', 'N/A')
                            tariff_info.append(f"- {name}: {price}")
                        if tariff_info:
                            data_parts.append("Доступные тарифы:\n" + "\n".join(tariff_info))
                except Exception as e:
                    logger.debug(f"Не удалось получить тарифы: {e}")
            
            await conn.close()
            
            return "\n\n".join(data_parts) if data_parts else None
            
        except Exception as e:
            logger.error(f"Ошибка запроса к PostgreSQL: {e}")
            return None
    
    async def _query_sqlite(self, db_url: str, query: str) -> Optional[Dict]:
        """Выполнить запрос к SQLite (legacy метод)"""
        result = await self._query_sqlite_enhanced(db_url, query.lower(), None)
        if result:
            return {"data": result}
        return None
    
    async def _query_sqlite_enhanced(self, db_url: str, question: str, user_id: int = None) -> Optional[str]:
        """Улучшенный запрос к SQLite с пониманием контекста"""
        try:
            db_path = db_url.replace("sqlite:///", "")
            
            async with aiosqlite.connect(db_path) as db:
                data_parts = []
                
                # Если есть user_id, пытаемся найти информацию о пользователе
                if user_id:
                    try:
                        cursor = await db.execute("""
                            SELECT * FROM users 
                            WHERE telegram_id = ? OR id = ? 
                            LIMIT 1
                        """, (user_id, user_id))
                        user_info = await cursor.fetchone()
                        
                        if user_info:
                            # Получаем названия колонок
                            cursor = await db.execute("PRAGMA table_info(users)")
                            columns = [row[1] for row in await cursor.fetchall()]
                            
                            info = []
                            for i, col in enumerate(columns):
                                if col in ['username', 'balance', 'subscription_expires_at']:
                                    info.append(f"{col}: {user_info[i]}")
                            if info:
                                data_parts.append("Информация о пользователе:\n" + "\n".join(info))
                    except Exception as e:
                        logger.debug(f"Не удалось получить данные пользователя: {e}")
                
                # Если вопрос о тарифах
                if any(kw in question for kw in ["тариф", "цена", "стоимость"]):
                    try:
                        cursor = await db.execute("SELECT * FROM tariffs LIMIT 10")
                        tariffs = await cursor.fetchall()
                        if tariffs:
                            cursor = await db.execute("PRAGMA table_info(tariffs)")
                            columns = [row[1] for row in await cursor.fetchall()]
                            
                            tariff_info = []
                            for t in tariffs:
                                name_idx = columns.index('name') if 'name' in columns else 0
                                price_idx = columns.index('price') if 'price' in columns else 1
                                tariff_info.append(f"- {t[name_idx]}: {t[price_idx]}")
                            if tariff_info:
                                data_parts.append("Доступные тарифы:\n" + "\n".join(tariff_info))
                    except Exception as e:
                        logger.debug(f"Не удалось получить тарифы: {e}")
                
                await db.commit()
                
                return "\n\n".join(data_parts) if data_parts else None
                
        except Exception as e:
            logger.error(f"Ошибка запроса к SQLite: {e}")
            return None
    
    async def _get_groq_answer(
        self, 
        question: str, 
        context: Optional[Dict] = None, 
        chat_history: Optional[List[Dict]] = None
    ) -> Optional[str]:
        """Получить ответ от Groq API с fallback на другие модели и ключи"""
        
        # Пробуем все комбинации ключей и моделей
        last_error = None
        
        for key_index, api_key in enumerate(self.api_keys):
            if not api_key:
                continue
                
            for model_index, model in enumerate(self.models):
                try:
                    result = await self._try_groq_request(
                        api_key=api_key,
                        model=model,
                        question=question,
                        context=context,
                        chat_history=chat_history
                    )
                    
                    if result:
                        # Сохраняем успешную комбинацию для следующего запроса
                        self.current_key_index = key_index
                        self.current_model_index = model_index
                        logger.info(f"Successfully used model: {model} with key index: {key_index}")
                        return result
                        
                except Exception as e:
                    error_msg = str(e).lower()
                    # Проверяем, является ли это ошибкой токенов/лимита
                    if any(keyword in error_msg for keyword in ["rate limit", "quota", "token", "limit exceeded", "429"]):
                        logger.warning(f"Rate limit/quota exceeded for model {model} with key {key_index}, trying next...")
                        last_error = e
                        continue
                    else:
                        # Другие ошибки - логируем и пробуем дальше
                        logger.debug(f"Error with model {model} (key {key_index}): {e}")
                        last_error = e
                        continue
        
        # Если все попытки не удались
        if last_error:
            logger.error(f"All Groq API attempts failed. Last error: {last_error}")
        else:
            logger.error("All Groq API attempts failed. No API keys or models available.")
        
        return None
    
    async def _try_groq_request(
        self,
        api_key: str,
        model: str,
        question: str,
        context: Optional[Dict] = None,
        chat_history: Optional[List[Dict]] = None
    ) -> Optional[str]:
        """Попытка одного запроса к Groq API"""
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            # Получаем контекст о сервисе (async)
            service_context = await self._build_service_context(context)
            
            # Формируем системный промпт с информацией о сервисе
            project_name = self._runtime_project_name or "DELTA-Support"
            default_prompt = """Ты профессиональный помощник службы поддержки VPN проекта {project_name}.

ТВОЯ РОЛЬ:
- Помогать пользователям решать их вопросы о VPN сервисе
- Предоставлять точную информацию на основе данных о сервисе
- Быть вежливым, дружелюбным и профессиональным
- Если не можешь решить вопрос - предложить пригласить менеджера

ПРАВИЛА ОБЩЕНИЯ:
- Отвечай на русском языке, если вопрос на русском
- Используй информацию о сервисе для точных ответов
- НЕ называй пользователя другими именами или проектами
- Обращайся к пользователю по имени (если известно) или на "вы"
- Будь конкретным и полезным в ответах
- Если вопрос неясен - уточни детали

СТРУКТУРА ОТВЕТОВ:
- Начни с приветствия или подтверждения понимания вопроса
- Дай четкий и структурированный ответ
- Если нужно - используй нумерованные списки или пункты
- В конце предложи дополнительную помощь или пригласи менеджера, если вопрос сложный

ИНФОРМАЦИЯ О СЕРВИСЕ:
{service_context}

ВАЖНО: Если вопрос пользователя касается личных данных (баланс, подписка, тариф), но у тебя нет доступа к этой информации - предложи пользователю проверить личный кабинет или пригласить менеджера."""
            tpl = self._runtime_system_prompt or default_prompt

            class _SafeDict(dict):
                def __missing__(self, key):
                    return "{" + key + "}"

            ctx = dict(context or {})
            ctx.update({"project_name": project_name, "service_context": service_context})
            try:
                system_prompt = tpl.format_map(_SafeDict(ctx))
            except Exception:
                system_prompt = default_prompt.format_map(_SafeDict(ctx))
            
            # Пытаемся получить данные из БД проектов, если вопрос связан с данными
            project_data = None
            user_id = context.get("user_id") if context else None
            question_lower = question.lower()
            
            # Расширенный список ключевых слов для запросов к БД
            db_keywords = self._runtime_db_keywords or [
                "пользователь", "подписка", "тариф", "баланс", "аккаунт", "профиль",
                "цена", "стоимость", "оплата", "платеж", "subscription", "tariff",
                "balance", "account", "profile", "price", "payment"
            ]
            
            if any(keyword in question_lower for keyword in db_keywords):
                project_data = await self._get_project_data(question, user_id)
                if project_data:
                    system_prompt += f"\n\nВАЖНО - Дополнительная информация из баз данных проектов:\n{project_data}\n\nИспользуй эту информацию для точных ответов о пользователе, тарифах и подписках."
            
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
                "model": model,  # Используем переданную модель
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 1000
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=payload, timeout=30.0)
                
                if response.status_code == 200:
                    data = response.json()
                    if "choices" in data and len(data["choices"]) > 0:
                        return data["choices"][0]["message"]["content"]
                elif response.status_code == 429:
                    # Rate limit - пробрасываем для fallback
                    raise Exception(f"Rate limit exceeded: {response.text}")
                elif response.status_code == 401:
                    # Неверный ключ - пробрасываем для fallback
                    raise Exception(f"Invalid API key: {response.text}")
                else:
                    error_msg = f"Groq API error: {response.status_code} - {response.text}"
                    logger.warning(error_msg)
                    raise Exception(error_msg)
                    
        except httpx.TimeoutException:
            raise Exception("Request timeout")
        except httpx.RequestError as e:
            raise Exception(f"Request error: {str(e)}")
        except Exception as e:
            # Пробрасываем дальше для fallback
            raise
    
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
