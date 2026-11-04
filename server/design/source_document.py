"""Canonical source-authoring wire types shared by the design and preset API."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt

Number = Annotated[StrictFloat | StrictInt, Field(allow_inf_nan=False)]
Identity = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Point(WireModel):
    id: Identity
    r_mm: Number
    z_mm: Number


class Segment(WireModel):
    id: Identity
    start: Identity
    end: Identity
    role: Literal["moving", "rigid"] = "moving"
    kind: Literal["line", "arc"] = "line"
    center_mm: tuple[Number, Number] | None = None
    direction: Literal["cw", "ccw"] = "ccw"


class Contour(WireModel):
    version: Annotated[StrictInt, Field(ge=1, le=1)] = 1
    physical_source_id: Identity
    rim_id: Identity
    points: Annotated[list[Point], Field(min_length=2, max_length=257)]
    segments: Annotated[list[Segment], Field(min_length=1, max_length=256)]


class Drive(WireModel):
    channel_id: Identity
    weights: Annotated[dict[Identity, Number], Field(min_length=1, max_length=256)]
    motion: Literal["normal", "axial"] = "normal"


class SourceDocument(WireModel):
    contour: Contour
    drive: Drive


class PhasePlug(WireModel):
    id: Identity
    z0_mm: Number
    z1_mm: Number
    inner0_mm: Number
    outer0_mm: Number
    inner1_mm: Number
    outer1_mm: Number

