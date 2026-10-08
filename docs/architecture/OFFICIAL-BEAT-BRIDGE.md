# Official BEAT production bridge

By default (unset or empty `WG2_BEAT_PROVIDER`), WG routes the registered `beat-cpu`, `beat-metal`, `beat-cuda` and `beat-rocm`
engines, including AUTO readiness and warm-up, through `official_beat.solve_compiled`.
The legacy explicit `WG2_BEAT_PROVIDER=official` remains accepted. For one
release, set `WG2_BEAT_PROVIDER=hbb` (alias `legacy`) before launch to roll back
solves, readiness, preparation, warm-up and Quit to hornlab-beat-bem. Unknown
values warn once and use the official default. Slice 10 removes the HBB route,
selector and installed rollback gates only after CPU and Metal performance
is proven as fast as HBB or faster.
The optional official distribution is
`beat-engine` from JWSound/BEAT_Engine; imports remain lazy. Pins are unchanged.

WG's `beat_adapter` owns compiled requests, source frames, observations,
unit-acceleration conversion, historical pressure loading and result mapping.
Parametric and imported channel solves support normal/axial motion, cuts,
spheres, retained traces and adaptive frequency batches. Production refuses
rigid ground, infinite baffles and unsupported native symmetry explicitly.
Every backend uses fixed order-4 regular quadrature; BEAT's CPU "wavelength" mode is
never requested, because one global rule is 3-4 dB wrong on graded meshes.

`beat_runtime` owns readiness proof, provider-scoped state, authenticated hosts,
worker admission/reuse and solve staging. Warm-up and production use the same
manager and thread policy. Each SolveSession stages a request and cancellation
marker, forwards startup/compile status, closes its public stream and releases
its lease. A monitor requests cooperative cancellation and retires blocked work
after its grace period; caller exceptions propagate unchanged. Idle workers
remain reusable. Runtime and host directories never overlap HBB's directories.

The explicit `solve_official_beat_from_msh_text` entry uses this production
route and supports an input mesh scale; frames, response artifacts and retained
mesh coordinates are metres while polar distance stays in metres.

Fake-worker tests qualify WG contracts and lifetime handling. Installed
CPU/Metal numerical, cold/warm/Quit/relaunch and platform qualification remain
separate rollout gates; these tests do not establish those gates.

CUDA (NVIDIA) and ROCm (AMD) are offered on Linux and Windows, never macOS.
Hardware detection follows HBB: CUDA, then ROCm, then Metal. NVIDIA detection
uses `nvidia-smi -L` with a 15 s timeout; ROCm detection checks the configured
runtime directories or `rocminfo`/`hipinfo`/`hipInfo` on PATH. Launch-time checks
use only PATH/directory hints; the preparation worker runs the NVIDIA check
after the CPU stage. A positive detection lasts until WG restarts, so install a GPU driver and then
restart WG; negative results expire after 30 s.
Setup instantiates and precompiles `julia_cuda` or `julia_rocm`, then runs the
matching package's `versioninfo()` and `functional()` to resolve device artifacts.
CUDA setup also tries to import CUDSS to fetch coupled-solve artifacts. If CUDSS
cannot load, setup continues and only coupled CUDA solves are refused, as in the engine.
Each backend has its own locked state and a matching 1 kHz solve proof. The
worker handshake must report that backend available. The probe uses Float32,
as production does by default; the adapter also accepts Float64 on CUDA/ROCm.

CUDA/ROCm use the source driver (`driver_mode: "source"`,
`fallback_reason: "backend_has_no_compiled_bundle"`); no compiled GPU bundle
is required for readiness. Their first solve warms slower than a compiled
bundle. These backends are **not hardware-qualified**. Mocked unit/integration
tests and existing CI qualify this routing change; real-GPU qualification is
not a gate for enabling it. CAD solves on CUDA/ROCm still require Accurate;
explicit BEAT Metal also accepts Fast. The official provider is the default;
HBB remains available through the explicit rollback selector.

The conformance and agreement tools in `scripts/beat_conformance` cover CPU
and Metal only. There is no CUDA/ROCm corpus.

Known risk: source-driver startup and `import CUDA` run under the engine's
default 300 s worker startup timeout. This has not been tested on slow Windows
hardware.
