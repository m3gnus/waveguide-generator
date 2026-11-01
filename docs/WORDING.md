# Failure messages

A message reaches the user only if the user can do something about it. Every
message says what is wrong and what to do next, naming the control.

| Class | Cause | What the user sees |
| --- | --- | --- |
| A | The user caused it and can change it. | What is wrong and how to fix it, naming the control. Better, disable the control and say why, or refuse before they commit. |
| B | The program caused it. | Nothing that blames them. A short, calm note only if they must know; the detail goes to the developer record. |
| C | An outside condition: no engine, memory, connection, disk, Fusion closed. | What happened and what to do next. |

Never write "something went wrong", "check the values", a status code, framework
text, a raw key, a developer term or an internal name. Say "run", not "job". Name
a field by the label on its control and the section it sits in.

## One owner

`frontend/src/api/httpError.ts` owns what a failure says:

| Use | For |
| --- | --- |
| `failureWords(reason, fallback)` | Every catch that reaches the screen. Plain sentences pass; anything else becomes the fallback and goes to the developer record. |
| `throw new ProgramFault(text)` | A parser or invariant the user cannot cause. Its text never reaches the screen. |
| `httpErrorMessage(response, sentence)` | A failed response. `sentence` is a whole sentence with its next step. |
| `refusalText(detail, operation, sentence)` | A response body already read. |
| `fieldSentence` / `rowSentence` | Validation away from, or beside, its control. |

`model/currentModel.ts` `solveFailureWords` does the same for a failed run. Text
another piece of code matches on stays raw in state and passes through
`failureWords` at the point it is shown.

Server text may be reworded freely, but wire codes may not. Exception text goes to
the log. A mesher or solver exception that reaches a response is listed for a
backend fix; the owner above keeps it off the screen until then.

Woofers: `sketch/adapters/frontView.ts` owns the fit rule. Margins never grow by
themselves. The user reads what is short and chooses the change.

# Wording on CAD surfaces

Lead with what happened to the user's model and give one next step. Put
transport details, stable diagnostic codes and exception detail in the log or
an existing developer/Diagnostics disclosure. Keep those codes unchanged.

Do not expose bundle, return / CAD return / `.wgreturn`, manifest, schema,
request id / Return ID / UUIDs, inbox / IPC, acknowledgement, selector /
`entity_token` / `options['…']`, traceback, main thread / event, gate, ingest,
operation, lineage, finding ids, raw role constants, UTF-8 / byte limits, or
determinant / orthonormal / chirality in primary CAD copy. Say “Port or passive
opening” for `PASSIVE_CARDIOID`. An explicit “details in Text Commands” link
belongs only inside Diagnostics. The user's own document names are their own
words, even when a name includes one of these terms.

Say “could not” instead of “refused”. Do not call a state the user can fix
“failed” or present it as an internal failure. A question waits for the user;
a prepared model is ready to solve; a modelling problem needs a change. An
internal preparation problem says WG could not prepare the model and has
recorded it, with the original detail kept for developers. A solver's actual
failure can still be described as a failure.

Use the Fusion commands consistently:

| Command | Meaning |
| --- | --- |
| Sources & Mesh… | Set up sources and mesh, then Send or Solve. |
| Send to WG | Open this model in WG to inspect or set up. Does not solve. |
| Solve in WG | Send and solve now. |

## File mesh terms

Use the same words as the Imported mesh screen:

| Words | Meaning |
| --- | --- |
| Surfaces | The groups read from the mesh file. |
| HF driver, MF driver, LF driver | The driver roles for a surface. |
| Port or passive opening | The role for a port or passive opening. |
| Rigid wall | A surface that does not move. |
| Untagged surface | Triangles with no group, initially a rigid wall. |
| Symmetry | Full model, Half x = 0, Half y = 0 or Quarter. |
| Remove the closed face | Remove the face on the chosen symmetry plane. |
| Mirror from the negative side | Mirror the model across its symmetry plane. |
| Size | The model dimensions and file units. |
| Share one driver | Use identical driver settings for same-role surfaces. |
| Drive separately | Set the drivers for those surfaces separately. |
| Try again | Retry preparation after a failure. |

## Guards and ownership

`frontend/src/testing/expectNoJargon.ts` is the shared vitest assertion for
primary CAD copy, including tooltips and accessible labels. Use it in component
tests, with the existing status-line tests' extra vocabulary when needed. Raw
text inside `.developer-details` is intentionally excluded, and identifiers in
data attributes are not user copy. Check primary states before enabling
technical disclosures in tests that also verify the developer view.

The server's guard is
`test_every_state_wg_can_write_reads_without_jargon` in
`server/tests/test_cad_request_status.py`. It reuses the vendored protocol's
`user_text_violations` for request-status text, WG's hold guidance and action
labels, preparation-failure messages, preparation stages, and mesh-limit
messages. The protocol owns its own text and guard vocabulary; update it in
WGLink and vendor it separately, preserving protocol parity.

Never rename protocol fields, role ids, canonical payloads, internal state
names or diagnostic codes as part of a wording edit. The legacy reader for
older mesh-ceiling rows must continue to recognise “This model meshes to ”;
new rows use the stable `mesh_too_large` code. User guides have separate
ownership from these developer rules.
