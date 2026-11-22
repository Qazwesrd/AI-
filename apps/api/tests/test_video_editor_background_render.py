import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.editing import VideoEditJob
from app.models.media import MediaTask, MediaFile
from app.routers import video_editor


class VideoEditorBackgroundRenderTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            self.engine,
            tables=[VideoEditJob.__table__, MediaTask.__table__, MediaFile.__table__],
        )
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.job = VideoEditJob(
            title="后台剪辑测试",
            provider="web_ai",
            model="manual",
            instruction="按时间线剪辑",
            status="planned",
            source_files=["source.mp4"],
            edit_plan={
                "title": "测试成片",
                "segments": [{"file_path": "source.mp4", "start": 0, "end": 5}],
                "width": 1280,
                "height": 720,
            },
        )
        self.db.add(self.job)
        self.db.commit()
        self.db.refresh(self.job)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_execute_endpoint_queues_persistent_local_render_without_ai_call(self):
        background = BackgroundTasks()
        with patch.object(video_editor, "safe_storage_file") as safe_file:
            safe_file.return_value = Path("music.mp3")
            result = video_editor.execute_job(
                self.job.id,
                video_editor.ExecuteEditInput(),
                background,
                self.db,
            )
        task = self.db.query(MediaTask).filter(
            MediaTask.id == result["task_id"]
        ).one()
        self.assertEqual(result["status"], "rendering")
        self.assertEqual(task.task_type, "local_render")
        self.assertEqual(task.status, "queued")
        self.assertEqual(task.request_data["render_kind"], "video_edit")
        self.assertEqual(self.db.query(VideoEditJob).filter(
            VideoEditJob.id == self.job.id
        ).one().status, "rendering")
        self.assertEqual(len(background.tasks), 1)

    def test_background_worker_persists_progress_and_final_media(self):
        task = MediaTask(
            task_type="local_render",
            provider="local_ffmpeg",
            model="video_edit_plan",
            status="queued",
            request_data={"render_kind": "video_edit", "video_edit_job_id": self.job.id},
            result_data={"progress": {"percent": 0, "stage": "queued"}},
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        output = Path(tempfile.gettempdir()) / f"video-editor-{task.id}.mp4"
        output.write_bytes(b"x" * 2048)

        def fake_execute(*args, **kwargs):
            kwargs["progress_callback"]({"percent": 72.0, "stage": "concatenating"})
            return str(output), output.stat().st_size

        with patch.object(video_editor, "SessionLocal", self.Session):
            with patch.object(video_editor, "execute_edit_plan", side_effect=fake_execute):
                with patch.dict(
                    video_editor._LOCAL_RENDER_RUNTIME,
                    {task.id: {"cancel": threading.Event(), "process": None}},
                    clear=True,
                ):
                    video_editor._run_video_edit_render_job(
                        self.job.id, task.id, {}
                    )

        self.db.expire_all()
        saved_task = self.db.query(MediaTask).filter(MediaTask.id == task.id).one()
        saved_job = self.db.query(VideoEditJob).filter(VideoEditJob.id == self.job.id).one()
        self.assertEqual(saved_task.status, "completed")
        self.assertEqual(saved_task.result_data["progress"]["percent"], 100)
        self.assertEqual(saved_job.status, "completed")
        self.assertEqual(saved_job.output_path, str(output))
        self.assertEqual(self.db.query(MediaFile).count(), 1)
        output.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
