"""Solve options: the execution choices a solve request carries beside its design.

Pure request schema below every server package that reads it, including the
design file's solve block (``server/design/solve_block.py``).
"""

from __future__ import annotations

import math
from typing import Any, Literal, Mapping

from pydantic import Field, SerializerFunctionWrapHandler, field_validator, model_serializer, model_validator

from .base import JobModel
from .observations import PolarConfig


# Serializer return annotations intentionally remain absent: Pydantic uses them
# to replace the public serialization schema, hiding the model fields. The
# narrow no-untyped-def exceptions below preserve the existing wire contract.
MAX_EXPLICIT_FREQUENCIES = 401


class GroundPlaneConfig(JobModel):
    """A rigid reflecting half space the model stands above.

    Kept in solve options rather than in the design because it describes the
    room the horn is placed in, not the horn: ATH's text format has no key for
    it, so a design-level field would either break round-trip or invent a
    WG-only dialect. It is also the setting a user changes between solves of
    the *same* design, which is what solve options are for.

    ``axis`` names the coordinate the half space bounds -- never an axis-pair
    token; see ``server/solver/ground_plane.py`` for why that distinction is
    load-bearing. WG's frame has z along the horn axis and y vertical, so the
    floor is ``axis="y"``, a side wall is ``"x"``, and ``"z"`` is a rigid wall
    behind the throat.

    ``height_m`` is the height of the model's own origin above the plane,
    matching Boundary Lab's per-source ``positionHeightM``. The default 1.0 m
    is a listening-axis height, not a physical constant: it is a starting
    value, and the solve refuses rather than guesses if the model would cross
    the plane at it.
    """

    enabled: bool = False
    axis: Literal["x", "y", "z"] = "y"
    height_m: float = Field(default=1.0)

    @field_validator("axis", mode="before")
    @classmethod
    def normalize_axis(cls, value: Any) -> Any:
        return str(value).strip().lower()

    @model_validator(mode="after")
    def validate_height(self) -> "GroundPlaneConfig":
        if not math.isfinite(self.height_m):
            raise ValueError("Height above surface is not finite. Enter a finite Height above surface under Ground plane in Solve options.")
        if self.height_m < 0.0:
            raise ValueError(
                "Height above surface cannot be negative. Enter zero or a positive Height above surface under Ground plane in Solve options."
            )
        return self


class SolveOptions(JobModel):
    """Execution choices kept separate from the authoritative v2 design."""

    engine: str = "auto"
    accuracy: Literal["fast", "accurate"] = "fast"
    adaptive_frequency_sampling: bool = False
    mesh_limit_override: bool = False
    mesh_limit_override_key: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def ignore_retired_precision(cls, value: Any) -> Any:
        """Read experimental saved requests without restoring their retired option."""
        if isinstance(value, Mapping) and "beat_precision" in value:
            return {key: item for key, item in value.items() if key != "beat_precision"}
        return value

    @model_serializer(mode="wrap")
    def serialize_sampling_option(self, handler: SerializerFunctionWrapHandler):  # type: ignore[no-untyped-def]
        """Keep every legacy wire encoding unchanged when sampling is off."""
        wire = handler(self)
        if not self.adaptive_frequency_sampling:
            wire.pop("adaptive_frequency_sampling", None)
        if not self.mesh_limit_override:
            wire.pop("mesh_limit_override", None)
        if self.mesh_limit_override_key is None:
            wire.pop("mesh_limit_override_key", None)
        return wire

    # ``auto`` remains accepted for old clients but resolves as Full 3D.
    # ``circsym`` stays decodable so historical Axisymmetric requests load;
    # planning, submission, retry and execution refuse it.
    solver_mode: Literal["auto", "full_3d", "circsym"] = "full_3d"
    symmetry: str = "auto"
    frequency_range: list[float] | None = None
    num_frequencies: int | None = Field(default=None, ge=1, le=MAX_EXPLICIT_FREQUENCIES)
    frequency_spacing: Literal["log", "linear"] = "log"
    # Explicit sweep points, solved verbatim instead of a generated grid. The
    # BEM cost per point is flat (same-size matrix at every frequency), so this
    # is about *where* the points land, not about spending fewer of them.
    frequencies_hz: list[float] | None = None
    verbose: bool = False
    # Per-band mesh ladder. ``auto`` solves each descending octave on a mesh
    # coarsened for that octave, which is where a full-band sweep's cost
    # actually is -- per-frequency BEM cost is flat on a fixed mesh, so 100 Hz
    # otherwise pays the 20 kHz element count. Off by default: it trades the
    # field plane (no single surface mesh to retain traces on) for the speed,
    # and the top octave -- and therefore the design's own mesh -- is unchanged
    # either way.
    mesh_ladder: Literal["off", "auto"] = "off"
    mesh_validation_mode: Literal["warn", "strict", "off"] = "warn"
    polar_config: PolarConfig = Field(default_factory=PolarConfig)
    # Off by default, so every existing solve is unchanged.
    ground_plane: GroundPlaneConfig = Field(default_factory=GroundPlaneConfig)
    stage_delay_ms: int = Field(default=30, ge=0, le=2000)

    @field_validator("engine", "solver_mode", "symmetry")
    @classmethod
    def normalize_named_option(cls, value: str, info: Any) -> str:
        normalized = value.strip().lower()
        if not normalized:
            from hornlab_mesher.controls import control_label
            raise ValueError(f"{control_label('options.' + info.field_name)} is empty. Choose a value in Solve options.")
        if info.field_name == "symmetry" and normalized not in {
            "auto",
            "full",
            "half_xz",
            "half_yz",
            "quarter",
        }:
            raise ValueError(
                "Symmetry is unavailable. Choose Automatic, Full model, a half model or Quarter in Solve options."
            )
        if info.field_name == "solver_mode" and normalized not in {
            "auto",
            "full_3d",
            "circsym",
        }:
            raise ValueError("Solver mode is unavailable. Choose Full 3D in Solve options.")
        return normalized

    @field_validator(
        "frequency_spacing", "mesh_validation_mode", "mesh_ladder", mode="before"
    )
    @classmethod
    def normalize_option_enum(cls, value: Any) -> Any:
        return str(value).strip().lower()

    @model_validator(mode="after")
    def validate_frequency_range(self) -> "SolveOptions":
        if self.frequency_range is None:
            return self
        if len(self.frequency_range) != 2:
            raise ValueError("Frequency range needs a start and an end. Enter both in Solve options.")
        start, end = self.frequency_range
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("Frequency range contains a value WG cannot use. Enter finite start and end frequencies in Solve options.")
        if start <= 0 or end <= start:
            raise ValueError("Frequency range must start above zero and end above the start. Change the start or end frequency in Solve options.")
        return self

    @model_validator(mode="after")
    def validate_explicit_frequencies(self) -> "SolveOptions":
        if self.frequencies_hz is None:
            return self
        # Refuse rather than silently pick a winner: a caller that sends both a
        # list and a grid has two different sweeps in mind, and quietly dropping
        # one is exactly the class of silent no-op this codebase keeps paying for.
        conflicts = [
            name
            for name, value in (
                ("frequency_range", self.frequency_range),
                ("num_frequencies", self.num_frequencies),
            )
            if value is not None
        ]
        if conflicts:
            raise ValueError(
                "Sweep points contains both an Explicit list and a Generated grid. "
                "Choose one in Solve options and clear the other."
            )
        if not self.frequencies_hz:
            raise ValueError("Solver frequencies in Hz is empty. Enter at least one frequency in Solve options.")
        if len(self.frequencies_hz) > MAX_EXPLICIT_FREQUENCIES:
            raise ValueError(f"Solver frequencies in Hz has too many entries. Enter at most {MAX_EXPLICIT_FREQUENCIES} frequencies in Solve options.")
        if not all(math.isfinite(value) for value in self.frequencies_hz):
            raise ValueError("Solver frequencies in Hz contains a value WG cannot use. Enter finite frequencies in Solve options.")
        if any(value <= 0 for value in self.frequencies_hz):
            raise ValueError("Solver frequencies in Hz must be above zero. Enter positive frequencies in Solve options.")
        # Ascending order is a result-contract requirement, not solver taste: the
        # frequency axis is emitted verbatim and every chart and exporter reads it
        # as monotonic. Sorting silently would hide a mistyped list.
        if any(
            later <= earlier
            for earlier, later in zip(self.frequencies_hz, self.frequencies_hz[1:])
        ):
            raise ValueError(
                "Solver frequencies in Hz must increase without duplicates. Enter each frequency once, from lowest to highest, in Solve options."
            )
        return self


__all__ = [
    "GroundPlaneConfig",
    "MAX_EXPLICIT_FREQUENCIES",
    "SolveOptions",
]
