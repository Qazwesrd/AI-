import threading
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.media import MediaTask
from app.routers import media


class EpisodeRenderJobTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine, tables=[MediaTask.__table__])
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.task = MediaTask(
            task_type="episode_render",
            status="processing",
            request_data={"episode_id": 12, "output_name": "episode.mp4"},
            result_data={"progress": {"percent": 42, "stage": "mixing_audio"}},
        )
        self.db.add(self.task)
        self.db.commit()
        self.db.refresh(self.task)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_status_after_restart_marks_interrupted_without_retry(self):
        with patch.object(media, "SessionLocal", self.Session):
            with patch.dict(media._EPISODE_RENDER_RUNTIME, {}, clear=True):
                result = media.get_episode_render_job(self.task.id, self.db)
        self.assertEqual(result["status"], "interrupted_unknown")
        self.assertEqual(result["progress"]["percent"], 42)
        self.assertIsNone(result["output"])
        self.assertIn("未自动重新合成", result["error"])

    def test_cancel_processing_requests_ffmpeg_stop(self):
        event = threading.Event()
        with patch.dict(
            media._EPISODE_RENDER_RUNTIME,
            {self.task.id: {"cancel": event, "phase": {"index": 0}}},
            clear=True,
        ):
            result = media.cancel_episode_render_job(self.task.id, self.db)
        self.assertEqual(result["status"], "cancellation_requested")
        self.assertTrue(event.is_set())
        self.assertEqual(self.task.status, "processing")

    def test_ffmpeg_runner_receives_duration_and_persistent_cancel_event(self):
        event = threading.Event()
        runtime = {"cancel": event, "phase": {"index": 0}}
        media._EPISODE_RENDER_CONTEXT.task_id = self.task.id
        media._EPISODE_RENDER_CONTEXT.duration_seconds = 120.0
        try:
            with patch.dict(
                media._EPISODE_RENDER_RUNTIME,
                {self.task.id: runtime},
                clear=True,
            ):
                with patch.object(media, "SessionLocal", self.Session):
                    with patch.object(media, "run_ffmpeg_with_progress") as runner:
                        media._run_episode_render_ffmpeg(["-i", "input.mp4", "output.mp4"])
            kwargs = runner.call_args.kwargs
            self.assertEqual(kwargs["duration_seconds"], 120.0)
            self.assertIs(kwargs["cancel_event"], event)
            self.assertEqual(runner.call_args.args[0][-1], "output.mp4")
        finally:
            del media._EPISODE_RENDER_CONTEXT.task_id
            del media._EPISODE_RENDER_CONTEXT.duration_seconds

    def test_worker_persists_successful_result(self):
        self.task.status = "queued"
        self.db.commit()
        with patch.object(media, "SessionLocal", self.Session):
            with patch.object(media, "render_episode_from_shots", return_value={
                "success": True, "episode_id": 12, "output": "storage/video/episode.mp4",
                "quality_report": {"valid": True},
            }):
                with patch.dict(
                    media._EPISODE_RENDER_RUNTIME,
                    {self.task.id: {"cancel": threading.Event(), "phase": {"index": 0}}},
                    clear=True,
                ):
                    media._run_episode_render_job(
                        self.task.id, 12, "episode.mp4", None, 0.18,
                    )
        self.db.expire_all()
        saved = self.db.query(MediaTask).filter(MediaTask.id == self.task.id).one()
        self.assertEqual(saved.status, "completed")
        self.assertEqual(saved.result_path, "storage/video/episode.mp4")
        self.assertEqual(saved.result_data["progress"]["percent"], 100)
        self.assertTrue(saved.result_data["result"]["success"])


if __name__ == "__main__":
    unittest.main()
