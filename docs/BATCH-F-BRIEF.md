# Phase 1/2, Batch F — WS preview server: protocol + mesher preview API wiring

Implement the `/ws/preview` channel of `docs/WS-PROTOCOL.md` (read it fully — it IS the contract) in this repo's server, computing real geometry through the mesher's new preview API and encoding frames with the batch-B codec.

**Path discipline (concurrent agents in this repo): create/modify ONLY `server/preview/**`, `server/tests/test_preview_*.py`, and `server/app.py` (mount/include lines only — do not restructure it). Nothing else.**

Runtime: `../Waveguide Generator/.venv/bin/python`. The mesher's editable install currently resolves to the `preview-api` branch, so `from hornlab_mesher.preview.api import build_preview_geometry, PreviewOptionsV1` works — that plus `server.protocol.frame` (batch B) and `server.design` (batch A) are your building blocks.

## Deliverables

1. `server/preview/translate.py` — v2 `DesignConfig` (server.design.schema) → mesher config dict. Reference semantics: v1's `waveguide_payload_to_mesher_config` in `../Waveguide Generator/server/solver/mesher_adapter.py` (~line 190) — port the per-family key mapping faithfully (OSSE/R-OSSE/ICW/FREEFORM, source, symmetry, enclosure/IB/freestanding mode selection), but from OUR schema's field names. Docstrings cite the v1 lines they mirror. Viewport contract: quadrants forced to full (display shows the whole device).
2. `server/preview/service.py` — the protocol engine, transport-agnostic core + FastAPI WS endpoint:
   - `hello` with `epoch`, `heartbeatSec`, `limits.maxFrameBytes`; heartbeat pings; close codes per spec (4400/4413/1012...).
   - `preview` and `curve` kinds; `{seq, designRevision, design, lod}`; validation errors → `{"kind":"error", code:"validation", fields:...}` carrying seq + revision (map Pydantic errors to field paths).
   - **One compute in flight per connection, one pending slot, latest-wins, `dropped` notices** (the spike's proven pattern), compute in `asyncio.to_thread`.
   - LOD mapping: `coarse`/`fine` → `PreviewOptionsV1` presets.
   - Frame encoding: surfaces from `PreviewGeometryV1` → FRAME-SPEC v1.1 sections (positions/indices/**normals** per surface, roles, shading, normalMethod, closed_phi, fidelity metadata passthrough, epoch/seq/designRevision/lod/evalMs in header). Convert f64→f32 at the boundary; refuse non-finite.
3. Mount in `server/app.py`; `/ws/preview` present in OpenAPI-adjacent docs is not required (WS is spec'd separately).
4. Tests (`server/tests/test_preview_ws_*.py`): the sandbox cannot bind sockets, so test the protocol core directly (drive the transport-agnostic state machine with fake send/receive queues): hello/epoch, pending-slot coalescing + dropped notices, error-carries-revision, stale-epoch discipline (core exposes epoch check), oversize design payload rejection, LOD presets, translate.py per-family golden checks against small fixture designs (reuse batch A test fixtures where possible), frame round-trip via the real codec (decode what you encoded; assert normals present per surface). Live end-to-end verification is the overseer's job — say so in the README section you may add to your test file docstrings (not the repo README).

## Rules
- No new dependencies. Keep the protocol core importable without FastAPI for tests.
- Self-verify: full `server/tests` suite green. Report new + total counts.
- Final message: files, test counts, any WS-PROTOCOL ambiguities you resolved (list them — the spec author reviews), and exactly how the overseer should live-test (launch command + a 20-line python websockets client snippet).
