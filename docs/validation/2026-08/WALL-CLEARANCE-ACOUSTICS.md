# Wall-clearance acoustic validation

Date: 2026-08-22
Verdict: **pass** — the capped rear-shell mesh removes the run-101 acoustic
failure. The remaining high-frequency full-3D/CircSym disagreement is the
already-reported 25 mm mesh-resolution limit, not the former jagged response.

## Reproduction

The archived run-101 design snapshot was replayed through the headless job
runtime on the merged dependency pins. Its execution geometry and solve-option
identities match the original run exactly:

| identity | original run 101 | capped replay |
|---|---|---|
| execution geometry | `be0bb99e34a5540fee47f9bb4e2ca7485db71b31936d846132055ecdc749fc84` | same |
| solve options | `8e7610daff0a8e7f4cac4cda332190e84a54e835bacef6849d871a34e5220f14` | same |
| sweep | 60 log-spaced points, 80 Hz–16 kHz | same |
| observation | H/V/diagonal, 0–90° by 5°, 2 m, normalized at 5° | same |

The request label and validation metadata differ, so the whole-request digest
is intentionally different. The full-3D Metal implementation is unchanged
between the old and replayed Metal pins; the intervening package diff is
limited to CircSym, tests, CI and documentation. The material solve change is
the mesher pin from `a7cfba26` to the clearance-capped `50a8d7e1`.

The replay ran in an isolated data directory and did not read or write the live
application database. Wall time was 56.23 s.

## Mesh result

| | original run 101 | capped replay |
|---|---:|---:|
| triangles | 10,310 | 10,578 |
| vertices | 5,157 | 5,291 |
| proper crossings | 77 (offline exact check) | **0** (runtime exact check) |
| coplanar overlaps | not recorded | **0** |
| watertight | yes | yes |
| signed volume (m³) | 0.00749178 | 0.00755482 |

This reproduces the expected 2.6% triangle increase while removing every
intersection.

## Acoustic comparison

Run 98 is the CircSym reference for the identical axisymmetric design. Values
below compare the archived broken run 101 and the capped replay against run 98.

| metric | broken run 101 | capped replay |
|---|---:|---:|
| on-axis SPL, max absolute delta, full sweep | 20.678 dB | **0.726 dB** |
| on-axis SPL, RMS delta, full sweep | 11.333 dB | **0.262 dB** |
| DI, max absolute delta, full sweep | 8.883 dB | **0.691 dB** |
| DI, RMS delta, full sweep | 2.775 dB | **0.182 dB** |
| H directivity, RMS delta, 80–1000 Hz | 5.151 dB | **0.323 dB** |
| V directivity, RMS delta, 80–1000 Hz | 5.141 dB | **0.265 dB** |
| diagonal directivity, RMS delta, 80–1000 Hz | 5.437 dB | **0.307 dB** |
| worst directivity delta, 80–1000 Hz, all three planes | 32.384 dB | **0.962 dB** |

The capped replay therefore restores sub-1 dB agreement throughout the mesh's
own trustworthy band (the solver reports a 1.024 kHz maximum valid frequency
for the original 25 mm resolution), and it restores the on-axis and DI curves
across the complete stored sweep. At 8–16 kHz, deep-null directivity samples
still differ by as much as 33 dB. That region has only about 0.4 elements per
wavelength at 16 kHz and is explicitly flagged `mesh_resolution_suspect`; it
must not be used to tune the clearance constants or claim high-frequency
full-3D parity.

## Artifact digests

The local replay bundle is intentionally not committed because it contains the
design request and a 10k-triangle solver mesh. These digests identify the exact
evidence retained by the validation session:

| artifact | SHA-256 |
|---|---|
| archived run-98 result | `ace5070ed3ebd1a7a59514afc1c1f06b85fc9ee69a30663bcf9acd699a599e49` |
| archived run-101 result | `ad5b03d9f533abd736dccc2eb63bef21b5fb1aa953f473a08fb24f42cbec825b` |
| capped effective request | `c5f1fecb77a2829e979ada277aee4a1ec2a3f0332e33fd48f66513ff2e4eea3f` |
| capped mesh | `67677553d3604c80d53dbb2644f1bbefb4760a79f3d019d24cd6c12a3e59f602` |
| capped result | `59f266af92c746ee5a8d3fedeeb18ec41cf88474536edf24c148b9bf09d5b336` |

## Conclusion

The one unproven claim in the wall-clearance guard is now measured: preventing
the rear-shell facets from crossing the acoustic surface fixes the acoustic
failure, not merely the mesh diagnostic. The provisional sizing constants are
not retuned by this result; doing that honestly requires a multi-geometry
acceptance corpus rather than one formerly failing design.
