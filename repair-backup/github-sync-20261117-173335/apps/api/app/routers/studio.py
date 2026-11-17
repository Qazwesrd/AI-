import os
import uuid
import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session
from cryptography.fernet import Fernet, InvalidToken
from app.database import get_db, ROOT_DIR
from app.models.studio import (
    StudioResource, AIProviderConfig, AIRuntimeSetting,
    AIUsageLog, MediaAsset
)

router = APIRouter(prefix="/api", tags=["AI管理与素材中心"])

SECRET_DIR = ROOT_DIR / "storage" / "secrets"
SECRET_DIR.mkdir(parents=True, exist_ok=True)
KEY_FILE = SECRET_DIR / "local.key"
ASSET_DIR = ROOT_DIR / "storage" / "assets"
ASSET_DIR.mkdir(parents=True, exist_ok=True)

def get_cipher():
    if not KEY_FILE.exists():
        KEY_FILE.write_bytes(Fernet.generate_key())
    return Fernet(KEY_FILE.read_bytes())

def encrypt_key(value):
    if not value:
        return ""
    return get_cipher().encrypt(value.encode("utf-8")).decode("utf-8")

def decrypt_key(value):
    if not value:
        return ""
    try:
        return get_cipher().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise HTTPException(500, "API密钥解密失败，请检查本地密钥文件")

def runtime(db):
    row = db.query(AIRuntimeSetting).first()
    if not row:
        row = AIRuntimeSetting(network_enabled=False, paid_api_allowed=False)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row

def provider_dict(x):
    return {
        "id": x.id,
        "provider": x.provider,
        "display_name": x.display_name,
        "base_url": x.base_url,
        "model": x.model,
        "enabled": x.enabled,
        "paid": x.paid,
        "purpose": x.purpose,
        "has_api_key": bool(x.encrypted_api_key),
    }

def resource_dict(x):
    return {
        "id": x.id,
        "category": x.category,
        "name": x.name,
        "description": x.description,
        "data": x.data or {},
        "image_path": x.image_path,
        "confirmed": x.confirmed,
        "created_at": x.created_at.isoformat() if x.created_at else None,
    }

def asset_dict(x):
    return {
        "id": x.id,
        "filename": x.filename,
        "category": x.category,
        "mime_type": x.mime_type,
        "file_size": x.file_size,
        "description": x.description,
        "url": f"/api/assets/{x.id}/download",
        "created_at": x.created_at.isoformat() if x.created_at else None,
    }

class ProviderInput(BaseModel):
    provider: str
    display_name: str
    base_url: str
    model: str
    api_key: str = ""
    enabled: bool = False
    paid: bool = True
    purpose: str = "text"

class RuntimeInput(BaseModel):
    network_enabled: bool
    paid_api_allowed: bool

class ResourceInput(BaseModel):
    name: str
    description: str = ""
    data: dict = {}
    image_path: str = ""
    confirmed: bool = False

class ResourceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    data: dict | None = None
    image_path: str | None = None
    confirmed: bool | None = None

class ChatInput(BaseModel):
    provider: str
    messages: list[dict]
    confirm_paid: bool = False
    task: str = "chat"

@router.get("/ai/providers")
def list_providers(db: Session = Depends(get_db)):
    items = db.query(AIProviderConfig).order_by(AIProviderConfig.id).all()
    return {"items": [provider_dict(x) for x in items]}

@router.post("/ai/providers")
def save_provider(payload: ProviderInput, db: Session = Depends(get_db)):
    provider_id = payload.provider.strip().lower()
    if not provider_id:
        raise HTTPException(400, "服务商标识不能为空")

    item = db.query(AIProviderConfig).filter(
        AIProviderConfig.provider == provider_id
    ).first()

    if not item:
        item = AIProviderConfig(provider=provider_id)
        db.add(item)

    item.display_name = payload.display_name
    item.base_url = payload.base_url.rstrip("/")
    item.model = payload.model
    item.enabled = payload.enabled
    item.paid = payload.paid
    item.purpose = payload.purpose

    if payload.api_key.strip():
        item.encrypted_api_key = encrypt_key(payload.api_key.strip())

    db.commit()
    db.refresh(item)
    return provider_dict(item)

@router.delete("/ai/providers/{provider}")
def delete_provider(provider: str, db: Session = Depends(get_db)):
    item = db.query(AIProviderConfig).filter(
        AIProviderConfig.provider == provider
    ).first()
    if not item:
        raise HTTPException(404, "模型配置不存在")
    db.delete(item)
    db.commit()
    return {"success": True}

@router.get("/ai/runtime")
def get_runtime(db: Session = Depends(get_db)):
    x = runtime(db)
    return {
        "network_enabled": x.network_enabled,
        "paid_api_allowed": x.paid_api_allowed,
    }

@router.put("/ai/runtime")
def update_runtime(payload: RuntimeInput, db: Session = Depends(get_db)):
    x = runtime(db)
    x.network_enabled = payload.network_enabled
    x.paid_api_allowed = payload.paid_api_allowed
    db.commit()
    return {
        "success": True,
        "network_enabled": x.network_enabled,
        "paid_api_allowed": x.paid_api_allowed,
    }

@router.get("/ai/usage-logs")
def usage_logs(db: Session = Depends(get_db)):
    items = db.query(AIUsageLog).order_by(
        AIUsageLog.created_at.desc()
    ).limit(200).all()
    return {
        "items": [{
            "id": x.id,
            "provider": x.provider,
            "model": x.model,
            "task": x.task,
            "status": x.status,
            "detail": x.detail,
            "created_at": x.created_at.isoformat(),
        } for x in items]
    }

@router.post("/ai/chat")
def ai_chat(payload: ChatInput, db: Session = Depends(get_db)):
    settings = runtime(db)
    config = db.query(AIProviderConfig).filter(
        AIProviderConfig.provider == payload.provider.lower()
    ).first()

    if not settings.network_enabled:
        raise HTTPException(403, "网络调用总开关已关闭")
    if not config or not config.enabled:
        raise HTTPException(400, "模型不存在或尚未启用")
    if not config.base_url or not config.model or not config.encrypted_api_key:
        raise HTTPException(400, "请先完整配置模型地址、模型名称和 API 密钥")
    if config.paid and not (settings.paid_api_allowed and payload.confirm_paid):
        raise HTTPException(403, "该模型标记为付费服务，必须开启付费授权并在本次请求明确确认")

    api_key = decrypt_key(config.encrypted_api_key)
    url = config.base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": config.model,
        "messages": payload.messages,
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
        db.add(AIUsageLog(
            provider=config.provider,
            model=config.model,
            task=payload.task,
            status="success",
            detail="请求成功",
        ))
        db.commit()
        return {
            "success": True,
            "provider": config.provider,
            "model": config.model,
            "result": result,
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        db.add(AIUsageLog(
            provider=config.provider,
            model=config.model,
            task=payload.task,
            status="failed",
            detail=detail,
        ))
        db.commit()
        raise HTTPException(exc.code, f"模型服务返回错误：{detail}")
    except Exception as exc:
        db.add(AIUsageLog(
            provider=config.provider,
            model=config.model,
            task=payload.task,
            status="failed",
            detail=str(exc)[:2000],
        ))
        db.commit()
        raise HTTPException(502, f"模型请求失败：{str(exc)}")

RESOURCE_CATEGORIES = {
    "characters", "relationships", "scenes", "props", "world",
    "events", "scripts", "storyboards"
}

@router.get("/studio/{category}")
def list_resources(category: str, db: Session = Depends(get_db)):
    if category not in RESOURCE_CATEGORIES:
        raise HTTPException(404, "不支持的素材分类")
    items = db.query(StudioResource).filter(
        StudioResource.category == category
    ).order_by(StudioResource.updated_at.desc()).all()
    return {"items": [resource_dict(x) for x in items], "total": len(items)}

@router.post("/studio/{category}")
def create_resource(
    category: str,
    payload: ResourceInput,
    db: Session = Depends(get_db),
):
    if category not in RESOURCE_CATEGORIES:
        raise HTTPException(404, "不支持的素材分类")
    item = StudioResource(
        category=category,
        name=payload.name.strip(),
        description=payload.description,
        data=payload.data,
        image_path=payload.image_path,
        confirmed=payload.confirmed,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return resource_dict(item)

@router.put("/studio/items/{item_id}")
def update_resource(
    item_id: int,
    payload: ResourceUpdate,
    db: Session = Depends(get_db),
):
    item = db.query(StudioResource).filter(
        StudioResource.id == item_id
    ).first()
    if not item:
        raise HTTPException(404, "素材记录不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return resource_dict(item)

@router.delete("/studio/items/{item_id}")
def delete_resource(item_id: int, db: Session = Depends(get_db)):
    item = db.query(StudioResource).filter(
        StudioResource.id == item_id
    ).first()
    if not item:
        raise HTTPException(404, "素材记录不存在")
    db.delete(item)
    db.commit()
    return {"success": True}

@router.post("/assets/upload")
async def upload_asset(
    file: UploadFile = File(...),
    category: str = Form("other"),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    allowed = {"image", "audio", "video", "other"}
    if category not in allowed:
        raise HTTPException(400, "素材分类无效")

    original = Path(file.filename or "upload.bin").name
    suffix = Path(original).suffix.lower()
    stored = f"{uuid.uuid4().hex}{suffix}"
    destination = ASSET_DIR / stored

    size = 0
    try:
        with destination.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 2 * 1024 * 1024 * 1024:
                    raise HTTPException(413, "单个素材文件不能超过 2 GB")
                output.write(chunk)

        item = MediaAsset(
            filename=original,
            stored_name=stored,
            category=category,
            mime_type=file.content_type or "",
            file_size=size,
            file_path=str(destination),
            description=description,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return asset_dict(item)
    except Exception:
        if destination.exists():
            destination.unlink()
        raise

@router.get("/assets")
def list_assets(category: str | None = None, db: Session = Depends(get_db)):
    query = db.query(MediaAsset)
    if category:
        query = query.filter(MediaAsset.category == category)
    items = query.order_by(MediaAsset.created_at.desc()).limit(1000).all()
    return {"items": [asset_dict(x) for x in items], "total": len(items)}

@router.get("/assets/{asset_id}/download")
def download_asset(asset_id: int, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse
    item = db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
    if not item or not Path(item.file_path).exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(
        item.file_path,
        media_type=item.mime_type or "application/octet-stream",
        filename=item.filename,
    )

@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: int, db: Session = Depends(get_db)):
    item = db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
    if not item:
        raise HTTPException(404, "素材不存在")
    path = Path(item.file_path)
    if path.exists():
        path.unlink()
    db.delete(item)
    db.commit()
    return {"success": True}
