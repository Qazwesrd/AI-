import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.prompts import (
    PromptCategory, PromptTemplate, PromptVersion, PromptTestRun, PromptRankingSnapshot,
)
from app.routers.prompts import (
    CategoryInput, PromptInput, PromptUpdate, TestInput,
    create_category, create_prompt, update_prompt, prompt_versions,
    add_test_run, refresh_ranking, ranking_history, archive_prompt,
)


class PromptCenterTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.category = create_category(CategoryInput(name="自定义测试分类"), self.db)["item"]

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def create_prompt_row(self):
        return create_prompt(PromptInput(
            name="测试提示词",
            category_id=self.category["id"],
            content="你是一名专业漫剧编剧。",
            notes="测试备注",
        ), self.db)["item"]

    def test_builtin_prompts_seed_once_and_are_editable_versions(self):
        ensure_default_prompts(self.db)
        first_count = self.db.query(PromptTemplate).count()
        ensure_default_prompts(self.db)
        second_count = self.db.query(PromptTemplate).count()
        self.assertGreaterEqual(first_count, 8)
        self.assertEqual(first_count, second_count)
        builtins = self.db.query(PromptTemplate).filter(PromptTemplate.is_system.is_(True)).all()
        self.assertGreaterEqual(len(builtins), 8)
        self.assertTrue(all(self.db.query(PromptVersion).filter_by(prompt_id=x.id).count() == 1 for x in builtins))

    def test_create_prompt_records_initial_immutable_version(self):
        item = self.create_prompt_row()
        self.assertEqual(item["current_version"], 1)
        history = prompt_versions(item["id"], self.db)["items"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["content"], "你是一名专业漫剧编剧。")

    def test_edit_creates_new_version_and_preserves_old_version(self):
        item = self.create_prompt_row()
        update_prompt(item["id"], PromptUpdate(
            content="你是一名擅长悬念结构的漫剧编剧。",
            change_reason="增强开篇悬念",
        ), self.db)
        history = prompt_versions(item["id"], self.db)["items"]
        self.assertEqual([x["version"] for x in history], [2, 1])
        self.assertEqual(history[1]["content"], "你是一名专业漫剧编剧。")
        self.assertEqual(history[0]["content"], "你是一名擅长悬念结构的漫剧编剧。")

    def test_manual_test_scores_are_versioned_and_ranking_refresh_snapshots(self):
        item = self.create_prompt_row()
        result = add_test_run(item["id"], TestInput(
            dataset_name="同一小说样本A",
            model_name="本地模型",
            score_overall=92,
            score_story=90,
            score_consistency=95,
            score_executability=88,
            score_visual=91,
        ), self.db)
        self.assertTrue(result["success"])
        ranking = refresh_ranking("overall", self.db)
        self.assertEqual(ranking["items"][0]["prompt_id"], item["id"])
        self.assertEqual(ranking["items"][0]["average_score"], 92)
        history = ranking_history("overall", self.db)["items"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["ranking"][0]["version"], 1)

    def test_ranking_refresh_does_not_modify_formal_prompt(self):
        item = self.create_prompt_row()
        add_test_run(item["id"], TestInput(score_overall=75), self.db)
        refresh_ranking("overall", self.db)
        current = self.db.query(PromptTemplate).filter(PromptTemplate.id == item["id"]).one()
        self.assertEqual(current.current_version, 1)
        self.assertEqual(current.content, "你是一名专业漫剧编剧。")

    def test_archive_keeps_version_and_test_history(self):
        item = self.create_prompt_row()
        add_test_run(item["id"], TestInput(score_overall=80), self.db)
        archive_prompt(item["id"], self.db)
        self.assertEqual(self.db.query(PromptVersion).filter_by(prompt_id=item["id"]).count(), 1)
        self.assertEqual(self.db.query(PromptTestRun).filter_by(prompt_id=item["id"]).count(), 1)
        self.assertFalse(self.db.query(PromptTemplate).filter_by(id=item["id"]).one().is_active)


if __name__ == "__main__":
    unittest.main()
