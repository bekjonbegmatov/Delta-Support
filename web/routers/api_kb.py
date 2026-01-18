from fastapi import APIRouter, Depends, HTTPException, Request
from modules.database import AdminUser, KnowledgeBaseEntry
from web.deps import get_current_user

router = APIRouter(prefix="/api/kb", tags=["knowledge_base"])


def _require_admin(user: AdminUser):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("")
async def list_entries(user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    rows = await KnowledgeBaseEntry.all().order_by("-updated_at")
    return [
        {
            "id": r.id,
            "title": r.title,
            "content": r.content,
            "is_active": r.is_active,
            "updated_at": r.updated_at.isoformat(),
        }
        for r in rows
    ]


@router.post("")
async def create_entry(request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    body = await request.json()
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="title/content required")
    created = await KnowledgeBaseEntry.create(title=title, content=content, is_active=True)
    return {"ok": True, "id": created.id}


@router.patch("/{entry_id}")
async def update_entry(entry_id: int, request: Request, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    row = await KnowledgeBaseEntry.get_or_none(id=entry_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    body = await request.json()
    if "title" in body:
        row.title = (body.get("title") or "").strip()
    if "content" in body:
        row.content = (body.get("content") or "").strip()
    if "is_active" in body:
        row.is_active = bool(body.get("is_active"))
    if not row.title or not row.content:
        raise HTTPException(status_code=400, detail="title/content required")
    await row.save()
    return {"ok": True}


@router.delete("/{entry_id}")
async def delete_entry(entry_id: int, user: AdminUser = Depends(get_current_user)):
    _require_admin(user)
    deleted = await KnowledgeBaseEntry.filter(id=entry_id).delete()
    if not deleted:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}

