# Windows offline delivery and installer repair

## User-facing delivery contract

- Keep the complete offline resource set as a separately versioned, persistent resource bundle.
- For ordinary application updates, publish one user-facing program installer ZIP only. Do not republish or require downloading unchanged AI resource bundles.
- The installer must discover, verify, and reuse an already installed resource bundle; only changed/missing resources may be installed from a separately managed bundle.
- Never silently download model weights or third-party runtimes during installation. Any network-based recovery must be explicit, opt-in, and clearly identified as not offline.
- Final release acceptance requires actual Windows install/upgrade/uninstall and offline inference tests; a successful CI build alone is not acceptance.

## Resource bundle contents

The resource bundle is versioned independently from the desktop app and has a machine-readable manifest containing component ID, version, source/license, relative install path, byte size, SHA-256, hardware requirements, and compatibility constraints.

Required components:
- Ollama Windows runtime
- Qwen3:4b model blobs and manifest
- ComfyUI runtime and pinned Python dependencies
- selected local image checkpoint(s), with license and checksum evidence
- FFmpeg and FFprobe (or a separately versioned media-runtime component)

Do not mark a component ready merely because a directory or UI catalog entry exists. A model is ready only after file integrity verification and an actual load/inference smoke test.

## Known user installation regressions to fix

1. The previous Ollama runtime was located at:
   `<userData>\\engines\\Ollama\\runtime\\ollama.exe`
   The user's prior install resolved this to
   `E:\\123\\ai-comic-studio-desktop-Data\\engines\\Ollama\\runtime\\ollama.exe`
   and used `127.0.0.1:11435`. Do not assume the system-wide PATH or default port 11434 is the only valid installation.
2. A prior installed build failed at startup with
   `Cannot find module './ollama-runtime-installer.cjs'`.
   Add a packaged-app smoke check that inspects `resources/app.asar` and confirms every relative main-process require target is present.
3. The user's selected install directory is `E:\\123`; user data, runtime, models, cache, logs, and temporary extraction must resolve safely without forcing large assets onto C:.
4. The user previously downloaded eight split archives, merged them successfully, and verified the resulting complete offline ZIP with SHA-256. Reuse those files when present; do not ask the user to download unchanged AI resources again.
5. A previous PowerShell merge script failed because of Chinese text encoding/parser issues. Build scripts and manifests must use explicit UTF-8 (with BOM where Windows PowerShell 5.1 requires it), validate filenames and checksums, and include an automated merge test.
6. Earlier diagnosis checked only common Ollama paths and port 11434, which missed the user's app-managed runtime path and port 11435. Diagnostics must search app-managed runtime locations and configured ports before reporting Ollama absent.

## Installer and upgrade behavior

- Detect app-managed Ollama first, then explicitly configured path/host, then system installation/PATH.
- Probe configured Ollama endpoint(s) and retain the discovered endpoint consistently in desktop IPC and backend configuration.
- If the app-managed runtime exists but is not running, offer/perform controlled startup and verify /api/tags before reporting ready.
- Do not overwrite or delete existing model blobs during upgrade.
- Preserve user projects and app data across in-place upgrades.
- Preflight free space on the actual destination volume, not only the Windows home/system volume.
- Use atomic extraction to a staging directory, verify every SHA-256, then switch the active resource pointer; rollback on failure.
- Detect occupied ports 3000, 8000, 11434, and 11435 and distinguish the app's own healthy services from unrelated processes.
- Surface actionable Chinese error messages and preserve diagnostic logs when startup fails.
- Confirm NSIS install directory selection, non-ASCII Windows user paths, spaces in paths, and paths on non-system drives.

## Build outputs

- Persistent resource bundle: independently versioned and checksummed; generated only when its contents change.
- Application update ZIP: contains the newest Windows installer and the minimum update metadata/checksum only; no unchanged AI model archives.
- Keep internal CI artifacts and user-facing Release assets distinct. The user-facing release must clearly identify the single program ZIP to download.

## Required acceptance tests

- Packaged ASAR contains `main.cjs`, `preload.cjs`, and `ollama-runtime-installer.cjs`.
- Fresh install to `E:\\123` and upgrade over an existing install both pass.
- Existing userData Ollama runtime at port 11435 is discovered and reused.
- Qwen3:4b is listed and answers a Chinese prompt with network disabled.
- ComfyUI starts and produces a real low-resolution image using the pinned image model with network disabled.
- FFmpeg and FFprobe execute from the installed application resources.
- Install, upgrade, rollback, and uninstall preserve user projects and do not delete separately managed AI resources unless the user explicitly selects their removal.
- The program ZIP is the only user download for an app-only update when the resource manifest has no changes.
- All generated manifests and archives pass SHA-256 verification.
