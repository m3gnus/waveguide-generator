# Proposal: the `WG.Solve` `Engine` / `SolverMode` contract

**Status: enacted in full, 2026-09-05.** C1, C2b, C3 and C4 are all done.
`CFG-FORMAT.md` no longer advertises `Engine` in its canonical block and no
longer claims the key is validated; the open report shows each migration's note
instead of its internal identifier; and a stated `WG.Solve.Engine` or
`SolverMode` is now named in that report rather than passing in silence. The
contract as built differs from the proposal below in two places, both recorded
under "How C1 was built".
**Answers:** the plan's "Engine selection contract — `WG.Solve`'s `Engine` and
`SolverMode` are silently ignored and stripped; `CFG-FORMAT.md` implies
otherwise. Decide and document."

## The problem, stated precisely

Three things disagree today.

**1. `CFG-FORMAT.md` shows the key in its canonical example.** The `WG.Solve`
block sample leads with `Engine = metal`, so the documented shape of a
well-formed config contains a key that does nothing.

**2. The same document says the key is validated.** Its table row reads:

> | `Engine` | backend name, or `auto` | Validated for shape only; the backend registry owns the names. |

Nothing validates it. `Engine` appears exactly once in `server/design/textcfg.py`
— inside `_MACHINE_SOLVE_KEYS` — and that set is only ever used to *remove* the
key. There is no shape check, no name check, no diagnostic. A user who writes
`Engine = mteal`, or `Engine = bempp` on a machine with no bempp, gets silence
and a solve on whatever AUTO picked.

**3. The same document, further down, says it is ignored.** It states that
`Engine` and `SolverMode` "are therefore not honoured on any host and are
stripped from a design WG writes", and that `Simulation.SolverMode` "reports
itself when dropped ... so stating one cannot be mistaken for setting one".

That last sentence names exactly the property the `WG.Solve` keys lack.
`Simulation.SolverMode` really does report itself — `server/design/migrate.py`
emits *"Dropped Simulation.SolverMode. The solver path (Auto, Full 3D, or ...)"*.
Its `WG.Solve` siblings are removed by `_block_without_machine_solve_keys` with
no note at all. **The legacy spelling of this setting is better behaved than the
current one.**

So the observable defect is not that the keys are ignored — that part is a
deliberate and, I will argue, correct decision. It is that they are ignored
*silently*, while the reference document simultaneously advertises them and
claims they are checked.

## What the current behaviour gets right

The reasoning already written into `CFG-FORMAT.md` is sound and should survive
any change:

> Two settings are deliberately not portable, because they describe the machine
> rather than the design.

A `.cfg` is a design artifact that moves between machines. `Engine = metal` is
meaningless on Windows; `Engine = bempp` will be meaningless there too once the
bempp retirement lands. Honouring a stored engine name would make a design file
fail, or silently change physics, depending on which machine opened it. WG
already has the right mechanism for reproducing a specific run: exporting a
config from a finished run writes that run's own recorded solve options.

**The strip is correct. Only its silence is not.**

## Proposed contract

### C1 — `Engine` and `SolverMode` are *diagnosed*, not honoured

Keep the strip. Add the report that `Simulation.SolverMode` already has: when a
`WG.Solve` block carries `Engine` or `SolverMode`, drop it *and* emit a
migration note naming the key, its value, and what actually chooses the backend.
Proposed wording, matching the existing note's voice:

> Dropped `WG.Solve.Engine` (`metal`). Which backend runs a solve depends on the
> host, so it is chosen per machine rather than stored in a design. Pick an
> engine in Solve options, or leave it on Auto.

This makes the document's own claim — *"stating one cannot be mistaken for
setting one"* — true for both spellings instead of one.

#### How C1 was built, and why it is not the paragraph above

The intent stands; two details of the sketch were wrong, and correcting them
made the change smaller rather than larger.

**It is not a migration, and the note does not say "dropped".** The proposal put
the diagnostic in `_block_without_machine_solve_keys`, which runs from
`_emit_block` — at *serialize*. Import strips nothing: after `parse()`,
`WG.Solve.Engine` is still in `extra_blocks["WG.Solve"].items`, and it has to
stay there, because `serialize(parse(source)) == source` for an untouched file is
a tested contract. So the keys are neither dropped at import nor migrated, and a
note claiming either would be false. A no-op migration was rejected for the same
reason: `MigrationApplication` records a transform that happened.

**It is a computed property, not stored state.** `ParsedDesign.ignored_settings`
derives the list from the retained block on each read:

```python
@property
def ignored_settings(self) -> list[IgnoredSetting]:
    block = self.extra_blocks.get(_WG_SOLVE_BLOCK)
    ...
```

Nothing is recorded at parse, so there is no second copy to keep in step with the
block, and lossless round-tripping is unaffected by construction. The key set is
`_MACHINE_SOLVE_ADVICE`, the same tuple the serializer's strip set is derived
from, so the keys that are ignored and the keys that are reported cannot drift.

**The wire cost was one additive field**, not the five-file API change this
document previously estimated. `ignoredSettings` — a list of `{key, value, note}`
— joins `migrationsApplied` in `server/design_io/api.py:_report`, so both
`/design/open` and `/design/import-report` carry it, and in the `wg validate`
payload, where `render.py` prints one `Ignored` line per entry. Both endpoints
return `dict[str, Any]`, so the OpenAPI snapshot is byte-unchanged;
`scripts/gen_openapi.py --check` passes without regeneration. On the client the
field is optional, so a server without it changes nothing.

**Wording.** The note names the key, quotes the value as written, says the solve
ignores it, and says where to choose the setting:

> `WG.Solve.Engine` states `'metal'`. Which backend runs a solve depends on the
> host, so the solve ignores it. Choose the engine in Solve options.

It does not say the key was removed, because at that moment it was not.

### C2 — validate the shape, since the document promises it

Two honest options, and I recommend the second:

- **C2a — implement the promise.** Check `Engine` against the registry's engine
  names plus `auto`, and reject an unknown name as a `TextConfigError`.
- **C2b — retract the promise.** A key that is discarded should not also be a
  parse error, because that punishes a file WG itself can no longer produce
  while changing nothing about the solve. Delete the "Validated for shape only"
  claim from the table and let C1's diagnostic carry the whole message.

**Recommend C2b.** Validating a value we are about to throw away buys the user
nothing and adds a failure mode to old files. The diagnostic is the feature.

### C3 — stop advertising the keys in the canonical example

Remove `Engine = metal` from the `WG.Solve` sample block in `CFG-FORMAT.md`. The
example is what people copy; it should not seed a key the importer deletes. Keep
the prose section that explains *why* the keys are not portable, and keep the
table rows — but move them to a short "keys WG accepts and discards" list, so a
reader meets them as history rather than as configuration.

### C4 — make the migration note reachable — DONE, and it was cheap

This proposal called C4 "the one part with a real cost". That was wrong, and the
error is worth recording because it was what held C1. The note was never
missing from the wire: `server/design_io/api.py` has always sent
`migrationsApplied[].note`, and `frontend/src/api/designIo.ts` has always typed
it. Only `reportText` in `frontend/src/design/DesignFileMenu.tsx` threw it away,
joining `item.name` — so an opened file that silently lost a setting reported
`006_machine_solver_mode_not_portable` to a user with nowhere to look that up.

Fixed by showing the notes. Every existing migration gained an explanation at
once, `Simulation.SolverMode` included. The gate this section set is therefore
met: a note added by C1 would reach the screen.

### What C1 cost, once its site was right

Recorded because this proposal's own estimate was wrong twice, in opposite
directions, and both errors were about *where* the diagnostic belongs.

It first read as cheap — "reuse the existing note mechanism" — which it is not:
`MigrationApplication` records an applied transform, and nothing here transforms
anything. It then read as expensive — a second notes channel across
`ParsedDesign`, both endpoints, `openapi.v1.json`, the CLI renderer and the
frontend type. That was closer, but it counted the OpenAPI snapshot, which does
not move because both endpoints return `dict[str, Any]`, and it counted a
persisted field where a computed property does.

What it actually took: one frozen `IgnoredSetting` record and one property in
`server/design/textcfg.py`, one list comprehension in each of `_report` and the
`wg validate` payload, one line in `render.py`, an optional field in the frontend
type, and a `[...migrations, ...ignored]` spread in `reportText`. See "How C1 was
built" above for the two design points that made it that small.

## What this deliberately does not do

**It does not make `Engine` a soft preference** — "use it if available, else fall
back to AUTO and say so". That is the attractive third option and I am
recommending against it, for three reasons:

1. It re-introduces exactly the non-portability the strip exists to remove, only
   with a fallback to hide it. A design would then behave differently on two
   machines *and* look like it had been honoured.
2. AUTO's ordering is already a measured preference, not an arbitrary one, and it
   is host-aware in ways a stored string cannot be — including the
   infinite-baffle mounting filter, which exists specifically to stop a request
   reaching an engine that would fail it.
3. The use case it serves — "reproduce this exact run" — is already served
   better by exporting the config from the finished run.

If a per-machine engine preference is wanted, the right home is host-local
settings, not the portable design file. That is a separate proposal.

## Scope if accepted

- `server/design/textcfg.py` — emit a note from `_block_without_machine_solve_keys`
  instead of dropping silently; it currently has no channel for one, so this is
  the real implementation work.
- `server/design/migrate.py` — reuse the existing note mechanism.
- `docs/reference/CFG-FORMAT.md` — C2b and C3 edits.
- `server/tests/test_textcfg.py` — a test that a `WG.Solve` block carrying
  `Engine` round-trips without it *and* produces a note. The current suite
  asserts the strip; nothing asserts a report, which is why the gap was
  invisible.

## Recommendation

Original recommendation: take **C1 + C2b + C3**, and treat **C4** as the gate.

Taken, 2026-09-05: **C1 + C2b + C3 + C4**. C4 turned out to be one function
rather than the expensive part, so it went first and the gate it set — that a
note would reach a user — was met before C1 was written. C1 then followed, as a
computed property and one additive report field rather than the migration this
document sketched; `CFG-FORMAT.md` now describes the built contract, and the
recommendation against making `Engine` a soft preference stands unchanged.
