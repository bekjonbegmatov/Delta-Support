from fastapi import Request, HTTPException, status, Depends
from jose import jwt, JWTError
from web.utils import SECRET_KEY, ALGORITHM
from modules.database import AdminUser
from datetime import datetime

async def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    
    try:
        scheme, _, param = token.partition(" ")
        payload = jwt.decode(param, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    
    user = await AdminUser.get_or_none(username=username)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User blocked")

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
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access time restricted")
    
    return user
