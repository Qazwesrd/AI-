import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.prompts import (
    PromptCategory, PromptTemplate, PromptVersion, PromptTestRun, PromptRankingSnapshot,
    TrendEvidence, PromptUpgradeCandidate, PublicResearchSource, PublicResearchCollectionRun,
)
from app.routers.prompts import (
    CategoryInput, PromptInput, PromptUpdate, TestInput,
    create_category, create_prompt, update_prompt, prompt_versions,
    add_test_run, refresh_ranking, ranking_history, archive_prompt, ensure_default_prompts,
    EvidenceInput, EvidenceVerifyInput, UpgradeCandidateInput, CandidateDecisionInput,
    create_trend_evidence, verify_trend_evidence, create_upgrade_candidate,
    approve_upgrade_candidate, reject_upgrade_candidate, research_trend_ranking,
    collect_public_source,
)
from app.services.public_source_collector import (
    FeedCollectionError, parse_feed, validate_public_feed_url,
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


    def create_verified_evidence_pair(self):
        first = create_trend_evidence(EvidenceInput(
            platform="红果", work_title="样本漫剧甲", observed_at="2026-11-01",
            metric_summary={"榜单": "TOP20", "互动": "公开页面记录"},
        ), self.db)["item"]
        second = create_trend_evidence(EvidenceInput(
            platform="抖音", work_title="样本漫剧甲", observed_at="2026-11-08",
            metric_summary={"榜单": "持续出现", "互动": "公开页面记录"},
        ), self.db)["item"]
        verify_trend_evidence(first["id"], EvidenceVerifyInput(
            verification_note="已人工核对公开页面与日期。"
        ), self.db)
        verify_trend_evidence(second["id"], EvidenceVerifyInput(
            verification_note="已人工核对另一平台及不同日期。"
        ), self.db)
        return [first["id"], second["id"]]

    def test_upgrade_candidate_requires_verified_multi_platform_multi_date_evidence(self):
        item = self.create_prompt_row()
        first = create_trend_evidence(EvidenceInput(
            platform="红果", work_title="样本", observed_at="2026-11-01"
        ), self.db)["item"]
        second = create_trend_evidence(EvidenceInput(
            platform="红果", work_title="样本", observed_at="2026-11-08"
        ), self.db)["item"]
        verify_trend_evidence(first["id"], EvidenceVerifyInput(
            verification_note="人工核验通过"
        ), self.db)
        verify_trend_evidence(second["id"], EvidenceVerifyInput(
            verification_note="人工核验通过"
        ), self.db)
        with self.assertRaises(Exception):
            create_upgrade_candidate(UpgradeCandidateInput(
                prompt_id=item["id"], base_version=1,
                proposed_name="升级候选", proposed_content="新提示词内容",
                change_reason="根据持续热度样本改进", evidence_ids=[first["id"], second["id"]],
            ), self.db)

    def test_only_explicit_candidate_approval_changes_formal_prompt(self):
        item = self.create_prompt_row()
        evidence_ids = self.create_verified_evidence_pair()
        candidate = create_upgrade_candidate(UpgradeCandidateInput(
            prompt_id=item["id"], base_version=1,
            proposed_name="强悬念剧本提示词",
            proposed_content="新版本：先建立危机，再逐步揭示人物动机。",
            change_reason="多平台、多日期核验后调整开篇冲突结构",
            expected_impact="改善开篇冲突密度与信息递进",
            evidence_ids=evidence_ids,
        ), self.db)["item"]
        unchanged = self.db.query(PromptTemplate).filter_by(id=item["id"]).one()
        self.assertEqual(unchanged.current_version, 1)
        self.assertEqual(unchanged.content, "你是一名专业漫剧编剧。")
        approved = approve_upgrade_candidate(candidate["id"], CandidateDecisionInput(
            decision_note="确认采用该升级"
        ), self.db)
        self.assertEqual(approved["candidate"]["status"], "approved")
        updated = self.db.query(PromptTemplate).filter_by(id=item["id"]).one()
        self.assertEqual(updated.current_version, 2)
        self.assertEqual(updated.content, "新版本：先建立危机，再逐步揭示人物动机。")
        self.assertEqual(self.db.query(PromptVersion).filter_by(prompt_id=item["id"]).count(), 2)

    def test_rejected_candidate_never_changes_formal_prompt(self):
        item = self.create_prompt_row()
        evidence_ids = self.create_verified_evidence_pair()
        candidate = create_upgrade_candidate(UpgradeCandidateInput(
            prompt_id=item["id"], base_version=1,
            proposed_name="暂不采用", proposed_content="不采用的内容",
            change_reason="待更多样本验证", evidence_ids=evidence_ids,
        ), self.db)["item"]
        rejected = reject_upgrade_candidate(candidate["id"], CandidateDecisionInput(
            decision_note="证据仍不足"
        ), self.db)
        self.assertEqual(rejected["item"]["status"], "rejected")
        unchanged = self.db.query(PromptTemplate).filter_by(id=item["id"]).one()
        self.assertEqual(unchanged.current_version, 1)
        self.assertEqual(unchanged.content, "你是一名专业漫剧编剧。")

    def test_public_trend_ranking_requires_verified_cross_platform_cross_day_evidence(self):
        first = create_trend_evidence(EvidenceInput(
            platform="红果", work_title="跨平台样本", observed_at="2026-11-20",
            metric_summary={"views": 12000, "likes": 800, "shares": 90},
            evidence_type="作品趋势证据",
        ), self.db)["item"]
        second = create_trend_evidence(EvidenceInput(
            platform="抖音", work_title="跨平台样本", observed_at="2026-11-26",
            metric_summary={"views": 22000, "likes": 1600, "comments": 120},
            evidence_type="作品趋势证据",
        ), self.db)["item"]
        verify_trend_evidence(first["id"], EvidenceVerifyInput(verification_note="核验公开页面"), self.db)
        verify_trend_evidence(second["id"], EvidenceVerifyInput(verification_note="核验另一平台公开页面"), self.db)
        result = research_trend_ranking(self.db)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["work_title"], "跨平台样本")
        self.assertEqual(result["items"][0]["platform_count"], 2)
        self.assertFalse(result["items"][0]["contains_public_prompt"])
        self.assertIn("尚未接入各平台自动抓取", result["notice"])

    def test_rss_parser_extracts_only_public_metadata(self):
        payload = b"""<?xml version="1.0"?><rss version="2.0"><channel>
        <item><title>公开漫剧更新</title><link>https://www.youtube.com/watch?v=abc</link>
        <pubDate>Fri, 27 Nov 2026 10:00:00 GMT</pubDate><description>公开摘要内容</description></item>
        </channel></rss>"""
        items = parse_feed(payload)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "公开漫剧更新")
        self.assertEqual(items[0]["url"], "https://www.youtube.com/watch?v=abc")
        self.assertLessEqual(len(items[0]["summary_excerpt"]), 300)

    def test_feed_parser_rejects_xml_entities_and_bad_urls(self):
        with self.assertRaises(FeedCollectionError):
            parse_feed(b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><rss/>')
        with self.assertRaises(FeedCollectionError):
            validate_public_feed_url("http://www.youtube.com/feeds/videos.xml")
        with self.assertRaises(FeedCollectionError):
            validate_public_feed_url("https://127.0.0.1/private.xml")
        with self.assertRaises(FeedCollectionError):
            validate_public_feed_url("https://example.com/feed.xml")
        with self.assertRaises(FeedCollectionError):
            validate_public_feed_url("https://www.youtube.com/feeds/videos.xml?channel_id=abc", "抖音")

    def test_public_source_collection_creates_unverified_evidence_and_deduplicates(self):
        source = PublicResearchSource(
            platform="YouTube", name="测试频道订阅",
            feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UCtest",
            source_type="rss_atom", enabled=True, refresh_interval_minutes=60,
        )
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)
        sample = [{
            "title": "自动发现作品", "url": "https://www.youtube.com/watch?v=unique123",
            "published": "Fri, 27 Nov 2026 10:00:00 GMT", "summary_excerpt": "公开摘要",
        }]
        with patch("app.routers.prompts.fetch_public_feed", return_value=sample):
            first = collect_public_source(source.id, self.db)
            second = collect_public_source(source.id, self.db)
        self.assertEqual(first["run"]["inserted_count"], 1)
        self.assertEqual(second["run"]["inserted_count"], 0)
        self.assertEqual(second["run"]["duplicate_count"], 1)
        evidence = self.db.query(TrendEvidence).filter_by(work_title="自动发现作品").one()
        self.assertFalse(evidence.is_verified)
        self.assertEqual(evidence.evidence_type, "公开订阅源发现（作品/创作内容，待人工核验）")
        self.assertEqual(self.db.query(PublicResearchCollectionRun).count(), 2)

    def test_archive_keeps_version_and_test_history(self):
        item = self.create_prompt_row()
        add_test_run(item["id"], TestInput(score_overall=80), self.db)
        archive_prompt(item["id"], self.db)
        self.assertEqual(self.db.query(PromptVersion).filter_by(prompt_id=item["id"]).count(), 1)
        self.assertEqual(self.db.query(PromptTestRun).filter_by(prompt_id=item["id"]).count(), 1)
        self.assertFalse(self.db.query(PromptTemplate).filter_by(id=item["id"]).one().is_active)


if __name__ == "__main__":
    unittest.main()
