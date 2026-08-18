# Measurement template for a validation case

Status: template. Copy it into a dated folder under `docs/validation/` (for example
`docs/validation/2026-09/tritonia-q-v03.md`) and fill it in beside the files it names.

A validation case is a claim that a simulated response and a measured one agree, or
disagree by a stated amount. That claim is only reproducible if a second person can
rebuild both sides of it. The blocks below are what it takes: what was solved, what was
measured, and where the two were made to line up.

Simulation and measurement never meet in a single tool here — the solve runs in the app
and the measurement runs in REW or an equivalent — so the reconciliation happens in the
person filling this in. Anything left blank is an assumption the reader will have to
make instead.

---

## 1. Identity

| Field | Value |
|---|---|
| Case name | |
| Date measured | |
| Measured by | |
| Waveguide / device under test | |
| Build notes (print material, layer height, finish, mounting) | |

## 2. Simulated side

| Field | Value |
|---|---|
| Design file | `<name>.cfg`, committed or archived where? |
| Design revision / run name | e.g. `260903tritonia-q_v03` |
| App version | |
| Engine | `metal`, `bempp`, `auto` as resolved |
| Symmetry | as resolved, not as requested |
| Frequency range or explicit list, and count | |
| Mesh element size and any refinement | |
| Enclosure / baffle / infinite-baffle mode | |
| Exported artifacts | e.g. `…_on_axis.frd`, `….csv` |

Record the **resolved** values, which the result's Summary card and the exported FRD
header both state. A requested polar step of 0.3° that resolved to 0.375° is the one the
physics ran at.

### Observation settings

These are the settings the measurement has to be built to match, under Directivity in
solve settings:

| Setting | Value | Notes |
|---|---|---|
| Distance | ` m` | Effective distance, as reported in the result metadata and the FRD header. |
| Observation origin | `mouth` or `throat` | Decides where the distance is measured *from*. Getting this wrong shifts level and phase but leaves the response shape plausible, so it is the easiest field to be quietly wrong about. |
| Normalization angle | `°` | |
| Angle range and step | | |
| Planes solved | horizontal / vertical / diagonal | |

## 3. Measured side

### Conditions

| Field | Value |
|---|---|
| Microphone distance | ` m` — measured from the same physical point the observation origin names |
| Microphone height and axis alignment | how on-axis was established |
| Environment | anechoic / outdoor / gated in-room; ground plane? |
| Gate length and resulting low-frequency limit | ` ms` → valid above ` Hz` |
| Temperature | ` °C` |
| Static pressure | ` kPa` |
| Relative humidity | ` %` |
| Noise floor | ` dB SPL`, A- or Z-weighted (say which) |

The solve does **not** take temperature, pressure or humidity: it runs at the pinned
solver constants (343 m/s and 1.2041 kg/m³ — see `server/solver/acoustics.py`). The
conditions are recorded so the reader can judge the mismatch, not because the app
consumes them. Air at 30 °C carries sound about 2 % faster than the solve assumes, which
moves every interference feature by roughly that fraction in frequency; state the
conditions and that shift becomes explicable instead of surprising.

### Hardware and calibration chain

| Link | Value |
|---|---|
| Microphone (model, serial) | |
| Calibration file used | filename, and 0° vs 90° variant |
| Preamp / interface | |
| Sound card calibration file | |
| SPL calibration reference and level | e.g. 94 dB @ 1 kHz piston, when |
| Amplifier and drive level at the terminals | e.g. 2.83 V |
| Driver (model, serial) | |
| Measurement signal | e.g. 256k log swept sine, 20 Hz–20 kHz |
| Averages | |
| Smoothing applied in the measurement tool | e.g. 1/12 octave, or none |

State the smoothing even when it is none. The overlay does not smooth a measured trace —
it is drawn exactly as the file states it — so an unlabelled smoothed file read as raw is
a quietly wrong comparison.

## 4. Files

Name the exports so the pairing survives being copied out of its folder:

```
<case>/
  <case>.cfg                       design as solved
  <case>_sim_on_axis.frd           exported from the app
  <case>_meas_on_axis_1m.frd       exported from REW
  <case>_meas_hor_<angle>.frd      one per measured angle, angle last in the name
  <case>.md                        this document, filled in
```

The angle goes last because that is the token measurement importers key on; the same
convention the app's own polar FRD export uses (`hor/<stem> 30.frd`).

## 5. Loading the overlay

1. Export the measurement as text from REW (or any tool writing FRD): comment lines
   introduced by `*`, `;`, `#` or `//`, then rows of `freq_hz spl_db [phase_deg]`
   separated by whitespace, tabs or commas. Both two- and three-column files load.
2. In the app's Results panel, click **Overlay measured…** in the toolbar and pick one or
   more files. Each is drawn on the on-axis SPL chart as a dotted curve named
   `<filename> (measured)`.
3. Use the per-overlay **dB** field to match levels. It shifts the drawn curve only; the
   file is never modified. Record the offset you used and why — a 6 dB offset that turns
   out to be a 2 V versus 2.83 V difference is part of the result, not a nuisance.
4. Hide or remove overlays with the row's own controls. Overlays live for the session
   only and are not restored by a reload: the app never copied the file, and a remembered
   curve that nothing on disk still backs is worse than reloading it.

Notes on what the chart does and does not do:

- The frequency axis stays on the **simulated** sweep. A measurement spanning 20 Hz–20 kHz
  against a solve covering 500 Hz–8 kHz is clipped, not zoomed out to fit.
- The smoothing preference applies to simulated curves only, for the reason in §3.
- Rows that are unreadable are skipped; a file yielding fewer than two usable points is
  rejected with a message naming it.

## 6. Result

| Band | Simulated | Measured | Delta | Comment |
|---|---|---|---|---|
| | | | | |

Close with the claim in one sentence: over which band, within how many dB, and which
disagreements are unexplained. An unexplained disagreement recorded now is worth more
than a tidy table that omitted it.
