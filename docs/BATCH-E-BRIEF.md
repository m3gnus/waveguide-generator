# Phase 1, Batch E — operational skeleton: app assembly, launcher, data layout, pins

Implement the v2 operational contract (plan §8 Phase 1; review findings R1-P1-8 / R2-P0.7 drove this) in this repo.

**Path discipline (concurrent agents): create/modify ONLY `server/app.py`, `server/platform/**`, `server/engines/**`, `server/tests/test_app*.py`, `server/tests/test_platform*.py`, `scripts/gen_requirements.py`, `pins.json`, `launch/**`, `frontend/dist/index.html` (placeholder). Do NOT touch `server/design/`, `server/protocol/`, `shared/`, `docs/`, `spike/`.**

Runtime: `../Waveguide Generator/.venv/bin/python` (FastAPI 0.136, pytest; no new dependencies — hand-roll what a library would give you).

## Deliverables

1. **`server/app.py`** — FastAPI v2 assembly: `/health` (version, uptime, data-dir path), `/api/capabilities` (engine registry report; see 4), serves `frontend/dist/` at `/` (create a minimal placeholder `frontend/dist/index.html` saying "Waveguide Generator v2 — shell under construction" with the parchment-light styling tokens as a nod), localhost-origin guard middleware (reject non-local origins), request logging.
2. **`server/platform/`**:
   - `paths.py` — versioned per-OS data layout, hand-rolled: macOS `~/Library/Application Support/WaveguideGenerator2/`, Windows `%APPDATA%/WaveguideGenerator2/`, Linux `$XDG_DATA_HOME|~/.local/share/WaveguideGenerator2/`; subdirs `db/`, `logs/`, `locks/`; everything overridable via `WG2_DATA_DIR`. Never touches v1's data.
   - `instance.py` — single-instance lock (pid file with staleness detection in `locks/`), plus port acquisition: default **3100**, `--port`/`WG2_PORT` override, and if busy scan +1..+9 with a clear log line (review: v1 sits on 3000 during the beta — never fight it).
   - `logging_setup.py` — log to `logs/server.log` (simple size-capped rotation: rename to `.1` at 5 MB) + stderr.
3. **`launch/serve.py`** (+ `python -m server` support): parse `--port`, `--no-browser`, `--data-dir`; acquire lock; start uvicorn programmatically; open the browser on readiness (skip with `--no-browser` or when `WG2_NO_BROWSER=1`); graceful shutdown on SIGINT/SIGTERM (release lock, flush logs); on lock conflict print the running instance's pid/port and exit 2.
4. **`server/engines/`** — engine registry seam: `registry.py` with `detect_engines() -> list[EngineInfo]`; for now registers only `dryrun` (guarded: only when `WG2_ENABLE_DRYRUN=1`, per the no-mock-solvers-in-production contract) and placeholders returning "not detected" for `metal`/`bempp`/`circsym` (real detection is a later batch; the seam + report shape is what matters now: name, available, reason, version).
5. **`pins.json` + `scripts/gen_requirements.py`** — single-source module pins (plan §6.4). Seed pins.json from `spike/oracle/v1-manifest.json` (module → repo URL + SHA; repos are `github.com/m3gnus/<name>`). The generator emits `server/requirements-pins.txt` (git+https pinned lines) deterministically; `--check` mode diffs instead of writing. A pytest asserts pins.json ↔ generated file consistency.
6. **Tests** (pytest, no network, no real browser): data-dir resolution per-OS (monkeypatch env/platform), lock acquire/stale/conflict, port fallback scan, health + capabilities via TestClient, origin guard rejects non-localhost, pins generator determinism + `--check`, dryrun engine hidden without the env flag.

## Rules

- Self-verify: run the new tests plus the whole `server/tests` suite (batch A+B tests must stay green — they share the venv, not your paths).
- Log lines and error messages should be the quality you'd want at 2 a.m. on a Windows box: say what happened AND what to do.
- Final message: files created, test counts (new + full suite), the exact launcher invocation, and any deviations with reasons.
