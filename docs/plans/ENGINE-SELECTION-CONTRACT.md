# Proposal: the `WG.Solve` `Engine` / `SolverMode` contract

**Status:** proposal, awaiting a decision. Nothing here is implemented.
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

### C4 — make the migration note reachable

Recorded as a known gap rather than a new one: migration notes currently do not
reach the browser status line, which shows names only. C1 is worth little if its
note lands somewhere no user reads. Either surface notes on the status line, or
attach them to the import result the design panel already renders. **This is the
one part of the proposal with a real cost, and it is the part that decides
whether C1 is worth doing at all.**

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

Take **C1 + C2b + C3**, and treat **C4** as the gate: if migration notes cannot
be surfaced to the user in this cycle, C1 is a log line nobody reads, and the
cheap honest subset is C2b + C3 alone — fix the document so it stops promising
something the code does not do, and revisit the diagnostic when notes have a
route to the screen.
