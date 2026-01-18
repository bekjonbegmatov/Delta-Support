from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from web.deps import get_current_user
from modules.database import Chat, AdminUser
from pathlib import Path

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

@router.get("/dashboard")
async def dashboard(request: Request, user: AdminUser = Depends(get_current_user)):
    # Получаем статистику
    total_chats = await Chat.all().count()
    active_chats = await Chat.filter(status="active").count()
    waiting_chats = await Chat.filter(status="waiting_manager").count()
    
    # Получаем последние чаты
    recent_chats = await Chat.all().order_by("-updated_at").limit(10)
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "stats": {
            "total": total_chats,
            "active": active_chats,
            "waiting": waiting_chats
        },
        "chats": recent_chats
    })
