import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.media import MediaTask
from app.routers import media


class LocalRenderJobPersistenceTests(unittest.TestCase):
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
