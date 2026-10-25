# CAD wording sweep hand-back

Repository: waveguide-generator. Branch: `chore/wording-sweep-261025`.
Base: `beffa757d0e88b581dde6dcdd3f60aaeb43564d4`.

This changes display wording and its guards. Layout, actions, protocol data,
role ids and stable diagnostic codes stay unchanged. The tables show changed
phrases; angle brackets stand for existing dynamic values. Repeated uses share
a row.

## Model, history, settings and viewport

| Before | After |
| --- | --- |
| Different CAD return | Different CAD model |
| This run's CAD return differs from the one in the viewport. | This run's CAD model differs from the one in the viewport. |
| Return / Return · `<time>` | Model / Model · `<time>` |
| Filename ending in `.wgreturn` as a fallback model name | Filename without that suffix; stored filename unchanged |
| Filename as the model tooltip | The same display name as the model heading |
| Manifest is unreadable | WG cannot read this model. Send it again from CAD. |
| Returns that name no CAD-linked project. Never selected as this project's geometry automatically. | Models with no CAD-linked project. Never selected as this project's geometry automatically. |
| CAD return history | CAD model history |
| `<N>` return(s) from other CAD-linked projects | `<N>` model(s) from other CAD-linked projects |
| Refresh the CAD return listing / Refresh CAD returns | Refresh the CAD model list / Refresh CAD models |
| No return from `<project>` is available to open. Send the document from Fusion again. | No model from `<project>` is available to open. Send the document from Fusion again. |
| This design lineage has more than one managed Onshape Part Studio link. Choose the exact link before WG updates, returns, or unlinks it. | This design has more than one linked Onshape Part Studio. Choose the exact link before WG updates, receives geometry, or unlinks it. |
| Updates and returns use this exact managed Part Studio link. | Updates and received models use this exact linked Part Studio. |
| Returning & preparing… | Receiving & preparing… |
| WG writes the bundle, starts Fusion | WG saves the model, starts Fusion |
| Show the exact triangles this CAD return will be solved on | Show the exact triangles this CAD model will be solved on |
| This is normal for a return from another machine | This is normal for a model from another machine |
| PORT_EXIT / PASSIVE_CARDIOID in model summaries and source sizing labels | Port exit / Passive radiator |
| Derived `passive_cardioid` channel; every PORT_EXIT patch (help text) | Derived Cardioid channel; every port exit patch |

## Connection and delivery

| Before | After |
| --- | --- |
| Background coordination: off. WG checks CAD Link returns and Fusion status only while CAD work is in flight, and when you act. | Background checks are off. WG checks models from CAD and Fusion status while CAD work is in progress, and when you act. |
| Background coordination: off, but this build of the Waveguide Generator add-in in Fusion needs the listing to pick up Send: it does not send through WG’s request inbox, so WG keeps checking the WGLink folder on a timer. | Background checks are off. This older Fusion add-in needs WG to keep checking the WGLink folder for models you send. |
| Its automatic coordination is off | Its background checks are off |
| WG is not collecting CAD Link requests. A Send or Solve from Fusion is refused there, and nothing here runs one. | WG cannot receive models from Fusion while CAD Link is turned off. Turn on CAD Link, then send the model again. |
| WG’s CAD Link request consumer is not running, so nothing sent from Fusion is taken. | WG cannot receive models from Fusion right now. Restart WG, then send the model again. |
| WG’s request consumer has not finished a pass since `<time>`; requests sent since then have not been taken. | WG has not finished checking models from Fusion since `<time>`. Restart WG, then send the model again. |
| A request from Fusion was refused / `<N>` requests from Fusion were refused | WG could not accept a model from Fusion / WG could not accept `<N>` models from Fusion |
| A CAD request was refused / `<N>` CAD requests were refused | WG could not accept a CAD request / WG could not accept `<N>` CAD requests |
| The CAD bundle was exported, but WG could not notify Fusion. Open it from `<path>`. | The model was saved, but WG could not notify Fusion. Open the model in Fusion. |
| Fusion refused to export this waveguide: it could not find the component… | Fusion could not export this waveguide: it could not find the component… |
| Fusion refused the request for this model’s geometry, and reported no detail. | Fusion could not carry out the request for this model’s geometry, and reported no detail. |
| `refused` as the fallback receipt explanation | please send it again from Fusion |

## Model arrivals and recovery

| Before | After |
| --- | --- |
| Project design loaded. Return it from Onshape to prepare Simulation geometry. | Project design loaded. Bring its geometry from Onshape to prepare it for simulation. |
| Selected the latest matching return from `<name>` | Selected the latest matching model from `<name>` |
| No matching CAD return is available yet; return the project from Fusion or Onshape to prepare Simulation geometry. | No matching CAD model is available yet; send the project from Fusion or Onshape to prepare Simulation geometry. |
| Project design loaded, but its CAD returns could not be read. | Project design loaded, but its CAD models could not be read. |
| Select a CAD return before preparing a simulation. | Select a CAD model before preparing a simulation. |
| That return belongs to another CAD-linked project. | That model belongs to another CAD-linked project. |
| Returned and prepared `<name>` from Onshape. | Received and prepared `<name>` from Onshape. |
| Discarded the Onshape return because the selected design changed. | Kept the selected design because it changed while Onshape was sending the model. |
| Select CAD Link to use a CAD return. | Select CAD Link to use a CAD model. |
| Recalled from an archived run; the original return bundle is not active. | Recalled from an archived run; the original CAD model is not active. |
| Looking for its latest CAD return… | Looking for its latest CAD model… |
| The design was replaced after this CAD return was prepared. | The design was replaced after this CAD model was prepared. |
| The return bundle was modified after this preparation. | The CAD model changed after it was prepared. |
| The return listing differs from the bundle used for this preparation. | The CAD model list differs from the copy used for this preparation. |
| The retained return manifest cannot be verified; prepare this model again before solving. | WG cannot verify its saved copy of this model; prepare it again before solving. |
| stale detection unavailable: this returned bundle predates wgreturn 1.1 and carries no document signature | WG cannot tell whether this model is out of date. Send it again from Fusion. |

## Solve status, requests and modelling remedies

| Before | After |
| --- | --- |
| Checking the return | Checking the model |
| Not solved (stopped preparation stage) | Not prepared |
| Failed (rejected CAD request stage) | Not prepared |
| Solve refused (title) | Solve did not start |
| Solve refused. (fallback message) | WG could not start this solve. Check the model settings. |
| The solve of `<name>` was refused: `<detail>` | WG could not solve `<name>`: `<detail>` |
| its return cannot be read / its return could not be read | its model cannot be read / its model could not be read |
| the selected engine cannot solve this return | the selected engine cannot solve this model |
| the jobs system refused it | WG could not start it |
| Fusion refused it (preflight reason) | needs a change in Fusion |
| Fusion refused it (adapter reason) | Fusion could not apply it |
| CAD operation · `<kind>` | CAD request |
| operation `<id>` as a button's model label | this model |
| the backend has not recorded its preparation yet | WG has not prepared this model yet |
| Only a refused solve can be dismissed. Use Stop for an active solve. | This solve cannot be dismissed yet. Use Stop for an active solve. |
| Refused: cut in CAD at `<plane>` cannot be recovered — send the uncut model | WG could not rebuild the model cut in CAD at `<plane>` — send the uncut model |
| Refused: cut on an oblique plane — send the uncut model | WG could not solve the model cut on an oblique plane — send the uncut model |
| Refused: cut off-centre at `<position>` — send the uncut model | WG could not solve the model cut off-centre at `<position>` — send the uncut model |
| Refused: the solver frame is not a proper rotation — send the model again | WG could not orient this model for solving — send the model again |
| Refused: this model cannot be solved as it stands | WG could not solve this model as it stands |
| Rebuild the mesh from the CAD return | Rebuild the mesh from the CAD model |
| The return has no port-exit aperture. Mark the face the port vents through as a PORT_EXIT source (or PORT_EXIT_L and PORT_EXIT_R for a split port)… | The model has no port-exit aperture. Mark the face the port vents through as a Port exit source (or Left port exit and Right port exit for a split port)… |
| Give the return a single source with the MF role | Give the model a single source with the MF role |
| Assign every PORT_EXIT patch to the same drive channel | Assign every port exit patch to the same drive channel |

## Remaining CAD tooltips and stale-model messages

| Before | After |
| --- | --- |
| cannot solve this return | cannot solve this model |
| On by default for a return with two or more drive channels; ordered by the sources' return roles | On by default for a model with two or more drive channels; ordered by the sources' roles |
| The solve is refused while a started driver is missing them. | WG cannot solve the model while a started driver is missing them. |
| Write an identity-bearing .wglink bundle to the selected workspace | Save this CAD model (.wglink) in the selected workspace |
| WG could not read a valid parameter table from the linked export manifest. | WG could not read this model’s linked parameter values. Send the design to CAD again. |
| over the PORT_EXIT aperture / Geometric area of the PORT_EXIT faces | over the port exit aperture / Geometric area of the port exit faces |
| The solve refuses if it disagrees with the matrix it actually computed. | WG cannot solve the model if this area disagrees with the matrix it computed. |
| A return the CAD author already cut is refused instead: the other half is not in the file. | WG cannot use a model already cut in CAD with this setting: the other half is not in the file. |
| New returns carry no Fusion model. Models already kept remain available. | New models sent from Fusion include no Fusion file. Models already kept remain available. |
| The prepared return no longer appears in the workspace listing. | The prepared CAD model no longer appears in the workspace list. |
| The return became readable after it was prepared. | The CAD model became readable after it was prepared. |
| The return is no longer readable | The CAD model is no longer readable |
| The return source inventory or source sizing suggestions changed after preparation. | The model’s sources or suggested source sizes changed after preparation. |
| The return document or linked-instance inventory changed after preparation. | The CAD document or its linked instances changed after preparation. |
| The last CAD project has no return to show yet. Pull one from CAD. | The last CAD project has no model to show yet. Bring its geometry from CAD. |
| The geometry return waiter was stopped. | Stopped waiting for the CAD model. |
| Fusion did not return the requested model within 60 seconds. | Fusion did not send the requested model within 60 seconds. |
| select <name> from the return list to use it | select <name> from the model list to use it |
| Discarded a completed preparation / preparation response because its selected return or design was superseded. Rebuild the mesh for the current state. | The model or design changed while its mesh was being prepared. Rebuild the mesh for the current model. |

## Deliberately retained

- `server/cadlink/wglink_protocol.py` is unchanged. Its “surface group” and
  `mesh_ceiling` remedy text belong to WGLink, to be vendored by the overseer
  after that repository lands. No WG assertion was found pinning those old
  human-readable sentences. `test_cad_preflight.py` pins the stable
  `mesh_ceiling` → `mesh_too_large` mapping, which must stay unchanged.
- Internal variable names, endpoints, payload keys, role ids, stored filenames,
  request identities, diagnostic codes, logs and developer evidence retain
  their original spelling. Existing diagnostic disclosures still show raw
  evidence. No replacement protocol or new error-translation mechanism was added.
- `MeshTooLarge.message`, the mesh-ceiling remedy, and the legacy “This model
  meshes to ” reader are retained: the current message is already plain, C2
  is fixed, and existing stored rows still need the reader.
- “Other requests”, the CAD model status line and viewport empty copy already
  meet the rules. “N CAD requests need you” counts requests, not distinct
  models; renaming the count to models would misstate what is counted.
- “Failed” remains for actual solver/internal failures; rejected model requests
  now say “Not prepared”. No status classification or styling changed.
- `CadSolveCard` no longer exists on this base. Its current surfaces,
  `CadModelStatusLine`, Model details and `CurrentModelStatus`, are guarded.
- Ordinary navigation verbs such as “return to CAD or Results” are retained;
  they describe navigation rather than a CAD transport object. Generic result
  exporters and non-CAD ATH parameter wording are outside this sweep.
- The four user-guide paths named in the brief were not edited.

## Verification

All checks used Node 20 or the supplied Python environment from this worktree.
The runs were unpiped and gated with `&&`. No full suite, solve, push or merge
was run. Initial runs found stale copy assertions, a guard teardown mistake and
a missing frontend build prerequisite; these were corrected before the passing
runs below.

| Check | Passing count |
| --- | --- |
| Main affected vitest run, 32 files | 1,137 |
| Final source-role/cardioid rerun: `ParamPanel.test.tsx`, `passiveCardioid.test.ts` | 231 (includes the ParamPanel tests already counted above) |
| Arrival/recovery rerun: arrivals, CadWorkspaceOwners, cadReturn | 151 |
| Additional tooltip/recovery coverage, 11 files | 648 validated across the run and corrected-assertion reruns |
| Final corrected-assertion and driver-picker run, 4 files | 195 validated across the run and final CadLinkPanel rerun |
| Final CadLinkPanel rerun | 107 |
| Final crossover/Simulation tooltip rerun | 228 |
| Shared status-line guard rerun | 39 |
| `npx tsc --noEmit` | Passed after affected vitest runs |
| `test_cad_request_status.py`, `test_cad_held.py` | 95 |
| `test_cadlink_wgreturn.py`, `test_fusion_status.py` | 131 |
| `git diff --check` | Passed |

The main vitest run covered the wording helper, designIo, domainDecision,
ParamPanel, cadSolve, importedSubmission, runCoherence, CadDeliveryHealth,
CadDomainInterpretation, CadLinkPanel, CadModelDetails, both affected
CadOperationsSection suites, CadProjectPanel, CadWorkspaceOwners,
JobsCoordinator, JobsPanel, M1Acceptance, ResultsPanel.coherence, SettingsDialog,
StatusBar, TopBar, arrivals, receiptDisplay, refusals, restores, returnWaiter,
solvedModel, solveProgress, cadModelStatusLine, cadModelState and Viewport.
The arrival/recovery rerun covers arrivals, CadWorkspaceOwners and cadReturn.
Additional tooltip coverage includes both DesignFileMenu suites,
SolveOptionsSections, CrossoverSection.live, ParamPanel, SettingsDialog,
cadReturn, restores, returnWaiter, arrivals and CadWorkspaceOwners. The final
corrected-assertion/driver-picker run covers cadReturn, restores, CadLinkPanel
and ExteriorDriverLibraryPicker. Results were reused for files whose inputs
were unchanged; stale text assertions were corrected and rerun.

Duplication check: extracted the existing on-screen jargon pattern into one
`expectNoJargon` helper, removed the status-line test's separate implementation,
reused the protocol's server guard, and moved the viewport's role-label table
into one shared display-label owner. No duplicate action, component or endpoint
was added. Wording rules live in `docs/WORDING.md`.
