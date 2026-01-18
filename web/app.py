from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from web.ws_manager import WSManager

# Создаем приложение FastAPI
app = FastAPI(
    title="DELTA-Support Admin Panel",
    description="Админ-панель для управления ботом поддержки",
    version="1.0.0"
)

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Пути к файлам
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# Подключение статики
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
SPA_DIR = STATIC_DIR / "spa"
if SPA_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(SPA_DIR), html=True), name="app")

# Настройка шаблонов
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Подключение роутеров
from web.routers import auth, dashboard, chats, ws, api_auth, api_chats, api_users, api_settings, api_branding, api_kb
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(chats.router)
app.include_router(ws.router)
app.include_router(api_auth.router)
app.include_router(api_chats.router)
app.include_router(api_users.router)
app.include_router(api_settings.router)
app.include_router(api_branding.router)
app.include_router(api_kb.router)

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "title": "DELTA-Support"})

# WS manager в состоянии приложения
app.state.ws_manager = WSManager()
