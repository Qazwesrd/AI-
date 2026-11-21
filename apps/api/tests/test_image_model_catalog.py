import unittest
from unittest.mock import patch

from app.routers.image_model_catalog import (
    _NoRedirectHandler,
    _head_pinned_file_metadata,
    _preflight_disk_space,
    list_image_model_catalog,
)


class FakeResponse:
    def __init__(self, headers, status=200, body=b""):
        self.headers = headers
        self.status = status
        self.code = status
        self.body = body
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        if size < 0:
            size = len(self.body)
        result, self.body = self.body[:size], self.body[size:]
        return result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.timeouts = []

    def open(self, request, timeout):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if not self.responses:
            raise AssertionError("No fake response configured")
        return self.responses.pop(0)


class ImageModelCatalogTests(unittest.TestCase):
    def test_catalog_is_pinned_and_download_is_disabled(self):
        result = list_image_model_catalog()
        self.assertEqual(result["count"], 1)
        item = result["models"][0]
        self.assertEqual(item["revision"], "ce8e3e3657a9b767b33fc0171ff91fb04aed1b2f")
        self.assertEqual(item["filename"], "v1-5-pruned-emaonly-fp16.safetensors")
        self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
        self.assertFalse(item["sha256_verified"])
        self.assertFalse(item["download_eligible"])
        self.assertFalse(item["install_eligible"])
        self.assertEqual(len(item["eligibility_blockers"]), 3)
        self.assertIsNone(item["size_bytes"])
        self.assertEqual(item["license_status"], "review_required")
        self.assertEqual(item["license_url"], "https://raw.githubusercontent.com/CompVis/stable-diffusion/main/LICENSE")
        self.assertEqual(item["hardware_status"], "cpu_not_recommended")
        self.assertFalse(result["network_used"])
        self.assertFalse(result["download_started"])
        self.assertFalse(result["installation_started"])
        self.assertFalse(result["download_available"])
        self.assertFalse(result["install_available"])

    def test_catalog_returns_independent_objects(self):
        first = list_image_model_catalog()
        first["models"][0]["name"] = "tampered"
        second = list_image_model_catalog()
        self.assertNotEqual(second["models"][0]["name"], "tampered")

    def test_metadata_head_reads_linked_size_without_following_redirect(self):
        opener = FakeOpener(FakeResponse({
            "X-Linked-Size": "2280000000",
            "X-Linked-Etag": "sha256:upstream-etag",
        }, status=302))
        url = "https://huggingface.co/Comfy-Org/stable-diffusion-1.5/resolve/ce8e3e3657a9b767b33fc0171ff91fb04aed1b2f/v1-5-pruned-emaonly-fp16.safetensors"
        result = _head_pinned_file_metadata(url, opener=opener)
        self.assertEqual(result["size_bytes"], 2280000000)
        self.assertTrue(result["etag_present"])
        self.assertEqual(opener.requests[0].get_method(), "HEAD")
        self.assertEqual(opener.timeouts[0], 12)

    def test_metadata_head_fails_closed_without_exact_size(self):
        opener = FakeOpener(FakeResponse({"ETag": "opaque"}), FakeResponse({"Content-Range": "bytes 0-0/*"}, status=206, body=b"x"))
        url = "https://huggingface.co/Comfy-Org/stable-diffusion-1.5/resolve/ce8e3e3657a9b767b33fc0171ff91fb04aed1b2f/v1-5-pruned-emaonly-fp16.safetensors"
        with self.assertRaisesRegex(ValueError, "精确文件大小"):
            _head_pinned_file_metadata(url, opener=opener)

    def test_metadata_falls_back_to_single_byte_range_for_exact_size(self):
        opener = FakeOpener(
            FakeResponse({"ETag": "opaque"}, status=200),
            FakeResponse({"Content-Range": "bytes 0-0/2280000000"}, status=206, body=b"x"),
        )
        url = "https://huggingface.co/Comfy-Org/stable-diffusion-1.5/resolve/ce8e3e3657a9b767b33fc0171ff91fb04aed1b2f/v1-5-pruned-emaonly-fp16.safetensors"
        result = _head_pinned_file_metadata(url, opener=opener)
        self.assertEqual(result["size_bytes"], 2280000000)
        self.assertEqual(opener.requests[0].get_method(), "HEAD")
        self.assertEqual(opener.requests[1].get_method(), "GET")
        self.assertEqual(opener.requests[1].get_header("Range"), "bytes=0-0")

    def test_metadata_rejects_server_ignoring_range_request(self):
        opener = FakeOpener(
            FakeResponse({"ETag": "opaque"}, status=200),
            FakeResponse({"Content-Length": "100"}, status=200, body=b"too much"),
        )
        url = "https://huggingface.co/Comfy-Org/stable-diffusion-1.5/resolve/ce8e3e3657a9b767b33fc0171ff91fb04aed1b2f/v1-5-pruned-emaonly-fp16.safetensors"
        with self.assertRaisesRegex(ValueError, "未遵守单字节范围请求"):
            _head_pinned_file_metadata(url, opener=opener)

    def test_metadata_url_is_pinned_and_rejects_untrusted_hosts(self):
        with self.assertRaisesRegex(ValueError, "白名单"):
            _head_pinned_file_metadata("https://example.com/anything")

    def test_redirect_handler_never_follows_redirects(self):
        handler = _NoRedirectHandler()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example"))

    def test_preflight_disk_space_includes_safety_margin(self):
        with patch("app.routers.image_model_catalog.shutil.disk_usage") as disk_usage:
            disk_usage.return_value = type("Usage", (), {"free": 3 * 1024 ** 3})()
            result = _preflight_disk_space(__import__("pathlib").Path("/not-created/models"), 2 * 1024 ** 3)
        self.assertEqual(result["required_bytes"], 3 * 1024 ** 3)
        self.assertTrue(result["sufficient"])


if __name__ == "__main__":
    unittest.main()
