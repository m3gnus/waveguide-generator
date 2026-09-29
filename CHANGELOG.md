# Changelog

## Unreleased

### Infinite baffle: results change

- Infinite-baffle solves on Metal and BEMPP now use the plain wavenumber instead of the small
  imaginary shift (complex-k, 0.005) that every Fast solve used before. The horn in an infinite
  baffle is closed by the radiating aperture, so there are no fictitious resonances for the shift
  to suppress; it only made the real cavity resonances look milder. Expect sharper, deeper
  resonances than in earlier infinite-baffle results. Free-standing and ground-plane solves are
  unchanged and still use the shift.
- Results you already solved keep their recorded formulation and open as before. A job that was
  submitted under an earlier build is never treated as the answer to a new infinite-baffle
  request; solving again produces a new result with the new formulation.
- The infinite-baffle aperture mesh scale now defaults to 1 (the aperture cap uses Mouth mesh
  resolution) instead of 1.5. The coarser cap lost accuracy above roughly 6 kHz and by several dB
  at 8-12 kHz on the horns checked. Designs that already store 1.5 keep it. The field now
  accepts values of 1 and above only; a lower value was always refused when the mesh was built.
- Infinite baffle now stays listed, disabled with a reason, when the selected engine cannot run
  it but another engine on the host can.
