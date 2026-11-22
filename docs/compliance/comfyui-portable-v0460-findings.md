# ComfyUI Windows Portable v0.46.0 — Inventory Findings

Generated from the four GitHub Actions inventory artifacts for Comfy-Org/ComfyUI v0.46.0 on 2026-11-22. This document records technical evidence only. It is **not legal advice or distribution approval**.

## Source integrity

All four inventory jobs downloaded the named assets from the official Comfy-Org release and recorded SHA-256 digests:

| Variant | Python distributions inventoried | Archive SHA-256 |
|---|---:|---|
| NVIDIA | 99 | `6a5255c7ec01fdc54bdbd8e4507073cf6acda2fbeea74ae1829a50681af5fac6` |
| NVIDIA CUDA 12.6 | 99 | `06ef2069702a7d7db7262e29a70ec524207f86c7bc4212f9dfcb7c977bf3e9ed` |
| AMD | 102 | `00f42ed58381e194892b74e9085d4d5666fac1aab8f021094c3a745e9b52ad97` |
| Intel | 119 | `db902d46c7ccf9627e4e0b7a45d52a2fc2b0b4d5dccb7052acfbc3d6124dc4d8` |

The NVIDIA, NVIDIA CUDA 12.6, AMD and Intel archives contain 99, 99, 102 and 119 Python distribution metadata records respectively. The scanner also found native runtime files; metadata inspection alone does not identify every native binary's owner or license.

## Items requiring manual resolution

The inventory metadata reports the following components. Their presence is a review trigger, not a conclusion that redistribution is prohibited.

| Component | Reported license / issue | Variants |
|---|---|---|
| comfyui-embedded-docs 0.5.17 | GPL-3.0 | All four |
| pygit2 1.20.1 | GPLv2 with linking exception | All four |
| autocommand 2.2.2 | LGPLv3 | All four |
| certifi 2026.10.23 | MPL-2.0 | All four |
| tqdm 4.70.1 | MPL-2.0 AND MIT | All four |
| Intel oneAPI / MKL / TBB runtime components | Intel Simplified Software License and Intel End User License Agreement for Developer Tools | Intel |
| AMD ROCm runtime packages | License metadata absent/unspecified for several packages | AMD |

The number of distributions with absent or ambiguous metadata license fields is 14 for NVIDIA, 14 for NVIDIA CUDA 12.6, 17 for AMD and 14 for Intel. This includes packages for which a license file may exist even though the metadata field is absent; each needs its upstream license and notice checked.

## Required completion work

1. Match every bundled Python distribution to its exact upstream version, source, license text, copyright and NOTICE requirements.
2. Determine whether each GPL/LGPL/MPL component is actually shipped and how it is linked or used; document applicable source-code, relinking and notice obligations.
3. Review Intel redistribution terms and confirm whether the bundled oneAPI/MKL/TBB components may be redistributed in this installer and to the intended users.
4. Inventory native DLL, PYD, EXE and other runtime binaries, including CUDA/ROCm/oneAPI and Microsoft runtime components, against upstream redistribution terms.
5. Resolve all unspecified metadata entries from upstream evidence; do not infer a license from a package name or a single metadata field.
6. Produce a complete third-party notices bundle and, where required, corresponding source or source-offer materials.
7. Repeat the review for Ollama's exact Windows release and all transitive runtime dependencies, Qwen3 4B model files, and any Stable Diffusion checkpoint intended for bundling.
8. Obtain qualified legal review before changing `distribution_approved` to `true`.

## Current decision

- `audit_status`: `pending`
- `distribution_approved`: `false`
- Public distribution of the complete offline bundle remains blocked.
- Do not remove packages or substitute runtime builds solely to make the inventory look permissive; first verify the app's actual runtime requirements and retest the resulting engine on supported hardware.

## References

- Official ComfyUI v0.46.0 release: https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.46.0
- Official Windows portable installation notes: https://github.com/Comfy-Org/ComfyUI/blob/master/docs/installation.md
- GNU GPL v3 text: https://www.gnu.org/licenses/gpl-3.0.html
- GNU LGPL v3 text: https://www.gnu.org/licenses/lgpl-3.0.html
- Mozilla Public License 2.0: https://www.mozilla.org/MPL/2.0/
