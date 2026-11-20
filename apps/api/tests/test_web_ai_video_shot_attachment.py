import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.media import MediaFile, MediaTask
from app.models.novel import Novel, Chapter
from app.models.story import StoryProject, EpisodePlan, StoryboardShot
from app.models.web_ai_tool import WebAITask, WebAIResultFile
from app.routers import web_ai_tools


class WebAIVideoShotAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            self.engine,
            tables=[
                Novel.__table__, Chapter.__table__,
                StoryProject.__table__, EpisodePlan.__table__, StoryboardShot.__table__,
                MediaFile.__table__, MediaTask.__table__,
                WebAITask.__table__, WebAIResultFile.__table__,
            ],
        )
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Path(self.temp.name)
        (self.storage / "web_ai_results").mkdir()
        self.original_storage = web_ai_tools.STORAGE_DIR
        web_ai_tools.STORAGE_DIR = self.storage

        novel = Novel(title="测试小说")
        self.db.add(novel)
        self.db.flush()
        chapter = Chapter(novel_id=novel.id, chapter_number=3, title="第三章")
        self.db.add(chapter)
        self.db.flush()
        project = StoryProject(novel_id=novel.id, name="测试漫剧")
        self.db.add(project)
        self.db.flush()
        episode = EpisodePlan(
            project_id=project.id, episode_number=2, title="第二集",
            source_chapter_ids=[chapter.id], duration_seconds=100,
        )
        self.db.add(episode)
        self.db.flush()
        self.shot = StoryboardShot(
            project_id=project.id, episode_id=episode.id, shot_number=4,
            duration_seconds=5, scene_name="山门",
            visual_description="主角走入山门",
        )
        self.db.add(self.shot)
        self.db.flush()
        self.task = WebAITask(
            task_key="video-task-1", title="第三章视频生成", category="video",
            tool_key="doubao", novel_id=novel.id, chapter_id=chapter.id,
            project_id=project.id, prompt="生成视频",
        )
        self.db.add(self.task)
        self.db.flush()
        self.stored_name = "a" * 32 + ".mp4"
        self.video_path = self.storage / "web_ai_results" / self.stored_name
        self.video_path.write_bytes(b"\x00\\x00\\x00\\x18ftypisom" + b"\x00" * 128)
        self.result = WebAIResultFile(
            task_key=self.task.task_key, original_name="shot-04.mp4",
            stored_name=self.stored_name, content_type="video/mp4",
            file_size=self.video_path.stat().st_size, classification="video",
        )
        self.db.add(self.result)
        self.db.add(MediaFile(
            name="shot-04.mp4", category="video", file_path=str(self.video_path.resolve()),
            mime_type="video/mp4", file_size=self.video_path.stat().st_size,
        ))
        self.db.commit()
        self.file_id = self.result.id

    def tearDown(self):
        web_ai_tools.STORAGE_DIR = self.original_storage
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def test_lists_only_shots_in_matching_novel_project_and_chapter(self):
        result = web_ai_tools.list_downloaded_video_shot_options(self.file_id, self.db)
        self.assertEqual([item["shotId"] for item in result["items"]], [self.shot.id])
        self.assertEqual(result["chapterNumber"], 3)
        self.assertEqual(result["items"][0]["episodeNumber"], 2)

    def test_attach_creates_completed_storyboard_video_task(self):
        with patch(
            "app.services.media_service.probe_media",
            return_value={
                "format": {"duration": "5.0"},
                "streams": [{"codec_type": "video", "width": 1280, "height": 720}],
            },
        ):
            result = web_ai_tools.attach_downloaded_video_to_shot(
                self.file_id,
                web_ai_tools.AttachDownloadedVideoInput(shotId=self.shot.id),
                self.db,
            )
        self.assertTrue(result["success"])
        self.assertFalse(result["reused"])
        task = self.db.query(MediaTask).filter(MediaTask.id == result["task_id"]).one()
        self.assertEqual(task.task_type, "storyboard_video")
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.request_data["shot_id"], self.shot.id)
        self.assertEqual(task.request_data["episode_id"], self.shot.episode_id)
        self.assertEqual(self.shot.status, "video_ready")

    def test_repeated_attachment_is_idempotent(self):
        probe = patch(
            "app.services.media_service.probe_media",
            return_value={"format": {"duration": "5.0"}, "streams": [{"codec_type": "video"}]},
        )
        with probe:
            first = web_ai_tools.attach_downloaded_video_to_shot(
                self.file_id, web_ai_tools.AttachDownloadedVideoInput(shotId=self.shot.id), self.db
            )
            second = web_ai_tools.attach_downloaded_video_to_shot(
                self.file_id, web_ai_tools.AttachDownloadedVideoInput(shotId=self.shot.id), self.db
            )
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertTrue(second["reused"])
        self.assertEqual(self.db.query(MediaTask).filter(MediaTask.task_type == "storyboard_video").count(), 1)

    def test_rejects_file_from_non_video_task(self):
        self.task.category = "image"
        self.db.commit()
        with self.assertRaises(HTTPException) as caught:
            web_ai_tools.list_downloaded_video_shot_options(self.file_id, self.db)
        self.assertEqual(caught.exception.status_code, 409)

    def test_rejects_shot_outside_task_chapter(self):
        self.db.query(EpisodePlan).filter(EpisodePlan.id == self.shot.episode_id).one().source_chapter_ids = []
        self.db.commit()
        with patch(
            "app.services.media_service.probe_media",
            return_value={"format": {"duration": "5.0"}, "streams": [{"codec_type": "video"}]},
        ):
            with self.assertRaises(HTTPException) as caught:
                web_ai_tools.attach_downloaded_video_to_shot(
                    self.file_id, web_ai_tools.AttachDownloadedVideoInput(shotId=self.shot.id), self.db
                )
        self.assertEqual(caught.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
