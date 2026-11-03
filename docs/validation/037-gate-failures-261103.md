# 0.3.7 gate corrections — 2026-11-03

Source base: `e438998d9`; failed checkpoint: `28f62c370`.
The mesher pin and checkout are unchanged. No push, merge or release operation
is part of these corrections.

## UI catalog compatibility (item 3)

The installed pinned mesher catalog retains `editor.Drive` with legacy key and
label `Drive`. WG keeps that catalog identity while displaying the sensible
section title **Drivers**. The new visible **Crossover** section is also absent
from the pinned catalog, so WG excludes its new catalog entry for 0.3.7.
The exact installed-catalog equality assertion is unchanged.

For **0.3.8**, update the mesher's `hornlab_mesher/control_labels.json` and WG's
catalog together: replace `editor.Drive` with `section.Drivers` (legacy key and
label `Drivers`) and add `section.Crossover` (legacy key and label `Crossover`).
Then remove WG's two compatibility mappings and move the mesher pin only after
that coordinated contract is qualified.

Validation: `test_errors_round3.py` **49 passed** against the installed pinned
catalog; the first run exposed the additional Crossover entry, which was
reconciled without altering the contract test. Node 20 `npx tsc --noEmit` passed; affected Vitest files
`refusalCatalog.test.ts` and `parameterRegistry.test.ts`: **45 passed**.
