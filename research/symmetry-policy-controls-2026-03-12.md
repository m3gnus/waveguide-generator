# Symmetry Policy Controls Decision

Date: March 12, 2026

## Question

Should the simulation UI add explicit symmetry-policy controls such as `auto` or `force_full`?

## Evidence

Command run from repository root:

```bash
cd server && python3 scripts/benchmark_symmetry.py --iterations 10 --json
```

The repeatable fixture harness passed all benchmark cases:

| Case | Expected outcome | Observed outcome |
| --- | --- | --- |
| `full_reference` | no reduction | matched |
| `half_yz` | `half_x` reduction (`2x`) | matched |
| `quarter_xz` | `quarter_xz` reduction (`4x`) | matched |
| `quarter_xz_off_center_source` | no reduction because the source is off-center | matched |

Key observations from the benchmark output:

- The current automatic policy already keeps asymmetric geometry on the full model.
- Centered half-domain and quarter-domain fixtures reduce correctly without extra user input.
- The off-center source guard prevents unsafe reduction even when geometric symmetry exists.
- The results UI now exposes `metadata.symmetry_policy` and `metadata.symmetry`, so the solver's decision is visible after the run.

## Decision

Do not add explicit symmetry-policy controls right now.

The benchmark evidence shows that the existing `enable_symmetry` toggle plus automatic policy evaluation already covers the supported cases correctly:

- centered symmetric models reduce automatically
- asymmetric models stay full-domain
- off-center source placement correctly blocks reduction

Adding `auto` / `force_full` controls now would increase UI and contract surface area without solving a demonstrated runtime problem.

## Revisit Trigger

Reopen this decision only if at least one of these happens:

- benchmark or production cases show a false-positive or false-negative symmetry decision
- users need to compare forced full-domain versus reduced-domain solves for the same model
- new geometry classes introduce ambiguity that the current centered-source rule cannot explain
