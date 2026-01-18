from fastapi import APIRouter, WebSocket
from jose import jwt, JWTError
from web.utils import SECRET_KEY, ALGORITHM
from modules.database import AdminUser

router = APIRouter()

@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    token = websocket.cookies.get("access_token")
    if not token:
        await websocket.close(code=1008)
        return
    try:
        scheme, _, param = token.partition(" ")
        payload = jwt.decode(param, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            await websocket.close(code=1008)
            return
        user = await AdminUser.get_or_none(username=username)
        if not user:
            await websocket.close(code=1008)
            return
    except JWTError:
        await websocket.close(code=1008)
        return
    await websocket.app.state.ws_manager.connect(websocket)
    try:
        while True:
            # Мы не принимаем клиентские события пока, просто держим соединение
            await websocket.receive_text()
    except Exception:
        websocket.app.state.ws_manager.disconnect(websocket)
