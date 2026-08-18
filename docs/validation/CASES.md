# Validation cases

Reference comparisons between simulated and measured responses. Each case
follows [MEASUREMENT-TEMPLATE.md](MEASUREMENT-TEMPLATE.md). Cases whose raw
data is not redistributable here name their public source instead.

## CAFMEH-P3 (multi-entry horn, published 2026-06-16)

The first real-measurement validation of the native Metal BEM engine that
powers WG's Metal solves, published in the diyAudio thread
[BIGMEH design exploration](https://www.diyaudio.com/community/threads/bigmeh-design-exploration.440373/post-8318332).

- **Device:** CAFMEH prototype 3, a compact multi-entry horn (modified
  Tritonia profile). Designed by m-a; built and measured by mbrennwa, whose
  measurement set is published in the
  [CAFMEH thread](https://www.diyaudio.com/community/threads/compact-almost-fullrange-meh.430398/).
- **Measurement:** outdoors, 1 m from the mouth plane, 1 Vrms MLS, absolute
  (loopback) timebase, MATAA toolchain; LF and HF sections raw plus the
  FIR-crossed sum, horizontal and vertical, 0–90° in 10° steps; far-field
  gating limits validity to ≥163 Hz. The FRD exports load directly through
  WG's *Overlay measured…* importer.
- **Simulation:** CAD geometry solved by hornlab-metal-bem (complex-k
  Burton–Miller, corrected assembly, `yz+xz` symmetry, 50 Hz–20 kHz), driven
  through the CAD STEP pipeline. Note: the geometry entered via CAD import,
  not a parametric WG design — the case validates the solver stack, not the
  parametric mesher.
- **Agreement:** LF front-chamber Helmholtz resonance within ~2 % (chamber
  volume was deliberately tuned, as the post states, since exact cone
  dimensions were unknown); HF on-axis RMS deviation vs measurement over
  1.2–6 kHz of 2.4 dB with rigid walls and 1.8–1.9 dB with a uniform
  impedance boundary (β = 0.05); directivity, the mids' quarter-wave notch,
  and the Helmholtz peak/notch reproduce without any damping. Rigid-wall
  numbers are the honest headline — β = 0.05 corresponds to ~18 % absorption,
  optimistic for hard walls; damping is a sensitivity, not a claim.
- **Caveats:** the published solve's mesh was resolution-valid to ~1.6 kHz; a
  follow-up mesh ladder (wall 30→5 mm) extended validity to ~4.9 kHz and is
  the rung to cite above 1.6 kHz. Mid drivers are 4× BMS 5N160 (confirmed by
  the designer, 2026-08-18).
