# WS-PROTOCOL v1 — preview + jobs channels

Status: Phase 1 contract (implements plan §4.2–§4.4; spike-validated patterns marked ✓).
Transport: WebSocket, one socket per channel per tab: `/ws/preview`, `/ws/jobs`.

## 0. Shared envelope

Client→server messages and server→client control messages are JSON:

```json
{ "v": 1, "kind": "<kind>", "epoch": <int>, ... }
```

- `v` — protocol version. Server rejects unknown majors with close code 4400.
- `epoch` — **connection epoch**: server assigns on accept (`hello`), client echoes on every message. Responses carry the epoch they answer; the client drops anything from a stale epoch (kills late frames from a pre-reconnect socket).
- Binary frames (preview geometry) follow FRAME-SPEC.md; their header carries `epoch`, `seq`, and `designRevision` so binary and JSON reconcile identically.

Handshake: server sends `{"v":1,"kind":"hello","epoch":N,"heartbeatSec":15,"limits":{"maxFrameBytes":...}}` on accept. Heartbeat: server pings every `heartbeatSec`; client closes + reconnects (exponential backoff, 250 ms → 5 s cap) on 2 missed beats.

## 1. `/ws/preview`

### Client → server

| kind | fields | semantics |
|---|---|---|
| `preview` | `seq`, `designRevision`, `design` (full design JSON), `lod` (`coarse`\|`fine`), `curvature` (optional bool, default `false`) | Request tessellation. Full design state every time (localhost bandwidth is free; no server-side design cache to desync). `curvature` asks for the FRAME-SPEC §5 curvature sections, which only the viewport's curvature heatmap reads; they are built on the dense canonical master and cost about a third of a fine build and an eighth of its bytes, so the other seven display modes leave it `false`. Honoured at `fine` only — coarse frames have never carried curvature — so a drag keeps one cache entry whatever the mode. |
| `curve` | `seq`, `designRevision`, `curveId`, `points` | 2-D editor curve evaluation request (small payloads, same coalescing). |

`seq` is per-connection monotonic. **`designRevision` is assigned by the client store on every committed mutation** (edit, undo, redo, load, family switch — plan §4.2); multiple seqs may share one revision (e.g. LOD refine), but a new revision always gets a new seq.

### Server behavior (✓ spike-validated)

- **One compute in flight per connection; one pending slot.** A newer request replaces the pending one; each replaced request gets `{"kind":"dropped","seq":S}`. Never queue more than one.
- Compute runs off the event loop (`asyncio.to_thread`); the socket stays responsive while a slow family (ICW rollback ≈1 s) computes.
- Reply per computed request: one binary frame (FRAME-SPEC), header `{epoch, seq, designRevision, lod, evalMs}`.
- Validation failure → `{"kind":"error","seq":S,"designRevision":R,"code":"validation","fields":{...}}`; runtime failure → `code:"internal"` with message. Errors carry revision so the client drops errors for superseded edits.
- A computed frame over `limits.maxFrameBytes` → `code:"too-large"` with message; **the socket stays open**. Closing with 4413 here wedges the viewport: the client reconnects, resends the same design, and the fine LOD closes the socket again forever while coarse keeps succeeding. The ceiling belongs to one request, not to the connection. 4413 remains the answer for an oversized *inbound* message, which is refused before any compute.

### Client rules

- Render a frame if `epoch` is current AND its `designRevision` is **not older than the displayed one and not older than the last discontinuous edit** (see below). Mark the viewport stale whenever `designRevision != store.currentRevision`.
- The original rule — render only when `designRevision == store.currentRevision` — is unreachable during any continuous gesture and was measured doing real damage: the store commits a revision per `pointermove` (~104/s in a real browser) while building a coarse preview takes 96 ms on an M1 Max and 192 ms on the Windows reference machine, so the revision has always moved on by the time geometry returns. Reproduced end to end against the real mesher: a 2.2 s drag produced 21 valid coarse frames and the client accepted **0**. That is the "0.6 displayed frames/s against 104 UI revisions/s" in `MACOS-PERFORMANCE.md`.
- A frame that lags the design by a few revisions is what the stale badge exists to describe. A frame older than a **discontinuous** edit is different: undo, redo, load and family-switch replace the design rather than advance it, so geometry in flight for a pre-undo revision is the state the user just rejected. Those edits all carry `immediate`, and their revision becomes a barrier that older frames never pass — which is conformance case 2 in §4 below.
- On revision mismatch: keep displaying `lastValidRevision`'s geometry with a **stale badge** (v1 FREEFORM behavior, kept deliberately).
- Undo/redo/load/family-switch: cancel debounce timers, bump revision, send immediately (plan §4.2).
- Reconnect: new epoch from `hello`, then immediately resend current full state (`preview` at current revision, fine LOD). Nothing is replayed server-side; the preview channel is stateless per request.

## 2. `/ws/jobs`

Design principle (review R2-P1.1): **WS is the fast path; HTTP remains the correctness path.** Durable job state is derivable by refetching `GET /api/jobs` + `GET /api/status/{id}`. In-flight frequency results are intentionally process-local; a dropped delta is repaired from `GET /api/partial-results/{id}`, while the completed result remains the sole durable artifact at `GET /api/results/{id}`.

### Server → client

| kind | fields | semantics |
|---|---|---|
| `snapshot` | `cursor`, `jobs:[...]` | Sent on connect: full current job list + the event cursor it corresponds to. |
| `event` | `cursor`, `jobId`, `type` (`queued`\|`started`\|`progress`\|`stage`\|`log`\|`completed`\|`failed`\|`cancelled`\|`deleted`\|`metadata`), payload | One job lifecycle event. `cursor` is a server-side monotonically increasing event id (persisted with the job store). |
| `partialResult` | `jobId`, `revision`, `snapshot`, `result` | Ephemeral frequency result. Live messages carry one frequency-shaped delta with `snapshot:false`; connect/recovery messages carry the accumulated process-local result with `snapshot:true`. These messages have no durable event cursor. |

### Client → server

| kind | fields | semantics |
|---|---|---|
| `resume` | `cursor` | Optional on reconnect: server replays events after `cursor` if still retained, else replies with a fresh `snapshot`. |

- `log` events carry bounded tail chunks (server truncates; full log via HTTP).
- Client reconciliation rule: on reconnect or any cursor gap → refetch snapshot over HTTP/WS; never trust an event stream with a hole.
- `partialResult.revision` starts at 1 and increments per completed frequency. A revision gap triggers `GET /api/partial-results/{id}` and replaces the local accumulator with that full snapshot. A server restart may remove this process-local snapshot; the client then keeps the previous completed result until the canonical completed result is available.
- The provisional mapper uses the same SPL, phase, impedance, DI, and polar normalization contract as the final mapper. BEMPP's serial callback currently omits complex observation pressure, so its live view can update normalized directivity while absolute SPL/phase remain empty until completion. An explicitly configured multi-worker BEMPP sweep does not stream because that native path has no callback seam.
- Multi-tab: each tab has its own socket/epoch; job mutations (stop/delete) go over HTTP; races resolve server-side and broadcast as events (last-writer wins, `deleted` is terminal).

Persisting frequency chunks in SQLite was considered and deliberately left out of this change. It would require a chunk schema, transaction/replay semantics, retention and migration policy, and rules for exports of incomplete solves. The in-memory accumulator keeps the existing one-commit final-result architecture while still matching Boundary Lab's live solve feedback.

## 3. Close codes

| code | meaning |
|---|---|
| 4400 | unsupported protocol version |
| 4401 | origin rejected (localhost guard) |
| 4408 | heartbeat timeout (server-initiated) |
| 4413 | inbound message too large (an over-budget outbound frame is a `too-large` error, not a close) |
| 1012 | server restarting (client: reconnect with backoff) |

## 4. Conformance tests (gate G2)

1. Drag sweep at 30 Hz: bounded pending (≤1), dropped notices for the rest, latest revision always painted last. ✓ (spike)
2. Undo during in-flight drag → old-revision frame arrives → NOT rendered; stale badge until new frame.
3. Undo before trailing debounce fires → trailing timer cancelled, no request at dead revision.
4. Reconnect mid-drag: stale-epoch frame dropped; resent state renders; exactly one `hello`.
5. Server restart during solve: jobs channel resnapshots; no duplicate/lost terminal events (cursor proof).
6. Two tabs: stop/delete race → both converge to the same job list from events alone.
7. Sleep/wake ≥ heartbeat window → clean reconnect + snapshot.
8. Malformed JSON / oversized message → error or 4413 close; connection state machine never wedges.
