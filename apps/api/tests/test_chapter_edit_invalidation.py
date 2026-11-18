import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
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


if __name__ == "__main__":
    unittest.main()
