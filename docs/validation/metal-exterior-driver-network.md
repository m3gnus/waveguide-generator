# Native Metal exterior driver network qualification

The backend-only route is experimental and disabled by default. A server
operator can opt in at startup with `WG2_METAL_EXTERIOR_TRANSDUCERS=1`.
There is no new UI control. The `metal` engine advertises
`exterior-transducers` only when enabled, ready, and the installed compatible
worker declares the complete exterior v3 network contract. Admission also
compiles and validates the actual request, including optional driver models;
unsupported requests name BEAT CPU or BEAT Metal as alternatives. Coupled FEM
regions still require BEAT, unless the request explicitly selects exterior-only.
The route requires an explicit `metal` engine selection. AUTO retains its
existing provider for saved voltage jobs even when the server flag is enabled.

The route reuses `DriveChannel.exterior_transducer`, the existing exterior
request builder, mixed assembly excitation planning, typed network decoder and
result packager. It does not run the post-solve LEM scaling path. Voltage bases
use the requested RMS reference voltage; other transducers are shorted. Ideal
source bases use 1 m/s RMS. Results retain per-physical-driver current, motion,
electrical input impedance, mutual radiation matrix and native diagnostics.
Provider metadata identifies `hornlab-metal-bem`, rather than BEAT's native
implementation. The formulation comes from WG's existing exterior policy:
`complex_k`, shift `0.005`. This route makes no Burton–Miller claim.

Stubbed worker tests cover translation, unchanged RMS values and complex field
bytes, provider identity, mixed sources, admission, cancellation, negotiation and
cleanup. These tests establish integration semantics; they do not establish
numerical parity with BEAT or qualify the installed native helper.

Before enabling the route for normal use, qualify the exact installed worker
and helper against BEAT and WG's current LEM path:

1. Use identical outward meshes, media, observation points, source axes and
   frequencies. Include one driven plus one shorted transducer, independent
   voltage bases, and a mixed ideal-source/transducer assembly. Cover explicit
   bare moving mass, declared/inferred Sd and every supported optional model.
   Confirm unsupported models refuse before execution.
2. Compare complex diaphragm velocity, per-driver coil current, electrical
   input impedance, all entries of the mutual radiation matrix and complex
   exterior pressure against BEAT CPU Float64 and BEAT Metal Float32. Record
   precision-aware tolerances, native residuals, reciprocity, passivity, source
   identities and helper/build fingerprints. Preserve the requested RMS voltage
   and verify there is no acceleration, peak/RMS or second-driver scaling.
3. Exercise both phasor conventions at the compatible-worker boundary and
   prove conjugation of complex results. WG's current request/result owner uses
   `exp(-i omega t)` internally and converts displayed impedance to engineering
   convention once; verify that end-to-end conversion separately.
4. Repeat full, x-only half, y-only half (rotated by the shared translator) and
   x/y quarter cases. Check split central diaphragms and complete mirrored
   drivers independently: surface completion and physical driver orbit count
   are different quantities. Verify field superposition, per-driver current,
   total electrical input impedance and radiation row weighting.
5. For a single isolated driver, compare with WG's current post-solve LEM path
   using matched mass, Sd, voltage and phase conventions. Explain differences
   in loading assumptions before setting tolerances. For multiple drivers,
   compare the mutual-load/shorted-coil effects separately; the one-way LEM path
   is not a coupled-network reference. Replay saved ideal-source/LEM designs
   with the flag off and on and require identical meshes and numerical results.
6. Verify streamed/final results, cancellation during native assembly and
   network work, retry, persistence and recombination on an installed build.
   Retain exact request/result artifacts and qualification evidence before any
   default-on or UI rollout decision.
