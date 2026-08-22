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

### Solver-neutral design identity

Run 98 used the axisymmetric-meridian path while run 101 and the replay used
full 3D, so their request and geometry-provenance digests are not expected to
match. The retained design snapshots provide a solver-neutral comparison. An
exact structural diff of the two archived snapshots found only one changed
leaf: the requested mouth mesh resolution. The replay snapshot is byte-for-byte
equivalent after canonical JSON serialization to the run-101 snapshot.

| identity input | run 98 | run 101 | capped replay |
|---|---:|---:|---:|
| design revision | 331 | 332 | 332 |
| canonical design-snapshot SHA-256 | `2036d543c19af030d1cde41b1acf8bd5ab26e5d152aaf1b47daa87c75d583bcb` | `9cc792b802699bb8868c2882e138c889b20cba4308bfbb905e732d1956b26de5` | same as run 101 |
| requested mouth resolution | 15 mm | 25 mm | 25 mm |
| solver-neutral model SHA-256 | `2667d10f72e3f5cf209d9fe1e8ac77ea9aa75c9cd5b4ad74e883abe5e474ccb8` | same | same |

The exact `wg-design-physical-source-v1` field selection and UI-value unwrapping
are implemented in `scripts/verify_model_identity.py`. The sanitized 40-leaf
canonical payload and expected digest are committed in
`docs/validation/2026-08/wall-clearance-model-identity.json`; it contains no
labels, paths, raw requests or mesh. Reproduce the published identity from a
clean checkout with:

```console
python3 scripts/verify_model_identity.py docs/validation/2026-08/wall-clearance-model-identity.json
```

The verifier serializes the payload as UTF-8 JSON with sorted keys, compact
separators, ASCII escaping and non-finite values forbidden, then applies
SHA-256. With `--snapshot PATH`, it can also extract and normalize an ordinary
design snapshot or execution request and compare it to the committed payload.
The schema includes the formula and all profile coefficients, scale and throat
extension, the full morph block, vertical offset, wall thickness, enclosure
depth/edge/clearances, and the complete source block. Mesh tessellation
controls, output flags, legacy solver text and sweep/observation options are
excluded.

| normalized field group | value in all three snapshots |
|---|---|
| profile | R-OSSE; `R=600`, `a=37`, `a0=5.25`, `b=0.3`, `k=0.65`, `m=0.8`, `q=4`, `r=0.3`, `r0=19.5`, `tmax=1` |
| scale and throat | scale 1; extension 5.25° by 12 mm; slot length 0 |
| morph | zero target shape/width/height/corner; rate 3; fixed part 0; shrinkage disabled |
| wall and placement | vertical offset 0; wall thickness 5 mm |
| enclosure inputs | depth 0; edge radius 18 mm, type 1; 25 mm clearance on all four sides |
| source | shape 2; automatic radius (`-1`); curvature 0; unit normal velocity; no contour override |

The differing mouth-resolution value is a discretization choice, not a change
to the physical profile or source. Run 98 requested automatic solver selection
and resolved to the axisymmetric-meridian formulation, whose 16 kHz refinement
produced a 3.559 mm maximum meridian edge. Run 101 and its replay explicitly
requested full 3D and a 25 mm mouth mesh target. This explains both the distinct
provenance hashes and why the comparison below is a physical-design comparison,
not a claim that the two formulations used identical meshes.

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

Run 98 is the CircSym reference for the same solver-neutral axisymmetric design
documented above. Values below compare the archived broken run 101 and the
capped replay against run 98.

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
| archived run-98 design snapshot (canonical JSON) | `2036d543c19af030d1cde41b1acf8bd5ab26e5d152aaf1b47daa87c75d583bcb` |
| archived run-101 design snapshot (canonical JSON) | `9cc792b802699bb8868c2882e138c889b20cba4308bfbb905e732d1956b26de5` |
| solver-neutral model identity | `2667d10f72e3f5cf209d9fe1e8ac77ea9aa75c9cd5b4ad74e883abe5e474ccb8` |
| capped effective request | `c5f1fecb77a2829e979ada277aee4a1ec2a3f0332e33fd48f66513ff2e4eea3f` |
| capped mesh | `67677553d3604c80d53dbb2644f1bbefb4760a79f3d019d24cd6c12a3e59f602` |
| capped result | `59f266af92c746ee5a8d3fedeeb18ec41cf88474536edf24c148b9bf09d5b336` |

## Conclusion

The one unproven claim in the wall-clearance guard is now measured: preventing
the rear-shell facets from crossing the acoustic surface fixes the acoustic
failure, not merely the mesh diagnostic. The provisional sizing constants are
not retuned by this result; doing that honestly requires a multi-geometry
acceptance corpus rather than one formerly failing design.
