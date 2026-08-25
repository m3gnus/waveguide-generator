# The dev venv is not running what `pins.json` pins

**Date:** 2026-08-25. **Machine:** the Windows VM in
[WINDOWS-VALIDATION.md](WINDOWS-VALIDATION.md) §1. **Method:** read-only —
compare each module's `dist-info/direct_url.json` `commit_id`, which pip
records for a VCS install, against `pins.json`.

Found while chasing why a circular design on `engine=auto` fails here (see
[FIRST-SOLVE-WARMUP.md](FIRST-SOLVE-WARMUP.md) §9). It is not specific to that
failure and is not specific to any one branch.

| Module | Pinned | Installed | |
|---|---|---|---|
| hornlab-beat-bem | `c20713913` | `c20713913` | match |
| hornlab-sim | `f47e70566` | `f47e70566` | match |
| hornlab-bempp-bem | `5e67e1973` | `1e7fe1b5c` | **stale** |
| hornlab-metal-bem | `56a9c035f` | `55ec710a0` | **stale** |
| hornlab-plots | `96068496b` | `b8b9094b4` | **stale** |
| hornlab-waveguide-mesher | `50a8d7e1a` | `a7cfba26b` | **stale** |

Four of six. Every version string still reads `0.1.0`, so nothing about the
environment advertises the drift — `importlib.metadata.version` cannot see it
and neither can a capability probe.

## What the drift actually costs, where it was checked

**hornlab-metal-bem — axisymmetric cancellation.** The installed
`SolveConfig` has `on_frequency_result`, `circsym_baffle_z`,
`circsym_aperture_tag` and `progress_callback`, but no `should_continue`.
`server/solver/circsym.py:491` turns the resulting `TypeError` into
*"Installed axisymmetric solver lacks intra-frequency cancellation"*, which is
why a circular R-OSSE submitted with `engine=auto` errors here at progress
0.30 before any solver runs. The **pinned** commit has `should_continue`
(five call sites in `hornlab_metal_bem/circsym.py`). So this is a stale venv,
not a missing upstream feature, and not something a pins bump would fix.

**hornlab-bempp-bem — coupled infinite baffle.** The pinned tree contains an
`infinite_baffle.py` that the installed one does not have at all, plus changes
to `__init__.py`, `config.py` and `mesh.py` (186 diff lines). This is why
`bempp_status()` here reports `coupled_infinite_baffle: false`, which in turn
removes `infinite-baffle` from the bempp engine's advertised mountings in
`server/engines/registry.py` and changes what `resolve_auto_engine` will hand
an infinite-baffle request.

**hornlab-waveguide-mesher and hornlab-plots** were not investigated. Mesher
drift would move mesh output, and therefore any measured solve time or
acoustic result on this machine.

## What this means for measurements taken here

Every number in [FIRST-SOLVE-WARMUP.md](FIRST-SOLVE-WARMUP.md) was measured on
the **installed** stack, not the pinned one. The finding it reports is robust
to that: the initialization cost being removed lives in `bempp-cl` and its
OpenCL/numba compilation, which is an ordinary PyPI dependency and identical in
both, and the `cache=True` count in `hornlab_bempp_bem` is 1 either way. The
absolute seconds could still move on a corrected venv.

The same caveat applies to any earlier dated evidence in this directory that
was captured on this machine, which is the reason this note is not filed under
the change that found it.

## Not done

The venv was **not** corrected. Bringing it to pins upgrades four modules,
turns on at least two capabilities that are currently reported as unavailable
(axisymmetric cancellation, bempp coupled infinite baffle), and changes the
mesher — so it needs its own re-validation pass rather than being folded into
an unrelated branch.

## The other side of the comparison: the Mac, 2026-08-25

Same method, run on the macOS development host the Metal numbers come from.
**All six modules match `pins.json` exactly.**

| Module | Pinned | Installed | |
|---|---|---|---|
| hornlab-beat-bem | `c20713913` | `c20713913` | match |
| hornlab-bempp-bem | `3fcb785ff` | `3fcb785ff` | match |
| hornlab-metal-bem | `f89765be2` | `f89765be2` | match |
| hornlab-plots | `a3befffc1` | `a3befffc1` | match |
| hornlab-sim | `f47e70566` | `f47e70566` | match |
| hornlab-waveguide-mesher | `50a8d7e1a` | `50a8d7e1a` | match |

That reading was taken against `main`'s `pins.json` (`a1a5deef`). **This branch
moves the mesher pin forward** off the Windows-broken rollback, to `e562e89`, so
a venv still holding `50a8d7e1` now reads as drifted from here until it is
reinstalled from `server/requirements-pins.txt`. That is not a contradiction of
the table above — it is the same mechanism, caught the moment the pin moved
rather than weeks later, which is the entire point of measuring it.

So the two hosts were **not** running the same stack, and the asymmetry has a
direction: the Mac is on pins and the Windows box is behind it. That settles the
open question in the section above — the axisymmetric cancellation failure there
is this box being stale, not a missing upstream feature — and it means that
where Mac and Windows figures disagree, the Mac side is the one measured against
the stack this repository actually declares.

**Read the `Pinned` column above as of this branch, not as of `main`.** WG's
pins moved in the 2026-08-25 integration batch: `hornlab-bempp-bem` to
`3fcb785`, `hornlab-metal-bem` to `f89765b`, `hornlab-plots` to `a3befff`, and
the mesher forward off the Windows-broken rollback. The Windows table's
`Pinned` values are the ones that branch carried when the drift was measured;
they are evidence, not a current manifest. A future reader comparing that table
against `pins.json` will find it disagrees, and that is expected.

The drift itself is unfixed on that machine, deliberately — see **Not done**.
