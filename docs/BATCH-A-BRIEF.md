# Phase 1, Batch A — design schema + `.cfg` text grammar + migration dry-run

You are implementing the first production batch of Waveguide Generator v2 (this repo). Unlike `spike/`, this is **real code**: clean, typed, tested. The v1 app at `../Waveguide Generator` (note the space) is **read-only ground truth** — never write there.

Runtime: `../Waveguide Generator/.venv/bin/python` (Python 3.13, Pydantic v2 via FastAPI 0.136, pytest 9.0.3 available). No new dependencies.

## Context

v2 keeps v1's ATH-style *text* design format, saved as `.cfg`/`.txt` (legacy `.mwg` readable — same grammar, different suffix). JSON is only the in-memory/API representation. Your job: a server-side formal implementation of that format + the complete Pydantic design schema + a dry-run migration report over the real corpus. This kills the biggest migration risk found by review (two independent reviews flagged "format break disguised as migration" as plan-breaking).

## Ground truth (derive the grammar from these, in this order)

1. `../Waveguide Generator/src/export/mwgConfig.js` — the writer: header comment, key = value lines, block emission, `_blocks` passthrough preservation (~line 282).
2. `../Waveguide Generator/src/config/index.js` — the parser: raw-value retention, expression strings, unknown-block preservation (~line 266).
3. `../Waveguide Generator/src/modules/design/useCases.js` (~line 17) — content-based ATH-vs-MWG import behavior.
4. The corpus: `../Waveguide Generator/output/*/script.snapshot.mwg` (191 files) and `../Waveguide Generator/tests/fixtures/ath/` — the grammar is only correct if it handles every one of these.
5. Schema field inventory: every key mwgConfig.js can write; keys `waveguide_payload_to_mesher_config` accepts (`../Waveguide Generator/server/solver/mesher_adapter.py` ~line 190); `../Waveguide Generator/src/ui/parameterInventory.js`.
6. FREEFORM legacy-field migrations: v1 dropped `cornerRatio`/`corner_ratio` and the `'allow'` alias with a state migration (search v1 `src/` for that migration; port its *semantics*). The spike found a real corpus file that fails today's viewport API on `corner_ratio` — your migration layer is what makes such files load.

## Deliverables

```
server/design/__init__.py
server/design/schema.py      # Pydantic v2 models
server/design/textcfg.py     # parse / serialize
server/design/migrate.py     # legacy-field migrations (versioned, ordered, documented)
server/tests/test_schema.py
server/tests/test_textcfg.py
server/tests/test_migrate.py
server/tests/test_corpus.py  # parametrized over the full real corpus
scripts/migration_dryrun.py  # writes docs/migration-dryrun-<YYMMDD>.md
docs/CFG-FORMAT.md           # the formal grammar, written down at last
```

### schema.py
- Root `DesignConfig` with a discriminated union on `formula` for profile params: OSSE, R-OSSE, ICW, FREEFORM (model every field each family actually uses in the corpus/inventory — including morph, rollback (depth/curl), coverage mode, source shape/velocity convention, quadrants, mesh segments/sampling/Z-map/resolution/max-size guards, enclosure (depth/edge/space…), sim_type (freestanding / infinite-baffle), wall thickness, FREEFORM spline tables + shape stations + per-ring corner grids).
- **Raw-expression preservation:** any numeric field may carry a v1 raw string expression; keep `raw` alongside the evaluated value (model with a custom scalar type, e.g. `Expr`), so serialize can emit the original expression text unchanged.
- Unknown keys/blocks must survive: `extra_blocks` / `extra_keys` containers, not errors.
- `model_config` strict where safe; validators mirror v1 semantics, never invent stricter rules that would reject corpus files.

### textcfg.py
- `parse(text: str) -> ParsedDesign` (model + preserved unknowns + per-key raw strings; tolerate comments; record a source-dialect flag: `mwg` vs `ath` per useCases.js sniffing).
- `serialize(design: ParsedDesign | DesignConfig) -> str` — output must remain **v1-readable** (v1 header line kept; a v2 version-discriminator comment v1 ignores; stable key order matching mwgConfig.js so diffs stay small).
- Round-trip law: `serialize(parse(text))` is semantically lossless (unknown blocks, expressions, comments preserved at least at block level); byte-identical where v1 itself is deterministic.

### migrate.py
- Ordered named migrations (e.g. `001_corner_ratio_to_corner_grid`), each: applies-if predicate + transform + note for the report. Applied at parse time behind `migrate=True`.

### test_corpus.py + scripts/migration_dryrun.py
- Parametrize over ALL 191 snapshots + ath fixtures. For each: parse → classify `clean | migrated (list) | unknown-blocks-preserved | expected-error (reason)`; for parseable: round-trip and assert semantic equality; validate against schema.
- The dry-run script writes `docs/migration-dryrun-<YYMMDD>.md`: totals by class, per-migration hit counts, and a table of every non-clean file with its reason. Zero silent failures: every file appears in exactly one class.
- An `expected-error` classification requires a stated reason (e.g. truly malformed) — target: overwhelming majority parse clean or migrated.

## Rules

- pytest style, type hints, docstrings that state the v1 file/line a behavior mirrors (that's the traceability the plan demands).
- Self-verify: run the full test suite and the dry-run script; iterate until green; include the generated report in your summary.
- Do not touch `spike/`. Do not run servers. Bounded exploration: the five ground-truth files above + corpus files as data; don't crawl the whole v1 tree.
- Final message: files created, test counts, dry-run classification totals, any corpus files you could not handle and why, deviations from this brief with reasons.
