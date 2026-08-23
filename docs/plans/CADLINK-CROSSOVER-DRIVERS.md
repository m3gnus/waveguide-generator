# CAD Link: customizable crossovers, delay alignment, driver presets

Status: implemented on `feature/cadlink-crossover-drivers` (2026-08-23); slice 6 open.

Multi-channel CAD Link runs combine their drive channels into one summed
response. Today that combine is a fixed LR4 chain with automatic level match
and automatic alignment. This plan generalises it and adds a driver library
so a drive channel's Thiele/Small parameters can be picked instead of typed.

## 1. Principles

- One spec drives the pre-solve rail, the post-solve results strip, and the
  exports. Editing in the strip writes back to the rail.
- Simple by default: a pair shares one frequency and one slope until the user
  opens *Advanced*, where each channel owns its high-pass, low-pass, gain,
  delay and polarity.
- Auto is a value you can see and take over. Auto gain and delay show the
  number they chose; Manual starts from that number; *Reset to auto* is one
  action.
- Every change repaints from stored bases (`server/solver/recombine.py`);
  nothing re-solves.

## 2. Crossover contract

`ChannelCombineSpec` (`server/jobs/models.py`) keeps its legacy fields and
adds a per-channel form. A spec with only the legacy fields expands to the
per-channel form as LR4 pairs with auto gain and auto delay, so existing
clients, stored solve profiles and the OpenAPI snapshot stay valid.

```json
{
  "id": "combined",
  "members": ["lf", "mf", "hf"],
  "reference": "hf",
  "channels": {
    "lf": { "hp": null,
            "lp": { "family": "lr", "order": 4, "fc_hz": 100 },
            "gain": { "mode": "auto" },
            "delay": { "mode": "auto" },
            "invert": null },
    "mf": { "hp": { "family": "lr", "order": 4, "fc_hz": 100 },
            "lp": { "family": "butterworth", "order": 3, "fc_hz": 900 },
            "gain": { "mode": "manual", "db": 0.0 },
            "delay": { "mode": "manual", "ms": 0.45 },
            "invert": false },
    "hf": { "hp": { "family": "lr", "order": 4, "fc_hz": 1100 },
            "lp": null,
            "gain": { "mode": "auto" },
            "delay": { "mode": "auto" },
            "invert": null }
  }
}
```

- `members`: chain in band order, lowest first (unchanged).
- `reference`: the channel pinned at 0 ms by auto alignment. Default: the
  last member (highest band). Must be a member.
- `channels`: one entry per member. `hp`/`lp` are `null` (off) or
  `{family, order, fc_hz}`. `gain` is `{mode: "auto"}` or
  `{mode: "manual", db}`. `delay` is `{mode: "auto"}` or
  `{mode: "manual", ms}` (negative allowed). `invert` is `null` (auto from
  the filter pair), `true` or `false`.
- Legacy `crossovers_hz` / `level_match` / `align` remain accepted. When
  `channels` is present they are ignored on input; the response still reports
  `crossovers_hz` for linked pairs.
- Families and orders: `lr` 2/4/6/8, `butterworth` 1–8, `bessel` 2/3/4,
  `linear_phase` 2/4/8 (LR magnitude, zero phase).
- Validation at submission: `channels` keys equal `members`; a channel's
  `hp.fc_hz` must be below its `lp.fc_hz`; every `fc_hz` must lie inside the
  solved band (extends `SolveRequest.validate_combine_band`); `reference`
  must be a member. Solve-time observations stay warnings.

### Filters

`server/solver/filters.py` evaluates each family in the engineering
`e^{+jωt}` convention with `s = j f / fc`:

- Butterworth order n: poles `exp(jπ(2k+n−1)/(2n))`, k = 1..n, H_lp = Π 1/(s−p_k).
- Linkwitz-Riley order n (even): H_lp = Butterworth_{n/2}².
- Bessel order n: analog Bessel low-pass normalised to −3 dB at fc
  (`scipy.signal.bessel(..., analog=True, norm="mag")`).
- Linear phase order n: |LR_n| with zero phase.
- High-pass of any family: H_hp(s) = H_lp(1/s).

Textbook sums with ideal coincident drivers are the test oracle: LR4/LR8
in phase sum to 0 dB; LR2 sums flat with one side inverted; BW2 nulls in
phase and sums +3 dB at fc inverted; BW1/BW3 sum flat with a 90° offset;
BW4 sums +3 dB at fc; BE4 dips ≈ −2.8 dB at fc in phase.

### Gains

Auto gain is today's rule: median filtered on-axis SPL per member in its
passband, target = median of members, gain = target − median. Manual gain
is applied verbatim. Mixed modes: compute the auto set, then override the
manual channels.

### Alignment

The legacy rule (filtered outputs 0° apart at fc) is correct only for LR4
and LR8. The general rule aligns the *raw* drivers so the chosen filter
chains sum the way their textbook says:

1. Overlap region per adjacent pair: weight `w(f) = |lower(f)|·|upper(f)|`
   of the filtered, level-matched channel responses; keep points with
   `w > −40 dB` of its maximum.
2. Coarse pass: in a ±1/3-octave window around the pair's crossover
   (where unwrapping is safe) fit the unwrapped phase of
   `P_lower / P_upper` (raw, on-axis) against `2πf` by weighted least
   squares. The slope is the coarse delay; with fewer than three points the
   whole overlap is used and a warning says the cycle may be wrong.
3. Refine: remove the coarse delay, unwrap over the whole overlap region,
   refit. The slope correction gives the fitted delay; the intercept is the
   raw polarity relationship; the weighted RMS residual is the confidence.
4. Pin: the fit settles the period branch only. The pair delay brings the
   raw ratio's phase *at the crossover* to its target (0 for a like-polarity
   pair, π when the applied polarity flips the raw pair) on the branch
   nearest the fitted delay. Measured on a three-way CAD return, the
   least-squares line alone left 68° at 1 kHz and a −8 dB reverse null where
   the pinned delay gives −15 dB: a real horn's raw ratio is rarely a pure
   delay, and the crossover sums at fc, not over the wings.
5. Chain pair delays from the top member down; pin the `reference` channel
   at 0 and shift the auto channels; manual channels keep their value
   verbatim.
6. Report per pair `fit_delay_ms`, `fit_residual_deg`, the residual phase
   error at fc against each channel's *full* filter chain (a middle band's
   other section rotates phase at the lower crossover by design, and is not
   error), the reverse-null depth, and the point count.

Regression: for a two-way LR4 pair the legacy and new delays agree to
numerical tolerance. For a three-way chain they differ deliberately: the
legacy rule absorbed the middle band's other section into the delay
(0.45 ms at 100 Hz on a CAD return), the new rule leaves coincident drivers
coincident. A residual above 30° produces a warning, never a silent value.

### Polarity

Two things decide a pair's relative polarity: the ideal filter pair
(invert when `|LP(fc) − HP(fc)| > |LP(fc) + HP(fc)|`: LR2, BW2, and by the
same rule Bessel), and the raw drivers (intercept closer to 180° than to 0°:
a source returned with opposite motion sign, or ports and a throat that sit
opposed at the crossover). Auto applies both, accumulating along the chain
from the lowest member, so an opposed pair is inverted and aligned on its
true delay. An explicit `invert` overrides that channel; the delay target
follows the polarity actually applied, so a pair the user keeps in polarity
is aligned with a half-period delay and a warning says so.

### Result payload

`metadata.combine`:

```
type: "filtered_time_aligned_sum"
members, member_roles, reference
crossovers_hz            # per linked pair, null when unlinked
channels: {id: {hp, lp, gain_db, gain_mode, gain_auto_db,
                delay_ms, delay_mode, delay_auto_ms, inverted, invert_mode}}
pairs: {"lf-mf": {eval_hz, fit_delay_ms, fit_residual_deg,
                  phase_error_at_fc_deg, reverse_null_db, points}}
delays_ms, gains_db      # flattened aliases kept for one release
level_match: {enabled, target_db, medians_db, gains_db}   # legacy shape
align: bool
warnings: [...]
```

## 3. Rail and strip UI

- Simulation tab → *Crossover* (existing section, same position): per
  pair a Hz field, a family selector and a slope selector; *Levels* and
  *Delay* as Auto/Manual segments; *Advanced ▸* opens the per-channel
  popover (HP, LP, Gain, Delay, Invert per channel; reference selector;
  *Relink pairs*). Dashed fields show auto values; manual fields show the
  auto value beside them with *Reset to auto*.
- Results dock: the recombine strip carries the same controls, the
  computed delays and the reverse-null depth as chips, and *Apply*. Applied
  values write back to the rail (widen `setCombineCrossoversFromResult`).
  The strip wraps to two rows when the dock is narrow.
- SPL card: optional reverse-null overlay (results preference *Show reverse
  null*, off by default), drawn under the Combined view only.
- Summary card: Combine group adds gains, the per-pair phase/null readout,
  and the driver names.
- VituixCAD export: `Shape` = Linkwitz-Riley / Butterworth / Bessel,
  `Order` = order, one active block per enabled HP/LP, buffer gain and delay
  from the resolved values, `Inverted` from `inverted`. Linear phase exports
  as LR of the same order with a note.
- A driver's recommended minimum crossover (from its preset) shows as an
  amber chip on the pair it concerns.

## 4. Driver library

Waveguide Generator ships no driver data. It reads CSV files from a
per-user library folder and exposes a search API.

- Folder: the application-support directory (`HornLab/driver-databases`)
  resolved per platform the way the app's other data directories are.
  Settings shows the folder, the file and driver counts, *Reveal* and
  *Rescan*. An empty library shows a hint and the manual T/S form.
- CSV columns are matched case-insensitively through an alias table:
  `Brand`, `Model`, `Z_ohm|Impedance_ohm`, `Size_in`, `Throat_in`,
  `Sd_cm2|Sd|Sd_cm^2`, `Bl_Tm|Bl`, `Re_ohm|Re`, `Le_mH|Le`, `Mms_g|Mms`,
  `Mmd_g`, `Fs_Hz|Fs`, `Vas_L|Vas`, `Qms`, `Qes`, `Qts`,
  `Cms_mm_per_N|Cms_mmN`, `Rms_kg_per_s`, `Xmax_mm|Xmax`,
  `Sensitivity_dB`, `Power_W|Power_AES_W`, `XO_min_Hz`, `Freq_low_Hz`,
  `Price_avg_EUR|Price_EUR`, `Source_URL|URL`. Unknown columns are kept
  as opaque extras. Never invent a value for a missing column.
- `GET /api/drivers?q=&kind=lf|cd|all&z=&limit=` — ranked by token prefix
  match on brand + model, then impedance match, then completeness. Each hit:
  `id` (`Brand::Model::Z`), `brand`, `model`, `z_ohm`, `kind`, `size`,
  `completeness` (`full` when Sd, Bl, Re, one mass and one compliance source
  are present; `partial`; `catalogue`), `spec` (the `DriverSpec`-ready
  fields it can fill), `display` (Fs, Sd, Bl, Xmax, sensitivity, price),
  `xo_min_hz`, `source`.
- `GET /api/drivers/{id}` — the full record with provenance.
- `GET /api/drivers/library` — folder, files, counts; `POST .../rescan`.
- Variants: rows that differ only in impedance group into one driver with
  a variant list.
- User-saved drivers live in a `driverLibrary` durable-settings namespace
  with `based_on` (library id or `manual`) and the overridden fields.

## 5. Driver picker UI

- *Drive channels & drivers*: each channel card keeps its toggle; the
  field grid is replaced by an inline search (results drop down under the
  field; ↑↓ ↵ Esc; the first hit pre-selected; `kind` defaults to `cd` for
  HF and `lf` otherwise, with a toggle). A picked driver collapses to a
  name chip, four key numbers and *Edit T/S…*. Catalogue-only drivers say
  so and fall back to manual fields.
- *Edit T/S* sheet: datasheet inputs (Sd, Bl, Re, Le, Mms, Fs, Vas, Qms,
  Xmax, Count, Rear volume); derived read-only Cms, Qes, Qts and 1 W
  sensitivity; edited fields outlined and counted in the header; impedance
  variant segment; *Reset to database values*; *Save to My drivers*. Count
  and rear volume are WG-only and never count as edits.
- The form's field keys widen to the API's alternatives (`mms_g`, `fs_hz`,
  `vas_l`, `qms`); `mmd_g` / `cms_m_per_n` stay for hand-entered drivers.
  Completeness mirrors the server rule (one mass, one compliance source).
- `DriverSpec` gains an optional `label` so results and exports can name
  the driver.

## 6. Slices

1. Filter library + spec v2 + resolved payload (server).
2. Alignment v2 + polarity + pair metrics (server).
3. Rail + strip + summary + reverse-null + VituixCAD mapping (frontend).
4. Driver library folder, loader, search API, settings namespace (server).
5. Driver picker + T/S sheet + XO-minimum chip (frontend).
6. Later: *Optimise* (flattest sum) button; add-in parity.
