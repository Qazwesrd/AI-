import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


class ComfyUIPortableAuditScriptTests(unittest.TestCase):
    def test_inventory_tool_keeps_distribution_blocked(self):
        source_path = REPO_ROOT / "scripts" / "audit-comfyui-portable-dependencies.ps1"
        source = source_path.read_text(encoding="utf-8")
        for marker in (
            "Get-ArchiveEntries", "Get-ExtractedEvidence", "Get-FileHash",
            "MaximumEntries", "MaximumEvidenceFiles", "MaximumMetadataBytes",
            "MaximumLicenseBytes", "MaximumTotalEvidenceBytes", "skipped_total_evidence_limit", "license_review_status = \"pending\"",
            "distribution_approved = $false", "audit_status = \"pending\"",
            "runtime_files", "finally", "Remove-Item -LiteralPath $temporaryRoot",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)
        self.assertNotIn("distribution_approved = $true", source)

    def test_only_known_official_portable_asset_names_are_accepted(self):
        source_path = REPO_ROOT / "scripts" / "audit-comfyui-portable-dependencies.ps1"
        source = source_path.read_text(encoding="utf-8")
        for asset in (
            "ComfyUI_windows_portable_nvidia.7z",
            "ComfyUI_windows_portable_nvidia_cu126.7z",
            "ComfyUI_windows_portable_amd.7z",
            "ComfyUI_windows_portable_intel.7z",
        ):
            self.assertIn(asset, source)


if __name__ == "__main__":
    unittest.main()
