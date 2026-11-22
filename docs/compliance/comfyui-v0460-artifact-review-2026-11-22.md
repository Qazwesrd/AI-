# ComfyUI v0.46.0 artifact-level license evidence review (2026-11-22)

This report records a direct review of the four JSON inventory artifacts from GitHub Actions run 44963877336. It refines the earlier summary counts; it does not grant approval or replace a legal review.

## Verified portable archive identities

| Variant | Python distributions | Portable archive SHA-256 |
|---|---:|---|
| NVIDIA | 99 | 6a5255c7ec01fdc54bdbd8e4507073cf6acda2fbeea74ae1829a50681af5fac6 |
| NVIDIA CUDA 12.6 | 99 | 06ef2069702a7d7db7262e29a70ec524207f86c7bc4212f9dfcb7c977bf3e9ed |
| AMD | 102 | 00f42ed58381e194892b74e9085d4d5666fac1aab8f021094c3a745e9b52ad97 |
| Intel | 119 | db902d46c7ccf9627e4e0b7a45d52a2fc2b0b4d5dccb7052acfbc3d6124dc4d8 |

All four source-verification records identify ComfyUI v0.46.0 and 7-Zip 26.06. All records remain `audit_status=pending` and `distribution_approved=false`.

## Actual metadata gaps in the archived artifacts

The previous count of 14/14/17/14 referred to packages without a populated METADATA License expression/string, including classifier-only packages. It should not be read as 14/14/17/14 packages with no license evidence at all.

A stricter split of the artifact component records is:

| Variant | Packages with no license expression/string and no classifier | Classifier-only records |
|---|---:|---:|
| NVIDIA | 2 | 12 |
| NVIDIA CUDA 12.6 | 2 | 12 |
| AMD | 5 | 12 |
| Intel | 2 | 12 |

The classifier-only records still require verification against the actual license text and notices; a classifier is not a complete license grant.

### Packages requiring targeted follow-up

- `comfyui-frontend-package 1.59.6`: present in all four variants. Its extracted METADATA is only 117 bytes; it has no declared license expression, license string, classifier, project URL, or declared license files. The PyPI 1.59.6 wheel is 30.1 MB and the source archive is 29.1 MB; PyPI reports no provenance for either. The exact wheel's bundled frontend assets and third-party license/notice files must be unpacked and enumerated. Do not infer a license from the Comfy-Org publisher.
- `comfy-aimdo 0.6.0`: present in all four variants. The extracted package contains a 36,091-byte dist-info LICENSE file, even though its METADATA has no license expression/string/classifier. The upstream repository license is GPL-3.0. PyPI lists the Windows x64 wheel SHA-256 as `45f821b5828c4b38bcac6553f2250b56e6db87bd65334ed37eb938985d809a3c`. The archive inventory does not record the installed wheel's own digest, so that exact match remains unverified. The source code corresponding to the Windows wheel must also be included or otherwise provided in a GPL-compliant manner; the embedded LICENSE file alone is not the source.
- AMD-only ROCm 7.2.1 distributions with no license metadata or declared license files: `rocm`, `rocm-sdk-core`, and `rocm-sdk-libraries-custom`. The archive contains individual AMD component license evidence for at least amd_comgr and hipcc, but not a complete license map for every binary in these SDK packages. AMD's official documentation states that ROCm components are licensed individually and may include third-party terms; Windows HIP SDK also includes a closed-source HIP runtime. Each exact Windows package and embedded runtime needs its own license/redistribution determination.

## Native runtime candidates recorded by the scanner

These are extension-filtered candidate counts from the artifact inventory, not a complete binary ownership determination:

| Variant | DLL | PYD | EXE | LIB |
|---|---:|---:|---:|---:|
| NVIDIA | 57 | 6 | 1 | 16 |
| NVIDIA CUDA 12.6 | 56 | 6 | 1 | 16 |
| AMD | 35 | 5 | 1 | 18 |
| Intel | 77 | 7 | 5 | 25 |

The broader scanner field called `runtime_file_entries` also includes source/header/config files; it should not be described as a count of binary files. Every redistributable native runtime still needs owner, version, license, notice, and source/offer analysis.

## Confirmed 7-Zip evidence

The current offline bundle builder selects the official source archive matching the selected 7-Zip release, includes it under `THIRD_PARTY_NOTICES/`, records its hash and size, and the installer verifies that source archive hash. This resolves the earlier uncertainty about source-archive inclusion. The exact `7zr.exe` component composition still needs review against 7-Zip's composite LGPL, unRAR restriction, BSD and public-domain terms.

## Release decision

- The SD 1.5 click-through acceptance gate and local full-license display are now implemented in source, but CI and packaged-Windows acceptance testing must pass.
- The exact SD 1.5 payload hash on the build host remains unverified.
- Frontend package asset licenses, comfy-aimdo exact wheel/source evidence, AMD ROCm component licenses, Intel/NVIDIA runtime binary obligations, and remaining dependency notices remain open.
- Keep `audit_status=pending`, `distribution_approved=false`, and public/customer distribution blocked. This artifact review is technical evidence, not legal advice or approval.

## Evidence sources

- GitHub Actions inventory run: https://github.com/Qazwesrd/AI-/actions/runs/44963877336
- PyPI comfyui-frontend-package 1.59.6: https://pypi.org/project/comfyui-frontend-package/1.59.6/
- PyPI comfy-aimdo 0.6.0: https://pypi.org/project/comfy-aimdo/0.6.0/
- comfy-aimdo upstream license: https://github.com/Comfy-Org/comfy-aimdo/blob/master/LICENSE
- AMD ROCm component licensing: https://rocm.docs.amd.com/en/docs-7.2.3/about/license.html
- AMD Windows HIP SDK install/license acceptance: https://rocm.docs.amd.com/projects/install-on-windows/en/develop/install/install.html
- 7-Zip license: https://github.com/ip7z/7zip/blob/main/DOC/License.txt
