from fastapi import APIRouter, Depends, HTTPException, Request
from modules.database import AdminUser, AIProvider
from web.deps import get_current_user
import json
import httpx
from modules.openai_api import (
    OpenAIAPIError, apply_chat_compatibility, endpoint_url, model_options, request_responses, raise_api_error, reasoning_model,
)

router = APIRouter(prefix="/api/ai/providers", tags=["ai_providers"])

def _require_admin(user: AdminUser):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

# Поддерживаемые провайдеры
SUPPORTED_PROVIDERS = {
    "openai_v2": {
        "name": "OpenAI v2 (Responses API)",
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "",
        "icon": "pi-microchip",
        "color": "#10a37f"
    },
    "openai": {
        "name": "OpenAI (Chat Completions)",
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "",
        "icon": "pi-microchip",
        "color": "#10a37f"
    },
    "groq": {
        "name": "Groq",
        "default_base_url": "https://api.groq.com/openai/v1",
        "default_model": "",
        "icon": "pi-bolt",
        "color": "#ff6b35"
    },
    "anthropic": {
        "name": "Anthropic (Claude)",
        "default_base_url": "https://api.anthropic.com/v1",
        "default_model": "",
        "icon": "pi-user",
        "color": "#cc785c"
    },
    "gemini": {
        "name": "Google Gemini",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "",
        "icon": "pi-sun",
        "color": "#8e7cc3"
    },
    "deepseek": {
        "name": "DeepSeek",
        "default_base_url": "https://api.deepseek.com/v1",
        "default_model": "",
        "icon": "pi-download",
        "color": "#0066cc"
    },
    "mistral": {
        "name": "Mistral AI",
        "default_base_url": "https://api.mistral.ai/v1",
        "default_model": "",
        "icon": "pi-cloud",
        "color": "#ff6b9d"
    },
    "openrouter": {
        "name": "OpenRouter",
        "default_base_url": "https://openrouter.ai/api/v1",
        "default_model": "",
        "icon": "pi-globe",
        "color": "#00d2d3"
    }
}

@router.get("")
async def list_providers(user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    providers = await AIProvider.all().order_by("priority", "id")
    result = []
    for p in providers:
        api_keys_data = p.api_keys
        if isinstance(api_keys_data, str):
            api_keys_data = json.loads(api_keys_data)
        if not isinstance(api_keys_data, list):
            api_keys_data = []
        
        masked_key = ""
        if p.api_key:
            masked_key = p.api_key[:6] + "***" + p.api_key[-4:] if len(p.api_key) > 15 else "***"
        
        result.append({
            "id": p.id,
            "name": p.name,
            "api_type": p.api_type,
            "api_key": masked_key,
            "api_keys_count": len(api_keys_data),
            "base_url": p.base_url,
            "model_name": p.model_name,
            "is_active": p.is_active,
            "priority": p.priority,
            "max_requests_per_minute": p.max_requests_per_minute,
            "provider_info": SUPPORTED_PROVIDERS.get(p.api_type, {}),
        })
    return result

@router.get("/types")
async def get_provider_types(user: AdminUser = Depends(get_current_user)):
    """Получить список поддерживаемых типов провайдеров"""
    _require_admin(user)
    return SUPPORTED_PROVIDERS

@router.get("/{provider_id}")
async def get_provider(provider_id: int, user: AdminUser = Depends(get_current_user)):
    """Получить данные провайдера включая реальные API ключи"""
    _require_admin(user)
    provider = await AIProvider.get_or_none(id=provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    
    api_keys_data = provider.api_keys
    if isinstance(api_keys_data, str):
        api_keys_data = json.loads(api_keys_data) if api_keys_data else []
    if not isinstance(api_keys_data, list):
        api_keys_data = []
    
    return {
        "id": provider.id,
        "name": provider.name,
        "api_type": provider.api_type,
        "api_key": provider.api_key or "",
        "api_keys": api_keys_data,
        "base_url": provider.base_url,
        "model_name": provider.model_name,
        "is_active": provider.is_active,
        "priority": provider.priority,
        "max_requests_per_minute": provider.max_requests_per_minute,
        "provider_info": SUPPORTED_PROVIDERS.get(provider.api_type, {}),
    }

@router.post("")
async def create_provider(request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    body = await request.json()
    name = body.get("name")
    api_type = body.get("api_type", "openai")
    api_key = body.get("api_key")
    model_name = body.get("model_name")
    
    if not all([name, api_key, model_name]):
        raise HTTPException(status_code=400, detail="name, api_key, model_name are required")
    
    if api_type not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail="Неизвестный тип провайдера")

    # Получаем дефолтные значения для типа провайдера
    provider_info = SUPPORTED_PROVIDERS.get(api_type, {})
    
    provider = await AIProvider.create(
        name=name,
        api_type=api_type,
        api_key=api_key,
        api_keys=body.get("api_keys"),  # Дополнительные ключи
        base_url=body.get("base_url") or provider_info.get("default_base_url"),
        model_name=model_name,
        is_active=body.get("is_active", True),
        priority=body.get("priority", 10),
        max_requests_per_minute=body.get("max_requests_per_minute", 60)
    )
    
    # Сброс кеша AI провайдеров
    try:
        from modules.ai_support import AISupport
        from modules.config import Config
        config = Config()
        ai = AISupport(config)
        ai.invalidate_cache()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to invalidate AI cache: {e}")
    
    return {"ok": True, "id": provider.id}

@router.patch("/{provider_id}")
async def update_provider(provider_id: int, request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    provider = await AIProvider.get_or_none(id=provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
        
    body = await request.json()
    if "api_type" in body and body["api_type"] not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail="Неизвестный тип провайдера")
    if "name" in body: provider.name = body["name"]
    if "api_type" in body: 
        provider.api_type = body["api_type"]
        # Автозаполняем base_url если выбран новый тип
        if not body.get("base_url"):
            provider.base_url = SUPPORTED_PROVIDERS.get(body["api_type"], {}).get("default_base_url")
    # Сохраняем API key только если он предоставлен и не пустой
    if "api_key" in body:
        new_key = body.get("api_key")
        if new_key and new_key.strip() and new_key != "********":
            provider.api_key = new_key.strip()
    # Сохраняем дополнительные API ключи
    if "api_keys" in body:
        provider.api_keys = body.get("api_keys")
    if "base_url" in body: provider.base_url = body["base_url"]
    if "model_name" in body: provider.model_name = body["model_name"]
    if "is_active" in body: provider.is_active = bool(body["is_active"])
    if "priority" in body: provider.priority = int(body["priority"])
    if "max_requests_per_minute" in body: provider.max_requests_per_minute = int(body["max_requests_per_minute"])
    
    await provider.save()
    
    # Сброс кеша AI провайдеров
    try:
        from modules.ai_support import AISupport
        from modules.config import Config
        config = Config()
        ai = AISupport(config)
        ai.invalidate_cache()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to invalidate AI cache: {e}")
    
    return {"ok": True}

@router.delete("/{provider_id}")
async def delete_provider(provider_id: int, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    deleted = await AIProvider.filter(id=provider_id).delete()
    if not deleted:
        raise HTTPException(status_code=404, detail="Provider not found")
    
    # Сброс кеша AI провайдеров
    try:
        from modules.ai_support import AISupport
        from modules.config import Config
        config = Config()
        ai = AISupport(config)
        ai.invalidate_cache()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to invalidate AI cache: {e}")
    
    return {"ok": True}

async def _connection(body):
    """Resolve saved keys on the server for model discovery and connection tests."""
    provider = None
    if body.get("provider_id") is not None:
        provider = await AIProvider.get_or_none(id=body["provider_id"])
        if provider is None:
            raise HTTPException(status_code=404, detail="Провайдер не найден")
    api_type = body.get("api_type") or (provider.api_type if provider else "openai_v2")
    if api_type not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail="Неизвестный тип провайдера")
    api_key = (body.get("api_key") or (provider.api_key if provider else "") or "").strip()
    if not api_key or "***" in api_key:
        raise HTTPException(status_code=400, detail="Укажите API ключ")
    base_url = body.get("base_url") or (provider.base_url if provider else None) or SUPPORTED_PROVIDERS[api_type]["default_base_url"]
    model = body.get("model_name") or (provider.model_name if provider else None)
    return api_type, api_key, base_url, model


def _error_message(error):
    if isinstance(error, httpx.TimeoutException):
        return "Таймаут соединения с провайдером"
    if isinstance(error, httpx.RequestError):
        return "Не удалось подключиться к провайдеру. Проверьте адрес и сеть"
    if isinstance(error, OpenAIAPIError):
        specific = {
            "responses_required": "Этой модели нужен тип провайдера OpenAI v2 (Responses API)",
            "unsupported_max_tokens": "Модель требует max_completion_tokens. Выберите OpenAI или OpenAI v2",
            "unsupported_max_completion_tokens": "Модель несовместима с Chat Completions. Попробуйте OpenAI v2",
            "unsupported_temperature": "Модель не поддерживает temperature. Попробуйте OpenAI v2",
            "unsupported_tools": "Выбранная модель не поддерживает эти инструменты. Выберите другую модель или OpenAI v2",
            "images_unsupported": "Выбранная модель не поддерживает изображения",
            "incomplete_response": "Модель не завершила ответ в пределах лимита. Попробуйте другую модель",
        }.get(error.code)
        if specific:
            return specific
        return {
            400: "Провайдер отклонил запрос. Проверьте модель и выбранный тип API",
            401: "Неверный API ключ", 403: "Доступ запрещён. Проверьте права ключа",
            404: "Модель или endpoint не найдены", 429: "Превышен лимит запросов или исчерпан баланс",
        }.get(error.status_code, "Провайдер не смог завершить ответ. Повторите проверку")
    return "Провайдер вернул некорректный ответ"


@router.post("/models")
async def discover_models(request: Request, user: AdminUser = Depends(get_current_user)):
    """Fetch the current account's model IDs; never change the chosen model."""
    _require_admin(user)
    api_type, api_key, base_url, _ = await _connection(await request.json())
    if api_type not in ("openai", "openai_v2", "groq", "deepseek", "mistral", "openrouter"):
        raise HTTPException(status_code=400, detail="Для этого провайдера укажите модель вручную")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(endpoint_url(base_url, "models"), headers={"Authorization": f"Bearer {api_key}"}, timeout=20.0)
            if response.status_code != 200:
                raise_api_error(response)
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                raise ValueError("Invalid models list")
            return {"models": model_options(data, api_type), "source": "api", "capabilities_verified": False}
    except (httpx.HTTPError, OpenAIAPIError, ValueError) as error:
        raise HTTPException(status_code=502, detail=_error_message(error)) from None


@router.post("/test")
async def test_provider(request: Request, user: AdminUser = Depends(get_current_user)):
    """Test the selected model through the selected API, without exposing secrets."""
    _require_admin(user)
    api_type, api_key, base_url, model_name = await _connection(await request.json())
    if not model_name:
        raise HTTPException(status_code=400, detail="Выберите модель из списка или укажите её ID")
    messages = [{"role": "user", "content": "Ответь одним словом: OK"}]
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {"model": model_name, "messages": messages, "max_tokens": 8192 if reasoning_model(model_name) else 4096, "temperature": 0.7}
    try:
        async with httpx.AsyncClient() as client:
            if api_type == "openai_v2":
                await request_responses(client, base_url, headers, payload)
            else:
                if api_type in ("openai", "groq", "deepseek", "mistral", "openrouter"):
                    payload = apply_chat_compatibility(payload, api_type)
                    url = endpoint_url(base_url, "chat/completions")
                elif api_type == "anthropic":
                    headers = {"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"}
                    payload.pop("temperature", None)
                    url = f"{base_url.rstrip('/')}/messages"
                else:
                    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
                    payload = {"contents": [{"parts": [{"text": messages[0]["content"]}]}], "generationConfig": {"maxOutputTokens": 4096}}
                    url = f"{base_url.rstrip('/')}/models/{model_name}:generateContent"
                response = await client.post(url, headers=headers, json=payload, timeout=90.0)
                if response.status_code != 200:
                    raise_api_error(response)
                data = response.json()
                if not isinstance(data, dict) or data.get("error"):
                    raise OpenAIAPIError(code="invalid_response")
        return {"ok": True, "status": "success", "message": "API и выбранная модель работают!"}
    except (httpx.HTTPError, OpenAIAPIError, ValueError) as error:
        return {"ok": False, "status": "error", "message": _error_message(error)}
