import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.routers.local_ai_runtime import (
    LocalModelTestInput,
    LocalCreativeInput,
    LocalSpecializedModelInput,
    create_local_specialized_model,
    generate_local_creative_content,
    _installed_models,
    local_ai_runtime,
    test_local_model,
    get_local_novel_context,
    _bounded_continuity,
    LocalEpisodePackageInput,
    generate_local_episode_package,
    _ollama_request,
)


class LocalAIRuntimeTests(unittest.TestCase):
    def test_ollama_inference_timeout_is_not_reported_as_disconnected(self):
        with patch("app.routers.local_ai_runtime.OPENER.open", side_effect=TimeoutError()):
            with self.assertRaises(HTTPException) as caught:
                _ollama_request("/api/generate", {"model": "qwen2.5:3b"}, timeout=1800)
        self.assertEqual(caught.exception.status_code, 504)
        self.assertIn("推理等待超时", caught.exception.detail)

    def test_urllib_wrapped_socket_timeout_is_not_reported_as_disconnected(self):
        from urllib.error import URLError
        import socket
        wrapped_timeout = URLError(socket.timeout("read timed out"))
        with patch("app.routers.local_ai_runtime.OPENER.open", side_effect=wrapped_timeout):
            with self.assertRaises(HTTPException) as caught:
                _ollama_request("/api/generate", {"model": "qwen2.5:3b"}, timeout=1800)
        self.assertEqual(caught.exception.status_code, 504)
        self.assertIn("等待响应", caught.exception.detail)
        self.assertNotIn("无法连接本机 Ollama", caught.exception.detail)

    def test_ollama_connection_failure_has_distinct_message(self):
        from urllib.error import URLError
        with patch("app.routers.local_ai_runtime.OPENER.open", side_effect=URLError("refused")):
            with self.assertRaises(HTTPException) as caught:
                _ollama_request("/api/tags")
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("无法连接本机 Ollama", caught.exception.detail)

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


    def test_episode_package_runs_five_local_stages_and_carries_previous_context(self):
        payload = LocalEpisodePackageInput(model="qwen2.5:3b", style="国漫", source_text="主角进入神殿。")
        generated = [{"response": f"阶段{i}成果"} for i in range(1, 6)]
        with patch("app.routers.local_ai_runtime._installed_models", return_value=[{"name": "qwen2.5:3b"}]), patch(
            "app.routers.local_ai_runtime._ollama_request", side_effect=generated
        ) as request:
            result = generate_local_episode_package(payload)
        self.assertEqual(result["stage_count"], 5)
        self.assertEqual([x["stage"] for x in result["stages"]], ["screenplay", "character", "plot", "storyboard", "image_prompt"])
        self.assertFalse(result["network_used"])
        self.assertFalse(result["cloud_fallback"])
        self.assertFalse(result["saved_automatically"])
        self.assertEqual(request.call_count, 5)
        self.assertIn("阶段1成果", request.call_args_list[3].args[1]["prompt"])
        self.assertIn("阶段2成果", request.call_args_list[3].args[1]["prompt"])

    def test_episode_package_rejects_missing_model(self):
        payload = LocalEpisodePackageInput(model="missing", source_text="章节")
        with patch("app.routers.local_ai_runtime._installed_models", return_value=[]), patch(
            "app.routers.local_ai_runtime._ollama_request"
        ) as request:
            with self.assertRaises(HTTPException) as caught:
                generate_local_episode_package(payload)
        self.assertEqual(caught.exception.status_code, 404)
        request.assert_not_called()


    def test_package_progress_events_emit_each_completed_stage(self):
        from app.routers.local_ai_runtime import _episode_package_progress_events
        payload = LocalEpisodePackageInput(model="qwen2.5:3b", source_text="章节")
        generated = [{"response": f"阶段{i}成果"} for i in range(1, 6)]
        with patch("app.routers.local_ai_runtime._installed_models", return_value=[{"name":"qwen2.5:3b"}]), patch(
            "app.routers.local_ai_runtime._ollama_request", side_effect=generated
        ) as request:
            events = [__import__("json").loads(line) for line in _episode_package_progress_events(payload)]
        done = [event for event in events if event["type"] == "stage_completed"]
        self.assertEqual(request.call_count, 5)
        self.assertEqual([event["completed"] for event in done], [1,2,3,4,5])
        self.assertEqual(events[-1]["type"], "completed")
        self.assertFalse(events[-1]["cloud_fallback"])

    def test_package_progress_keeps_completed_stage_if_next_stage_times_out(self):
        from app.routers.local_ai_runtime import _episode_package_progress_events
        payload = LocalEpisodePackageInput(model="qwen2.5:3b", source_text="章节")
        with patch("app.routers.local_ai_runtime._installed_models", return_value=[{"name":"qwen2.5:3b"}]), patch(
            "app.routers.local_ai_runtime._ollama_request", side_effect=[{"response":"已完成第一阶段"}, HTTPException(504,"推理等待超时")]
        ):
            events = [__import__("json").loads(line) for line in _episode_package_progress_events(payload)]
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["completed"], 1)
        self.assertEqual(events[-1]["stages"][0]["content"], "已完成第一阶段")
        self.assertEqual(events[-1]["status"], 504)


    def test_ollama_stream_yields_fragments_and_requests_stream_mode(self):
        from app.routers.local_ai_runtime import _ollama_stream
        class Response:
            def __init__(self):
                self.lines = [
                    b'{"response":"\xe4\xbd\xa0","done":false}\n',
                    b'{"response":"\xe5\xa5\xbd","done":false}\n',
                    b'{"response":"","done":true}\n',
                ]
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def readline(self, limit):
                return self.lines.pop(0) if self.lines else b""
        with patch("app.routers.local_ai_runtime.OPENER.open", return_value=Response()) as opener:
            self.assertEqual(list(_ollama_stream({"model":"qwen2.5:3b","prompt":"hi","stream":False})), ["你","好"])
        request = opener.call_args.args[0]
        body = __import__("json").loads(request.data.decode("utf-8"))
        self.assertTrue(body["stream"])
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/generate")

    def test_ollama_stream_maps_midstream_timeout_to_504(self):
        from app.routers.local_ai_runtime import _ollama_stream
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def readline(self, limit): raise TimeoutError("slow model")
        with patch("app.routers.local_ai_runtime.OPENER.open", return_value=Response()):
            with self.assertRaises(HTTPException) as caught:
                list(_ollama_stream({"model":"qwen2.5:3b","prompt":"hi"}))
        self.assertEqual(caught.exception.status_code, 504)



if __name__ == "__main__":
    unittest.main()
