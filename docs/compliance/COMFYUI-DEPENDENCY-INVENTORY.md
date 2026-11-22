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

The offline bundle builder currently **does include** the pinned Stable Diffusion 1.5 FP16 checkpoint (2,132,696,762 bytes) at `ComfyUI-Payload/models/checkpoints/v1-5-pruned-emaonly-fp16.safetensors`, and includes the full CreativeML Open RAIL-M license at `THIRD_PARTY_NOTICES/Stable-Diffusion-CreativeML-OpenRAIL-M-LICENSE.txt`. The installer copies that license into the installed engine. The desktop UI displays the local full text and Attachment A summary, persists click-through acceptance, and the local API blocks SD 1.5 generation until accepted. This is a technical safeguard only; legal enforceability review remains pending. The builder pins the upstream revision and expected SHA-256, but the exact payload hash has not yet been verified on the build host. The bundle remains internal-validation-only. Before any distribution to other users, the paragraph 5 restrictions and license must be passed through to recipients, with applicable attribution notices retained. Target-device inference performance is also unverified. Do not describe this checkpoint as approved for redistribution.

The complete offline bundle builder also runs this inventory automatically against each of the four downloaded official portable archives, one at a time. The Qwen3-4B upstream model card and Ollama `qwen3:4b` library page both identify Apache-2.0; this confirms the published model license identity, but does not approve the exact Ollama conversion/runtime bundle until its built manifest, every blob digest, Ollama transitive dependencies, and notices are recorded and reviewed. It includes the combined report at `THIRD_PARTY_NOTICES/comfyui-portable-dependency-inventory.json`, records each archive SHA-256 and release tag, and refuses to package if any inventory fails. The bundle copy of the audit register is populated with those exact hashes, release tags, component records, and report path; the source register in the repository remains an unapproved template. The installer checks that all four report entries and register entries match the asset hashes and sizes in the bundle manifest. This automated evidence remains `pending`; it is not a license approval.

## Safety and limitations

- The tool does not download archives or execute their contents.
- It consumes the 7-Zip listing incrementally with a bounded line count, then enforces entry-count, evidence-count, and per-file extraction-size limits.
- It rejects absolute and parent-traversal evidence paths, refuses symbolic/hard-link evidence files, disables 7-Zip wildcard expansion for evidence extraction, and extracts evidence files into isolated temporary folders.
- It parses Python package `Name`, `Version`, license fields, license classifiers, and project URLs from `METADATA` or `PKG-INFO`.
- It flags likely PyTorch, CUDA, ROCm, Intel XPU, Python runtime, video codec, ONNX/OpenVINO, Triton, MKL/OpenMP, and math-runtime files for human follow-up. It also records native runtime candidate paths and sizes, plus each Python distribution's declared `License-File` metadata, so reviewers can associate package declarations with extracted license evidence.
- The report is an automated inventory, not a legal opinion. A metadata `License` field is not proof of redistribution permission.
- All package reviews remain `pending`; the report never approves distribution or modifies the central compliance register.
- Review exact component versions, license texts, notices, copyright, source-code obligations, redistribution conditions, and model licenses separately before any public/customer distribution.

The central register `docs/compliance/comfyui-portable-dependency-audit.json` remains the release gate. Do not change its approval fields merely because an inventory report was generated.


## Ollama and bundled Qwen3 4B local model

The complete offline installer now prepares a separate `LocalAI-Payload` containing the official Ollama Windows amd64 standalone runtime and the pinned `qwen3:4b` model store.

- `scripts/prepare-local-ai-offline-payload.ps1` downloads the official Ollama release asset and requires its GitHub SHA-256 digest.
- It starts a temporary isolated Ollama service on loopback port 11435, pulls only `qwen3:4b`, and validates the model manifest and every referenced blob digest and size.
- It requires the model manifest's Apache-2.0 license layer to be present and readable, and includes the Ollama MIT license, Apache-2.0 text, and a Qwen3 attribution notice.
- `scripts/install-local-ai-offline.ps1` verifies the runtime archive, model manifest, model file hashes, and safe relative paths before installing. It does not download or contact a remote service.
- The desktop app starts the bundled Ollama runtime on `127.0.0.1:11435` and configures the API to use that loopback endpoint. The development default remains `127.0.0.1:11434`.
- Ollama's bundled transitive dependencies and the exact model revision still require manual license review. The central audit register remains `pending` and public/customer distribution remains blocked.

The offline bundle includes both the Qwen3 4B local text model and the pinned Stable Diffusion 1.5 FP16 image checkpoint. The latter is included for internal validation only; its click-through acceptance gate is implemented, while exact payload hash verification and legal review remain release blockers.
