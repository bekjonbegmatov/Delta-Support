import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import Response
from modules.database import Chat, Message, AdminUser
from web.deps import get_current_user
from modules.bot import SupportBot
from telegram.constants import ParseMode
from tortoise.expressions import Q
from datetime import datetime, timedelta, timezone
import io

logger = logging.getLogger(__name__)


def _notify_client_background(bot: SupportBot, telegram_user_id: int, text: str):
    """Уведомление клиенту в Telegram — фоновой задачей, не блокируя HTTP-ответ
    панели. Если сеть до Telegram зависла/недоступна, кнопка в панели не должна
    висеть в ожидании этого запроса — статус в БД уже обновлён к этому моменту."""
    async def _send():
        try:
            await bot.application.bot.send_message(chat_id=telegram_user_id, text=text)
        except Exception as e:
            logger.warning(f"Background notify to {telegram_user_id} failed: {e}")
    asyncio.create_task(_send())


def _edit_group_topic_background(bot: SupportBot, chat: Chat, role_hint: str = None):
    """Переименование топика в тг-группе под новый статус — тоже фоновой задачей
    (edit_forum_topic это тоже поход в Telegram API, по той же причине что и
    _notify_client_background). Chat.filter(...).update() — bulk-апдейт, он не
    обновляет переданный объект chat, поэтому статус на нём выставляем здесь же
    перед вызовом _edit_group_topic_status, которая формирует имя темы из chat.status."""
    async def _run():
        try:
            await bot._edit_group_topic_status(chat, role_hint=role_hint)
        except Exception as e:
            logger.warning(f"Background topic rename failed for chat {chat.id}: {e}")
    asyncio.create_task(_run())

router = APIRouter(prefix="/api/chats", tags=["chats"])

@router.get("")
async def list_chats(
    user: AdminUser = Depends(get_current_user),
    status: str = Query(None),
    q: str = Query(None),
    mine: bool = Query(False),
    limit: int = Query(50),
):
    qs = Chat.all().order_by("-updated_at")
    if status:
        qs = qs.filter(status=status)
    if mine:
        qs = qs.filter(assigned_admin_id=user.id)
    if q:
        qv = q.strip()
        digits = qv.lstrip("-")
        if digits.isdigit():
            qs = qs.filter(Q(user_id=int(qv)) | Q(id=int(digits)) | Q(username__icontains=qv) | Q(first_name__icontains=qv) | Q(last_name__icontains=qv))
        else:
            qs = qs.filter(Q(username__icontains=qv) | Q(first_name__icontains=qv) | Q(last_name__icontains=qv))
    chats = await qs.limit(min(max(limit, 1), 200))
    return [
        {
            "id": c.id,
            "user_id": c.user_id,
            "user_tg_id": c.user_tg_id,
            "username": c.username,
            "first_name": c.first_name,
            "last_name": c.last_name,
            "status": c.status,
            "manager_id": c.manager_id,
            "assigned_admin_id": c.assigned_admin_id,
            "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
            "updated_at": c.updated_at.isoformat(),
            "waiting_since": c.waiting_since.isoformat() if c.waiting_since else None,
            "unread_count": await _unread_count(c),
        }
        for c in chats
    ]


async def _unread_count(chat: Chat) -> int:
    """Сколько сообщений клиента пришло с момента, когда чат последний раз
    открывали в панели. Ответы AI сюда не входят — не хочется, чтобы
    менеджер видел "непрочитанное", если клиенту уже ответил бот."""
    since = chat.last_viewed_at or chat.created_at
    return await Message.filter(chat_id=chat.id, message_type="user", created_at__gt=since).count()

@router.get("/counts")
async def chat_counts(user: AdminUser = Depends(get_current_user)):
    """Количество чатов по статусам — для бейджей на табах фильтра"""
    active = await Chat.filter(status="active").count()
    waiting = await Chat.filter(status="waiting_manager").count()
    closed = await Chat.filter(status="closed").count()
    mine = await Chat.filter(assigned_admin_id=user.id).exclude(status="closed").count()
    return {"active": active, "waiting_manager": waiting, "closed": closed, "all": active + waiting + closed, "mine": mine}


async def _chat_snippet(chat_id: int) -> str:
    """Короткое описание проблемы для карточки канбана: сводка AI, если есть, иначе последнее сообщение"""
    msgs = await Message.filter(chat_id=chat_id).order_by("-id").limit(5).all()
    for m in msgs:
        text = getattr(m, "text", None) or m.content or ""
        if "Сводка:" in text:
            idx = text.find("Сводка:")
            snippet = text[idx + len("Сводка:"):].strip()
            if snippet:
                return snippet[:220]
    if msgs:
        text = getattr(msgs[0], "text", None) or msgs[0].content or ""
        return text[:220]
    return ""


@router.get("/board")
async def chats_board(period: str = Query("today"), user: AdminUser = Depends(get_current_user)):
    """Канбан-доска: чаты за период, сгруппированные по статусу, с кратким описанием проблемы"""
    now = datetime.now(timezone.utc)
    if period == "week":
        since = now - timedelta(days=7)
    elif period == "month":
        since = now - timedelta(days=30)
    else:
        period = "today"
        since = now - timedelta(hours=24)

    chats = await Chat.filter(updated_at__gte=since).order_by("-updated_at").limit(300).all()

    columns: dict = {"waiting_manager": [], "active": [], "closed": []}
    for c in chats:
        bucket = columns.get(c.status)
        if bucket is None or len(bucket) >= 60:
            continue
        snippet = await _chat_snippet(c.id)
        name = " ".join(p for p in [c.first_name, c.last_name] if p) or (f"@{c.username}" if c.username else f"ID {c.user_id}")
        bucket.append({
            "id": c.id,
            "user_id": c.user_id,
            "username": c.username,
            "name": name,
            "status": c.status,
            "assigned_admin_id": c.assigned_admin_id,
            "snippet": snippet,
            "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
            "updated_at": c.updated_at.isoformat(),
        })

    return {
        "period": period,
        "since": since.isoformat(),
        "columns": columns,
        "counts": {k: len(v) for k, v in columns.items()},
    }


@router.get("/{chat_id}")
async def chat_details(chat_id: int, user: AdminUser = Depends(get_current_user)):
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return {
        "id": chat.id,
        "user_id": chat.user_id,
        "user_tg_id": chat.user_tg_id,
        "username": chat.username,
        "first_name": chat.first_name,
        "last_name": chat.last_name,
        "status": chat.status,
        "manager_id": chat.manager_id,
        "assigned_admin_id": chat.assigned_admin_id,
        "last_message_at": chat.last_message_at.isoformat() if chat.last_message_at else None,
        "updated_at": chat.updated_at.isoformat(),
        "waiting_since": chat.waiting_since.isoformat() if chat.waiting_since else None,
        "unread_count": await _unread_count(chat),
        "topic_id": chat.topic_id,
    }

@router.get("/{chat_id}/profile")
async def chat_profile(chat_id: int, user: AdminUser = Depends(get_current_user)):
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return {
        "id": chat.id,
        "user_id": chat.user_id,
        "user_tg_id": chat.user_tg_id,
        "username": chat.username,
        "first_name": chat.first_name,
        "last_name": chat.last_name,
        "status": chat.status,
        "manager_id": chat.manager_id,
        "assigned_admin_id": chat.assigned_admin_id,
        "last_message_at": chat.last_message_at.isoformat() if chat.last_message_at else None,
        "updated_at": chat.updated_at.isoformat(),
        "created_at": chat.created_at.isoformat() if getattr(chat, "created_at", None) else None,
        "waiting_since": chat.waiting_since.isoformat() if chat.waiting_since else None,
        "unread_count": await _unread_count(chat),
        "topic_id": chat.topic_id,
        "avatar_url": f"/api/chats/{chat_id}/avatar",
    }


@router.post("/{chat_id}/read")
async def mark_chat_read(chat_id: int, user: AdminUser = Depends(get_current_user)):
    """Отмечает чат просмотренным менеджером — сбрасывает счётчик непрочитанных.
    Панель дёргает это при открытии чата."""
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    await Chat.filter(id=chat_id).update(last_viewed_at=datetime.now(timezone.utc))
    return {"ok": True}


@router.get("/{chat_id}/account")
async def chat_account(chat_id: int, request: Request, force: bool = Query(False), user: AdminUser = Depends(get_current_user)):
    """Данные аккаунта клиента из Support API (баланс, подписки, ключи) для панели менеджера"""
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    bot: SupportBot = request.app.state.bot
    svc = getattr(bot, "user_info", None)
    if not svc:
        return {"ok": False, "error": "not_configured"}
    await svc._refresh_settings()
    if not svc.is_configured():
        return {"ok": False, "error": "not_configured"}
    data = await svc.get_user_info(chat.user_id, force=force)
    if not data:
        return {"ok": False, "error": "not_found"}
    return {"ok": True, **data}


@router.get("/{chat_id}/avatar")
async def chat_avatar(request: Request, chat_id: int, user: AdminUser = Depends(get_current_user)):
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    bot: SupportBot = request.app.state.bot
    try:
        photos = await bot.application.bot.get_user_profile_photos(chat.user_id, limit=1)
        if not photos or not getattr(photos, "photos", None) or not photos.photos:
            raise HTTPException(status_code=404, detail="No avatar")
        sizes = photos.photos[0]
        photo = sizes[-1] if sizes else None
        if not photo:
            raise HTTPException(status_code=404, detail="No avatar")
        file = await bot.application.bot.get_file(photo.file_id)
        data = await file.download_as_bytearray()
        ct = "image/jpeg"
        path = (getattr(file, "file_path", "") or "").lower()
        if path.endswith(".png"):
            ct = "image/png"
        elif path.endswith(".webp"):
            ct = "image/webp"
        return Response(content=bytes(data), media_type=ct)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="No avatar")


@router.get("/{chat_id}/messages")
async def chat_messages(chat_id: int, before_id: int = Query(None), limit: int = Query(50), user: AdminUser = Depends(get_current_user)):
    qs = Message.filter(chat_id=chat_id).order_by("-id")
    if before_id:
        qs = qs.filter(id__lt=before_id)
    msgs = await qs.limit(limit)
    out = []
    for m in reversed(msgs):
        source = getattr(m, "source", None) or m.message_type
        text = getattr(m, "text", None) or m.content
        created = m.created_at.isoformat() if m.created_at else None
        out.append({"id": m.id, "source": source, "text": text, "created_at": created, "media_type": m.media_type, "media_file_id": m.media_file_id})
    return out


@router.get("/messages/{message_id}/media")
async def message_media(request: Request, message_id: int, user: AdminUser = Depends(get_current_user)):
    m = await Message.get_or_none(id=message_id)
    if not m:
        raise HTTPException(status_code=404, detail="Not found")
    if not m.media_file_id:
        raise HTTPException(status_code=404, detail="No media")
    bot: SupportBot = request.app.state.bot
    try:
        file = await bot.application.bot.get_file(m.media_file_id)
        data = await file.download_as_bytearray()
        path = (getattr(file, "file_path", "") or "").lower()
        ct = "application/octet-stream"
        if m.media_type == "photo":
            ct = "image/jpeg"
            if path.endswith(".png"):
                ct = "image/png"
            elif path.endswith(".webp"):
                ct = "image/webp"
        elif m.media_type == "video":
            ct = "video/mp4"
        elif m.media_type in ["audio", "voice"]:
            ct = "audio/ogg" if m.media_type == "voice" else "audio/mpeg"
            if path.endswith(".ogg"):
                ct = "audio/ogg"
            elif path.endswith(".wav"):
                ct = "audio/wav"
            elif path.endswith(".webm"):
                ct = "audio/webm"
        if ct == "application/octet-stream":
            if path.endswith(".png"):
                ct = "image/png"
            elif path.endswith(".jpg") or path.endswith(".jpeg"):
                ct = "image/jpeg"
            elif path.endswith(".webp"):
                ct = "image/webp"
            elif path.endswith(".gif"):
                ct = "image/gif"
            elif path.endswith(".mp4"):
                ct = "video/mp4"
            elif path.endswith(".webm"):
                ct = "video/webm"
            elif path.endswith(".pdf"):
                ct = "application/pdf"
        disposition = "inline"
        if m.media_type == "document":
            disposition = "attachment"
        headers = {"Content-Disposition": f'{disposition}; filename="media_{message_id}"'}
        return Response(content=bytes(data), media_type=ct, headers=headers)
    except Exception:
        raise HTTPException(status_code=404, detail="Download failed")

@router.post("/{chat_id}/send")
async def send_api_message(request: Request, chat_id: int, user: AdminUser = Depends(get_current_user)):
    body = await request.json()
    text = body.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    sender_uid = chat.manager_id or user.id
    try:
        msg = await Message.create(chat_id=chat_id, user_id=sender_uid, message_type="manager", content=text, source="manager_web", text=text, admin_user_id=user.id)
    except Exception:
        msg = await Message.create(chat_id=chat_id, user_id=sender_uid, message_type="manager", content=text)
    try:
        await Chat.filter(id=chat_id).update(last_message_at=msg.created_at, updated_at=msg.created_at)
    except Exception:
        pass
    # отправка через бота
    bot: SupportBot = request.app.state.bot
    await bot.refresh_runtime_settings()
    session_mode = bot._manager_reply_style == "session_header"
    if session_mode:
        reply_to = await bot._get_or_create_manager_header(chat.user_id, chat_id)
        sent_user = await bot.application.bot.send_message(
            chat_id=chat.user_id, text=text,
            **({"reply_to_message_id": reply_to} if reply_to else {}),
        )
    else:
        combined = f"{bot._manager_reply_prefix}\n\n{text}"
        sent_user = await bot.application.bot.send_message(chat_id=chat.user_id, text=combined)
    try:
        await Message.filter(id=msg.id).update(tg_message_id_user=sent_user.message_id)
    except Exception:
        pass
    if bot._group_id:
        thread_id = await bot._ensure_group_topic(chat)
        if thread_id:
            group_text = f"👨‍💼 Менеджер (web):\n{text}"
            sent_group = await bot.application.bot.send_message(chat_id=bot._group_id, message_thread_id=int(thread_id), text=group_text, parse_mode=ParseMode.HTML)
            try:
                await Message.filter(id=msg.id).update(tg_message_id_group=sent_group.message_id)
            except Exception:
                pass
            await bot._edit_group_topic_status(chat, role_hint="manager")
    await request.app.state.ws_manager.broadcast(
        "new_message",
        {
            "chat_id": chat_id,
            "message": {
                "id": msg.id,
                "text": getattr(msg, "text", None) or msg.content,
                "source": getattr(msg, "source", None) or msg.message_type,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
                "media_type": getattr(msg, "media_type", None),
                "media_file_id": getattr(msg, "media_file_id", None),
            },
        },
    )
    return {"ok": True, "message_id": msg.id}

@router.post("/{chat_id}/send-media")
async def send_api_media(
    request: Request,
    chat_id: int,
    file: UploadFile = File(...),
    text: str = Form(""),
    user: AdminUser = Depends(get_current_user),
):
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    caption = (text or "").strip()
    media_type = "document"
    content_type = (file.content_type or "").lower()
    if content_type.startswith("image/"):
        media_type = "photo"
    elif content_type.startswith("video/"):
        media_type = "video"
    elif content_type in ["audio/ogg", "audio/oga"]:
        media_type = "voice"
    elif content_type.startswith("audio/"):
        media_type = "audio"
    stored_text = (f"[{media_type}] {caption}".strip() if caption else f"[{media_type}]")
    sender_uid = chat.manager_id or user.id
    msg = await Message.create(
        chat_id=chat_id,
        user_id=sender_uid,
        message_type="manager",
        content=stored_text,
        source="manager_web",
        text=stored_text,
        media_type=media_type,
        admin_user_id=user.id,
    )
    try:
        await Chat.filter(id=chat_id).update(last_message_at=msg.created_at, updated_at=msg.created_at)
    except Exception:
        pass
    bot: SupportBot = request.app.state.bot
    await bot.refresh_runtime_settings()
    session_mode = bot._manager_reply_style == "session_header"
    reply_kwargs = {}
    if session_mode:
        reply_to = await bot._get_or_create_manager_header(chat.user_id, chat_id)
        if reply_to:
            reply_kwargs = {"reply_to_message_id": reply_to}
        final_caption = caption or None
    else:
        final_caption = f"{bot._manager_reply_prefix}\n\n{caption}" if caption else bot._manager_reply_prefix
    raw_bytes = await file.read()
    filename = file.filename or "upload"
    sent_user = None
    media_file_id_for_ws = None
    if media_type == "photo":
        sent_user = await bot.application.bot.send_photo(chat_id=chat.user_id, photo=io.BytesIO(raw_bytes), caption=final_caption, **reply_kwargs)
        try:
            file_id = sent_user.photo[-1].file_id if sent_user.photo else None
            media_file_id_for_ws = file_id
            await Message.filter(id=msg.id).update(tg_message_id_user=sent_user.message_id, media_file_id=file_id)
        except Exception:
            pass
    elif media_type == "video":
        sent_user = await bot.application.bot.send_video(chat_id=chat.user_id, video=io.BytesIO(raw_bytes), caption=final_caption, **reply_kwargs)
        try:
            file_id = getattr(sent_user.video, "file_id", None)
            media_file_id_for_ws = file_id
            await Message.filter(id=msg.id).update(tg_message_id_user=sent_user.message_id, media_file_id=file_id)
        except Exception:
            pass
    elif media_type == "audio":
        try:
            sent_user = await bot.application.bot.send_audio(chat_id=chat.user_id, audio=io.BytesIO(raw_bytes), caption=final_caption, **reply_kwargs)
            try:
                file_id = getattr(sent_user.audio, "file_id", None)
                media_file_id_for_ws = file_id
                await Message.filter(id=msg.id).update(tg_message_id_user=sent_user.message_id, media_file_id=file_id)
            except Exception:
                pass
        except Exception:
            bio = io.BytesIO(raw_bytes)
            try:
                bio.name = filename
            except Exception:
                pass
            sent_user = await bot.application.bot.send_document(chat_id=chat.user_id, document=bio, caption=final_caption, **reply_kwargs)
            try:
                file_id = getattr(sent_user.document, "file_id", None)
                media_file_id_for_ws = file_id
                await Message.filter(id=msg.id).update(tg_message_id_user=sent_user.message_id, media_file_id=file_id, media_type="document")
                media_type = "document"
                stored_text = (f"[{media_type}] {caption}".strip() if caption else f"[{media_type}]")
                await Message.filter(id=msg.id).update(content=stored_text, text=stored_text)
            except Exception:
                pass
    elif media_type == "voice":
        try:
            sent_user = await bot.application.bot.send_voice(chat_id=chat.user_id, voice=io.BytesIO(raw_bytes), caption=final_caption, **reply_kwargs)
            try:
                file_id = getattr(sent_user.voice, "file_id", None)
                media_file_id_for_ws = file_id
                await Message.filter(id=msg.id).update(tg_message_id_user=sent_user.message_id, media_file_id=file_id)
            except Exception:
                pass
        except Exception:
            bio = io.BytesIO(raw_bytes)
            try:
                bio.name = filename
            except Exception:
                pass
            sent_user = await bot.application.bot.send_document(chat_id=chat.user_id, document=bio, caption=final_caption, **reply_kwargs)
            try:
                file_id = getattr(sent_user.document, "file_id", None)
                media_file_id_for_ws = file_id
                await Message.filter(id=msg.id).update(tg_message_id_user=sent_user.message_id, media_file_id=file_id, media_type="document")
                media_type = "document"
                stored_text = (f"[{media_type}] {caption}".strip() if caption else f"[{media_type}]")
                await Message.filter(id=msg.id).update(content=stored_text, text=stored_text)
            except Exception:
                pass
    else:
        bio = io.BytesIO(raw_bytes)
        try:
            bio.name = filename
        except Exception:
            pass
        sent_user = await bot.application.bot.send_document(chat_id=chat.user_id, document=bio, caption=final_caption, **reply_kwargs)
        try:
            file_id = getattr(sent_user.document, "file_id", None)
            media_file_id_for_ws = file_id
            await Message.filter(id=msg.id).update(tg_message_id_user=sent_user.message_id, media_file_id=file_id)
        except Exception:
            pass
    if bot._group_id:
        thread_id = await bot._ensure_group_topic(chat)
        if thread_id:
            group_caption = f"👨‍💼 Менеджер (web):\n{caption}" if caption else "👨‍💼 Менеджер (web)"
            try:
                if media_type == "photo":
                    sent_group = await bot.application.bot.send_photo(
                        chat_id=bot._group_id,
                        message_thread_id=int(thread_id),
                        photo=io.BytesIO(raw_bytes),
                        caption=group_caption,
                        parse_mode=ParseMode.HTML,
                    )
                elif media_type == "video":
                    sent_group = await bot.application.bot.send_video(
                        chat_id=bot._group_id,
                        message_thread_id=int(thread_id),
                        video=io.BytesIO(raw_bytes),
                        caption=group_caption,
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    bio = io.BytesIO(raw_bytes)
                    try:
                        bio.name = filename
                    except Exception:
                        pass
                    sent_group = await bot.application.bot.send_document(
                        chat_id=bot._group_id,
                        message_thread_id=int(thread_id),
                        document=bio,
                        caption=group_caption,
                        parse_mode=ParseMode.HTML,
                    )
                try:
                    await Message.filter(id=msg.id).update(tg_message_id_group=sent_group.message_id)
                except Exception:
                    pass
                await bot._edit_group_topic_status(chat, role_hint="manager")
            except Exception:
                pass
    await request.app.state.ws_manager.broadcast(
        "new_message",
        {
            "chat_id": chat_id,
            "message": {
                "id": msg.id,
                "text": stored_text,
                "source": "manager_web",
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
                "media_type": media_type,
                "media_file_id": media_file_id_for_ws,
            },
        },
    )
    return {"ok": True, "message_id": msg.id}


@router.post("/{chat_id}/ai-suggest")
async def ai_suggest_reply(chat_id: int, request: Request, user: AdminUser = Depends(get_current_user)):
    """AI составляет черновик ответа клиенту по истории диалога — менеджер правит перед отправкой"""
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    bot: SupportBot = request.app.state.bot
    if not getattr(bot, "ai", None) or not bot.ai.enabled:
        return {"ok": False, "error": "ai_disabled"}

    messages = list(reversed(await Message.filter(chat_id=chat_id).order_by("-id").limit(20).all()))
    if not messages:
        return {"ok": False, "error": "no_messages"}

    last_user_msg = next((m for m in reversed(messages) if m.message_type == "user"), None)
    question = (
        (getattr(last_user_msg, "text", None) or last_user_msg.content)
        if last_user_msg
        else "Клиент ждёт ответа менеджера, предложи, как продолжить разговор на основе истории."
    )
    history = [
        {"role": "user" if m.message_type == "user" else "assistant", "message": getattr(m, "text", None) or m.content}
        for m in messages
        if (getattr(m, "source", None) or "") != "ai_tool"
    ]
    ctx = {"user_id": chat.user_id, "username": chat.username, "first_name": chat.first_name, "last_name": chat.last_name}
    try:
        account_info = await bot.user_info.get_ai_context(chat.user_id)
        if account_info:
            ctx["account_info"] = account_info
    except Exception:
        pass

    suggestion = await bot.ai.get_ai_answer(question, ctx, history)
    if not suggestion:
        return {"ok": False, "error": "ai_unavailable"}
    return {"ok": True, "suggestion": suggestion}


@router.post("/{chat_id}/join")
async def join_chat(request: Request, chat_id: int, user: AdminUser = Depends(get_current_user)):
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    # Идемпотентность: этот же менеджер уже подключён — повторный клик не должен
    # плодить дубликаты системных сообщений и уведомлений клиенту
    if chat.status == "waiting_manager" and chat.manager_id == user.id:
        return {"ok": True, "already_joined": True}
    bot: SupportBot = request.app.state.bot
    # update_chat_status (а не голый ORM update) — чтобы корректно проставился
    # waiting_since для SLA-пинга и сбросился sla_notified
    await bot.db.update_chat_status(chat_id, "waiting_manager", manager_id=user.id)
    await Chat.filter(id=chat_id).update(assigned_admin_id=user.id)
    chat.status = "waiting_manager"
    chat.manager_id = user.id
    chat.assigned_admin_id = user.id
    _edit_group_topic_background(bot, chat, role_hint="manager")
    try:
        sysmsg = await Message.create(chat_id=chat_id, user_id=chat.user_id, message_type="manager", content="Менеджер подключился", source="system", text="Менеджер подключился")
    except Exception:
        sysmsg = await Message.create(chat_id=chat_id, user_id=chat.user_id, message_type="manager", content="Менеджер подключился")
    try:
        await Chat.filter(id=chat_id).update(last_message_at=sysmsg.created_at, updated_at=sysmsg.created_at)
    except Exception:
        pass
    _notify_client_background(bot, chat.user_id, "👨‍💼 Менеджер подключился к вашему чату. Можете писать сообщение.")
    await request.app.state.ws_manager.broadcast("status_changed", {"chat_id": chat_id, "status": "waiting_manager", "assigned_admin_id": user.id})
    try:
        await request.app.state.ws_manager.broadcast(
            "new_message",
            {
                "chat_id": chat_id,
                "message": {
                    "id": sysmsg.id,
                    "text": getattr(sysmsg, "text", None) or sysmsg.content,
                    "source": "system",
                    "created_at": sysmsg.created_at.isoformat() if sysmsg.created_at else None,
                },
            },
        )
    except Exception:
        pass
    return {"ok": True}


@router.post("/{chat_id}/close")
async def close_chat(request: Request, chat_id: int, user: AdminUser = Depends(get_current_user)):
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    # Идемпотентность: чат уже закрыт — повторный клик не должен создавать ещё
    # одно системное сообщение и слать клиенту дубликат уведомления
    if chat.status == "closed":
        return {"ok": True, "already_closed": True}
    bot: SupportBot = request.app.state.bot
    # update_chat_status (а не голый ORM update) — сбрасывает waiting_since/
    # reminder_sent_at/sla_notified, иначе автозакрытие и SLA-пинг могут
    # опираться на устаревшие таймеры закрытого чата
    await bot.db.update_chat_status(chat_id, "closed")
    await Chat.filter(id=chat_id).update(assigned_admin_id=None, manager_id=None)
    chat.status = "closed"
    chat.manager_id = None
    chat.assigned_admin_id = None
    _edit_group_topic_background(bot, chat, role_hint=None)
    try:
        sysmsg = await Message.create(chat_id=chat_id, user_id=chat.user_id, message_type="manager", content="Чат закрыт. AI активирован", source="system", text="Чат закрыт. AI активирован", admin_user_id=user.id)
    except Exception:
        sysmsg = await Message.create(chat_id=chat_id, user_id=chat.user_id, message_type="manager", content="Чат закрыт. AI активирован")
    try:
        await Chat.filter(id=chat_id).update(last_message_at=sysmsg.created_at, updated_at=sysmsg.created_at)
    except Exception:
        pass
    bot._clear_manager_header(chat_id)
    _notify_client_background(bot, chat.user_id, "✅ Чат с менеджером закрыт. Теперь вам помогает 🤖 AI-поддержка.")
    await request.app.state.ws_manager.broadcast("status_changed", {"chat_id": chat_id, "status": "closed"})
    try:
        await request.app.state.ws_manager.broadcast(
            "new_message",
            {
                "chat_id": chat_id,
                "message": {
                    "id": sysmsg.id,
                    "text": getattr(sysmsg, "text", None) or sysmsg.content,
                    "source": "system",
                    "created_at": sysmsg.created_at.isoformat() if sysmsg.created_at else None,
                },
            },
        )
    except Exception:
        pass
    return {"ok": True}


@router.post("/{chat_id}/ai")
async def back_to_ai(request: Request, chat_id: int, user: AdminUser = Depends(get_current_user)):
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    # Идемпотентность: сессия уже завершена (чат уже active) — повторный клик
    # не должен создавать ещё одно системное сообщение и дубликат уведомления
    if chat.status == "active":
        return {"ok": True, "already_active": True}
    bot: SupportBot = request.app.state.bot
    await bot.db.update_chat_status(chat_id, "active")
    await Chat.filter(id=chat_id).update(assigned_admin_id=None, manager_id=None, ai_disabled=False)
    chat.status = "active"
    chat.manager_id = None
    chat.assigned_admin_id = None
    chat.ai_disabled = False
    _edit_group_topic_background(bot, chat, role_hint=None)
    try:
        sysmsg = await Message.create(chat_id=chat_id, user_id=chat.user_id, message_type="manager", content="Менеджер завершил сессию. AI активирован", source="system", text="Менеджер завершил сессию. AI активирован", admin_user_id=user.id)
    except Exception:
        sysmsg = await Message.create(chat_id=chat_id, user_id=chat.user_id, message_type="manager", content="Менеджер завершил сессию. AI активирован")
    try:
        await Chat.filter(id=chat_id).update(last_message_at=sysmsg.created_at, updated_at=sysmsg.created_at)
    except Exception:
        pass
    bot._clear_manager_header(chat_id)
    _notify_client_background(bot, chat.user_id, "👨‍💼 Менеджер завершил сессию. Теперь вам помогает 🤖 AI-поддержка.")
    await request.app.state.ws_manager.broadcast("status_changed", {"chat_id": chat_id, "status": "active"})
    try:
        await request.app.state.ws_manager.broadcast(
            "new_message",
            {
                "chat_id": chat_id,
                "message": {
                    "id": sysmsg.id,
                    "text": getattr(sysmsg, "text", None) or sysmsg.content,
                    "source": "system",
                    "created_at": sysmsg.created_at.isoformat() if sysmsg.created_at else None,
                },
            },
        )
    except Exception:
        pass
    return {"ok": True}


@router.delete("/{chat_id}")
async def delete_chat(chat_id: int, request: Request, user: AdminUser = Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    from modules.database import Message, ManagerNotification
    await Message.filter(chat_id=chat_id).delete()
    await ManagerNotification.filter(chat_id=chat_id).delete()
    
    bot = request.app.state.bot
    if bot._group_id and getattr(bot, "redis", None):
        thread_id = bot.redis.get(f"group_topic:chat:{chat.id}")
        if thread_id:
            try:
                await bot.application.bot.delete_forum_topic(chat_id=bot._group_id, message_thread_id=int(thread_id))
            except Exception:
                pass
            bot.redis.delete(f"group_topic:chat:{chat.id}")
            bot.redis.delete(f"group_topic:thread:{thread_id}")
            bot.redis.delete(f"group_topic:name:{thread_id}")
            bot.redis.delete(f"group_topic:pin:{thread_id}")

    await chat.delete()
    return {"ok": True}
