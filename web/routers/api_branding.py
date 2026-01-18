from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse
from modules.config import Config
from modules.database import AdminUser, SystemConfig
from web.deps import get_current_user
from pathlib import Path
import time

router = APIRouter(prefix="/api/branding", tags=["branding"])


def _require_admin(user: AdminUser):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("")
async def get_branding(request: Request):
    config = Config()
    keys = ["brand_name", "brand_tagline", "brand_logo_url"]
    rows = await SystemConfig.filter(key__in=keys).all()
    values = {r.key: r.value for r in rows}
    logo_url = (values.get("brand_logo_url") or "").strip()
    if logo_url.startswith("/static/uploads/branding/"):
        logo_url = f"/api/branding/logo/{Path(logo_url).name}"
    return {
        "name": values.get("brand_name") or config.project_name or "Support Desk",
        "tagline": values.get("brand_tagline") or "Админ‑панель поддержки",
        "logo_url": logo_url,
    }


@router.put("")
async def update_branding(request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    body = await request.json()
    name = (body.get("name") or "").strip()
    tagline = (body.get("tagline") or "").strip()
    logo_url = (body.get("logo_url") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    await SystemConfig.update_or_create(key="brand_name", defaults={"value": name, "description": "Название панели"})
    await SystemConfig.update_or_create(key="brand_tagline", defaults={"value": tagline, "description": "Подзаголовок/слоган"})
    await SystemConfig.update_or_create(key="brand_logo_url", defaults={"value": logo_url, "description": "URL логотипа"})
    return {"ok": True}


@router.post("/logo")
async def upload_logo(file: UploadFile = File(...), user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    content_type = (file.content_type or "").lower()
    ext = None
    if content_type in ["image/jpeg", "image/jpg"]:
        ext = "jpg"
    elif content_type == "image/png":
        ext = "png"
    elif content_type == "image/webp":
        ext = "webp"
    if not ext:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    ts = int(time.time())
    base = Path(__file__).resolve().parent.parent
    static_dir = base / "static"
    out_dir = static_dir / "uploads" / "branding"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"logo_{ts}.{ext}"
    data = await file.read()
    out_path.write_bytes(data)
    url = f"/api/branding/logo/{out_path.name}"
    await SystemConfig.update_or_create(key="brand_logo_url", defaults={"value": url, "description": "URL логотипа"})
    return {"ok": True, "logo_url": url}


@router.get("/logo/{filename}")
async def get_logo_file(filename: str):
    safe = Path(filename).name
    base = Path(__file__).resolve().parent.parent
    path = base / "static" / "uploads" / "branding" / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path)
