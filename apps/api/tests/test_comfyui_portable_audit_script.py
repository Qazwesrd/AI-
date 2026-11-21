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
            "maximumListingLines", "System.Collections.Generic.List[string]",
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

    def test_archive_listing_optional_fields_are_guarded(self):
        source_path = REPO_ROOT / "scripts" / "audit-comfyui-portable-dependencies.ps1"
        source = source_path.read_text(encoding="utf-8")
        self.assertEqual(source.count('ContainsKey("Folder")'), 2)
        self.assertEqual(source.count('ContainsKey("Attributes")'), 2)
        self.assertEqual(source.count('IsLink = ($record.ContainsKey("Symbolic Link") -or $record.ContainsKey("Hard Link"))'), 2)
        self.assertIn('"e", "-y", "-spd"', source)
        self.assertIn("return $entries.ToArray()", source)
        self.assertIn("components = $components.ToArray()", source)
        self.assertIn("evidence_files = $evidence.ToArray()", source)

    def test_comfyui_installer_records_runtime_and_preserves_existing_install(self):
        installer_path = REPO_ROOT / "scripts" / "install-comfyui-offline.ps1"
        installer = installer_path.read_text(encoding="utf-8")
        self.assertIn("[object[]]$DetectedAdapters", installer)
        self.assertIn('PSBoundParameters.ContainsKey("DetectedAdapters")', installer)
        self.assertIn('runtime-manifest.json', installer)
        self.assertIn('$existingMain', installer)
        self.assertIn('$existingPython', installer)
        self.assertIn('Stop-Transcript', installer)
        self.assertIn('if ($entry.IsLink)', source)
        self.assertFalse(any('IsFolder = ($record["Folder"] -eq "+")' in line for line in source.splitlines()))
        self.assertFalse(any('Attributes = [string]$record["Attributes"]' in line for line in source.splitlines()))


if __name__ == "__main__":
    unittest.main()
