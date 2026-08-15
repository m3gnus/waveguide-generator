# Waveguide Generator text configuration format

Status: canonical file-format contract, verified against `server/design/` on
2026-08-13.

The conventional suffix is `.cfg` or `.txt`. Legacy `.mwg` files use the same
ATH-style grammar and remain readable.

## Dialects and lexical grammar

Files are Unicode text with LF or CRLF line endings. Content is classified as the
Waveguide Generator dialect when it contains a case-insensitive `; Parameter config`
or `; MWG config` comment; classification does not depend on the suffix. Canonical
output starts with:

```cfg
; Parameter config
; Waveguide Generator design-format: 2
```

```text
document       = { blank | comment | assignment | block } ;
assignment     = whitespace, name, whitespace, "=", raw-value,
                 [ inline-comment ], EOL ;
block          = whitespace, name, whitespace, "=", whitespace, "{", EOL,
                 { blank | comment | assignment | raw-row },
                 whitespace, "}", [ inline-comment ], EOL ;
inline-comment = whitespace, ";", text ;
```

An assignment splits on its first `=`. Blocks do not nest. Unknown top-level
assignments and blocks retain their spelling, order, raw rows, and comments. Historical
multiline `function anonymous(` expressions are consumed through their balanced closing
brace so their internal lines cannot become false keys.

## Expressions and families

Every numeric schema value is represented internally as an `Expr` with a finite scalar
`value` when one can be resolved and the original `raw` spelling. Serialization prefers
the raw spelling. Parsing never executes arbitrary code; angular expressions involving
ATH's `p` variable remain raw expressions without an invented scalar value.

The root design is discriminated as `OSSE`, `R-OSSE`, `ICW`, or `FREEFORM`. Common
sections cover scale, throat, morphing, mesh/sizing, sampling, enclosure, source,
frequency, and simulation options. `MORPH`, `Mesh`, `Source`, and `Simulation` blocks
are accepted alongside their flat dotted spellings.

## Directivity / ATH polar blocks

Portable directivity settings use the same blocks as v1 and ATH:

```cfg
ABEC.Polars:SPL_H = {
MapAngleRange = 0,180,37
NormAngle = 5
Distance = 2
}
ABEC.Polars:SPL_V = {
MapAngleRange = 0,180,37
NormAngle = 5
Distance = 2
Inclination = 90
}
ABEC.Polars:SPL_D = {
MapAngleRange = 0,180,37
NormAngle = 5
Distance = 2
Inclination = 45
}
```

`MapAngleRange` is `startDeg,endDeg,sampleCount`. A block without `Inclination`
is horizontal; inclinations equivalent to 90 degrees are vertical; every other
inclination is diagonal. Enabled planes are represented by which blocks exist.
Opening a file restores these settings, and saving or exporting a run replaces
only the managed `ABEC.Polars:*` blocks while retaining `Report` and unrelated
ATH passthrough blocks.

ATH has no portable fields for WG's measurement-origin choice or for retaining
the full spherical balloon grid. Those remain solve options and are deliberately
not written as invented ATH keys.

## FREEFORM: disk representation versus typed model

The text format remains compatible with the physical-axis form:

```cfg
Freeform.Length = 120
Freeform.ThroatRadius = 12.7
Freeform.ThroatAngle = 15.5
Freeform.InflectionPolicy = warn
Freeform.H = {
MouthRadius = 140
MouthAngle = 60
}
Freeform.H.Points = {
40 35
80 80 50
}
Freeform.CrossSections = {
0 ellipse
0.5 superellipse 4
1 rounded_rectangle 10
}
```

`Freeform.V` and `.V.Points` have the same form. Point rows are interior anchors in
`z_mm r_mm [angleDeg]`; throat and mouth endpoints are implicit. Cross-section rows are
`t shape [exponent|cornerRadiusMm]`, where `t` is in `[0,1]` and shape is `ellipse`,
`superellipse`, or `rounded_rectangle`.

After parsing/migration, the typed API stores both complete meridians as
`{t, r, angle_deg?}` with a shared top-level length. Each profile begins at `t=0`, ends
at `t=1`, and is strictly increasing. The first cross-section is `0 ellipse`; the last
is at `t=1`.

The reader accepts retired tangent scales, per-anchor strength, overshoot policy, and
`circle` stations only long enough to migrate old files. The canonical writer never
emits them: tangent speed is solved automatically and `ellipse` represents the round
endpoint.

## Ordered migrations

Migrations run before validation and are independently reported:

1. `001_corner_ratio_to_corner_grid` converts legacy fractional FREEFORM corner ratios
   to absolute millimetres using the local H/V radii.
2. `002_inflection_allow_to_warn` renames the retired `allow` alias.
3. `003_js_undefined_lines_dropped` removes `undefined`, `NaN`, and `null` assignments
   only at known numeric-schema paths so ordinary unknown text is preserved.
4. `004_freeform_solved_tangent_contract` removes tangent scales, per-anchor strength,
   and overshoot policy, and converts `circle` stations to `ellipse`.
5. `005_freeform_normalized_axis` converts legacy absolute-z anchors to the shared
   normalized `t` axis and moves physical length to the top-level design.

A migration is reported as applied only when its `applies_if` predicate matches, and
those predicates recognise the payloads their transform can rewrite **completely** —
not merely payloads that look legacy. `005_freeform_normalized_axis` is the clearest
case: it applies only when a full normalisation plan can be built, so a file carrying
partial or unresolvable absolute-z anchors is left alone and is not reported as
migrated. A migration note therefore means "this file was rewritten", never "this file
resembled an old one".

The migrations are defined in `server/design/migrate.py` and frozen by
`server/tests/test_migrate.py`, `test_textcfg.py`, and `test_preview_ws_translate.py`.

## Adding keys without a format bump

`design-format` is **2** and adding a schema key does not change it. Unknown top-level
assignments and blocks round-trip unchanged, so a file written by a newer build stays
readable by an older one: the older reader preserves the key it does not understand
rather than dropping or rejecting it. The version is reserved for changes that alter
how existing text is *interpreted* — a renamed key, a changed unit, a new default that
silently shifts geometry — because only those can make an old reader wrong rather than
merely incomplete.

`Morph.Exponent` is the current example. It is a normal schema key
(`server/design/textcfg.py`, read and written like any other), the mesher maps it to
`morphExponent`, and `design-format` stayed at 2.

Not everything the application sends to a solve belongs in this file. The passive
cardioid's fields — rear volume, port length and area, port-area provenance, foam
resistance, coupled-output flag — are **job** fields on the imported-geometry wire
model (`server/jobs/models.py`), not design fields. They are absent from the schema and
from this format by design: they describe one solve request against imported CAD, not
the portable parametric design, and a `.cfg` that carried them would imply a
reproducibility it cannot offer.

## CAD identity block

Saved linked designs may carry exactly this schema-1 block:

```cfg
CadLink = {
DesignId = wgd_<26-character ULID>
LineageId = wgl_<26-character ULID>
EditVersion = 1
SavedAt = 2026-08-13T12:00:00Z
SavedDesignHash = sha256:<16 lowercase hex digits>
Schema = 1
}
```

It is save-time identity only. Export history, bundle paths, and per-machine CAD state
belong in the server-side CAD-link registry, not in the portable design file.

## Serialization

Canonical output uses the stable original-writer order, followed by preserved unknown
keys and blocks. Numeric booleans use `1`/`0`; enclosure/resolution lists use comma
separation. A pristine parsed file may be returned byte-for-byte so comments and raw
expressions survive; once edited or normalized into canonical output, parsing the
serialized result must retain the same validated API meaning.
