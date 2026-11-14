"""CSV column aliasing and value parsing for the driver library.

CADLINK-CROSSOVER-DRIVERS.md §4 defines the alias table: a driver CSV's
columns are matched case-insensitively against a fixed set of names, unknown
columns are kept verbatim as opaque extras, and a missing value is never
invented. ``Diameter_mm`` is not part of that alias list in the plan text but
is required by the ``kind`` classifier below it, so it is carried here too.
"""

from __future__ import annotations

import math
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
    "driver_type": ("Driver_type",),
}

#: Fields kept as (stripped) text rather than parsed as numbers.
TEXT_FIELDS = frozenset({"brand", "model", "source_url", "driver_type"})

#: The values a ``Driver_type`` cell may carry; they are the picker's kinds.
DRIVER_TYPES = frozenset({"cd", "lf", "unknown"})

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

#: A leading number: digits and the separators a spreadsheet may put between
#: them, then an optional exponent. Which separators mean what is decided
#: afterwards, in ``_read_number``, because that depends on the file.
_NUMBER_START = re.compile(
    r"(?P<sign>[-+]?)(?P<body>[0-9.,][0-9.,\u00a0\u2009\u202f ]*)(?P<exp>[eE][-+]?[0-9]+)?"
)
#: Spaces a spreadsheet uses to group thousands: plain, no-break, thin and
#: narrow no-break.
_GROUP_SPACES = " \u00a0\u2009\u202f"
_SPACE_GROUPED = re.compile(r"[0-9]{1,3}(?:[" + _GROUP_SPACES + r"][0-9]{3})+")
#: What may follow a number in the same cell: a unit, which starts with a
#: letter or a unit sign -- ``8 Ohm``, ``1.2 mH``, ``350 cm²``, ``92 dB``.
_UNIT_START = re.compile(r"[^\W\d_]|[%°Ω\u00b5\u03bc\"']", re.UNICODE)

_FIX_POINT = "Write it with a point as the decimal mark and no thousands separator, e.g. 0.9 or 1200."


class NumberFormatProblem(ValueError):
    """A cell holds a number that cannot be read without guessing.

    The message says why and how to write it instead; the caller adds where.
    """


def _read_number(body: str, *, decimal_comma: bool) -> str:
    """``body`` as text ``float()`` accepts, or a refusal saying why."""

    integer, mark, fraction = body, "", ""
    if decimal_comma:
        if "," in body and "." in body:
            raise NumberFormatProblem(
                "it uses both a comma and a point, so one of them must be a thousands "
                "separator and the value is ambiguous. Write it with a comma as the decimal "
                "mark and no thousands separator, e.g. 1200,5."
            )
        if body.count(",") > 1 or body.count(".") > 1:
            raise NumberFormatProblem(
                "it has more than one decimal mark. Write it with one comma as the decimal "
                "mark and no thousands separator, e.g. 1200,5."
            )
        if "," in body:
            integer, mark, fraction = body.partition(",")
        elif "." in body:
            integer, mark, fraction = body.partition(".")
            if integer.strip(_GROUP_SPACES).lstrip("0") and len(fraction) == 3:
                # 1.200 is 1200 in the locales that write decimal commas.
                raise NumberFormatProblem(
                    "in a file that uses decimal commas, a point followed by three digits "
                    "may be a thousands separator. Write it as 1200 or as 1,2."
                )
    else:
        if "," in body:
            raise NumberFormatProblem(
                "it contains a comma, which could be a decimal comma or a thousands "
                "separator. " + _FIX_POINT
            )
        if body.count(".") > 1:
            raise NumberFormatProblem("it has more than one decimal point. " + _FIX_POINT)
        integer, mark, fraction = body.partition(".")
    if any(space in integer for space in _GROUP_SPACES):
        if not _SPACE_GROUPED.fullmatch(integer):
            raise NumberFormatProblem(
                "it has spaces inside the number that are not groups of three digits, so it "
                "may be two values. Put a single value in the cell, without spaces."
            )
        integer = "".join(ch for ch in integer if ch not in _GROUP_SPACES)
    if not fraction.isdigit() and fraction:
        raise NumberFormatProblem("it is not a number. " + _FIX_POINT)
    if not integer and not fraction:
        raise NumberFormatProblem("it is not a number. " + _FIX_POINT)
    return f"{integer or '0'}{'.' if mark else ''}{fraction}"


def parse_numeric(raw: str | None, *, decimal_comma: bool = False) -> float | None:
    """Read a cell like ``8``, ``8.0`` or ``8 Ohm`` as a number.

    A blank cell, or one with no digits at all (``n/a``, ``TBD``), reads as
    missing -- never as zero and never as a guess. A cell whose number could
    only be read by guessing -- ``0,9`` in a comma-separated file, ``<1``,
    ``8/16``, ``1 2`` -- raises ``NumberFormatProblem`` instead of being
    read as the digits before the first separator.

    ``decimal_comma`` is for a file separated by semicolons or tabs, where a
    comma inside a cell can only be a decimal mark. Spaces group thousands in
    either kind of file, but only as exact groups of three digits.
    """

    if raw is None:
        return None
    text = raw.strip()
    if not any(ch.isdigit() for ch in text):
        return None
    match = _NUMBER_START.match(text)
    if match is None:
        raise NumberFormatProblem(
            "there is text before the number, which changes what it means (for example "
            "<1 or approx. 8). Put only the value in the cell, optionally followed by its unit."
        )
    raw_body = match.group("body")
    body = raw_body.rstrip(_GROUP_SPACES)
    if match.group("exp") is not None and body == raw_body:
        exponent, rest = match.group("exp"), text[match.end():]
    else:
        exponent, rest = "", text[match.start("body") + len(body):]
    rest = rest.lstrip(_GROUP_SPACES)
    if rest and not _UNIT_START.match(rest):
        raise NumberFormatProblem(
            "there is more after the number than a unit (for example 8/16 or 5-6), so it may "
            "be more than one value. Put a single value in the cell, optionally followed by its unit."
        )
    number = _read_number(body, decimal_comma=decimal_comma)
    return float(f"{match.group('sign')}{number}{exponent}")


class CellProblem(ValueError):
    """A cell this row cannot be read with, and why."""

    def __init__(self, column: str, value: str, reason: str) -> None:
        super().__init__(f"{column} = {value!r}: {reason}")
        self.column = column
        self.value = value
        self.reason = reason


class RowShapeProblem(ValueError):
    """The row has a different number of cells from the header."""


def parse_row(
    row: dict[str | None, str | None], *, decimal_comma: bool = False
) -> tuple[dict[str, float | str | None], dict[str, str]]:
    """Map cells to fields and extras.

    Raises ``RowShapeProblem`` for a row with too many or too few cells and
    ``CellProblem`` for a value that cannot be read without guessing, so the
    caller skips the row, not the library, and can say why.
    """

    # DictReader uses a None key for excess cells and None values for missing
    # cells.
    if None in row:
        raise RowShapeProblem("it has more cells than the header has columns")
    if any(value is None for value in row.values()):
        raise RowShapeProblem("it has fewer cells than the header has columns")

    fields: dict[str, float | str | None] = {}
    extras: dict[str, str] = {}
    for raw_header, raw_value in row.items():
        if raw_header is None:
            continue
        header = raw_header.strip()
        if not header:
            continue
        if header.lower() in {"reliability", "reliability_reasons"}:
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
            try:
                number = parse_numeric(value, decimal_comma=decimal_comma)
            except NumberFormatProblem as exc:
                raise CellProblem(header, value, str(exc)) from None
            if number is not None and not math.isfinite(number):
                raise CellProblem(header, value, "the number is too large to use.")
            fields[canonical] = number
    return fields, extras


def classify_kind(fields: dict[str, float | str | None]) -> str:
    """CADLINK-CROSSOVER-DRIVERS.md §4's row-level compression/LF classifier.

    A row's own ``Driver_type`` cell (``cd``, ``lf`` or ``unknown``) wins over
    the field heuristic below. It exists for products the heuristic cannot tell
    apart from a compression driver, such as a dome tweeter that publishes a
    diameter and a recommended crossover. A blank or unrecognised cell falls
    back to the heuristic, so a typo can never hide a driver.
    """

    declared = fields.get("driver_type")
    if isinstance(declared, str) and declared.strip().lower() in DRIVER_TYPES:
        return declared.strip().lower()
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
    diameter = fields.get("diameter_mm")
    if kind == "lf" and isinstance(diameter, (int, float)):
        return f"{_fmt(float(diameter))} mm"
    return None


__all__ = [
    "ALIASES",
    "CellProblem",
    "NumberFormatProblem",
    "RowShapeProblem",
    "DRIVER_TYPES",
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
