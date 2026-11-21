import unittest

from app.routers.comfyui_installer import (
    GIB, ENGINE_RESERVE_BYTES, MODEL_RESERVE_BYTES, assess_comfyui_install, parse_official_release,
    classify_gpu_vendor, parse_windows_video_adapters, recommend_official_asset,
)


class ComfyUIInstallReadinessTests(unittest.TestCase):
    def test_low_disk_blocks_engine_install(self):
        result = assess_comfyui_install(
            {"memory": {"total_bytes": 32 * GIB}, "gpu": {"vram_bytes": 8 * GIB}},
            7 * GIB,
        )
        self.assertFalse(result["can_install_engine"])
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["download_started"])

    def test_engine_can_fit_but_model_reserve_warns(self):
        result = assess_comfyui_install(
            {"memory": {"total_bytes": 16 * GIB}, "gpu": {"vram_bytes": 6 * GIB}},
            ENGINE_RESERVE_BYTES + 1,
        )
        self.assertTrue(result["can_install_engine"])
        self.assertTrue(any("不足以同时预留" in item for item in result["warnings"]))

    def test_unknown_hardware_requires_manual_review(self):
        result = assess_comfyui_install({"memory": {}, "gpu": {}}, None)
        self.assertEqual(result["status"], "needs_manual_check")
        self.assertEqual(result["hardware_profile"], "unknown")

    def test_large_storage_separates_engine_and_optional_model(self):
        result = assess_comfyui_install(
            {"memory": {"total_bytes": 32 * GIB}, "gpu": {"vram_bytes": 16 * GIB}},
            ENGINE_RESERVE_BYTES + MODEL_RESERVE_BYTES,
        )
        self.assertTrue(result["can_install_engine"])
        self.assertEqual(result["recommended_total_reserve_bytes"], 23 * GIB)
        self.assertFalse(result["network_used"])


    def test_gpu_vendor_classification_prefers_pnp_vendor_id(self):
        self.assertEqual(classify_gpu_vendor("Generic Display Adapter", "PCI\\\\VEN_10DE&DEV_2684"), "NVIDIA")
        self.assertEqual(classify_gpu_vendor("Generic Display Adapter", "PCI\\\\VEN_1002&DEV_744C"), "AMD")
        self.assertEqual(classify_gpu_vendor("Generic Display Adapter", "PCI\\\\VEN_8086&DEV_56A0"), "Intel")
        self.assertEqual(classify_gpu_vendor("Mystery Adapter", None), "unknown")

    def test_windows_adapter_parser_does_not_trust_adapter_ram(self):
        adapters = parse_windows_video_adapters([
            {"Name": "NVIDIA GeForce RTX", "PNPDeviceID": "PCI\\\\VEN_10DE", "AdapterRAM": 4294967295},
            {"Name": "Intel UHD Graphics", "PNPDeviceID": "PCI\\\\VEN_8086", "AdapterRAM": 1073741824},
        ])
        self.assertEqual([item["vendor"] for item in adapters], ["NVIDIA", "Intel"])
        self.assertIsNone(adapters[0]["vram_bytes"])
        self.assertIsNone(adapters[1]["vram_bytes"])

    def test_recommends_asset_for_detected_gpu_and_architecture(self):
        self.assertEqual(recommend_official_asset("NVIDIA", "NVIDIA GeForce GTX 1080 Ti", "AMD64"), "ComfyUI_windows_portable_nvidia_cu126.7z")
        self.assertEqual(recommend_official_asset("NVIDIA", "NVIDIA GeForce RTX 4080", "AMD64"), "ComfyUI_windows_portable_nvidia.7z")
        self.assertEqual(recommend_official_asset("NVIDIA", "NVIDIA GPU", "ARM64"), "ComfyUI_windows_portable_nvidia_arm64.7z")
        self.assertEqual(recommend_official_asset("AMD", "AMD Radeon RX 7900", "AMD64"), "ComfyUI_windows_portable_amd.7z")
        self.assertEqual(recommend_official_asset("Intel", "Intel Arc", "AMD64"), "ComfyUI_windows_portable_intel.7z")
        self.assertIsNone(recommend_official_asset("unknown", "Unknown GPU", "AMD64"))

    def test_release_catalog_only_accepts_official_assets_and_urls(self):
        payload = {
            "tag_name": "v-test",
            "published_at": "2026-11-21T00:00:00Z",
            "assets": [
                {
                    "name": "ComfyUI_windows_portable_nvidia.7z",
                    "browser_download_url": "https://github.com/Comfy-Org/ComfyUI/releases/download/v-test/ComfyUI_windows_portable_nvidia.7z",
                    "size": 123456,
                    "digest": "sha256:" + "a" * 64,
                },
                {
                    "name": "ComfyUI_windows_portable_amd.7z",
                    "browser_download_url": "https://evil.example/file.7z",
                    "size": 999,
                },
                {
                    "name": "ComfyUI_windows_portable_intel.7z",
                    "browser_download_url": "https://github.com/Comfy-Org/ComfyUI/releases/download/v-test/ComfyUI_windows_portable_intel.7z.evil",
                    "size": 100,
                    "digest": "sha256:not-a-real-digest",
                },
                {
                    "name": "ComfyUI_windows_portable_nvidia_arm64.7z",
                    "browser_download_url": "https://github.com/Comfy-Org/ComfyUI/releases/download/v-test/ComfyUI_windows_portable_nvidia_arm64.7z",
                    "size": 456,
                    "digest": "sha256:" + "b" * 64,
                },
                {
                    "name": "malicious.exe",
                    "browser_download_url": "https://github.com/Comfy-Org/ComfyUI/releases/download/v-test/malicious.exe",
                    "size": 100,
                },
            ],
        }
        result = parse_official_release(payload)
        self.assertEqual(result["version"], "v-test")
        self.assertEqual(len(result["assets"]), 2)
        self.assertEqual(result["assets"][0]["size_bytes"], 123456)
        self.assertEqual(result["assets"][0]["sha256"], "a" * 64)
        self.assertFalse(result["download_started"])
        self.assertFalse(result["installation_started"])

    def test_release_catalog_handles_missing_digest_without_claiming_verified(self):
        payload = {
            "tag_name": "v-test",
            "assets": [{
                "name": "ComfyUI_windows_portable_intel.7z",
                "browser_download_url": "https://github.com/Comfy-Org/ComfyUI/releases/download/v-test/ComfyUI_windows_portable_intel.7z",
                "size": 500,
            }],
        }
        result = parse_official_release(payload)
        self.assertEqual(len(result["assets"]), 1)
        self.assertFalse(result["assets"][0]["digest_available"])
        self.assertIsNone(result["assets"][0]["sha256"])

    def test_release_catalog_rejects_invalid_release_payload(self):
        with self.assertRaises(ValueError):
            parse_official_release({"assets": []})


    def test_install_asset_requires_official_url_size_and_sha256(self):
        from app.routers.comfyui_installer import validate_official_asset_for_install
        asset = {
            "name": "ComfyUI_windows_portable_nvidia.7z",
            "download_url": "https://github.com/Comfy-Org/ComfyUI/releases/download/v1/ComfyUI_windows_portable_nvidia.7z",
            "size_bytes": 1024,
            "sha256": "a" * 64,
        }
        self.assertEqual(validate_official_asset_for_install(asset, "v1")["sha256"], "a" * 64)
        for changed in (
            {**asset, "sha256": None},
            {**asset, "download_url": "https://evil.example/file.7z"},
            {**asset, "size_bytes": 0},
            {**asset, "download_url": asset["download_url"] + "?redirect=1"},
        ):
            with self.assertRaises(ValueError):
                validate_official_asset_for_install(changed, "v1")

    def test_download_response_requires_exact_length_and_blocks_redirects(self):
        from app.routers.comfyui_installer import validate_download_response
        result = validate_download_response(200, {"Content-Length": "100"}, 100)
        self.assertEqual(result["expected_response_bytes"], 100)
        with self.assertRaises(ValueError):
            validate_download_response(302, {"Location": "https://evil.example"}, 100)
        with self.assertRaises(ValueError):
            validate_download_response(200, {"Content-Length": "99"}, 100)

    def test_resume_requires_matching_range_and_etag(self):
        from app.routers.comfyui_installer import validate_download_response
        result = validate_download_response(
            206, {"Content-Range": "bytes 50-99/100", "Content-Length": "50", "ETag": '"v1"'},
            100, resume_offset=50, expected_etag='"v1"',
        )
        self.assertTrue(result["resume_allowed"])
        with self.assertRaises(ValueError):
            validate_download_response(
                206, {"Content-Range": "bytes 40-99/100", "ETag": '"v1"'},
                100, resume_offset=50, expected_etag='"v1"',
            )
        with self.assertRaises(ValueError):
            validate_download_response(
                206, {"Content-Range": "bytes 50-99/100", "ETag": '"v2"'},
                100, resume_offset=50, expected_etag='"v1"',
            )

    def test_archive_manifest_rejects_traversal_links_and_oversized_files(self):
        from app.routers.comfyui_installer import validate_archive_entries
        safe = validate_archive_entries([{"path": "ComfyUI/main.py", "size": 100}])
        self.assertEqual(safe[0]["path"], "ComfyUI/main.py")
        for entry in (
            {"path": "../outside.txt", "size": 1},
            {"path": "C:\\outside.txt", "size": 1},
            {"path": "ComfyUI/link", "size": 1, "is_symlink": True},
        ):
            with self.assertRaises(ValueError):
                validate_archive_entries([entry])

if __name__ == "__main__":
    unittest.main()
