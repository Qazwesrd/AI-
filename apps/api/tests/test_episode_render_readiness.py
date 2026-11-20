import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.media import MediaTask
from app.models.story import StoryProject, EpisodePlan, StoryboardShot
from app.routers import media


class EpisodeRenderReadinessTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            self.engine,
            tables=[
                StoryProject.__table__, EpisodePlan.__table__,
                StoryboardShot.__table__, MediaTask.__table__,
            ],
        )
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Path(self.temp.name)
        self.project = StoryProject(novel_id=1, name="readiness-test")
        self.db.add(self.project)
        self.db.flush()
        self.episode = EpisodePlan(
            project_id=self.project.id, episode_number=1, title="第一集",
            source_chapter_ids=[], duration_seconds=100,
        )
        self.db.add(self.episode)
        self.db.flush()
        self.shot_a = StoryboardShot(
            project_id=self.project.id, episode_id=self.episode.id,
            shot_number=1, duration_seconds=5, scene_name="山门",
        )
        self.shot_b = StoryboardShot(
            project_id=self.project.id, episode_id=self.episode.id,
            shot_number=2, duration_seconds=5, scene_name="大殿",
        )
        self.db.add_all([self.shot_a, self.shot_b])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def add_task(self, shot, status="completed", path=None, task_id_marker=None):
        task = MediaTask(
            task_type="storyboard_video", provider="web_ai", model="browser",
            status=status,
            request_data={"episode_id": self.episode.id, "shot_id": shot.id},
            result_path=str(path) if path else None,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def safe_existing_path(self, value):
        path = Path(value)
        if not path.is_file():
            raise ValueError("视频文件不存在或路径无效")
        return path

    def probe_ok(self, _path):
        return {
            "format": {"duration": "5.0"},
            "streams": [{"codec_type": "video", "width": 1280, "height": 720}],
        }

    def call_readiness(self):
        return media.get_episode_render_readiness(self.episode.id, self.db)

    def test_all_shots_ready(self):
        a = self.storage / "a.mp4"
        b = self.storage / "b.mp4"
        a.write_bytes(b"video")
        b.write_bytes(b"video")
        self.add_task(self.shot_a, path=a)
        self.add_task(self.shot_b, path=b)
        with patch.object(media, "safe_storage_file", side_effect=self.safe_existing_path), \
             patch.object(media, "probe_media", side_effect=self.probe_ok):
            result = self.call_readiness()
        self.assertTrue(result["ready"])
        self.assertEqual(result["ready_count"], 2)
        self.assertEqual([item["status"] for item in result["items"]], ["ready", "ready"])

    def test_missing_shot_is_reported(self):
        a = self.storage / "a.mp4"
        a.write_bytes(b"video")
        self.add_task(self.shot_a, path=a)
        with patch.object(media, "safe_storage_file", side_effect=lambda p: Path(p)), \
             patch.object(media, "probe_media", side_effect=self.probe_ok):
            result = self.call_readiness()
        self.assertFalse(result["ready"])
        self.assertEqual(result["missing_count"], 1)
        self.assertEqual(result["items"][1]["status"], "missing")

    def test_completed_task_with_missing_file_is_broken(self):
        missing = self.storage / "gone.mp4"
        self.add_task(self.shot_a, path=missing)
        with patch.object(media, "safe_storage_file", side_effect=lambda p: Path(p)), \
             patch.object(media, "probe_media", side_effect=self.probe_ok):
            result = self.call_readiness()
        self.assertEqual(result["broken_count"], 1)
        self.assertEqual(result["items"][0]["status"], "broken")

    def test_processing_task_is_not_reported_as_missing(self):
        self.add_task(self.shot_a, status="processing")
        with patch.object(media, "safe_storage_file", side_effect=lambda p: Path(p)), \
             patch.object(media, "probe_media", side_effect=self.probe_ok):
            result = self.call_readiness()
        self.assertEqual(result["processing_count"], 1)
        self.assertEqual(result["items"][0]["status"], "processing")

    def test_older_valid_video_is_available_as_fallback_with_warning(self):
        valid = self.storage / "old.mp4"
        valid.write_bytes(b"video")
        self.add_task(self.shot_a, path=valid)
        self.add_task(self.shot_a, path=self.storage / "new-broken.mp4")
        with patch.object(media, "safe_storage_file", side_effect=lambda p: Path(p)), \
             patch.object(media, "probe_media", side_effect=self.probe_ok):
            result = self.call_readiness()
        item = result["items"][0]
        self.assertEqual(item["status"], "ready")
        self.assertTrue(item["warnings"])

    def test_rejects_unknown_episode(self):
        with self.assertRaises(HTTPException) as caught:
            media.get_episode_render_readiness(99999, self.db)
        self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
