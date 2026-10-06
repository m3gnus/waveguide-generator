# Official BEAT production bridge

`WG2_BEAT_PROVIDER=official` routes the registered `beat-cpu` and `beat-metal`
engines, including AUTO readiness and warm-up, through `official_beat.solve_compiled`.
The default selector retains HBB. The optional official distribution is
`beat-engine` from JWSound/BEAT_Engine; imports remain lazy. Pins are unchanged.

WG's `beat_adapter` owns compiled requests, source frames, observations,
unit-acceleration conversion, historical pressure loading and result mapping.
Parametric and imported channel solves support normal/axial motion, cuts,
spheres, retained traces and adaptive frequency batches. Production refuses
rigid ground, infinite baffles and unsupported native symmetry explicitly.
CPU retains wavelength quadrature; Metal retains fixed quadrature.

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
