"""Read-only end-to-end production readiness audit for one story project."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.media import MediaFile, MediaTask
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


def _as_int_ids(values):
    result = set()
    if not isinstance(values, list):
        return result
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
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
    shot_ids = {item.id for item in shots}
    media_tasks = db.query(MediaTask).filter(
        MediaTask.task_type.in_(["storyboard_video", "episode_speech_track"])
    ).all()
    video_tasks_by_shot = {}
    speech_tracks_by_episode = {}
    episode_id_set = set(episode_ids)
    for task in media_tasks:
        metadata = task.request_data if isinstance(task.request_data, dict) else {}
        try:
            task_episode_id = int(metadata.get("episode_id") or 0)
            task_shot_id = int(metadata.get("shot_id") or 0)
        except (TypeError, ValueError):
            continue
        if task.task_type == "storyboard_video" and task_episode_id in episode_id_set and task_shot_id in shot_ids:
            video_tasks_by_shot.setdefault(task_shot_id, []).append(task)
        elif task.task_type == "episode_speech_track" and task_episode_id in episode_id_set:
            speech_tracks_by_episode.setdefault(task_episode_id, []).append(task)
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
        if shot.scene_asset_id:
            referenced_asset_ids.add(int(shot.scene_asset_id))
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
            for path in (
                assets_by_id[asset_id].image_path,
                assets_by_id[asset_id].reference_path,
            )
            if asset_id in assets_by_id and path
        )
        if not linked_image:
            linked_image = any(
                task.result_path and _local_file_state(task.result_path)["exists"]
                for asset_id in referenced_asset_ids
                for task in image_tasks_by_asset.get(asset_id, [])
            )
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
        voice_state = _local_file_state(data.get("voice_path"))
        if not voice_state["exists"] and speech_task:
            voice_state = _local_file_state(speech_task.result_path)
        music_state = _local_file_state(data.get("music_path"))
        effect_tracks = data.get("sound_effect_tracks") if isinstance(data.get("sound_effect_tracks"), list) else []
        valid_effects = 0
        for track in effect_tracks:
            if not isinstance(track, dict):
                continue
            effect_state = _local_file_state(track.get("path"))
            if effect_state["exists"]:
                valid_effects += 1
            else:
                add_issue("warning", "sound_effect_file_missing", f"第 {episode.episode_number} 集有音效轨道无法读取", episode.id)
        subtitle_cues = data.get("subtitle_cues") if isinstance(data.get("subtitle_cues"), list) else []
        if not subtitle_cues and speech_task and isinstance(speech_task.request_data, dict):
            subtitle_cues = speech_task.request_data.get("subtitle_cues") or []
        if episode_has_dialogue and not voice_state["exists"]:
            add_issue("warning", "episode_voice_track_missing", f"第 {episode.episode_number} 集有对白，但没有可读取的独立配音轨", episode.id)
        if episode_has_dialogue and not subtitle_cues:
            add_issue("warning", "episode_subtitles_missing", f"第 {episode.episode_number} 集有对白，但时间轴中没有字幕条目", episode.id)
        timeline_summary.append({
            "episode_id": episode.id,
            "voice_track_available": voice_state["exists"],
            "music_track_available": music_state["exists"],
            "sound_effect_track_count": valid_effects,
            "subtitle_cue_count": len(subtitle_cues),
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
