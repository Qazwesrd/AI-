import io
import unittest
import zipfile
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.novel import Novel, Chapter, NovelDistributionPackage, NovelSubmissionRecord, NovelTranslation
from app.routers.novel_publishing import (
    DistributionPackageInput, SubmissionInput, SubmissionUpdate,
    create_distribution_package, create_submission_record, update_submission_record,
    export_distribution_package, list_novel_distribution_platforms, build_ai_assist_prompt,
    TranslationCreateInput, create_translation, translate_next_chunk, get_translation,
    export_translation, _translation_chunks,
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
        self.assertIn("kdp",{item["id"] for item in result["items"]})
        self.assertIn("google_play_books",{item["id"] for item in result["items"]})
        google=next(item for item in result["items"] if item["id"]=="google_play_books")
        self.assertIn("EPUB",google["formats"])
        self.assertIn("PDF",google["formats"])

    def test_create_exportable_distribution_package_and_submission(self):
        result = create_distribution_package(
            self.novel.id,
            DistributionPackageInput(
                platform_id="royalroad", pen_name="Writer", localized_title="A Test Novel", language="en",
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
        self.assertIn("# A Test Novel", exported.body.decode("utf-8"))
        self.assertIn("第一章", exported.body.decode("utf-8"))
        epub=export_distribution_package(row.id,"epub",self.db)
        with zipfile.ZipFile(io.BytesIO(epub.body)) as archive:
            self.assertEqual(archive.read("mimetype"),b"application/epub+zip")
            chapter=archive.read("OEBPS/chapter.xhtml").decode("utf-8")
            self.assertIn("第一章",chapter)
            self.assertIn("这是原创小说正文。",chapter)

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

    def test_translation_chunks_keep_real_paragraph_breaks(self):
        chunks = _translation_chunks("第一段。\n\n第二段。")
        self.assertEqual(chunks, ["第一段。\n\n第二段。"])

    @patch("app.routers.novel_publishing._installed_models", return_value=[{"name":"local-translate-model"}])
    @patch("app.routers.novel_publishing._ollama_request", return_value={"response":"Translated original text."})
    def test_local_translation_saves_separate_edition_without_changing_source(self, mock_generate, mock_models):
        original = self.db.query(Chapter).filter_by(novel_id=self.novel.id).one().content
        created = create_translation(
            self.novel.id,
            TranslationCreateInput(target_language="en",model_name="local-translate-model",glossary={"测试原创小说":"Test Original Novel"}),
            self.db,
        )
        translation_id = created["item"]["id"]
        self.assertEqual(created["item"]["status"],"pending")
        result = translate_next_chunk(translation_id,self.db)
        self.assertEqual(result["item"]["status"],"completed")
        detail = get_translation(translation_id,self.db)
        self.assertIn("Translated original text.",detail["chapters"][0]["translated_text"])
        exported = export_translation(translation_id,"md",self.db)
        self.assertIn("Translated original text.",exported.body.decode("utf-8"))
        self.assertEqual(self.db.query(Chapter).filter_by(novel_id=self.novel.id).one().content,original)
        mock_generate.assert_called_once()

    def test_translation_rejects_uninstalled_local_model(self):
        with patch("app.routers.novel_publishing._installed_models",return_value=[]):
            with self.assertRaises(Exception):
                create_translation(self.novel.id,TranslationCreateInput(target_language="en",model_name="missing-model"),self.db)

    @patch("app.routers.novel_publishing._installed_models", return_value=[{"name":"local-translate-model"}])
    @patch("app.routers.novel_publishing._ollama_request", return_value={"response":"A translated chapter."})
    def test_completed_translation_can_be_used_for_overseas_distribution(self, mock_generate, mock_models):
        created=create_translation(
            self.novel.id,
            TranslationCreateInput(target_language="en",model_name="local-translate-model"),
            self.db,
        )
        translation_id=created["item"]["id"]
        translate_next_chunk(translation_id,self.db)
        package=create_distribution_package(
            self.novel.id,
            DistributionPackageInput(
                platform_id="royalroad",language="en",translation_id=translation_id,
                rights_confirmed=True,ai_assistance_disclosed=True,
            ),
            self.db,
        )["item"]
        row=self.db.query(NovelDistributionPackage).filter_by(id=package["id"]).one()
        self.assertEqual(row.translation_id,translation_id)
        self.assertIn("A translated chapter.",row.manuscript_markdown)
        self.assertNotIn("这是原创小说正文。",row.manuscript_markdown)

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
