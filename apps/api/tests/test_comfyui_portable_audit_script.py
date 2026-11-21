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
            "runtime_files", "runtime_file_entries", "license_files", "triton", "onnxruntime", "avcodec", "finally", "Remove-Item -LiteralPath $temporaryRoot",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)
        self.assertNotIn("distribution_approved = $true", source)
        self.assertIn("[int]$MaximumEntries = 100000", source)
        self.assertIn("$MaximumEntries -gt 100000", source)

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
        self.assertIn('if ($record.ContainsKey("Path") -and $record["Path"] -ne $Archive -and !$record.ContainsKey("Type"))', source)
        self.assertIn("components = $components.ToArray()", source)
        self.assertIn("evidence_files = $evidence.ToArray()", source)

        self.assertIn('if ($entry.IsLink)', source)
        self.assertFalse(any('IsFolder = ($record["Folder"] -eq "+")' in line for line in source.splitlines()))
        self.assertFalse(any('Attributes = [string]$record["Attributes"]' in line for line in source.splitlines()))

    def test_bundle_builder_scans_all_portable_assets_before_packaging(self):
        builder_path = REPO_ROOT / "scripts" / "prepare-comfyui-offline-bundle.ps1"
        builder = builder_path.read_text(encoding="utf-8")
        self.assertIn("[string]$MirrorPath", builder)
        self.assertIn("-SevenZipPath $sevenZipTempPath", builder)
        self.assertIn("dependency_inventory_report", builder)
        self.assertIn("comfyui-portable-dependency-inventory.json", builder)
        self.assertIn("largestAssetBytes", builder)
        self.assertIn("inventoryReports.Count -ne 4", builder)
        installer_path = REPO_ROOT / "scripts" / "install-comfyui-offline.ps1"
        installer = installer_path.read_text(encoding="utf-8")
        self.assertIn("inventoryReportPath", installer)
        self.assertIn("inventoryVariants.Count -ne 4", installer)
        self.assertIn("archive_size_bytes", installer)
        self.assertIn("auditRegisterPath", installer)
        self.assertIn("registerVariants", installer)

    def test_image_studio_profile_parsers_use_real_regex_tokens(self):
        page_path = REPO_ROOT / "apps" / "web" / "src" / "app" / "local-image-studio" / "page.tsx"
        page = page_path.read_text(encoding="utf-8")
        self.assertIn(r"/^\s*(\d+)\s*[=:：]\s*(.+)\s*$/", page)
        self.assertIn(r"/^\d+$/", page)
        self.assertNotIn(r"\\s", page)
        self.assertNotIn(r"\\d", page)
        self.assertIn('const [width, setWidth] = useState("512")', page)
        self.assertIn('const [height, setHeight] = useState("512")', page)
        self.assertIn('const [steps, setSteps] = useState("16")', page)

    def test_comfyui_installer_records_runtime_and_preserves_existing_install(self):
        installer_path = REPO_ROOT / "scripts" / "install-comfyui-offline.ps1"
        installer = installer_path.read_text(encoding="utf-8")
        self.assertIn("[object[]]$DetectedAdapters", installer)
        self.assertIn('PSBoundParameters.ContainsKey("DetectedAdapters")', installer)
        self.assertIn("$assetDifferences = @(Compare-Object", installer)
        self.assertIn('runtime-manifest.json', installer)
        self.assertIn('$existingMain', installer)
        self.assertIn('$existingPython', installer)
        self.assertIn('Stop-Transcript', installer)

        nsis_path = REPO_ROOT / "apps" / "desktop" / "build-resources" / "installer.nsh"
        nsis = nsis_path.read_text(encoding="utf-8")
        self.assertIn("install-comfyui-offline.ps1", nsis)
        self.assertIn("install-local-engines-transaction.ps1", nsis)
        self.assertIn("ComfyUI-Payload", nsis)
        self.assertIn("LocalAI-Payload", nsis)

        transaction_path = REPO_ROOT / "scripts" / "install-local-engines-transaction.ps1"
        transaction = transaction_path.read_text(encoding="utf-8")
        self.assertIn("-ValidateOnly", transaction)
        self.assertIn("Remove-TransactionOwnedInstall", transaction)
        self.assertIn("$comfyExistedBefore", transaction)
        self.assertIn("$localAIExistedBefore", transaction)
        self.assertIn('Join-Path $LocalAIPayloadRoot "install-local-ai-offline.ps1"', transaction)

        local_ai_installer = (REPO_ROOT / "scripts" / "install-local-ai-offline.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$ValidateOnly", local_ai_installer)
        self.assertIn("if ($ValidateOnly)", local_ai_installer)
        self.assertNotIn('IfFileExists "$PROFILE\\AIComicStudio\\ComfyUI\\ComfyUI\\main.py"', nsis)
        self.assertNotIn('Remove-Item "$PROFILE\\AIComicStudio', nsis)


if __name__ == "__main__":
    unittest.main()
