import os
import json
import base64
import mimetypes
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from cryptography.fernet import Fernet, InvalidToken

from app.database import get_db, ROOT_DIR
from app.models.visual import VisualAsset, ImageTask
from app.models.studio import AIProviderConfig, AIRuntimeSetting
from app.services.media_service import http_download

router = APIRouter(prefix="/api/visual", tags=["AI视觉素材"])

ROOT = ROOT_DIR
ASSET_DIR = ROOT / "storage" / "visual-assets"
REFERENCE_DIR = ROOT / "storage" / "reference-images"
ASSET_DIR.mkdir(parents=True, exist_ok=True)
REFERENCE_DIR.mkdir(parents=True, exist_ok=True)


class AssetInput(BaseModel):
    category: str
    name: str
    description: str = ""
    appearance: str = ""
    clothing: str = ""
    personality: str = ""
    consistency_prompt: str = ""
    style: str = "3D动漫电影风格"
    data: dict = Field(default_factory=dict)


class GenerateInput(BaseModel):
    asset_id: Optional[int] = None
    provider: str = "doubao"
    task_type: str = "character"
    prompt: str
    size: str = "2K"
    confirm_paid: bool = False


def decrypt_key(encrypted):
    if not encrypted:
        return ""
    key_file = ROOT / "storage" / "secrets" / "local.key"
    if not key_file.exists():
        raise HTTPException(400, "本地加密密钥不存在，无法读取服务商 API Key")
    try:
        key = key_file.read_bytes()
        return Fernet(key).decrypt(encrypted.encode()).decode()
    except (InvalidToken, ValueError):
        raise HTTPException(400, "API Key 解密失败，请重新保存服务商配置")


def get_runtime(db):
    setting = db.query(AIRuntimeSetting).first()
    if not setting:
        return {"network_enabled": False, "paid_api_allowed": False}
    return {
        "network_enabled": bool(setting.network_enabled),
        "paid_api_allowed": bool(setting.paid_api_allowed),
    }


@router.get("/assets")
def list_assets(category: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(VisualAsset)
    if category:
        query = query.filter(VisualAsset.category == category)
    items = query.order_by(VisualAsset.created_at.desc()).all()
    return [{
        "id": x.id,
        "category": x.category,
        "name": x.name,
        "description": x.description,
        "appearance": x.appearance,
        "clothing": x.clothing,
        "personality": x.personality,
        "consistency_prompt": x.consistency_prompt,
        "style": x.style,
        "image_path": x.image_path,
        "reference_path": x.reference_path,
        "confirmed": x.confirmed,
        "data": x.data or {},
        "created_at": x.created_at.isoformat() if x.created_at else None,
    } for x in items]


@router.post("/assets")
def create_asset(payload: AssetInput, db: Session = Depends(get_db)):
    allowed = {"characters", "scenes", "props"}
    if payload.category not in allowed:
        raise HTTPException(400, "素材分类只支持 characters、scenes、props")
    item = VisualAsset(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"success": True, "id": item.id}


@router.put("/assets/{asset_id}")
def update_asset(asset_id: int, payload: AssetInput, db: Session = Depends(get_db)):
    item = db.query(VisualAsset).filter(VisualAsset.id == asset_id).first()
    if not item:
        raise HTTPException(404, "素材不存在")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    item.updated_at = datetime.utcnow()
    db.commit()
    return {"success": True}


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: int, db: Session = Depends(get_db)):
    item = db.query(VisualAsset).filter(VisualAsset.id == asset_id).first()
    if not item:
        raise HTTPException(404, "素材不存在")
    db.delete(item)
    db.commit()
    return {"success": True}


@router.post("/references")
async def upload_reference(
    file: UploadFile = File(...),
    asset_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    item = None
    if asset_id:
        item = db.query(VisualAsset).filter(VisualAsset.id == asset_id).first()
        if not item:
            raise HTTPException(404, "指定素材不存在")

    suffix = Path(file.filename or "reference.png").suffix[:12].lower() or ".png"
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise HTTPException(400, "参考图片只支持 PNG、JPG、WEBP 或 GIF")
    filename = f"reference_{datetime.now().strftime('%Y%m%d%H%M%S%f')}{suffix}"
    destination = REFERENCE_DIR / filename
    size = 0
    try:
        with destination.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise HTTPException(413, "参考图片不能超过 100 MB")
                output.write(chunk)
        if item:
            item.reference_path = str(destination)
            db.commit()
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return {"success": True, "filename": filename, "path": str(destination)}


@router.get("/references/{filename}")
def get_reference(filename: str):
    from fastapi.responses import FileResponse
    path = (REFERENCE_DIR / Path(filename).name).resolve()
    if not path.exists() or REFERENCE_DIR.resolve() not in path.parents:
        raise HTTPException(404, "参考图片不存在")
    return FileResponse(path)


@router.get("/tasks")
def list_tasks(db: Session = Depends(get_db)):
    tasks = db.query(ImageTask).order_by(ImageTask.created_at.desc()).limit(100).all()
    return [{
        "id": x.id,
        "asset_id": x.asset_id,
        "provider": x.provider,
        "model": x.model,
        "task_type": x.task_type,
        "prompt": x.prompt,
        "status": x.status,
        "result_path": x.result_path,
        "result_url": x.result_url,
        "error": x.error,
        "created_at": x.created_at.isoformat() if x.created_at else None,
    } for x in tasks]


@router.post("/tasks")
def create_task(payload: GenerateInput, db: Session = Depends(get_db)):
    if not payload.prompt.strip():
        raise HTTPException(400, "图片提示词不能为空")
    if payload.asset_id:
        asset = db.query(VisualAsset).filter(VisualAsset.id == payload.asset_id).first()
        if not asset:
            raise HTTPException(404, "关联素材不存在")

    task = ImageTask(
        asset_id=payload.asset_id,
        provider=payload.provider,
        task_type=payload.task_type,
        prompt=payload.prompt.strip(),
        size=payload.size,
        status="pending",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return {"success": True, "task_id": task.id, "status": task.status}


@router.post("/tasks/{task_id}/run")
def run_task(task_id: int, confirm_paid: bool = False, db: Session = Depends(get_db)):
    task = db.query(ImageTask).filter(ImageTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "图片任务不存在")
    if task.status == "completed":
        return {"success": True, "task_id": task.id, "status": task.status, "result_path": task.result_path}
    if task.status == "running":
        raise HTTPException(409, "任务正在执行")

    runtime = get_runtime(db)
    if not runtime["network_enabled"]:
        raise HTTPException(403, "网络调用总开关已关闭，请先到 AI 管理中心开启")
    
    provider = db.query(AIProviderConfig).filter(
        AIProviderConfig.provider == task.provider
    ).first()
    if not provider or not provider.enabled:
        raise HTTPException(400, f"{task.provider} 服务商尚未配置或未启用")
    if not provider.encrypted_api_key:
        raise HTTPException(400, "服务商尚未保存 API Key")
    if not runtime["paid_api_allowed"] or not confirm_paid:
        raise HTTPException(403, "该服务商可能产生费用。请开启付费授权，并在本次任务中明确确认")

    task.status = "running"
    task.error = ""
    db.commit()

    try:
        api_key = decrypt_key(provider.encrypted_api_key)
        base_url = (provider.base_url or "").rstrip("/")
        if not base_url:
            raise ValueError("服务商 Base URL 未配置")

        # 豆包 Ark 图片生成接口采用 OpenAI 风格的 images/generations 请求格式。
        endpoint = base_url if base_url.endswith("/images/generations") else base_url + "/images/generations"
        body = {
            "model": provider.model,
            "prompt": task.prompt,
            "size": task.size,
            "response_format": "url",
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            result = json.loads(response.read().decode("utf-8"))

        data = result.get("data") or []
        if not data:
            raise ValueError("图片服务没有返回图片数据：" + json.dumps(result, ensure_ascii=False)[:1000])

        first = data[0]
        image_url = first.get("url", "")
        b64_data = first.get("b64_json", "")

        if b64_data:
            image_bytes = base64.b64decode(b64_data)
            filename = f"image_task_{task.id}.png"
            output = ASSET_DIR / filename
            output.write_bytes(image_bytes)
            task.result_path = str(output)
        elif image_url:
            task.result_url = image_url
            filename = f"image_task_{task.id}.png"
            output = ASSET_DIR / filename
            http_download(image_url, output, max_bytes=100 * 1024 * 1024)
            task.result_path = str(output)
        else:
            raise ValueError("服务商返回结果中没有图片 URL 或 b64_json")

        task.model = provider.model
        task.status = "completed"
        task.finished_at = datetime.utcnow()

        if task.asset_id:
            asset = db.query(VisualAsset).filter(VisualAsset.id == task.asset_id).first()
            if asset:
                asset.image_path = task.result_path
                asset.updated_at = datetime.utcnow()

        db.commit()
        return {
            "success": True,
            "task_id": task.id,
            "status": task.status,
            "result_path": task.result_path,
            "result_url": task.result_url,
        }

    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        task.status = "failed"
        task.error = f"图片服务 HTTP {exc.code}: {detail}"
        db.commit()
        raise HTTPException(502, task.error)
    except Exception as exc:
        task.status = "failed"
        task.error = str(exc)[:2000]
        db.commit()
        raise HTTPException(500, task.error)


@router.get("/files/{filename}")
def get_visual_file(filename: str):
    from fastapi.responses import FileResponse
    path = (ASSET_DIR / Path(filename).name).resolve()
    if not path.exists() or ASSET_DIR.resolve() not in path.parents:
        raise HTTPException(404, "图片文件不存在")
    return FileResponse(path)


@router.post("/assets/{asset_id}/confirm")
def confirm_asset(asset_id: int, db: Session = Depends(get_db)):
    item = db.query(VisualAsset).filter(VisualAsset.id == asset_id).first()
    if not item:
        raise HTTPException(404, "素材不存在")
    item.confirmed = True
    db.commit()
    return {"success": True, "confirmed": True}

