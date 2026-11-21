import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.routers.local_ai_runtime import (
    LocalModelTestInput,
    LocalCreativeInput,
    generate_local_creative_content,
    _installed_models,
    local_ai_runtime,
    test_local_model,
    get_local_novel_context,
    _bounded_continuity,
)


class LocalAIRuntimeTests(unittest.TestCase):
    def test_lists_only_valid_local_model_names(self):
        with patch(
            "app.routers.local_ai_runtime._ollama_request",
            return_value={"models": [
                {"name": "qwen2.5:3b", "size": 123},
                {"name": "../outside", "size": 456},
                {"name": "llama3.2:latest", "size": 789},
                "bad-entry",
            ]},
        ):
            models = _installed_models()
        self.assertEqual([item["name"] for item in models], ["qwen2.5:3b", "llama3.2:latest"])

    def test_runtime_reports_not_running_without_cloud_fallback(self):
        with patch(
            "app.routers.local_ai_runtime._installed_models",
            side_effect=HTTPException(503, "not running"),
        ):
            result = local_ai_runtime()
        self.assertEqual(result["status"], "not_running")
        self.assertFalse(result["cloud_fallback"])
        self.assertFalse(result["downloaded"])

    def test_test_requires_model_already_installed(self):
        with patch(
            "app.routers.local_ai_runtime._installed_models",
            return_value=[{"name": "qwen2.5:3b"}],
        ), patch(
            "app.routers.local_ai_runtime._ollama_request"
        ) as request:
            with self.assertRaises(HTTPException) as caught:
                test_local_model(LocalModelTestInput(model="missing:latest"))
        self.assertEqual(caught.exception.status_code, 404)
        request.assert_not_called()

    def test_test_uses_fixed_small_prompt_and_no_cloud(self):
        with patch(
            "app.routers.local_ai_runtime._installed_models",
            return_value=[{"name": "qwen2.5:3b"}],
        ), patch(
            "app.routers.local_ai_runtime._ollama_request",
            return_value={"response": "本机运行成功"},
        ) as request:
            result = test_local_model(LocalModelTestInput(model="qwen2.5:3b"))
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["network_used"])
        self.assertFalse(result["cloud_fallback"])
        payload = request.call_args.args[1]
        self.assertEqual(payload["model"], "qwen2.5:3b")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["options"]["num_predict"], 48)

    def test_rejects_invalid_model_name(self):
        with self.assertRaises(HTTPException) as caught:
            test_local_model(LocalModelTestInput(model="../bad"))
        self.assertEqual(caught.exception.status_code, 400)

    def test_creative_generation_requires_preinstalled_model(self):
        payload = LocalCreativeInput(model="missing", source_text="故事梗概")
        with patch("app.routers.local_ai_runtime._installed_models", return_value=[]), patch(
            "app.routers.local_ai_runtime._ollama_request"
        ) as request:
            with self.assertRaises(HTTPException) as caught:
                generate_local_creative_content(payload)
        self.assertEqual(caught.exception.status_code, 404)
        request.assert_not_called()

    def test_creative_generation_uses_only_local_model_and_selected_style(self):
        payload = LocalCreativeInput(
            model="qwen2.5:3b",
            task="storyboard",
            style="3D漫",
            source_text="主角走进古老神殿。",
            extra_requirements="保持服装连续性",
        )
        with patch(
            "app.routers.local_ai_runtime._installed_models",
            return_value=[{"name": "qwen2.5:3b"}],
        ), patch(
            "app.routers.local_ai_runtime._ollama_request",
            return_value={"response": "镜头1：远景……"},
        ) as request:
            result = generate_local_creative_content(payload)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["style"], "3D漫")
        self.assertFalse(result["network_used"])
        self.assertFalse(result["cloud_fallback"])
        self.assertFalse(result["saved_automatically"])
        body = request.call_args.args[1]
        self.assertEqual(body["model"], "qwen2.5:3b")
        self.assertIn("3D漫", body["system"])
        self.assertIn("保持服装连续性", body["prompt"])


    def test_context_endpoint_rejects_chapter_from_another_novel(self):
        from app.models.novel import Novel
        class Query:
            def __init__(self, value): self.value = value
            def filter(self, *args): return self
            def first(self): return self.value
        class DB:
            def query(self, model):
                return Query(type("NovelRow", (), {"id": 1, "title": "测试"})() if model is Novel else None)
            def close(self): pass
        with patch("app.database.SessionLocal", return_value=DB()):
            with self.assertRaises(HTTPException) as caught:
                get_local_novel_context(1, 999)
        self.assertEqual(caught.exception.status_code, 404)

    def test_context_endpoint_limits_chapter_and_excludes_stale_notes(self):
        from app.models.novel import AnalysisItem, Chapter, Novel
        novel = type("NovelRow", (), {"id": 1, "title": "测试小说"})()
        chapter = type("ChapterRow", (), {"id": 2, "chapter_number": 1, "title": "第一章", "content": "正文" * 7000, "word_count": 14000})()
        items = [
            type("Item", (), {"category": "人物", "name": "主角", "description": "已确认", "data": {"age": 18}})(),
            type("Item", (), {"category": "人物", "name": "旧资料", "description": "过期", "data": {"source_stale": True}})(),
        ]
        class Query:
            def __init__(self, model): self.model = model
            def filter(self, *args): return self
            def order_by(self, *args): return self
            def limit(self, count): return self
            def first(self): return novel if self.model is Novel else chapter
            def all(self): return items
        class DB:
            def query(self, model): return Query(model)
            def close(self): pass
        with patch("app.database.SessionLocal", return_value=DB()):
            result = get_local_novel_context(1, 2)
        self.assertEqual(len(result["content"]), 12000)
        self.assertTrue(result["source_text_truncated"])
        self.assertEqual(result["continuity_count"], 1)
        self.assertEqual(result["confirmed_continuity"][0]["name"], "主角")
        self.assertFalse(result["cloud_fallback"])
        self.assertFalse(result["network_used"])

    def test_continuity_context_has_a_strict_character_budget(self):
        items = [type("Item", (), {"category": "人物", "name": f"人物{i}", "description": "设定" * 1000, "data": {}})() for i in range(20)]
        result, truncated = _bounded_continuity(items, max_chars=1200)
        self.assertTrue(truncated)
        self.assertLessEqual(sum(len(__import__("json").dumps(x, ensure_ascii=False)) for x in result), 1200)


if __name__ == "__main__":
    unittest.main()
