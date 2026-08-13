# Run-export follow-ups

Status: active plan containing only unfinished work, verified 2026-08-13.

## Already shipped

The completed-run menu, separate manual/automatic format preferences, snapshot-bound
geometry export, per-format failure reporting and retry, stable run-number filenames,
CSV union-frequency join, chart/directivity PNGs, on-axis FRD, VituixCAD H/V FRD sets,
stored polar phase, STEP solid/surface, STL, config, JSON, polar/impedance CSV, and
Fusion curve exports are implemented. Those are current behavior, not plan items.

`v0.2.3` added the workspace write route. **Manual and automatic completed-run exports**
no longer download through the browser: every run-result entry point uses
`runWorkspaceExportBundle`, which prepares the selected members in memory and posts them
to `POST /api/workspace/write-export` in a single request. The server writes them into
the configured workspace, defaulting to `<checkout>/output`, under the run's stable
subdirectory. Browser download permissions and the browser's Downloads destination are
therefore outside the completed-run export path.

## Remaining engineering

- [x] Fold on-axis FRD, polar FRD, and the second directivity PNG request into the
      shared export dispatcher. All three are now first-class preference formats;
      manual polar sets retain their selected-Workspace destination, while automatic
      polar sets use the common per-run Workspace staging path.
- [ ] Decide whether a multi-format action should remain individual members in one
      Workspace run directory or gain one server-built archive in that directory. If an
      archive is built, define a versioned manifest, member hashes, omission/failure
      states, ZIP64/size limits, cancellation, temp-file cleanup, and the relationship
      between the archive and its expanded members. Manual and automatic run exports
      already share the Workspace route; this is now a packaging and retention decision,
      not a browser-download destination decision.
- [ ] Give archived exports a job-snapshot transaction boundary so retention cannot
      remove results or mesh artifacts halfway through assembly.
- [ ] Reconcile VACS: it remains a preferences format and emits magnitude-only polar
      pressure with zero phase. Either specify a correct complex-pressure contract and
      test it or migrate/remove the format explicitly.
- [ ] Share one export artifact catalogue and numbering policy with CAD link instead of
      allowing run archives, `.wglink` bundles, and workspace folders to invent parallel
      identities. This got more urgent in `v0.2.3`: automatic run exports and CAD-link
      bundles now both write into the workspace, from different code with different
      naming, and the Onshape leg writes its bundles under the data directory instead.
      Three writers, three conventions, no shared catalogue.
- [ ] Measure realistic maximum result/mesh/archive sizes before choosing hard limits.

## Product decisions

- Should an archive include every available artifact or only the selected formats?
- Are server-rendered chart PNGs always included together, or independently selectable?
- Do manual or automatic exports ever create an archive, or only durable individual
  files? (Both write individual files into the workspace today; the question is whether
  that is the settled answer or an interim one.)
- Which artifacts are long-lived records versus reproducible conveniences?

Coordinate the shared catalogue/numbering decision with the active CAD-link plan. The
workspace CAD documents stay outside this directory until that work closes.
