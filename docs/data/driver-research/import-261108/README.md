# Manufacturer import, 2026-11-08

502 windings were added to the library on 2026-11-08. Every value was read from
the manufacturer's own product page, product data service or datasheet PDF on
that date. `driver-library-provenance.csv` gives the source URL of each row
(evidence `driver-research/import-261108`).

## Which drivers

Candidates were current models from 15 manufacturers that the library did not
yet hold. A candidate was added only when its manufacturer source states Fs,
Re, Bl, Mms, Sd and Qes or Qts. Candidates were left out when:

- the library already holds the same brand, model and impedance (27, found
  once the impedance and brand were read from the manufacturer page, including
  library rows filed under "EPIQUE by Dayton Audio" or with a blank impedance);
- they are Celestion drivers that Monacor resells under Celestion's model
  names (3); Celestion's own data is the source for those;
- the page publishes no full T/S set (8);
- the page's Re does not fit the impedance, meaning the page shows another
  winding's data (6);
- they repeat another candidate (10).

| Brand | Rows | Source |
|---|---:|---|
| Ciare | 101 | product page |
| Jensen | 97 | product page |
| SB Acoustics | 65 | product page |
| Celestion | 55 | product page |
| Peerless by Tymphany | 55 | Tymphany product data service |
| Monacor | 31 | Monacor web shop product page |
| RCF | 25 | product page |
| Dayton Audio | 18 | product page |
| Beyma | 17 | product page |
| 18Sound | 14 | product page |
| SB Audience | 9 | datasheet PDF |
| Faital Pro | 8 | product page |
| HOQS | 3 | product page |
| B&C | 2 | product page |
| Eminence | 2 | product page |

## Conventions applied

- **Units** follow the library columns (see `../driver-library-review.md`).
  Published m², kg, m³, ft³, µm/N and µH are converted. Monacor writes decimal
  commas (`4,372` is 4.372); its `1,210 cm²` groups thousands.
- **Impedance** is the manufacturer's: the product URL path for 18Sound, Ciare
  and B&C, the value column for Jensen's multi-impedance tables, the data
  service for Peerless, the SKU for Monacor, and the page's nominal impedance
  otherwise.
- **Dual voice coils** are listed at series impedance, as elsewhere in the
  library: a "2 Ω + 2 Ω" driver whose stated Re is the series value is a 4 Ω row.
- **Sensitivity** is 1 W / 1 m. Ciare, Dayton Audio, B&C and SB Acoustics
  publish 2.83 V / 1 m; those values are converted with 10·log10(8/Z). Peerless
  states no drive level, so its sensitivity is left empty.
- **Power** is the manufacturer's nominal (AES/RMS) rating, never program or
  peak power.
- **A published 0** (for example Xmax "0 mm") means not stated and is left
  empty.
- **Size** comes from the product URL, page or model name. Dome tweeters
  without an inch size are left empty.

## Screening

The rows were screened with the same checks as the rest of the library
(coupling, Fs, Qes, Vas, Qts; 20% tolerance). 37 are marked unreliable and
listed in `driver-library-review.csv`. The failures that were checked by hand
against their source pages are the manufacturer's own published numbers, for
example Jensen Mod 10-50 Vas "2078 lt." beside 20.8 lt. for its other winding,
Beyma 18QLEX1600Fe Mms "0.036 kg" and Celestion BL15-300X Mms "14.15 g". They
are kept as published, like the other unreliable rows.
