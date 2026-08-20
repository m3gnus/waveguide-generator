# Result and archive size measurements

Date: 2026-08-20  
Code baseline: `c8411ddc`  
Host: Apple Silicon macOS

## Conclusion

The local evidence is useful for engineering, but it is not sufficient to set a
hard result, snapshot, or archive limit. Across 68 real Workspace run directories,
the largest archive actually on disk was 5,017,449 bytes. Across 90 complete rows
in frozen copies of the normal and live-campaign job databases, the measured
component maxima were a 583,116-byte exact mesh, 696,080 bytes of public pressure
bases, a 3,586-byte radiation NPZ, and a 2,374,809-byte archive-snapshot response.
Those maxima occurred in different runs and must not be added together as if they
were one observed worst case.

No number in this report is a production maximum or a proposed limit.

## Method

`scripts/measure_artifact_sizes.py` recursively classifies every file in a run
directory containing `run.json`. For job databases, it opens a frozen database
copy read-only, reads the same results, mesh, retained channel-bases, and radiation
rows used by the archive snapshot, then invokes the production pressure-basis and
radiation readers. It serializes the public snapshot with the endpoint's current
`json.dumps(..., allow_nan=False)` shape. File and wire sizes are exact byte counts;
reported shapes come from `run.json` and result payloads.

The source databases were copied before measurement so the corpus could not change
under the scan. The scan did not solve new geometry, alter either source database,
or write into the Workspace archive. A representative invocation is:

```bash
PYTHONPATH=. .venv/bin/python scripts/measure_artifact_sizes.py \
  --archives "/Users/magnus/Documents/Waveguide Generator/runs" \
  --database /path/to/frozen-normal-simulations.db \
  --database /path/to/frozen-live-campaign-simulations.db \
  --json
```

The archive corpus spans exporter revisions. Older directories do not contain the
derived/report, exact-mesh, or pressure-basis members that the current archive path
would include. A zero below therefore means "not present in that measured artifact,"
not "this kind of run can never produce it."

## Raw representative measurements

All values are bytes. `Result JSON` is the sum of the pretty, per-channel JSON files
actually in the run directory. `DB results` is the canonical compact result retained
by the server. `Snapshot` is the current archive-snapshot JSON wire payload rebuilt
from retained rows. The observed archive total includes every file in the directory.

| Case | Shape | Result JSON | DB results | Exact mesh | Public bases | Radiation NPZ | Derived | Report | Observed archive | Snapshot |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Small, current-feature live gate, run 6 | 4 frequencies, 3 result channels, 72 triangles | 128,354 | 75,728 | 5,028 | 62,642 (2 files) | 3,586 | 8,391 | 75,251 | 286,669 | 170,882 |
| Imported high-mesh example, run 74 | 24 frequencies, 2 channels, 8,904 triangles | 1,070,575 | 502,486 | 583,116 | 0 | 0 | 0 | 0 | 1,079,806 | 1,099,292 |
| Multi-channel retained-basis example, run 82 | 24 frequencies, 2 channels, 7,160 triangles | 3,585,068 | 1,446,409 | 0 (pruned) | 696,080 (2 files) | 0 | 0 | 0 | 3,594,323 | 2,374,809 |
| Largest observed archive, run 67 | 60 frequencies, 1 channel, 5,488 triangles | 5,005,857 | 2,167,087 | 0 (pruned) | 0 | 0 | 0 | 0 | 5,017,449 | 2,167,281 |

The live run directory was produced immediately before retention-snapshot archive
support landed. Its exact 5,028-byte mesh was still retained in the database but is
not part of the 286,669-byte observed directory. Adding it gives a mechanically
reconstructed 291,697-byte member sum, not another observed archive. Likewise, the
older examples must not be described as current full-member archive totals.

Corpus maxima that are independently measured:

| Quantity | Maximum | Evidence |
| --- | ---: | --- |
| Files in one observed Workspace run directory | 18 | live gate, run 6 |
| Observed archive directory | 5,017,449 | run 67 |
| Exported result JSON in one archive | 5,005,857 | run 67 |
| Exact retained MSH | 583,116 | run 74, 4,573 vertices / 8,904 triangles |
| Public pressure bases | 696,080 | run 82, two files |
| Radiation matrix NPZ | 3,586 | live gate, four frequencies / two apertures |
| Derived sidecars | 8,391 | live gate, three channels |
| Static report | 75,251 | live gate, three channels |
| Archive-snapshot wire payload | 2,374,809 | run 82 |

## Scaling drivers

- Result JSON grows with frequency count, enabled polar planes and angles,
  spherical sampling, channel count, and retained solver diagnostics. Pretty
  per-channel archive JSON is materially larger than the compact canonical DB row.
- ASCII MSH size grows primarily with vertex and element counts and the number of
  physical tags. Triangle count alone is not a byte contract.
- A pressure basis grows with native drive-channel count and the complex arrays for
  `frequency × plane × angle`, plus `frequency × sphere sample` when balloon data is
  retained. NPZ compression depends on the actual field, so array shape cannot
  safely predict a byte maximum.
- A radiation matrix's dominant arrays grow approximately with
  `frequency × aperture_count²`; only the small two-aperture/four-frequency live
  case was retained in this corpus.
- Derived tables grow roughly with frequency and result-channel count. The static
  report also embeds result metadata, so verbose solver diagnostics can dominate it.
- Total archive size is the sum of all expanded members. The current implementation
  does not build a ZIP, and the observed legacy totals omit some current members.

These relationships are projections about shape, not projected byte maxima.

## Snapshot and browser-memory implications

The archive-snapshot endpoint embeds the mesh as an escaped JSON string and embeds
each NPZ as base64. Base64 length is exactly `4 × ceil(binary_bytes / 3)`: run 82's
696,080 public pressure-basis bytes became 928,108 JSON characters, a 33.33% wire
expansion before field names and the result payload. Its complete snapshot was
2,374,809 bytes.

Peak memory is higher than wire size at both ends. The server holds parsed results,
exported NPZ bytes, base64 strings, and the final JSON during assembly. The frontend
uses `response.json()`, then `atob`, a decoded JavaScript string, a `Uint8Array`, and
a `Blob` for each binary member while the parsed snapshot remains reachable. String
width, copies made by the JS engine and `Blob`, allocator overhead, and garbage-
collection timing are implementation-dependent, so this campaign does not claim an
exact RAM multiplier. The non-streaming, all-members-at-once shape is the important
constraint to test before setting a wire or archive limit.

## Evidence still needed before hard limits

At minimum, a limit decision needs:

- real retained runs near the supported mesh ceiling, not only an 8,904-triangle
  maximum artifact;
- the largest supported frequency grid, polar grid, sphere grid, drive-channel
  count, and passive-cardioid aperture count, both separately and together;
- field data whose NPZ compression is representative of difficult production
  geometries rather than a synthetic or unusually smooth field;
- current-version archives containing every member in one run, including exact
  mesh, all native pressure bases, radiation NPZ/CSV, derived sidecars, and report;
- backend peak RSS and latency while building a snapshot, plus browser/WebView peak
  memory while parsing, decoding, and writing it on supported macOS and Windows
  hardware;
- concurrent archive attempts, low-free-disk behavior, cancellation, and cleanup;
- a broader customer corpus with outliers and enough headroom for schema growth.

Until that evidence exists, any hard limit would be a product choice rather than a
measured safety boundary.
