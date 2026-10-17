# Deferred BEMPP retirement

Status: parked proposal, refreshed 2026-10-17 against `dev` at `db790ee6`.
BEMPP remains supported. No release version or removal date is assigned.

The `feature/remove-bempp-docs` branch originally changed active documentation
as though BEMPP had already been removed in 0.3.6. That wording was never
landed. This refresh keeps the current `dev` documentation and records the
conditions for revisiting removal here. The original proposal remains in Git
history at `915cb0f1`; its release wording is superseded by this plan.

## Why removal remains deferred

WG uses the official BEAT package for BEAT solves, and the old HBB rollback
has been removed. That does not retire BEMPP: it is a separate supported
engine with capabilities the current WG BEAT adapter does not expose.

The current [engine registry](../../server/engines/registry.py) filters Auto
selection by availability, mounting and symmetry support. In particular:

- BEMPP provides the rigid ground-plane path, with axes and combinations
  advertised only when supported by its probed adapter.
- Coupled infinite-baffle requests need a capable engine. The current BEAT
  adapter does not advertise this mounting; on a CPU-only machine, removing
  BEMPP would remove the supported path.
- BEMPP remains a fallback for compatible requests while BEAT is unavailable
  or preparing. Removal must define the replacement behavior for these users.

These are limits of WG's current integration, not a claim that the upstream
BEAT package cannot implement the missing capabilities. See the current
[user guide](../USER-GUIDE.md) for supported behavior.

## Conditions before reopening implementation

1. Implement the replacement paths in WG and expose only capabilities that
   the installed adapter actually supports. Cover coupled infinite baffle,
   rigid ground planes and supported axis/symmetry combinations on CPU-only
   machines as well as applicable accelerators.
2. Demonstrate numerical equivalence with retained reference cases, including
   pressure/results and field outputs where supported. Preserve explicit
   unsupported-case errors rather than silently changing the physical model.
3. Verify the complete user paths: parametric, file-mesh and CAD requests,
   coupled driver configurations, Auto and explicit engine choice, preparation
   failure, cancellation, restart, retained Runs and result export.
4. Decide compatibility for existing `bempp` selections in projects, saved
   run setups and API requests. Keep original run provenance. Any replacement
   selection must preserve the requested physics and explain the change;
   do not promise automatic substitution before that behavior is implemented.
5. Qualify the replacement in installed Mac, Windows and Linux applications,
   including a CPU-only installation without a usable accelerator. Source
   tests alone do not establish installed runtime or update behavior.
6. Obtain the release scope decision, then update runtime dependencies,
   installers, diagnostics, UI and active documentation together. Preserve
   historical validation records and record the version that actually ships
   the removal.

## Next work when the hold is lifted

Start implementation from the then-current `dev`. Reassess the capability
and compatibility gaps against that source and its pinned runtime; the list
above is an acceptance checklist, not a completed qualification report.
Rewrite active documentation from the behavior that is implemented and
verified. Do not reuse the old 0.3.6 removal statements as current guidance.

This branch currently changes documentation only. It does not remove an
engine, change engine selection or qualify any future removal.
