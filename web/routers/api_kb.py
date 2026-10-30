from fastapi import APIRouter, Depends, HTTPException, Request
from modules.database import AdminUser, KnowledgeBaseEntry, KnowledgeGap, Chat, Message
from tortoise.transactions import in_transaction
from web.deps import get_current_user

router = APIRouter(prefix="/api/kb", tags=["knowledge_base"])


def _require_admin(user: AdminUser):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")


def _parse_kb_draft(text: str):
    """Разобрать ответ AI в формате 'ЗАГОЛОВОК: ...' / 'СОДЕРЖАНИЕ: ...'"""
    title = ""
    content_lines = []
    mode = None
    for line in (text or "").split("\n"):
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("ЗАГОЛОВОК:"):
            title = stripped.split(":", 1)[1].strip()
            mode = None
        elif upper.startswith("СОДЕРЖАНИЕ:"):
            mode = "content"
            rest = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            if rest:
                content_lines.append(rest)
        elif mode == "content":
            content_lines.append(line)
    return title.strip(), "\n".join(content_lines).strip()


@router.get("")
async def list_entries(user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    rows = await KnowledgeBaseEntry.all().order_by("-updated_at")
    return [
        {
            "id": r.id,
            "title": r.title,
            "content": r.content,
            "is_active": r.is_active,
            "updated_at": r.updated_at.isoformat(),
        }
        for r in rows
    ]


@router.post("/learn-from-chat")
async def learn_from_chat(request: Request, user: AdminUser = Depends(get_current_user)):
    """AI составляет черновик статьи БЗ по диалогу менеджера с клиентом — на проверку перед сохранением"""
    body = await request.json()
    chat_id = body.get("chat_id")
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id required")
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    bot = request.app.state.bot
    if not getattr(bot, "ai", None) or not bot.ai.enabled:
        return {"ok": False, "error": "ai_disabled"}

    messages = await Message.filter(chat_id=chat_id).order_by("created_at").all()
    role_map = {"user": "Клиент", "ai": "AI", "manager": "Менеджер", "manager_web": "Менеджер", "manager_group": "Менеджер"}
    lines = []
    for m in messages:
        text = (getattr(m, "text", None) or m.content or "").strip()
        if not text:
            continue
        role = role_map.get(getattr(m, "source", None) or m.message_type)
        if not role:
            continue
        lines.append(f"{role}: {text}")
    if len(lines) < 2:
        return {"ok": False, "error": "not_enough_data"}

    dialogue = "\n".join(lines[-60:])
    prompt = (
        "Ниже диалог поддержки, где решался вопрос клиента. Составь статью для базы знаний на основе "
        "итогового решения из этого диалога: короткий заголовок (в чём проблема) и содержание — чёткая "
        "инструкция по шагам, как принято в базе знаний сервиса (используй эмодзи-заголовки и нумерованные шаги "
        "1️⃣ 2️⃣ 3️⃣, где уместно). Обобщи под похожие случаи, НЕ упоминай имена, ID, номера чатов и другие личные данные "
        "конкретного клиента.\n\n"
        "Ответь СТРОГО в формате:\n"
        "ЗАГОЛОВОК: <заголовок>\n"
        "СОДЕРЖАНИЕ:\n<содержание>\n\n"
        f"ДИАЛОГ:\n{dialogue}"
    )
    draft = await bot.ai.get_ai_answer(prompt)
    if not draft:
        return {"ok": False, "error": "ai_unavailable"}

    title, content = _parse_kb_draft(draft)
    if not title or not content:
        return {"ok": False, "error": "parse_failed", "raw": draft}
    return {"ok": True, "title": title, "content": content}


@router.get("/suggestions")
async def list_suggestions(status: str = "open", user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    if status not in {"open", "dismissed", "resolved", "all"}:
        raise HTTPException(status_code=400, detail="Invalid suggestion status")
    query = KnowledgeGap.all()
    if status != "all":
        query = query.filter(status=status)
    rows = await query.order_by("-mention_count", "-last_seen_at").limit(200)
    return [{
        "id": row.id,
        "topic": row.topic,
        "reason": row.reason,
        "example_question": row.example_question,
        "status": row.status,
        "mention_count": row.mention_count,
        "chat_id": row.last_chat_id,
        "entry_id": row.entry_id,
        "created_at": row.created_at.isoformat(),
        "last_seen_at": row.last_seen_at.isoformat(),
    } for row in rows]


@router.patch("/suggestions/{suggestion_id}")
async def update_suggestion(suggestion_id: int, request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    body = await request.json()
    status = body.get("status")
    if status not in {"open", "dismissed", "resolved"}:
        raise HTTPException(status_code=400, detail="Invalid suggestion status")
    row = await KnowledgeGap.get_or_none(id=suggestion_id)
    if not row:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    row.status = status
    await row.save(update_fields=["status", "updated_at"])
    return {"ok": True}


@router.post("")
async def create_entry(request: Request, user: AdminUser = Depends(get_current_user)):
    # Доступно и менеджерам: основной сценарий — сохранить статью прямо из решённого диалога
    body = await request.json()
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="title/content required")
    suggestion_id = body.get("suggestion_id")
    if suggestion_id is not None:
        _require_admin(user)
    is_active = body.get("is_active", True)
    if not isinstance(is_active, bool):
        raise HTTPException(status_code=400, detail="is_active must be boolean")
    async with in_transaction() as connection:
        suggestion = None
        if suggestion_id is not None:
            if isinstance(suggestion_id, bool) or not isinstance(suggestion_id, int):
                raise HTTPException(status_code=400, detail="suggestion_id must be integer")
            suggestion = await KnowledgeGap.filter(id=suggestion_id).using_db(connection).select_for_update().first()
            if not suggestion:
                raise HTTPException(status_code=404, detail="Suggestion not found")
            if suggestion.status != "open":
                raise HTTPException(status_code=409, detail="Предложение уже обработано. Обновите список.")
        created = await KnowledgeBaseEntry.create(title=title, content=content, is_active=is_active, using_db=connection)
        if suggestion:
            suggestion.entry_id = created.id
            # A disabled draft does not fill the knowledge gap for the assistant.
            suggestion.status = "resolved" if is_active else "open"
            await suggestion.save(using_db=connection, update_fields=["entry_id", "status", "updated_at"])
    
    # Сброс кеша AI при изменении базы знаний
    try:
        from modules.ai_support import AISupport
        from modules.config import Config
        config = Config()
        ai = AISupport(config)
        ai.invalidate_cache()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to invalidate AI cache: {e}")
    
    return {"ok": True, "id": created.id}


@router.patch("/{entry_id}")
async def update_entry(entry_id: int, request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    row = await KnowledgeBaseEntry.get_or_none(id=entry_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    body = await request.json()
    if "title" in body:
        row.title = (body.get("title") or "").strip()
    if "content" in body:
        row.content = (body.get("content") or "").strip()
    if "is_active" in body:
        row.is_active = bool(body.get("is_active"))
    if not row.title or not row.content:
        raise HTTPException(status_code=400, detail="title/content required")
    await row.save()
    if row.is_active:
        await KnowledgeGap.filter(entry_id=row.id, status="open").update(status="resolved")
    
    # Сброс кеша AI при изменении базы знаний
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


@router.delete("/{entry_id}")
async def delete_entry(entry_id: int, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    deleted = await KnowledgeBaseEntry.filter(id=entry_id).delete()
    if not deleted:
        raise HTTPException(status_code=404, detail="Not found")
    
    # Сброс кеша AI при изменении базы знаний
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

