from fastapi import APIRouter, Depends, Request, Form, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from web.deps import get_current_user
from modules.database import Chat, Message, AdminUser
from modules.bot import SupportBot
from pathlib import Path
from loguru import logger
from telegram.constants import ParseMode

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

@router.get("/chats/{chat_id}")
async def chat_detail(request: Request, chat_id: int, user: AdminUser = Depends(get_current_user)):
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    messages = await Message.filter(chat_id=chat_id).order_by("created_at")
    
    return templates.TemplateResponse("chat_detail.html", {
        "request": request,
        "user": user,
        "chat": chat,
        "messages": messages
    })

@router.post("/chats/{chat_id}/send")
async def send_message(
    request: Request, 
    chat_id: int, 
    text: str = Form(...), 
    user: AdminUser = Depends(get_current_user)
):
    chat = await Chat.get_or_none(id=chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    # Сохраняем сообщение в БД (используем ID назначенного менеджера, иначе ID пользователя админки)
    sender_uid = chat.manager_id or user.id
    try:
        msg = await Message.create(
            chat_id=chat_id,
            user_id=sender_uid,
            message_type="manager",
            content=text,
            source="manager_web",
            text=text,
            admin_user_id=user.id
        )
    except Exception as e:
        logger.warning(f"Extended fields not supported, fallback create: {e}")
        msg = await Message.create(
            chat_id=chat_id,
            user_id=sender_uid,
            message_type="manager",
            content=text
        )
    
    # Отправляем через бота
    bot: SupportBot = request.app.state.bot
    try:
        # Отправка пользователю
        header = await bot.application.bot.send_message(chat_id=chat.user_id, text="👨‍💼 Менеджер поддержки")
        sent_user = await bot.application.bot.send_message(
            chat_id=chat.user_id,
            text=text,
            reply_to_message_id=header.message_id
        )
        try:
            await Message.filter(id=msg.id).update(tg_message_id_user=sent_user.message_id)
        except Exception:
            pass
        
        # Отправка в группу поддержки (если включено)
        if bot.config.telegram_group_mode and bot.config.telegram_support_group_id:
            thread_id = await bot._ensure_group_topic(chat)
            if thread_id:
                try:
                    mute = chat.status != "waiting_manager"
                    header_g = await bot.application.bot.send_message(
                        chat_id=bot.config.telegram_support_group_id,
                        message_thread_id=int(thread_id),
                        text=f"👨‍💻 Менеджер: {user.username or 'manager'}",
                        disable_notification=mute
                    )
                    sent_group = await bot.application.bot.send_message(
                        chat_id=bot.config.telegram_support_group_id,
                        message_thread_id=int(thread_id),
                        reply_to_message_id=header_g.message_id,
                        text=text,
                        parse_mode=ParseMode.HTML,
                        disable_notification=mute
                    )
                    try:
                        await Message.filter(id=msg.id).update(tg_message_id_group=sent_group.message_id)
                    except Exception:
                        pass
                    await bot._edit_group_topic_status(chat, role_hint="manager")
                except Exception as e:
                    logger.error(f"Failed to send to group: {e}")
            else:
                logger.warning(f"No group thread for chat {chat.id}")
                
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        # Можно добавить flash message об ошибке
    
    # Broadcast событие для UI
    try:
        await request.app.state.ws_manager.broadcast("new_message", {
            "chat_id": chat_id,
            "message": {
                "id": msg.id,
                "text": text,
                "source": "manager_web"
            }
        })
    except Exception as e:
        logger.warning(f"WS broadcast failed: {e}")
    
    return RedirectResponse(url=f"/chats/{chat_id}", status_code=status.HTTP_303_SEE_OTHER)
