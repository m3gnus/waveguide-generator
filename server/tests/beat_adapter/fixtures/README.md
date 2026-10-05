`hbb_observations.json` freezes the Float64 scalar coordinate formulas in the
read-only donor `hornlab_beat_bem/julia/BeatEngineDriver.jl:539–677`, inspected
2026-10-05. HBB has no Python point generator. These values were calculated by
an independent Python `math` transcription of that driver, without running
Julia or a solver, and are not numerical solve/parity evidence.

Polar inputs: range -90..90 with step 45, radius 2 m, diagonal inclination 30°.
For each angle a, H=(r sin a,0,r cos a), V=(0,r sin a,r cos a), and
D=(r sin a cos inclination,r sin a sin inclination,r cos a).

Sphere inputs: radius 2 m; full 3×4 and 37×72 grids, plus a 3×4 hemisphere.
The generator looped theta index i outside phi index j, evaluating
t=pi*theta_max/180*i/(n_theta-1), p=2*pi*j/n_phi, then
(r sin t cos p,r sin t sin p,r cos t). The 37×72 fixture stores selected
indices around row boundaries, the equator and both poles; tests also check
every full-grid row/column's axes and radius. All values precede frame mapping.

Float32 geometry is checked at its arithmetic precision. Exact Float64 sphere
axes come from HBB `sweep.py:311–336`, rather than Float32 radian echoes.
