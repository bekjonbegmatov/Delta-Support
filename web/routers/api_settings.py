from fastapi import APIRouter, Depends, HTTPException, Request
from modules.config import Config
from modules.database import AdminUser, SystemConfig, ProjectDatabase
from web.deps import get_current_user

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _require_admin(user: AdminUser):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("/system")
async def list_system_settings(user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    rows = await SystemConfig.all().order_by("key")
    return [{"key": r.key, "value": r.value, "description": r.description, "updated_at": r.updated_at.isoformat()} for r in rows]


@router.put("/system/{key}")
async def put_system_setting(key: str, request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    body = await request.json()
    value = body.get("value")
    description = body.get("description")
    if value is None:
        raise HTTPException(status_code=400, detail="value required")
    row = await SystemConfig.get_or_none(key=key)
    if row:
        row.value = str(value)
        if description is not None:
            row.description = description
        await row.save()
    else:
        await SystemConfig.create(key=key, value=str(value), description=description)
    return {"ok": True}


@router.get("/datasources")
async def list_datasources(user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    rows = await ProjectDatabase.all().order_by("id")
    return [
        {
            "id": r.id,
            "name": r.name,
            "connection_string": r.connection_string,
            "db_type": r.db_type,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.post("/datasources")
async def create_datasource(request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    body = await request.json()
    name = (body.get("name") or "").strip()
    db_type = (body.get("db_type") or "").strip()
    connection_string = (body.get("connection_string") or "").strip()
    if not name or not db_type or not connection_string:
        raise HTTPException(status_code=400, detail="name/db_type/connection_string required")
    created = await ProjectDatabase.create(name=name, db_type=db_type, connection_string=connection_string)
    return {"ok": True, "id": created.id}


@router.patch("/datasources/{source_id}")
async def update_datasource(source_id: int, request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    row = await ProjectDatabase.get_or_none(id=source_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    body = await request.json()
    if "name" in body:
        row.name = body.get("name")
    if "db_type" in body:
        row.db_type = body.get("db_type")
    if "connection_string" in body:
        row.connection_string = body.get("connection_string")
    await row.save()
    return {"ok": True}


@router.get("/ai-context")
async def get_ai_context(user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    cfg = Config()
    keys = [
        "service_faq",
        "service_tariffs",
        "service_instructions",
        "service_features",
        "service_support_hours",
    ]
    rows = await SystemConfig.filter(key__in=keys).all()
    values = {r.key: r.value for r in rows}
    defaults = {
        "service_faq": cfg.service_faq or "",
        "service_tariffs": cfg.service_tariffs or "",
        "service_instructions": cfg.service_instructions or "",
        "service_features": cfg.service_features or "",
        "service_support_hours": cfg.service_support_hours or "",
    }
    effective = {k: (values.get(k) if values.get(k) is not None else defaults.get(k, "")) for k in keys}
    return {
        "defaults": defaults,
        "overrides": {k: values.get(k) for k in keys},
        "effective": effective,
    }


@router.put("/ai-context")
async def put_ai_context(request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    body = await request.json()
    keys = [
        "service_faq",
        "service_tariffs",
        "service_instructions",
        "service_features",
        "service_support_hours",
    ]
    for k in keys:
        if k in body:
            await SystemConfig.update_or_create(key=k, defaults={"value": str(body.get(k) or ""), "description": f"AI контекст: {k}"})
    return {"ok": True}


@router.post("/ai-context/reset")
async def reset_ai_context(user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    keys = [
        "service_faq",
        "service_tariffs",
        "service_instructions",
        "service_features",
        "service_support_hours",
    ]
    await SystemConfig.filter(key__in=keys).delete()
    return {"ok": True}


@router.get("/media")
async def get_media_settings(user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    keys = ["media_keep_forever", "media_retention_days"]
    rows = await SystemConfig.filter(key__in=keys).all()
    values = {r.key: r.value for r in rows}
    keep_forever = (values.get("media_keep_forever") or "").lower() in ["1", "true", "yes", "y", "on"]
    days_raw = (values.get("media_retention_days") or "").strip()
    retention_days = int(days_raw) if days_raw.isdigit() else None
    return {"keep_forever": keep_forever, "retention_days": retention_days}


@router.put("/media")
async def put_media_settings(request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    body = await request.json()
    keep_forever = bool(body.get("keep_forever"))
    retention_days = body.get("retention_days")
    await SystemConfig.update_or_create(key="media_keep_forever", defaults={"value": "true" if keep_forever else "false", "description": "Не удалять медиа"})
    if retention_days is None or retention_days == "":
        await SystemConfig.filter(key="media_retention_days").delete()
    else:
        try:
            days = int(retention_days)
        except Exception:
            raise HTTPException(status_code=400, detail="retention_days must be integer")
        if days < 1:
            raise HTTPException(status_code=400, detail="retention_days must be >= 1")
        await SystemConfig.update_or_create(key="media_retention_days", defaults={"value": str(days), "description": "Удалять медиа через N дней"})
    return {"ok": True}


@router.get("/telegram")
async def get_telegram_settings(user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    cfg = Config()
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
    ]
    rows = await SystemConfig.filter(key__in=keys).all()
    values = {r.key: r.value for r in rows}
    defaults = {
        "telegram_group_mode": cfg.telegram_group_mode,
        "telegram_support_group_id": cfg.telegram_support_group_id,
        "telegram_topic_title_template": "{emoji} {first_name} ({user_id}) {status_label}",
        "telegram_emoji_default": "🟢",
        "telegram_emoji_client": "🔴",
        "telegram_emoji_manager": "🟡",
        "telegram_emoji_ai": "🤖",
        "telegram_status_emoji_active": "🟢",
        "telegram_status_emoji_waiting_manager": "🟡",
        "telegram_status_emoji_closed": "🔴",
    }
    effective = {}
    for k, d in defaults.items():
        if k == "telegram_group_mode":
            raw = values.get(k)
            effective[k] = d if raw is None else (str(raw).lower() in ["1", "true", "yes", "y", "on"])
        elif k == "telegram_support_group_id":
            raw = (values.get(k) or "").strip()
            effective[k] = int(raw) if raw.lstrip("-").isdigit() else d
        else:
            effective[k] = values.get(k) if values.get(k) is not None else d
    return {"defaults": defaults, "overrides": {k: values.get(k) for k in defaults.keys()}, "effective": effective}


@router.put("/telegram")
async def put_telegram_settings(request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    body = await request.json()
    allowed = {
        "telegram_group_mode": ("Включить режим группы", "bool"),
        "telegram_support_group_id": ("ID группы поддержки", "int"),
        "telegram_topic_title_template": ("Шаблон названия топиков", "str"),
        "telegram_emoji_default": ("Эмодзи по умолчанию", "str"),
        "telegram_emoji_client": ("Эмодзи клиента", "str"),
        "telegram_emoji_manager": ("Эмодзи менеджера", "str"),
        "telegram_emoji_ai": ("Эмодзи AI", "str"),
        "telegram_status_emoji_active": ("Эмодзи статуса active", "str"),
        "telegram_status_emoji_waiting_manager": ("Эмодзи статуса waiting_manager", "str"),
        "telegram_status_emoji_closed": ("Эмодзи статуса closed", "str"),
    }
    for k, (desc, typ) in allowed.items():
        if k not in body:
            continue
        v = body.get(k)
        if typ == "bool":
            await SystemConfig.update_or_create(key=k, defaults={"value": "true" if bool(v) else "false", "description": desc})
        elif typ == "int":
            if v is None or str(v).strip() == "":
                await SystemConfig.filter(key=k).delete()
            else:
                try:
                    n = int(v)
                except Exception:
                    raise HTTPException(status_code=400, detail=f"{k} must be integer")
                await SystemConfig.update_or_create(key=k, defaults={"value": str(n), "description": desc})
        else:
            if v is None:
                await SystemConfig.filter(key=k).delete()
            else:
                await SystemConfig.update_or_create(key=k, defaults={"value": str(v), "description": desc})
    return {"ok": True}


@router.get("/bot")
async def get_bot_settings(user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    cfg = Config()
    keys = [
        "project_name",
        "project_description",
        "project_website",
        "project_bot_link",
        "project_owner_contacts",
        "bot_welcome_message",
        "ai_system_prompt",
        "ai_support_api_type",
        "ai_support_api_key",
        "ai_support_api_keys",
        "groq_models",
    ]
    rows = await SystemConfig.filter(key__in=keys).all()
    values = {r.key: r.value for r in rows}
    defaults = {
        "project_name": cfg.project_name or "Support Desk",
        "project_description": cfg.project_description or "",
        "project_website": cfg.project_website or "",
        "project_bot_link": cfg.project_bot_link or "",
        "project_owner_contacts": cfg.project_owner_contacts or "",
        "bot_welcome_message": (
            "👋 Здравствуйте, {first_name}!\n\n"
            "Я бот поддержки проекта {project_name}.\n"
            "Я помогу вам с вопросами и проблемами.\n\n"
            "Просто напишите ваш вопрос, и я постараюсь помочь!\n\n"
            "{project_description}"
        ),
        "ai_system_prompt": (
            "Ты профессиональный помощник службы поддержки VPN проекта {project_name}.\n\n"
            "ТВОЯ РОЛЬ:\n"
            "- Помогать пользователям решать их вопросы о VPN сервисе\n"
            "- Предоставлять точную информацию на основе данных о сервисе\n"
            "- Быть вежливым, дружелюбным и профессиональным\n"
            "- Если не можешь решить вопрос - предложить пригласить менеджера\n\n"
            "ПРАВИЛА ОБЩЕНИЯ:\n"
            "- Отвечай на русском языке, если вопрос на русском\n"
            "- Используй информацию о сервисе для точных ответов\n"
            "- НЕ называй пользователя другими именами или проектами\n"
            "- Обращайся к пользователю по имени (если известно) или на \"вы\"\n"
            "- Будь конкретным и полезным в ответах\n"
            "- Если вопрос неясен - уточни детали\n\n"
            "СТРУКТУРА ОТВЕТОВ:\n"
            "- Начни с приветствия или подтверждения понимания вопроса\n"
            "- Дай четкий и структурированный ответ\n"
            "- Если нужно - используй нумерованные списки или пункты\n"
            "- В конце предложи дополнительную помощь или пригласи менеджера, если вопрос сложный\n\n"
            "ИНФОРМАЦИЯ О СЕРВИСЕ:\n"
            "{service_context}\n\n"
            "ВАЖНО: Если вопрос пользователя касается личных данных (баланс, подписка, тариф), но у тебя нет доступа к этой информации - "
            "предложи пользователю проверить личный кабинет или пригласить менеджера."
        ),
        "ai_support_api_type": cfg.ai_support_api_type or "groq",
        "ai_support_api_key": "",
        "ai_support_api_keys": "",
        "groq_models": cfg.groq_models or "",
    }
    effective = {}
    for k in defaults.keys():
        raw = values.get(k)
        if k in ["ai_system_prompt", "bot_welcome_message"] and raw is not None and str(raw).strip() == "":
            raw = None
        effective[k] = raw if raw is not None else defaults.get(k, "")
    ai_key_set = bool((values.get("ai_support_api_key") or "").strip() or (cfg.ai_support_api_key or "").strip())
    ai_keys_set = bool((values.get("ai_support_api_keys") or "").strip() or (cfg.ai_support_api_keys or "").strip())

    def _split_keys(s: str):
        return [p.strip() for p in (s or "").split(",") if p.strip()]

    def _mask_key(k: str):
        k = (k or "").strip()
        if not k:
            return ""
        tail = k[-4:] if len(k) >= 4 else k
        return f"••••{tail}"

    existing_keys = []
    for k in _split_keys(values.get("ai_support_api_keys") or "") or _split_keys(cfg.ai_support_api_keys or ""):
        if k not in existing_keys:
            existing_keys.append(k)
    one = (values.get("ai_support_api_key") or "").strip() or (cfg.ai_support_api_key or "").strip()
    if one and one not in existing_keys:
        existing_keys.insert(0, one)
    secrets_preview = [_mask_key(k) for k in existing_keys if k]
    return {
        "defaults": defaults,
        "overrides": {k: values.get(k) for k in defaults.keys()},
        "effective": {k: ("" if k in ["ai_support_api_key", "ai_support_api_keys"] else effective.get(k)) for k in defaults.keys()},
        "secrets": {"ai_support_api_key_set": ai_key_set, "ai_support_api_keys_set": ai_keys_set},
        "secrets_preview": {"ai_support_api_keys": secrets_preview, "ai_support_api_keys_count": len(existing_keys)},
    }


@router.put("/bot")
async def put_bot_settings(request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    body = await request.json()
    allowed = {
        "project_name": "Проект: название",
        "project_description": "Проект: описание",
        "project_website": "Проект: сайт",
        "project_bot_link": "Проект: ссылка на бота",
        "project_owner_contacts": "Проект: контакты владельца",
        "bot_welcome_message": "Бот: приветственное сообщение",
        "ai_system_prompt": "AI: system prompt",
        "ai_support_api_type": "AI: API type",
        "groq_models": "AI: модели Groq",
    }
    for k, desc in allowed.items():
        if k not in body:
            continue
        v = body.get(k)
        if v is None or (k in ["ai_system_prompt", "bot_welcome_message"] and str(v).strip() == ""):
            await SystemConfig.filter(key=k).delete()
        else:
            await SystemConfig.update_or_create(key=k, defaults={"value": str(v), "description": desc})
    if "ai_support_api_key" in body:
        v = body.get("ai_support_api_key")
        if v is None or str(v).strip() == "":
            await SystemConfig.filter(key="ai_support_api_key").delete()
        else:
            await SystemConfig.update_or_create(key="ai_support_api_key", defaults={"value": str(v), "description": "AI: API key"})
    if "ai_support_api_keys" in body:
        v = body.get("ai_support_api_keys")
        if v is None or str(v).strip() == "":
            await SystemConfig.filter(key="ai_support_api_keys").delete()
        else:
            append = bool(body.get("ai_support_api_keys_append"))
            new_raw = str(v)
            if append:
                cfg = Config()
                existing = (await SystemConfig.get_or_none(key="ai_support_api_keys"))
                existing_list = [p.strip() for p in ((existing.value if existing else "") or cfg.ai_support_api_keys or "").split(",") if p.strip()]
                one = (await SystemConfig.get_or_none(key="ai_support_api_key"))
                one_val = (one.value if one else "") or cfg.ai_support_api_key or ""
                one_val = one_val.strip()
                combined = []
                if one_val and one_val not in combined:
                    combined.append(one_val)
                for k in existing_list:
                    if k not in combined:
                        combined.append(k)
                for k in [p.strip() for p in new_raw.split(",") if p.strip()]:
                    if k not in combined:
                        combined.append(k)
                new_raw = ",".join(combined)
            await SystemConfig.update_or_create(key="ai_support_api_keys", defaults={"value": new_raw, "description": "AI: API keys"})
    return {"ok": True}
