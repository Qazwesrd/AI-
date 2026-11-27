# Windows Desktop: 12-Item Repair and Acceptance Tracker

Scope: the installed Windows desktop application on `offline-ai-engine-integration`, tracked by Draft PR #152. This file is the acceptance checklist; it does not claim that any unchecked item is implemented.

## Non-negotiable constraints

- Keep PR #152 open as Draft; do not merge it.
- Do not create a test release or publish a desktop release before user acceptance.
- Prefer local inference. No paid/cloud API, model download, or external upload without explicit user approval.
- Use `E:\\AI\\数据` as the default local AI data root on the user's Windows machine, while allowing a different drive/path where appropriate.
- Never report a model or engine as ready based only on directory existence or a successful HTTP response; distinguish discovered, load-tested, and end-to-end verified states.
- Preserve existing user data and model files. Back up before any migration or destructive operation.

## Repair items

- [ ] 1. Visual asset production center: four distinct routes/views for character, scene, prop, and 3D asset workflows; parse the selected novel's current analysis into categorized assets and preserve novel/source provenance.
- [ ] 2. Asset state tracking: beginner-friendly local-model workflow that extracts chapter changes, cites source text, and presents reviewable state history without requiring manual field-by-field entry.
- [ ] 3. Worldbuilding and comic production: wire the deep-analysis actions to a real local Ollama job; show progress, errors, persisted results, and downstream availability. No silent no-op.
- [ ] 4. Local image generation: discover ComfyUI and actual installed checkpoints; explain missing models and test an end-to-end image generation, not just queue submission.
- [ ] 5. AI video editing: analyze local video metadata/keyframes with local models, create an editable timeline/plan, support local FFmpeg execution, optional user-selected web handoff, and text-to-video workflow where compatible local models exist.
- [ ] 6. Local video workbench: robustly detect ComfyUI health/system stats, node inventory, Wan/VACE models and node classes; provide compatibility-aware setup guidance and workflow preflight. Do not claim Wan/VACE is ready until a real test render succeeds.
- [ ] 7. Viral prompt center: fix create/edit actions; auto-suggest public RSS/Atom source title and URL from a validated feed, with preview and safe scheduled collection.
- [ ] 8. Local model management: repair Ollama create-model API, expose the effective data root, detect ComfyUI/Ollama connection and model inventory accurately.
- [ ] 9. Resource bundle wizard: check resource root, image/video engines, installed models, node dependencies and actual inference tests; make each step actionable.
- [ ] 10. Storyboard-to-video configuration: generate and validate workflow configuration automatically from detected local capabilities; ask the user only for unavoidable choices.
- [ ] 11. Runtime installation guidance: one managed Ollama/ComfyUI installation and one configurable data root; avoid duplicate services and desktop-folder installs.
- [ ] 12. Codex workflow: document optional Codex setup for repository code maintenance; clearly distinguish coding assistance from inference engines and video models.

## Verified changes so far

- [x] Fixed specialized Ollama model creation to use the current `/api/create` request contract (`from`, `system`, and `parameters`) instead of relying only on a legacy Modelfile payload. This directly targets the reported `neither from or files was specified` rejection.
- [x] Added a regression test asserting the new request shape and local-only behavior.

## Additional local chat and cost-safety work

- [x] Added a dedicated `/local-chat` page and homepage/navigation entry so the user can chat with an installed Ollama model using multi-turn context.
- [x] Added `POST /api/local-ai/chat` using Ollama's local `/api/chat` endpoint; the API accepts only installed local models, bounds each request to 20 messages / 24,000 characters, and never downloads or falls back to cloud.
- [x] Filtered obvious Ollama Cloud aliases (for example `*-cloud` and `:cloud`) from the local model inventory so they cannot be selected in local workflows.
- [x] Added browser-local persistence for the selected model and the latest 100 chat messages. The transcript is stored in this computer's browser localStorage only; it is not uploaded or cloud-synced by this feature. This is one saved conversation, not a multi-chat archive.
- [ ] Verify the chat end-to-end on the user's Windows machine with their installed model; GitHub CI and hardware inference are still pending.

## Final acceptance gate

Do not prepare the user's next installer until all 12 items are checked, backend and frontend tests pass, the Windows packaging workflow succeeds, the installer and SHA-256 are verified, and the PR remains Draft/unmerged. Hardware-dependent image/video inference must be explicitly reported as passed, failed, or blocked by missing model resources; never mark it passed without a generated output file.
