# Waveguide Generator text configuration format

This document formalizes the ATH-style format used by Waveguide Generator v1 and v2. The conventional suffix is `.cfg` or `.txt`; legacy `.mwg` files use the same grammar and remain readable.

## Dialects and encoding

Files are Unicode text. Line endings may be LF or CRLF. The parser classifies content as `mwg` when it contains a case-insensitive comment matching `; Parameter config` or `; MWG config`; all other content is `ath`. This content sniff mirrors v1 `src/geometry/params.js:65-67` and is independent of the filename.

V2 serialization starts with the v1 header and adds an ignored version comment:

```cfg
; Parameter config
; Waveguide Generator v2 design-format: 2
```

## Lexical grammar

The grammar below is EBNF-like. `EOL` is a line ending and `TEXT` means any characters other than `EOL`.

```text
document       = { blank | comment | assignment | block } ;
blank          = whitespace, EOL ;
comment        = whitespace, ";", TEXT, EOL ;
assignment     = whitespace, name, whitespace, "=", raw-value, [ inline-comment ], EOL ;
block          = whitespace, name, whitespace, "=", whitespace, "{", EOL,
                 { blank | comment | assignment | raw-row },
                 whitespace, "}", [ inline-comment ], EOL ;
raw-row        = whitespace, TEXT, EOL ;
inline-comment = whitespace, ";", TEXT ;
name           = name-char, { name-char } ;
name-char      = letter | digit | "_" | "." | ":" | "-" ;
raw-value      = TEXT ;
whitespace     = { " " | "\t" } ;
```

An assignment is split on its first `=` only. Leading and trailing whitespace around its name and value is insignificant. The value itself is retained verbatim after that trim, so `a = b=c` has the value `b=c`. A semicolon starts a comment outside the historical multiline-expression exception below. Blocks do not nest.

Unknown top-level assignments are stored in `extra_keys`. Unknown blocks retain their name, assignment order, raw rows, and block comments in `extra_blocks`. They are never discarded.

### Historical multiline expressions

Some v1 snapshots persisted JavaScript `Function#toString` output beginning with `function anonymous(`. Although this was not intentional writer syntax, it occurs in 143 corpus files. V2 consumes through the function's balanced closing brace as one raw expression. This prevents internal lines such as `const pi = Math.PI;` from becoming false config keys and preserves the source byte-for-byte.

## Numeric expressions

Every numeric schema field uses an `Expr` value:

```json
{"value": 55.0, "raw": "45 + 10"}
```

`raw` is the original text and is always preferred on serialization. `value` is populated for finite scalar numeric expressions. An angular expression involving v1's `p` variable has no single scalar value, so its `value` is null and its `raw` text remains authoritative. Legacy tokens such as `undefined` and `NaN` are likewise retained without inventing a value.

ATH expression syntax commonly uses arithmetic, `^` for exponentiation, `p`, `pi`, and functions such as `sin`, `cos`, and `abs`. Parsing does not execute arbitrary code.

## Formula sections

The root schema is discriminated by `formula`:

- `OSSE`: accepted as an `OSSE = { ... }` ATH block or the writer's flat keys (`Coverage.Angle`, `Length`, `Term.*`, `Throat.*`, `OS.*`).
- `R-OSSE`: `R-OSSE = { ... }` with `R`, `a`, `a0`, `b`, `k`, `m`, `q`, `r`, `r0`, and optional `tmax`.
- `ICW`: `ICW = { ... }` with profile size, coverage/hold, rollback depth/curl, coefficient, angle, and termination fields. This is the v2 extension for the ICW fields already accepted by the solver adapter.
- `FREEFORM`: the flat keys and blocks below.

The optional common sections cover throat extension, morphing, mesh segmentation and solver sizing, Z-map sampling, quadrant selection, enclosure, source shape/radius/curvature/velocity convention, output flags, frequency sweep, simulation mode, and maximum-size guards.

ATH fixture-style `MORPH`, `Mesh`, `Source`, and `Simulation` blocks are accepted as aliases for their flat dotted keys. `Mesh.Enclosure` is a named block in both dialects:

```cfg
Mesh.Enclosure = {
Depth = 280
EdgeRadius = 18
EdgeType = 1
Spacing = 25,25,25,25
FrontResolution = 25,25,25,25
BackResolution = 40,40,40,40
}
```

## FREEFORM tables

FREEFORM uses five required flat values and five blocks:

```cfg
Freeform.Length = 120
Freeform.ThroatRadius = 12.7
Freeform.ThroatAngle = 15.5
Freeform.OvershootPolicy = reject
Freeform.InflectionPolicy = warn
Freeform.H = {
MouthRadius = 140
MouthAngle = 60
ThroatTangentScale = 1
MouthTangentScale = 1
}
Freeform.H.Points = {
40 35
80 80 50 1
}
```

`Freeform.V` and `Freeform.V.Points` have the same shape. Point rows are:

```text
z r [angleDeg [strength]]
```

The endpoints are implicit: `(0, Freeform.ThroatRadius)` and `(Freeform.Length, MouthRadius)`. `Freeform.CrossSections` contains:

```text
t shape [exponent|cornerRadiusMm]
```

Shapes are `circle`, `ellipse`, `superellipse`, or `rounded_rectangle`. The third superellipse value is its exponent; the third rounded-rectangle value is an absolute corner radius in millimetres. The API schema additionally models optional per-station and per-ring corner grids.

## Ordered migrations

Migrations run before schema validation when `parse(..., migrate=True)` (the default):

1. `001_corner_ratio_to_corner_grid` converts removed FREEFORM `cornerRatio`/`corner_ratio` fractions—and the old text token `ratio:<number>`—to `corner_radius_mm`. It multiplies the ratio by the smaller local H/V radius and rounds to 0.1 mm, matching v1 state normalization semantics.
2. `002_inflection_allow_to_warn` changes the removed FREEFORM inflection-policy alias `allow` to `warn`.

Each migration has an applies-if predicate, is idempotent, and is reported by name in the migration dry-run.

## Serialization and round trips

New or modified designs serialize in the stable order of v1 `src/export/mwgConfig.js:46-295`, followed by preserved unknown keys and blocks. Booleans use v1 numeric spelling (`1`/`0`) when represented numerically. Lists used by enclosure and resolution fields retain comma-separated text.

For an unchanged parsed file, serialization returns its exact source bytes. Therefore comments, expressions, block rows, spacing, and legacy writer output survive exactly. For all parseable inputs, parsing the serialized result must produce the same validated API meaning.
