"""CSV column aliasing and value parsing for the driver library.

CADLINK-CROSSOVER-DRIVERS.md §4 defines the alias table: a driver CSV's
columns are matched case-insensitively against a fixed set of names, unknown
columns are kept verbatim as opaque extras, and a missing value is never
invented. ``Diameter_mm`` is not part of that alias list in the plan text but
is required by the ``kind`` classifier below it, so it is carried here too.
"""

from __future__ import annotations

import re


#: canonical field name -> the column headers (case-insensitive) that mean it.
ALIASES: dict[str, tuple[str, ...]] = {
    "brand": ("Brand",),
    "model": ("Model",),
    "z_ohm": ("Z_ohm", "Impedance_ohm"),
    "size_in": ("Size_in",),
    "throat_in": ("Throat_in",),
    "diameter_mm": ("Diameter_mm",),
    "sd_cm2": ("Sd_cm2", "Sd", "Sd_cm^2"),
    "bl_t_m": ("Bl_Tm", "Bl"),
    "re_ohm": ("Re_ohm", "Re"),
    "le_mh": ("Le_mH", "Le"),
    "le2_mh": ("Le2_mH", "Le2"),
    "re2_ohm": ("Re2_ohm", "Re2"),
    "mms_g": ("Mms_g", "Mms"),
    "mmd_g": ("Mmd_g",),
    "fs_hz": ("Fs_Hz", "Fs"),
    "vas_l": ("Vas_L", "Vas"),
    "qms": ("Qms",),
    "qes": ("Qes",),
    "qts": ("Qts",),
    "cms_mm_per_n": ("Cms_mm_per_N", "Cms_mmN"),
    "rms_kg_per_s": ("Rms_kg_per_s",),
    "xmax_mm": ("Xmax_mm", "Xmax"),
    "sensitivity_db": ("Sensitivity_dB",),
    "power_w": ("Power_W", "Power_AES_W"),
    "xo_min_hz": ("XO_min_Hz",),
    "freq_low_hz": ("Freq_low_Hz",),
    "price_avg_eur": ("Price_avg_EUR", "Price_EUR"),
    "source_url": ("Source_URL", "URL"),
}

#: Fields kept as (stripped) text rather than parsed as numbers.
TEXT_FIELDS = frozenset({"brand", "model", "source_url"})

#: Thiele/Small fields that make a driver record "partial" rather than
#: "catalogue" when at least one of them is present.
TS_FIELDS = (
    "sd_cm2",
    "bl_t_m",
    "re_ohm",
    "le_mh",
    "mms_g",
    "mmd_g",
    "fs_hz",
    "vas_l",
    "qms",
    "xmax_mm",
    "cms_mm_per_n",
    "rms_kg_per_s",
)

#: canonical DriverSpec field -> library field it is copied from verbatim.
#: ``cms_m_per_n`` is handled separately because it also converts units.
SPEC_FIELD_MAP: dict[str, str] = {
    "sd_cm2": "sd_cm2",
    "bl_t_m": "bl_t_m",
    "re_ohm": "re_ohm",
    "le_mh": "le_mh",
    "le2_mh": "le2_mh",
    "re2_ohm": "re2_ohm",
    "mms_g": "mms_g",
    "mmd_g": "mmd_g",
    "fs_hz": "fs_hz",
    "vas_l": "vas_l",
    "qms": "qms",
    "xmax_mm": "xmax_mm",
    "rms_kg_per_s": "rms_kg_per_s",
    # Neither drives the LEM: both are ceilings, read only when a channel is
    # asked how loud it can go. ``z_ohm`` is the nominal impedance the power
    # rating is quoted against, which is the only honest divisor for turning a
    # drive voltage into watts that can be compared with that rating.
    "power_w": "power_w",
    "z_ohm": "z_nom_ohm",
}

_ALIAS_LOOKUP: dict[str, str] = {
    alias.strip().lower(): canonical
    for canonical, aliases in ALIASES.items()
    for alias in aliases
}

_NUMERIC_PREFIX = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def parse_numeric(raw: str | None) -> float | None:
    """Extract a leading number from a cell like ``8``, ``8.0`` or ``8 Ohm``.

    A blank cell, or one with no recognisable number, reads as missing --
    never as zero and never as a guess.
    """

    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    match = _NUMERIC_PREFIX.search(text)
    if match is None:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_row(row: dict[str | None, str | None]) -> tuple[dict[str, float | str | None], dict[str, str]]:
    """Map one CSV row's cells onto canonical fields plus opaque extras."""

    fields: dict[str, float | str | None] = {}
    extras: dict[str, str] = {}
    for raw_header, raw_value in row.items():
        if raw_header is None:
            continue
        header = raw_header.strip()
        if not header:
            continue
        value = (raw_value or "").strip() if isinstance(raw_value, str) else ""
        canonical = _ALIAS_LOOKUP.get(header.lower())
        if canonical is None:
            if value:
                extras[header] = value
            continue
        if canonical in TEXT_FIELDS:
            fields[canonical] = value or None
        else:
            fields[canonical] = parse_numeric(value)
    return fields, extras


def classify_kind(fields: dict[str, float | str | None]) -> str:
    """CADLINK-CROSSOVER-DRIVERS.md §4's row-level compression/LF classifier."""

    has_throat = fields.get("throat_in") is not None
    has_bare_diameter = fields.get("diameter_mm") is not None and fields.get("sd_cm2") is None
    has_xo = fields.get("xo_min_hz") is not None
    if has_throat or has_bare_diameter or has_xo:
        return "cd"
    if fields.get("size_in") is not None or fields.get("sd_cm2") is not None:
        return "lf"
    return "unknown"


def classify_completeness(fields: dict[str, float | str | None]) -> str:
    has_sd = fields.get("sd_cm2") is not None
    has_bl = fields.get("bl_t_m") is not None
    has_re = fields.get("re_ohm") is not None
    has_mass = fields.get("mms_g") is not None or fields.get("mmd_g") is not None
    has_compliance = (
        fields.get("cms_mm_per_n") is not None
        or fields.get("vas_l") is not None
        or fields.get("fs_hz") is not None
    )
    if has_sd and has_bl and has_re and has_mass and has_compliance:
        return "full"
    if any(fields.get(name) is not None for name in TS_FIELDS):
        return "partial"
    return "catalogue"


def build_spec(fields: dict[str, float | str | None]) -> dict[str, float]:
    """The subset of ``fields`` that fills a ``DriverSpec``, Hornresp units.

    Only present values are emitted, and ``mmd_g``/``mms_g`` never both are --
    ``mms_g`` wins, matching ``DriverSpec``'s exactly-one-mass rule. The LR-2
    pair obeys the same principle from the other side: a row stating only one
    half of it emits neither, because a spec the server refuses would make the
    library row unpickable rather than merely less accurate.
    """

    spec: dict[str, float] = {}
    for canonical, spec_key in SPEC_FIELD_MAP.items():
        value = fields.get(canonical)
        if isinstance(value, (int, float)):
            spec[spec_key] = float(value)
    cms_mm_per_n = fields.get("cms_mm_per_n")
    if isinstance(cms_mm_per_n, (int, float)):
        spec["cms_m_per_n"] = float(cms_mm_per_n) / 1000.0
    if "mmd_g" in spec and "mms_g" in spec:
        del spec["mmd_g"]
    if ("le2_mh" in spec) != ("re2_ohm" in spec):
        spec.pop("le2_mh", None)
        spec.pop("re2_ohm", None)
    return spec


def build_display(fields: dict[str, float | str | None]) -> dict[str, float | None]:
    return {
        "fs_hz": fields.get("fs_hz"),
        "sd_cm2": fields.get("sd_cm2"),
        "bl_t_m": fields.get("bl_t_m"),
        "xmax_mm": fields.get("xmax_mm"),
        "power_w": fields.get("power_w"),
        "sensitivity_db": fields.get("sensitivity_db"),
        "price_eur": fields.get("price_avg_eur"),
    }


def build_source(file_name: str, fields: dict[str, float | str | None]) -> dict[str, float | str | None]:
    return {
        "file": file_name,
        "source_url": fields.get("source_url"),
        "price_eur": fields.get("price_avg_eur"),
    }


def classify_size(fields: dict[str, float | str | None], kind: str) -> str | None:
    def _fmt(value: float) -> str:
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return text or "0"

    if kind == "cd":
        throat = fields.get("throat_in")
        if isinstance(throat, (int, float)):
            return f'{_fmt(float(throat))}"'
        diameter = fields.get("diameter_mm")
        if isinstance(diameter, (int, float)):
            return f"{_fmt(float(diameter))} mm"
        return None
    size = fields.get("size_in")
    if isinstance(size, (int, float)):
        return f'{_fmt(float(size))}"'
    return None


__all__ = [
    "ALIASES",
    "SPEC_FIELD_MAP",
    "TEXT_FIELDS",
    "TS_FIELDS",
    "build_display",
    "build_source",
    "build_spec",
    "classify_completeness",
    "classify_kind",
    "classify_size",
    "parse_numeric",
    "parse_row",
]
