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

- `comfyui-frontend-package 1.59.6`: present in all four variants. Its extracted METADATA is only 117 bytes and has no license declaration, classifier, project URL, or declared license files. The exact upstream ComfyUI_frontend v1.59.6 tag (commit `c62d747f378e0fa277cbb5137bffaf282598c64c`) declares GPL-3.0-only in package.json and contains a GPL-3.0 LICENSE. PyPI's 30.1 MB wheel and 29.1 MB sdist have no provenance, so the exact wheel-to-tag correspondence remains unverified. The frontend repository's THIRD_PARTY_NOTICES.md only states an AMD trademark notice; enumerate npm dependency and asset licenses from the exact wheel/build before concluding notice completeness.
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


## Follow-up change: standalone GPL notices

The offline bundle builder now fetches and validates the exact upstream GPL-3.0 license texts for comfy-aimdo v0.6.0 (source commit 68088de9dce9f5e32231fccf230938769c5562da) and ComfyUI Frontend v1.59.6 (source commit c62d747f378e0fa277cbb5137bffaf282598c64c). It adds those license files to THIRD_PARTY_NOTICES, includes the frontend repository's THIRD_PARTY_NOTICES.md, and the installer copies all three notices into the installed ComfyUI engine's licenses directory. The installer source checks were extended accordingly.
This does not yet supply the corresponding comfy-aimdo or frontend source archives, prove the exact installed wheel digests match those source commits, or complete the frontend's transitive npm asset/license inventory. Those remain explicit release blockers. The standalone license files are notice improvements, not GPL source-compliance sign-off.


## Follow-up change: pinned GPL source archives included

The offline builder now downloads complete source ZIP archives from codeload.github.com, pinned to immutable upstream commits: comfy-aimdo v0.6.0 (`68088de9dce9f5e32231fccf230938769c5562da`) and ComfyUI_frontend v1.59.6 (`c62d747f378e0fa277cbb5137bffaf282598c64c`). The source archive bytes are SHA-256 hashed during build; the hashes and sizes are recorded in the generated manifest and included in the bundle's integrity verification. The installer verifies both archives and copies them into `licenses/source-code/` alongside the GPL license texts.
This is a material improvement to the corresponding-source availability for offline recipients, but it is not proof that the exact binaries inside the ComfyUI portable wheels were built from those commits. The scanner does not yet record the exact installed wheel file hashes/build provenance. Exact binary-to-source correspondence and the frontend npm transitive dependency/asset license inventory remain release blockers. The license register remains pending and distribution remains blocked.


## Additional AMD Windows runtime blocker

AMD's official Windows HIP SDK component matrix identifies the Windows HIP runtime as closed-source, unlike the open-source HIP runtime on Linux. AMD's Windows deployment guidance says applications should not distribute the HIP runtime and that end users obtain it through the AMD GPU driver. The current ComfyUI AMD Portable inventory identifies ROCm SDK package names and native binary candidates but does not map every DLL to an exact HIP SDK component. Therefore each bundled AMD DLL must be mapped before approval; if any is the HIP runtime, remove it from redistribution unless AMD confirms a permitted packaging route. See https://rocm.docs.amd.com/projects/install-on-windows/en/develop/conceptual/component-support.html and https://rocm.docs.amd.com/projects/install-on-windows/en/develop/conceptual/deployment-guidelines.html.
