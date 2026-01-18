from fastapi import APIRouter, Request, status, HTTPException, Depends
from fastapi.responses import JSONResponse
from modules.database import AdminUser
from web.utils import verify_password, create_access_token, get_password_hash
from web.deps import get_current_user
from datetime import datetime

router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/login")
async def api_login(request: Request):
    body = await request.json()
    username = body.get("username")
    password = body.get("password")
    if not username or not password:
        raise HTTPException(status_code=400, detail="username/password required")
    user = await AdminUser.get_or_none(username=username)
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User blocked")
    start = getattr(user, "access_start_hour", None)
    end = getattr(user, "access_end_hour", None)
    if user.role == "manager" and start is not None and end is not None:
        now_h = datetime.now().hour
        allowed = False
        if start == end:
            allowed = True
        elif start < end:
            allowed = start <= now_h < end
        else:
            allowed = now_h >= start or now_h < end
        if not allowed:
            raise HTTPException(status_code=403, detail="Access time restricted")
    try:
        user.last_login = datetime.utcnow()
        await user.save()
    except Exception:
        pass
    token = create_access_token(data={"sub": user.username, "role": user.role})
    resp = JSONResponse({"ok": True})
    # httpOnly cookie
    resp.set_cookie(
        key="access_token",
        value=f"Bearer {token}",
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 24,
    )
    return resp

@router.post("/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("access_token")
    return resp

@router.get("/me")
async def api_me(user: AdminUser = Depends(get_current_user)):
    return {"username": user.username, "role": user.role, "id": user.id}

@router.post("/change-password")
async def api_change_password(request: Request, user: AdminUser = Depends(get_current_user)):
    body = await request.json()
    old_password = body.get("old_password")
    new_password = body.get("new_password")
    if not old_password or not new_password:
        raise HTTPException(status_code=400, detail="old_password/new_password required")
    if not verify_password(old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Invalid old password")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password too short")
    user.password_hash = get_password_hash(new_password)
    await user.save()
    return {"ok": True}


@router.post("/change-username")
async def api_change_username(request: Request, user: AdminUser = Depends(get_current_user)):
    body = await request.json()
    new_username = (body.get("new_username") or "").strip()
    if not new_username:
        raise HTTPException(status_code=400, detail="new_username required")
    if len(new_username) < 3:
        raise HTTPException(status_code=400, detail="Username too short")
    if len(new_username) > 64:
        raise HTTPException(status_code=400, detail="Username too long")
    exists = await AdminUser.filter(username=new_username).exclude(id=user.id).exists()
    if exists:
        raise HTTPException(status_code=400, detail="Username already taken")
    user.username = new_username
    await user.save()
    token = create_access_token(data={"sub": user.username, "role": user.role})
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        key="access_token",
        value=f"Bearer {token}",
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 24,
    )
    return resp
