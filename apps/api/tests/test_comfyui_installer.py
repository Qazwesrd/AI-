import unittest

from app.routers.comfyui_installer import (
    GIB, ENGINE_RESERVE_BYTES, MODEL_RESERVE_BYTES, assess_comfyui_install, parse_official_release,
    classify_gpu_vendor, parse_windows_video_adapters, recommend_official_asset, recommend_runtime_mode,
)


class ComfyUIInstallReadinessTests(unittest.TestCase):
    def test_offline_installer_accepts_only_internal_validation_manifest(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[3]
        prepare = (root / "scripts" / "prepare-comfyui-offline-bundle.ps1").read_text(encoding="utf-8")
        # Every PowerShell script embedded in the offline payload must carry a UTF-8 BOM,
        # otherwise Windows PowerShell 5.1 can misread Chinese comments/messages.
        self.assertIn('$EntryName.EndsWith(".ps1"', prepare)
        self.assertIn('$normalized[0] = 0xEF', prepare)
        self.assertIn('$normalized[1] = 0xBB', prepare)
        self.assertIn('$normalized[2] = 0xBF', prepare)
        workflow = (root / ".github" / "workflows" / "windows-release.yml").read_text(encoding="utf-8")
        self.assertIn("build_offline_bundle:", workflow)
        self.assertIn("default: false", workflow)
        install = (root / "scripts" / "install-comfyui-offline.ps1").read_text(encoding="utf-8")
        bundle_type = "complete-offline-comfyui-engine-internal-validation-only"
        distribution_status = "blocked-pending-third-party-dependency-audit"
        self.assertIn(f'bundle_type = "{bundle_type}"', prepare)
        self.assertIn(f'$manifest.bundle_type -ne "{bundle_type}"', install)
        self.assertIn(f'$manifest.distribution_status -ne "{distribution_status}"', install)
        self.assertIn('$manifest.distribution_approved -ne $false', install)
        self.assertIn('$maximumEntries = 100000', install)
        self.assertIn('$maximumListingLines = ([long]$maximumEntries * 16) + 2000', install)
        self.assertIn('System.Collections.Generic.List[string]', install)
        self.assertIn('$manifest.comfy_aimdo.license_file', install)
        self.assertIn('$manifest.comfyui_frontend.license_file', install)
        self.assertIn('Copy-Item -LiteralPath $comfyAimdoLicensePath', install)
        self.assertIn('Copy-Item -LiteralPath $frontendLicensePath', install)
        self.assertIn('$manifest.comfy_aimdo.license_sha256', install)
        self.assertIn('$manifest.comfyui_frontend.license_sha256', install)
        self.assertIn('$manifest.comfyui_frontend.third_party_notice_sha256', install)
        self.assertIn('comfy-aimdo-GPL-3.0-LICENSE.txt', prepare)
        self.assertIn('ComfyUI-Frontend-GPL-3.0-LICENSE.txt', prepare)
        self.assertIn('source_code_status = "upstream_commit_pinned_source_archive_included"', prepare)
        self.assertIn('Add-PinnedSourceEntry', prepare)
        self.assertIn('("/zip/" + [regex]::Escape($PinnedCommit) + "$")', prepare)
        self.assertEqual(prepare.count('$manifest = @{'), 1)
        self.assertIn('THIRD_PARTY_NOTICES/source-code/comfy-aimdo-v0.6.0-68088de9.zip', prepare)
        self.assertIn('THIRD_PARTY_NOTICES/source-code/ComfyUI_frontend-v1.59.6-c62d747f.zip', prepare)
        self.assertIn('https://codeload.github.com/Comfy-Org/comfy-aimdo/zip/$comfyAimdoSourceCommit', prepare)
        self.assertIn('source-code/comfy-aimdo-v0.6.0-68088de9.zip', prepare)
        self.assertIn('source-code/ComfyUI_frontend-v1.59.6-c62d747f.zip', prepare)
        self.assertIn('$manifest.comfy_aimdo.source_archive_sha256', install)
        self.assertIn('$manifest.comfyui_frontend.source_archive_sha256', install)
        self.assertIn('Copy-Item -LiteralPath $comfyAimdoSourcePath', install)
        self.assertIn('Copy-Item -LiteralPath $frontendSourcePath', install)
        frontend_audit = (root / "scripts" / "audit-comfyui-frontend-npm-licenses.ps1").read_text(encoding="utf-8")
        self.assertIn('packageManager -cne "pnpm@11.13.1"', frontend_audit)
        self.assertIn('--prod --frozen-lockfile --ignore-scripts', frontend_audit)
        self.assertIn('node_modules/.pnpm', frontend_audit)
        self.assertIn('license_expression_missing_manual_review_required', frontend_audit)
        self.assertIn('inventory_generated_manual_license_review_pending', frontend_audit)
        self.assertIn('$frontendNpmAuditManifest', prepare)
        self.assertIn('$manifest.comfyui_frontend.npm_dependency_audit', install)
        self.assertIn('$frontendNpmAudit.report_sha256', install)
        self.assertIn('$frontendNpmAudit.summary_sha256', install)
        self.assertIn('Copy-Item -LiteralPath $bundleSource', install)

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
        self.assertEqual(recommend_official_asset("NVIDIA", "NVIDIA GeForce GTX 1650", "AMD64"), "ComfyUI_windows_portable_nvidia.7z")
        self.assertEqual(recommend_official_asset("NVIDIA", "NVIDIA GeForce GTX 1080", "AMD64"), "ComfyUI_windows_portable_nvidia_cu126.7z")
        self.assertEqual(recommend_official_asset("NVIDIA", "NVIDIA GeForce MX450", "AMD64"), "ComfyUI_windows_portable_nvidia.7z")
        self.assertIsNone(recommend_official_asset("NVIDIA", "NVIDIA GPU", "ARM64"))
        self.assertEqual(recommend_official_asset("AMD", "AMD Radeon RX 7900", "AMD64"), "ComfyUI_windows_portable_amd.7z")
        self.assertEqual(recommend_official_asset("Intel", "Intel Arc A770", "AMD64"), "ComfyUI_windows_portable_intel.7z")
        self.assertEqual(recommend_official_asset("Intel", "Intel UHD Graphics", "AMD64"), "ComfyUI_windows_portable_intel.7z")
        integrated = parse_windows_video_adapters([{
            "Name": "AMD Radeon(TM) Graphics",
            "PNPDeviceID": "PCI\\\\VEN_1002&DEV_1636",
        }])[0]
        self.assertEqual(integrated["recommended_asset"], "ComfyUI_windows_portable_amd.7z")
        self.assertEqual(integrated["runtime_mode"], "cpu")
        self.assertEqual(recommend_official_asset("AMD", "AMD Radeon RX 580", "AMD64"), "ComfyUI_windows_portable_amd.7z")
        self.assertEqual(recommend_runtime_mode("AMD", "AMD Radeon RX 580"), "cpu")
        self.assertEqual(recommend_runtime_mode("AMD", "AMD Radeon RX 7900 XTX"), "gpu")
        self.assertEqual(recommend_official_asset("AMD", "AMD Radeon Graphics", "AMD64"), "ComfyUI_windows_portable_amd.7z")
        self.assertEqual(recommend_runtime_mode("AMD", "AMD Radeon Graphics"), "cpu")
        self.assertEqual(recommend_runtime_mode("Intel", "Intel UHD Graphics"), "cpu")
        self.assertEqual(recommend_runtime_mode("Intel", "Intel Arc A770"), "gpu")
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
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["assets"][0]["name"], "ComfyUI_windows_portable_nvidia.7z")
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


    def test_install_plan_requires_space_and_verified_official_asset(self):
        from app.routers.comfyui_installer import build_comfyui_install_plan, ENGINE_RESERVE_BYTES
        asset = {
            "name": "ComfyUI_windows_portable_nvidia.7z",
            "download_url": "https://github.com/Comfy-Org/ComfyUI/releases/download/v1/ComfyUI_windows_portable_nvidia.7z",
            "size_bytes": 1024,
            "sha256": "c" * 64,
        }
        plan = build_comfyui_install_plan(asset, "v1", ENGINE_RESERVE_BYTES + 1024, "C:/AIComicStudio/ComfyUI")
        self.assertEqual(plan["status"], "plan_ready")
        self.assertFalse(plan["download_started"])
        self.assertFalse(plan["installation_started"])
        self.assertEqual(plan["required_free_bytes"], ENGINE_RESERVE_BYTES + 1024)
        with self.assertRaises(ValueError):
            build_comfyui_install_plan(asset, "v1", ENGINE_RESERVE_BYTES + 1023, "C:/AIComicStudio/ComfyUI")
        with self.assertRaises(ValueError):
            build_comfyui_install_plan({**asset, "sha256": None}, "v1", 100 * GIB, "C:/AIComicStudio/ComfyUI")

    def test_install_plan_rejects_wrong_version_and_unknown_asset(self):
        from app.routers.comfyui_installer import build_comfyui_install_plan
        asset = {
            "name": "ComfyUI_windows_portable_nvidia.7z",
            "download_url": "https://github.com/Comfy-Org/ComfyUI/releases/download/v2/ComfyUI_windows_portable_nvidia.7z",
            "size_bytes": 1024,
            "sha256": "d" * 64,
        }
        with self.assertRaises(ValueError):
            build_comfyui_install_plan(asset, "v1", 100 * GIB, "C:/ComfyUI")


    def test_download_helper_verifies_digest_and_atomically_saves(self):
        import hashlib
        import io
        import tempfile
        import threading
        from pathlib import Path
        from app.routers.comfyui_installer import download_verified_official_asset
        payload = b"comfyui archive bytes"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ComfyUI"
            target = root / "downloads" / "ComfyUI_windows_portable_nvidia.7z"
            asset = {
                "name": target.name,
                "download_url": "https://github.com/Comfy-Org/ComfyUI/releases/download/v1/" + target.name,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            class Response:
                status = 200
                headers = {"Content-Length": str(len(payload))}
                def __init__(self): self.stream = io.BytesIO(payload)
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def geturl(self): return asset["download_url"]
                def getcode(self): return self.status
                def read(self, size=-1): return self.stream.read(size)
            result = download_verified_official_asset(
                asset, "v1", root, target, threading.Event(),
                opener=lambda request, timeout: Response(),
            )
            self.assertEqual(Path(result["path"]).read_bytes(), payload)
            self.assertEqual(result["sha256"], asset["sha256"])

    def test_download_helper_rejects_bad_digest_and_outside_destination(self):
        import hashlib
        import io
        import tempfile
        import threading
        from pathlib import Path
        from app.routers.comfyui_installer import download_verified_official_asset
        payload = b"untrusted"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ComfyUI"
            target = root / "ComfyUI_windows_portable_nvidia.7z"
            asset = {
                "name": target.name,
                "download_url": "https://github.com/Comfy-Org/ComfyUI/releases/download/v1/" + target.name,
                "size_bytes": len(payload),
                "sha256": "0" * 64,
            }
            class Response:
                status = 200
                headers = {"Content-Length": str(len(payload))}
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def geturl(self): return asset["download_url"]
                def getcode(self): return self.status
                def read(self, size=-1):
                    nonlocal payload
                    chunk, payload = payload[:size], payload[size:]
                    return chunk
            with self.assertRaises(ValueError):
                download_verified_official_asset(
                    asset, "v1", root, target, threading.Event(),
                    opener=lambda request, timeout: Response(),
                )
            self.assertFalse(target.exists())
            with self.assertRaises(ValueError):
                download_verified_official_asset(
                    {**asset, "sha256": hashlib.sha256(b"x").hexdigest()},
                    "v1", root, Path(tmp) / "outside.7z", threading.Event(),
                    opener=lambda request, timeout: Response(),
                )


    def test_download_task_public_view_hides_internal_cancel_event(self):
        from app.routers.comfyui_installer import _public_download_task
        import threading
        task = {
            "task_id": "abc123", "status": "downloading", "asset_name": "asset.7z",
            "version": "v1", "received_bytes": 10, "total_bytes": 100,
            "percent": 10, "error": None, "cancel_event": threading.Event(),
        }
        public = _public_download_task(task)
        self.assertEqual(public["percent"], 10)
        self.assertNotIn("cancel_event", public)
        self.assertFalse(public["installation_started"])

    def test_download_task_worker_records_cancelled_state(self):
        import threading
        from unittest.mock import patch
        from app.routers import comfyui_installer as module
        task_id = "cancel-task-test"
        event = threading.Event()
        event.set()
        with module._DOWNLOAD_TASKS_LOCK:
            module._DOWNLOAD_TASKS[task_id] = {
                "task_id": task_id, "status": "queued", "asset_name": "a.7z",
                "version": "v1", "received_bytes": 0, "total_bytes": 10,
                "percent": 0, "error": None, "cancel_event": event,
            }
        with patch.object(module, "download_verified_official_asset", side_effect=module.DownloadCancelled()):
            module._run_download_task(task_id, {}, "v1", "/tmp/ComfyUI")
        with module._DOWNLOAD_TASKS_LOCK:
            self.assertEqual(module._DOWNLOAD_TASKS[task_id]["status"], "cancelled")
            module._DOWNLOAD_TASKS.pop(task_id, None)


    def test_download_task_history_persists_and_restores_interrupted_tasks(self):
        import json
        import tempfile
        import threading
        from pathlib import Path
        from unittest.mock import patch
        from app.routers import comfyui_installer as module

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / ".installer_state" / "download_tasks.json"
            task_id = "a" * 32
            task = {
                "task_id": task_id,
                "status": "downloading",
                "asset_name": "ComfyUI_windows_portable_nvidia.7z",
                "version": "v-test",
                "received_bytes": 40,
                "total_bytes": 100,
                "percent": 40,
                "error": None,
                "cancel_event": threading.Event(),
            }
            with patch.object(module, "_download_state_file", return_value=state_file):
                with module._DOWNLOAD_TASKS_LOCK:
                    module._DOWNLOAD_TASKS.clear()
                    module._DOWNLOAD_TASKS[task_id] = task
                    module._persist_download_tasks_locked()
                saved = json.loads(state_file.read_text(encoding="utf-8"))
                self.assertEqual(saved["schema_version"], 1)
                self.assertNotIn("cancel_event", saved["tasks"][0])
                with module._DOWNLOAD_TASKS_LOCK:
                    module._DOWNLOAD_TASKS.clear()
                module._restore_download_tasks()
                with module._DOWNLOAD_TASKS_LOCK:
                    restored = module._DOWNLOAD_TASKS[task_id]
                    self.assertEqual(restored["status"], "interrupted")
                    self.assertEqual(restored["received_bytes"], 40)
                    self.assertIn("没有自动续传", restored["error"])
                    module._DOWNLOAD_TASKS.clear()

    def test_download_task_restore_keeps_verified_history_and_ignores_bad_records(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from app.routers import comfyui_installer as module

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "download_tasks.json"
            task_id = "b" * 32
            state_file.write_text(json.dumps({
                "schema_version": 1,
                "tasks": [
                    {
                        "task_id": task_id,
                        "status": "verified",
                        "asset_name": "ComfyUI_windows_portable_amd.7z",
                        "version": "v-test",
                        "received_bytes": 100,
                        "total_bytes": 100,
                        "percent": 100,
                        "error": None,
                    },
                    {"task_id": "../../bad", "status": "verified"},
                ],
            }), encoding="utf-8")
            with patch.object(module, "_download_state_file", return_value=state_file):
                with module._DOWNLOAD_TASKS_LOCK:
                    module._DOWNLOAD_TASKS.clear()
                module._restore_download_tasks()
                with module._DOWNLOAD_TASKS_LOCK:
                    self.assertEqual(list(module._DOWNLOAD_TASKS), [task_id])
                    self.assertEqual(module._DOWNLOAD_TASKS[task_id]["status"], "verified")
                    module._DOWNLOAD_TASKS.clear()

    def test_download_task_state_rejects_symlinked_state_directory(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from app.routers import comfyui_installer as module

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real_dir = base / "real"
            real_dir.mkdir()
            link_dir = base / ".installer_state"
            try:
                link_dir.symlink_to(real_dir, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable on this platform")
            state_file = link_dir / "download_tasks.json"
            with patch.object(module, "_download_state_file", return_value=state_file):
                with self.assertRaises(OSError):
                    with module._DOWNLOAD_TASKS_LOCK:
                        module._DOWNLOAD_TASKS.clear()
                        module._persist_download_tasks_locked()


    def test_standalone_resource_pack_builder_preserves_verified_offline_payloads(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[3]
        builder = (root / "scripts" / "create-ai-resource-pack.ps1").read_text(encoding="utf-8")
        workflow = (root / ".github" / "workflows" / "windows-release.yml").read_text(encoding="utf-8")
        self.assertIn('bundle_type = "ai-comic-studio-offline-ai-resource-pack"', builder)
        self.assertIn('ComfyUI-Payload/manifest.json', builder)
        self.assertIn('LocalAI-Payload/manifest.json', builder)
        self.assertIn('LocalAI-Payload/ollama-windows-amd64.zip', builder)
        self.assertIn('Setup.exe', builder)
        self.assertIn('AI-Resource-Pack.manifest.json', builder)
        self.assertIn('Get-FileHash -LiteralPath $tempZip -Algorithm SHA256', builder)
        self.assertIn('AI-Comic-Studio-Offline-AI-Resource-Pack', workflow)
        self.assertIn('Build standalone offline AI resource pack', workflow)

if __name__ == "__main__":
    unittest.main()
