import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.media import MediaTask, MediaFile
from app.routers import media


class LocalRenderJobPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine, tables=[MediaTask.__table__, MediaFile.__table__])
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.task = MediaTask(
            task_type="local_render",
            status="processing",
            request_data={"video_path": "input.mp4"},
            result_path="output.mp4",
            result_data={"progress": {"percent": 37.5, "stage": "rendering"}},
        )
        self.db.add(self.task)
        self.db.commit()
        self.db.refresh(self.task)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_status_after_process_restart_becomes_interrupted_not_requeued(self):
        with patch.dict(media._LOCAL_RENDER_RUNTIME, {}, clear=True):
            result = media.get_local_render_job(self.task.id, self.db)
        self.assertEqual(result["status"], "interrupted_unknown")
        self.assertEqual(result["progress"]["percent"], 37.5)
        self.assertIsNone(result["output"])
        self.assertIn("未自动重新渲染", result["error"])
        self.assertEqual(
            self.db.query(MediaTask).filter(MediaTask.id == self.task.id).one().status,
            "interrupted_unknown",
        )

    def test_queued_job_after_restart_is_not_left_stuck_or_auto_started(self):
        self.task.status = "queued"
        self.db.commit()
        with patch.dict(media._LOCAL_RENDER_RUNTIME, {}, clear=True):
            result = media.get_local_render_job(self.task.id, self.db)
        self.assertEqual(result["status"], "interrupted_unknown")
        self.assertIn("未自动重新渲染", result["error"])

    def test_worker_validates_then_atomically_publishes_completed_output(self):
        import tempfile
        import threading
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "final.mp4"
            partial = output.with_name("final.partial.mp4")
            self.task.status = "queued"
            self.task.result_path = str(output)
            self.task.request_data = {
                "video_path": "source.mp4", "audio_path": None,
                "subtitle_path": None, "width": 1280, "height": 720,
                "music_volume": 0.25, "original_volume": 1.0,
            }
            self.db.commit()

            def write_output(args, **kwargs):
                Path(args[-1]).write_bytes(b"x" * 2048)
                kwargs["progress_callback"]({"percent": 99.9, "stage": "validating"})

            with patch.object(media, "SessionLocal", self.Session):
                with patch.object(media, "safe_storage_file", return_value=Path(directory) / "source.mp4"):
                    with patch.object(media, "probe_media", return_value={
                        "format": {"duration": "8.0"},
                        "streams": [{"codec_type": "video", "width": 1280, "height": 720}],
                    }):
                        with patch.object(media, "run_ffmpeg_with_progress") as ffmpeg:
                            with patch.object(media, "validate_rendered_video", return_value={
                                "valid": True, "duration_seconds": 8.0,
                            }) as validate:
                                ffmpeg.side_effect = write_output
                                with patch.dict(
                                    media._LOCAL_RENDER_RUNTIME,
                                    {self.task.id: {"cancel": threading.Event(), "process": None}},
                                    clear=True,
                                ):
                                    media._run_local_render_job(self.task.id)
                                validate.assert_called_once()

            self.db.expire_all()
            saved = self.db.query(MediaTask).filter(MediaTask.id == self.task.id).one()
            self.assertEqual(saved.status, "completed")
            self.assertTrue(output.is_file())
            self.assertFalse(partial.exists())
            self.assertEqual(saved.result_data["progress"]["percent"], 100)

    def test_create_job_persists_task_and_schedules_background_worker(self):
        import tempfile
        from pathlib import Path
        from fastapi import BackgroundTasks

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(media, "safe_storage_file", return_value=Path(directory) / "source.mp4"):
                with patch.object(media, "VIDEO_DIR", Path(directory)):
                    with patch.dict(media._LOCAL_RENDER_RUNTIME, {}, clear=True):
                        background = BackgroundTasks()
                        data = media.RenderInput(video_path="source.mp4", output_name="episode final")
                        result = media.create_local_render_job(data, background, self.db)
            self.assertEqual(result["status"], "queued")
            self.assertEqual(len(background.tasks), 1)
            saved = self.db.query(MediaTask).filter(MediaTask.id == result["task_id"]).one()
            self.assertEqual(saved.task_type, "local_render")
            self.assertTrue(saved.result_path.endswith("_episode_final.mp4"))
            self.assertEqual(saved.result_data["progress"]["stage"], "queued")

    def test_processing_cancel_requests_ffmpeg_stop_without_claiming_completed(self):
        import threading

        event = threading.Event()
        with patch.dict(
            media._LOCAL_RENDER_RUNTIME,
            {self.task.id: {"cancel": event, "process": object()}},
            clear=True,
        ):
            result = media.cancel_local_render_job(self.task.id, self.db)
        self.assertEqual(result["status"], "cancellation_requested")
        self.assertTrue(event.is_set())
        self.assertEqual(self.task.status, "processing")

    def test_progress_updates_are_saved_to_media_task(self):
        with patch.object(media, "SessionLocal", self.Session):
            media._update_local_render_job(
                self.task.id,
                progress={"percent": 62.0, "stage": "rendering", "frame": "310"},
            )
        self.db.expire_all()
        saved = self.db.query(MediaTask).filter(MediaTask.id == self.task.id).one()
        self.assertEqual(saved.result_data["progress"]["percent"], 62.0)
        self.assertEqual(saved.result_data["progress"]["frame"], "310")

    def test_status_read_does_not_recover_a_job_that_is_running_here(self):
        with patch.dict(
            media._LOCAL_RENDER_RUNTIME,
            {self.task.id: {"cancel": None, "process": object()}},
            clear=True,
        ):
            result = media.get_local_render_job(self.task.id, self.db)
        self.assertEqual(result["status"], "processing")
        self.assertEqual(self.db.query(MediaTask).filter(MediaTask.id == self.task.id).one().status, "processing")


if __name__ == "__main__":
    unittest.main()
