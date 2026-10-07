# Driver library — STX catalogue expansion

Revision: 2026-10-06. Source: STX manufacturer product technical tables in English and Polish, plus the W.38.1200.8.MC embedded parameter-table image. This is a source-data expansion, not an applied correction batch or an official solver release.

Archived evidence for the STX import. The 80 rows are part of the one driver library (`docs/data/driver-library-master.csv`); their sources are also listed in `docs/data/driver-library-provenance.csv`. In this repository: only the master, this report and the two STX sidecars (in this folder) were imported. The package-only files it mentions (`originals/`, `prior-review/`, `research/`, `validate_stx_update.py`, `driver-library-stx-measurement-differences.csv`, `manifest-sha256.json`) are not here. This report is archived research: its counts and statuses describe the 30%-limit screen it was written against. `driver-library-review.md` is the current entry point for counts, holds and driver types.

## Result

The expanded `driver-library-master.csv` contains **5,794 measurement records**, including **93 STX records**. Its first **5,714 rows** are the original master, preserved byte-for-byte. The added **80 records** represent 80 distinct STX catalogue listings reviewed in this pass. Eight share an exact model/impedance identity with an older record and deliberately retain a separate published measurement set. There are **72 additional exact model strings**, not necessarily 72 newly introduced physical products: punctuation aliases, explicit revisions and legacy names are not automatically collapsed. STX has **85 exact model strings** in the expanded master.

The new records contain **60 complete core-T/S sets** and **20 incomplete sets**. Completeness only means Sd, Bl, Re, mass and at least one compliance/Fs representation are present; it is not a quality verdict. No original physical cell or proposed correction was overwritten.

| Category in this research pass | Added records |
|---|---:|
| Woofer | 28 |
| Midrange | 9 |
| Dome tweeter | 12 |
| Full-range | 5 |
| PA woofer | 12 |
| Compression driver | 3 |
| Car subwoofer (DVC) | 6 |
| Waveguide dome tweeter | 1 |
| Compression tweeter | 3 |
| Ceiling two-way assembly | 1 |

Coverage is the 80 distinct listings collected from the linked STX speaker categories, not every discontinued STX product ever made. Horn-only accessories and complete cabinet systems were excluded; one ceiling-driver assembly is retained explicitly as assembly-level data. Catalogue placement can overlap, but each collected listing was added once. Product availability and prices were not imported.

## Screening result

| Preliminary status | Added records |
|---|---:|
| preliminary_pass | 43 |
| numerical_review | 9 |
| incomplete | 20 |
| source_or_supplemental_review | 8 |

The basic reproduced screens pass **51** rows and fail **9** complete rows. A further **8** otherwise numerically passing rows are held for source-identity/parameter conflicts or the supplemental Qts consistency check. Thus **43** new rows receive a preliminary `reliable` label and **37** remain `unreliable`. All 20 incomplete rows are included in the latter number. The ceiling assembly also lacks a separate single-driver model.

The numerical reproduction matches the reliable/unreliable screening status of **all 5,714 original master rows**, with **zero mismatches**, and the previous review audit matches the individual failure flags on all 1,375 original review rows. This does not mean every redundant value was independently verified. The repository's pinned environment, official correction script and official library-regeneration script were **not run**. New `reliable` labels mean only that the standalone checks found no blocking issue in the transcribed table.

The preliminary master now contains 4,382 rows labelled reliable and 1,412 labelled unreliable, before exact deduplication. These are not official regenerated bundle counts.

## Normalization rules

| Source item | Treatment in the library |
|---|---|
| Sd in m² | Multiply by 10,000 to obtain `Sd_cm2`. |
| Maximum **linear** excursion stated peak-to-peak | Divide by two for one-way `Xmax_mm`; mechanical/damage excursion is not substituted. |
| Inductance at 1 kHz | Import into `Le_mH`. |
| Inductance at 10 kHz | Keep in `driver-library-stx-sources.csv`, not `Le2_mH`. The latter is part of a paired LR-2 loss model, not a second measurement frequency or second voice coil. |
| AES power | Import as `Power_W` where explicitly labelled AES. |
| Polish `Moc znamionowa` | Import explicit rated power with its distinct basis in the source sidecar; it is not relabelled AES. |
| Music/program/system or recommended-amplifier power | Do not substitute for driver AES/rated power. |
| Sensitivity | Retain the published number and record its voltage/1 m convention. Do not silently normalize 2 V, 2.83 V or 4 V measurements. |
| Circular dimensions | `Diameter_mm` is the published outside frame/panel diameter, not effective piston diameter. Noncircular panel dimensions stay in notes. |
| Nominal size | Import a stated inch class only; do not infer nominal inches from millimetres or a voice-coil diameter. Tweeter panel size is not dome size. |
| Dual voice coils | Record the table as the series-connected measurement interpretation indicated by its sensitivity note. Five combined nominal impedances are the sum of the stated coil ratings. One contradictory 2x4/2x2 page has `Z_ohm` blank and is held. No parallel or single-coil T/S set is synthesized. |
| Cone-midrange lower crossover | Keep the published frequency and slope in the source sidecar/notes. Do not populate master `XO_min_Hz` for these nine cone midranges because the inspected importer uses that field to classify a row as HF/compression. |
| AES/rated-power test band | Keep separately where recorded; do not treat the lower test-band edge as `Freq_low_Hz`. |
| Unknown mass, force factor, compliance, throat, second loss element | Leave blank. No missing value is invented to satisfy a screen. |

Source tables, rather than marketing summaries, were used for transcribed values. Conflicting summary values are disclosed in each row's notes. Material unresolved model/revision or power/parameter conflicts have an explicit hold. Minor caption or small summary discrepancies remain visible as notes, even when not a blocking hold.

## Important unresolved cases

`W.15.140.8.MCX`: the page publishes Sd = 0.087 m², which maps literally to 870 cm² and exceeds its 151 mm circular frame area. That value is retained as published, with a geometry warning plus coupling/Vas failures. It has not been silently changed to the plausible-looking 0.0087 m².

`W.18.140.8.WMCX`: the page gives Re = 3.3 ohm for the 8-ohm product. The published set fails the reproduced Qes check; no value is borrowed from another impedance or cone variant.

`W.18.180.16.FCX_v2`, `W.18.200.8.MCX_v2`, `W.22.200.8.MCX_v3` and `W.25.400.2x4.MC`: conflicting impedance or revision labels require manufacturer confirmation. Exact page identities and the conflicts are preserved.

`M.15.250.8.FCX`, `M.18.200.8.MCX` and `F.18.140.4.WMCX`: detailed parameter tables disagree with other parts of their pages on power and/or T/S values. Detailed-table data are retained but the rows are held.

`F.30.300.8.MC`: published Qts = 1.51 disagrees with Qms/Qes (approximately 1.045). The basic original screens do not test this relation, so the separate supplemental check holds it. The old `F30.300.8.MC` record remains unchanged; the similar punctuation is not used to overwrite it.

The nine rows that fail basic screens retain all published values. A coupling flag is not proof of a transcription error and must not be repaired simply by increasing Mms or decreasing Sd. Tweeters/compression drivers with incomplete T/S remain useful catalogue references, not complete LEM models. `CS.18.140.4.FG` is a two-way coaxial assembly with an integrated crossover, not a separately characterized LF/HF driver pair.

## Files and integration

`driver-library-master.csv` is the expanded 28-column master. `driver-library-stx-additions.csv` is the 80-row append-only subset in the same schema. Use **one or the other**, not both, to avoid adding the same records twice.

`driver-library-stx-sources.csv` links every added measurement to the manufacturer's table, access date, published peak-to-peak excursion, source inductance frequencies, sensitivity convention, power basis and notes. `master_data_row` is a one-based data-record index, excluding the header; `master_csv_line` includes the header. The plain technical-document reference is suitable for evidence notes; URL fields contain only manufacturer links.

`driver-library-stx-review.csv` is an **80-row research/QA sheet**, including passes and failures, stated/computed diagnostics, missing fields and source holds. Its `record_id` values are local research IDs, **not** the official correction tool's hashed `row_id`. Do not pass this CSV to `apply_driver_corrections.py`. Computed values here are diagnostics, not replacement measurements.

`driver-library-stx-measurement-differences.csv` compares the eight exact model/impedance matches with their older imported records. It records 74 filled or changed physical cells in the new measurement sets. It is **not** an instruction to modify the old records.

`driver-library-duplicates.csv` retains the original five exact duplicates. No new exact physical duplicate was added. Different measurement sets with the same identity remain distinct. Exact deduplication of the expanded master leaves **5,789 physical records**, but this package does not claim to provide an officially regenerated bundle.

`originals/` contains the four original input files unchanged. The old 1,375-row official-format review is intentionally preserved there; it is a **baseline**, not an updated outstanding review for this expanded master.

`prior-review/` preserves the earlier filled review, **22 proposed correction rows and 82 proposed correction cells**, report, change log and validator. They are **not applied** to this expanded master. Preserve this archive before any regeneration of the official outstanding review.

### Repository regeneration

After placing the expanded master in `docs/data/`, use the command documented in the original review, from the repository revision that contains that script and its pinned environment:

```sh
python scripts/screen_driver_library.py docs/data/driver-library-master.csv --out server/drivers/bundled/hornlab-drivers.csv --duplicates-out docs/data/driver-library-duplicates.csv --review-out docs/data/driver-library-review.csv
```

This command is provided from the user's workflow, not claimed to have been executed here. It should generate official row IDs and a new outstanding review. Preserve the separate STX source/QA sidecars: a numerical-only regeneration may not carry forward additional source-conflict or Qts/assembly holds. Do not treat a row as usable merely because that regeneration omits these supplemental warnings.

The previous correction-only CSV can still be validated/applied separately using the original workflow after exact original measurement matching. It does not append new catalogue records and should not be used to import this STX expansion.

### Read-only package audit

```sh
python validate_stx_update.py
```

The audit checks original-file preservation, row counts, schema, every STX unit mapping, record-to-source pairing, duplicate counts, stored screen flags, and the earlier 22/82 proposed corrections. It does not contact the network or write to the repository.

## Record index

All statuses below are preliminary. Detailed numerical diagnostics and source discrepancies are in the two STX sidecars.

| Research ID | Model | Category | Preliminary status |
|---|---|---|---|
| STX-20261006-001 | W.11.100.8.MC | Woofer | preliminary_pass |
| STX-20261006-002 | W.12.100.8.MC | Woofer | preliminary_pass |
| STX-20261006-003 | W.12.100.8.WMC | Woofer | preliminary_pass |
| STX-20261006-004 | W.15.140.8.MCX | Woofer | numerical_review |
| STX-20261006-005 | W.15.160.8.FCX | Woofer | numerical_review |
| STX-20261006-006 | W.15.160.16.FCX | Woofer | preliminary_pass |
| STX-20261006-007 | M.11.100.8.MC | Midrange | numerical_review |
| STX-20261006-008 | T.9.100.4.MS | Dome tweeter | incomplete |
| STX-20261006-009 | W.16.50.4.MC | Woofer | preliminary_pass |
| STX-20261006-010 | W.16.50.8.MC | Woofer | numerical_review |
| STX-20261006-011 | W.18.140.4.MCX | Woofer | preliminary_pass |
| STX-20261006-012 | W.18.140.4.WMCX | Woofer | preliminary_pass |
| STX-20261006-013 | W.18.140.8.MCX | Woofer | preliminary_pass |
| STX-20261006-014 | W.18.140.8.WMCX | Woofer | numerical_review |
| STX-20261006-015 | W.18.180.8.FCX_v2 | Woofer | preliminary_pass |
| STX-20261006-016 | W.18.180.16.FCX_v2 | Woofer | source_or_supplemental_review |
| STX-20261006-017 | W.18.200.8.MCX_v2 | Woofer | source_or_supplemental_review |
| STX-20261006-018 | W.18.200.8.FGX_v3 | Woofer | preliminary_pass |
| STX-20261006-019 | W.18.220.8.MCX | Woofer | preliminary_pass |
| STX-20261006-020 | W.20.80.4.MC | Woofer | preliminary_pass |
| STX-20261006-021 | W.20.80.8.MC | Woofer | preliminary_pass |
| STX-20261006-022 | W.20.140.8.MC | Woofer | preliminary_pass |
| STX-20261006-023 | W.22.200.8.MCX_v3 | Woofer | source_or_supplemental_review |
| STX-20261006-024 | W.22.250.8.FCX | Woofer | preliminary_pass |
| STX-20261006-025 | W.25.200.8.MC | Woofer | preliminary_pass |
| STX-20261006-026 | W.27.400.8.MC | Woofer | preliminary_pass |
| STX-20261006-027 | W.27.500.8.FCX | Woofer | preliminary_pass |
| STX-20261006-028 | W.30.200.8.MC | Woofer | preliminary_pass |
| STX-20261006-029 | W.32.500.8.MC_v2 | Woofer | preliminary_pass |
| STX-20261006-030 | M.12.140.8.MC | Midrange | preliminary_pass |
| STX-20261006-031 | M.12.140.8.WMC | Midrange | preliminary_pass |
| STX-20261006-032 | M.15.150.8.MCX | Midrange | preliminary_pass |
| STX-20261006-033 | M.15.250.8.FCX | Midrange | source_or_supplemental_review |
| STX-20261006-034 | M.16.120.8.MC | Midrange | preliminary_pass |
| STX-20261006-035 | M.18.140.8.MC | Midrange | preliminary_pass |
| STX-20261006-036 | M.18.140.8.WMC | Midrange | preliminary_pass |
| STX-20261006-037 | M.18.200.8.MCX | Midrange | source_or_supplemental_review |
| STX-20261006-038 | F.18.140.8.WMCX | Full-range | preliminary_pass |
| STX-20261006-039 | F.18.140.8.MCX | Full-range | preliminary_pass |
| STX-20261006-040 | F.30.300.8.MC | Full-range | source_or_supplemental_review |
| STX-20261006-041 | W.32.800.8.FCX | Woofer | preliminary_pass |
| STX-20261006-042 | F.18.140.4.WMCX | Full-range | source_or_supplemental_review |
| STX-20261006-043 | F.18.140.4.MCX | Full-range | preliminary_pass |
| STX-20261006-044 | W.25.300.8.MC_v2 | PA woofer | preliminary_pass |
| STX-20261006-045 | W.25.400.8.MCX | PA woofer | preliminary_pass |
| STX-20261006-046 | W.30.300.4.MC | PA woofer | numerical_review |
| STX-20261006-047 | W.30.300.8.MC | PA woofer | preliminary_pass |
| STX-20261006-048 | W.30.500.8.MC v2 | PA woofer | numerical_review |
| STX-20261006-049 | W.30.750.8.MCX | PA woofer | preliminary_pass |
| STX-20261006-050 | W.38.500.4.MC | PA woofer | numerical_review |
| STX-20261006-051 | W.38.500.8.MC | PA woofer | numerical_review |
| STX-20261006-052 | W.38.800.4.MC | PA woofer | preliminary_pass |
| STX-20261006-053 | W.38.800.8.MC | PA woofer | preliminary_pass |
| STX-20261006-054 | W.46.1300.8.MC | PA woofer | preliminary_pass |
| STX-20261006-055 | W.38.1200.8.MC | PA woofer | preliminary_pass |
| STX-20261006-056 | D.9.500.8.TI | Compression driver | incomplete |
| STX-20261006-057 | D.12.800.8.TI | Compression driver | incomplete |
| STX-20261006-058 | D.14.1000.8.TI | Compression driver | incomplete |
| STX-20261006-059 | W.20.300.2x2.MC | Car subwoofer (DVC) | preliminary_pass |
| STX-20261006-060 | W.20.300.2x4.MC | Car subwoofer (DVC) | preliminary_pass |
| STX-20261006-061 | W.25.400.2x2.MC | Car subwoofer (DVC) | preliminary_pass |
| STX-20261006-062 | W.25.400.2x4.MC | Car subwoofer (DVC) | source_or_supplemental_review |
| STX-20261006-063 | W.30.500.2x2.MC | Car subwoofer (DVC) | preliminary_pass |
| STX-20261006-064 | W.30.500.2x4.MC | Car subwoofer (DVC) | preliminary_pass |
| STX-20261006-065 | T.9.100.8.MS | Dome tweeter | incomplete |
| STX-20261006-066 | T.9.200.8.PC | Dome tweeter | incomplete |
| STX-20261006-067 | T.10.100.8.PC | Dome tweeter | incomplete |
| STX-20261006-068 | T.10.150.4.MS | Dome tweeter | incomplete |
| STX-20261006-069 | T.10.150.8.MS | Dome tweeter | incomplete |
| STX-20261006-070 | T.10.150.8.MSX | Dome tweeter | incomplete |
| STX-20261006-071 | T.10.200.8.PC | Dome tweeter | incomplete |
| STX-20261006-072 | T.10.200.8.PCX | Dome tweeter | incomplete |
| STX-20261006-073 | T.10.200.8.MSX | Dome tweeter | incomplete |
| STX-20261006-074 | T.10.250.8.PCX | Dome tweeter | incomplete |
| STX-20261006-075 | T.10.200.8.ALX | Dome tweeter | incomplete |
| STX-20261006-076 | T.10.250.8.PC | Waveguide dome tweeter | incomplete |
| STX-20261006-077 | T.10.800.8.AL | Compression tweeter | incomplete |
| STX-20261006-078 | T.9.250.8.PH | Compression tweeter | incomplete |
| STX-20261006-079 | T.18.250.8.PH | Compression tweeter | incomplete |
| STX-20261006-080 | CS.18.140.4.FG | Ceiling two-way assembly | incomplete |

## Reproducibility

`research/stx-published-parameters.csv` retains the transcribed manufacturer parameters before area, excursion and inductance mappings into the master. Its `Z_ohm` field includes the explicitly documented combined-series interpretation for five DVC products; it is not a claim that those combined values are printed literally on the page. Manufacturer URLs and the separate source notes remain the evidence references. `manifest-sha256.json` records checksums for the delivered package files.
