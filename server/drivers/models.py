"""Response shapes for the driver library API.

These describe output only, so unlike ``server/jobs/models.py``'s wire
contracts they do not forbid extra fields -- FastAPI's ``response_model``
uses them purely to validate and document what the routes already return.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class DriverVariantSummary(BaseModel):
    id: str
    z_ohm: float | None = None


class DriverSource(BaseModel):
    file: str
    source_url: str | None = None
    price_eur: float | None = None


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


class DriverDetail(DriverHit):
    fields: dict[str, float | str | None]
    extras: dict[str, str]


class DriverLibraryFile(BaseModel):
    name: str
    rows: int


class DriverLibraryInfo(BaseModel):
    folder: str
    files: list[DriverLibraryFile]
    total_drivers: int
    #: How many of them carry enough Thiele-Small data to drive a channel, and
    #: so are the ones a ``complete`` search will offer.
    complete_drivers: int = 0
    last_scan: str | None = None


__all__ = [
    "DriverDetail",
    "DriverDisplay",
    "DriverHit",
    "DriverLibraryFile",
    "DriverLibraryInfo",
    "DriverSearchResponse",
    "DriverSource",
    "DriverVariantSummary",
]
