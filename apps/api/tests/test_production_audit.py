import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models.media import MediaFile, MediaTask
from app.models.novel import Chapter, Novel
from app.models.story import EpisodePlan, StoryboardShot, StoryProject
from app.routers.production_audit import (
    _as_int_ids,
    build_production_readiness_report,
)


class ProductionReadinessAuditTests(unittest.TestCase):
    def test_as_int_ids_ignores_invalid_values(self):
        self.assertEqual(_as_int_ids([1, "2", 0, -1, "bad", None]), {1, 2})
        self.assertEqual(_as_int_ids(None), set())

    def test_project_audit_reports_unfinished_pipeline_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = create_engine(f"sqlite:///{Path(temporary) / 'audit.db'}")
            Base.metadata.create_all(engine)
            db = Session(engine)
            try:
                novel = Novel(title="原著", source_text="原著正文")
                db.add(novel)
                db.flush()
                chapter = Chapter(
                    novel_id=novel.id,
                    chapter_number=1,
                    title="第一章",
                    content="剧情正文",
                    word_count=4,
                )
                project = StoryProject(
                    novel_id=novel.id,
                    name="待制作项目",
                    min_duration=90,
                    max_duration=150,
                )
                db.add_all([chapter, project])
                db.flush()
                episode = EpisodePlan(
                    project_id=project.id,
                    episode_number=1,
                    title="第一集",
                    source_chapter_ids=[chapter.id],
                    screenplay="正式剧本正文",
                    duration_seconds=120,
                )
                db.add(episode)
                db.flush()
                db.add(StoryboardShot(
                    project_id=project.id,
                    episode_id=episode.id,
                    shot_number=1,
                    visual_description="主角走入大厅",
                    image_prompt="cinematic character entering a hall",
                    character_ids=[],
                ))
                db.commit()

                report = build_production_readiness_report(project.id, db)
                self.assertEqual(report["status"], "blocked")
                self.assertEqual(report["summary"]["chapter_count"], 1)
                self.assertEqual(report["summary"]["covered_chapter_count"], 1)
                self.assertEqual(report["summary"]["episode_count"], 1)
                self.assertEqual(report["summary"]["shot_count"], 1)
                self.assertEqual(report["summary"]["shots_missing_video"], 1)
                self.assertFalse(report["network_used"])
                self.assertFalse(report["ai_calls_started"])
                self.assertIn(
                    "shot_video_missing",
                    {item["code"] for item in report["issues"]},
                )
            finally:
                db.close()
                engine.dispose()

    def test_completed_storyboard_video_task_satisfies_shot_video_requirement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "shot.mp4"
            video.write_bytes(b"video")
            comfy_install = root / "comfy-install"
            comfy_output = comfy_install / "ComfyUI" / "output"
            comfy_output.mkdir(parents=True)
            generated_image = comfy_output / "shot.png"
            generated_image.write_bytes(b"image")
            engine = create_engine(f"sqlite:///{root / 'ready.db'}")
            Base.metadata.create_all(engine)
            db = Session(engine)
            try:
                novel = Novel(title="原著")
                db.add(novel)
                db.flush()
                chapter = Chapter(
                    novel_id=novel.id,
                    chapter_number=1,
                    title="第一章",
                    content="剧情正文",
                    word_count=4,
                )
                project = StoryProject(
                    novel_id=novel.id,
                    name="可交付项目",
                    min_duration=90,
                    max_duration=150,
                )
                db.add_all([chapter, project])
                db.flush()
                episode = EpisodePlan(
                    project_id=project.id,
                    episode_number=1,
                    title="第一集",
                    source_chapter_ids=[chapter.id],
                    screenplay="正式剧本",
                    duration_seconds=120,
                )
                db.add(episode)
                db.flush()
                shot = StoryboardShot(
                    project_id=project.id,
                    episode_id=episode.id,
                    shot_number=1,
                    visual_description="主角进入大厅",
                    image_prompt="cinematic shot",
                )
                db.add(shot)
                db.flush()
                db.add_all([
                    MediaTask(
                        task_type="storyboard_video",
                        status="completed",
                        request_data={"episode_id": episode.id, "shot_id": shot.id},
                        result_path=str(video),
                    ),
                    MediaTask(
                        task_type="local_image_batch",
                        status="completed",
                        request_data={"project_id": project.id, "episode_id": episode.id, "shot_id": shot.id},
                        result_data={"images": [{"filename": generated_image.name, "subfolder": "", "type": "output"}]},
                    ),
                    MediaFile(
                        name="episode_1_final.mp4",
                        category="video",
                        file_path=str(video),
                        file_size=video.stat().st_size,
                        metadata_json={
                            "project_id": project.id,
                            "episode_id": episode.id,
                            "rendered_output": True,
                        },
                    ),
                ])
                db.commit()

                def safe_file(raw):
                    if str(raw) != str(video):
                        raise ValueError("not a managed test file")
                    return video

                with (
                    patch("app.routers.production_audit.safe_storage_file", side_effect=safe_file),
                    patch("app.routers.local_image_runtime._install_root", return_value=comfy_install),
                ):
                    report = build_production_readiness_report(project.id, db)
                self.assertEqual(report["status"], "ready")
                self.assertEqual(report["summary"]["shots_with_video"], 1)
                self.assertEqual(report["summary"]["shots_missing_video"], 0)
                self.assertEqual(report["summary"]["shots_with_image"], 1)
            finally:
                db.close()
                engine.dispose()

    def test_missing_project_raises_lookup_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = create_engine(f"sqlite:///{Path(temporary) / 'empty.db'}")
            Base.metadata.create_all(engine)
            db = Session(engine)
            try:
                with self.assertRaisesRegex(LookupError, "剧本项目不存在"):
                    build_production_readiness_report(123, db)
            finally:
                db.close()
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
