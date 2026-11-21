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
            'ollama.exe pull',
            'model_manifest_sha256',
            'distribution_approved = $false',
            'network_required_after_install = $false',
            '12GB',
            'Apache License',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_offline_installer_verifies_paths_hashes_and_preserves_existing_data(self):
        source = (ROOT / "scripts" / "install-local-ai-offline.ps1").read_text(encoding="utf-8")
        for marker in (
            "Resolve-SafeRelativePath",
            "distribution_approved -ne $false",
            "Get-Sha256",
            "Expand-Archive",
            "Move-Item -LiteralPath $stage",
            "为保护已有数据",
            "OLLAMA_HOST",
            "127.0.0.1:11435",
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
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, packer)
        self.assertIn("prepare-local-ai-offline-payload.ps1", wrapper)
        self.assertIn("-LocalAIPayloadRoot $localAIPayloadRoot", wrapper)
        self.assertIn("LocalAI-Payload\\install-local-ai-offline.ps1", nsis)

    def test_desktop_uses_the_bundled_runtime_without_changing_dev_default(self):
        main = (ROOT / "apps" / "desktop" / "main.cjs").read_text(encoding="utf-8")
        runtime = (ROOT / "apps" / "api" / "app" / "routers" / "local_ai_runtime.py").read_text(encoding="utf-8")
        model_installer = (ROOT / "apps" / "api" / "app" / "routers" / "local_model_installer.py").read_text(encoding="utf-8")
        self.assertIn('path.join(ollamaRuntime, "ollama.exe")', main)
        self.assertIn('OLLAMA_HOST: "127.0.0.1:11435"', main)
        self.assertIn("waitForOllama(ollamaBaseUrl)", main)
        self.assertIn('os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")', runtime)
        self.assertIn('os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")', model_installer)


if __name__ == "__main__":
    unittest.main()
