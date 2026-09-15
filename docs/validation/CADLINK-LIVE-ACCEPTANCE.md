# CAD Link live acceptance checklist

For the release owner. This is the walk-through that a local test suite cannot
do for you: it runs against a real Fusion 360 and a real, packaged WG
candidate, and it is what closes the "live-Fusion acceptance still owed" gap
named in `hornlab-policy/CADLINK-ARCHITECTURE-PLAN.md` and
`hornlab-policy/CADLINK-M1-M4-TRIAGE.md`. It does not replace
`server/tests` or `scripts/tests`, which cover the same contracts against
fakes; it is the proof that the real add-in and the real WG agree with them.

Fill in one copy per platform per candidate. Leave `Result` and `Evidence`
blank until you run the step; do not mark a row done from memory.

## 0. Before you start

- **Fresh OS user.** Not an account that has ever run an older WGLink or an
  older WG. A leftover managed package, data directory or Fusion AddIns entry
  invalidates every row below it.
- **Packaged candidate**, not a checkout run from source: the installer or
  bundle the release would actually ship.
- **Fresh WG data directory** (`WG2_DATA_DIR` unset, or pointed at an empty
  directory) — see `server/platform/paths.py`.
- **Fusion 360 with NEW test documents only.** Never a document that matters;
  several steps below intentionally kill Fusion, wait past a timeout, or
  create a baseline conflict.
- Record, once, for this run: WG commit (`git rev-parse HEAD` in the built
  candidate's source, or the version string in the About dialog), the
  installed WGLink's `sourceCommit` (from its heartbeat or `About`), the
  Fusion build number, the OS and version, and the date.
- After **every** numbered step below, run the evidence collector and keep
  its zip with this filled-in checklist:

  ```
  python scripts/cadlink_evidence.py <WG2_DATA_DIR> --operation-id <id-if-one-exists> --output step-<N>.zip
  ```

  `scripts/cadlink_evidence.py` is read-only: it never writes to the data
  directory it reads, and a step you have not reached yet (no operation
  published, no heartbeat yet) is reported as "not present" in
  `manifest.json` rather than crashing the collector. See its module
  docstring for exactly what each zip member is and where it comes from.

## Run record

| | |
|---|---|
| Platform | |
| OS version | |
| WG commit | |
| WGLink `sourceCommit` | |
| Fusion build | |
| Operator | |
| Date | |

## 1. Clean-room walk-through

One continuous run, in order — later steps depend on state earlier steps
created. `hornlab-policy/CADLINK-ARCHITECTURE-PLAN.md` §6 is the source for
this sequence.

| # | Step | What must be true | Result | Evidence |
|---|---|---|---|---|
| 1 | Start WG with Fusion closed, then start Fusion. | WGLink installs itself into Fusion's AddIns while Fusion is closed; once Fusion opens, WG's `.fusion-status.json` reports a heartbeat at `deliveryVersion` 3 (`server/cadlink/fusion_delivery.py` `DELIVERY_VERSION`). | ☐ pass ☐ fail ☐ blocked | |
| 2 | Hand-install an **older** WGLink into Fusion's AddIns, restart Fusion; then restart WG. | With the older add-in running, WG's heartbeat read shows the outdated state and WG surfaces `ADDIN_OUTDATED_MESSAGE` (`server/cadlink/fusion_status.py`). After WG restarts, the older add-in is replaced; after a further Fusion restart, the current add-in loads and works. | ☐ pass ☐ fail ☐ blocked | |
| 3 | Send a source to WG as an **insert**, then as an **update**. Create two copies of the linked instance in one assembly and update only one. | Insert and update both complete and the returned bundle validates (`server/cadlink/wgreturn.py`). Updating one copy touches only that copy's instance — the other's `assembly_from_link` and body are unchanged. | ☐ pass ☐ fail ☐ blocked | |
| 4 | While an update is in flight, edit the linked body in Fusion; separately, kill Fusion immediately after an update **applies** (before WG can observe it) and restart Fusion. | The concurrent edit is refused as a baseline conflict, not silently overwritten. The update that applied before the kill is later recognised as `accepted`/reconciled, not re-run, once Fusion is back (0B.1/0B.6, `hornlab-policy/CADLINK-ARCHITECTURE-PLAN.md`). | ☐ pass ☐ fail ☐ blocked | |
| 5 | Kill Fusion **during** an update (mid-apply), then restart WG (not Fusion) before doing anything else. | Both Fusion (on its next open) and WG report "recovery required" for that operation. The recovery state survives the WG restart — it is read from durable storage, not memory. Undo in Fusion and Dismiss in WG both behave as documented (README's Undo limitation). | ☐ pass ☐ fail ☐ blocked | |
| 6 | With document A open and unsaved changes pending, run Solve in WG for document B. Then use "Send to WG" from Fusion for A. | A's unsaved edits are untouched — WG solves B with the engine WG selected, never A. "Send to WG" from Fusion does not create a new WG job (it is a data delivery, not a solve trigger — CL05 in `hornlab-policy` planning). | ☐ pass ☐ fail ☐ blocked | |
| 7 | Close Fusion. Delete the return from the WGLink exchange folder on disk. Restart WG. Solve the design again. | WG solves from its own retained snapshot (`prepare_and_solve`, `docs/architecture/CAD-OPERATIONS.md`) without needing Fusion running or the file present. | ☐ pass ☐ fail ☐ blocked | |
| 8 | Create two root-fallback linked instances in one assembly with different placement offsets. Then mirror one linked component in Fusion and send it. | The two root-fallback links resolve to their own, distinct source axes (not one axis reused for both — M2, `hornlab-policy/CADLINK-M1-M4-TRIAGE.md`). The mirrored instance is refused at validation, naming the offending instance (M1; `server/cadlink/wgreturn.py` `_validate_instance`) — not silently accepted and solved backwards. | ☐ pass ☐ fail ☐ blocked | |
| 9 | Publish an insert from Fusion, then quit Fusion and wait more than 30 minutes before starting it again. | The insert is not applied — it expired before Fusion ever saw it, and WG's operation record for it shows `cancelled`/`expired`, not a silent drop (`INSERT_HANDOFF_TTL`, CL01a in the CAD Link planning docs). | ☐ pass ☐ fail ☐ blocked | |
| 10 | Install this candidate over an existing **0.3.2** install that has a mix of completed, pending and retained CAD items. | Nothing already completed replays. A pending item resumes rather than restarting. Retained snapshots remain solvable (step 7's contract, against pre-existing data). | ☐ pass ☐ fail ☐ blocked | |

## 2. Acceptance items the single-machine walk-through above does not cover

These need either a second/third platform, a real prior-release install, or a
pin move — none of which the sequence above exercises by itself.

| Item | What must be true | Result | Evidence |
|---|---|---|---|
| Packaged fresh-install gate, macOS | Step 1 above, run clean on macOS. | ☐ pass ☐ fail ☐ blocked | |
| Packaged fresh-install gate, Windows | Step 1 above, run clean on Windows. | ☐ pass ☐ fail ☐ blocked | |
| Packaged fresh-install gate, Linux | Step 1 above, run clean on Linux. | ☐ pass ☐ fail ☐ blocked | |
| Windows RC proof of the CAD child `PYTHONPATH`/`._pth` fix | A Windows release-candidate build reproduces the fix for the CAD child process import gate (tracked as owed against commit `cf7f526e` in prior session notes) — a real Windows RC, not a local simulation. | ☐ pass ☐ fail ☐ blocked | |
| Setup publisher survives an app restart | Start building a setup in WG, restart the WG application before publishing, and confirm the in-progress setup is recovered rather than lost (Phase 3 of the CAD Link plan). | ☐ pass ☐ fail ☐ blocked | |
| Upgrade path, mixed CAD item states, second platform | Repeat checklist row 10 on a platform other than the one already run, since an installer/upgrade defect is frequently platform-specific. | ☐ pass ☐ fail ☐ blocked | |

## 3. Pin-move requalification (only when a pin move ships this release)

Each `PM-n` pin move (see `hornlab-policy`'s CAD Link work-package table) needs
its own pass after the add-in build it pins is in place, on every platform the
release ships:

| Pin move | What must be true | Result | Evidence |
|---|---|---|---|
| PM-1 (after CL03) | The managed WGLink package rebuilds and installs clean; `test_wglink_package.py` and `test_wglink_activation.py` both pass against the built package, not only against source. | ☐ pass ☐ fail ☐ blocked | |
| PM-2 (after CL14a/b, if shipped) | Same as PM-1, plus: a mixed-version pairing (old add-in talking to the new WG, and vice versa) degrades to the documented fallback rather than failing silently. | ☐ pass ☐ fail ☐ blocked | |
| PM-3 (after CL25a/b, if shipped) | Same as PM-1, plus: source-identity reassignment (if shipped) behaves as specified end to end with a real Fusion session. | ☐ pass ☐ fail ☐ blocked | |

## 4. Phase 4/5 rows — only if these phases ship in this release

As of this writing Phases 4 and 5 (items 1-4) are **not** recommended for the
0.3.3 cut (`hornlab-policy`'s CAD Link planning notes, §5 and §7). If a future
release does ship them, add and complete these rows before calling that
release's CAD Link surface accepted:

- Live-session reconnect, token refresh, and the outbox fallback, each
  exercised at least once with a real network interruption.
- The add-in's loaded identity is what WG expects after a live-session
  handshake.
- A mixed pairing (one side on the live-session transport, the other still on
  file-based v3 delivery) behaves per the compatibility notes in the CAD Link
  plan, not by accident.
- Each update-journal phase (`prepare` → `apply` → `verify`) interrupted in
  turn, confirming the timeline marker is restored and the recorded phase
  matches what actually happened.
- A face removed or split in the CAD source after linking is handled per the
  documented contract, not a silent mismatch.
- The unlinked-solver-frame preview (if shipped) matches the frame WG
  actually solves in.

## References

- `hornlab-policy/CADLINK-ARCHITECTURE-PLAN.md` — the CAD Link architecture
  and phase plan this checklist is derived from.
- `hornlab-policy/CADLINK-M1-M4-TRIAGE.md` — M1-M4 issue triage (chirality,
  root-fallback datum ownership, update reconciliation ordering, distinct
  capabilities).
- `docs/architecture/CAD-OPERATIONS.md` — the operation-kind contract between
  WG and a CAD adapter.
- `scripts/cadlink_evidence.py` — the read-only evidence collector this
  checklist runs after each step.
