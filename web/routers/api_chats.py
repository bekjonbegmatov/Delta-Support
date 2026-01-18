from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import Response
from modules.database import Chat, Message, AdminUser
from web.deps import get_current_user
from modules.bot import SupportBot
from telegram.constants import ParseMode
from tortoise.expressions import Q
import io

router = APIRouter(prefix="/api/chats", tags=["chats"])

@router.get("")
async def list_chats(user: AdminUser = Depends(get_current_user), status: str = Query(None), q: str = Query(None)):
    qs = Chat.all().order_by("-updated_at")
    if status:
        qs = qs.filter(status=status)
    if q:
        qv = q.strip()
        qs = qs.filter(Q(username__icontains=qv) | Q(first_name__icontains=qv) | Q(last_name__icontains=qv))
    chats = await qs.limit(50)
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
        }
        for c in chats
    ]

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
        "updated_at": chat.updated_at.isoformat(),
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
        "updated_at": chat.updated_at.isoformat(),
        "created_at": chat.created_at.isoformat() if getattr(chat, "created_at", None) else None,
        "topic_id": chat.topic_id,
        "avatar_url": f"/api/chats/{chat_id}/avatar",
    }


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
        await Chat.filter(id=chat_id).update(last_message_at=msg.created_at)
    except Exception:
        pass
    # отправка через бота
    bot: SupportBot = request.app.state.bot
    header = await bot.application.bot.send_message(chat_id=chat.user_id, text="👨‍💼 Менеджер поддержки")
    sent_user = await bot.application.bot.send_message(chat_id=chat.user_id, text=text, reply_to_message_id=header.message_id)
    try:
        await Message.filter(id=msg.id).update(tg_message_id_user=sent_user.message_id)
    except Exception:
        pass
    if bot.config.telegram_group_mode and bot.config.telegram_support_group_id:
        thread_id = await bot._ensure_group_topic(chat)
        if thread_id:
            sent_group = await bot.application.bot.send_message(chat_id=bot.config.telegram_support_group_id, message_thread_id=int(thread_id), text=text, parse_mode=ParseMode.HTML)
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
        await Chat.filter(id=chat_id).update(last_message_at=msg.created_at)
    except Exception:
        pass
    bot: SupportBot = request.app.state.bot
    raw_bytes = await file.read()
    filename = file.filename or "upload"
    header = await bot.application.bot.send_message(chat_id=chat.user_id, text="👨‍💼 Менеджер поддержки")
    sent_user = None
    media_file_id_for_ws = None
    if media_type == "photo":
        sent_user = await bot.application.bot.send_photo(chat_id=chat.user_id, photo=io.BytesIO(raw_bytes), caption=caption or None, reply_to_message_id=header.message_id)
        try:
            file_id = sent_user.photo[-1].file_id if sent_user.photo else None
            media_file_id_for_ws = file_id
            await Message.filter(id=msg.id).update(tg_message_id_user=sent_user.message_id, media_file_id=file_id)
        except Exception:
            pass
    elif media_type == "video":
        sent_user = await bot.application.bot.send_video(chat_id=chat.user_id, video=io.BytesIO(raw_bytes), caption=caption or None, reply_to_message_id=header.message_id)
        try:
            file_id = getattr(sent_user.video, "file_id", None)
            media_file_id_for_ws = file_id
            await Message.filter(id=msg.id).update(tg_message_id_user=sent_user.message_id, media_file_id=file_id)
        except Exception:
            pass
    elif media_type == "audio":
        try:
            sent_user = await bot.application.bot.send_audio(chat_id=chat.user_id, audio=io.BytesIO(raw_bytes), caption=caption or None, reply_to_message_id=header.message_id)
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
            sent_user = await bot.application.bot.send_document(chat_id=chat.user_id, document=bio, caption=caption or None, reply_to_message_id=header.message_id)
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
            sent_user = await bot.application.bot.send_voice(chat_id=chat.user_id, voice=io.BytesIO(raw_bytes), caption=caption or None, reply_to_message_id=header.message_id)
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
            sent_user = await bot.application.bot.send_document(chat_id=chat.user_id, document=bio, caption=caption or None, reply_to_message_id=header.message_id)
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
        sent_user = await bot.application.bot.send_document(chat_id=chat.user_id, document=bio, caption=caption or None, reply_to_message_id=header.message_id)
        try:
            file_id = getattr(sent_user.document, "file_id", None)
            media_file_id_for_ws = file_id
            await Message.filter(id=msg.id).update(tg_message_id_user=sent_user.message_id, media_file_id=file_id)
        except Exception:
            pass
    if bot.config.telegram_group_mode and bot.config.telegram_support_group_id:
        thread_id = await bot._ensure_group_topic(chat)
        if thread_id:
            try:
                if media_type == "photo":
                    sent_group = await bot.application.bot.send_photo(
                        chat_id=bot.config.telegram_support_group_id,
                        message_thread_id=int(thread_id),
                        photo=io.BytesIO(raw_bytes),
                        caption=caption or None,
                        parse_mode=ParseMode.HTML,
                    )
                elif media_type == "video":
                    sent_group = await bot.application.bot.send_video(
                        chat_id=bot.config.telegram_support_group_id,
                        message_thread_id=int(thread_id),
                        video=io.BytesIO(raw_bytes),
                        caption=caption or None,
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    bio = io.BytesIO(raw_bytes)
                    try:
                        bio.name = filename
                    except Exception:
                        pass
                    sent_group = await bot.application.bot.send_document(
                        chat_id=bot.config.telegram_support_group_id,
                        message_thread_id=int(thread_id),
                        document=bio,
                        caption=caption or None,
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


@router.post("/{chat_id}/join")
async def join_chat(request: Request, chat_id: int, user: AdminUser = Depends(get_current_user)):
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    await Chat.filter(id=chat_id).update(status="waiting_manager", assigned_admin_id=user.id)
    try:
        sysmsg = await Message.create(chat_id=chat_id, user_id=chat.user_id, message_type="manager", content="Менеджер подключился", source="system", text="Менеджер подключился")
    except Exception:
        sysmsg = await Message.create(chat_id=chat_id, user_id=chat.user_id, message_type="manager", content="Менеджер подключился")
    try:
        await Chat.filter(id=chat_id).update(last_message_at=sysmsg.created_at)
    except Exception:
        pass
    bot: SupportBot = request.app.state.bot
    try:
        await bot.application.bot.send_message(chat_id=chat.user_id, text="👨‍💼 Менеджер подключился к вашему чату. Можете писать сообщение.")
    except Exception:
        pass
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
    await Chat.filter(id=chat_id).update(status="closed", assigned_admin_id=None, manager_id=None)
    try:
        sysmsg = await Message.create(chat_id=chat_id, user_id=chat.user_id, message_type="manager", content="Чат закрыт. AI активирован", source="system", text="Чат закрыт. AI активирован", admin_user_id=user.id)
    except Exception:
        sysmsg = await Message.create(chat_id=chat_id, user_id=chat.user_id, message_type="manager", content="Чат закрыт. AI активирован")
    try:
        await Chat.filter(id=chat_id).update(last_message_at=sysmsg.created_at)
    except Exception:
        pass
    bot: SupportBot = request.app.state.bot
    try:
        await bot.application.bot.send_message(chat_id=chat.user_id, text="✅ Чат с менеджером закрыт. Теперь вам помогает 🤖 AI-поддержка.")
    except Exception:
        pass
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
    await Chat.filter(id=chat_id).update(status="active", assigned_admin_id=None, manager_id=None)
    try:
        sysmsg = await Message.create(chat_id=chat_id, user_id=chat.user_id, message_type="manager", content="Менеджер завершил сессию. AI активирован", source="system", text="Менеджер завершил сессию. AI активирован", admin_user_id=user.id)
    except Exception:
        sysmsg = await Message.create(chat_id=chat_id, user_id=chat.user_id, message_type="manager", content="Менеджер завершил сессию. AI активирован")
    try:
        await Chat.filter(id=chat_id).update(last_message_at=sysmsg.created_at)
    except Exception:
        pass
    bot: SupportBot = request.app.state.bot
    try:
        await bot.application.bot.send_message(chat_id=chat.user_id, text="👨‍💼 Менеджер завершил сессию. Теперь вам помогает 🤖 AI-поддержка.")
    except Exception:
        pass
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
