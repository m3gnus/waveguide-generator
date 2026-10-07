# Driver library review

Owner decision: Magnus, 2026-10-06. Keep all original and imported measurements.
The bundled library contains 5,709 windings: 4,334 reliable and 1,375 unreliable.
Failures overlap: coupling_failed 1,295, fs_inconsistent 12, qes_inconsistent 51,
vas_inconsistent 26. Five exact duplicates after model-name cleanup are listed
in `driver-library-duplicates.csv`; conflicting measurements remain distinct.
`driver-library-master.csv` preserves all 5,714 original/import rows as a local,
physical-data master for reproducible regeneration and correction.

## Task for the reviewing AI

Research each unreliable winding against the manufacturer's technical document
or independently measured T/S data. Fill only `corrected_<param>`,
`correction_source`, and `notes`. Do not guess values merely to satisfy equations.
Keep uncertain rows unchanged and explain uncertainty in notes. Keep manufacturer,
model, original cells and row_id unchanged. Rows may be sorted or omitted, and a
partially completed review can be applied. A corrected row must pass every screen.
Use a plain document title/revision and page/table in correction_source.
Do not include licensing text, attribution, or retailer links.

## Columns and units

- `row_id`: hash of the cleaned identity and every original physical cell. It
  distinguishes conflicting measurements even with the same model/impedance.
- `manufacturer`, `model`: cleaned manufacturer identity.
- Original physical columns: `Size_in`, `Throat_in` in inches; `Diameter_mm`,
  `Xmax_mm` in mm; `Z_ohm`, `Re_ohm`, `Re2_ohm` in ohms; `Le_mH`, `Le2_mH` in mH;
  `Bl_Tm` in T m; `Sd_cm2` in cm²; `Mms_g`, `Mmd_g` in grams;
  `Cms_mm_per_N` in mm/N (**not m/N**); `Vas_L` in litres; `Fs_Hz`, `XO_min_Hz`,
  `Freq_low_Hz` in Hz; `Qms`, `Qes`, `Qts` dimensionless; `Rms_kg_per_s` in kg/s;
  `Power_W` in W; `Sensitivity_dB` in dB. Empty means not stated.
- `failing_checks`: semicolon-separated reason codes.
- `reliability_reasons`: human-readable failures, stated/computed values and %
  difference where computation is available. Additional import failures may
  be `driver_spec_invalid` or `derivation_failed`.
- `fs_*`, `qes_*`, `vas_*`: stated and computed values in Hz, dimensionless and
  L respectively, plus absolute percentage difference relative to stated.
  The tolerance remains 20%. Compliance follows Cms, then Vas, then Fs, using
  the app solver's air constants. Qes uses the solver's Fs.
- `mass_correction_*`: stated Mms and computed dry Mmd in grams; difference_pct
  is the free-air radiation-mass correction as % of Mms (coupling limit 30%).
  Blank computed cells mean the inputs could not be derived, not zero.
- `corrected_<param>`: blank means keep the original. Enter a bare decimal or
  scientific-notation number in the original column's units. `CLEAR` removes
  an optional value; removing a required value fails validation.
- `correction_source`, `notes`: evidence document/page and explanation.

## Validate and apply

From the repository root, with the pinned server Python environment:

```sh
python scripts/apply_driver_corrections.py docs/data/driver-library-review.csv
python scripts/apply_driver_corrections.py docs/data/driver-library-review.csv --apply --review-out docs/data/driver-library-review.csv
```

The first command validates without writing. The second validates all filled
corrections, updates the local master and bundle, recomputes reliability on every
row, and regenerates the outstanding review. Blank corrections are skipped.
Bad numbers, unit-suffixed cells, invalid physics, altered original values,
duplicate corrections, and stale/unmatched rows are refused before any write.
The correction source and notes stay in the completed review; archive that filled
file separately before replacing it with an outstanding review.
For other local masters, repeat `--master path.csv`; `--bundled path.csv` selects
another bundle. Every correction must match the bundle and at least one master by the same
original physical values. Differing
measurements with the same identity are never overwritten by identity alone.

Regenerate from the local master with:

```sh
python scripts/screen_driver_library.py docs/data/driver-library-master.csv --out server/drivers/bundled/hornlab-drivers.csv --duplicates-out docs/data/driver-library-duplicates.csv --review-out docs/data/driver-library-review.csv
```

Reliability does not imply every redundant value was independently verified.
Missing quantities are never invented. Unreliable rows remain browsable with
reasons and are refused before solving until corrected and re-screened.
