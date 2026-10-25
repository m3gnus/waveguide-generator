# Wording on CAD surfaces

Lead with what happened to the user's model and give one next step. Put
transport details, stable diagnostic codes and exception detail in the log or
an existing developer/Diagnostics disclosure. Keep those codes unchanged.

Do not expose bundle, return / CAD return / `.wgreturn`, manifest, schema,
request id / Return ID / UUIDs, inbox / IPC, acknowledgement, selector /
`entity_token` / `options['…']`, traceback, main thread / event, gate, ingest,
operation, lineage, finding ids, raw role constants, UTF-8 / byte limits, or
determinant / orthonormal / chirality in primary CAD copy. Say “Passive
radiator” for `PASSIVE_CARDIOID`. An explicit “details in Text Commands” link
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
