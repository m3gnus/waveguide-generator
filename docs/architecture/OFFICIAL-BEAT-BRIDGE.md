# Official BEAT worker bridge (unregistered qualification slice)

`server/solver/official_beat.py` is an executable, standalone adapter for the
installed official `beat_engine` worker, built on HornLab's shared full-3D
solver port. It is **not** in the production registry or AUTO routing. The
current HornLab BEAT fork pin remains unchanged.

The first supported path is a parametric, full-domain, free-air exterior BEM
solve with a single source moving at uniform **normal velocity**. The bridge
uses HornLab's authoritative source-tagged Gmsh 2.2 mesh, source frame and
polar points. WG-generated mesh coordinates are metres (`scale_to_m=1`); the
standalone qualification entry accepts an explicit scale for millimetre ATH
fixtures, scaling the mesh-derived observation origin and source area while
keeping the requested polar distance in metres. Region constants are
`c=343 m/s`, `rho=1.2041 kg/m³`; q4/s4 fixed quadrature and
`exp(-i omega t)` are explicit. The worker's 1 m/s complex pressure and
force are scaled by `1/(-iω)` for HornLab's unit-acceleration response.

The installed worker supplies `EngineWorker` and `engine_paths`. Before any
request reaches stdin, the adapter validates the compiled request and
negotiates worker protocol 1, `system_request` 1, `compiled_system` 1,
`system_result` 2, selected backend/precision/phasor and marker-file
cancellation through official `negotiate_submission`. CPU and Metal are
selected independently; neither falls back to the other. A job-owned
temporary directory holds absolute mesh, request and cancellation-marker
paths until the stream is closed and worker terminated. A monitor interrupts
blocked startup or result reads on cancellation. Binary complex outputs are
checked for identity, units, axes, shape, encoding, byte order, byte count and
finiteness before any provisional or final mapping. Endpoints-first streamed
frequency order is sorted only for the durable result.

The adapter explicitly refuses axial-piston motion (one scalar normal drive
cannot represent face-dependent `n·axis`), ground, imported/multi-source
geometry, coupled infinite baffle, reduced-domain symmetry and spherical
sampling. Surface field traces are marked unavailable in the normalized
result. Directivity index is likewise unavailable because the fork's sphere
grid is not yet translated, even when a user did not request the full balloon.
Production registration, AUTO routing and the fork pin must wait for
qualification of those modes and near/field trace parity. The temporary
runtime resolver still consults the retained fork's provisioned Julia record;
before fork removal, runtime provisioning must become app-owned.

Rollout evidence still required: installed-wheel CPU/Metal startup and solve
on the target release, matched complex pressure and directivity on ATH full
and quarter meshes at 3 frequencies, 40/100-frequency sweeps, cancellation
under real worker load, and exact-pin full server tests. A standalone
`OfficialBeatEngine` constructor is not capability advertisement.
