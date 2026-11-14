"""Response shapes for the driver library API.

These describe output only, so unlike ``server/jobs/models.py``'s wire
contracts they do not forbid extra fields -- FastAPI's ``response_model``
uses them purely to validate and document what the routes already return.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MmdDerivation(BaseModel):
    """Accepted free-air correction, retained as provenance, never solver input."""

    method: Literal["mms_free_air"] = "mms_free_air"
    mms_g: float = Field(gt=0, allow_inf_nan=False)
    sd_cm2: float = Field(gt=0, allow_inf_nan=False)
    mmd_kg: float = Field(gt=0, allow_inf_nan=False)
    correction_kg: float = Field(gt=0, allow_inf_nan=False)
    correction_fraction: float = Field(gt=0, le=0.5, allow_inf_nan=False)
    rho_kg_m3: float = Field(gt=0, allow_inf_nan=False)


class MmdSuggestion(BaseModel):
    suggestion: MmdDerivation | None = None
    reason: str | None = None
    warning: str | None = None


class ExteriorDriverInputs(BaseModel):
    """Editor inputs and provenance, separate from the bare-driver physics."""

    mms_g: float | None = Field(default=None, allow_inf_nan=False)
    driver_id: str | None = None
    label: str | None = None
    derived_mmd: MmdDerivation | None = None


class DriverVariantSummary(BaseModel):
    id: str
    z_ohm: float | None = None


class DriverSource(BaseModel):
    file: str
    source_url: str | None = None
    price_eur: float | None = None
    #: Read from the library that ships with the application rather than from
    #: the user's own folder, so nothing offers to edit a file they do not have.
    bundled: bool = False


class DriverDisplay(BaseModel):
    fs_hz: float | None = None
    sd_cm2: float | None = None
    bl_t_m: float | None = None
    xmax_mm: float | None = None
    power_w: float | None = None
    sensitivity_db: float | None = None
    price_eur: float | None = None


class DriverHit(BaseModel):
    id: str
    brand: str
    model: str
    z_ohm: float | None = None
    variants: list[DriverVariantSummary]
    kind: Literal["lf", "cd", "unknown"]
    size: str | None = None
    completeness: Literal["full", "partial", "catalogue"]
    spec: dict[str, float]
    display: DriverDisplay
    xo_min_hz: float | None = None
    source: DriverSource


class DriverSearchResponse(BaseModel):
    items: list[DriverHit]
    total: int
    #: Matches withheld because no winding of them carries enough T/S data to
    #: drive a channel. Always 0 unless the request asked for ``complete``.
    hidden_incomplete: int = 0
    #: How many drivers of each type this query matches with the type filter
    #: lifted, so a search that answers nothing can say which type would have
    #: answered instead of dead-ending on "no matches".
    matches_by_kind: dict[str, int] = {}


class DriverDetail(DriverHit):
    fields: dict[str, float | str | None]
    extras: dict[str, str]


class DriverLibraryFile(BaseModel):
    name: str
    rows: int
    bundled: bool = False
    #: What went wrong reading this file, each saying which file, what is
    #: wrong and how to fix it. Empty for a file that read cleanly.
    problems: list[str] = []


class DriverKindCount(BaseModel):
    """What the library holds of one driver type."""

    kind: str
    total: int
    #: Of those, how many can drive a channel -- what the picker will offer.
    complete: int = 0


class DriverLibraryInfo(BaseModel):
    folder: str
    files: list[DriverLibraryFile]
    total_drivers: int
    #: How many of them carry enough Thiele-Small data to drive a channel, and
    #: so are the ones a ``complete`` search will offer.
    complete_drivers: int = 0
    #: The breakdown by driver type, in the order a filter should list it. Types
    #: the library holds none of are absent rather than zero.
    kinds: list[DriverKindCount] = []
    last_scan: str | None = None


__all__ = [
    "DriverDetail",
    "DriverDisplay",
    "DriverHit",
    "DriverKindCount",
    "DriverLibraryFile",
    "DriverLibraryInfo",
    "DriverSearchResponse",
    "DriverSource",
    "DriverVariantSummary",
]
