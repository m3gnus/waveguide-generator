# Release readiness

Status: active checklist. Last local verification: 2026-08-13.

The rebuilt application is on `main`, the remote is the existing
`m3gnus/waveguide-generator` repository, the local repository has `v0.2.1` and
`v0.2.2` tags, and hosted CI has run since the initial port. These facts supersede the
pre-cutover plan. Local tags and workflow files do not prove that a current GitHub
release or every remote check succeeded.

## Verified locally

- macOS, Windows, and Linux installers and launchers exist.
- the CI workflow defines Ubuntu, macOS, Windows, frontend, codec, and drift checks;
- version copies agree through `scripts/bump_version.py --check`;
- release assets are checksummed before installation;
- automatic v1 run migration, dry-run reporting, backup, and rollback exist;
- the original application is retired on the v1 line and the rebuilt app is `main`.

## Gates still requiring fresh evidence

- [ ] Confirm the current GitHub Actions matrix is green at the intended release SHA.
- [ ] Confirm the matching GitHub release contains the SPA archive and `.sha256`, then
      install it over HTTPS rather than from a local stand-in.
- [ ] Run the Windows installer end to end on a fresh machine, including a parent path
      with spaces, launch, update/relaunch, uninstall, and retained-data behavior.
- [ ] Repeat the fresh-machine macOS install on a machine other than the development
      host and perform the minimal Linux `/health`/UI smoke.
- [ ] Exercise upgrade and rollback against a real original-app install on both desktop
      platforms, recording pre/post job counts and artifact hashes.
- [ ] Publish owned-hardware Metal/BEMPP qualification evidence for the exact pins.
- [ ] Complete the beta matrix and record duration, machines, backends, and failures.
- [ ] Sweep the maintained compatibility summary and explicitly accept or close every
      remaining deliberate divergence.

## Release procedure

1. Start from a clean tree whose pinned sibling revisions have passed their required
   checks.
2. Run server/script tests, the shared codec suite, frontend tests/build, version check,
   and the repository drift checks.
3. Complete the fresh evidence above for the release candidate.
4. Tag the version declared in `shared/version.json` and verify the remote workflow and
   attached checksums.
5. Install the published artifact through each supported platform entry point before
   announcing it.

The dated Windows/macOS reports under `docs/validation/2026-08/` are useful baselines,
but they do not close a current release gate by themselves.
