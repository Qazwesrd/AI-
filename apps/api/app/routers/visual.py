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

from app.database import get_db, ROOT_DIR, STORAGE_DIR
from app.models.visual import VisualAsset, ImageTask
from app.models.novel import Novel, AnalysisItem
from app.models.studio import AIProviderConfig, AIRuntimeSetting, AIUsageLog
from app.services.media_service import http_download
from app.services.task_runtime import acquire_task_lease, release_task_lease

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


def mark_image_submission_unknown(task, error: str):
    """Persist an explicit manual-review state after a request may have reached the provider."""
    task.status = "interrupted_unknown"
    task.error = (
        "图片请求已发出，但服务商返回错误，无法仅凭本地响应确认是否创建或计费。"
        "请先核对图像服务商控制台；确认没有创建任务且不会重复扣费后，"
        "再从统一任务中心人工确认重排队。原始错误：" + str(error)
    )[:2000]


def tripo_task_action(task):
    """Return a safe action for an existing 3D task; never resubmit uncertain work."""
    if task.status in ("submitting", "interrupted_unknown"):
        return "review"
    if task.status in ("submitted", "running"):
        return "reuse" if (task.result_url or "").startswith("tripo-task:") else "review"
    return "new"


def mark_tripo_submission_failure(task, asset, error: str, request_started: bool):
    """Keep the local task and asset status aligned after a failed 3D submission."""
    status = "interrupted_unknown" if request_started else "failed"
    message = (
        "3D 生成请求已发出，但结果未能可靠确认。请先核对 Tripo 控制台；"
        "确认没有远程任务后才能人工释放提交锁并重新提交。原始错误："
        if request_started else "3D 任务在远程生成请求发出前失败："
    ) + str(error)
    task.status = status
    task.error = message[:2000]
    if asset:
        asset_data = dict(asset.data) if isinstance(asset.data, dict) else {}
        model_task_data = dict(asset_data.get("model3d_task") or {})
        model_task_data["task_id"] = task.id
        model_task_data["status"] = status
        model_task_data["error"] = task.error
        model_task_data["updated_at"] = datetime.utcnow().isoformat()
        asset_data["model3d_task"] = model_task_data
        asset.data = asset_data
        asset.updated_at = datetime.utcnow()
    return status


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


@router.post("/assets/import-analysis/{novel_id}")
def import_novel_analysis_assets(novel_id: int, db: Session = Depends(get_db)):
    novel = db.query(Novel).filter(Novel.id == novel_id).first()
    if not novel:
        raise HTTPException(404, "小说不存在")
    category_map = {
        "character": "characters", "scene": "scenes", "prop": "props",
        "weapon": "weapons", "costume": "costumes",
        "architecture": "buildings", "special_item": "special_items",
    }
    rows = db.query(AnalysisItem).filter(AnalysisItem.novel_id == novel_id).all()
    created, updated, skipped, stale_skipped = 0, 0, 0, 0
    for row in rows:
        row_data = row.data if isinstance(row.data, dict) else {}
        # Old analysis belongs to the previous novel source revision. Keep it
        # visible for reference, but never silently sync it into current assets.
        if row_data.get("source_stale"):
            stale_skipped += 1
            continue
        target = category_map.get(row.category)
        if not target:
            skipped += 1
            continue
        data = row.data if isinstance(row.data, dict) else {}
        # 素材必须按来源小说隔离；不同小说同名角色/武器不能被错误覆盖。
        candidates = db.query(VisualAsset).filter(
            VisualAsset.category == target, VisualAsset.name == row.name,
        ).all()
        existing = next((
            item for item in candidates
            if isinstance(item.data, dict)
            and item.data.get("source_novel_id") == novel_id
            and item.data.get("source_analysis_item_id") == row.id
        ), None)
        appearance = str(data.get("appearance") or data.get("visual_description") or data.get("look") or "")
        clothing = str(data.get("clothing") or data.get("costume") or data.get("equipment") or "")
        personality = str(data.get("personality") or data.get("temperament") or "")
        consistency = str(data.get("consistency_prompt") or data.get("visual_consistency") or appearance)
        merged_data = dict(data)
        merged_data["source_novel_id"] = novel_id
        merged_data["source_analysis_item_id"] = row.id
        if existing:
            if not existing.confirmed:
                existing.description = row.description or existing.description
                existing.appearance = appearance or existing.appearance
                existing.clothing = clothing or existing.clothing
                existing.personality = personality or existing.personality
                existing.consistency_prompt = consistency or existing.consistency_prompt
                existing.data = merged_data
                existing.confirmed = bool(row.confirmed)
                updated += 1
            else:
                skipped += 1
            continue
        db.add(VisualAsset(
            category=target, name=row.name, description=row.description or "",
            appearance=appearance, clothing=clothing, personality=personality,
            consistency_prompt=consistency, style="3D动漫电影风格",
            data=merged_data, confirmed=bool(row.confirmed),
        ))
        created += 1
    db.commit()
    return {"success": True, "novel_id": novel_id, "created": created, "updated": updated, "skipped": skipped, "stale_skipped": stale_skipped, "total_analysis_items": len(rows), "message": f"已同步当前版本解析结果；跳过 {stale_skipped} 条旧版本过期解析，已确认的视觉资产不会被覆盖。"}

@router.post("/assets")
def create_asset(payload: AssetInput, db: Session = Depends(get_db)):
    allowed = {"characters", "scenes", "props", "weapons", "costumes", "buildings", "special_items"}
    if payload.category not in allowed:
        raise HTTPException(400, "素材分类支持人物、场景、道具、武器刀具、服装、建筑和特殊物品")
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
    asset_ids = {x.asset_id for x in tasks if x.asset_id}
    asset_rows = db.query(VisualAsset).filter(VisualAsset.id.in_(asset_ids)).all() if asset_ids else []
    asset_data_by_id = {x.id: (x.data if isinstance(x.data, dict) else {}) for x in asset_rows}
    return [{
        "id": x.id,
        "asset_id": x.asset_id,
        "provider": x.provider,
        "model": (asset_data_by_id.get(x.asset_id, {}).get("model3d_task") or {}).get("model", ""),
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
    if task.status == "paused":
        raise HTTPException(409, "任务已暂停，请先恢复任务")
    if task.status == "cancelled":
        raise HTTPException(409, "任务已取消，请先点击重试")

    runtime = get_runtime(db)
    if not runtime["network_enabled"]:
        raise HTTPException(403, "网络调用总开关已关闭，请先到 AI 管理中心开启")
    
    provider = db.query(AIProviderConfig).filter(
        AIProviderConfig.provider == task.provider
    ).first()
    if not provider or not provider.enabled:
        raise HTTPException(400, f"{task.provider} 服务商尚未配置或未启用")
    if getattr(provider, "purpose", "text") != "image":
        raise HTTPException(400, "所选服务商用途不是图像生成，请在 AI 管理中心选择图像服务商")
    if not provider.encrypted_api_key:
        raise HTTPException(400, "服务商尚未保存 API Key")
    if bool(getattr(provider, "paid", True)):
        if not runtime["paid_api_allowed"]:
            raise HTTPException(403, "该图像模型属于付费服务，请先开启付费API总授权")
        if not confirm_paid:
            raise HTTPException(403, "本次图像生成可能产生费用，请明确确认后再执行")

    lease = acquire_task_lease(db, "image", task.id)
    task.status = "running"
    task.error = ""
    db.commit()

    request_started = False
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
        request_started = True
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

        task.status = "completed"
        task.finished_at = datetime.utcnow()

        if task.asset_id:
            asset = db.query(VisualAsset).filter(VisualAsset.id == task.asset_id).first()
            if asset:
                data = dict(asset.data) if isinstance(asset.data, dict) else {}
                generated_views = dict(data.get("generated_views") or {})
                generated_views[task.task_type] = {
                    "task_id": task.id,
                    "path": task.result_path,
                    "generated_at": datetime.utcnow().isoformat(),
                    "provider": task.provider,
                    "model": provider.model,
                }
                data["generated_views"] = generated_views
                asset.data = data
                if task.task_type in ("character", "scene", "props", "weapons", "costumes", "buildings", "special_items"):
                    asset.image_path = task.result_path
                elif not asset.image_path:
                    asset.image_path = task.result_path
                asset.updated_at = datetime.utcnow()

        db.commit()
        release_task_lease(db, "image", task.id, True)
        return {
            "success": True,
            "task_id": task.id,
            "status": task.status,
            "result_path": task.result_path,
            "result_url": task.result_url,
        }

    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        provider_error = f"图片服务 HTTP {exc.code}: {detail}"
        # Even an HTTP error is not proof that the provider did not accept or
        # bill a request. Once urlopen has been called, require manual
        # reconciliation instead of making this task safely retryable.
        if request_started:
            mark_image_submission_unknown(task, provider_error)
        else:
            task.status = "failed"
            task.error = provider_error
        db.commit()
        release_task_lease(db, "image", task.id, False, task.error)
        raise HTTPException(502, task.error)
    except Exception as exc:
        if request_started:
            # A timeout, broken connection, malformed response, or local
            # download/write failure after sending the request can mean the
            # provider accepted and billed the image job. Never mark it as a
            # safely retryable failure without checking the provider console.
            task.status = "interrupted_unknown"
            task.error = (
                "图片请求已发出，但结果未能可靠确认。请先核对图像服务商控制台，"
                "确认没有创建任务且不会重复扣费后，再从统一任务中心人工确认重排队。"
                "原始错误：" + str(exc)
            )[:2000]
        else:
            task.status = "failed"
            task.error = str(exc)[:2000]
        db.commit()
        release_task_lease(db, "image", task.id, False, task.error)
        raise HTTPException(500, task.error)


def _get_image_task_or_404(task_id: int, db: Session):
    task = db.query(ImageTask).filter(ImageTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "图片任务不存在")
    return task


@router.post("/tasks/{task_id}/pause")
def pause_image_task(task_id: int, db: Session = Depends(get_db)):
    task = _get_image_task_or_404(task_id, db)
    if task.status != "pending":
        raise HTTPException(409, "只有尚未开始的待执行任务可以暂停；正在执行或已提交给第三方的任务不能在本地强制中断")
    task.status = "paused"
    db.commit()
    return {"success": True, "task_id": task.id, "status": task.status}


@router.post("/tasks/{task_id}/resume")
def resume_image_task(task_id: int, db: Session = Depends(get_db)):
    task = _get_image_task_or_404(task_id, db)
    if task.status != "paused":
        raise HTTPException(409, "只有已暂停的任务可以恢复")
    task.status = "pending"
    db.commit()
    return {"success": True, "task_id": task.id, "status": task.status}


@router.post("/tasks/{task_id}/retry")
def retry_image_task(task_id: int, db: Session = Depends(get_db)):
    task = _get_image_task_or_404(task_id, db)
    if task.task_type.startswith("model3d_"):
        raise HTTPException(409, "3D任务重试可能再次产生费用，请回到素材卡片重新提交，并重新确认本次付费调用")
    if task.status not in ("failed", "cancelled"):
        raise HTTPException(409, "只有失败或已取消的图片任务可以重试")
    task.status = "pending"
    task.error = ""
    task.result_path = ""
    task.result_url = ""
    task.finished_at = None
    db.commit()
    return {"success": True, "task_id": task.id, "status": task.status, "message": "任务已重新加入待执行队列；不会自动调用 AI。请再次手动执行并确认费用。"}


@router.post("/tasks/{task_id}/cancel")
def cancel_image_task(task_id: int, db: Session = Depends(get_db)):
    task = _get_image_task_or_404(task_id, db)
    if task.status not in ("pending", "paused", "failed"):
        raise HTTPException(409, "当前任务已开始执行或已提交第三方，不能通过本地取消接口保证中断；请在服务商控制台检查远程任务")
    task.status = "cancelled"
    task.error = task.error or "用户取消任务"
    db.commit()
    return {"success": True, "task_id": task.id, "status": task.status}


@router.get("/files/{filename}")
def get_visual_file(filename: str):
    from fastapi.responses import FileResponse
    safe_name = Path(filename).name
    allowed_roots = [ASSET_DIR.resolve(), (STORAGE_DIR / "web_ai_results").resolve()]
    path = next((root / safe_name for root in allowed_roots if (root / safe_name).is_file()), None)
    if path is None or path.resolve().parent not in allowed_roots:
        raise HTTPException(404, "图片文件不存在")
    return FileResponse(path)



class PropEvolutionInput(BaseModel):
    chapter_number: int = Field(ge=1)
    event: str = Field(min_length=2, max_length=2000)
    change_type: str = Field(default="状态变化", max_length=80)
    appearance_after: str = Field(default="", max_length=4000)
    clothing_after: str = Field(default="", max_length=4000)
    holder: str = Field(default="", max_length=300)
    confirmed: bool = False
    evidence: str = Field(default="", max_length=2000)
    source: str = Field(default="manual", max_length=40)
    confidence: float = Field(default=1.0, ge=0, le=1)


def resolve_asset_state(asset: VisualAsset, chapter_number: Optional[int] = None):
    data = asset.data if isinstance(asset.data, dict) else {}
    history = [x for x in (data.get("evolution_history") or []) if isinstance(x, dict)]
    # Legacy records without a confirmed flag are treated as previously accepted.
    history = [x for x in history if x.get("confirmed", True) is True]
    history.sort(key=lambda x: (int(x.get("chapter_number") or 0), str(x.get("recorded_at") or "")))
    applicable = [x for x in history if chapter_number is None or int(x.get("chapter_number") or 0) <= chapter_number]
    latest = applicable[-1] if applicable else {}
    return {
        "chapter_number": latest.get("chapter_number"),
        "event": latest.get("event", ""),
        "change_type": latest.get("change_type", "基础状态"),
        "appearance": latest.get("appearance_after") or asset.appearance or "",
        "clothing": latest.get("clothing_after") or asset.clothing or "",
        "holder": latest.get("holder", ""),
        "confirmed": bool(latest.get("confirmed", False)),
        "is_evolved": bool(latest),
    }


@router.post("/assets/{asset_id}/evolution")
def append_asset_evolution(asset_id: int, payload: PropEvolutionInput, db: Session = Depends(get_db)):
    asset = db.query(VisualAsset).filter(VisualAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(404, "素材不存在")
    data = dict(asset.data) if isinstance(asset.data, dict) else {}
    history = list(data.get("evolution_history") or [])
    history.append({
        "chapter_number": payload.chapter_number,
        "event": payload.event.strip(),
        "change_type": payload.change_type.strip(),
        "appearance_after": payload.appearance_after.strip(),
        "clothing_after": payload.clothing_after.strip(),
        "holder": payload.holder.strip(),
        "evidence": payload.evidence.strip(),
        "source": payload.source.strip() or "manual",
        "confidence": payload.confidence,
        "confirmed": payload.confirmed,
        "recorded_at": datetime.utcnow().isoformat(),
    })
    history.sort(key=lambda x: (int(x.get("chapter_number") or 0), str(x.get("recorded_at") or "")))
    data["evolution_history"] = history
    confirmed_history = [item for item in history if item.get("confirmed", True) is True]
    latest = confirmed_history[-1] if confirmed_history else {}
    data["current_state"] = {
        "chapter_number": latest.get("chapter_number"),
        "appearance": latest.get("appearance_after") or asset.appearance,
        "clothing": latest.get("clothing_after") or asset.clothing,
        "holder": latest.get("holder", ""),
        "change_type": latest.get("change_type", "基础状态"),
    }
    asset.data = data
    asset.updated_at = datetime.utcnow()
    db.commit()
    return {"success": True, "asset_id": asset_id, "history_count": len(history), "current_state": data["current_state"]}


@router.get("/assets/{asset_id}/evolution")
def get_asset_evolution(asset_id: int, chapter_number: Optional[int] = None, db: Session = Depends(get_db)):
    asset = db.query(VisualAsset).filter(VisualAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(404, "素材不存在")
    data = asset.data if isinstance(asset.data, dict) else {}
    history = sorted(
        [x for x in (data.get("evolution_history") or []) if isinstance(x, dict)],
        key=lambda x: (int(x.get("chapter_number") or 0), str(x.get("recorded_at") or "")),
    )
    return {
        "asset_id": asset.id,
        "asset_name": asset.name,
        "history": history,
        "effective_state": resolve_asset_state(asset, chapter_number),
        "chapter_number": chapter_number,
    }


def build_asset_prompt(asset: VisualAsset, style: str = "", chapter_number: Optional[int] = None):
    category_labels = {
        "characters": "角色", "scenes": "场景", "props": "道具",
        "weapons": "武器", "costumes": "服装", "buildings": "建筑",
        "special_items": "特殊物品",
    }
    label = category_labels.get(asset.category, "视觉资产")
    data = asset.data if isinstance(asset.data, dict) else {}
    history = [
        item for item in (data.get("evolution_history") or [])
        if isinstance(item, dict)
        and (chapter_number is None or int(item.get("chapter_number") or 0) <= chapter_number)
    ]
    history.sort(key=lambda x: (int(x.get("chapter_number") or 0), str(x.get("recorded_at") or "")))
    history = [item for item in history if item.get("confirmed", True) is True]
    history_text = "；".join(
        f"第{item.get('chapter_number', '?')}章：{item.get('change_type', '变化')}，{item.get('event', '')}，"
        f"变化后外观：{item.get('appearance_after', '')}，变化后服装：{item.get('clothing_after', '')}"
        for item in history[-12:]
    )
    effective_state = resolve_asset_state(asset, chapter_number)
    style_name = style or asset.style or "3D动漫电影风格"
    positive = (
        f"为小说视觉资产“{asset.name}”制作专业{label}设定图。"
        f"剧情时间点：{('第' + str(chapter_number) + '章') if chapter_number else '当前最新状态'}。"
        f"原文描述：{asset.description or '以已确认设定为准'}。"
        f"该剧情时间点外观结构：{effective_state['appearance'] or '严格依据原文和参考图，不擅自添加设定'}。"
        f"该剧情时间点服装/装备/表面细节：{effective_state['clothing'] or '按资产类别合理呈现'}。"
        f"当前持有人：{effective_state['holder'] or '原设定持有人或剧情明确安排'}。"
        f"气质/环境氛围：{asset.personality or '忠实原文'}。"
        f"固定一致性特征：{asset.consistency_prompt or asset.appearance}。"
        f"剧情变化记录：{history_text or '暂无已记录变化'}。"
        f"视觉风格：{style_name}。"
        "要求：电影级构图与材质表现，细节清晰，比例准确，颜色统一，"
        "不得改变已确认的身份、轮廓、结构、标志性特征，不得凭空增加配件或文字。"
    )
    negative = (
        "错误角色身份，错误物品结构，额外肢体，重复物体，结构断裂，"
        "比例失真，视角不一致，颜色漂移，服装装备变化，模糊，低分辨率，"
        "文字，字幕，水印，Logo，边框，裁切主体，背景杂乱，"
        "与原文冲突的装饰或零件。"
    )
    return {
        "asset_id": asset.id, "asset_name": asset.name, "category": asset.category,
        "style": style_name, "positive_prompt": positive, "negative_prompt": negative,
        "generation_parameters": {
            "resolution": "2K", "background": "纯净中性背景（场景类除外）",
            "consistency": "固定外观与结构", "views": ["正面", "侧面", "背面"],
            "source_novel_id": data.get("source_novel_id"),
            "source_analysis_item_id": data.get("source_analysis_item_id"),
        },
        "evolution_history": history,
        "effective_state": effective_state,
        "chapter_number": chapter_number,
    }


@router.get("/assets/{asset_id}/prompt")
def get_asset_prompt(asset_id: int, style: Optional[str] = None, chapter_number: Optional[int] = None, db: Session = Depends(get_db)):
    asset = db.query(VisualAsset).filter(VisualAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(404, "素材不存在")
    return build_asset_prompt(asset, style or "", chapter_number)


@router.post("/assets/{asset_id}/views")
def create_asset_view_tasks(asset_id: int, provider: str = "doubao", size: str = "2K", style: Optional[str] = None, db: Session = Depends(get_db)):
    asset = db.query(VisualAsset).filter(VisualAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(404, "素材不存在")
    if not provider.strip():
        raise HTTPException(400, "请指定图像服务商")
    prompt_data = build_asset_prompt(asset, style or "")
    if asset.category == "scenes":
        views = [
            ("scene_wide", "广角全景，建立空间总布局、地形、出入口和主要物体位置"),
            ("scene_eye_level", "人物视线高度的主视角，展示可行动区域和空间关系"),
            ("scene_side", "侧向机位，展示空间纵深、遮挡关系和光照方向"),
            ("scene_overview", "高位俯视布局图，保持地理结构与物体位置一致"),
        ]
    else:
        views = [
            ("view_front", "正面正交视图，完整展示主体，不裁切"),
            ("view_side", "严格90度侧面正交视图，结构与正面完全一致"),
            ("view_back", "背面正交视图，展示背部结构与细节"),
        ]
    created = []
    for task_type, view_instruction in views:
        active = db.query(ImageTask).filter(
            ImageTask.asset_id == asset.id,
            ImageTask.task_type == task_type,
            ImageTask.status.in_(("pending", "running", "submitted", "paused")),
        ).order_by(ImageTask.id.desc()).first()
        if active:
            created.append({
                "task_id": active.id,
                "task_type": task_type,
                "status": active.status,
                "existing": True,
            })
            continue
        task = ImageTask(
            asset_id=asset.id, provider=provider, task_type=task_type,
            prompt=f"{prompt_data['positive_prompt']}视角要求：{view_instruction}。负面提示词：{prompt_data['negative_prompt']}",
            size=size, status="pending",
        )
        db.add(task)
        db.flush()
        created.append({"task_id": task.id, "task_type": task_type, "status": task.status})
    db.commit()
    data = dict(asset.data) if isinstance(asset.data, dict) else {}
    data["last_view_batch"] = {
        "created_at": datetime.utcnow().isoformat(),
        "task_ids": [item["task_id"] for item in created],
        "view_types": [item["task_type"] for item in created],
        "provider": provider,
        "note": "任务已入队；每个任务仍需用户主动执行，执行时遵循网络及付费授权规则。",
    }
    asset.data = data
    db.commit()
    return {"success": True, "asset_id": asset.id, "tasks": created, "count": len(created), "message": f"本次处理 {len(created)} 个视角任务（新建 {sum(1 for item in created if not item.get('existing'))} 个，复用 {sum(1 for item in created if item.get('existing'))} 个）。尚未调用外部 AI，请在任务队列中逐项主动执行。"}



class TripoModelInput(BaseModel):
    provider: str = "tripo3d"
    model: str = "v3.1-20260211"
    texture: bool = True
    pbr: bool = True
    texture_quality: str = "detailed"
    geometry_quality: str = "standard"
    confirm_paid: bool = False


def _tripo_provider(db, provider_name, confirm_paid, check_paid=True):
    runtime = get_runtime(db)
    if not runtime["network_enabled"]:
        raise HTTPException(403, "全局网络调用开关已关闭")
    provider = db.query(AIProviderConfig).filter(AIProviderConfig.provider == provider_name).first()
    if not provider or not provider.enabled:
        raise HTTPException(400, f"{provider_name} 服务商尚未配置或启用")
    if getattr(provider, "purpose", "image") not in ("image", "3d", "media"):
        raise HTTPException(400, "该服务商用途必须设置为图像、3D 或媒体")
    if not provider.encrypted_api_key:
        raise HTTPException(400, "请先保存 3D 服务商 API Key")
    if bool(getattr(provider, "paid", True)) and check_paid:
        if not runtime["paid_api_allowed"]:
            raise HTTPException(403, "3D 模型生成属于付费 API，请先开启付费 API 总授权")
        if not confirm_paid:
            raise HTTPException(403, "本次 3D 模型生成可能产生费用，必须明确确认")
    base = (provider.base_url or "https://openapi.tripo3d.ai/v3").rstrip("/")
    if not base.startswith("https://"):
        raise HTTPException(400, "3D 服务商必须使用 HTTPS Base URL")
    return provider, decrypt_key(provider.encrypted_api_key), base


def _tripo_json(url, api_key, body=None, method="POST", timeout=300):
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("code", 0) not in (0, None):
        raise RuntimeError("Tripo API 错误：" + str(result.get("message") or result.get("code")))
    return result


def _tripo_upload_image(path, api_key, base):
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("三视图图片不存在或超过 Tripo 20 MB 上传限制")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    if mime not in ("image/png", "image/jpeg", "image/webp"):
        raise ValueError("Tripo 仅接受 PNG、JPEG、WebP 参考图片")
    boundary = "----AIComicStudio" + os.urandom(12).hex()
    filename = path.name.replace('"', "")
    content = path.read_bytes()
    body = (
        ("--" + boundary + "\r\n").encode()
        + f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
        + f"Content-Type: {mime}\r\n\r\n".encode()
        + content + ("\r\n--" + boundary + "--\r\n").encode()
    )
    request = urllib.request.Request(
        base + "/files", data=body,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "multipart/form-data; boundary=" + boundary},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("code", 0) != 0:
        raise RuntimeError("Tripo 图片上传失败：" + str(result.get("message") or result.get("code")))
    token = (result.get("data") or {}).get("file_token")
    if not token:
        raise RuntimeError("Tripo 图片上传未返回 file_token")
    return token


def _safe_visual_view_path(raw_path):
    candidate = Path(raw_path).resolve()
    allowed = [ASSET_DIR.resolve(), REFERENCE_DIR.resolve()]
    if not any(candidate == root or root in candidate.parents for root in allowed):
        raise ValueError("三视图文件路径不在视觉素材目录内")
    if not candidate.is_file():
        raise ValueError("三视图图片文件不存在")
    return candidate


@router.post("/assets/{asset_id}/model3d/tripo")
def create_tripo_3d_model(asset_id: int, payload: TripoModelInput, db: Session = Depends(get_db)):
    asset = db.query(VisualAsset).filter(VisualAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(404, "素材不存在")
    if not asset.confirmed:
        raise HTTPException(409, "请先确认人物/场景/道具设定，再生成三维模型")

    active = db.query(ImageTask).filter(
        ImageTask.asset_id == asset_id,
        ImageTask.task_type == "model3d_multiview",
        ImageTask.status.in_(("submitting", "submitted", "running", "interrupted_unknown")),
    ).order_by(ImageTask.id.desc()).first()
    if active:
        action = tripo_task_action(active)
        if action == "review":
            raise HTTPException(
                409,
                f"该素材已有 3D 任务（本地任务 #{active.id}）处于提交中断或结果待核实状态。"
                "请先核对 Tripo 控制台；确认没有远程任务后，再从统一任务中心人工释放提交锁，"
                "不得直接重复提交。",
            )
        if action == "reuse":
            return {
                "success": True, "task_id": active.id, "status": active.status,
                "message": "该素材已有正在处理的 3D 任务，已返回现有任务，避免重复扣费。",
            }

    provider, api_key, base = _tripo_provider(db, payload.provider, payload.confirm_paid)
    data = asset.data if isinstance(asset.data, dict) else {}
    views = data.get("generated_views") if isinstance(data.get("generated_views"), dict) else {}
    front = views.get("view_front") or views.get("scene_wide") or {}
    side = views.get("view_side") or views.get("scene_side") or {}
    back = views.get("view_back") or views.get("scene_overview") or {}
    front_path = front.get("path") if isinstance(front, dict) else None
    side_path = side.get("path") if isinstance(side, dict) else None
    back_path = back.get("path") if isinstance(back, dict) else None
    if not front_path and asset.image_path:
        front_path = asset.image_path
    if not front_path:
        raise HTTPException(409, "缺少正面参考图，请先生成并确认正面视图")
    if not side_path and not back_path:
        raise HTTPException(409, "至少还需要侧面或背面图片；请先生成完整三视图")

    # A stable per-asset lease serializes simultaneous submissions even when
    # two requests arrive before either one has inserted an ImageTask row.
    # The lease also consumes the shared global concurrency budget.
    acquire_task_lease(db, "image3d_asset", asset.id, priority=5)

    task = None
    request_started = False
    try:
        # Re-check after acquiring the per-asset lease to close the race between
        # the first lookup and the actual remote submission.
        active = db.query(ImageTask).filter(
            ImageTask.asset_id == asset_id,
            ImageTask.task_type == "model3d_multiview",
            ImageTask.status.in_(("submitting", "submitted", "running", "interrupted_unknown")),
        ).order_by(ImageTask.id.desc()).first()
        if active:
            action = tripo_task_action(active)
            release_task_lease(db, "image3d_asset", asset.id, True)
            if action == "review":
                raise HTTPException(409, "已有 3D 提交结果待人工核实；为避免重复扣费，已阻止再次提交。")
            if action == "reuse":
                return {
                    "success": True, "task_id": active.id, "status": active.status,
                    "message": "已返回现有 3D 任务，避免重复提交。",
                }

        # Persist a local placeholder BEFORE any remote submission call. If
        # the desktop app exits or the DB connection drops, this record survives.
        task = ImageTask(
            asset_id=asset.id, provider=payload.provider, model=payload.model,
            task_type="model3d_multiview", prompt=f"多视图生成 3D 模型：{asset.name}",
            size="GLB", status="submitting",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        asset_data = dict(asset.data) if isinstance(asset.data, dict) else {}
        asset_data["model3d_task"] = {
            "task_id": task.id, "provider": payload.provider, "model": payload.model,
            "status": "submitting", "submitted_at": datetime.utcnow().isoformat(),
            "format": "GLB",
        }
        asset.data = asset_data
        asset.updated_at = datetime.utcnow()
        db.commit()

        front_file = _safe_visual_view_path(front_path)
        tokens = {"front": _tripo_upload_image(front_file, api_key, base)}
        if side_path:
            tokens["right"] = _tripo_upload_image(_safe_visual_view_path(side_path), api_key, base)
        if back_path:
            tokens["back"] = _tripo_upload_image(_safe_visual_view_path(back_path), api_key, base)
        inputs = [{view: token} for view, token in tokens.items()]
        body = {
            "inputs": inputs,
            "model": payload.model,
            "texture": payload.texture,
            "pbr": payload.pbr if payload.texture else False,
            "texture_quality": payload.texture_quality,
            "geometry_quality": payload.geometry_quality,
        }

        # From this point on, an exception can mean the provider accepted and
        # billed the generation even if the response was lost or malformed.
        request_started = True
        result = _tripo_json(base + "/generation/multiview-to-model", api_key, body)
        remote_id = (result.get("data") or {}).get("task_id")
        if not remote_id:
            raise RuntimeError("Tripo 已收到提交请求，但未返回可核实的远程任务编号")

        task.status = "submitted"
        task.result_url = "tripo-task:" + str(remote_id)
        task.error = ""
        db.add(AIUsageLog(
            provider=payload.provider, model=payload.model, task="visual_multiview_to_3d",
            status="submitted", detail=f"素材：{asset.name}；本地任务：{task.id}；远程任务：{remote_id}；输入视角：{','.join(tokens.keys())}",
        ))
        asset_data = dict(asset.data) if isinstance(asset.data, dict) else {}
        asset_data["model3d_task"] = {
            "task_id": task.id, "provider": payload.provider, "model": payload.model,
            "status": "submitted", "remote_task_id": str(remote_id),
            "submitted_at": datetime.utcnow().isoformat(),
            "input_views": list(tokens.keys()), "format": "GLB",
        }
        asset.data = asset_data
        asset.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(task)
        release_task_lease(db, "image3d_asset", asset.id, True)
        return {
            "success": True, "task_id": task.id, "remote_task_id": str(remote_id),
            "status": "submitted", "input_views": list(tokens.keys()),
            "message": "已保存本地任务记录并提交真实 3D 模型生成任务；请稍后查询任务。",
        }

    except HTTPException as exc:
        db.rollback()
        if task is not None:
            task_id = task.id
            task = db.query(ImageTask).filter(ImageTask.id == task_id).first()
            current_asset = db.query(VisualAsset).filter(VisualAsset.id == asset.id).first()
            if task:
                mark_tripo_submission_failure(task, current_asset, str(exc.detail), request_started)
                db.add(AIUsageLog(
                    provider=payload.provider, model=payload.model,
                    task="visual_multiview_to_3d",
                    status="interrupted_unknown" if request_started else "failed",
                    detail=str(exc.detail)[:1600],
                ))
                db.commit()
        release_task_lease(db, "image3d_asset", asset.id, False, str(exc.detail)[:2000])
        raise
    except Exception as exc:
        db.rollback()
        if task is not None:
            task_id = task.id
            task = db.query(ImageTask).filter(ImageTask.id == task_id).first()
            current_asset = db.query(VisualAsset).filter(VisualAsset.id == asset.id).first()
            if task:
                mark_tripo_submission_failure(task, current_asset, str(exc)[:1600], request_started)
                db.add(AIUsageLog(
                    provider=payload.provider, model=payload.model,
                    task="visual_multiview_to_3d",
                    status="interrupted_unknown" if request_started else "failed",
                    detail=str(exc)[:1600],
                ))
                db.commit()
        release_task_lease(db, "image3d_asset", asset.id, False, str(exc)[:2000])
        raise HTTPException(502, "提交 Tripo 3D 任务失败：" + str(exc)[:1600]) from exc


@router.post("/tasks/{task_id}/refresh-model3d")
def refresh_tripo_3d_model(task_id: int, confirm_paid: bool = False, db: Session = Depends(get_db)):
    task = db.query(ImageTask).filter(ImageTask.id == task_id).first()
    if not task or not task.task_type.startswith("model3d_"):
        raise HTTPException(404, "3D 模型任务不存在")
    if task.status == "completed":
        return {"success": True, "task_id": task.id, "status": task.status, "result_path": task.result_path}
    remote_id = (task.result_url or "").removeprefix("tripo-task:")
    if not remote_id or remote_id == task.result_url:
        raise HTTPException(409, "3D 任务缺少远程任务编号")
    provider, api_key, base = _tripo_provider(db, task.provider, confirm_paid, check_paid=False)
    try:
        result = _tripo_json(base + "/tasks/" + remote_id, api_key, method="GET")
        remote = result.get("data") or {}
        status = str(remote.get("status") or "running").lower()
        asset = db.query(VisualAsset).filter(VisualAsset.id == task.asset_id).first()
        asset_data = dict(asset.data) if asset and isinstance(asset.data, dict) else {}
        model_task_data = dict(asset_data.get("model3d_task") or {})
        model_task_data["status"] = status
        model_task_data["progress"] = remote.get("progress")
        model_task_data["last_checked_at"] = datetime.utcnow().isoformat()
        if status in ("failed", "cancelled", "canceled"):
            task.status = "failed"
            task.error = str(remote.get("message") or remote.get("error") or "Tripo 3D 任务失败")[:2000]
            model_task_data["error"] = task.error
        elif status == "success":
            output = remote.get("output") or {}
            model_url = output.get("model_url")
            if not model_url:
                raise RuntimeError("Tripo 已完成，但没有返回 model_url")
            filename = f"visual_asset_{task.asset_id}_task_{task.id}.glb"
            destination = ASSET_DIR / filename
            http_download(model_url, destination, max_bytes=500 * 1024 * 1024)
            with destination.open("rb") as model_file:
                if model_file.read(4) != b"glTF":
                    destination.unlink(missing_ok=True)
                    raise RuntimeError("服务商返回文件不是有效的 GLB 模型")
            task.result_path = str(destination)
            task.result_url = model_url
            task.status = "completed"
            task.finished_at = datetime.utcnow()
            task.error = ""
            if asset:
                data = dict(asset.data) if isinstance(asset.data, dict) else {}
                data["model3d"] = {
                    "task_id": task.id, "provider": task.provider, "model": model_task_data.get("model", ""),
                    "format": "GLB", "path": str(destination), "downloaded_at": datetime.utcnow().isoformat(),
                    "preview_url": output.get("rendered_image_url"),
                    "credits_consumed": remote.get("credits_consumed"),
                }
                model_task_data["status"] = "completed"
                model_task_data["progress"] = 100
                model_task_data["completed_at"] = datetime.utcnow().isoformat()
                data["model3d_task"] = model_task_data
                asset.data = data
                asset.updated_at = datetime.utcnow()
        else:
            task.status = "running"
        if asset and task.status != "completed":
            asset_data["model3d_task"] = model_task_data
            asset.data = asset_data
            asset.updated_at = datetime.utcnow()
        db.commit()
        return {
            "success": task.status != "failed", "task_id": task.id, "status": task.status,
            "progress": remote.get("progress"), "result_path": task.result_path,
            "error": task.error or "",
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1600]
        raise HTTPException(502, f"Tripo 查询失败 HTTP {exc.code}：{detail}") from exc
    except Exception as exc:
        raise HTTPException(502, "查询/下载 3D 模型失败：" + str(exc)[:1600]) from exc


@router.get("/models3d/{filename}")
def get_3d_model(filename: str):
    from fastapi.responses import FileResponse
    path = (ASSET_DIR / Path(filename).name).resolve()
    if not path.is_file() or ASSET_DIR.resolve() not in path.parents or path.suffix.lower() != ".glb":
        raise HTTPException(404, "GLB 模型不存在")
    return FileResponse(path, media_type="model/gltf-binary", filename=path.name)


@router.get("/capabilities")
def visual_capabilities():
    return {
        "image_reference_generation": {"available": True, "formats": ["PNG", "JPG", "WEBP"], "note": "生成的是二维视觉参考图，不是可编辑三维网格。"},
        "three_view_generation": {"available": True, "views": ["front", "side", "back"], "scene_views": ["wide", "eye_level", "side", "overview"]},
        "editable_3d_model": {"available": True, "provider": "tripo3d", "formats": ["GLB"], "modes": ["multiview-to-model"], "note": "需用户自行配置 Tripo API Key；调用受网络开关、付费授权和逐次确认控制。"},
        "asset_consistency": {"available": True, "features": ["stable_asset_id", "confirmed_lock", "chapter_evolution_history", "positive_negative_prompt", "source_novel_link"]},
        "execution_policy": {"requires_network_switch": True, "paid_calls_require_global_authorization_and_per_task_confirmation": True},
    }

@router.post("/assets/{asset_id}/confirm")
def confirm_asset(asset_id: int, db: Session = Depends(get_db)):
    item = db.query(VisualAsset).filter(VisualAsset.id == asset_id).first()
    if not item:
        raise HTTPException(404, "素材不存在")
    item.confirmed = True
    db.commit()
    return {"success": True, "confirmed": True}

