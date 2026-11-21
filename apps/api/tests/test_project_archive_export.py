import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
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
from app.routers import project_exports


class ProjectArchiveExportTests(unittest.TestCase):
    def test_path_rewriter_only_rewrites_path_fields(self):
        value = {
            "image_path": "/private/storage/character.png",
            "description": "/private/storage/this-is-not-a-path-field",
            "nested": [{"path": "/private/storage/music.wav"}],
            "api_key": "must-not-leak",
            "result_url": "https://example.invalid/signed?token=secret",
        }
        rewritten = project_exports._rewrite_paths(
            value,
            {
                "/private/storage/character.png": "files/00001-character.png",
                "/private/storage/music.wav": "files/00002-music.wav",
            },
        )
        self.assertEqual(rewritten["image_path"], "files/00001-character.png")
        self.assertEqual(rewritten["description"], value["description"])
        self.assertEqual(rewritten["nested"][0]["path"], "files/00002-music.wav")
        self.assertIsNone(rewritten["api_key"])
        self.assertIsNone(rewritten["result_url"])

    def test_export_contains_project_records_and_only_managed_media(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = root / "storage"
            storage.mkdir()
            image = storage / "character.png"
            image.write_bytes(b"fake-image-content")
            music = storage / "music.wav"
            music.write_bytes(b"fake-audio-content")
            video = storage / "shot.mp4"
            video.write_bytes(b"fake-video-content")
            outside = root / "outside-secret.txt"
            outside.write_text("not included", encoding="utf-8")

            engine = create_engine(f"sqlite:///{root / 'test.db'}")
            Base.metadata.create_all(engine)
            db = Session(engine)
            try:
                novel = Novel(title="测试小说", source_text="完整原著正文")
                db.add(novel)
                db.flush()
                chapter = Chapter(
                    novel_id=novel.id,
                    chapter_number=1,
                    title="第一章",
                    content="章节正文",
                    word_count=4,
                )
                project = StoryProject(
                    novel_id=novel.id,
                    name="测试漫剧项目",
                    settings={"api_key": "must-not-export", "ratio": "16:9"},
                )
                db.add_all([chapter, project])
                db.flush()

                asset = VisualAsset(
                    category="character",
                    name="主角",
                    image_path=str(image),
                    reference_path=str(outside),
                    data={"reference_image_path": str(image)},
                )
                episode = EpisodePlan(
                    project_id=project.id,
                    episode_number=1,
                    title="第一集",
                    source_chapter_ids=[chapter.id],
                    source_text="章节正文",
                )
                db.add_all([asset, episode])
                db.flush()

                shot = StoryboardShot(
                    project_id=project.id,
                    episode_id=episode.id,
                    shot_number=1,
                    character_ids=[asset.id],
                )
                media = MediaFile(
                    name="背景音乐",
                    category="audio",
                    file_path=str(music),
                    file_size=music.stat().st_size,
                    metadata_json={"access_token": "must-not-export"},
                )
                db.add_all([shot, media])
                db.flush()
                db.add_all([
                    StoryAssetLink(project_id=project.id, asset_id=asset.id, role="character"),
                    StoryboardMediaLink(
                        project_id=project.id,
                        episode_id=episode.id,
                        shot_id=shot.id,
                        media_file_id=media.id,
                    ),
                    EpisodeEditTimeline(
                        episode_id=episode.id,
                        edit_data={"music_path": str(music)},
                    ),
                    MediaTask(
                        task_type="storyboard_video",
                        status="completed",
                        request_data={"episode_id": episode.id, "shot_id": shot.id},
                        result_path=str(video),
                    ),
                    ImageTask(
                        asset_id=asset.id,
                        prompt="角色参考图",
                        result_path=str(image),
                    ),
                ])
                db.commit()

                def safe_file(raw):
                    candidate = Path(raw).resolve()
                    if storage.resolve() not in candidate.parents:
                        raise ValueError("outside managed storage")
                    if not candidate.is_file():
                        raise FileNotFoundError(raw)
                    return candidate

                with patch.object(project_exports, "safe_storage_file", side_effect=safe_file):
                    archive_path, manifest = project_exports.build_project_archive(
                        project.id, db, export_dir=root / "exports"
                    )

                self.assertTrue(archive_path.is_file())
                self.assertEqual(manifest["file_count"], 3)
                self.assertEqual(manifest["missing_file_count"], 1)
                with zipfile.ZipFile(archive_path, "r") as archive:
                    self.assertIsNone(archive.testzip())
                    names = set(archive.namelist())
                    self.assertIn("project-data.json", names)
                    self.assertIn("manifest.json", names)
                    self.assertEqual(len([name for name in names if name.startswith("files/")]), 3)
                    payload = json.loads(archive.read("project-data.json"))
                    self.assertEqual(payload["project"]["name"], "测试漫剧项目")
                    self.assertIsNone(payload["project"]["settings"]["api_key"])
                    self.assertIsNone(payload["records"]["media_files"][0]["metadata_json"]["access_token"])
                    self.assertEqual(len(payload["records"]["media_tasks"]), 1)
                    self.assertTrue(payload["records"]["media_tasks"][0]["result_path"].startswith("files/"))
                    self.assertTrue(
                        payload["records"]["visual_assets"][0]["image_path"].startswith("files/")
                    )
                    self.assertNotIn(str(outside), archive.read("project-data.json").decode("utf-8"))
            finally:
                db.close()
                engine.dispose()

    def test_missing_project_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = create_engine(f"sqlite:///{Path(temporary) / 'empty.db'}")
            Base.metadata.create_all(engine)
            db = Session(engine)
            try:
                with self.assertRaisesRegex(LookupError, "剧本项目不存在"):
                    project_exports.build_project_archive(999, db, export_dir=Path(temporary) / "out")
            finally:
                db.close()
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
