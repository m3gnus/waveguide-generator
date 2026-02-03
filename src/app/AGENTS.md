# App Module - AI Agent Context

## Purpose

Application bootstrap and cross-cutting orchestration (scene setup, UI wiring, exports, and event hookups).

## Files

| File | Purpose | Complexity |
|------|---------|------------|
| `App.js` | App class and lifecycle | Medium |
| `scene.js` | Three.js scene setup and rendering | Medium |
| `params.js` | Parameter parsing and preparation | Medium |
| `configImport.js` | Config file import handling | Simple |
| `events.js` | UI event wiring and keyboard shortcuts | Simple |
| `panelSizing.js` | UI panel resizing logic | Simple |
| `exports.js` | STL/CSV/Config/Gmsh exports | Medium |
| `mesh.js` | Simulation mesh handoff | Simple |
| `logging.js` | Change log initialization | Simple |

## Notes

- Keep this module focused on orchestration.
- Avoid direct cross-module coupling; use the event bus where possible.
