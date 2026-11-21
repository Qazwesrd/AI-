import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models.media import MediaFile
from app.models.story import EpisodePlan, StoryProject
from app.routers.media import upload_media


class MediaUploadOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.engine = create_engine(f"sqlite:///{self.root / 'upload.db'}")
        Base.metadata.create_all(
            self.engine,
            tables=[
                StoryProject.__table__,
                EpisodePlan.__table__,
                MediaFile.__table__,
            ],
        )
        self.db = Session(self.engine)
        project = StoryProject(id=1, novel_id=1, name="当前项目")
        self.db.add(project)
        self.db.flush()
        episode = EpisodePlan(
            id=1, project_id=1, episode_number=1, title="第一集"
        )
        self.db.add(episode)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temporary.cleanup()

    def test_episode_audio_upload_persists_project_and_episode_metadata(self):
        audio_dir = self.root / "audio"
        upload = UploadFile(filename="music.mp3", file=io.BytesIO(b"audio-data"))
        with patch("app.routers.media.AUDIO_DIR", audio_dir):
            result = asyncio.run(upload_media(
                file=upload,
                category="audio",
                project_id=1,
                episode_id=1,
                db=self.db,
            ))
        record = self.db.query(MediaFile).filter(MediaFile.id == result["id"]).first()
        self.assertEqual(record.metadata_json["project_id"], 1)
        self.assertEqual(record.metadata_json["episode_id"], 1)
        self.assertEqual(result["project_id"], 1)
        self.assertEqual(result["episode_id"], 1)

    def test_upload_rejects_project_episode_mismatch(self):
        upload = UploadFile(filename="foreign.mp3", file=io.BytesIO(b"audio-data"))
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(upload_media(
                file=upload,
                category="audio",
                project_id=2,
                episode_id=1,
                db=self.db,
            ))
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(self.db.query(MediaFile).count(), 0)


if __name__ == "__main__":
    unittest.main()
