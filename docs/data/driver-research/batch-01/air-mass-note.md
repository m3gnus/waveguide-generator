# Task 4 — air mass included in published Mms

2026-10-06 · Source research only; no driver-data changes.

**What is included.** Klippel defines Mms as the moving assembly including air load. Its LPM free-air workflow and its Mmd(Sd) calculation must be distinguished: the reported dry-mass estimate is obtained using Mmd = Mms - 1.13 Sd^(3/2), not by weighing a disassembled diaphragm. The same convention appears in its laser and added-mass algorithms. [1]

**Free air is not the same measurement as an SPL baffle test.** Wavecor explicitly labels Mms as including air in free air, with no baffle. On the same sheet, the sensitivity/SPL measurement uses an infinite baffle; IEC 268-5 is attached to the power ratings, with those tests described as free-air/no-cabinet. Thus an IEC or baffle reference elsewhere in a sheet is not evidence that its Mms was obtained in an IEC baffle. [2]

**How large is the difference?.** At rho = 1.2 kg/m3, the stated HornLab expression equals 1.14936 Sd^(3/2), with mass in kg and area in m2. Relative to Klippel's 1.13 convention it subtracts about 1.71% more air mass, so its inferred Mmd is slightly lower, not lower by a factor of two. This is a comparison of conventions, not proof of the actual acoustic loading of every driver. [1; calculation]

**A published cross-check.** For the FaitalPRO 15FH520 4-ohm table, Mms = 114.7 g, Mmd = 86.8 g and Sd = 847 cm2. The published difference is 27.9 g; the stated HornLab formula gives 28.33 g and the Klippel convention 27.86 g. HornLab therefore infers 86.37 g here, about 0.43 g below the published Mmd. The table does not establish whether its Mmd was separately measured or conventionally derived. [3; calculation]

**Recommendation and limit.** Do not halve the subtraction globally based only on the words free air. Preserve published Mms and separately published Mmd, and record the fixture, conditioning and derivation convention. For sheets without a separate Mmd or documented convention, these sources do not quantify the true over- or under-subtraction. Validate fixture-specific loading separately; keep the 30% warning and 50% refusal identified as policies, not physical accuracy certificates. No driver value is changed by this note.

[1] [Klippel LPM manual: parameter definitions, measurement methods, Algorithms and Equations](https://docs.klippel.de/db-lab/latest/transducer-parameter-identification/lpm/lpm.html). Accessed 2026-10-06.
[2] [Wavecor SW215WA01/02: nominal specifications and measurement notes; updated 2025-03-12](https://www.wavecor.com/html/sw215wa01_02.html). Accessed 2026-10-06.
[3] [FaitalPRO 15FH520, 4-ohm: Thiele and Small table, P/N 03804183](https://faitalpro.com/en/products/LF_Loudspeakers/product_details/index.php?id=151060152). Accessed 2026-10-06.
