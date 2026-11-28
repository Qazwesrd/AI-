import unittest
from types import SimpleNamespace

from app.routers.ai_novel_analysis import (
    ANALYSIS_CHUNKING_VERSION,
    DEFAULT_ANALYSIS_CHUNK_CHARS,
    make_chunks,
    is_context_length_error,
    split_text_for_context_retry,
    chunk_tree_is_complete,
    append_chunk_tree_results,
)


class AnalysisChunkSizingTests(unittest.TestCase):
    def test_default_chunk_size_is_context_conservative(self):
        self.assertEqual(DEFAULT_ANALYSIS_CHUNK_CHARS, 3000)
        self.assertEqual(ANALYSIS_CHUNKING_VERSION, "context-safe-v3")

    def test_only_explicit_http_400_context_rejections_are_classified(self):
        self.assertTrue(is_context_length_error(400, "prompt is longer than the context length"))
        self.assertTrue(is_context_length_error(400, "maximum context length is 4096 tokens"))
        self.assertFalse(is_context_length_error(500, "context length"))
        self.assertFalse(is_context_length_error(400, "invalid api key"))

    def test_context_retry_split_is_lossless(self):
        source = ("第一段内容。" * 100) + "\\n" + ("第二段内容。" * 100)
        parts = split_text_for_context_retry(source)
        self.assertIsNotNone(parts)
        self.assertEqual(parts[0] + parts[1], source)
        self.assertTrue(parts[0])
        self.assertTrue(parts[1])

    def test_recursive_split_resume_skips_saved_left_branch_and_keeps_order(self):
        saved = {
            "1.1": [{"name": "左侧已完成"}],
            "1.2.1": [{"name": "右侧前半已完成"}],
            "1.2.2": [{"name": "右侧后半已完成"}],
        }
        states = {
            "1": "context_rejected",
            "1.2": "context_rejected",
            "1.1": "completed",
            "1.2.1": "completed",
            "1.2.2": "completed",
        }

        self.assertTrue(chunk_tree_is_complete("1", saved, states))
        output = []
        append_chunk_tree_results("1", saved, states, output)
        self.assertEqual(
            [item["name"] for item in output],
            ["左侧已完成", "右侧前半已完成", "右侧后半已完成"],
        )

    def test_recursive_split_is_incomplete_when_any_leaf_is_missing(self):
        saved = {"2.1": [{"name": "已完成"}]}
        states = {"2": "context_rejected", "2.1": "completed"}
        self.assertFalse(chunk_tree_is_complete("2", saved, states))

    def test_unknown_submission_is_not_mistaken_for_completed_leaf(self):
        saved = {"3.1": [{"name": "已完成"}]}
        states = {"3": "context_rejected", "3.1": "completed", "3.2": "submission_unknown"}
        self.assertFalse(chunk_tree_is_complete("3", saved, states))

    def test_long_chapter_is_split_without_losing_source_text(self):
        source = "小说正文。" * 5000
        chapter = SimpleNamespace(
            chapter_number=1,
            title="测试章节",
            content=source,
        )

        chunks = make_chunks([chapter])

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= DEFAULT_ANALYSIS_CHUNK_CHARS for chunk in chunks))
        self.assertEqual("".join(chunks), f"\n\n【第1章：测试章节】\n{source}")

    def test_multiple_chapters_keep_order_and_headers(self):
        chapters = [
            SimpleNamespace(chapter_number=1, title="第一章", content="甲" * 3500),
            SimpleNamespace(chapter_number=2, title="第二章", content="乙" * 3500),
        ]

        chunks = make_chunks(chapters)
        combined = "".join(chunks)

        self.assertLess(combined.index("【第1章：第一章】"), combined.index("【第2章：第二章】"))
        self.assertEqual(combined.count("甲"), 3500)
        self.assertEqual(combined.count("乙"), 3500)
        self.assertTrue(all(len(chunk) <= DEFAULT_ANALYSIS_CHUNK_CHARS for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
