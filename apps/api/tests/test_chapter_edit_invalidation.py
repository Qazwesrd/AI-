import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
import app.database as database_module
from app.models.novel import Novel, Chapter, AnalysisItem
from app.models.story import StoryProject
from app.routers.novels import ChapterUpdate, update_chapter
from app.routers.story import AIPlanInput, generate_episode_script_and_shots
from unittest.mock import Mock
from fastapi import HTTPException


class ChapterEditInvalidationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_episode_ai_generation_rejects_stale_project(self):
        episode = Mock(project_id=7, source_text="原章节内容")
        project = Mock(status="source_changed")
        db = Mock()
        db.query.return_value.filter.return_value.first.side_effect = [
            episode,
            project,
        ]

        with self.assertRaises(HTTPException) as raised:
            generate_episode_script_and_shots(3, AIPlanInput(), db)

        self.assertEqual(raised.exception.status_code, 409)

    def test_editing_chapter_marks_analysis_and_story_projects_stale(self):
        novel = Novel(title="测试小说", source_text="第一章\\n旧正文")
        self.db.add(novel)
        self.db.flush()
        chapter = Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="第一章",
            content="旧正文",
            word_count=3,
        )
        analysis = AnalysisItem(
            novel_id=novel.id,
            category="character",
            name="主角",
            data={"aliases": []},
        )
        project = StoryProject(
            novel_id=novel.id,
            name="测试漫剧项目",
            status="planned",
            settings={},
        )
        self.db.add_all([chapter, analysis, project])
        self.db.commit()

        update_chapter(
            chapter.id,
            ChapterUpdate(content="修改后的正文"),
            db=self.db,
        )

        self.db.refresh(novel)
        self.db.refresh(analysis)
        self.db.refresh(project)
        self.assertIn("修改后的正文", novel.source_text)
        self.assertTrue(analysis.data["source_stale"])
        self.assertEqual(analysis.data["source_stale_reason"], "chapter_edited")
        self.assertEqual(project.status, "source_changed")
        self.assertEqual(
            project.settings["source_change_notice"]["status"],
            "pending_review",
        )


    def test_get_novel_returns_chapter_metadata_without_full_bodies(self):
        from app.routers.novels import get_novel

        novel = Novel(
            title="长篇测试",
            source_text="完整原文不应随摘要返回",
        )
        self.db.add(novel)
        self.db.flush()
        self.db.add_all([
            Chapter(
                novel_id=novel.id,
                chapter_number=1,
                title="第一章",
                content="第一章正文",
                word_count=5,
            ),
            Chapter(
                novel_id=novel.id,
                chapter_number=2,
                title="第二章",
                content="第二章正文",
                word_count=5,
            ),
        ])
        self.db.commit()

        result = get_novel(novel.id, db=self.db)

        self.assertNotIn("source_text", result)
        self.assertTrue(result["source_text_available"])
        self.assertEqual(
            result["chapters"],
            [
                {
                    "id": result["chapters"][0]["id"],
                    "novel_id": novel.id,
                    "chapter_number": 1,
                    "title": "第一章",
                    "word_count": 5,
                },
                {
                    "id": result["chapters"][1]["id"],
                    "novel_id": novel.id,
                    "chapter_number": 2,
                    "title": "第二章",
                    "word_count": 5,
                },
            ],
        )

    def test_additive_database_migration_adds_missing_column(self):
        import tempfile
        from pathlib import Path
        from sqlalchemy import text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            legacy_engine = create_engine(
                f"sqlite:///{db_path.as_posix()}",
                future=True,
            )
            with legacy_engine.begin() as connection:
                connection.execute(text(
                    'CREATE TABLE novels (id INTEGER PRIMARY KEY, title VARCHAR(200))'
                ))

            old_engine = database_module.engine
            old_path = database_module.DATABASE_PATH
            database_module.engine = legacy_engine
            database_module.DATABASE_PATH = db_path
            try:
                added = database_module._migrate_missing_columns()
                self.assertTrue(any(item == "novels.description" for item in added))
                with legacy_engine.connect() as connection:
                    columns = {
                        row[1]
                        for row in connection.execute(
                            text("PRAGMA table_info(novels)")
                        )
                    }
                self.assertIn("description", columns)
            finally:
                database_module.engine = old_engine
                database_module.DATABASE_PATH = old_path
                legacy_engine.dispose()


if __name__ == "__main__":
    unittest.main()
