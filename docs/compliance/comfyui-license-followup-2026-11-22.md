# ComfyUI dependency license follow-up (2026-11-22)

This is a supplemental evidence note for the existing v0.46.0 automated inventories. It does not replace those inventories, constitute legal advice, or approve redistribution.

## Scope and method

- Existing inventory workflow: GitHub Actions run 44963877336; all four variant inventory jobs completed successfully.
- Existing inventory remains `audit_status=pending` and `distribution_approved=false`.
- This follow-up checks the two packages previously flagged for missing or ambiguous package METADATA license fields. No package inventory was rerun.

## Findings

### comfyui-frontend-package 1.59.6

- PyPI release page identifies the publisher/owner as Comfy-Org and records release 1.59.6 on 2026-11-18.
- The release provides an sdist and a wheel; PyPI reports neither file has provenance metadata.
- The PyPI release page does not expose an explicit license classification or license text in the rendered metadata.
- The package is a 30.1 MB wheel, so it may contain a substantial frontend asset bundle. Its embedded third-party notices and dependency licenses must be extracted from the exact wheel used by the ComfyUI portable archive before a license conclusion is recorded.
- Status: `exact_wheel_notice_and_embedded_asset_review_required`. Do not infer a license from the Comfy-Org publisher name.

Evidence:
- https://pypi.org/project/comfyui-frontend-package/1.59.6/
- Wheel SHA-256 (PyPI): `7564e716da2d1e122612f067bcffda6c3b9bc766f3b9f8c85fc44cf1d9c54f85`
- Source distribution SHA-256 (PyPI): `68f828837a6d4c43b4f70df982452ab76aefd9108dfd70ea22cf928e9bfccdd2`

### comfy-aimdo 0.6.0

- The upstream repository contains an explicit `LICENSE` file stating GNU GPL version 3.0. It also states that the original author/copyright holder reserves the right to offer the software under alternative licenses.
- The upstream `pyproject.toml` does not declare a project license field; this explains the missing/ambiguous package METADATA field, but does not override the repository's license file.
- PyPI lists a Windows x64 wheel of 284.5 kB with SHA-256 `45f821b5828c4b38bcac6553f2250b56e6db87bd65334ed37eb938985d809a3c`; PyPI reports provenance for all five 0.6.0 release files.
- This confirms the upstream license identity, not that the exact archived portable wheel matches that PyPI hash or that the installer currently includes corresponding source and notices.
- GPL-3.0 object-code redistribution requires providing the corresponding source under GPL terms, preserving notices and license text, and avoiding additional restrictions inconsistent with GPL. Whether/how the bundled wheel is conveyed in the product should be checked against the actual package and distribution architecture.
- Status: `upstream_gpl_3_0_confirmed_exact_bundled_wheel_hash_and_corresponding_source_and_notice_inclusion_pending`.

Evidence:
- https://pypi.org/project/comfy-aimdo/0.6.0/
- https://github.com/Comfy-Org/comfy-aimdo/blob/master/LICENSE
- https://github.com/Comfy-Org/comfy-aimdo/blob/master/pyproject.toml

## Required next evidence before any distribution decision

1. Extract the exact `comfyui_frontend_package-1.59.6-py3-none-any.whl` from each applicable portable variant and enumerate embedded license/notice files and bundled frontend dependencies.
2. Retrieve the exact `comfy-aimdo 0.6.0` Windows x64 wheel and its upstream repository license/notice files; match the wheel hash to the archived package.
3. Add all found notices and license texts to the installer third-party notices directory, preserving original copyright statements.
4. Continue the already-listed review of GPL/LGPL/MPL, Intel oneAPI, native runtime binaries, 7-Zip, Ollama, Qwen3 and Stable Diffusion OpenRAIL-M obligations.

## Release gate

No approval is granted by this note. Keep `audit_status=pending`, `distribution_approved=false`, and public/customer distribution blocked until exact payload evidence and required legal review are complete.
