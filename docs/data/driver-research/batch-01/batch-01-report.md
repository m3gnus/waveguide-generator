# HornLab driver-library source research — batch 01

Date: 2026-10-06 · Input snapshot: waveguide-generator `77be64fc`

## Delivered result

**40 Group A rows researched; 17 rows have 101 proposed correction cells: 65 replacements and 36 filled blanks.** This is one completed research batch, not completion of the entire archive. The other **208 unreliable rows** and **164 lower-priority Vas-rounding rows** were not researched in this pass. Original row identities, physical values, diagnostics, and all input files are unchanged. No correction has been applied to the master or shipped library.

| Primary notes tag | Rows | Rows with corrections |
|---|---:|---:|
| TRANSCRIPTION_ERROR | 10 | 10 |
| WRONG_VARIANT | 8 | 7 |
| SOURCE_INCONSISTENT | 20 | 0 |
| NOT_FOUND | 2 | 0 |
| MMD_PUBLISHED | 0 | 0 |
| VERIFIED_MATCHES_SOURCE | 0 | 0 |

`WRONG_VARIANT` includes one unrepaired 8-ohm FR 8 WP record. The separately published Mmd on FaitalPRO 15FH520 is added under its primary `WRONG_VARIANT` tag, rather than giving the row a second primary tag.

Coverage: Celestion 5; Dayton Audio 7; Faital Pro 3; SB Acoustics 3; SEAS 1; Tang Band 3; Visaton 16; Wavecor 2.

## Every corrected row

Additional directly published metadata may also be filled. The change log contains all 101 cells; source links below point only to manufacturers.

| Row ID | Driver / nominal impedance | Main change |
|---|---|---|
| `ddfd14bd3f9b37bb0579aeea` | [Celestion FTX0820 — 8 Ω](https://celestion.com/product/ftx0820/) | Current LF motor/suspension set: Bl 14.56 Tm, Mms 22.66 g, Vas 13.13 L; HF values excluded. |
| `f44c78160402477a8b11241f` | [Celestion Truvox 0820 — 8 Ω](https://celestion.com/product/truvox-0820/) | Bl 1.51 → 11.51 Tm; directly published Vas 17.25 L. |
| `2b393ac7a4954373b9529321` | [Dayton Audio CE54N-4 — 4 Ω](https://www.daytonaudio.com/images/resources/285-167--dayton-audio-ce54n-4-specification-sheet.pdf) | Vas 0.08495053978 → 0.111 L. |
| `794249e5d4bae8938d9267d0` | [Dayton Audio HARB252-8 — 8 Ω](https://www.daytonaudio.com/product/1282/harb252-8-1-x-5-dual-motor-high-aspect-ratio-bmt-8-ohms) | Vas 0.2831684659 → 0.19 L; current published Sd/Mms/compliance/Xmax. |
| `14de91c4979d53eb631a3fe5` | [Dayton Audio RS125P — 4.0 Ω](https://www.daytonaudio.com/product/1223/rs125p-4-5-reference-paper-woofer-4-ohm) | 4 Ω table: Bl 5.08 → 3.49 Tm; Sd 52.8 → 54.11 cm². |
| `444bf4aabda9e85f1d05b424` | [Faital Pro 15FH520 — 4.0 Ω](https://faitalpro.com/en/products/LF_Loudspeakers/product_details/index.php?id=151060152) | 4 Ω Cms 0.19 → 0.15 mm/N and Rms 2.4 → 3 kg/s; add published Mmd 86.8 g. |
| `8b294dbd1314aba046977cea` | [SB Acoustics MW19PNW — 4.0 Ω](https://sbacoustics.com/product/7%C2%BDin-satori-mw19pnw-4/) | Current 4 Ω set: Fs 32 → 29 Hz, Mms 16.7 → 17.4 g, Qes 0.22 → 0.26; fill Vas/Cms/Rms. |
| `043448a93fbfdbe61384513f` | [SB Acoustics SB13PFCR25 — 8.0 Ω](https://sbacoustics.com/product/5-sb13pfcr25-8-paper/) | 8 Ω Re 3.2 → 5.6 Ω. |
| `8db2e932d631fe4e9232818b` | [SB Acoustics SB15BAC30-8-COAX — 8 Ω](https://sbacoustics.com/product/sb15bac30-8-coax/) | Woofer Re 3.8 → 5.7 Ω; Xmax 6 → 3 mm from published peak-to-peak travel. |
| `20d3f116134c706ce91d02ae` | [Tang Band W3-2141 — 8 Ω](https://www.tb-speaker.com/products/w3-2141) | Mms 1996 → 1.996 g; Xmax 0.5 → 1.6 mm; rated power 25 → 12 W. |
| `625b0327e6a96145d62c7043` | [Visaton AL 170 — 8 Ω](https://www.visaton.de/en/products/drivers/woofers/al-170-8-ohm) | Vas 1.132673864 → 34 L; linear-Xmax interpretation remains unverified. |
| `a2710d4f371c548c2e4c47dc` | [Visaton FR 8 WP-4 — 4 Ω](https://www.visaton.de/en/products/drivers/fullrange-systems/fr-8-wp-4-ohm-white) | Vas 11.6099071 → 0.4 L; nominal size 3 → 3.3 inches. |
| `1558ea22c8fd10936dd3fdf3` | [Visaton FRS8 — 8.0 Ω](https://www.visaton.de/en/products/drivers/fullrange-systems/frs-8-8-ohm) | 8 Ω variant: Re 3.5 → 7.2 Ω, Le 0.4 → 0.9 mH, Fs 115 → 120 Hz, Qes 0.85 → 1.32. |
| `270cfddd38fdbe0377632a0d` | [Visaton GF200 — 2 Ω](https://www.visaton.de/en/products/drivers/woofers/gf-200-2-x-4-ohm) | Parallel-coil 2 Ω set: Fs 30 → 35 Hz, Qms 4.12 → 4.33, Qes 0.37 → 0.44, Qts 0.77 → 0.39. |
| `4c6a07e540c99362f4c289e4` | [Visaton W130X — 4 Ω](https://www.visaton.de/en/products/drivers/woofers/w-130-x-2-x-4-ohm) | Single-coil 4 Ω set: Re 2 → 3.8 Ω, Le 0.69 → 0.77 mH, Qes 0.41 → 0.86; published linear Xmax 5.8 mm. |
| `b1a65b78dac3073affd82e7d` | [Wavecor SW215WA01 — 4 Ω](https://www.wavecor.com/html/sw215wa01_02.html) | Restore the before-burn-in set: Cms 0.54 → 0.43 mm/N; Vas 26 L; nominal size 9 → 8.5 inches. |
| `a41dda7db1f3bc71bcd964c8` | [Wavecor WF168WA02 — 8 Ω](https://www.wavecor.com/html/wf168wa01_02.html) | 8 Ω column: Bl 5.1 → 6.4 Tm, Fs 47.5 → 49 Hz, Qes 0.46 → 0.56, Qts 0.43 → 0.52. |

## Patterns and unresolved evidence

**Mixed electrical or measurement conditions are a recurring problem.** The GF200 and W130X records mix single-coil, series and parallel columns. FRS8 and WF168WA02 mix impedance variants. The SB15 coaxial record uses tweeter resistance in a woofer record. SW215WA01 mixes before- and after-burn-in parameters. These are distinct cases; there is no blanket manufacturer-wide repair.

**The requested source-conflict rule was applied conservatively.** All 20 `SOURCE_INCONSISTENT` rows have blank correction cells, including apparently obvious library errors where the current source also conflicts. For example, Truvox 0615 has an identifiable Bl copy error, but the source Vas/compliance/area set is inconsistent. CE53N and ND140 have disagreement between current linked PDFs and web tables. CE45N has consistent detailed 5 W data but an unexplained 8 W product heading; its candidate Vas correction is withheld with the conflict.

**Several Visaton sources reproduce, rather than resolve, the Qes contradiction.** The comparison does not identify which published parameter is wrong. Calculated values are diagnostic only and are never written into `corrected_*`. This does not establish that every >20% discrepancy has the same cause.

**Linear excursion and mechanical limits are not interchangeable.** SB15 peak-to-peak linear travel is divided by two; W130X explicitly publishes a one-way linear excursion at 10% THD. Other Visaton pages state an excursion limit without separately verifying the imported linear Xmax. AL 170, FR 8 WP-4, FRS8 8 Ω and GF200 2 Ω retain that uncertainty. WF168WA02 also lacks a separately published Xmax. Passing the numerical screens does not certify those retained values for output-limit calculations.

**Unpublished original values are retained, not endorsed.** The 960-line field comparison marks every original parameter as matching, differing, missing, unavailable or unverified for its field definition. In particular, retained compliance values absent from the accessed table are not represented as independently measured or newly verified.

**Source access limits:** no usable exact-variant SEAS FU10RB H1600-08 technical data were retrievable. Visaton MR 130 has an accessible product sheet, but not the full T/S evidence required to resolve its failure. The combined FR 8 WP sheet does not provide an 8-ohm T/S set; values from its 4-ohm sibling are not repurposed.

## Numerical and file validation

- The delivered batch retains all **71 columns**, original string formatting in immutable cells, and all **40 original row IDs**. Only permitted editable fields change. All `hold_resolution` cells are blank because this is Group A, not the hold batch.
- Each researched row has exactly one provenance row. Manufacturer URLs occur in the provenance and supporting evidence, never in `correction_source`.
- The independent numerical preview reproduces all original failing-check sets and populated numerical diagnostics for these 40 rows, with zero mismatches at the documented floating-point comparison tolerance. All **17 proposed rows** pass the reproduced Fs/Qes/Vas/Qts and 50% mass-correction screens.
- This preview uses rho = 1.2041 kg/m³ and c = 343 m/s, matching the supplied snapshot diagnostics. Compliance precedence is Cms, then Vas, then Fs. Qes uses stated Fs when present; this was checked against the supplied diagnostics rather than silently changing them. A generic rho = 1.2 comparison is used only in the separate air-mass note.
- The official correction script, solver environment and repository tests were **not run**. The preview is not a substitute for official validation, and it does not resolve source holds or certify every retained optional field.

## Package files

| File | Purpose |
|---|---|
| `driver-library-review-batch-01.csv` | The requested 40-row correction sheet, all 71 columns. |
| `driver-library-provenance.csv` | One source record per researched row in the requested schema. |
| `driver-library-change-log.csv` | All 101 changes, including source references. |
| `driver-library-field-comparison.csv` | Every physical cell compared with available source evidence. |
| `driver-library-source-details.csv` | Supplementary manufacturer references and measurement conventions. |
| `driver-library-numerical-preview.csv` | Original, proposed, and source-only numerical diagnostics; never input replacements. |
| `source-catalog.json` | Normalized published facts and research notes used in this batch. |
| `validation-summary.json` | Preservation assertions, counts and input checksums. |
| `driver-library-review-remaining-208.csv` | Unresearched queue, with its original rows and columns unchanged. |
| `air-mass-note.md` / `.pdf` | Task 4, a one-page source-grounded note; no driver-data changes. |

## Official validation, not performed here

Archive this completed batch before regeneration. From the matching repository and pinned environment, validate the 40-row batch first:

```sh
python scripts/apply_driver_corrections.py path/to/driver-library-review-batch-01.csv
```

Only after successful validation should the documented `--apply` workflow be used. The remaining-row file and evidence/preview sidecars are not additional correction batches.
