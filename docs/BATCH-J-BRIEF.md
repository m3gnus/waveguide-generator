# Phase 3, Batch J — jobs & solve spine (server)

Port the v1 job runtime into v2 and wire the dry-run engine end-to-end, per plan §4.4/§8-P3 and WS-PROTOCOL §2. The v1 sources embed years of lifecycle gotchas — port semantics faithfully, cite v1 file:line in docstrings.

**Path discipline: create/modify ONLY `server/jobs/**`, `server/engines/**` (extend the registry), `server/tests/test_jobs_*.py`, and `server/app.py` (mount lines only).** v1 at `../Waveguide Generator` is read-only ground truth: `server/services/job_runtime.py` (SQLite registry, FIFO scheduler, strong task refs, startup recovery ~line 629), `server/db.py` (schema ~line 41 — port it as-is per plan §4.5), `server/api/routes_simulation.py` (endpoints incl. metadata/ratings ~line 253).

## Deliverables

1. `server/jobs/store.py` — ported SQLite schema (jobs/results/artifacts tables as v1 has them; same columns so the Phase 6 migration is a data copy, not a transform), transactional writes, WG2 data-dir location (§ platform paths).
2. `server/jobs/runtime.py` — FIFO scheduler as asyncio tasks with strong refs (v1 pattern), stages+progress+log-tail capture, cooperative cancellation at every stage, startup recovery of queued/running orphans, clear-failed, metadata patch (label, rating).
3. `server/jobs/events.py` — the `/ws/jobs` channel per WS-PROTOCOL §2: snapshot-on-connect with cursor, monotonic persisted event ids, `resume` replay-or-snapshot, bounded log-tail events. HTTP remains the correctness path.
4. REST: submit (`POST /api/solve` accepting a v2 DesignConfig + solve options), status/list/stop/delete/clear-failed/metadata — shapes documented, generated-client-friendly.
5. Dry-run engine grows up: given a design, produce deterministic canned-but-plausible results (frequencies, on-axis SPL, H/V directivity matrices, impedance) with realistic stage progression (mesh → assemble → solve → postprocess, small sleeps) so the UI pipeline is fully exercisable; still gated by `WG2_ENABLE_DRYRUN`.
6. Tests — the backend-invariants slice from plan §8-G3: FIFO order; cancellation at each stage; startup recovery (kill mid-run simulated by constructing orphan rows); DB write-failure handling; delete/clear races; event-cursor monotonicity + resume semantics; store round-trips. Protocol core tested transport-agnostically (sandbox can't bind).

## Rules
- No new dependencies. Solve execution model: asyncio tasks + to_thread (plan §4.4) — no processes.
- Self-verify: full `server/tests` green. Final message: files, new/total test counts, v1 behaviors you deliberately did NOT port yet (with reasons), and the overseer live-test recipe (submit a dry-run solve via curl + watch /ws/jobs).
