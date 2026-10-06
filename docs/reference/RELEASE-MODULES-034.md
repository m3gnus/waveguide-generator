# Dependency inputs for the 0.3.4 candidate

Status: historical 0.3.4 dependency and qualification record. For current engine
choices, see [the user guide](../USER-GUIDE.md#what-each-backend-can-solve).

The candidate uses the exact commits in `pins.json`. Each Python module below
has an immutable release tag at the same commit; generated requirements retain
the full SHA rather than resolving a tag at installation time.

| Module | Version | Commit | Change adopted by WG |
|---|---|---|---|
| hornlab-waveguide-mesher | 0.2.4 | `b922e807a5cf10cf732c4557ec0b9b495762afd7` | Morph-mouth fitting, resolved preview dimensions, throat stretch and strict ATH import |
| hornlab-metal-bem | 0.2.1 | `18ec6bcce6c02e2338bd046546c082aaf5ea75d5` | Axial source handling and explicit source-axis weighting |
| hornlab-bempp-bem | 0.2.0 | `b88355539437ce28f4620594dca702316aceacd7` | Axial and coupled infinite-baffle support with the required packaged validation code |
| hornlab-beat-bem | 0.2.0 | `df452398ef60048886d2ec0b8805f65a8a6f8a06` | CPU SIMD, sweep reuse and Metal kernel caching |
| hornlab-plots | 0.1.3 | `9e4a6d0dd2ccb6e735dd00233dccccb50d12ed44` | Current plotting implementation and installed-package qualification |
| hornlab-sim | 0.1.2 | `aa67235f5c4685fa95c9411e19762e82f093fcc5` | Combined-pressure impedance correction and native solver seams |

WG keeps its existing public request/result contracts. A module release does
not by itself establish an installed WG release candidate's platform support.
The historical reports under `docs/validation/` retain their original source
identities; results from those reports are not relabelled as final-pin results.

Metal requires macOS 13.3 or later. BEMPP's OpenCL route uses a CPU device that
passes its compute check; GPU OpenCL is not a supported route. Apple Silicon
uses BEMPP's numba fallback when no CPU OpenCL device exists. BEAT provides CPU
and platform-specific GPU backends, whose availability is checked separately.
CUDA and ROCm hardware qualification remains separate from CPU and Metal tests.

WGLink 0.2.1 is packaged from
`7ec3c907e23e46362e47001b7c119c38ccefe3c8`, as recorded in
`integrations/wglink/source.json`. Its manifest version matches the source
specification. The package preserves the upstream sources, license and member
hashes. This version change preserves the CAD protocol and refreshes the
endpoint-oracle provenance against the selected source. Unit fixture versions
remain identifiers of their original cases.
