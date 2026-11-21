# ComfyUI Portable Dependency Inventory

The automated inventory helper is:

`scripts/audit-comfyui-portable-dependencies.ps1`

It uses the official 7-Zip command-line listing format and extracts only bounded metadata/license evidence into a unique temporary directory. It records each supplied archive's SHA-256, caller-supplied release tag, package metadata, likely GPU runtime files, and evidence paths.

## Example

Run in PowerShell after obtaining the official portable archives and the matching verified `7zr.exe`:

```powershell
$archives = @(
  "D:\ComfyUI\ComfyUI_windows_portable_nvidia.7z",
  "D:\ComfyUI\ComfyUI_windows_portable_nvidia_cu126.7z",
  "D:\ComfyUI\ComfyUI_windows_portable_amd.7z",
  "D:\ComfyUI\ComfyUI_windows_portable_intel.7z"
)

.\scripts\audit-comfyui-portable-dependencies.ps1 `
  -SevenZipPath "D:\ComfyUI\7zr.exe" `
  -OutputPath "D:\ComfyUI\dependency-inventory.json" `
  -ReleaseTag "PUT-THE-VERIFIED-COMFYUI-RELEASE-TAG-HERE" `
  -ArchivePaths $archives
```

You may pass only the variants actually available; every supplied archive must use one of the four exact official asset filenames. Verify the archive SHA-256 and release tag against the official release before treating the report as evidence.

## Safety and limitations

- The tool does not download archives or execute their contents.
- It lists archive entries first and enforces entry-count, evidence-count, and per-file extraction-size limits.
- It rejects absolute and parent-traversal evidence paths and extracts evidence files into isolated temporary folders.
- It parses Python package `Name`, `Version`, license fields, license classifiers, and project URLs from `METADATA` or `PKG-INFO`.
- It flags likely PyTorch, CUDA, ROCm, Intel XPU, and math-runtime files for human follow-up.
- The report is an automated inventory, not a legal opinion. A metadata `License` field is not proof of redistribution permission.
- All package reviews remain `pending`; the report never approves distribution or modifies the central compliance register.
- Review exact component versions, license texts, notices, copyright, source-code obligations, redistribution conditions, and model licenses separately before any public/customer distribution.

The central register `docs/compliance/comfyui-portable-dependency-audit.json` remains the release gate. Do not change its approval fields merely because an inventory report was generated.
