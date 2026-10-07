# Driver library review

The driver library is a document of driver data. The app reads
`server/drivers/bundled/hornlab-drivers.csv`. It skips rows it cannot parse, and it
never refuses a driver because of its reliability columns. Those columns are
information for the person choosing a driver.

## Contents

`driver-library-master.csv` is the physical-data master. It holds 5,794 rows: the
5,714 original imports, plus 80 STX manufacturer-catalogue rows added on
2026-10-06, with 17 rows corrected by research batch 01. The bundled library
holds those rows minus the 5 exact duplicates listed in
`driver-library-duplicates.csv`: **5,789 windings, 5,558 marked reliable and 231
marked unreliable**. Conflicting measurements of the same model stay as separate
rows.

## What "unreliable" means

The reliability columns were computed on 2026-10-06 and are not recomputed by
the app. A winding is marked unreliable, with the reason, when:

| Reason | Rows | Meaning |
|---|---:|---|
| `coupling_failed` | 130 | The estimated air mass, 2·(8/3)·ρ·a³ = 1.149·Sd^1.5, is more than 50% of the stated Mms, or Mms is missing or too small to subtract it. This matches Klippel's convention Mmd = Mms − 1.13·Sd^1.5 within 1.7%. hornlab-sim refuses such a driver when a solve runs. |
| `qes_inconsistent` | 46 | Qes recomputed from Fs, Mms, Re and Bl differs from the stated value by more than 20%. |
| `vas_inconsistent` | 23 | Vas recomputed from Cms and Sd differs by more than 20%. |
| `driver_spec_invalid` | 20 | Sd, Bl or a moving mass is not published. These are STX tweeters, compression drivers and a ceiling assembly, kept as catalogue entries. |
| `fs_inconsistent` | 10 | Fs recomputed from Mms and Cms differs by more than 20%. |
| `source_hold` | 7 | The manufacturer's own page contradicts itself on impedance, revision, power or parameters. The reason names the conflict. |
| `qts_inconsistent` | 2 | The stated Qts differs from Qms·Qes/(Qms+Qes) by more than 20%. |

A row can have more than one reason. `driver-library-review.csv` lists the 231
unreliable windings with their stated and recomputed values. Its `corrected_*`,
`correction_source`, `notes` and `hold_resolution` columns are blank and kept
for any later manual review.

## Known misclassified rows

The app decides a row's picker group from its fields. A throat size, a diameter
without Sd, or a recommended crossover makes a row a compression driver. These
16 STX products publish a panel or horn diameter and a recommended crossover,
so they appear as compression drivers. They are not bare compression drivers.
Their published values are kept unchanged.

| Model | Product type |
|---|---|
| T.9.100.4.MS, T.9.100.8.MS, T.9.200.8.PC, T.10.100.8.PC, T.10.150.4.MS, T.10.150.8.MS, T.10.150.8.MSX, T.10.200.8.PC, T.10.200.8.PCX, T.10.200.8.MSX, T.10.250.8.PCX, T.10.200.8.ALX | dome tweeter |
| T.10.250.8.PC | waveguide dome tweeter |
| T.10.800.8.AL, T.9.250.8.PH, T.18.250.8.PH | tweeter with an integral horn |

The bare STX compression drivers D.9.500.8.TI, D.12.800.8.TI and D.14.1000.8.TI
are correctly classified.

## Provenance and research

`driver-library-provenance.csv` names the source of every measurement that has
been checked against one. Each entry gives the row_id (a hash of the identity and
every physical cell), the source type, the document, the manufacturer URL, the
access date, and the evidence folder. It has 120 entries; rows not listed have no
recorded source. The evidence is archived in `driver-research/`:

- `stx-import/`: the report, sources and QA sheet for the 80 STX rows. Its
  counts predate the final screen.
- `batch-01/`: 40 consistency failures researched against manufacturer data.
  17 rows were corrected and are applied in the master. 20 were left unchanged
  because the source contradicts itself, 2 sources were not found, and for 1
  the manufacturer publishes no data for that variant. The folder also holds a
  cell-by-cell comparison, per-row sources and a note on the air-mass
  convention.

Source research is closed. The remaining unreliable rows are accepted as
documented.

## Columns and units

`Size_in` and `Throat_in` are in inches. `Diameter_mm` and `Xmax_mm` (one-way)
are in mm. `Z_ohm`, `Re_ohm` and `Re2_ohm` are in ohms; `Le_mH` and `Le2_mH` in
mH. `Bl_Tm` is in T·m and `Sd_cm2` in cm². `Mms_g` and `Mmd_g` are in grams.
`Cms_mm_per_N` is in mm/N (**not m/N**). `Vas_L` is in litres. `Fs_Hz`,
`XO_min_Hz` and `Freq_low_Hz` are in Hz. `Qms`, `Qes` and `Qts` are
dimensionless. `Rms_kg_per_s` is in kg/s, `Power_W` in W and `Sensitivity_dB` in
dB. An empty cell means the value is not stated; no missing value is invented.
