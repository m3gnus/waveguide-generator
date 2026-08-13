# August 2026 rebuild history

Status: historical summary. This is a map to Git evidence, not an implementation plan.

Waveguide Generator was rebuilt as the current React/FastAPI application between
2026-08-03 and 2026-08-06, then hardened through platform, export, performance, and CAD
work. The detailed one-use task briefs and review ledgers were removed from the working
tree once their work was implemented; Git history retains them.

## Implementation batches

| Work | Result | Implementing commit |
|---|---|---|
| Phase 0 preview/WS/chart spike | Measured the rebuild risks | `34f16f9` |
| A — schema, text format, migrations | `server/design/` and corpus dry run | `8ae0eb2` |
| B — binary frame codec | Python/JS codec, shared fixtures, fuzz tests | `20fc4fc` |
| C — v1 contract mining | Export/result inventories and traceability seed | `f51a23c` |
| E — operational skeleton | FastAPI assembly, launch, data layout, pins | `17fe82f` |
| F — preview service | Mesher-backed `/ws/preview` | `634c8e8` |
| G — application shell | React/Dockview workspace and preview socket | `6fe2726` |
| I — 3-D viewport | Three.js rendering and analytic-normal modes | `4ae679f` |
| J/K — job and result spine | Durable jobs, events, jobs/results UI | `5162ddd`, `a7e22be` |
| P — parameter surface | Full ATH/WG parameter inventory | `83a4424` |
| Q — solver adapters | Metal/BEMPP/CircSym/IB mapping | `a6dc24d` |
| R — exports and file I/O | Geometry/result exports and config UI | `9a3b9ed` |
| S — viewport/shell polish | Front-side rendering and interaction fixes | `46ebd31` |
| FF/FF2 — FREEFORM simplification | Solved tangents and one normalized axial coordinate | `624d52b`, `6e8c89c` |
| AS — automatic symmetry | Geometry-derived reduction; CircSym became a fast path | `2ade812` |
| UX — workspace controls | Layouts, settings, compact tabs, command palette | `a545323` |

## Review disposition

The first Luna review wave produced the triage ledger at `87f5868` and three fix
commits: `33ec5db`, `9b14b42`, and `142eaa5`. The v1 input audit at `748a6c6`
identified 60 missing rows and 43 expression-domain gaps; the four remediation commits
were `1099637`, `925100b`, `778254f`, and `27685ce`.

A real-browser walkthrough then found a degenerate seed morph and an OrbitControls
key-events crash (`0dd1757`). The final full-repository review (`637688b`) reported no
P0, five P1 integration seams, and seven P2 findings. The fix round `9560955` closed
all twelve with regressions for WebSocket recovery, app-lifetime job coordination,
atomic solve records, expression spelling, fidelity aggregation, engine registry,
undo epochs, retryable exports, and the shared version source.

Later commits supersede the original review claims. Current compatibility is summarized
in [V1-COMPATIBILITY.md](../legacy/V1-COMPATIBILITY.md); current contracts live under
`docs/reference/`.
