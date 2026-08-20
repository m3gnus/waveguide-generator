# Live passive-cardioid CAD campaign

Date: 2026-08-20  
Host: Apple Silicon macOS, Metal backend  
Status: workflow gate passed; acoustic-reference validation remains out of scope

## Scope

This pass exercised the real browser UI, CAD-return ingestion, imported Metal
solve, passive-cardioid radiation-impedance campaign, result presentation,
download endpoint, and automatic permanent run archive. Fusion itself was not
used: the disposable `.wgreturn` contained a real STEP solid with independently
tagged `MF` and `PORT_EXIT` faces and was selected through the CAD Link UI.

The test geometry was a 100 mm cube reduced to a quarter domain: 72 triangles,
45 vertices, and two retained 25 cm² aperture patches. This is deliberately a
fast workflow fixture, not a physically representative loudspeaker or an
acoustic accuracy reference.

## Campaign

- Frequencies: 100, 158.7401, 251.9842, and 400 Hz.
- Imported sources: `MF` and `PORT_EXIT`.
- Passive model: coupled solve enabled, 6 L rear volume, 25 mm port length,
  50 cm² model port, 25 cm² BEM port, 10,000 Pa·s/m³ foam resistance, inverted
  port drive.
- Output planes: horizontal polar only; balloon and field-plane retention off.

An intentional first run requested a 100 cm² BEM port. The radiation campaign
still completed, but the derived channel failed closed with the explicit
reason: `matrix=0.0025 m^2, request=0.01 m^2`. No invalid
`passive_cardioid` result channel was published.

With the BEM port corrected to 25 cm², job
`8dddba43-b5d2-49d9-92bb-54d89e64136d` completed in 0.332 s of reported solve
wall time. The event stream used the distinct `radiation_impedance` stage for
configuration and completion, and its log retained all four per-frequency
progress messages.

## Results and artifacts

The Results UI presented `drive-mf`, `drive-port`, and `passive_cardioid`
together. Selecting the derived channel and the **Radiation Matrix Load** chart
showed the engineering-convention load in Pa·s/m³. The Jobs rail exposed the
Radiation Z download action.

The raw NPZ endpoint returned HTTP 200 with a 3,586-byte artifact. The archive
contains both solver- and engineering-convention `(4, 2, 2)` matrices for
`PORT_EXIT` and `MF`; the four frequencies match the campaign, both retained
areas are 0.0025 m², and every passivity diagnostic is true.

The permanent archive for the final run contains:

- both physical drive-channel JSON/CSV results and pressure-basis NPZ files;
- the derived passive-cardioid result and derived-acoustics CSV/JSON exports;
- radiation-impedance NPZ and engineering-convention CSV;
- a self-contained HTML report; and
- `run.json` with the canonical job ID, completed timestamp, radiation artifact
  name, byte count, units, and phase convention.

## Race found and closed

The first successful campaign exposed a completion-order race. The browser
could receive the compact `complete` event just before the metadata event that
advertised retained bases, radiation matrices, final timings, and byte counts.
Automatic archiving made that transient snapshot permanent and omitted files
already present on the server.

`JobsCoordinator` now refreshes the terminal job from the canonical list
endpoint immediately before creating a permanent archive. A focused regression
test covers late pressure-basis and radiation metadata. The rebuilt app's next
campaign produced the complete artifact inventory and manifest described above.

## Remaining live gates

This closes the passive-cardioid UI campaign gate. It does not replace the
separate real-Fusion CAD-link checklist, `.f3d` capture round trip, native
Windows/BEMPP field-plane pass, or a reference-geometry acoustic validation.
