# Wall-clearance acoustic replay note

Date: 2026-08-22
Evidence status: **non-reproducible local observation**. The committed material
supports the solver-neutral design-identity comparison below. It does not
contain the acoustic result arrays, replay request, generated meshes, metric
calculation, or mesh-checker outputs needed to validate the reported acoustic
and mesh observations independently. No causal verdict is published.

## Reproducible scope

The two sanitized archived design snapshots and their identity manifest are
committed. The command below checks them through the real application design
schema and establishes that their identity-bearing physical profile, enclosure,
placement and source inputs normalize equally while their mouth-resolution
requests differ.

The rest of this report records a local replay session whose decisive artifacts
were not committed. In that session, the following execution identities and
solve settings were reported:

| identity | original run 101 | capped replay |
|---|---|---|
| execution geometry | `be0bb99e34a5540fee47f9bb4e2ca7485db71b31936d846132055ecdc749fc84` | same |
| solve options | `8e7610daff0a8e7f4cac4cda332190e84a54e835bacef6849d871a34e5220f14` | same |
| sweep | 60 log-spaced points, 80 Hz–16 kHz | same |
| observation | H/V/diagonal, 0–90° by 5°, 2 m, normalized at 5° | same |

The request label and validation metadata reportedly differed, so the
whole-request digest was intentionally different. The session notes attribute
the material change to the mesher pin moving from `a7cfba26` to the
clearance-capped `50a8d7e1`, but they do not record the exact Waveguide
Generator replay commit or the old and replay Metal SHAs. A clean checkout
therefore cannot recreate that execution environment.

The local replay was recorded as running in an isolated data directory in
56.23 s. That runtime and isolation are session notes, not facts checked by the
committed verifier.

### Solver-neutral design identity

Run 98 used the axisymmetric-meridian path while run 101 and the replay used
full 3D, so their request and geometry-provenance digests are not expected to
match. The committed sanitized snapshots provide a solver-neutral comparison.
Their `mouth_resolution` expressions differ (`15` versus the numeric value
`25`); the v2 identity deliberately excludes that tessellation input. The
uncommitted replay snapshot was recorded as canonically equivalent to the
run-101 snapshot, but that replay-specific equality cannot be rechecked here.

| identity input | committed run 98 | committed run 101 | capped replay (local record) |
|---|---:|---:|---:|
| design revision | 331 | 332 | 332 |
| canonical design-snapshot SHA-256 | `2036d543c19af030d1cde41b1acf8bd5ab26e5d152aaf1b47daa87c75d583bcb` | `9cc792b802699bb8868c2882e138c889b20cba4308bfbb905e732d1956b26de5` | same as run 101 |
| requested mouth resolution | 15 mm | 25 mm | 25 mm |
| solver-neutral model SHA-256 | `2dfeb34b1a0e0dd111fd6b81a883ba9eca0ce37da6cfc6b84e55ad42d81fd2b8` | same | same |

The exact `wg-design-physical-source-v2` field selection and expression
normalization are implemented in `scripts/verify_model_identity.py`. It parses
each snapshot with the application design schema. Constant expressions reduce
to their checked numeric value; parameterized expressions retain their
mesher-compatible executable text. The sanitized run-98 and run-101 design
snapshots and the 40-leaf canonical payload are committed under
`docs/validation/2026-08/`. Reproduce the published identity from a clean
checkout after installing the repository's CPython 3.13 dependencies. Use the
entry point native to the platform rather than assuming `python3` exists on
every supported machine.

**POSIX (macOS/Linux)**

```console
python3.13 -m scripts.verify_model_identity docs/validation/2026-08/wall-clearance-model-identity.json \
  --snapshot docs/validation/2026-08/evidence/wall-clearance-run-98-design-snapshot.json \
  --snapshot docs/validation/2026-08/evidence/wall-clearance-run-101-design-snapshot.json
```

**Windows Command Prompt**

```console
py -3.13 -m scripts.verify_model_identity docs/validation/2026-08/wall-clearance-model-identity.json ^
  --snapshot docs/validation/2026-08/evidence/wall-clearance-run-98-design-snapshot.json ^
  --snapshot docs/validation/2026-08/evidence/wall-clearance-run-101-design-snapshot.json
```

The verifier places the payload in a domain-separated envelope with the v2
schema identifiers, serializes it as UTF-8 JSON with sorted keys, compact
separators, ASCII escaping and non-finite values forbidden, then applies
SHA-256. With `--snapshot PATH`, it can also extract and normalize an ordinary
design snapshot or execution request and compare it to the committed payload.
The schema includes the formula and all profile coefficients, scale and throat
extension, the full morph block, vertical offset, wall thickness, enclosure
depth/edge/clearances, and the complete source block. Mesh tessellation
controls, output flags, legacy solver text and sweep/observation options are
excluded.

| normalized field group | value in the committed run-98/run-101 snapshots |
|---|---|
| profile | R-OSSE; `R=600`, `a=37`, `a0=5.25`, `b=0.3`, `k=0.65`, `m=0.8`, `q=4`, `r=0.3`, `r0=19.5`, `tmax=1` |
| scale and throat | scale 1; extension 5.25° by 12 mm; slot length 0 |
| morph | zero target shape/width/height/corner; rate 3; fixed part 0; shrinkage disabled |
| wall and placement | vertical offset 0; wall thickness 5 mm |
| enclosure inputs | depth 0; edge radius 18 mm, type 1; 25 mm clearance on all four sides |
| source | shape 2; automatic radius (`-1`); curvature 0; unit normal velocity; no contour override |

The differing mouth-resolution value is a discretization choice rather than a
change to the identity-bearing profile or source. Session notes say run 98
resolved automatic selection to the axisymmetric-meridian formulation and
produced a 3.559 mm maximum meridian edge, while run 101 and its replay
requested full 3D with a 25 mm mouth target. Those uncommitted execution details
explain the intended comparison but are not independently verified here.

## Reported local mesh observation

The following values were copied from the local session. Neither mesh, neither
checker output, nor the exact checker implementation, version, tolerances,
triangle-inclusion rules or signed-volume orientation convention is committed.
They cannot be recomputed from this checkout.

| | original run 101 | capped replay |
|---|---:|---:|
| triangles | 10,310 | 10,578 |
| vertices | 5,157 | 5,291 |
| proper crossings | 77 (offline exact check) | 0 (runtime exact check) |
| coplanar overlaps | not recorded | 0 |
| watertight | yes | yes |
| signed volume (m³) | 0.00749178 | 0.00755482 |

The two recorded triangle counts differ by 2.6%. The unavailable checker
evidence prevents this document from independently concluding that every
intersection was removed.

## Reported local acoustic observation

Run 98 was used locally as the CircSym reference for the same solver-neutral
design. The values below were copied from that session. The result arrays and
calculation code are not committed, and the session did not record precise RMS
dimensions and sample counts, frequency-grid matching, dB/null-floor rules,
invalid-sample policy or the definition of “worst directivity delta.” The table
is therefore a custody note, not an independently reproducible calculation.

| metric | broken run 101 | capped replay |
|---|---:|---:|
| on-axis SPL, max absolute delta, full sweep | 20.678 dB | 0.726 dB |
| on-axis SPL, RMS delta, full sweep | 11.333 dB | 0.262 dB |
| DI, max absolute delta, full sweep | 8.883 dB | 0.691 dB |
| DI, RMS delta, full sweep | 2.775 dB | 0.182 dB |
| H directivity, RMS delta, 80–1000 Hz | 5.151 dB | 0.323 dB |
| V directivity, RMS delta, 80–1000 Hz | 5.141 dB | 0.265 dB |
| diagonal directivity, RMS delta, 80–1000 Hz | 5.437 dB | 0.307 dB |
| worst directivity delta, 80–1000 Hz, all three planes | 32.384 dB | 0.962 dB |

The local table records sub-1 dB differences through the reported 1.024 kHz
mesh-validity limit. It also records directivity differences as large as 33 dB
at 8–16 kHz, where the 25 mm mesh has only about 0.4 elements per wavelength at
16 kHz and was flagged `mesh_resolution_suspect`. No mesh-refinement or
convergence run was performed. The high-frequency discrepancy is therefore
unresolved and uninterpretable beyond the recorded mesh-validity limit: mesh
resolution may contribute, but formulation or solver defects have not been
excluded. These observations must not be used to tune clearance constants or
claim high-frequency full-3D parity.

## Unpublished artifact custody

The local replay bundle and archived acoustic results are not committed, and no
repository-relative retrieval procedure is available. Their hashes are listed
only as custody identifiers. They do not independently validate the recorded
contents, calculations, or association between artifacts and runs.

| artifact | SHA-256 |
|---|---|
| archived run-98 result | `ace5070ed3ebd1a7a59514afc1c1f06b85fc9ee69a30663bcf9acd699a599e49` |
| archived run-101 result | `ad5b03d9f533abd736dccc2eb63bef21b5fb1aa953f473a08fb24f42cbec825b` |
| archived run-98 design snapshot (canonical JSON) | `2036d543c19af030d1cde41b1acf8bd5ab26e5d152aaf1b47daa87c75d583bcb` |
| archived run-101 design snapshot (canonical JSON) | `9cc792b802699bb8868c2882e138c889b20cba4308bfbb905e732d1956b26de5` |
| solver-neutral model identity | `2dfeb34b1a0e0dd111fd6b81a883ba9eca0ce37da6cfc6b84e55ad42d81fd2b8` |
| capped effective request | `c5f1fecb77a2829e979ada277aee4a1ec2a3f0332e33fd48f66513ff2e4eea3f` |
| capped mesh | `67677553d3604c80d53dbb2644f1bbefb4760a79f3d019d24cd6c12a3e59f602` |
| capped result | `59f266af92c746ee5a8d3fedeeb18ec41cf88474536edf24c148b9bf09d5b336` |

## Supported conclusion and missing evidence

The committed evidence establishes one narrow fact: the sanitized run-98 and
run-101 snapshots have the same v2 solver-neutral physical/source identity even
though they request different mouth resolutions. It does not establish that the
clearance cap removed all mesh intersections or caused the reported acoustic
improvement.

Promoting this note to a validation verdict requires sanitized run-98, run-101
and replay inputs and acoustic results; exact generator and dependency SHAs;
versioned mesh-check commands and outputs; and a calculation script that emits
every table cell with explicit metric rules. Attributing the high-frequency
discrepancy to resolution additionally requires a full-3D mesh-refinement or
convergence sequence. Until then, the provisional sizing constants must not be
retuned from this session.
