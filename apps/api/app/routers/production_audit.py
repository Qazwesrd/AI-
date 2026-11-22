"""Read-only end-to-end production readiness audit for one story project."""
from pathlib import Path
import json
import re
import shutil
import subprocess

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.media import MediaFile, MediaTask, SubtitleTrack
from app.models.novel import Chapter, Novel
from app.models.story import (
    EpisodeEditTimeline,
    EpisodePlan,
    StoryAssetLink,
    StoryboardMediaLink,
    StoryboardShot,
    StoryProject,
)
from app.models.visual import ImageTask, VisualAsset
from app.routers.project_exports import _safe_comfyui_output_file
from app.services.media_service import safe_storage_file

router = APIRouter(prefix="/api/story/projects", tags=["漫剧生产链路检查"])


def _local_file_state(raw_path):
    if not isinstance(raw_path, str) or not raw_path.strip():
        return {"exists": False, "reason": "未设置本地文件路径"}
    try:
        path = safe_storage_file(raw_path)
    except (ValueError, FileNotFoundError, OSError):
        return {"exists": False, "reason": "文件不存在或路径不属于本地素材目录"}
    if path.is_symlink() or not path.is_file():
        return {"exists": False, "reason": "文件不是受管理的普通文件"}
    return {"exists": True, "size_bytes": path.stat().st_size}


def _project_media_file_state(db, raw_path, project_id, episode_id=None):
    """Resolve managed media and reject explicit foreign project/episode ownership."""
    state = _local_file_state(raw_path)
    if not state.get("exists"):
        return state
    try:
        normalized = str(safe_storage_file(raw_path))
    except (ValueError, FileNotFoundError, OSError):
        return state
    media = db.query(MediaFile).filter(MediaFile.file_path == normalized).first()
    if not media:
        return state
    metadata = media.metadata_json if isinstance(media.metadata_json, dict) else {}
    owner_project = metadata.get("project_id")
    owner_episode = metadata.get("episode_id")
    if owner_project not in (None, "", 0, "0"):
        if isinstance(owner_project, bool):
            return {"exists": False, "reason": "素材项目归属元数据无效"}
        try:
            if int(owner_project) != int(project_id):
                return {"exists": False, "reason": "素材明确归属于其他项目"}
        except (TypeError, ValueError):
            return {"exists": False, "reason": "素材项目归属元数据无效"}
    if episode_id is not None and owner_episode not in (None, "", 0, "0"):
        if isinstance(owner_episode, bool):
            return {"exists": False, "reason": "素材分集归属元数据无效"}
        try:
            if int(owner_episode) != int(episode_id):
                return {"exists": False, "reason": "素材明确归属于其他分集"}
        except (TypeError, ValueError):
            return {"exists": False, "reason": "素材分集归属元数据无效"}
    return state


def _subtitle_track_matches_episode(track, project_id, episode_id):
    """Treat unscoped legacy tracks as shared; reject explicit foreign ownership."""
    return bool(
        track
        and track.project_id in (None, project_id)
        and track.episode_id in (None, episode_id)
    )


def _as_int_ids(values):
    result = set()
    if not isinstance(values, list):
        return result
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
        else:
            continue
        if parsed > 0:
            result.add(parsed)
    return result


def build_production_readiness_report(project_id: int, db: Session):
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    if not project:
        raise LookupError("剧本项目不存在")
    novel = db.query(Novel).filter(Novel.id == project.novel_id).first()
    chapters = db.query(Chapter).filter(
        Chapter.novel_id == project.novel_id
    ).order_by(Chapter.chapter_number).all()
    episodes = db.query(EpisodePlan).filter(
        EpisodePlan.project_id == project.id
    ).order_by(EpisodePlan.sort_order, EpisodePlan.episode_number).all()
    episode_ids = [item.id for item in episodes]
    shots = db.query(StoryboardShot).filter(
        StoryboardShot.project_id == project.id
    ).order_by(StoryboardShot.episode_id, StoryboardShot.sort_order, StoryboardShot.shot_number).all()
    timelines = db.query(EpisodeEditTimeline).filter(
        EpisodeEditTimeline.episode_id.in_(episode_ids or [-1])
    ).all()
    media_links = db.query(StoryboardMediaLink).filter(
        StoryboardMediaLink.project_id == project.id
    ).all()
    asset_links = db.query(StoryAssetLink).filter(
        StoryAssetLink.project_id == project.id
    ).all()

    media_ids = sorted({item.media_file_id for item in media_links})
    media_files = db.query(MediaFile).filter(MediaFile.id.in_(media_ids or [-1])).all()
    media_by_id = {item.id: item for item in media_files}
    all_media_records = db.query(MediaFile).all()
    final_outputs_by_episode = {}
    for media in all_media_records:
        metadata = media.metadata_json if isinstance(media.metadata_json, dict) else {}
        try:
            media_episode_id = int(metadata.get("episode_id") or 0)
            metadata_project_id = int(metadata.get("project_id") or 0)
        except (TypeError, ValueError):
            media_episode_id = 0
            metadata_project_id = 0
        # MediaFile is a global table. Never let an explicitly foreign project
        # claim an episode's final render, even if its metadata reuses this ID.
        belongs_to_project = metadata_project_id in {0, project.id}
        for episode_id in episode_ids:
            category = (media.category or "").lower()
            is_episode_video = belongs_to_project and category in {"video", "视频"} and (
                (
                    metadata.get("rendered_output") is True
                    and metadata_project_id == project.id
                    and media_episode_id == episode_id
                )
                or (
                    not metadata.get("shot_id")
                    and (media.name or "").startswith(f"episode_{episode_id}_")
                )
            )
            if is_episode_video:
                state = _local_file_state(media.file_path)
                if state["exists"]:
                    final_outputs_by_episode.setdefault(episode_id, []).append(media)
    shot_ids = {item.id for item in shots}
    media_tasks = db.query(MediaTask).filter(
        MediaTask.task_type.in_(["storyboard_video", "episode_speech_track", "local_image_batch", "local_image_reference"])
    ).all()
    video_tasks_by_shot = {}
    image_tasks_by_shot = {}
    speech_tracks_by_episode = {}
    episode_id_set = set(episode_ids)
    for task in media_tasks:
        metadata = task.request_data if isinstance(task.request_data, dict) else {}
        try:
            task_episode_id = int(metadata.get("episode_id") or 0)
            task_shot_id = int(metadata.get("shot_id") or 0)
            task_project_id = int(metadata.get("project_id") or 0)
        except (TypeError, ValueError):
            continue
        # Older task records may omit project_id; when present it must match.
        if task_project_id not in {0, project.id}:
            continue
        if task.task_type == "storyboard_video" and task_episode_id in episode_id_set and task_shot_id in shot_ids:
            video_tasks_by_shot.setdefault(task_shot_id, []).append(task)
        elif task.task_type == "episode_speech_track" and task_episode_id in episode_id_set:
            speech_tracks_by_episode.setdefault(task_episode_id, []).append(task)
        elif task.task_type in {"local_image_batch", "local_image_reference"} and task_episode_id in episode_id_set and task_shot_id in shot_ids:
            image_tasks_by_shot.setdefault(task_shot_id, []).append(task)
    links_by_shot = {}
    for link in media_links:
        links_by_shot.setdefault(link.shot_id, []).append(link)

    asset_ids = {item.asset_id for item in asset_links if item.asset_id}
    for shot in shots:
        asset_ids.update(_as_int_ids(shot.character_ids or []))
        asset_ids.update(_as_int_ids(shot.prop_ids or []))
        asset_ids.update(_as_int_ids([shot.scene_asset_id]))
    assets = db.query(VisualAsset).filter(
        VisualAsset.id.in_(sorted(asset_ids) or [-1])
    ).all()
    assets_by_id = {item.id: item for item in assets}
    image_tasks = db.query(ImageTask).filter(
        ImageTask.asset_id.in_(sorted(asset_ids) or [-1])
    ).all()
    image_tasks_by_asset = {}
    for task in image_tasks:
        image_tasks_by_asset.setdefault(task.asset_id, []).append(task)

    timeline_by_episode = {item.episode_id: item for item in timelines}
    shots_by_episode = {}
    for shot in shots:
        shots_by_episode.setdefault(shot.episode_id, []).append(shot)

    issues = []

    def add_issue(severity, code, message, episode_id=None, shot_id=None):
        issues.append({
            "severity": severity,
            "code": code,
            "message": message,
            "episode_id": episode_id,
            "shot_id": shot_id,
        })

    missing_link_asset_ids = sorted({
        item.asset_id for item in asset_links if item.asset_id
    } - set(assets_by_id))
    if missing_link_asset_ids:
        add_issue(
            "warning",
            "project_asset_link_missing",
            f"项目素材关联中有 {len(missing_link_asset_ids)} 个视觉素材记录不存在",
        )

    if not novel:
        add_issue("blocker", "source_novel_missing", "项目关联的原著不存在")
    elif not chapters:
        add_issue("blocker", "source_chapters_missing", "原著尚无章节内容，无法核对剧情覆盖")
    if not episodes:
        add_issue("blocker", "project_episodes_missing", "项目尚未建立任何分集")
    if project.status == "source_changed":
        add_issue("blocker", "source_changed", "原著内容已变更，旧分集和分镜必须复核后才能继续制作")

    chapter_ids = {item.id for item in chapters}
    covered_chapter_ids = set()
    invalid_chapter_ids = set()
    for episode in episodes:
        referenced = _as_int_ids(episode.source_chapter_ids or [])
        covered_chapter_ids.update(referenced & chapter_ids)
        invalid_chapter_ids.update(referenced - chapter_ids)
        if not referenced:
            add_issue("blocker", "episode_missing_source", f"第 {episode.episode_number} 集没有关联原著章节", episode.id)
        if not (episode.screenplay or "").strip() or "待 AI 改编" in (episode.screenplay or ""):
            add_issue("blocker", "episode_missing_screenplay", f"第 {episode.episode_number} 集尚未形成正式剧本", episode.id)
        duration = int(episode.duration_seconds or 0)
        minimum = int(project.min_duration or 0)
        maximum = int(project.max_duration or 0)
        if duration <= 0 or duration < minimum or duration > maximum:
            add_issue("warning", "episode_duration_out_of_range", f"第 {episode.episode_number} 集计划时长 {duration} 秒，超出项目设定范围或尚未设置", episode.id)
        episode_shots = shots_by_episode.get(episode.id, [])
        if not episode_shots:
            add_issue("blocker", "episode_missing_shots", f"第 {episode.episode_number} 集没有分镜", episode.id)
        if not final_outputs_by_episode.get(episode.id):
            add_issue("blocker", "episode_final_render_missing", f"第 {episode.episode_number} 集尚无可读取的最终合成视频", episode.id)

    if novel and chapters and len(covered_chapter_ids) < len(chapter_ids):
        missing = sorted(chapter_ids - covered_chapter_ids)
        add_issue("warning", "source_chapter_coverage_incomplete", f"有 {len(missing)} 个原著章节尚未关联到分集")
    if invalid_chapter_ids:
        add_issue("warning", "source_chapter_reference_invalid", f"分集引用了 {len(invalid_chapter_ids)} 个不存在的章节 ID")

    shot_video_ready = 0
    shot_image_ready = 0
    shot_missing_video = 0
    dialogue_shots = 0
    for shot in shots:
        referenced_asset_ids = _as_int_ids(shot.character_ids or []) | _as_int_ids(shot.prop_ids or [])
        referenced_asset_ids.update(_as_int_ids([shot.scene_asset_id]))
        missing_asset_ids = sorted(referenced_asset_ids - set(assets_by_id))
        if missing_asset_ids:
            add_issue("warning", "shot_asset_reference_missing", f"镜头 #{shot.shot_number} 引用了不存在的视觉素材 ID：{missing_asset_ids}", shot.episode_id, shot.id)
        if not (shot.visual_description or "").strip():
            add_issue("warning", "shot_visual_description_missing", f"镜头 #{shot.shot_number} 缺少画面描述", shot.episode_id, shot.id)
        if not (shot.image_prompt or "").strip():
            add_issue("warning", "shot_image_prompt_missing", f"镜头 #{shot.shot_number} 缺少图片生成提示词", shot.episode_id, shot.id)
        if (shot.dialogue or "").strip():
            dialogue_shots += 1

        linked_video = False
        linked_image = any(
            _local_file_state(path)["exists"]
            for asset_id in referenced_asset_ids
            if asset_id in assets_by_id
            for path in (
                assets_by_id[asset_id].image_path,
                assets_by_id[asset_id].reference_path,
            )
            if path
        )
        if not linked_image:
            linked_image = any(
                task.result_path and _local_file_state(task.result_path)["exists"]
                for asset_id in referenced_asset_ids
                for task in image_tasks_by_asset.get(asset_id, [])
            )
        if not linked_image:
            for image_task in image_tasks_by_shot.get(shot.id, []):
                result_data = image_task.result_data if isinstance(image_task.result_data, dict) else {}
                images = result_data.get("images") if isinstance(result_data.get("images"), list) else []
                if image_task.status == "completed" and any(
                    isinstance(image, dict)
                    and image.get("type", "output") == "output"
                    and _safe_comfyui_output_file(image.get("filename"), image.get("subfolder", "")) is not None
                    for image in images
                ):
                    linked_image = True
                    break
        for task in video_tasks_by_shot.get(shot.id, []):
            if task.status == "completed" and task.result_path:
                task_file_state = _local_file_state(task.result_path)
                if task_file_state["exists"]:
                    linked_video = True
                else:
                    add_issue("warning", "shot_video_task_file_missing", f"镜头 #{shot.shot_number} 的已完成视频任务文件无法读取", shot.episode_id, shot.id)
            elif task.status not in {"failed", "cancelled", "submission_unknown"}:
                add_issue("warning", "shot_video_task_pending", f"镜头 #{shot.shot_number} 仍有视频任务未完成（{task.status}）", shot.episode_id, shot.id)
        for link in links_by_shot.get(shot.id, []):
            media = media_by_id.get(link.media_file_id)
            if media is None:
                add_issue("warning", "shot_media_record_missing", f"镜头 #{shot.shot_number} 的关联媒体记录不存在", shot.episode_id, shot.id)
                continue
            state = _local_file_state(media.file_path)
            if not state["exists"]:
                add_issue("warning", "shot_media_file_missing", f"镜头 #{shot.shot_number} 的素材“{media.name}”无法读取：{state['reason']}", shot.episode_id, shot.id)
                continue
            category = (media.category or "").lower()
            if "video" in category or category in {"视频", "clip"}:
                linked_video = True
            if "image" in category or category in {"图片", "image_reference"}:
                linked_image = True

        if linked_video:
            shot_video_ready += 1
        else:
            shot_missing_video += 1
            add_issue("blocker", "shot_video_missing", f"镜头 #{shot.shot_number} 尚未关联可读取的视频素材", shot.episode_id, shot.id)
        if linked_image:
            shot_image_ready += 1

    for asset in assets:
        if not asset.confirmed:
            add_issue("warning", "visual_asset_unconfirmed", f"视觉素材“{asset.name}”尚未确认")
        paths = [asset.image_path, asset.reference_path]
        if not any(_local_file_state(path)["exists"] for path in paths if path):
            add_issue("warning", "visual_asset_reference_missing", f"视觉素材“{asset.name}”没有可读取的本地参考图")

    timeline_summary = []
    for episode in episodes:
        timeline = timeline_by_episode.get(episode.id)
        speech_task = next((
            task for task in speech_tracks_by_episode.get(episode.id, [])
            if task.status == "completed" and task.result_path and _local_file_state(task.result_path)["exists"]
        ), None)
        episode_has_dialogue = any(
            (shot.dialogue or "").strip() for shot in shots_by_episode.get(episode.id, [])
        )
        if not timeline:
            if episode_has_dialogue:
                add_issue("warning", "episode_timeline_missing", f"第 {episode.episode_number} 集尚未建立剪辑时间轴", episode.id)
                if not speech_task:
                    add_issue("warning", "episode_voice_track_missing", f"第 {episode.episode_number} 集有对白，但没有可读取的配音轨", episode.id)
            timeline_summary.append({
                "episode_id": episode.id,
                "voice_track_available": bool(speech_task),
                "music_track_available": False,
                "sound_effect_track_count": 0,
                "subtitle_cue_count": len((speech_task.request_data or {}).get("subtitle_cues") or []) if speech_task and isinstance(speech_task.request_data, dict) else 0,
            })
            continue
        data = timeline.edit_data if isinstance(timeline.edit_data, dict) else {}
        voice_state = _project_media_file_state(db, data.get("voice_path"), project.id, episode.id)
        if not voice_state["exists"] and speech_task:
            voice_state = _project_media_file_state(db, speech_task.result_path, project.id, episode.id)
        music_state = _project_media_file_state(db, data.get("music_path"), project.id, episode.id)
        effect_tracks = data.get("sound_effect_tracks") if isinstance(data.get("sound_effect_tracks"), list) else []
        valid_effects = 0
        for track in effect_tracks:
            if not isinstance(track, dict):
                continue
            effect_state = _project_media_file_state(db, track.get("path"), project.id, episode.id)
            if effect_state["exists"]:
                valid_effects += 1
            else:
                add_issue("warning", "sound_effect_file_missing", f"第 {episode.episode_number} 集有音效轨道无法读取", episode.id)
        subtitle_cues = data.get("subtitle_cues") if isinstance(data.get("subtitle_cues"), list) else []
        if not subtitle_cues and speech_task and isinstance(speech_task.request_data, dict):
            subtitle_cues = speech_task.request_data.get("subtitle_cues") or []
        subtitle_track = None
        subtitle_track_mismatch = False
        try:
            subtitle_track_id = int(data.get("subtitle_track_id") or data.get("subtitle_id") or 0)
        except (TypeError, ValueError):
            subtitle_track_id = 0
        if subtitle_track_id > 0:
            candidate_track = db.query(SubtitleTrack).filter(
                SubtitleTrack.id == subtitle_track_id
            ).first()
            if not candidate_track or not _subtitle_track_matches_episode(
                candidate_track, project.id, episode.id
            ):
                subtitle_track_mismatch = True
            else:
                subtitle_track = candidate_track
        subtitle_path = None if subtitle_track_mismatch else (
            data.get("subtitle_path") or (subtitle_track.file_path if subtitle_track else None)
        )
        subtitle_file_state = _project_media_file_state(db, subtitle_path, project.id, episode.id)
        subtitles_available = bool(subtitle_cues) or subtitle_file_state["exists"]
        if episode_has_dialogue and not voice_state["exists"]:
            add_issue("warning", "episode_voice_track_missing", f"第 {episode.episode_number} 集有对白，但没有可读取的独立配音轨", episode.id)
        if episode_has_dialogue and not subtitles_available:
            add_issue("warning", "episode_subtitles_missing", f"第 {episode.episode_number} 集有对白，但时间轴中没有可读取的字幕文件或字幕条目", episode.id)
        timeline_summary.append({
            "episode_id": episode.id,
            "voice_track_available": voice_state["exists"],
            "music_track_available": music_state["exists"],
            "sound_effect_track_count": valid_effects,
            "subtitle_cue_count": len(subtitle_cues),
            "subtitle_file_available": subtitle_file_state["exists"],
        })

    blockers = sum(1 for item in issues if item["severity"] == "blocker")
    warnings = sum(1 for item in issues if item["severity"] == "warning")
    return {
        "project_id": project.id,
        "project_name": project.name,
        "novel_id": project.novel_id,
        "novel_title": novel.title if novel else None,
        "status": "blocked" if blockers else ("ready_with_warnings" if warnings else "ready"),
        "summary": {
            "chapter_count": len(chapters),
            "covered_chapter_count": len(covered_chapter_ids & chapter_ids),
            "chapter_coverage_ratio": round(len(covered_chapter_ids & chapter_ids) / len(chapter_ids), 4) if chapter_ids else 0.0,
            "episode_count": len(episodes),
            "visual_asset_count": len(assets),
            "shot_count": len(shots),
            "shots_with_video": shot_video_ready,
            "shots_missing_video": shot_missing_video,
            "shots_with_image": shot_image_ready,
            "final_rendered_episode_count": sum(bool(final_outputs_by_episode.get(item.id)) for item in episodes),
            "dialogue_shot_count": dialogue_shots,
            "blocker_count": blockers,
            "warning_count": warnings,
        },
        "episodes": [
            {
                "episode_id": episode.id,
                "episode_number": episode.episode_number,
                "title": episode.title,
                "shot_count": len(shots_by_episode.get(episode.id, [])),
                "duration_seconds": episode.duration_seconds,
                "final_video_available": bool(final_outputs_by_episode.get(episode.id)),
                "final_video_files": [item.name for item in final_outputs_by_episode.get(episode.id, [])],
                "timeline": next((item for item in timeline_summary if item["episode_id"] == episode.id), None),
            }
            for episode in episodes
        ],
        "issues": issues,
        "network_used": False,
        "ai_calls_started": False,
        "message": (
            "生产链路检查完成。存在阻断项，当前不能视为可交付项目。"
            if blockers else
            ("生产链路检查完成。没有阻断项，但仍有内容需要人工复核。" if warnings else "生产链路检查通过。")
        ),
        "limitations": [
            "本报告检查数据库关联、素材文件存在性和基本制作字段，不会解码全部视频。",
            "不会判断人物身份一致性、画面内容正确性、实际口型同步或配音情绪。",
            "最终成片仍需执行逐集 FFmpeg/FFprobe 质量检查并人工试听复核。",
        ],
    }


@router.get("/{project_id}/production-readiness")
def get_project_production_readiness(project_id: int, db: Session = Depends(get_db)):
    try:
        return build_production_readiness_report(project_id, db)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc



def _probe_final_video(path: Path):
    """Run bounded local FFprobe/FFmpeg checks; never uploads or modifies the source."""
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if not ffprobe:
        return {
            "status": "blocked",
            "issues": [{"code": "ffprobe_unavailable", "severity": "blocker", "message": "未找到 FFprobe，无法验证视频流和时长。"}],
            "probe": None,
            "decode_check": "not_run",
        }
    command = [
        ffprobe, "-v", "error", "-show_entries",
        "format=duration,size,format_name:stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels,start_time,duration",
        "-of", "json", str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        return {
            "status": "blocked",
            "issues": [{"code": "ffprobe_timeout", "severity": "blocker", "message": "FFprobe 检查超过 30 秒，已停止本次检查。"}],
            "probe": None,
            "decode_check": "not_run",
        }
    if result.returncode != 0:
        return {
            "status": "blocked",
            "issues": [{"code": "ffprobe_failed", "severity": "blocker", "message": (result.stderr or "FFprobe 无法读取该文件。")[:1200]}],
            "probe": None,
            "decode_check": "not_run",
        }
    try:
        info = json.loads(result.stdout)
    except (TypeError, ValueError):
        info = None
    if not isinstance(info, dict):
        return {
            "status": "blocked",
            "issues": [{"code": "ffprobe_invalid_json", "severity": "blocker", "message": "FFprobe 返回的数据格式无效。"}],
            "probe": None,
            "decode_check": "not_run",
        }
    streams = info.get("streams") if isinstance(info.get("streams"), list) else []
    video_streams = [item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"]
    audio_streams = [item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"]
    issues = []
    if not video_streams:
        issues.append({"code": "video_stream_missing", "severity": "blocker", "message": "文件中没有可识别的视频流。"})
    if not audio_streams:
        issues.append({"code": "audio_stream_missing", "severity": "warning", "message": "文件中没有音频流；请确认是否为无声作品。"})
    fmt = info.get("format") if isinstance(info.get("format"), dict) else {}
    if video_streams and audio_streams:
        try:
            video_start = float(video_streams[0].get("start_time") or 0)
            audio_start = float(audio_streams[0].get("start_time") or 0)
            av_start_delta = abs(video_start - audio_start)
            if av_start_delta > 0.25:
                issues.append({
                    "code": "audio_video_start_offset",
                    "severity": "warning",
                    "message": f"音视频起始时间相差 {av_start_delta:.3f} 秒，建议人工复核同步。",
                })
        except (TypeError, ValueError):
            pass
        try:
            video_duration = float(video_streams[0].get("duration") or 0)
            audio_duration = float(audio_streams[0].get("duration") or 0)
            if video_duration > 0 and audio_duration > 0:
                av_duration_delta = abs(video_duration - audio_duration)
                if av_duration_delta > 0.5:
                    issues.append({
                        "code": "audio_video_duration_mismatch",
                        "severity": "warning",
                        "message": f"音视频流时长相差 {av_duration_delta:.3f} 秒，建议检查尾部音画同步。",
                    })
        except (TypeError, ValueError):
            pass
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        issues.append({"code": "duration_invalid", "severity": "blocker", "message": "视频时长无效或无法读取。"})
    decode_check = "not_run"
    black_events = []
    silence_events = []
    if ffmpeg and video_streams and duration > 0:
        try:
            scan_command = [
                ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info",
                "-i", str(path),
                "-vf", "blackdetect=d=0.5:pix_th=0.10",
            ]
            if audio_streams:
                scan_command.extend(["-af", "silencedetect=n=-50dB:d=1.0"])
            scan_command.extend(["-f", "null", "-"])
            decoded = subprocess.run(
                scan_command,
                capture_output=True, text=True, timeout=240, check=False,
            )
            decode_check = "passed" if decoded.returncode == 0 else "failed"
            scan_log = decoded.stderr or ""
            if decoded.returncode != 0:
                issues.append({
                    "code": "decode_errors",
                    "severity": "blocker",
                    "message": scan_log[-1600:] or "FFmpeg 解码检查失败。",
                })
            black_events = re.findall(r"black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)", scan_log)
            silence_events = re.findall(r"silence_start:([0-9.]+)", scan_log)
            if black_events:
                issues.append({
                    "code": "black_frames_detected",
                    "severity": "warning",
                    "message": f"检测到 {len(black_events)} 段持续至少 0.5 秒的黑场，请确认是否为有意转场。",
                })
            if silence_events:
                issues.append({
                    "code": "silence_detected",
                    "severity": "warning",
                    "message": f"检测到 {len(silence_events)} 段持续至少 1 秒、低于 -50 dB 的静音，请确认是否为有意留白。",
                })
        except subprocess.TimeoutExpired:
            decode_check = "timeout"
            issues.append({"code": "decode_timeout", "severity": "warning", "message": "完整解码检查超过 240 秒，已停止本次扫描，未判定文件完整。"})
    elif not ffmpeg:
        issues.append({"code": "ffmpeg_unavailable", "severity": "warning", "message": "未找到 FFmpeg，跳过完整解码检查。"})
    return {
        "status": "blocked" if any(item["severity"] == "blocker" for item in issues) else ("ready_with_warnings" if issues else "ready"),
        "issues": issues,
        "probe": {
            "duration_seconds": round(duration, 3),
            "size_bytes": path.stat().st_size,
            "format_name": fmt.get("format_name"),
            "video_streams": video_streams,
            "audio_streams": audio_streams,
        },
        "decode_check": decode_check,
        "content_checks": {
            "black_frame_segments": len(black_events) if ffmpeg and video_streams and duration > 0 and decode_check != "not_run" else None,
            "silence_segments": len(silence_events) if ffmpeg and audio_streams and duration > 0 and decode_check != "not_run" else None,
            "black_threshold_seconds": 0.5,
            "silence_threshold_seconds": 1.0,
            "silence_level_db": -50,
        },
    }


@router.get("/{project_id}/episodes/{episode_id}/video-quality")
def audit_episode_video_quality(project_id: int, episode_id: int, db: Session = Depends(get_db)):
    """Explicit, read-only technical QA for a rendered episode video."""
    project = db.query(StoryProject).filter(StoryProject.id == project_id).first()
    if not project:
        raise HTTPException(404, "剧本项目不存在")
    episode = db.query(EpisodePlan).filter(
        EpisodePlan.id == episode_id,
        EpisodePlan.project_id == project_id,
    ).first()
    if not episode:
        raise HTTPException(404, "该分集不属于此剧本项目")
    candidates = db.query(MediaFile).filter(
        MediaFile.category.in_(["video", "视频"])
    ).order_by(MediaFile.id.desc()).all()
    matched = []
    for media in candidates:
        metadata = media.metadata_json if isinstance(media.metadata_json, dict) else {}
        try:
            media_episode_id = int(metadata.get("episode_id") or 0)
            metadata_project_id = int(metadata.get("project_id") or 0)
        except (TypeError, ValueError):
            media_episode_id = 0
            metadata_project_id = 0
        # Keep this endpoint consistent with the project-level readiness audit:
        # global media rows explicitly owned by another project are never candidates.
        belongs_to_project = metadata_project_id in {0, project.id}
        is_final = belongs_to_project and (
            (
                metadata.get("rendered_output") is True
                and metadata_project_id == project.id
                and media_episode_id == episode_id
            ) or (
                not metadata.get("shot_id")
                and (media.name or "").startswith(f"episode_{episode_id}_")
            )
        )
        if is_final:
            state = _local_file_state(media.file_path)
            if state["exists"]:
                matched.append(media)
    if not matched:
        raise HTTPException(404, "该分集没有可读取的最终合成视频")
    results = []
    for media in matched:
        path = safe_storage_file(media.file_path)
        if path.is_symlink() or not path.is_file():
            continue
        report = _probe_final_video(path)
        probe = report.get("probe") or {}
        duration = probe.get("duration_seconds")
        if isinstance(duration, (int, float)) and duration > 0:
            minimum = int(project.min_duration or 0)
            maximum = int(project.max_duration or 0)
            if minimum and duration < minimum or maximum and duration > maximum:
                report["issues"].append({
                    "code": "episode_duration_out_of_range",
                    "severity": "warning",
                    "message": f"实际时长 {duration:.1f} 秒，项目目标范围为 {minimum}-{maximum} 秒。",
                })
                if report["status"] == "ready":
                    report["status"] = "ready_with_warnings"
        results.append({"media_file_id": media.id, "file_name": media.name, **report})
    return {
        "project_id": project_id,
        "episode_id": episode_id,
        "episode_number": episode.episode_number,
        "status": "blocked" if any(item["status"] == "blocked" for item in results) else ("ready_with_warnings" if any(item["status"] == "ready_with_warnings" for item in results) else "ready"),
        "files": results,
        "read_only": True,
        "network_used": False,
        "source_files_modified": False,
        "notice": "本检查包含容器、音视频流、时长、完整解码、黑场和静音区间的自动筛查；黑场/静音可能是有意设计，最终画面质量、口型同步与剧情内容仍需人工复核。",
    }
