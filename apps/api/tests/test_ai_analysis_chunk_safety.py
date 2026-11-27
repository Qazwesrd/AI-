import unittest
import json
from unittest.mock import patch
from fastapi import HTTPException

from app.routers import ai_novel_analysis as analysis

from app.routers.ai_novel_analysis import (
    merge_chunk_state,
    split_chunk_state,
    unknown_chunk_keys,
)


class AnalysisChunkSafetyTests(unittest.TestCase):
    def test_chunk_state_metadata_does_not_mix_with_analysis_results(self):
        original = {
            "1": [{"category": "character", "name": "主角"}],
            "__chunk_states__": {"1": "completed", "2": "submission_unknown"},
        }

        results, states = split_chunk_state(original)

        self.assertEqual(results["1"][0]["name"], "主角")
        self.assertNotIn("__chunk_states__", results)
        self.assertEqual(states["2"], "submission_unknown")

        merged = merge_chunk_state(results, states)
        self.assertEqual(merged["1"], results["1"])
        self.assertEqual(merged["__chunk_states__"]["2"], "submission_unknown")

    def test_unknown_chunk_detection_includes_submitting_and_unknown_states(self):
        states = {
            "1": "completed",
            "2": "submitting",
            "3": "submission_unknown",
            "4": "interrupted_unknown",
            "5": "pending",
        }
        self.assertEqual(unknown_chunk_keys(states), ["2", "3", "4"])


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, size=-1):
        return self.payload[:size] if size >= 0 else self.payload


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload
        self.request = None
    def open(self, request, timeout):
        self.request = request
        return FakeResponse(self.payload)


class LocalNovelAnalysisTests(unittest.TestCase):
    def test_local_provider_uses_first_installed_ollama_model(self):
        opener = FakeOpener(b'{"models":[{"name":"qwen2.5:7b"},{"name":"llama3"}]}')
        with patch.object(analysis, "_LOCAL_OLLAMA_OPENER", opener):
            config = analysis.choose_local_provider()
        self.assertEqual(config.provider, "ollama")
        self.assertEqual(config.model, "qwen2.5:7b")
        self.assertEqual(opener.request.full_url, "http://127.0.0.1:11435/api/tags")

    def test_local_provider_fails_closed_when_no_model_is_installed(self):
        opener = FakeOpener(b'{"models":[]}')
        with patch.object(analysis, "_LOCAL_OLLAMA_OPENER", opener):
            with self.assertRaises(HTTPException) as caught:
                analysis.choose_local_provider()
        self.assertEqual(caught.exception.status_code, 404)
        self.assertIn("不会自动下载", caught.exception.detail)

    def test_local_provider_fails_closed_when_ollama_is_unavailable(self):
        class Offline:
            def open(self, request, timeout):
                raise OSError("offline")
        with patch.object(analysis, "_LOCAL_OLLAMA_OPENER", Offline()):
            with self.assertRaises(HTTPException) as caught:
                analysis.choose_local_provider()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("不会切换到云端", caught.exception.detail)

    def test_analysis_chunk_calls_local_ollama_chat_without_authorization_header(self):
        response = {"message": {"content": json.dumps({"items": []}, ensure_ascii=False)}}
        opener = FakeOpener(json.dumps(response).encode("utf-8"))
        config = type("LocalConfig", (), {
            "provider": "ollama", "model": "qwen2.5:7b",
            "base_url": "http://127.0.0.1:11435",
        })()
        with patch.object(analysis, "_LOCAL_OLLAMA_OPENER", opener):
            result = analysis.analyze_chunk(config, "", "正文片段", "第1/1段")
        self.assertEqual(result, [])
        self.assertEqual(opener.request.full_url, "http://127.0.0.1:11435/api/chat")
        self.assertIsNone(opener.request.get_header("Authorization"))
        body = json.loads(opener.request.data.decode("utf-8"))
        self.assertEqual(body["model"], "qwen2.5:7b")
        self.assertFalse(body["stream"])
        self.assertEqual(body["format"], "json")

if __name__ == "__main__":
    unittest.main()
