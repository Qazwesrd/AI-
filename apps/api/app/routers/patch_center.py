from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/patch-center", tags=["patch-center"])
MAX_PATCH_BYTES = 512 * 1024


def _patch_root() -> Path:
    data_root = Path(os.environ.get("APP_DATA_DIR") or (Path.home() / ".ai-comic-studio"))
    root = (data_root / "patch-center" / "packages").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


class PatchPackageInput(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    target_version: str = Field(default="current", max_length=40)
    patch_format: Literal["unified-diff", "source-code", "json-patch"] = "unified-diff"
    target_path: str = Field(default="", max_length=240)
    code: str = Field(min_length=1, max_length=MAX_PATCH_BYTES)


ALLOWED_PATCH_ROOTS = ("apps/web/src/", "apps/api/app/", "apps/desktop/")


def _validate_target_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or re.match(r"^[a-zA-Z]:", normalized):
        raise HTTPException(status_code=422, detail="补丁目标必须是项目内的相对文件路径。")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts) or not normalized.endswith((".py", ".ts", ".tsx", ".js", ".cjs", ".json", ".css", ".md")):
        raise HTTPException(status_code=422, detail="补丁目标路径包含不允许的目录或文件类型。")
    if not normalized.startswith(ALLOWED_PATCH_ROOTS):
        raise HTTPException(status_code=422, detail="补丁只能修改应用源码白名单目录。")
    return normalized


def _diff_paths(diff_text: str) -> list[str]:
    paths = []
    for line in diff_text.splitlines():
        if line.startswith(("--- ", "+++ ")):
            raw = line[4:].split("\\t", 1)[0].strip()
            if raw == "/dev/null":
                continue
            if raw.startswith(("a/", "b/")):
                raw = raw[2:]
            paths.append(_validate_target_path(raw))
    if not paths or len(paths) % 2:
        raise HTTPException(status_code=422, detail="统一差异补丁文件头不完整。")
    return sorted(set(paths))


def _safe_package_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-f0-9]{32}", value))


@router.get("/status")
def patch_center_status():
    root = _patch_root()
    packages = sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    return {
        "status": "ready",
        "storage": str(root),
        "package_count": len(packages),
        "max_patch_bytes": MAX_PATCH_BYTES,
        "apply_supported": False,
        "message": "补丁包可在本机生成、校验和暂存；自动应用器尚未启用。",
    }


@router.get("/packages")
def list_patch_packages():
    root = _patch_root()
    result = []
    for file in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            item = json.loads(file.read_text(encoding="utf-8"))
            result.append({key: item.get(key) for key in (
                "id", "title", "description", "target_version", "patch_format",
                "created_at", "sha256", "size_bytes", "status"
            )})
        except (OSError, json.JSONDecodeError):
            continue
    return {"packages": result}


@router.post("/packages")
def create_patch_package(payload: PatchPackageInput):
    code_bytes = payload.code.encode("utf-8")
    if len(code_bytes) > MAX_PATCH_BYTES:
        raise HTTPException(status_code=413, detail="单个补丁最大支持 512 KiB；大型模型和媒体资源不能放入代码补丁。")

    changed_files = []
    if payload.patch_format == "unified-diff":
        if not payload.code.startswith("--- ") or "\n+++ " not in payload.code:
            raise HTTPException(status_code=422, detail="统一差异补丁格式不正确：需要包含 --- 与 +++ 文件头。")
        forbidden = ("\nGIT binary patch", "\nBinary files ", "\nrename from ", "\nrename to ")
        if any(token in payload.code for token in forbidden):
            raise HTTPException(status_code=422, detail="不接受二进制或重命名补丁；请仅提交文本代码差异。")
        changed_files = _diff_paths(payload.code)
    elif payload.patch_format == "source-code":
        changed_files = [_validate_target_path(payload.target_path)]
    elif payload.patch_format == "json-patch":
        changed_files = [_validate_target_path(payload.target_path)]
        try:
            parsed = json.loads(payload.code)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"JSON 补丁格式错误：{exc.msg}") from exc
        if not isinstance(parsed, (dict, list)):
            raise HTTPException(status_code=422, detail="JSON 补丁必须是对象或数组。")

    package_id = uuid.uuid4().hex
    sha256 = hashlib.sha256(code_bytes).hexdigest()
    record = {
        "id": package_id,
        "title": payload.title.strip(),
        "description": payload.description.strip(),
        "target_version": payload.target_version.strip(),
        "patch_format": payload.patch_format,
        "target_path": payload.target_path,
        "changed_files": changed_files,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sha256": sha256,
        "size_bytes": len(code_bytes),
        "changed_files": changed_files,
        "status": "staged",
        "code": payload.code,
        "apply_supported": False,
    }
    root = _patch_root()
    destination = root / f"{package_id}.json"
    temporary = root / f".{package_id}.tmp"
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return {
        "id": package_id,
        "title": record["title"],
        "sha256": sha256,
        "size_bytes": len(code_bytes),
        "status": "staged",
        "message": "补丁包已生成并暂存；当前版本不会自动执行或覆盖程序文件。",
    }


@router.get("/packages/{package_id}")
def get_patch_package(package_id: str):
    if not _safe_package_id(package_id):
        raise HTTPException(status_code=400, detail="补丁编号无效。")
    path = _patch_root() / f"{package_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="未找到该补丁包。")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="补丁包读取失败。") from exc
