# CAD Link: customizable crossovers, delay alignment, driver presets

Status: approved design, implementation in slices (2026-08-23).

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
in phase sum to 0 dB; LR2/BW2 need one side inverted; BW1/BW3 sum flat with
a 90° offset; BW4 sums +3 dB at fc; BE4 dips ≈ −2.8 dB at fc.

### Gains

Auto gain is today's rule: median filtered on-axis SPL per member in its
passband, target = median of members, gain = target − median. Manual gain
is applied verbatim. Mixed modes: compute the auto set, then override the
manual channels.

### Alignment

The legacy rule (filtered outputs 0° apart at fc) is correct only for LR4
and LR8. The general rule aligns the *raw* drivers so the chosen filter pair
sums the way its textbook says:

1. Overlap region per adjacent pair: weight `w(f) = |LP_lower(f)|·|HP_upper(f)|`
   after gains; keep points with `w > −40 dB` of its maximum.
2. Coarse pass: in a ±1/3-octave window around the pair's crossover
   (where unwrapping is safe) fit the unwrapped phase of
   `P_lower / P_upper` (raw, on-axis, gains applied) against `2πf` by weighted
   least squares. The slope is the coarse delay.
3. Refine: remove the coarse delay, unwrap over the whole overlap region,
   refit. Slope correction gives the pair delay; the intercept is the raw
   polarity relationship (reported as a warning when near 180°).
4. Chain pair delays from the top member down as today; pin the `reference`
   channel at 0 and shift the auto channels; manual channels keep their
   value verbatim.
5. Report per pair the weighted RMS fit residual (confidence), the residual
   phase error at fc after alignment, and the reverse-null depth (sum with
   the upper member inverted, minimum over the overlap region, relative to
   the sum).

Regression: LR4 pairs must reproduce the legacy delays within 0.01 ms on the
existing `test_combine.py` fixtures. A fit residual above 30° or fewer than
three points in the coarse window produces a warning, never a silent value.

### Polarity

Auto polarity of member k+1 relative to member k: invert when
`|LP(fc) − HP(fc)| > |LP(fc) + HP(fc)|` for the ideal filter pair at the
pair's crossover. Polarities accumulate along the chain from the lowest
member. An explicit `invert` overrides that channel.

### Result payload

`metadata.combine`:

```
type: "filtered_time_aligned_sum"
members, member_roles, reference
crossovers_hz            # per linked pair, null when unlinked
channels: {id: {hp, lp, gain_db, gain_mode, gain_auto_db,
                delay_ms, delay_mode, delay_auto_ms, inverted, invert_mode}}
pairs: {"lf-mf": {eval_hz, fit_residual_deg, phase_error_at_fc_deg,
                  reverse_null_db, points}}
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
