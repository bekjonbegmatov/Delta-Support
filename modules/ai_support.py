"""
Модуль AI поддержки для ответов на вопросы клиентов
Поддерживает несколько OpenAI-совместимых провайдеров с балансировкой
Автоматически обновляет контекст при изменении базы знаний и настроек
"""

import os
import logging
import json
import re
import time
import httpx
from html import unescape
from uuid import uuid4
from typing import Optional, Dict, List, Union
from modules.config import Config
from modules.database import KnowledgeBaseEntry, SystemConfig, AIProvider
from modules.user_info import PendingAction, subscription_display_name
from modules.openai_api import apply_chat_compatibility, endpoint_url, reasoning_model, request_responses
from modules.ai_policy import DEFAULT_SUPPORT_PROMPT, SUPPORT_POLICY, OFFER_MANAGER_TOOL, SupportAnswer, validate_manager_offer
from modules.knowledge_gaps import REPORT_KNOWLEDGE_GAP_TOOL, record_knowledge_gap
from modules.ai_streaming import (
    TOOL_PROTOCOL, StreamingUnsupported, clean_response_text, emit_preview, read_chat_stream,
)

logger = logging.getLogger(__name__)


# Read-only инструменты: AI вызывает автономно во время чата с клиентом.
# Владение (что это данные ИМЕННО этого клиента) проверяется на исполнении,
# а не доверяется параметрам, которые сформировал сам AI.
_READONLY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_payment_methods",
            "description": (
                "Получить актуальные доступные способы пополнения баланса, их коды, валюту и ограничения по сумме. "
                "Используй, когда клиент спрашивает, как оплатить или какие способы оплаты доступны."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_payment",
            "description": (
                "Проверить статус платежа клиента по ID транзакции, order_id или provider transaction id. "
                "Используй для актуального статуса оплаты; передавай order_id/transaction_id созданного счёта. "
                "Снимок ответа создания, открытие ссылки или возврат с платёжной страницы не доказывают оплату."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string", "description": "ID транзакции/order_id, который назвал клиент"}
                },
                "required": ["transaction_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_subscription_info",
            "description": (
                "Получить подробную информацию о конкретной подписке клиента: тариф, срок действия, трафик, "
                "лимит устройств. Используй username или локальный id выбранной подписки из ДАННЫХ АККАУНТА. "
                "Показывай её display_name (fallback username), а тариф — отдельно; Telegram username владельца не подходит."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subscription_username": {"type": "string", "description": "Уникальный username выбранной подписки, например 46235_41nr3; не тариф и не Telegram username владельца"},
                    "subscription_id": {"type": "integer", "minimum": 1, "description": "Локальный id выбранной подписки из Support API; укажи username или этот id"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_subscription_devices",
            "description": "Получить список устройств, подключённых к подписке клиента.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subscription_username": {"type": "string", "description": "Уникальный username выбранной подписки, например 46235_41nr3; не тариф и не Telegram username владельца"},
                    "subscription_id": {"type": "integer", "minimum": 1, "description": "Локальный id выбранной подписки из Support API; укажи username или этот id"}
                },
                "required": [],
            },
        },
    },
]

# Разрушающие инструменты: AI НЕ выполняет их напрямую — только формирует
# запрос на подтверждение, реальное действие происходит после явного клика
# клиента по кнопке в Telegram (см. modules.user_info.PendingAction)
_DESTRUCTIVE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_payment",
            "description": (
                "Подготовить ссылку на пополнение баланса клиента. Вызывай только когда клиент явно попросил "
                "создать счёт и указал сумму. Метод можно передать кодом или названием; если он не указан и "
                "доступно несколько способов, сначала уточни выбор. Ссылка создаётся только после подтверждения клиента. "
                "Не вызывай повторно для исправления таймаута/409/502: повтор той же операции сохраняет прежний ключ. "
                "Новый счёт возможен только по отдельному явному запросу клиента; старые счета не отменяются. "
                "При просьбе повторить тот же запрос выбери request_mode=retry и operation_id конкретной операции из контекста счетов. "
                "Сумма и метод для повтора необязательны: сервер использует сохранённые. Если счетов несколько, уточни выбор, не угадывай. "
                "при явном запросе нового счёта — new. UUID выдаёт сервер только после отдельного подтверждения нового счёта. "
                "Для Platega не спрашивай email и не передавай metadata."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "integer", "description": "Для нового счёта обязательна сумма 50–9999 целых рублей; для повтора можно не передавать"},
                    "method": {"type": "string", "description": "Для нового счёта код или название способа оплаты, например platega_sbp или СБП; для повтора не передавай либо передай исходный код из контекста"},
                    "request_mode": {"type": "string", "enum": ["new", "retry"], "description": "new — отдельный явно запрошенный новый счёт; retry — повтор прежней операции с тем же ключом и параметрами"},
                    "operation_id": {"type": "integer", "minimum": 1, "description": "Только для retry: локальный ID конкретной операции из контекста счетов. Это не order_id и не UUID"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reset_devices",
            "description": (
                "Сбросить (отключить) ВСЕ устройства подписки клиента. Разрушающее действие — используй, "
                "только если клиент явно просит сбросить/отключить/сбросить все устройства подписки."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subscription_username": {"type": "string", "description": "Уникальный username выбранной подписки, например 46235_41nr3; не тариф и не Telegram username владельца"},
                    "subscription_id": {"type": "integer", "minimum": 1, "description": "Локальный id выбранной подписки из Support API; укажи username или этот id"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revoke_subscription",
            "description": (
                "Перевыпустить ССЫЛКУ подписки клиента, старые устройства отключаются. Это НЕ продление срока подписки. "
                "Работает только если с даты выдачи подписки прошло больше 20 дней. Используй, только если "
                "клиент явно просит перевыпустить/обновить ссылку подписки."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subscription_username": {"type": "string", "description": "Уникальный username выбранной подписки, например 46235_41nr3; не тариф и не Telegram username владельца"},
                    "subscription_id": {"type": "integer", "minimum": 1, "description": "Локальный id выбранной подписки из Support API; укажи username или этот id"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_device",
            "description": (
                "Удалить одно конкретное устройство из подписки клиента по его hwid. Обычно вызывается после "
                "get_subscription_devices, когда клиент указал, какое именно устройство убрать."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subscription_username": {"type": "string", "description": "Уникальный username выбранной подписки; не тариф и не Telegram username владельца"},
                    "subscription_id": {"type": "integer", "minimum": 1, "description": "Локальный id выбранной подписки из Support API; укажи username или этот id"},
                    "hwid": {"type": "string", "description": "hwid устройства из списка get_subscription_devices"},
                },
                "required": ["hwid"],
            },
        },
    },
]

_TEXT_TOOL_ALIASES = {
    "get_available_payment_methods": "get_payment_methods",
    "create_payment_link": "create_payment",
    "create_payment_invoice": "create_payment",
    "remove_subscription_device": "delete_device",
    "delete_subscription_device": "delete_device",
    "remove_device": "delete_device",
}


class AISupport:
    """Класс для работы с AI поддержкой"""
    
    def __init__(self, config: Config):
        self.config = config
        self.enabled = config.ai_support_enabled
        self.response_mode = "standard"
        self._stream_unsupported_until = {}
        
        self._runtime_settings_ts = 0.0
        self._runtime_settings = {}
        self._project_name = config.project_name or "DELTA-Support"
        self._project_description = config.project_description or ""
        self._project_website = config.project_website or ""
        self._project_bot_link = config.project_bot_link or ""
        self._project_owner_contacts = config.project_owner_contacts or ""
        self._system_prompt = ""
        self._ai_providers = []
        
        # Сервисная информация из config.py
        self._service_faq = config.service_faq or ""
        self._service_tariffs = config.service_tariffs or ""
        self._service_instructions = config.service_instructions or ""
        self._service_features = config.service_features or ""
        self._service_support_hours = config.service_support_hours or "Круглосуточно"
        
        # Кеш для оптимизации
        self._kb_entries_ts = 0.0
        self._kb_entries_cache = []
        self._service_context_cache = ""
        self._service_context_ts = 0.0
        
        # Настройки кеширования
        self._settings_cache_ttl = 5.0
        self._kb_cache_ttl = 30.0
        self._context_cache_ttl = 10.0

    async def _refresh_runtime_settings(self, force: bool = False):
        """Обновить настройки из БД с кешированием"""
        now = time.monotonic()
        
        if not force and now - self._runtime_settings_ts < self._settings_cache_ttl:
            return
        
        keys = [
            "project_name",
            "project_description",
            "project_website",
            "project_bot_link",
            "project_owner_contacts",
            "ai_system_prompt",
            "ai_support_enabled",
            "ai_response_mode",
            "service_faq",
            "service_tariffs",
            "service_instructions",
            "service_features",
            "service_support_hours",
        ]
        
        try:
            rows = await SystemConfig.filter(key__in=keys).all()
            values = {r.key: (r.value or "") for r in rows}
            self._runtime_settings = values
            self.response_mode = "streaming" if values.get("ai_response_mode") == "streaming" else "standard"
            
            self._project_name = (values.get("project_name") or self.config.project_name or "DELTA-Support").strip()
            self._project_description = (values.get("project_description") or self.config.project_description or "").strip()
            self._project_website = (values.get("project_website") or self.config.project_website or "").strip()
            self._project_bot_link = (values.get("project_bot_link") or self.config.project_bot_link or "").strip()
            self._project_owner_contacts = (values.get("project_owner_contacts") or self.config.project_owner_contacts or "").strip()
            self._system_prompt = (values.get("ai_system_prompt") or "").strip()
            
            # Сервисная информация из БД (приоритетнее чем из config)
            self._service_faq = (values.get("service_faq") or self.config.service_faq or "").strip()
            self._service_tariffs = (values.get("service_tariffs") or self.config.service_tariffs or "").strip()
            self._service_instructions = (values.get("service_instructions") or self.config.service_instructions or "").strip()
            self._service_features = (values.get("service_features") or self.config.service_features or "").strip()
            self._service_support_hours = (values.get("service_support_hours") or self.config.service_support_hours or "Круглосуточно").strip()
            
            enabled_raw = values.get("ai_support_enabled")
            if enabled_raw is None or str(enabled_raw).strip() == "":
                self.enabled = bool(self.config.ai_support_enabled)
            else:
                self.enabled = str(enabled_raw).strip().lower() in ["1", "true", "yes", "y", "on"]
            
            # Загружаем провайдеров из БД
            self._ai_providers = await AIProvider.filter(is_active=True).order_by("priority", "id").all()
            
            self._runtime_settings_ts = now
            
            # Сбрасываем кеш контекста при обновлении настроек
            self._service_context_ts = 0.0
            
        except Exception as e:
            logger.error(f"Ошибка обновления настроек AI: {e}")
            if not self._runtime_settings_ts:
                self._runtime_settings_ts = now - self._settings_cache_ttl + 1

    async def _get_kb_entries(self, force: bool = False) -> List[KnowledgeBaseEntry]:
        """Получить записи базы знаний с кешированием"""
        now = time.monotonic()
        
        if not force and now - self._kb_entries_ts < self._kb_cache_ttl:
            return self._kb_entries_cache
        
        try:
            self._kb_entries_cache = await KnowledgeBaseEntry.filter(is_active=True).all()
            self._kb_entries_ts = now
        except Exception as e:
            logger.warning(f"Ошибка получения базы знаний: {e}")
            if not self._kb_entries_cache:
                self._kb_entries_cache = []
        
        return self._kb_entries_cache

    def invalidate_cache(self):
        """Сбросить весь кеш (вызывать при изменении данных в БД)"""
        self._runtime_settings_ts = 0.0
        self._kb_entries_ts = 0.0
        self._service_context_ts = 0.0
        logger.debug("AI cache invalidated")

    # Модели, умеющие принимать изображения
    _VISION_MODEL_MARKERS = ["llama-4", "scout", "maverick", "vision", "gpt-4", "gpt-5", "gpt-6", "chatgpt", "o3", "o4", "gemini", "claude", "qwen-vl", "qwen3.6", "pixtral"]

    def _model_supports_vision(self, model_name: str) -> bool:
        name = (model_name or "").lower()
        return any(m in name for m in self._VISION_MODEL_MARKERS)

    def _get_groq_api_key(self) -> Optional[str]:
        """Ключ Groq для вспомогательных запросов (Whisper): из провайдеров или config"""
        for p in self._ai_providers:
            if p.api_type == "groq" and p.api_key:
                return p.api_key
        return (self.config.ai_support_api_key or "").strip() or None

    async def transcribe_audio(self, data: bytes, filename: str = "voice.ogg") -> Optional[str]:
        """Расшифровка голосового через Groq Whisper"""
        await self._refresh_runtime_settings()
        api_key = self._get_groq_api_key()
        if not api_key:
            logger.warning("Whisper: нет Groq API ключа")
            return None
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (filename, data)},
                    data={"model": "whisper-large-v3-turbo"},
                    timeout=60.0,
                )
            if resp.status_code != 200:
                # fallback на обычную whisper-large-v3
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        files={"file": (filename, data)},
                        data={"model": "whisper-large-v3"},
                        timeout=60.0,
                    )
            if resp.status_code == 200:
                text = (resp.json().get("text") or "").strip()
                return text or None
            logger.warning(f"Whisper error {resp.status_code}: {resp.text[:200]}")
            return None
        except Exception as e:
            logger.warning(f"Whisper request failed: {e}")
            return None

    async def get_ai_answer(
        self,
        question: str,
        context: Optional[Dict] = None,
        chat_history: Optional[List[Dict]] = None,
        image_data_url: Optional[str] = None,
        execute_tools: bool = False,
        user_info_service=None,
        tool_event_callback=None,
        stream_callback=None,
    ) -> Optional[Union[str, PendingAction]]:
        """Получить ответ от AI с поддержкой нескольких провайдеров.

        execute_tools=True включает tool calling (проверка платежа, инфо о подписке/
        устройствах, сброс устройств, перевыпуск подписки) — используется ТОЛЬКО в
        основном чате с живым клиентом (bot.py), требует context['user_id'].
        Для инструментов аккаунта также нужен user_info_service для проверки владения.
        Разрушающие действия не выполняются напрямую — возвращается PendingAction,
        которое ждёт подтверждения клиента кнопкой в Telegram."""
        await self._refresh_runtime_settings()
        if self.response_mode != "streaming":
            stream_callback = None

        if not self.enabled:
            return None

        if not self._ai_providers:
            logger.warning("Нет активных AI провайдеров в базе данных!")
            return None

        # Для vision-запросов ужимаем историю: лимиты токенов/мин у vision-моделей ниже
        if image_data_url and chat_history:
            chat_history = chat_history[-6:]

        # Local support tools work even when the account integration is disabled.
        tools_allowed = execute_tools and bool((context or {}).get("user_id"))

        last_error = None
        for provider in self._ai_providers:
            # Для изображений подходят только vision-модели
            if image_data_url and provider.api_type != "openai_v2" and not self._model_supports_vision(provider.model_name):
                continue
            try:
                result = await self._try_provider_request(
                    provider=provider,
                    question=question,
                    context=context,
                    chat_history=chat_history,
                    image_data_url=image_data_url,
                    execute_tools=tools_allowed,
                    user_info_service=user_info_service,
                    tool_event_callback=tool_event_callback,
                    stream_callback=stream_callback,
                )
                if result:
                    return result
                await emit_preview(stream_callback, "")
            except Exception as e:
                await emit_preview(stream_callback, "")
                logger.warning(f"Провайдер {provider.name} ({provider.model_name}) ошибка: {e}")
                last_error = e
                continue

        if last_error:
            logger.error(f"Все AI провайдеры вернули ошибку. Последняя: {last_error}")
        return None

    @staticmethod
    async def _emit_tool_event(callback, event: Dict):
        if not callback:
            return
        try:
            await callback(event)
        except Exception as e:
            logger.warning(f"AI tool audit event failed: {e}")

    @staticmethod
    def _resolve_payment_method(requested: str, methods: List[Dict]) -> Optional[Dict]:
        """Resolve a human payment-method label to the API method returned at runtime.

        Models often repeat the user-facing label with extra details, for example
        ``Банковская карта (МИР)`` while the API title is just ``Банковская карта``.
        Keep the final choice constrained to the current API response instead of
        trusting a model-generated code.
        """
        if not methods:
            return None

        def normalize(value) -> str:
            value = str(value or "").casefold().replace("ё", "е")
            return " ".join(re.findall(r"[a-zа-я0-9]+", value))

        query = normalize(requested)
        if not query:
            return methods[0] if len(methods) == 1 else None

        # Prefer an exact code/title before applying fuzzy human-label matching.
        for method in methods:
            code = normalize(method.get("code"))
            title = normalize(method.get("title"))
            if query == code or query == title:
                return method

        # Aliases list possible provider codes in priority order. Only a code that
        # is actually present in ``methods`` can be selected.
        alias_codes = {
            "сбп": ("platega_sbp", "sbp"),
            "sbp": ("platega_sbp", "sbp"),
            "карта": ("platega_card", "card"),
            "картой": ("platega_card", "card"),
            "банковская карта": ("platega_card", "card"),
            "банковская карта мир": ("platega_card", "card"),
            "мир": ("platega_card", "card"),
            "card": ("platega_card", "card"),
            "крипта": ("cryptobot", "platega_crypto"),
            "криптовалюта": ("cryptobot", "platega_crypto"),
            "crypto": ("cryptobot", "platega_crypto"),
            "крипто бот": ("cryptobot", "platega_crypto"),
            "cryptobot": ("cryptobot", "platega_crypto"),
            "stars": ("tgs",),
            "звезды": ("tgs",),
            "tgs": ("tgs",),
        }
        requested_codes = alias_codes.get(query, ())
        if requested_codes:
            by_code = {normalize(method.get("code")): method for method in methods}
            for requested_code in requested_codes:
                match = by_code.get(normalize(requested_code))
                if match:
                    return match

        # Accept harmless qualifiers added by the model, and use descriptions for
        # labels such as "карта МИР". Select only a unique best match.
        query_tokens = set(query.split())
        scored = []
        for method in methods:
            title = normalize(method.get("title"))
            description = normalize(method.get("description"))
            searchable = " ".join(part for part in (title, description) if part)
            searchable_tokens = set(searchable.split())
            score = 0
            if len(title) >= 3 and (title in query or query in title):
                score = 80 + min(len(set(title.split()) & query_tokens), 10)
            elif len(query_tokens) >= 2 and query_tokens.issubset(searchable_tokens):
                score = 60 + len(query_tokens)
            elif len(query_tokens & searchable_tokens) >= 2:
                score = 40 + len(query_tokens & searchable_tokens)
            if score:
                scored.append((score, method))
        if scored:
            scored.sort(key=lambda item: item[0], reverse=True)
            if len(scored) == 1 or scored[0][0] > scored[1][0]:
                return scored[0][1]
        return None

    async def _execute_tool_call(self, name: str, args: Dict, owner_telegram_id: int, user_info_service):
        """Выполнить один вызов инструмента. Возвращает кортеж:
        ("result", dict) — обычный результат, отдаётся AI как есть
        ("denied", str) — владение не подтверждено, AI получит объяснение вместо данных
        ("pending_action", PendingAction) — разрушающее действие подтверждено по владению,
            но не выполнено — ждёт подтверждения клиента"""
        try:
            if not user_info_service or not user_info_service.tool_enabled(name):
                return ("result", {"ok": False, "error": "инструмент отключен в настройках интеграции"})

            if name == "get_payment_methods":
                data = await user_info_service.get_payment_methods()
                if not data:
                    return ("result", {"ok": False, "error": "Не удалось получить способы оплаты"})
                return ("result", data)

            if name == "create_payment":
                request_mode = args.get("request_mode") or "new"
                if not isinstance(request_mode, str) or request_mode not in {"new", "retry"}:
                    return ("result", {"ok": False, "error": "invalid_request_mode"})
                if request_mode == "retry":
                    # Replaying an existing operation uses its immutable saved
                    # parameters, regardless of current payment method availability.
                    # The bot validates operation ownership and rejects ambiguity.
                    retry_params = {"request_mode": "retry"}
                    operation_id = args.get("operation_id")
                    if operation_id is not None:
                        if isinstance(operation_id, bool) or not isinstance(operation_id, int) or operation_id <= 0:
                            return ("result", {"ok": False, "error": "invalid_operation_id"})
                        retry_params["operation_id"] = operation_id
                    if args.get("amount") is not None:
                        amount = args["amount"]
                        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                            return ("result", {"ok": False, "error": "invalid_amount"})
                        retry_params["amount"] = amount
                    if args.get("method") is not None:
                        if not isinstance(args["method"], str) or not args["method"].strip():
                            return ("result", {"ok": False, "error": "invalid_method"})
                        retry_params["method"] = args["method"].strip()
                    return ("pending_action", PendingAction(
                        action="create_payment", params=retry_params,
                        confirm_text="Повторить выбранную платёжную операцию с прежним ключом и сохранёнными параметрами?",
                    ))
                try:
                    raw_amount = args.get("amount")
                    if isinstance(raw_amount, bool) or isinstance(raw_amount, float) and not raw_amount.is_integer():
                        raise ValueError("Not an integer amount")
                    amount = int(raw_amount)
                except (TypeError, ValueError):
                    return ("result", {"ok": False, "error": "invalid_amount", "message": "Укажите сумму целым числом от 50 до 9999 рублей"})
                if amount < 50 or amount > 9999:
                    return ("result", {"ok": False, "error": "invalid_amount", "message": "Сумма должна быть от 50 до 9999 рублей"})
                methods_data = await user_info_service.get_payment_methods()
                if not methods_data or not methods_data.get("ok"):
                    return ("result", methods_data or {"ok": False, "error": "payment_methods_unavailable"})
                methods = methods_data.get("methods") or []
                selected = self._resolve_payment_method(args.get("method"), methods)
                if not selected:
                    return (
                        "result",
                        {
                            "ok": False,
                            "error": "payment_method_required" if methods else "payment_methods_unavailable",
                            "currency": methods_data.get("currency") or "RUB",
                            "methods": methods,
                        },
                    )
                code = str(selected.get("code") or "").strip()
                title = selected.get("title") or code
                try:
                    min_amount = int(float(selected.get("min_amount") or 50))
                    max_amount = int(float(selected.get("max_amount") or 9999))
                except (TypeError, ValueError):
                    min_amount, max_amount = 50, 9999
                if amount < min_amount or amount > max_amount:
                    return (
                        "result",
                        {
                            "ok": False,
                            "error": "invalid_amount",
                            "message": f"Для способа «{title}» доступна сумма от {min_amount} до {max_amount} рублей",
                        },
                    )
                action = PendingAction(
                    action="create_payment",
                    params={
                        "method": code,
                        "method_title": title,
                        "amount": amount,
                        "currency": selected.get("currency") or methods_data.get("currency") or "RUB",
                        "request_mode": request_mode,
                    },
                    confirm_text=f"Создать новый счёт на пополнение на {amount} ₽ через «{title}»?",
                )
                return ("pending_action", action)

            if name == "check_payment":
                tx_id = str(args.get("transaction_id") or "").strip()
                if not tx_id:
                    return ("result", {"ok": False, "error": "transaction_id не указан"})
                data = await user_info_service.check_payment(tx_id, owner_telegram_id)
                if not data:
                    return ("result", {"ok": False, "error": "Транзакция не найдена"})
                if not data.get("ok"):
                    return ("result", data)
                return ("result", data)

            if name in {"get_subscription_info", "get_subscription_devices", "reset_devices", "revoke_subscription", "delete_device"}:
                username_arg = args.get("subscription_username")
                subscription_id = args.get("subscription_id")
                if username_arg is not None and not isinstance(username_arg, str):
                    return ("result", {"ok": False, "error": "invalid_subscription_username"})
                username = (username_arg or "").strip()
                if subscription_id is not None and (isinstance(subscription_id, bool) or not isinstance(subscription_id, int) or subscription_id <= 0):
                    return ("result", {"ok": False, "error": "invalid_subscription_id"})
                if not username and subscription_id is None:
                    return ("result", {"ok": False, "error": "subscription_identifier_required", "message": "Выберите подписку по её коду или локальному id; название тарифа не является идентификатором."})
                selector = {}
                if username:
                    selector["username"] = username
                if subscription_id is not None:
                    selector["subscription_id"] = subscription_id
                owned = await user_info_service.find_own_subscription(owner_telegram_id, **selector)
                if not owned:
                    return ("denied", "Выбранная подписка не найдена среди подписок этого клиента — действие отклонено. Уточните код подписки.")
                # Trust only the selected account record. Never derive identity from
                # the tariff title, owner Telegram username, or model-generated IDs.
                username = str(owned.get("username") or "").strip() or None
                owned_id = owned.get("id")
                selector = {}
                if username:
                    selector["username"] = username
                if owned_id is not None:
                    selector["subscription_id"] = owned_id
                if not selector:
                    return ("denied", "В данных подписки нет username или id для безопасного выполнения операции.")
                label = subscription_display_name(owned)
                if not username and not owned.get("display_name") and owned_id is not None:
                    label = f"Подписка ID {owned_id} (код отсутствует)"
                if name == "get_subscription_info":
                    data = await user_info_service.get_subscription_info(**selector)
                    return ("result", data or {"ok": False, "error": "Не удалось получить данные подписки"})
                if name == "get_subscription_devices":
                    data = await user_info_service.get_subscription_devices(**selector)
                    return ("result", data or {"ok": False, "error": "Не удалось получить список устройств"})
                action_params = {"subscription_username": username, "subscription_id": owned_id}
                if name == "reset_devices":
                    action_params["short_id"] = owned.get("short_id")
                    confirm_text = (
                        f"⚠️ Точно сбросить ВСЕ устройства подписки «{label}»? "
                        "Все подключённые устройства отключатся, их нужно будет подключить заново."
                    )
                elif name == "revoke_subscription":
                    confirm_text = (
                        f"⚠️ Точно перевыпустить ссылку подписки «{label}»? "
                        "Будет создана новая ссылка, старые устройства перестанут работать. Срок подписки не продлевается."
                    )
                else:
                    hwid = args.get("hwid")
                    if not isinstance(hwid, str) or not hwid.strip():
                        return ("denied", "Не указано устройство из выбранной подписки — действие отклонено.")
                    action_params["hwid"] = hwid.strip()
                    confirm_text = f"⚠️ Удалить это устройство из подписки «{label}»?"
                return ("pending_action", PendingAction(action=name, params=action_params, confirm_text=confirm_text))

            return ("result", {"ok": False, "error": f"неизвестный инструмент {name}"})
        except Exception as e:
            logger.warning(f"Tool execution error ({name}): {e}")
            return ("result", {"ok": False, "error": "внутренняя ошибка"})

    def _extract_text_tool_call(self, content: str) -> Optional[tuple[str, Dict]]:
        """Parse tool calls emitted as text by partially compatible providers.

        Besides the common ``functions.name {json}`` fallback, DeepSeek can emit
        DSML tags in ``message.content`` even when OpenAI-compatible tools were
        requested. These tags must be executed as tools, never shown to clients.
        """
        text = content or ""
        marker = re.search(r"(?:^|\s)(?:To\s*=\s*)?functions\.([a-zA-Z0-9_]+)", text)
        name = ""
        args = None
        if marker:
            name = marker.group(1)
            json_start = text.find("{", marker.end())
            if json_start >= 0:
                try:
                    args, _ = json.JSONDecoder().raw_decode(text[json_start:])
                except Exception:
                    args = None

        if args is None:
            # DeepSeek DSML uses full-width vertical bars. Normalise only markup
            # characters; parameter values are kept intact.
            dsml = text.replace("｜", "|").replace("＜", "<").replace("＞", ">")
            invoke = re.search(
                r'<\|\|DSML\|\|\s+invoke\s+name=["\']([a-zA-Z0-9_]+)["\']\s*>(.*?)'
                r'</\|\|DSML\|\|\s+invoke\s*>',
                dsml,
                flags=re.DOTALL | re.IGNORECASE,
            )
            if invoke:
                name = invoke.group(1)
                args = {}
                param_pattern = re.compile(
                    r'<\|\|DSML\|\|\s+parameter\s+name=["\']([^"\']+)["\']'
                    r'(?:\s+string=["\'](true|false)["\'])?\s*>(.*?)'
                    r'</\|\|DSML\|\|\s+parameter\s*>',
                    flags=re.DOTALL | re.IGNORECASE,
                )
                for param in param_pattern.finditer(invoke.group(2)):
                    key = param.group(1).strip()
                    raw_value = unescape(param.group(3).strip())
                    if (param.group(2) or "").casefold() == "true":
                        value = raw_value
                    else:
                        try:
                            value = json.loads(raw_value)
                        except Exception:
                            value = raw_value
                    args[key] = value

        if not name or not isinstance(args, dict):
            return None
        name = _TEXT_TOOL_ALIASES.get(name, name)
        if "subscription_username" not in args:
            for key in ("username", "subscription", "subscription_name", "short_id"):
                if args.get(key):
                    args["subscription_username"] = args[key]
                    break
        return name, args

    @staticmethod
    def _contains_tool_protocol(content: str) -> bool:
        """Return True for leaked provider/tool protocol that is unsafe to display."""
        return bool(TOOL_PROTOCOL.search(content or ""))

    async def _try_provider_request(
        self,
        provider: AIProvider,
        question: str,
        context: Optional[Dict] = None,
        chat_history: Optional[List[Dict]] = None,
        image_data_url: Optional[str] = None,
        execute_tools: bool = False,
        user_info_service=None,
        tool_event_callback=None,
        stream_callback=None,
    ) -> Optional[Union[str, PendingAction]]:
        """Попытка запроса к конкретному провайдеру с поддержкой нескольких ключей"""
        url = endpoint_url(provider.base_url, "chat/completions")
        
        # Собираем все ключи провайдера (основной + дополнительные)
        all_keys = [provider.api_key] if provider.api_key else []
        if provider.api_keys:
            try:
                additional_keys = json.loads(provider.api_keys) if isinstance(provider.api_keys, str) else provider.api_keys
                if isinstance(additional_keys, list):
                    all_keys.extend([k for k in additional_keys if k])
            except Exception:
                pass
        
        if not all_keys:
            logger.warning(f"Провайдер {provider.name} не имеет API ключей")
            return None
        
        # Случайный выбор ключа для балансировки нагрузки
        import random
        api_key = random.choice(all_keys)
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        if provider.api_type == "groq" and not provider.base_url:
            url = "https://api.groq.com/openai/v1/chat/completions"

        service_context = await self._build_service_context(context, question=question)
        user_context = self._build_user_context(context)
        project_name = self._project_name or "DELTA-Support"
        
        tpl = self._system_prompt or DEFAULT_SUPPORT_PROMPT
        
        class _SafeDict(dict):
            def __missing__(self, key): return "{" + key + "}"

        ctx = dict(context or {})
        ctx.update({
            "project_name": project_name,
            "service_context": service_context,
            "user_context": user_context,
            # Данные аккаунта из Support API (если интеграция включена)
            "account_info": (ctx.get("account_info") or "")
        })
        system_prompt = tpl.format_map(_SafeDict(ctx))

        # Если кастомный промпт не содержит плейсхолдер {account_info},
        # всё равно добавляем данные аккаунта в конец системного промпта
        account_info = ctx.get("account_info") or ""
        if account_info and "{account_info}" not in tpl:
            system_prompt = f"{system_prompt}\n\n{account_info}"
        payment_context = ctx.get("payment_context") or ""
        if payment_context and "{payment_context}" not in tpl:
            system_prompt = f"{system_prompt}\n\n{payment_context}"

        # Always retain account/KB context with custom templates and images.
        if "{service_context}" not in tpl:
            system_prompt += f"\n\n{service_context}"
        if "{user_context}" not in tpl:
            system_prompt += f"\n\n{user_context}"
        system_prompt = f"{SUPPORT_POLICY}\n\n{system_prompt}"

        messages = [{"role": "system", "content": system_prompt}]
        
        if chat_history:
            for msg in chat_history:
                role = msg.get("role", "user")
                content = msg.get("message", "") or msg.get("content", "")
                if content and role in ("user", "assistant"):
                    messages.append({"role": role, "content": content})
        
        if image_data_url:
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            })
        else:
            messages.append({"role": "user", "content": question})

        if provider.api_type == "groq" and "llama-3.1-8b-instant" in (provider.model_name or "").lower():
            system_message = str(messages[0].get("content") or "")
            if len(system_message) > 3500:
                messages[0]["content"] = (
                    system_message[:2100]
                    + "\n…(сервисный контекст сокращён под лимит модели)…\n"
                    + system_message[-1350:]
                )
            if len(messages) > 6:
                messages = [messages[0], *messages[-5:]]

        payload = {
            "model": provider.model_name,
            "messages": messages,
            "temperature": 0.7,
            # Reasoning tokens share the output budget with the visible answer.
            "max_tokens": 8192 if reasoning_model(provider.model_name) else 4096
        }
        # Qwen3 на Groq по умолчанию отдает reasoning-блок <think> — прячем его
        if provider.api_type == "groq" and "qwen" in (provider.model_name or "").lower():
            payload["reasoning_format"] = "hidden"
            payload["max_tokens"] = 4096

        payload = apply_chat_compatibility(payload, provider.api_type)
        enabled_tool_names = set(user_info_service.get_enabled_tools()) if user_info_service else set()
        allowed_tools = [t for t in (_READONLY_TOOLS + _DESTRUCTIVE_TOOLS) if t["function"]["name"] in enabled_tool_names]
        if execute_tools:
            allowed_tools += [OFFER_MANAGER_TOOL]
            if (context or {}).get("chat_id"):
                allowed_tools.append(REPORT_KNOWLEDGE_GAP_TOOL)
            payload["tools"] = allowed_tools
            payload["tool_choice"] = "auto"
        allowed_names = {t["function"]["name"] for t in allowed_tools} if execute_tools else set()
        offer_manager = False
        executed = {}

        async def run_tool(name, arguments):
            nonlocal offer_manager
            try:
                args = json.loads(arguments) if isinstance(arguments, str) else arguments
                if not isinstance(args, dict):
                    raise ValueError("Expected object")
            except (ValueError, TypeError):
                return "result", {"ok": False, "error": "invalid_arguments", "message": "Аргументы должны быть JSON-объектом по схеме инструмента."}
            if name not in allowed_names:
                return "result", {"ok": False, "error": "tool_not_available"}
            fingerprint = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
            if fingerprint in executed:
                return executed[fingerprint]
            await self._emit_tool_event(tool_event_callback, {
                "phase": "requested", "tool": name, "args": args,
                "provider": provider.name, "model": provider.model_name,
            })
            if name == "offer_manager":
                accepted = validate_manager_offer(args, question, chat_history)
                offer_manager = offer_manager or accepted
                kind, result = "result", {
                    "ok": accepted, "button_offered": accepted, "manager_invited": False,
                    "message": "Кнопка будет показана с ответом; менеджер еще не вызван." if accepted else "Сначала уточните детали и помогите клиенту; оснований для эскалации пока недостаточно.",
                }
            elif name == "report_knowledge_gap":
                try:
                    result = await record_knowledge_gap(
                        topic=args.get("topic"), reason=args.get("reason"),
                        example_question=args.get("example_question"), chat_id=(context or {}).get("chat_id"),
                    )
                except Exception:
                    logger.warning("Could not record a knowledge-base suggestion")
                    result = {"ok": False, "error": "suggestion_unavailable"}
                kind = "result"
            else:
                kind, result = await self._execute_tool_call(name, args, (context or {}).get("user_id"), user_info_service)
            executed[fingerprint] = (kind, result)
            event_result = ({"action": result.action, "params": result.params, "confirm_text": result.confirm_text}
                if isinstance(result, PendingAction) else result)
            await self._emit_tool_event(tool_event_callback, {
                "phase": "pending" if kind == "pending_action" else "result", "tool": name,
                "kind": kind, "result": event_result, "provider": provider.name, "model": provider.model_name,
            })
            return kind, result

        async with httpx.AsyncClient() as client:
            # Bounded multi-step tool loop: information lookup may be needed before
            # the next tool. Keep native Responses output/reasoning on every turn.
            for round_index in range(7):
                request_payload = {**payload, "messages": messages}
                if round_index == 6:
                    request_payload.pop("tools", None)
                    request_payload.pop("tool_choice", None)
                if provider.api_type == "openai_v2":
                    response = await request_responses(client, url, headers, request_payload, stream_callback)
                else:
                    response = await self._request_chat_completion(client, url, headers, request_payload, stream_callback)
                message = response["choices"][0]["message"]
                content = clean_response_text(message.get("content") or "")
                tool_calls = message.get("tool_calls") or []
                text_call = self._extract_text_tool_call(content)
                if text_call and not tool_calls and provider.api_type != "openai_v2":
                    name, args = text_call
                    tool_calls = [{"id": "text_" + uuid4().hex, "type": "function", "function": {
                        "name": name, "arguments": json.dumps(args, ensure_ascii=False),
                    }}]
                    message = {"role": "assistant", "content": None, "tool_calls": tool_calls}
                if tool_calls:
                    await emit_preview(stream_callback, "")
                    if not execute_tools or round_index == 6 or len(tool_calls) > 12:
                        return None
                    # Preserve _responses_output so encrypted reasoning and call IDs
                    # survive the function_call_output continuation with store=False.
                    messages.append({**message, "role": "assistant"})
                    for call in tool_calls:
                        fn = call.get("function") or {}
                        kind, result = await run_tool(fn.get("name", ""), fn.get("arguments", "{}"))
                        if kind == "pending_action":
                            return result
                        tool_result = result if kind == "result" else {"ok": False, "error": result}
                        messages.append({"role": "tool", "tool_call_id": call.get("id"),
                            "content": json.dumps(tool_result, ensure_ascii=False, default=str)})
                    continue
                if self._contains_tool_protocol(content):
                    logger.warning("Suppressed internal tool protocol from provider %s", provider.name)
                    return None
                if content:
                    return SupportAnswer(content, offer_manager=offer_manager)
                raise ValueError("Empty response from AI")
        return None

    async def _request_chat_completion(self, client, url, headers, payload, stream_callback=None):
        key = (url, payload.get("model"))
        unsupported = getattr(self, "_stream_unsupported_until", {})
        if stream_callback and unsupported.get(key, 0) <= time.monotonic():
            try:
                return await read_chat_stream(client, url, headers, payload, stream_callback)
            except StreamingUnsupported:
                # Retry once without streaming, then remember this model/endpoint
                # for an hour. Other validation/authentication errors are not retried.
                unsupported[key] = time.monotonic() + 3600
                self._stream_unsupported_until = unsupported
                logger.info("Streaming unsupported for model %s; using a completed reply", payload.get("model"))
        return await self._post_chat_completion(client, url, headers, payload)

    @staticmethod
    async def _post_chat_completion(client: httpx.AsyncClient, url: str, headers: dict, payload: dict) -> dict:
        """POST к /chat/completions с обработкой типовых кодов ошибок — общий код для
        первого запроса и follow-up после исполнения tool calls"""
        response = await client.post(url, headers=headers, json=payload, timeout=40.0)
        if response.status_code == 200:
            return response.json()
        if response.status_code == 429:
            raise Exception("Rate limit exceeded (429)")
        if response.status_code == 401:
            raise Exception("Invalid API key (401)")
        if response.status_code == 400:
            raise ValueError("AI request rejected (400): check model and provider settings")
        raise ValueError(f"AI HTTP error {response.status_code}")

    def _build_user_context(self, context: Optional[Dict] = None) -> str:
        """Построить контекст о пользователе"""
        if not context:
            return ""
        
        parts = []
        parts.append("ИНФОРМАЦИЯ О ТЕКУЩЕМ ПОЛЬЗОВАТЕЛЕ:")
        
        user_fields = []
        if context.get("first_name"):
            user_fields.append(f"Имя: {context['first_name']}")
        if context.get("last_name"):
            user_fields.append(f"Фамилия: {context['last_name']}")
        if context.get("username"):
            user_fields.append(f"Username: @{context['username']}")
        if context.get("user_id"):
            user_fields.append(f"Telegram ID: {context['user_id']}")
        
        if user_fields:
            parts.append(", ".join(user_fields))
            parts.append(f"Обращайся к пользователю по имени '{context.get('first_name', 'Уважаемый клиент')}'")
        else:
            parts.append("Обращайся к пользователю на 'Вы'")
        
        return "\n".join(parts)

    async def _build_base_context(self) -> str:
        """Статичная часть контекста о сервисе (без базы знаний) — кешируется"""
        now = time.monotonic()
        if now - self._service_context_ts < self._context_cache_ttl:
            return self._service_context_cache

        parts = []
        parts.append("ИНФОРМАЦИЯ О СЕРВИСЕ:")
        parts.append(f"Название: {self._project_name}")

        if self._project_description:
            parts.append(f"\nОписание сервиса:\n{self._project_description}")

        if self._project_website:
            parts.append(f"\nВеб-сайт: {self._project_website}")

        if self._project_bot_link:
            parts.append(f"Ссылка на бота: {self._project_bot_link}")

        if self._project_owner_contacts:
            parts.append(f"Контакты поддержки: {self._project_owner_contacts}")

        parts.append(f"Часы работы поддержки: {self._service_support_hours}")

        if self._service_features:
            parts.append(f"\nОсобенности сервиса:\n{self._service_features}")

        if self._service_tariffs:
            parts.append(f"\nТарифы и цены:\n{self._service_tariffs}")

        if self._service_faq:
            parts.append(f"\nЧасто задаваемые вопросы:\n{self._service_faq}")

        if self._service_instructions:
            parts.append(f"\nИнструкции по использованию:\n{self._service_instructions}")

        self._service_context_cache = "\n".join(parts)
        self._service_context_ts = now
        return self._service_context_cache

    # Статьи БЗ, которые включаем целиком без отбора, пока их немного —
    # отбор под вопрос включается только когда база разрастается
    _KB_INLINE_ALL_THRESHOLD = 6
    # Сколько статей включать в промпт после отбора по релевантности
    _KB_TOP_K = 5

    def _select_kb_entries(self, kb_entries: List, question: Optional[str]) -> List:
        """Лёгкий RAG без embeddings: лексический скоринг статей БЗ по вопросу.
        При маленькой базе (<= _KB_INLINE_ALL_THRESHOLD статей) включаем всё —
        отбор нужен только когда база разрослась и не помещается в промпт целиком."""
        if not kb_entries or len(kb_entries) <= self._KB_INLINE_ALL_THRESHOLD or not question:
            return kb_entries

        from modules.stats import _tokenize

        def _stems(text: str) -> set:
            # Обрезка до 6 символов — грубый, но бесплатный аналог стемминга:
            # ловит совпадения вроде "устройства"/"устройствами" без словарей и ML
            return {w[:6] for w in _tokenize(text)}

        q_tokens = _stems(question)
        if not q_tokens:
            return kb_entries[: self._KB_TOP_K]

        scored = []
        for entry in kb_entries:
            title_tokens = _stems(entry.title)
            content_tokens = _stems(entry.content)
            score = 3 * len(q_tokens & title_tokens) + len(q_tokens & content_tokens)
            scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)

        top = [entry for score, entry in scored[: self._KB_TOP_K] if score > 0]
        # Ничего не совпало лексически — не оставляем AI совсем без базы знаний
        return top if top else kb_entries[: self._KB_TOP_K]

    async def _build_service_context(self, context: Optional[Dict] = None, question: Optional[str] = None) -> str:
        """Контекст о сервисе: статичная часть + релевантные вопросу статьи БЗ"""
        base = await self._build_base_context()

        kb_entries = await self._get_kb_entries()
        selected = self._select_kb_entries(kb_entries, question)
        if not selected:
            return base

        kb_parts = ["\n\nБАЗА ЗНАНИЙ (FAQ и инструкции):"]
        for entry in selected:
            kb_parts.append(f"\n--- {entry.title} ---")
            kb_parts.append(entry.content)

        return base + "\n".join(kb_parts)
