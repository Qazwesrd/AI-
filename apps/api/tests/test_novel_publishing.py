import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.novel import Novel, Chapter, NovelDistributionPackage, NovelSubmissionRecord
from app.routers.novel_publishing import (
    DistributionPackageInput, SubmissionInput, SubmissionUpdate,
    create_distribution_package, create_submission_record, update_submission_record,
    export_distribution_package, list_novel_distribution_platforms, build_ai_assist_prompt,
)


class NovelPublishingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.novel = Novel(title="测试原创小说", description="测试简介", genre="奇幻")
        self.db.add(self.novel)
        self.db.flush()
        self.db.add(Chapter(novel_id=self.novel.id, chapter_number=1, title="第一章", content="这是原创小说正文。", word_count=8))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_catalog_only_claims_manual_platform_submission(self):
        result = list_novel_distribution_platforms()
        self.assertGreaterEqual(len(result["items"]), 6)
        self.assertTrue(all(item["api_publish"] is False for item in result["items"]))
        self.assertTrue(all(item["revenue_sync"] is False for item in result["items"]))

    def test_create_exportable_distribution_package_and_submission(self):
        result = create_distribution_package(
            self.novel.id,
            DistributionPackageInput(
                platform_id="royalroad", pen_name="Writer", language="en",
                synopsis="A fantasy adventure.", tags=["fantasy"],
                rights_confirmed=True, ai_assistance_disclosed=True,
            ),
            self.db,
        )
        package = result["item"]
        self.assertEqual(package["platform_name"], "Royal Road")
        self.assertEqual(package["status"], "prepared")
        row = self.db.query(NovelDistributionPackage).filter_by(id=package["id"]).one()
        exported = export_distribution_package(row.id, "md", self.db)
        self.assertIn("测试原创小说", exported.body.decode("utf-8"))
        self.assertIn("第一章", exported.body.decode("utf-8"))

        submission = create_submission_record(
            row.id, SubmissionInput(status="submitted", note="作者已在官网提交"), self.db
        )
        self.assertEqual(submission["item"]["status"], "submitted")
        updated = update_submission_record(
            submission["item"]["id"],
            SubmissionUpdate(status="published", external_work_url="https://www.royalroad.com/fiction/123"),
            self.db,
        )
        self.assertEqual(updated["item"]["status"], "published")
        self.assertTrue(updated["item"]["external_work_url"].startswith("https://"))

    def test_ai_assist_prompt_is_prepared_without_automatic_paid_api_call(self):
        result = build_ai_assist_prompt(
            self.novel.id,
            __import__("app.routers.novel_publishing", fromlist=["AIAssistPromptInput"]).AIAssistPromptInput(
                task="outline", target_language="en", platform_id="kdp",
                extra_requirements="Keep the magic system consistent.",
            ),
            self.db,
        )
        self.assertIn("长篇大纲", result["prompt"])
        self.assertIn("Amazon Kindle", result["prompt"])
        self.assertFalse(result["automatic_paid_api_call"])

    def test_published_submission_requires_real_platform_url(self):
        package = create_distribution_package(
            self.novel.id,
            DistributionPackageInput(
                platform_id="fanqie", rights_confirmed=True,
                ai_assistance_disclosed=True,
            ),
            self.db,
        )["item"]
        submission = create_submission_record(
            package["id"], SubmissionInput(status="submitted"), self.db
        )["item"]
        with self.assertRaises(Exception):
            update_submission_record(
                submission["id"], SubmissionUpdate(status="published"), self.db
            )

    def test_rejects_language_not_listed_for_target_platform(self):
        with self.assertRaises(Exception):
            create_distribution_package(
                self.novel.id,
                DistributionPackageInput(
                    platform_id="royalroad", language="zh-CN",
                    rights_confirmed=True, ai_assistance_disclosed=True,
                ),
                self.db,
            )

    def test_package_requires_rights_and_ai_disclosure_acknowledgement(self):
        with self.assertRaises(Exception):
            create_distribution_package(
                self.novel.id,
                DistributionPackageInput(
                    platform_id="fanqie", rights_confirmed=False,
                    ai_assistance_disclosed=True,
                ),
                self.db,
            )
        with self.assertRaises(Exception):
            create_distribution_package(
                self.novel.id,
                DistributionPackageInput(
                    platform_id="fanqie", rights_confirmed=True,
                    ai_assistance_disclosed=False,
                ),
                self.db,
            )


if __name__ == "__main__":
    unittest.main()
