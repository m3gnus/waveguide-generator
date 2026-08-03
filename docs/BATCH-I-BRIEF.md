# Phase 2, Batch I — the real 3-D viewport

Replace the viewport telemetry placeholder with the actual three.js renderer consuming live FRAME-SPEC v1.1 frames. Design reference for chrome/feel: `../wg-v2-ui-sketches/opus-sketch.html`. Contracts: `docs/FRAME-SPEC.md` (v1.1 surfaces/normals), `docs/WS-PROTOCOL.md`, plan §4.1/§4.6.

**Path discipline (concurrent agents): work ONLY under `frontend/src/viewport/**`, plus minimal wiring in the existing ViewportPanel mount point (`frontend/src/shell/` — touch only the panel component that hosts the viewport) and `frontend/src/api/` ONLY if the frame wrapper needs additional typed accessors (no behavioral changes there). No npm installs (three + @react-three/fiber + drei are preinstalled).**

## Deliverables

1. **Frame → scene** (`viewport/frameScene.ts` + `viewport/SurfaceMesh.tsx`): per-surface meshes from decoded frames — positions/normals/indices as BufferAttributes (Float32/Uint32 views, zero copies), one material per shading class. **Buffer reuse rules (hard requirements, both learned the hard way — spike RESULTS §5.1):** reuse GPU buffers when byte lengths match; when any count changes, dispose/replace EVERY attribute (positions, normals, index) — never leave a stale attribute; never call computeVertexNormals (server normals are authoritative).
2. **Display modes** matching v1's seven (traceability rows exist): clay (matcap-ish standard material), solid+wire (mesh + barycentric or thin-line overlay), wireframe, x-ray (transparent, depth-sorted), zebra (shader: stripes from view-reflected normal — trustworthy now because normals are analytic), curvature (vertex-colored from the frame's curvature section when present; graceful "needs inspection LOD" empty-state otherwise), edges (declared hard boundaries only — surface borders, not derived angles).
3. **Camera + chrome:** Front / ¾ / Top presets + orbit/pan/zoom (drei controls fine), section-cut toggle (clip plane on X=0 with capped material look), enclosure show/hide (role-based visibility), the sketch's toolbar wired to all of it, LIVE/stale badge from the store's revision state, latency readout (evalMs from header + client frame time).
4. **LOD behavior:** render whatever revision+LOD frame is newest-valid; when a fine frame for the current revision arrives after its coarse, swap silently (nested stations upstream minimize pop); NEVER replace fine with coarse for the same revision. Keep last-valid geometry with stale badge on validation errors (store already models this).
5. **Rendering discipline (plan §4.6):** demand-rendering — render on new frame, camera interaction, mode change, resize; idle = zero GPU work; exposed `__wg2ViewportTestHook.forceFrame()` for tests/screenshots (spike lesson: at-rest WebGL is unverifiable otherwise). rAF-batched uploads; decode stays off the render path.
6. **Tests** (vitest, jsdom + headless-gl not available — test logic, not pixels): buffer-manager attribute replacement matrix (grow/shrink/same across all three attributes), frame→scene surface mapping (roles→materials, hard-edge surfaces not merged), LOD swap policy (fine-after-coarse same revision swaps; coarse-after-fine same revision ignored), demand-render scheduler (no renders when idle).
7. `npm run build` + `npx vitest run` green; no console errors against the live server.

## Rules
- Keep the telemetry readout available behind a small "stats" toggle (it proved useful) — off by default.
- Final message: component/file list, test counts, which display modes are fully functional vs stubbed (curvature may be data-limited), buffer-reuse edge cases handled, and what the overseer should verify live (interaction list).
