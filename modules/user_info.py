"""
Интеграционный слой данных о клиенте.

Сейчас поддерживаются два канала:
  - Support API
  - Remnawave v3+

Одновременно активен только один канал. Настройки хранятся в SystemConfig,
а .env используется как bootstrap/fallback.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from modules.config import Config
from modules.database import SystemConfig
from modules.remnawave import RemnawaveAPI, RemnawaveAPIError

logger = logging.getLogger(__name__)

_TRUE_VALUES = ("1", "true", "yes", "y", "on")
_SUPPORTED_CHANNELS = ("none", "support_api", "remnawave")
_TOOL_CHANNELS = ("support_api", "remnawave")
_PAYMENT_TOOLS = {"get_payment_methods", "create_payment", "check_payment"}

INTEGRATION_TOOL_META = {
    "get_payment_methods": {
        "label": "Способы оплаты",
        "description": "AI может получить включённые способы оплаты, валюту и допустимые суммы.",
        "destructive": False,
    },
    "create_payment": {
        "label": "Создание ссылки на оплату",
        "description": "AI может подготовить счёт на пополнение. Ссылка создаётся только после подтверждения клиента.",
        "destructive": True,
    },
    "check_payment": {
        "label": "Проверка платежа",
        "description": "AI может уточнять статус платежа по transaction_id/order_id.",
        "destructive": False,
    },
    "get_subscription_info": {
        "label": "Информация о подписке",
        "description": "AI может читать срок, статус, трафик и лимит устройств подписки клиента.",
        "destructive": False,
    },
    "get_subscription_devices": {
        "label": "Список устройств",
        "description": "AI может получать список устройств, привязанных к подписке клиента.",
        "destructive": False,
    },
    "reset_devices": {
        "label": "Сброс всех устройств",
        "description": "AI может предложить сбросить все устройства подписки. Выполнение только после подтверждения клиента.",
        "destructive": True,
    },
    "revoke_subscription": {
        "label": "Перевыпуск подписки",
        "description": "AI может предложить перевыпустить подписку. Выполнение только после подтверждения клиента.",
        "destructive": True,
    },
    "delete_device": {
        "label": "Удаление одного устройства",
        "description": "AI может предложить удалить конкретное устройство по HWID. Выполнение только после подтверждения клиента.",
        "destructive": True,
    },
}

INTEGRATION_TOOL_DEFAULTS = {name: True for name in INTEGRATION_TOOL_META.keys()}
INTEGRATION_CONTEXT_DEFAULTS = {
    "provide_ai_context": True,
    "provide_manager_card": True,
}
INTEGRATION_FEATURE_DEFAULTS = {
    "payments": True,
    "subscription_profile": True,
    "subscription_url": True,
}
INTEGRATION_FEATURE_META = {
    "payments": {
        "label": "Платежи",
        "description": "Разрешить AI показывать способы оплаты, создавать ссылки на пополнение и проверять платежи.",
        "channels": ["support_api"],
    },
    "subscription_profile": {
        "label": "Профиль подписки",
        "description": "Short ID, трафик, лимиты и технические данные подписки Remnawave.",
        "channels": ["remnawave"],
    },
    "subscription_url": {
        "label": "Выдача subscription URL",
        "description": "Основная ссылка подписки Remnawave для выдачи клиенту, AI и менеджеру.",
        "channels": ["remnawave"],
    },
}
INTEGRATION_CHANNEL_TOOL_SUPPORT = {
    "support_api": {
        "get_payment_methods",
        "create_payment",
        "check_payment",
        "get_subscription_info",
        "get_subscription_devices",
        "reset_devices",
        "revoke_subscription",
        "delete_device",
    },
    "remnawave": {
        "get_subscription_info",
        "get_subscription_devices",
        "reset_devices",
        "revoke_subscription",
        "delete_device",
    },
    "none": set(),
}


@dataclass
class PendingAction:
    """Разрушающее действие, которое AI предложил, но не выполнил без подтверждения клиента."""

    action: str
    params: Dict = field(default_factory=dict)
    confirm_text: str = ""


def _fmt_gb(value_bytes) -> str:
    try:
        n = int(value_bytes or 0)
    except (TypeError, ValueError):
        return "?"
    if n <= 0:
        return "0"
    gb = n / (1024**3)
    return f"{gb:.1f}" if gb < 100 else f"{gb:.0f}"


def _fmt_date(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.year >= 2070:
            return "бессрочно"
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return str(iso)[:10]


def _fmt_datetime(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(iso)[:16]


def _to_iso(value) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _as_bool(raw, default: bool) -> bool:
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in _TRUE_VALUES


class UserInfoService:
    """Единая точка доступа к внешнему источнику данных о клиенте."""

    def __init__(self, config: Config):
        self.config = config
        self._settings_ts = 0.0
        self._settings_cache_ttl = 5.0
        self.active_channel = self._normalize_channel(getattr(config, "integration_active_channel", "support_api"))

        self.support_api_enabled = bool(getattr(config, "support_api_enabled", False))
        self.support_api_url = (getattr(config, "support_api_url", "") or "").strip()
        self.support_api_token = (getattr(config, "support_api_token", "") or "").strip()
        self.support_api_cache_ttl = 120.0

        self.remnawave_enabled = bool(getattr(config, "remnawave_enabled", False))
        self.remnawave_url = (getattr(config, "remnawave_url", "") or "").strip()
        self.remnawave_api_key = (getattr(config, "remnawave_api_key", "") or "").strip()
        self.remnawave_secret_key = (getattr(config, "remnawave_secret_key", "") or "").strip()
        self.remnawave_auth_type = (getattr(config, "remnawave_auth_type", "") or "api_key").strip().lower()
        self.remnawave_username = (getattr(config, "remnawave_username", "") or "").strip()
        self.remnawave_password = (getattr(config, "remnawave_password", "") or "").strip()
        self.remnawave_caddy_token = (getattr(config, "remnawave_caddy_token", "") or "").strip()
        self.remnawave_cache_ttl = 120.0

        self._tool_flags = {channel: dict(INTEGRATION_TOOL_DEFAULTS) for channel in _TOOL_CHANNELS}
        self._provide_ai_context = INTEGRATION_CONTEXT_DEFAULTS["provide_ai_context"]
        self._provide_manager_card = INTEGRATION_CONTEXT_DEFAULTS["provide_manager_card"]
        self._feature_flags = dict(INTEGRATION_FEATURE_DEFAULTS)
        self._user_cache: Dict[str, tuple] = {}

    @staticmethod
    def _normalize_channel(raw: Optional[str]) -> str:
        value = (raw or "").strip().lower()
        return value if value in _SUPPORTED_CHANNELS else "none"

    @classmethod
    def get_tool_meta(cls) -> Dict[str, Dict]:
        return INTEGRATION_TOOL_META

    @classmethod
    def get_feature_meta(cls) -> Dict[str, Dict]:
        return INTEGRATION_FEATURE_META

    @classmethod
    def get_channel_tool_support(cls) -> Dict[str, set]:
        return INTEGRATION_CHANNEL_TOOL_SUPPORT

    def _cache_key(self, channel: str, telegram_id: int) -> str:
        return f"{channel}:{int(telegram_id)}"

    async def _refresh_settings(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self._settings_ts < self._settings_cache_ttl:
            return
        keys = [
            "integration_active_channel",
            "integration_provide_ai_context",
            "integration_provide_manager_card",
            "integration_feature_payments",
            "integration_feature_subscription_profile",
            "integration_feature_subscription_url",
            "support_api_enabled",
            "support_api_url",
            "support_api_token",
            "support_api_cache_ttl",
            "remnawave_enabled",
            "remnawave_url",
            "remnawave_api_key",
            "remnawave_secret_key",
            "remnawave_auth_type",
            "remnawave_username",
            "remnawave_password",
            "remnawave_caddy_token",
            "remnawave_cache_ttl",
        ] + [f"integration_tool_{name}" for name in INTEGRATION_TOOL_META.keys()] + [
            f"integration_tool_{channel}_{name}"
            for channel in _TOOL_CHANNELS
            for name in INTEGRATION_TOOL_META.keys()
        ]
        try:
            rows = await SystemConfig.filter(key__in=keys).all()
            values = {r.key: (r.value or "") for r in rows}

            self.support_api_enabled = _as_bool(values.get("support_api_enabled"), bool(getattr(self.config, "support_api_enabled", False)))
            self.support_api_url = (values.get("support_api_url") or getattr(self.config, "support_api_url", "") or "").strip().rstrip("/")
            self.support_api_token = (values.get("support_api_token") or getattr(self.config, "support_api_token", "") or "").strip()
            ttl_raw = (values.get("support_api_cache_ttl") or "").strip()
            self.support_api_cache_ttl = max(10.0, float(int(ttl_raw))) if ttl_raw.isdigit() else 120.0

            self.remnawave_enabled = _as_bool(values.get("remnawave_enabled"), bool(getattr(self.config, "remnawave_enabled", False)))
            self.remnawave_url = (values.get("remnawave_url") or getattr(self.config, "remnawave_url", "") or "").strip().rstrip("/")
            self.remnawave_api_key = (values.get("remnawave_api_key") or getattr(self.config, "remnawave_api_key", "") or "").strip()
            self.remnawave_secret_key = (values.get("remnawave_secret_key") or getattr(self.config, "remnawave_secret_key", "") or "").strip()
            self.remnawave_auth_type = (values.get("remnawave_auth_type") or getattr(self.config, "remnawave_auth_type", "") or "api_key").strip().lower()
            self.remnawave_username = (values.get("remnawave_username") or getattr(self.config, "remnawave_username", "") or "").strip()
            self.remnawave_password = (values.get("remnawave_password") or getattr(self.config, "remnawave_password", "") or "").strip()
            self.remnawave_caddy_token = (values.get("remnawave_caddy_token") or getattr(self.config, "remnawave_caddy_token", "") or "").strip()
            remna_ttl_raw = (values.get("remnawave_cache_ttl") or "").strip()
            self.remnawave_cache_ttl = max(10.0, float(int(remna_ttl_raw))) if remna_ttl_raw.isdigit() else 120.0

            active_raw = self._normalize_channel(values.get("integration_active_channel") or getattr(self.config, "integration_active_channel", ""))
            if active_raw != "none":
                self.active_channel = active_raw
            elif self.support_api_enabled:
                self.active_channel = "support_api"
            elif self.remnawave_enabled:
                self.active_channel = "remnawave"
            else:
                self.active_channel = "none"

            self._provide_ai_context = _as_bool(
                values.get("integration_provide_ai_context"),
                INTEGRATION_CONTEXT_DEFAULTS["provide_ai_context"],
            )
            self._provide_manager_card = _as_bool(
                values.get("integration_provide_manager_card"),
                INTEGRATION_CONTEXT_DEFAULTS["provide_manager_card"],
            )
            for name, default in INTEGRATION_FEATURE_DEFAULTS.items():
                self._feature_flags[name] = _as_bool(values.get(f"integration_feature_{name}"), default)

            for channel in _TOOL_CHANNELS:
                for name, default in INTEGRATION_TOOL_DEFAULTS.items():
                    raw = values.get(f"integration_tool_{channel}_{name}")
                    if raw is None:
                        raw = values.get(f"integration_tool_{name}")
                    self._tool_flags[channel][name] = _as_bool(raw, default)

            self._settings_ts = now
        except Exception as e:
            logger.warning(f"Integration settings read error: {e}")
            self._settings_ts = now

    def invalidate_cache(self):
        self._settings_ts = 0.0
        self._user_cache.clear()

    def _remnawave_auth_ready(self) -> bool:
        if not self.remnawave_url:
            return False
        if self.remnawave_auth_type == "basic":
            return bool(self.remnawave_username and self.remnawave_password)
        if self.remnawave_auth_type == "caddy":
            return bool(self.remnawave_caddy_token or self.remnawave_api_key)
        return bool(self.remnawave_api_key)

    def is_configured(self, channel: Optional[str] = None) -> bool:
        channel = self._normalize_channel(channel or self.active_channel)
        if channel == "support_api":
            return bool(self.support_api_url and self.support_api_token)
        if channel == "remnawave":
            return bool(self._remnawave_auth_ready())
        return False

    def tool_enabled(self, name: str, channel: Optional[str] = None) -> bool:
        channel = self._normalize_channel(channel or self.active_channel)
        if channel == "support_api" and name in _PAYMENT_TOOLS and not self.feature_enabled("payments"):
            return False
        if channel == "remnawave" and name == "get_subscription_info" and not self.feature_enabled("subscription_profile"):
            return False
        return (
            self.is_configured(channel)
            and name in INTEGRATION_CHANNEL_TOOL_SUPPORT.get(channel, set())
            and bool((self._tool_flags.get(channel) or {}).get(name, False))
        )

    def feature_enabled(self, name: str) -> bool:
        return bool(self._feature_flags.get(name, False))

    def get_enabled_tools(self, channel: Optional[str] = None) -> List[str]:
        channel = self._normalize_channel(channel or self.active_channel)
        return [name for name in INTEGRATION_TOOL_META.keys() if self.tool_enabled(name, channel)]

    def channel_capabilities(self) -> Dict[str, Dict[str, bool]]:
        return {
            channel: {name: name in supported for name in INTEGRATION_TOOL_META.keys()}
            for channel, supported in INTEGRATION_CHANNEL_TOOL_SUPPORT.items()
        }

    @staticmethod
    def _subscription_has_profile_details(sub: Dict) -> bool:
        return any(
            key in sub
            for key in (
                "short_id",
                "device_limit",
                "traffic_limit_bytes",
                "traffic_used_bytes",
                "links",
                "happ_link",
                "happ_crypto_link",
            )
        )

    def _remnawave_api(self) -> RemnawaveAPI:
        return RemnawaveAPI(
            base_url=self.remnawave_url,
            api_key=self.remnawave_api_key,
            secret_key=self.remnawave_secret_key,
            username=self.remnawave_username,
            password=self.remnawave_password,
            caddy_token=self.remnawave_caddy_token,
            auth_type=self.remnawave_auth_type,
        )

    async def _support_api_request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict] = None,
        params: Optional[Dict] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict]:
        await self._refresh_settings()
        if not self.is_configured("support_api"):
            return None
        url = f"{self.support_api_url}{path}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.support_api_token}",
        }
        headers.update(extra_headers or {})
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method,
                    url,
                    headers=headers,
                    json=payload if method.upper() != "GET" else None,
                    params=params,
                    timeout=15.0,
                )
            try:
                data = resp.json()
            except Exception:
                data = {"ok": False, "error": "invalid_response"}
            if not isinstance(data, dict):
                data = {"ok": False, "error": "invalid_response"}
            data.setdefault("http_status", resp.status_code)
            if resp.status_code != 200:
                logger.warning(f"Support API: HTTP {resp.status_code} для {path}: {data.get('error')}")
                return data
            if not data.get("ok"):
                logger.info(f"Support API {path} error: {data.get('error')}")
                return data if data.get("error") else None
            return data
        except httpx.TimeoutException:
            logger.warning(f"Support API: таймаут для {path}")
            return {"ok": False, "error": "request_timeout"}
        except Exception as e:
            logger.warning(f"Support API: ошибка запроса {path}: {e}")
            return {"ok": False, "error": "request_failed"}

    async def _support_api_post(self, path: str, payload: Dict) -> Optional[Dict]:
        return await self._support_api_request("POST", path, payload=payload)

    def _prune_cache(self):
        if len(self._user_cache) <= 2000:
            return
        oldest = sorted(self._user_cache.items(), key=lambda kv: kv[1][0])[:1000]
        for key, _ in oldest:
            self._user_cache.pop(key, None)

    def _apply_exposure_permissions(self, data: Optional[Dict]) -> Optional[Dict]:
        if not data:
            return data
        if (data.get("channel") or self.active_channel) != "remnawave":
            return data
        result = dict(data)
        connections = dict(result.get("connections") or {})
        raw_subscriptions = [dict(sub) for sub in (connections.get("remna_subscriptions") or [])]
        profile_enabled = self.feature_enabled("subscription_profile")
        url_enabled = self.feature_enabled("subscription_url")

        if profile_enabled:
            if not url_enabled:
                for sub in raw_subscriptions:
                    sub.pop("subscription_url", None)
            connections["remna_subscriptions"] = raw_subscriptions
            result["connections"] = connections
            return result

        minimal_subscriptions = []
        if url_enabled:
            for sub in raw_subscriptions:
                subscription_url = sub.get("subscription_url")
                if not subscription_url:
                    continue
                minimal_subscriptions.append(
                    {
                        "id": sub.get("id"),
                        "username": sub.get("username"),
                        "tarif": sub.get("tarif"),
                        "is_currently_active": sub.get("is_currently_active"),
                        "subscription_url": subscription_url,
                    }
                )

        result["summary"] = dict(result.get("summary") or {})
        result["summary"]["remna_subscriptions_count"] = len(raw_subscriptions)
        result["summary"]["active_remna_subscriptions_count"] = sum(1 for sub in raw_subscriptions if sub.get("is_currently_active"))
        connections["remna_subscriptions"] = minimal_subscriptions
        connections["accessible_nodes"] = []
        result["connections"] = connections
        return result

    async def _get_support_api_user_info(self, telegram_id: int, force: bool = False) -> Optional[Dict]:
        now = time.monotonic()
        key = self._cache_key("support_api", telegram_id)
        if not force:
            cached = self._user_cache.get(key)
            if cached and now - cached[0] < self.support_api_cache_ttl:
                return cached[1]
        url = f"{self.support_api_url}/api/support/user_info"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.support_api_token}",
                    },
                    json={"user_id": int(telegram_id)},
                    timeout=10.0,
                )
            if resp.status_code != 200:
                logger.warning(f"Support API: HTTP {resp.status_code} для user {telegram_id}")
                return None
            data = resp.json()
            if not data.get("ok"):
                if data.get("error") == "user_not_found":
                    self._user_cache[key] = (now, None)
                else:
                    logger.warning(f"Support API error: {data.get('error')}")
                return None
            data["channel"] = "support_api"
            self._user_cache[key] = (now, data)
            self._prune_cache()
            return data
        except httpx.TimeoutException:
            logger.warning(f"Support API: таймаут для user {telegram_id}")
            return None
        except Exception as e:
            logger.warning(f"Support API: ошибка запроса для user {telegram_id}: {e}")
            return None

    def _remna_status_label(self, user: Dict) -> str:
        return str(user.get("status") or "").strip().upper() or "UNKNOWN"

    def _remna_tariff_label(self, user: Dict) -> str:
        squads = user.get("activeInternalSquads") or []
        squad_name = next((s.get("name") for s in squads if s.get("name")), None)
        return squad_name or user.get("tag") or user.get("description") or user.get("username") or "Remnawave"

    def _remna_is_active(self, user: Dict) -> bool:
        status = self._remna_status_label(user)
        expire_at = _parse_dt(user.get("expireAt"))
        if status != "ACTIVE":
            return False
        if expire_at and expire_at < datetime.now(timezone.utc):
            return False
        return True

    async def _build_remnawave_account(self, api: RemnawaveAPI, telegram_id: int, users: List[Dict]) -> Dict:
        subscriptions = []
        now = datetime.now(timezone.utc)
        created_dates = []
        active_count = 0
        for user in users:
            user_uuid = str(user.get("uuid") or user.get("id") or "")
            short_uuid = str(user.get("shortUuid") or "")
            created_at = _parse_dt(user.get("createdAt"))
            if created_at:
                created_dates.append(created_at)
            traffic = user.get("userTraffic") or {}
            is_active = self._remna_is_active(user)
            if is_active:
                active_count += 1
            subscription_info = {}
            links = {}
            if short_uuid:
                try:
                    subscription_info = await api.get_subscription_info(short_uuid)
                except Exception as e:
                    logger.debug(f"Remnawave subscription info failed for {short_uuid}: {e}")
                try:
                    links = await api.get_subscription_links(short_uuid)
                except Exception:
                    links = {}
            subscriptions.append(
                {
                    "id": user.get("id") or user_uuid or short_uuid,
                    "uuid": user_uuid or None,
                    "short_id": short_uuid or None,
                    "username": user.get("username"),
                    "tarif": self._remna_tariff_label(user),
                    "status": self._remna_status_label(user),
                    "status_label": self._remna_status_label(user),
                    "expire_at": _to_iso(user.get("expireAt")),
                    "traffic_limit_bytes": int(user.get("trafficLimitBytes") or 0),
                    "traffic_used_bytes": int(traffic.get("usedTrafficBytes") or 0),
                    "lifetime_used_traffic_bytes": int(traffic.get("lifetimeUsedTrafficBytes") or 0),
                    "device_limit": user.get("hwidDeviceLimit"),
                    "is_currently_active": is_active,
                    "subscription_url": user.get("subscriptionUrl") or subscription_info.get("subscriptionUrl"),
                    "happ_link": user.get("happLink") or subscription_info.get("happLink"),
                    "happ_crypto_link": user.get("happCryptoLink") or subscription_info.get("happCryptoLink"),
                    "links": links,
                    "online_at": _to_iso(traffic.get("onlineAt")),
                    "first_connected_at": _to_iso(traffic.get("firstConnectedAt")),
                    "last_connected_node_uuid": traffic.get("lastConnectedNodeUuid"),
                }
            )

        earliest_created = min(created_dates).isoformat() if created_dates else None
        disabled_count = sum(1 for user in users if self._remna_status_label(user) == "DISABLED")
        expired_count = sum(1 for user in users if _parse_dt(user.get("expireAt")) and _parse_dt(user.get("expireAt")) < now)
        return {
            "channel": "remnawave",
            "user": {
                "telegram_id": telegram_id,
                "created": earliest_created,
                "balance": None,
                "blocked": disabled_count == len(users) and len(users) > 0,
                "trial_available": None,
            },
            "summary": {
                "remna_subscriptions_count": len(subscriptions),
                "active_remna_subscriptions_count": active_count,
                "disabled_subscriptions_count": disabled_count,
                "expired_subscriptions_count": expired_count,
            },
            "connections": {
                "remna_subscriptions": subscriptions,
                "outline_keys": [],
                "xray_keys": [],
                "accessible_nodes": [],
            },
            "transactions": [],
            "actions": [],
        }

    async def _get_remnawave_user_info(self, telegram_id: int, force: bool = False) -> Optional[Dict]:
        now = time.monotonic()
        key = self._cache_key("remnawave", telegram_id)
        if not force:
            cached = self._user_cache.get(key)
            if cached and now - cached[0] < self.remnawave_cache_ttl:
                return cached[1]
        try:
            async with self._remnawave_api() as api:
                users = await api.get_user_by_telegram_id(int(telegram_id))
                if not users:
                    self._user_cache[key] = (now, None)
                    return None
                data = await self._build_remnawave_account(api, int(telegram_id), users)
                self._user_cache[key] = (now, data)
                self._prune_cache()
                return data
        except RemnawaveAPIError as e:
            logger.warning(f"Remnawave API error for user {telegram_id}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Remnawave request failed for user {telegram_id}: {e}")
            return None

    async def get_user_info(self, telegram_id: int, force: bool = False) -> Optional[Dict]:
        await self._refresh_settings()
        if not self.is_configured():
            return None
        if self.active_channel == "support_api":
            data = await self._get_support_api_user_info(telegram_id, force=force)
            return self._apply_exposure_permissions(data)
        if self.active_channel == "remnawave":
            data = await self._get_remnawave_user_info(telegram_id, force=force)
            return self._apply_exposure_permissions(data)
        return None

    async def get_ai_context(self, telegram_id: int) -> Optional[str]:
        if not self._provide_ai_context:
            return None
        data = await self.get_user_info(telegram_id)
        if not data:
            return None
        return self.format_for_ai(data)

    async def get_payment_methods(self) -> Optional[Dict]:
        await self._refresh_settings()
        if self.active_channel != "support_api" or not self.tool_enabled("get_payment_methods", "support_api"):
            return None
        return await self._support_api_request("GET", "/api/support/payment/methods")

    async def create_payment_link(
        self,
        owner_telegram_id: int,
        method: str,
        amount: int,
        idempotency_key: str,
    ) -> Optional[Dict]:
        await self._refresh_settings()
        if self.active_channel != "support_api" or not self.tool_enabled("create_payment", "support_api"):
            return None
        try:
            amount_value = int(amount)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_amount"}
        if amount_value < 50 or amount_value > 9999:
            return {"ok": False, "error": "invalid_amount"}
        return await self._support_api_request(
            "POST",
            "/api/support/payment/create",
            payload={
                "user_id": int(owner_telegram_id),
                "method": str(method or "").strip(),
                "amount": amount_value,
            },
            extra_headers={"Idempotency-Key": str(idempotency_key)},
        )

    async def check_payment(self, transaction_id: str, owner_telegram_id: int) -> Optional[Dict]:
        await self._refresh_settings()
        if self.active_channel != "support_api" or not self.tool_enabled("check_payment", "support_api"):
            return None
        data = await self._support_api_request(
            "GET",
            "/api/support/payment/check",
            params={"transaction_id": transaction_id},
        )
        if not data or not data.get("ok"):
            return data
        owner = (data.get("user") or {}).get("telegram_id")
        if owner is not None and int(owner) != int(owner_telegram_id):
            logger.warning(
                f"Support API: попытка получить чужую транзакцию {transaction_id} (owner={owner}, requester={owner_telegram_id})"
            )
            return None
        return data

    async def get_own_subscriptions(self, telegram_id: int) -> List[Dict]:
        if self.active_channel == "remnawave" and not self.feature_enabled("subscription_profile"):
            data = await self._get_remnawave_user_info(telegram_id, force=True)
        else:
            data = await self.get_user_info(telegram_id)
        if not data:
            return []
        return (data.get("connections") or {}).get("remna_subscriptions") or []

    async def find_own_subscription(
        self,
        telegram_id: int,
        username: Optional[str] = None,
        short_id: Optional[str] = None,
    ) -> Optional[Dict]:
        subs = await self.get_own_subscriptions(telegram_id)
        for sub in subs:
            if username and sub.get("username") == username:
                return sub
            if short_id and sub.get("short_id") == short_id:
                return sub
        return None

    async def get_subscription_info(self, username: Optional[str] = None, short_id: Optional[str] = None) -> Optional[Dict]:
        await self._refresh_settings()
        if not self.tool_enabled("get_subscription_info"):
            return None
        if self.active_channel == "support_api":
            payload = {"username": username} if username else {"short_id": short_id}
            return await self._support_api_post("/api/support/subscription_info", payload)
        if self.active_channel != "remnawave":
            return None
        try:
            async with self._remnawave_api() as api:
                user = None
                if username:
                    user = await api.get_user_by_username(username)
                elif short_id:
                    user = await api.get_user_by_short_uuid(short_id)
                if not user:
                    return None
                user_uuid = str(user.get("uuid") or user.get("id") or "")
                user_short_id = str(user.get("shortUuid") or short_id or "")
                info = await api.get_subscription_info(user_short_id) if user_short_id else {}
                subscription = await self._build_remnawave_account(api, int(user.get("telegramId") or 0), [user])
                sub = ((subscription.get("connections") or {}).get("remna_subscriptions") or [{}])[0]
                return {
                    "ok": True,
                    "channel": "remnawave",
                    "subscription": sub,
                    "links": info.get("links") or sub.get("links") or {},
                    "subscription_url": info.get("subscriptionUrl") or sub.get("subscription_url"),
                    "happ": info.get("happ"),
                    "accessible_nodes": [],
                }
        except Exception as e:
            logger.warning(f"Remnawave subscription info failed: {e}")
            return None
        return None

    async def get_subscription_devices(self, username: str) -> Optional[Dict]:
        await self._refresh_settings()
        if not self.tool_enabled("get_subscription_devices"):
            return None
        if self.active_channel == "support_api":
            return await self._support_api_post("/api/support/subscription/devices", {"username": username})
        if self.active_channel != "remnawave":
            return None
        try:
            async with self._remnawave_api() as api:
                user = await api.get_user_by_username(username)
                if not user:
                    return None
                devices = await api.get_user_devices_all(str(user.get("uuid") or user.get("id") or ""))
                return {
                    "ok": True,
                    "channel": "remnawave",
                    "subscription_username": username,
                    "total": devices.get("total") or 0,
                    "devices": devices.get("devices") or [],
                }
        except Exception as e:
            logger.warning(f"Remnawave devices failed: {e}")
            return None

    async def delete_device(self, username: str, hwid: str) -> Optional[Dict]:
        await self._refresh_settings()
        if not self.tool_enabled("delete_device"):
            return None
        if self.active_channel == "support_api":
            return await self._support_api_post("/api/support/subscription/device/delete", {"username": username, "hwid": hwid})
        if self.active_channel != "remnawave":
            return None
        try:
            async with self._remnawave_api() as api:
                user = await api.get_user_by_username(username)
                if not user:
                    return {"ok": False, "error": "user_not_found"}
                ok = await api.remove_device(str(user.get("uuid") or user.get("id") or ""), hwid)
                return {"ok": ok}
        except Exception as e:
            logger.warning(f"Remnawave delete device failed: {e}")
            return {"ok": False}

    async def reset_devices(self, short_id: str) -> Optional[Dict]:
        await self._refresh_settings()
        if not self.tool_enabled("reset_devices"):
            return None
        if self.active_channel == "support_api":
            return await self._support_api_post("/api/support/subscription/devices/reset", {"short_id": short_id})
        if self.active_channel != "remnawave":
            return None
        try:
            async with self._remnawave_api() as api:
                user = await api.get_user_by_short_uuid(short_id)
                if not user:
                    return {"ok": False, "error": "user_not_found"}
                ok = await api.reset_user_devices(str(user.get("uuid") or user.get("id") or ""))
                return {"ok": ok}
        except Exception as e:
            logger.warning(f"Remnawave reset devices failed: {e}")
            return {"ok": False}

    async def revoke_subscription(self, username: str) -> Optional[Dict]:
        await self._refresh_settings()
        if not self.tool_enabled("revoke_subscription"):
            return None
        if self.active_channel == "support_api":
            return await self._support_api_post("/api/support/subscription/revoke", {"username": username})
        if self.active_channel != "remnawave":
            return None
        try:
            async with self._remnawave_api() as api:
                user = await api.get_user_by_username(username)
                if not user:
                    return {"ok": False, "error": "user_not_found"}
                updated = await api.revoke_user_subscription(str(user.get("uuid") or user.get("id") or ""))
                new_short_id = str(updated.get("shortUuid") or user.get("shortUuid") or "")
                links = await api.get_subscription_links(new_short_id) if new_short_id else {}
                return {
                    "ok": True,
                    "short_id": new_short_id or None,
                    "subscription_url": updated.get("subscriptionUrl"),
                    "links": links,
                }
        except Exception as e:
            logger.warning(f"Remnawave revoke subscription failed: {e}")
            return {"ok": False}

    def _format_support_api_for_ai(self, data: Dict) -> str:
        user = data.get("user") or {}
        summary = data.get("summary") or {}
        conn = data.get("connections") or {}
        parts: List[str] = []
        parts.append("ДАННЫЕ АККАУНТА ПОЛЬЗОВАТЕЛЯ (из системы сервиса, актуальные):")
        parts.append(f"- Баланс: {user.get('balance', '0')} руб.")
        if user.get("blocked"):
            parts.append("- ⚠️ Аккаунт ЗАБЛОКИРОВАН")
        parts.append(f"- Пробный период доступен: {'да' if user.get('trial_available') else 'нет'}")
        parts.append(f"- Дата регистрации: {_fmt_date(user.get('created'))}")
        subs = conn.get("remna_subscriptions") or []
        active_subs = [s for s in subs if s.get("is_currently_active")]
        inactive_count = len(subs) - len(active_subs)
        parts.append(
            f"- Подписок: всего {summary.get('remna_subscriptions_count', len(subs))}, активных {summary.get('active_remna_subscriptions_count', len(active_subs))}"
        )
        for s in active_subs[:6]:
            limit = s.get("traffic_limit_bytes") or 0
            traffic = f"{_fmt_gb(s.get('traffic_used_bytes'))} ГБ из {'безлимит' if not limit else _fmt_gb(limit) + ' ГБ'}"
            extras = []
            if s.get("is_trial"):
                extras.append("пробная")
            if s.get("device_limit"):
                extras.append(f"устройств: {s['device_limit']}")
            extra_txt = f" ({', '.join(extras)})" if extras else ""
            parts.append(f"  • «{s.get('tarif', '?')}» до {_fmt_date(s.get('expire_at'))}, трафик {traffic}{extra_txt}")
            if s.get("subscription_url"):
                parts.append(f"    subscription_url: {s.get('subscription_url')}")
        if len(active_subs) > 6:
            parts.append(f"  • ... и еще {len(active_subs) - 6} активных подписок")
        if inactive_count > 0:
            parts.append(f"  • неактивных/истекших: {inactive_count}")
        outline = conn.get("outline_keys") or []
        if outline:
            names = ", ".join(k.get("profile_name", "?") for k in outline[:5])
            more = f" и еще {len(outline) - 5}" if len(outline) > 5 else ""
            parts.append(f"- Outline-ключей: {len(outline)} ({names}{more})")
        xray = conn.get("xray_keys") or []
        if xray:
            valid = sum(1 for k in xray if k.get("is_valid"))
            parts.append(f"- Xray-ключей: {len(xray)} (рабочих: {valid})")
            for k in xray[:5]:
                parts.append(f"  • «{k.get('name', '?')}» сервер {k.get('server', '?')}, {'работает' if k.get('is_valid') else 'НЕ работает'}")
        txs = data.get("transactions") or []
        if txs:
            parts.append("- Последние платежи:")
            for t in txs[:3]:
                status = "✅ оплачен" if t.get("status") else "⏳ не оплачен"
                parts.append(f"  • {t.get('summa', '?')} руб. через {t.get('method_label') or t.get('method', '?')}, {status}, {_fmt_datetime(t.get('created'))}")
        acts = data.get("actions") or []
        if acts:
            parts.append("- Последние действия по балансу:")
            for a in acts[:3]:
                parts.append(
                    f"  • {a.get('action_label') or a.get('action', '?')}: {a.get('amount', '?')} руб. ({_fmt_datetime(a.get('created_at'))}), баланс {a.get('balance_before', '?')} → {a.get('balance_after', '?')}"
                )
        parts.append(
            "Используй эти данные для точных ответов о балансе, подписках и ключах пользователя. Не выдумывай данные, которых здесь нет."
        )
        return "\n".join(parts)

    def _format_remnawave_for_ai(self, data: Dict) -> str:
        user = data.get("user") or {}
        summary = data.get("summary") or {}
        conn = data.get("connections") or {}
        subs = conn.get("remna_subscriptions") or []
        parts: List[str] = []
        parts.append("ДАННЫЕ АККАУНТА ПОЛЬЗОВАТЕЛЯ (Remnawave, актуальные):")
        parts.append("- Баланс и платежи в этом канале недоступны.")
        parts.append(f"- Telegram ID: {user.get('telegram_id', '—')}")
        parts.append(f"- Первая найденная регистрация: {_fmt_date(user.get('created'))}")
        if user.get("blocked"):
            parts.append("- ⚠️ Все найденные подписки клиента сейчас отключены.")
        parts.append(
            f"- Подписок: всего {summary.get('remna_subscriptions_count', len(subs))}, активных {summary.get('active_remna_subscriptions_count', 0)}"
        )
        if subs and not any(self._subscription_has_profile_details(sub) for sub in subs):
            parts.append("- Технический профиль подписок скрыт настройками интеграции; доступна только subscription_url.")
        for sub in subs[:8]:
            if not self._subscription_has_profile_details(sub):
                url_mark = f"subscription_url: {sub.get('subscription_url')}" if sub.get("subscription_url") else "subscription_url скрыта"
                parts.append(f"  • «{sub.get('tarif') or sub.get('username') or '?'}» / username {sub.get('username', '?')}: {url_mark}")
                continue
            limit = sub.get("traffic_limit_bytes") or 0
            traffic = f"{_fmt_gb(sub.get('traffic_used_bytes'))} ГБ из {'безлимит' if not limit else _fmt_gb(limit) + ' ГБ'}"
            extra = []
            if sub.get("device_limit"):
                extra.append(f"лимит устройств: {sub['device_limit']}")
            if sub.get("online_at"):
                extra.append(f"онлайн: {_fmt_datetime(sub.get('online_at'))}")
            extra_txt = f" ({', '.join(extra)})" if extra else ""
            parts.append(
                f"  • «{sub.get('tarif') or sub.get('username') or '?'}» / username {sub.get('username', '?')} / short_id {sub.get('short_id', '?')}: "
                f"статус {sub.get('status_label', '?')}, до {_fmt_date(sub.get('expire_at'))}, трафик {traffic}{extra_txt}"
            )
            if sub.get("subscription_url"):
                parts.append(f"    subscription_url: {sub.get('subscription_url')}")
        if len(subs) > 8:
            parts.append(f"  • ... и ещё {len(subs) - 8} подписок")
        parts.append(
            "Если пользователь просит ссылку подключения, отдавай subscription_url из данных подписки. Для устройств или управляющего действия используй доступные инструменты."
        )
        return "\n".join(parts)

    def format_for_ai(self, data: Dict) -> str:
        channel = data.get("channel") or self.active_channel
        if channel == "remnawave":
            return self._format_remnawave_for_ai(data)
        return self._format_support_api_for_ai(data)

    def _format_support_api_for_manager(self, data: Dict) -> str:
        user = data.get("user") or {}
        summary = data.get("summary") or {}
        conn = data.get("connections") or {}
        parts: List[str] = []
        parts.append("👤 Карточка клиента:")
        name = user.get("first_name") or "—"
        uname = f"@{user['username']}" if user.get("username") else "без username"
        parts.append(f"• {name} ({uname}), TG ID: {user.get('telegram_id', '?')}")
        parts.append(f"• Баланс: {user.get('balance', '0')} руб." + (" | 🚫 ЗАБЛОКИРОВАН" if user.get("blocked") else ""))
        parts.append(f"• Регистрация: {_fmt_date(user.get('created'))} | Триал доступен: {'да' if user.get('trial_available') else 'нет'}")
        subs = conn.get("remna_subscriptions") or []
        active_subs = [s for s in subs if s.get("is_currently_active")]
        parts.append(
            f"• Подписки: {summary.get('active_remna_subscriptions_count', len(active_subs))} актив. из {summary.get('remna_subscriptions_count', len(subs))}"
        )
        for s in active_subs[:4]:
            limit = s.get("traffic_limit_bytes") or 0
            traffic = f"{_fmt_gb(s.get('traffic_used_bytes'))}/{'∞' if not limit else _fmt_gb(limit)} ГБ"
            parts.append(f"   – «{s.get('tarif', '?')}» до {_fmt_date(s.get('expire_at'))}, {traffic}")
        if len(active_subs) > 4:
            parts.append(f"   – ... еще {len(active_subs) - 4}")
        outline_n = summary.get("outline_keys_count", len(conn.get("outline_keys") or []))
        xray = conn.get("xray_keys") or []
        xray_valid = summary.get("valid_xray_keys_count", sum(1 for k in xray if k.get("is_valid")))
        parts.append(f"• Ключи: Outline {outline_n} шт., Xray {len(xray)} шт. (рабочих {xray_valid})")
        txs = data.get("transactions") or []
        if txs:
            t = txs[0]
            status = "оплачен" if t.get("status") else "НЕ оплачен"
            parts.append(f"• Последний платеж: {t.get('summa', '?')} руб. ({t.get('method_label') or t.get('method', '?')}), {status}, {_fmt_datetime(t.get('created'))}")
        return "\n".join(parts)

    def _format_remnawave_for_manager(self, data: Dict) -> str:
        user = data.get("user") or {}
        summary = data.get("summary") or {}
        conn = data.get("connections") or {}
        subs = conn.get("remna_subscriptions") or []
        parts: List[str] = []
        parts.append("👤 Карточка клиента:")
        parts.append(f"• TG ID: {user.get('telegram_id', '?')}")
        parts.append(f"• Первая найденная регистрация: {_fmt_date(user.get('created'))}")
        if user.get("blocked"):
            parts.append("• 🚫 Все найденные подписки клиента отключены")
        parts.append(
            f"• Подписки: {summary.get('active_remna_subscriptions_count', 0)} актив. из {summary.get('remna_subscriptions_count', len(subs))}"
        )
        if subs and not any(self._subscription_has_profile_details(sub) for sub in subs):
            parts.append("• Технический профиль скрыт; в выдаче оставлена только subscription_url")
        for sub in subs[:4]:
            if not self._subscription_has_profile_details(sub):
                links_mark = f"subscription_url: {sub.get('subscription_url')}" if sub.get("subscription_url") else "без ссылки"
                parts.append(f"   – {sub.get('username', '?')}: {links_mark}")
                continue
            limit = sub.get("traffic_limit_bytes") or 0
            traffic = f"{_fmt_gb(sub.get('traffic_used_bytes'))}/{'∞' if not limit else _fmt_gb(limit)} ГБ"
            links_mark = "есть ссылка" if sub.get("subscription_url") else "без ссылки"
            parts.append(
                f"   – {sub.get('username', '?')} ({sub.get('status_label', '?')}) до {_fmt_date(sub.get('expire_at'))}, {traffic}, {links_mark}"
            )
        if len(subs) > 4:
            parts.append(f"   – ... еще {len(subs) - 4}")
        parts.append("• Баланс и платежи в этом канале недоступны")
        return "\n".join(parts)

    def format_for_manager(self, data: Dict) -> str:
        if not self._provide_manager_card:
            return ""
        channel = data.get("channel") or self.active_channel
        if channel == "remnawave":
            return self._format_remnawave_for_manager(data)
        return self._format_support_api_for_manager(data)
