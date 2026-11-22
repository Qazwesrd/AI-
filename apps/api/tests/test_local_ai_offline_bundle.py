import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class LocalAIOfflineBundleContractTests(unittest.TestCase):
    def test_payload_builder_pins_model_and_verifies_runtime_and_model_digests(self):
        source = (ROOT / "scripts" / "prepare-local-ai-offline-payload.ps1").read_text(encoding="utf-8")
        for marker in (
            'qwen3:4b',
            'ollama-windows-amd64.zip',
            'Get-Sha256',
            'digest',
            'OLLAMA_MODELS',
            'OLLAMA_HOST',
            'OLLAMA_NO_CLOUD = "1"',
            'pull $ModelName',
            'manifest_sha256',
            'distribution_approved = $false',
            'network_required_after_install = $false',
            '12GB',
            'Apache License',
            'cloud_features_disabled = $true',
            'minimum_system_ram_bytes = 8GB',
            'recommended_system_ram_bytes = 16GB',
            'OLLAMA_NUM_PARALLEL = "1"',
            'OLLAMA_MAX_LOADED_MODELS = "1"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_offline_installer_verifies_paths_hashes_and_preserves_existing_data(self):
        source = (ROOT / "scripts" / "install-local-ai-offline.ps1").read_text(encoding="utf-8")
        for marker in (
            "Resolve-SafeRelativePath",
            "Get-ZipExpandedSize",
            "$modelDescriptors",
            "$uniqueModelPaths",
            "cloud_features_disabled -ne $true",
            "distribution_approved -ne $false",
            "payload_manifest_sha256 -ne $payloadManifestHash",
            "Get-Sha256",
            "Expand-Archive",
            "Move-Item -LiteralPath $stage",
            "为保护已有数据",
            "OLLAMA_HOST",
            "127.0.0.1:11435",
            'set "OLLAMA_NO_CLOUD=1"',
            'set "OLLAMA_NUM_PARALLEL=1"',
            'set "OLLAMA_MAX_LOADED_MODELS=1"',
            "Win32_ComputerSystem",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)
        self.assertNotIn("Invoke-WebRequest", source)
        self.assertNotIn("Invoke-RestMethod", source)

    def test_complete_offline_bundle_includes_local_ai_payload_and_installer(self):
        packer = (ROOT / "scripts" / "prepare-comfyui-offline-bundle.ps1").read_text(encoding="utf-8")
        wrapper = (ROOT / "scripts" / "create-comfyui-offline-installer.ps1").read_text(encoding="utf-8")
        nsis = (ROOT / "apps" / "desktop" / "build-resources" / "installer.nsh").read_text(encoding="utf-8")
        for marker in (
            "LocalAI-Payload/manifest.json",
            "LocalAI-Payload/install-local-ai-offline.ps1",
            "LocalAI-Payload/ollama-windows-amd64.zip",
            "local_ai = @{",
            "license_review_status = \"pending\"",
            "payload_manifest_sha256",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, packer)
        self.assertIn("prepare-local-ai-offline-payload.ps1", wrapper)
        self.assertIn("-LocalAIPayloadRoot $localAIPayloadRoot", wrapper)
        self.assertIn("ComfyUI-Payload/install-local-engines-transaction.ps1", packer)
        self.assertIn("install-local-engines-transaction.ps1", nsis)
        self.assertIn("LocalAI-Payload", nsis)
        transaction = (ROOT / "scripts" / "install-local-engines-transaction.ps1").read_text(encoding="utf-8")
        self.assertIn("-ValidateOnly", transaction)
        self.assertIn("Remove-TransactionOwnedInstall", transaction)

    def test_image_checkpoint_and_download_manual_match_the_actual_bundle_contract(self):
        packer = (ROOT / "scripts" / "prepare-comfyui-offline-bundle.ps1").read_text(encoding="utf-8")
        installer = (ROOT / "scripts" / "install-comfyui-offline.ps1").read_text(encoding="utf-8")
        manual = (ROOT / "docs" / "manual" / "COMFYUI-OFFLINE-BUNDLE.md").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "windows-release.yml").read_text(encoding="utf-8")

        self.assertIn("ComfyUI-Payload/models/checkpoints/v1-5-pruned-emaonly-fp16.safetensors", packer)
        self.assertIn("Stable-Diffusion-CreativeML-OpenRAIL-M-LICENSE.txt", packer)
        self.assertIn("imageModelHash", installer)
        self.assertIn("本套件构建脚本会把固定版本的 Stable Diffusion 1.5 FP16 检查点", manual)
        user_manual = (ROOT / "docs" / "manual" / "manual.html").read_text(encoding="utf-8")
        self.assertIn("WINDOWS DESKTOP · 桌面版使用指南", user_manual)
        self.assertNotIn("本地测试版使用指南", user_manual)
        self.assertNotIn("本次测试包", user_manual)
        self.assertIn("不要只提取或移动 Setup.exe", user_manual)
        self.assertNotIn("图片生成检查点是独立资源，当前不随安装包自动下载或打包", manual)
        self.assertIn("必须下载全部三个产物", manual)
        self.assertIn("分别解压这三个 ZIP", manual)
        self.assertIn("AI漫剧制作大师-离线套件合并工具.ps1", manual)
        for artifact in (
            "AI-Comic-Studio-Offline-Bundle-Volumes-1",
            "AI-Comic-Studio-Offline-Bundle-Volumes-2",
            "AI-Comic-Studio-Offline-Bundle-Volumes-3",
        ):
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, workflow)
                self.assertIn(artifact, manual)

    def test_desktop_uses_the_bundled_runtime_without_changing_dev_default(self):
        main = (ROOT / "apps" / "desktop" / "main.cjs").read_text(encoding="utf-8")
        runtime = (ROOT / "apps" / "api" / "app" / "routers" / "local_ai_runtime.py").read_text(encoding="utf-8")
        model_installer = (ROOT / "apps" / "api" / "app" / "routers" / "local_model_installer.py").read_text(encoding="utf-8")
        self.assertIn('path.join(ollamaRuntime, "ollama.exe")', main)
        self.assertIn('host: "127.0.0.1:11435"', main)
        self.assertIn('OLLAMA_NO_CLOUD: "1"', main)
        self.assertIn('OLLAMA_NUM_PARALLEL: "1"', main)
        self.assertIn('OLLAMA_MAX_LOADED_MODELS: "1"', main)
        self.assertIn("scheduleOllamaRestart", main)
        self.assertIn("startBundledOllama", main)
        self.assertIn('os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")', runtime)
        self.assertIn('os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")', model_installer)


if __name__ == "__main__":
    unittest.main()
