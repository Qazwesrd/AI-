"""Local click-through acceptance for the bundled Stable Diffusion 1.5 checkpoint."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

OPENRAIL_MODEL_FILENAME = "v1-5-pruned-emaonly-fp16.safetensors"
OPENRAIL_TERMS_VERSION = "2022-08-22"
OPENRAIL_LICENSE_URL = "https://huggingface.co/spaces/CompVis/stable-diffusion-license"


def acceptance_path() -> Path:
    configured = os.environ.get("APP_DATA_DIR")
    root = Path(configured).expanduser() if configured else Path.home() / "AIComicStudio" / "data"
    return root / "legal" / "creativeml-openrail-m-acceptance.json"


def is_openrail_checkpoint(checkpoint: str) -> bool:
    filename = str(checkpoint or "").replace("\\", "/").rsplit("/", 1)[-1]
    return filename.casefold() == OPENRAIL_MODEL_FILENAME.casefold()


def license_path() -> Path:
    configured = os.environ.get("APP_DATA_DIR")
    root = Path(configured).expanduser() if configured else Path.home() / "AIComicStudio" / "data"
    return root / "engines" / "ComfyUI" / "licenses" / "Stable-Diffusion-CreativeML-OpenRAIL-M-LICENSE.txt"


def get_openrail_license_text() -> str:
    path = license_path()
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(503, "未找到本机随模型安装的 CreativeML Open RAIL-M 完整许可证文件。请修复本地 ComfyUI 引擎安装。") from exc
    if "CreativeML Open RAIL-M" not in content or "Attachment A" not in content:
        raise HTTPException(503, "本机 CreativeML Open RAIL-M 许可证文件不完整，暂不能确认。")
    return content


def get_openrail_acceptance() -> dict:
    path = acceptance_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = {}
    accepted = (
        isinstance(payload, dict)
        and payload.get("accepted") is True
        and payload.get("terms_version") == OPENRAIL_TERMS_VERSION
        and isinstance(payload.get("accepted_at"), str)
    )
    return {
        "accepted": accepted,
        "terms_version": OPENRAIL_TERMS_VERSION,
        "accepted_at": payload.get("accepted_at") if accepted else None,
        "license_url": OPENRAIL_LICENSE_URL,
        "model_filename": OPENRAIL_MODEL_FILENAME,
    }


def set_openrail_acceptance(accepted: bool, terms_version: str) -> dict:
    if terms_version != OPENRAIL_TERMS_VERSION:
        raise HTTPException(409, "许可证条款版本已更新，请重新阅读后再确认。")
    path = acceptance_path()
    if not accepted:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise HTTPException(500, "撤销许可证确认记录失败。") from exc
        return get_openrail_acceptance()

    payload = {
        "accepted": True,
        "terms_version": OPENRAIL_TERMS_VERSION,
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "license_url": OPENRAIL_LICENSE_URL,
        "model_filename": OPENRAIL_MODEL_FILENAME,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        raise HTTPException(500, "保存许可证确认记录失败。") from exc
    return get_openrail_acceptance()


def require_openrail_acceptance(checkpoint: str) -> None:
    if is_openrail_checkpoint(checkpoint) and not get_openrail_acceptance()["accepted"]:
        raise HTTPException(
            403,
            "Stable Diffusion 1.5 使用前必须先阅读 CreativeML Open RAIL-M 完整许可证及附件 A，并在软件中确认接受其使用限制。",
        )
