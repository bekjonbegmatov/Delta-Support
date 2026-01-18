from fastapi import APIRouter, Depends, HTTPException, Request
from modules.database import AdminUser, Message
from web.deps import get_current_user
from web.utils import get_password_hash
from datetime import datetime, timedelta

router = APIRouter(prefix="/api/users", tags=["users"])


def _require_admin(user: AdminUser):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("")
async def list_users(user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    users = await AdminUser.all().order_by("id")
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat(),
            "last_login": u.last_login.isoformat() if u.last_login else None,
            "access_start_hour": getattr(u, "access_start_hour", None),
            "access_end_hour": getattr(u, "access_end_hour", None),
        }
        for u in users
    ]


@router.post("")
async def create_user(request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    role = body.get("role") or "manager"
    if not username or not password:
        raise HTTPException(status_code=400, detail="username/password required")
    if role not in ("admin", "manager"):
        raise HTTPException(status_code=400, detail="Invalid role")
    exists = await AdminUser.get_or_none(username=username)
    if exists:
        raise HTTPException(status_code=409, detail="User exists")
    created = await AdminUser.create(username=username, password_hash=get_password_hash(password), role=role, is_active=True)
    return {"ok": True, "id": created.id}


@router.patch("/{user_id}")
async def update_user(user_id: int, request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    target = await AdminUser.get_or_none(id=user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    body = await request.json()
    if "username" in body:
        username = (body.get("username") or "").strip()
        if not username:
            raise HTTPException(status_code=400, detail="username required")
        if len(username) < 3:
            raise HTTPException(status_code=400, detail="Username too short")
        if len(username) > 64:
            raise HTTPException(status_code=400, detail="Username too long")
        exists = await AdminUser.filter(username=username).exclude(id=target.id).exists()
        if exists:
            raise HTTPException(status_code=400, detail="Username already taken")
        target.username = username
    if "role" in body:
        role = body.get("role")
        if role not in ("admin", "manager"):
            raise HTTPException(status_code=400, detail="Invalid role")
        target.role = role
        if role == "admin":
            target.is_active = True
            target.access_start_hour = None
            target.access_end_hour = None
    if "is_active" in body:
        next_active = bool(body.get("is_active"))
        if not next_active and target.role == "admin":
            raise HTTPException(status_code=400, detail="Admin cannot be disabled")
        target.is_active = next_active
    if "access_start_hour" in body or "access_end_hour" in body:
        if target.role == "admin":
            target.access_start_hour = None
            target.access_end_hour = None
            await target.save()
            return {"ok": True}
        start = body.get("access_start_hour", None)
        end = body.get("access_end_hour", None)
        if start is None or start == "":
            start = None
        if end is None or end == "":
            end = None
        if start is not None:
            try:
                start = int(start)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid access_start_hour")
            if start < 0 or start > 23:
                raise HTTPException(status_code=400, detail="Invalid access_start_hour")
        if end is not None:
            try:
                end = int(end)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid access_end_hour")
            if end < 0 or end > 23:
                raise HTTPException(status_code=400, detail="Invalid access_end_hour")
        target.access_start_hour = start
        target.access_end_hour = end
    await target.save()
    return {"ok": True}


@router.post("/{user_id}/reset-password")
async def reset_password(user_id: int, request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    target = await AdminUser.get_or_none(id=user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    body = await request.json()
    new_password = body.get("new_password") or ""
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password too short")
    target.password_hash = get_password_hash(new_password)
    await target.save()
    return {"ok": True}


@router.get("/{user_id}/stats")
async def user_stats(user_id: int, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    target = await AdminUser.get_or_none(id=user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    since = datetime.utcnow() - timedelta(days=7)
    msgs = await Message.filter(admin_user_id=user_id, created_at__gte=since).all()
    by_hour = [0] * 24
    for m in msgs:
        try:
            h = (m.created_at or datetime.utcnow()).hour
            by_hour[h] += 1
        except Exception:
            pass
    return {
        "id": target.id,
        "last_login": target.last_login.isoformat() if target.last_login else None,
        "messages_7d": len(msgs),
        "by_hour": by_hour,
    }
