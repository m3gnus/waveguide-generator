# Manufacturer import, 2026-11-08

1,318 windings were added to the library on 2026-11-08. Every value was read from
the manufacturer's own product page, the data service that page loads, or the
datasheet PDF it links, on that date. `driver-library-provenance.csv` gives the
source URL of each row (evidence `driver-research/import-261108`).

## Which drivers

The current online catalogues of 46 manufacturers were requested. 32 could be
read in full, giving 3,822 driver windings; the others are listed below. A winding was added when its source states Fs, Re, Bl, Sd, Mms
(or Mmd) and Qes or Qts, and the library did not already hold it. It was left
out when:

- the library already holds the same brand, model and impedance, including rows
  filed under another spelling of the brand ("EPIQUE by Dayton Audio", "Sica",
  "PD") or with a blank impedance;
- the source publishes no full T/S set. This is most compression drivers,
  tweeters and guitar speakers, all of HiVi (no Sd published), all of DAS Audio,
  and most JBL Professional components;
- the stated Re does not fit the stated impedance, which means the page shows
  another winding's data;
- no impedance is stated;
- Monacor resells it under a Celestion model name (Celestion's own data is the
  source);
- the manufacturer marks it as a prototype;
- it is an SB Acoustics dual-coil woofer published per coil (the library stores
  dual coils at series impedance).

| Brand | Rows | Source |
|---|---:|---|
| SB Acoustics | 142 | product page |
| Wavecor | 139 | product page, datasheet PDF |
| Ciare | 126 | product page |
| Tang Band | 108 | product page |
| Jensen | 105 | product page |
| PHL Audio | 75 | datasheet PDF |
| Peerless by Tymphany | 66 | Tymphany product data service |
| Celestion | 63 | product page |
| Visaton | 56 | product page |
| Eminence | 53 | product page |
| Dayton Audio | 50 | product page |
| RCF | 42 | product page |
| Monacor | 41 | Monacor web shop product page |
| Faital Pro | 34 | product page |
| PURIFI | 31 | product page, datasheet PDF |
| Beyma | 24 | product page |
| Markaudio | 24 | product page |
| SICA | 21 | product page |
| B&C | 20 | product page data |
| BMS | 19 | product page |
| 18Sound | 16 | product page |
| Audiotechnology | 15 | datasheet PDF |
| SB Audience | 9 | datasheet PDF |
| Radian Audio | 9 | datasheet PDF |
| Supravox | 9 | product page |
| Fane | 7 | product page |
| Volt | 7 | product page |
| HOQS | 4 | product page |
| Davis | 3 | product page, datasheet PDF |

## Catalogues that could not be read

- LaVoce, Precision Devices and P.Audio answer every request with a CAPTCHA
  challenge; PRV Audio and Scan-Speak with a browser check; Morel's product data
  sits behind a CAPTCHA.
- SEAS and Fostex refuse the connection.
- Oberton's site did not answer.
- Audax, ATC, Electro-Voice and Selenium publish no driver catalogue; Fertin's
  domain does not resolve.

## Conventions applied

- **Units** follow the library columns (see `../driver-library-review.md`).
  Published kg, m², m³, ft³, ml, µm/N, µH and mg are converted. Decimal commas
  are read as decimals (Monacor, Visaton, Supravox); thousands separators are
  recognised.
- **Impedance** is the manufacturer's nominal impedance for that winding. Where
  a page offers several impedances with one data set, the impedance the stated
  Re belongs to is used.
- **Dual voice coils** are listed at series impedance, as elsewhere in the
  library, when the stated Re is the series value.
- **Sensitivity** is 1 W / 1 m. A figure published at a stated voltage V is
  converted with 10·log10(V²/Z). A figure with no stated drive level is kept only
  for 8 Ω windings, where 2.83 V and 1 W coincide.
- **Power** is the nominal (AES, IEC or RMS) rating, never program, music, peak
  or maximum power.
- **Xmax** is one-way. SB Acoustics and Davis publish linear travel peak to
  peak; half of it is used. PURIFI's "Xlin" is used as Xmax.
- **Values a source contradicts** are left out rather than corrected, for
  example a Cms unit that does not fit its own Fs and Mms. The few exceptions
  are recorded in the crawl notes. A published 0 means not stated.
- **Size** comes from the product URL, page or model name. Dome tweeters
  without an inch size are left empty.

## Screening

The rows were screened with the same checks as the rest of the library
(coupling, Fs, Qes, Vas, Qts; 20% tolerance). 81 are marked unreliable and
listed in `driver-library-review.csv`. The failures checked by hand against
their sources are the manufacturer's own published numbers, for example SICA
10 D 1.5 CS Fs "682.0 Hz" (68 Hz by its own Mms and Cms), Tang Band W6-1559S Sd
"0.0014 M²" on a 6.5-inch driver, Jensen Mod 10-50 Vas "2078 lt.", Beyma
18QLEX1600Fe Mms "0.036 kg", and Visaton full-range drivers whose Mms and Bl are
published to one significant figure. They are kept as published, like the other
unreliable rows.

A random sample of two rows per brand (55 rows) was re-fetched from the
manufacturer. 54 sources answered, and every recorded value was found in them
after unit conversion; one page did not answer the re-fetch.
