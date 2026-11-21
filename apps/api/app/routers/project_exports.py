"""Portable, project-scoped archive export.

Exports only the selected story project and its explicitly linked source/media
records. Files are copied only from the managed local storage root; secrets,
provider credentials and workspace-level database files are never included.
"""
import hashlib
import json
import re
import shutil
import uuid
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import DATA_ROOT, get_db
from app.models.media import MediaFile, MediaTask
from app.models.novel import Chapter, Novel
from app.models.story import (
    EpisodeEditTimeline,
    EpisodePlan,
    StoryAssetLink,
    StoryboardMediaLink,
    StoryboardMediaRevision,
    StoryboardShot,
    StoryProject,
)
from app.models.visual import ImageTask, VisualAsset
from app.services.media_service import safe_storage_file

router = APIRouter(prefix="/api/story/projects", tags=["项目归档导出"])

EXPORT_DIR = DATA_ROOT / "project_exports"
MAX_PROJECT_EXPORT_BYTES = 50 * 1024**3
MAX_PROJECT_EXPORT_FILES = 20_000
MAX_PROJECT_DATA_JSON_BYTES = 512 * 1024**2
EXPORT_DISK_SAFETY_MARGIN = 512 * 1024**2


_SENSITIVE_KEY_PARTS = ("api_key", "secret", "password", "credential", "token", "authorization")


def _json_value(value: Any, key: str = ""):
    normalized_key = key.lower()
    if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        return None
    if normalized_key == "result_url":
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(child_key): _json_value(item, str(child_key)) for child_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item, key) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _row_dict(row):
    return {
        column.key: _json_value(getattr(row, column.key), column.key)
        for column in row.__mapper__.column_attrs
    }


def _looks_like_path_key(key: str) -> bool:
    normalized = key.lower()
    return (
        normalized in {"path", "paths"}
        or normalized.endswith("_path")
        or normalized.endswith("_paths")
        or normalized.endswith("filepath")
    )


def _walk_path_values(value, key=""):
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from _walk_path_values(child, str(child_key))
    elif isinstance(value, list):
        for child in value:
            yield from _walk_path_values(child, key)
    elif isinstance(value, str) and _looks_like_path_key(key) and value.strip():
        yield value.strip()


def _safe_local_file(raw_path: str):
    try:
        path = safe_storage_file(raw_path)
    except (ValueError, FileNotFoundError, OSError):
        return None
    if path.is_symlink() or not path.is_file():
        return None
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _rewrite_paths(value, path_map, key=""):
    normalized_key = key.lower()
    if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS) or normalized_key == "result_url":
        return None
    if isinstance(value, dict):
        return {
            str(child_key): _rewrite_paths(child, path_map, str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, list):
        return [_rewrite_paths(child, path_map, key) for child in value]
    if isinstance(value, str) and _looks_like_path_key(key):
        return path_map.get(value)
    return _json_value(value)


def _safe_archive_name(filename: str) -> str:
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", name).strip("._")
    return (name or "asset.bin")[:180]


def _collect_project_records(db: Session, project: StoryProject):
    novel = db.query(Novel).filter(Novel.id == project.novel_id).first()
    episodes = db.query(EpisodePlan).filter(
        EpisodePlan.project_id == project.id
    ).order_by(EpisodePlan.sort_order, EpisodePlan.episode_number).all()
    episode_ids = [item.id for item in episodes]
    shots = db.query(StoryboardShot).filter(
        StoryboardShot.project_id == project.id
    ).order_by(StoryboardShot.episode_id, StoryboardShot.sort_order, StoryboardShot.shot_number).all()
    asset_links = db.query(StoryAssetLink).filter(
        StoryAssetLink.project_id == project.id
    ).all()
    media_links = db.query(StoryboardMediaLink).filter(
        StoryboardMediaLink.project_id == project.id
    ).all()
    media_revisions = db.query(StoryboardMediaRevision).filter(
        StoryboardMediaRevision.shot_id.in_([shot.id for shot in shots] or [-1])
    ).all()
    timelines = db.query(EpisodeEditTimeline).filter(
        EpisodeEditTimeline.episode_id.in_(episode_ids or [-1])
    ).all()

    asset_ids = {link.asset_id for link in asset_links}
    for shot in shots:
        asset_ids.update(int(item) for item in (shot.character_ids or []) if str(item).isdigit())
        asset_ids.update(int(item) for item in (shot.prop_ids or []) if str(item).isdigit())
        if shot.scene_asset_id:
            asset_ids.add(int(shot.scene_asset_id))
    visual_assets = db.query(VisualAsset).filter(
        VisualAsset.id.in_(sorted(asset_ids) or [-1])
    ).all()

    media_ids = {link.media_file_id for link in media_links}
    media_ids.update(item.media_file_id for item in media_revisions)
    media_files = db.query(MediaFile).filter(
        MediaFile.id.in_(sorted(media_ids) or [-1])
    ).all()
    shot_ids = {item.id for item in shots}
    storyboard_tasks = db.query(MediaTask).filter(
        MediaTask.task_type == "storyboard_video"
    ).all()
    media_tasks = []
    for task in storyboard_tasks:
        metadata = task.request_data if isinstance(task.request_data, dict) else {}
        try:
            task_episode_id = int(metadata.get("episode_id") or 0)
            task_shot_id = int(metadata.get("shot_id") or 0)
        except (TypeError, ValueError):
            continue
        if task_episode_id in set(episode_ids) and task_shot_id in shot_ids:
            media_tasks.append(task)
    image_tasks = db.query(ImageTask).filter(
        ImageTask.asset_id.in_(sorted(asset_ids) or [-1])
    ).all()
    chapters = db.query(Chapter).filter(
        Chapter.novel_id == project.novel_id
    ).order_by(Chapter.chapter_number).all() if novel else []

    return {
        "novel": novel,
        "chapters": chapters,
        "episodes": episodes,
        "storyboard_shots": shots,
        "story_asset_links": asset_links,
        "visual_assets": visual_assets,
        "storyboard_media_links": media_links,
        "storyboard_media_revisions": media_revisions,
        "media_files": media_files,
        "media_tasks": media_tasks,
        "episode_timelines": timelines,
        "image_tasks": image_tasks,
    }


def build_project_archive(project_id: int, db: Session, export_dir: Path | None = None):
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    if not project:
        raise LookupError("剧本项目不存在")

    records = _collect_project_records(db, project)
    target_dir = Path(export_dir) if export_dir else EXPORT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    all_path_values = []
    for row in records["visual_assets"]:
        all_path_values.extend([row.image_path or "", row.reference_path or ""])
        all_path_values.extend(_walk_path_values(row.data or {}))
    for row in records["media_files"]:
        all_path_values.append(row.file_path or "")
        all_path_values.extend(_walk_path_values(row.metadata_json or {}))
    for row in records["media_tasks"]:
        all_path_values.append(row.result_path or "")
        all_path_values.extend(_walk_path_values(row.request_data or {}))
        all_path_values.extend(_walk_path_values(row.result_data or {}))
    for row in records["image_tasks"]:
        all_path_values.append(row.result_path or "")
    for row in records["episode_timelines"]:
        all_path_values.extend(_walk_path_values(row.edit_data or {}))

    unique_paths = list(dict.fromkeys(item for item in all_path_values if item))
    if len(unique_paths) > MAX_PROJECT_EXPORT_FILES:
        raise ValueError("项目关联文件数量超过安全上限，无法创建归档")

    file_entries = []
    path_map = {}
    missing_files = []
    total_bytes = 0
    for raw_path in unique_paths:
        path = _safe_local_file(raw_path)
        if path is None:
            missing_files.append({"name": Path(raw_path).name[:180], "reason": "文件不存在或不属于本地素材目录"})
            continue
        size = path.stat().st_size
        total_bytes += size
        if total_bytes > MAX_PROJECT_EXPORT_BYTES:
            raise ValueError("项目关联素材总量超过 50 GiB 安全上限")
        archive_name = f"files/{len(file_entries) + 1:05d}-{_safe_archive_name(path.name)}"
        digest = _sha256_file(path)
        path_map[raw_path] = archive_name
        file_entries.append({
            "source_path": raw_path,
            "archive_path": archive_name,
            "size_bytes": size,
            "sha256": digest,
        })

    usage = shutil.disk_usage(target_dir)
    required = total_bytes + EXPORT_DISK_SAFETY_MARGIN
    if usage.free < required:
        raise OSError(
            f"归档磁盘空间不足：需要素材大小加 512 MiB 余量，共 {required} 字节；当前可用 {usage.free} 字节"
        )

    project_data = {
        "format": "ai-comic-studio-project",
        "format_version": 1,
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "project": _rewrite_paths(_row_dict(project), path_map),
        "records": {},
    }
    if records["novel"]:
        project_data["records"]["novel"] = _row_dict(records["novel"])
    for key, rows in records.items():
        if key == "novel":
            continue
        serialized = [_row_dict(row) for row in rows]
        project_data["records"][key] = [
            _rewrite_paths(row, path_map) for row in serialized
        ]

    project_data_bytes = json.dumps(project_data, ensure_ascii=False, indent=2).encode("utf-8")
    if len(project_data_bytes) > MAX_PROJECT_DATA_JSON_BYTES:
        raise ValueError("项目文字和结构化数据超过 512 MiB 安全上限，请先拆分超大型项目再归档")

    filename = f"project-{project.id}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.zip"
    destination = target_dir / filename
    temporary = target_dir / (filename + ".tmp")
    manifest = {
        "application": "AI漫剧制作大师",
        "archive_type": "single-story-project",
        "format_version": 1,
        "project_id": project.id,
        "project_name": project.name,
        "created_at": project_data["exported_at"],
        "file_count": len(file_entries),
        "total_file_bytes": total_bytes,
        "missing_file_count": len(missing_files),
        "project_data_size_bytes": len(project_data_bytes),
        "project_data_sha256": hashlib.sha256(project_data_bytes).hexdigest(),
        "missing_files": missing_files,
        "files": [
            {key: value for key, value in item.items() if key != "source_path"}
            for item in file_entries
        ],
        "security": {
            "provider_credentials_included": False,
            "local_encryption_keys_included": False,
            "workspace_database_included": False,
            "only_managed_storage_files_included": True,
        },
    }

    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
            archive.writestr(
                "project-data.json",
                project_data_bytes,
                compress_type=zipfile.ZIP_DEFLATED,
            )
            for item in file_entries:
                archive.write(
                    item["source_path"],
                    arcname=item["archive_path"],
                    compress_type=zipfile.ZIP_STORED,
                )
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
                compress_type=zipfile.ZIP_DEFLATED,
            )
        with zipfile.ZipFile(temporary, "r") as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise ValueError(f"归档完整性检查失败：{bad_member}")
            for item in file_entries:
                digest = hashlib.sha256()
                with archive.open(item["archive_path"], "r") as source:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                if digest.hexdigest() != item["sha256"]:
                    raise ValueError(f"归档素材在打包期间发生变化，SHA-256 不匹配：{item['archive_path']}")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise

    return destination, manifest


@router.get("/{project_id}/export")
def export_story_project(project_id: int, db: Session = Depends(get_db)):
    try:
        path, manifest = build_project_archive(project_id, db)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, "项目归档失败：" + str(exc)[:1200]) from exc
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/zip",
        headers={
            "X-Project-Archive-Files": str(manifest["file_count"]),
            "X-Project-Archive-Missing": str(manifest["missing_file_count"]),
        },
    )
