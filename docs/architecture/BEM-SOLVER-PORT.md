# HornLab full-3D BEM solver port

`server.solver.base.Full3DSolverPort` is the application-facing boundary for
BEAT and Metal. Both adapters expose `run(request, cancel_cb, stage_cb,
artifact_cb, result_cb, imported_record)` and return `EngineRunResult`. The job
runner owns persistence, progress delivery and cancellation state; an adapter
owns meshing, native submission, response mapping and its worker lifetime.
`EngineInfo` in `server.engines.registry` remains the capability/readiness
snapshot used before creating a port. These are HornLab contacts, not a request
for Metal to implement BEAT's Julia protocol. Registered adapters opt in with
`FULL3D_SOLVER_PORT_MARKER`; the runner never infers the contract from an
engine-name prefix.

The installed BEAT pin still uses the `hornlab-beat-bem` `SolveConfig` API.
Replacing that pin with official `beat_engine` requires a separate adapter,
not an import rename. Keep the existing BEAT adapter and Metal adapter
selectable while that bridge is developed.

## Official BEAT worker bridge

1. Probe the selected official BEAT backend, its Julia runtime and the
   versioned `beat-worker` ready message. Advertise only capabilities actually
   exercised by this HornLab adapter. Do not infer readiness from package
   import alone.
2. Build HornLab's authoritative Gmsh 2.2 mesh as today. Write that artifact
   and a version-1 `compiled_system` request to a job-owned temporary area.
   Map physical tag 2 to the moving source boundary and other relevant surface
   groups to rigid boundaries, with explicit metre scale, unbounded-air
   region, sound speed and density. Validate the request against official
   BEAT's system contract before submission.
3. Translate the exact live frequency list, supported normal source motion,
   observation frame and polar points into excitation ports and output
   requests. Preserve HornLab's `exp(-i omega t)` outgoing-wave convention;
   verify complex pressure, not only SPL. Preserve source-tag framing and
   unit normal-acceleration scaling in both partial and final results.
4. Negotiate worker protocol, backend, precision, operation and phasor in the
   ready/submission handshake. Consume progress and per-frequency result
   events; close or cancel the submitted iterator on abandonment so a later
   solve cannot inherit its events. Map native results through the existing
   HornLab response builder and return `EngineRunResult`, including mesh and
   field-trace availability metadata.
5. Add parity tests against the retained BEAT fork for full and quarter ATH
   meshes at 3, 40 and 100 frequencies, along with cancellation, concurrent
   submission, malformed/partial output and worker-restart tests. Gate a pin
   change on these tests and an installed-package smoke, not an editable
   checkout.

## Rollout boundaries

The current BEAT port accepts parametric single-source exterior solves. It
refuses imported CAD/multi-source solves, coupled infinite-baffle solves and
rigid ground-plane solves. Their `run` entry points refuse ground before
meshing or publishing an artifact, and the native mesh entry points refuse it
again; neither may silently return a
free-standing result. Metal retains imported multi-source and coupled-baffle
paths that the official BEAT bridge must not advertise without implementation.

The initial official bridge must reject axial-piston source motion: its
`normal_velocity` port applies one scalar to a source boundary, not the
per-face normal projection of an axial piston. Do not substitute a uniform
normal velocity. Before routing further source modes to the official worker,
qualify the complete positive-time/negative-time phasor path and add an
explicit per-face axial-drive contract. Before
advertising near-field/field-trace output, verify the returned pressure basis,
surface traces, units, orientation and retention limit. Before advertising
half or quarter symmetry, verify the requested mirror planes and positive
fundamental domain. A future ground-plane or coupled-baffle capability needs
its own geometry/physics validation and parity tests; it is not implied by a
worker-level option. Unsupported modes must fail at submission, before an
expensive solve, and capability rows must describe the same restrictions.
