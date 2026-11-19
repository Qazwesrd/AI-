import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.novel import Novel, Chapter, NovelAnalysisJob
from app.routers.ai_novel_analysis import (
    get_novel_analysis_progress,
    novel_source_signature,
)


class AnalysisProgressSourceRevisionTests(unittest.TestCase):
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

    def create_novel_with_chapter(self, content="当前版本正文"):
        novel = Novel(title="版本测试小说")
        self.db.add(novel)
        self.db.flush()
        chapter = Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="第一章",
            content=content,
            word_count=len(content),
        )
        self.db.add(chapter)
        self.db.commit()
        self.db.refresh(novel)
        return novel

    def test_progress_does_not_report_old_source_job_as_current(self):
        novel = self.create_novel_with_chapter()
        self.db.add(NovelAnalysisJob(
            novel_id=novel.id,
            source_signature="old-source-signature",
            provider="deepseek",
            model="test-model",
            status="completed",
            total_chunks=4,
            completed_chunks=4,
            chunk_results={},
        ))
        self.db.commit()

        result = get_novel_analysis_progress(novel.id, db=self.db)

        self.assertTrue(result["has_job"])
        self.assertFalse(result["source_current"])
        self.assertEqual(result["status"], "stale_source")
        self.assertEqual(result["previous_status"], "completed")
        self.assertEqual(result["progress_percent"], 0)

    def test_progress_returns_job_for_matching_source_revision(self):
        novel = self.create_novel_with_chapter()
        signature = novel_source_signature(novel.chapters)
        self.db.add(NovelAnalysisJob(
            novel_id=novel.id,
            source_signature=signature,
            provider="deepseek",
            model="test-model",
            status="partial",
            total_chunks=4,
            completed_chunks=2,
            chunk_results={},
        ))
        self.db.commit()

        result = get_novel_analysis_progress(novel.id, db=self.db)

        self.assertTrue(result["source_current"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["progress_percent"], 50.0)

    def test_progress_is_not_found_for_missing_novel(self):
        with self.assertRaises(HTTPException) as raised:
            get_novel_analysis_progress(999, db=self.db)
        self.assertEqual(raised.exception.status_code, 404)

    def test_source_signature_is_independent_of_input_order(self):
        novel = self.create_novel_with_chapter()
        chapter = novel.chapters[0]
        self.assertEqual(
            novel_source_signature([chapter]),
            novel_source_signature([chapter]),
        )


if __name__ == "__main__":
    unittest.main()
