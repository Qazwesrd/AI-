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
from app.models.novel import Novel, AnalysisItem
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
    if getattr(provider, "purpose", "text") != "image":
        raise HTTPException(400, "所选服务商用途不是图像生成，请在 AI 管理中心选择图像服务商")
    if not provider.encrypted_api_key:
        raise HTTPException(400, "服务商尚未保存 API Key")
    if bool(getattr(provider, "paid", True)):
        if not runtime["paid_api_allowed"]:
            raise HTTPException(403, "该图像模型属于付费服务，请先开启付费API总授权")
        if not confirm_paid:
            raise HTTPException(403, "本次图像生成可能产生费用，请明确确认后再执行")

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



class PropEvolutionInput(BaseModel):
    chapter_number: int = Field(ge=1)
    event: str = Field(min_length=2, max_length=2000)
    change_type: str = Field(default="状态变化", max_length=80)
    appearance_after: str = Field(default="", max_length=4000)
    holder: str = Field(default="", max_length=300)
    confirmed: bool = False


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
        "holder": payload.holder.strip(),
        "confirmed": payload.confirmed,
        "recorded_at": datetime.utcnow().isoformat(),
    })
    data["evolution_history"] = history
    data["current_state"] = {
        "chapter_number": payload.chapter_number,
        "appearance": payload.appearance_after.strip() or asset.appearance,
        "holder": payload.holder.strip(),
        "change_type": payload.change_type.strip(),
    }
    asset.data = data
    asset.updated_at = datetime.utcnow()
    db.commit()
    return {"success": True, "asset_id": asset_id, "history_count": len(history), "current_state": data["current_state"]}


def build_asset_prompt(asset: VisualAsset, style: str = ""):
    category_labels = {
        "characters": "角色", "scenes": "场景", "props": "道具",
        "weapons": "武器", "costumes": "服装", "buildings": "建筑",
        "special_items": "特殊物品",
    }
    label = category_labels.get(asset.category, "视觉资产")
    data = asset.data if isinstance(asset.data, dict) else {}
    history = data.get("evolution_history") or []
    history_text = "；".join(
        f"第{item.get('chapter_number', '?')}章：{item.get('change_type', '变化')}，{item.get('event', '')}，变化后外观：{item.get('appearance_after', '')}"
        for item in history[-12:] if isinstance(item, dict)
    )
    style_name = style or asset.style or "3D动漫电影风格"
    positive = (
        f"为小说视觉资产“{asset.name}”制作专业{label}设定图。"
        f"原文描述：{asset.description or '以已确认设定为准'}。"
        f"外观结构：{asset.appearance or '严格依据原文和参考图，不擅自添加设定'}。"
        f"服装/装备/表面细节：{asset.clothing or '按资产类别合理呈现'}。"
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
    }


@router.get("/assets/{asset_id}/prompt")
def get_asset_prompt(asset_id: int, style: Optional[str] = None, db: Session = Depends(get_db)):
    asset = db.query(VisualAsset).filter(VisualAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(404, "素材不存在")
    return build_asset_prompt(asset, style or "")


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
    return {"success": True, "asset_id": asset.id, "tasks": created, "count": len(created), "message": f"已创建 {len(created)} 个视角图片任务，尚未调用外部 AI。请在任务队列中逐项主动执行。"}


@router.get("/capabilities")
def visual_capabilities():
    return {
        "image_reference_generation": {"available": True, "formats": ["PNG", "JPG", "WEBP"], "note": "生成的是二维视觉参考图，不是可编辑三维网格。"},
        "three_view_generation": {"available": True, "views": ["front", "side", "back"], "scene_views": ["wide", "eye_level", "side", "overview"]},
        "editable_3d_model": {"available": False, "formats": ["GLB", "GLTF"], "note": "当前仓库尚未配置可验证的图像/文本转3D服务商适配器；不会伪造模型文件。"},
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

