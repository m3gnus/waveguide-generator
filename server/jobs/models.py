"""Generated-client-friendly request and response models for solve jobs."""

from __future__ import annotations

import json
import math
import re
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_serializer, model_validator

from server.contracts import DriverSpec as DriverSpec, JobModel as JobModel
from server.design.defaults import default_sweep_start_hz
from server.design.schema import DesignConfig


MAX_EXPLICIT_FREQUENCIES = 401


class PolarConfig(JobModel):
    """Directivity observation contract shared by every solve engine."""

    angle_range: tuple[float, float, int] = (0.0, 180.0, 37)
    # The three-value range is authoritative. angle_step retains the UI's
    # requested step so non-divisible spans remain observable in metadata.
    angle_step: float | None = Field(default=None, gt=0)
    distance: float = Field(default=2.0, ge=0.1)
    norm_angle: float = 5.0
    inclination: float = 45.0
    enabled_axes: list[Literal["horizontal", "vertical", "diagonal"]] = Field(
        default_factory=lambda: ["horizontal", "vertical", "diagonal"],
        min_length=1,
    )
    observation_origin: Literal["mouth", "throat"] = "mouth"
    spherical_sampling: bool = False
    field_plane: bool = True
    spherical_theta_count: int = Field(default=37, ge=5, le=121)
    spherical_phi_count: int = Field(default=72, ge=8, le=241)

    @model_validator(mode="before")
    @classmethod
    def convert_angle_step(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        result = dict(value)
        raw_range = result.get("angle_range")
        raw_step = result.get("angle_step")
        if isinstance(raw_range, (list, tuple)) and len(raw_range) == 2:
            if raw_step is None:
                raise ValueError(
                    "polar_config.angle_step is required when angle_range contains only start/end"
                )
            try:
                start, end, step = float(raw_range[0]), float(raw_range[1]), float(raw_step)
                count = int(round((end - start) / step)) + 1
            except (OverflowError, TypeError, ValueError, ZeroDivisionError) as exc:
                raise ValueError("polar_config angle range/step must be finite numbers") from exc
            result["angle_range"] = (start, end, count)
        return result

    @field_validator("enabled_axes", mode="before")
    @classmethod
    def normalize_enabled_axes(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)):
            return value
        return list(dict.fromkeys(str(axis).strip().lower() for axis in value))

    @field_validator("observation_origin", mode="before")
    @classmethod
    def normalize_observation_origin(cls, value: Any) -> Any:
        return str(value).strip().lower()

    @model_validator(mode="after")
    def validate_polar_domain(self) -> "PolarConfig":
        start, end, count = self.angle_range
        finite_values = (start, end, self.distance, self.norm_angle, self.inclination)
        if not all(math.isfinite(value) for value in finite_values):
            raise ValueError("polar_config numeric values must be finite")
        if end <= start:
            raise ValueError("polar_config.angle_range must be increasing")
        if not 2 <= count <= 721:
            raise ValueError("polar_config angle sample count must be between 2 and 721")
        if not start <= self.norm_angle <= end:
            raise ValueError(
                "polar_config.norm_angle must lie within polar_config.angle_range"
            )
        if self.angle_step is not None:
            if not math.isfinite(self.angle_step):
                raise ValueError("polar_config.angle_step must be finite")
        return self

    def resolved_grid(self) -> dict[str, float | int | None]:
        start, end, count = self.angle_range
        return {
            "start": float(start),
            "end": float(end),
            "count": int(count),
            "requested_step": (
                float(self.angle_step) if self.angle_step is not None else None
            ),
            "resolved_step": float((end - start) / (count - 1)),
        }


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
            raise ValueError("ground_plane.height_m must be finite")
        if self.height_m < 0.0:
            raise ValueError(
                "ground_plane.height_m must not be negative; the model's origin "
                "cannot sit below the plane it stands on"
            )
        return self


class SolveOptions(JobModel):
    """Execution choices kept separate from the authoritative v2 design."""

    engine: str = "auto"
    accuracy: Literal["fast", "accurate"] = "fast"
    adaptive_frequency_sampling: bool = False

    @model_serializer(mode="wrap")
    def serialize_sampling_option(self, handler):
        """Keep every legacy wire encoding unchanged when sampling is off."""
        wire = handler(self)
        if not self.adaptive_frequency_sampling:
            wire.pop("adaptive_frequency_sampling", None)
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
            raise ValueError(f"{info.field_name} must not be empty")
        if info.field_name == "symmetry" and normalized not in {
            "auto",
            "full",
            "half_xz",
            "half_yz",
            "quarter",
        }:
            raise ValueError(
                "symmetry must be one of auto, full, half_xz, half_yz, or quarter"
            )
        if info.field_name == "solver_mode" and normalized not in {
            "auto",
            "full_3d",
            "circsym",
        }:
            raise ValueError("solver_mode must be one of auto, full_3d, or circsym")
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
            raise ValueError("frequency_range must contain [start_hz, end_hz]")
        start, end = self.frequency_range
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("frequency_range values must be finite")
        if start <= 0 or end <= start:
            raise ValueError("frequency_range must be positive and increasing")
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
                "frequencies_hz replaces the generated grid and cannot be combined "
                f"with {' or '.join(conflicts)}"
            )
        if not self.frequencies_hz:
            raise ValueError("frequencies_hz must contain at least one frequency")
        if len(self.frequencies_hz) > MAX_EXPLICIT_FREQUENCIES:
            raise ValueError(f"frequencies_hz must contain at most {MAX_EXPLICIT_FREQUENCIES} frequencies")
        if not all(math.isfinite(value) for value in self.frequencies_hz):
            raise ValueError("frequencies_hz values must be finite")
        if any(value <= 0 for value in self.frequencies_hz):
            raise ValueError("frequencies_hz values must be positive")
        # Ascending order is a result-contract requirement, not solver taste: the
        # frequency axis is emitted verbatim and every chart and exporter reads it
        # as monotonic. Sorting silently would hide a mistyped list.
        if any(
            later <= earlier
            for earlier, later in zip(self.frequencies_hz, self.frequencies_hz[1:])
        ):
            raise ValueError(
                "frequencies_hz must be strictly ascending with no duplicates"
            )
        return self


class SolveOptionsResponse(SolveOptions):
    """Stored options are fully dumped; submission defaults remain optional."""

    engine: str
    accuracy: Literal["fast", "accurate"]
    adaptive_frequency_sampling: bool = False
    solver_mode: Literal["auto", "full_3d", "circsym"]
    symmetry: str
    frequency_range: list[float] | None
    num_frequencies: int | None = Field(ge=1, le=MAX_EXPLICIT_FREQUENCIES)
    frequency_spacing: Literal["log", "linear"]
    frequencies_hz: list[float] | None
    verbose: bool
    mesh_ladder: Literal["off", "auto"]
    mesh_validation_mode: Literal["warn", "strict", "off"]
    polar_config: PolarConfig
    ground_plane: GroundPlaneConfig
    stage_delay_ms: int = Field(ge=0, le=2000)


class DesignSnapshot(JobModel):
    version: Literal[1] = 1
    design: DesignConfig


class DesignAvailability(JobModel):
    """Whether a job's stored design can be reopened, and if not, why not.

    A job that cannot be reopened must say what is wrong in words the user can
    act on, because "Rerun is greyed out" is not a diagnosis.
    """

    reopenable: bool
    source: Literal["v2-snapshot", "cad-import", "none"]
    reason_code: Literal[
        "ok",
        "imported_geometry",
        "no_stored_design",
        "unreadable_design",
    ]
    #: Why this job cannot be reopened. Set exactly when ``reopenable`` is false.
    reason: str | None
    #: A fidelity caveat about the stored design, when there is one.
    note: str | None


class ParametricGeometrySource(JobModel):
    type: Literal["parametric"]
    design: DesignConfig
    design_revision: int = Field(default=0, ge=0)
    design_snapshot: DesignSnapshot | None = None

    @model_validator(mode="after")
    def validate_snapshot_matches_design(self) -> "ParametricGeometrySource":
        if self.design_snapshot is None:
            # Backward-compatible API callers still become atomic records: the
            # server versions the already-validated canonical design wire.
            object.__setattr__(
                self,
                "design_snapshot",
                DesignSnapshot(design=self.design.model_copy(deep=True)),
            )
        elif self.design_snapshot.design != self.design:
            raise ValueError("design_snapshot.design must match design")
        return self


class DriveChannel(JobModel):
    id: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)
    motion: Literal["normal", "axial"] = "normal"
    physical_source_id: str | None = Field(default=None, min_length=1)
    patch_weights: dict[str, float] | None = None
    driver: DriverSpec | None = None

    @model_validator(mode="after")
    def validate_patch_weights(self) -> "DriveChannel":
        if self.patch_weights is not None:
            if set(self.patch_weights) != set(self.source_ids):
                raise ValueError("patch_weights must cover exactly source_ids")
            if not all(math.isfinite(w) for w in self.patch_weights.values()):
                raise ValueError("patch_weights must be finite literal relative weights")
            if self.driver is not None:
                raise ValueError("weighted prescribed patches cannot carry a driver model")
        return self

    @model_validator(mode="after")
    def validate_driver_applicability(self) -> "DriveChannel":
        if self.driver is None:
            return self
        if self.motion != "normal":
            raise ValueError(
                "a driver model requires normal source motion; axial channels "
                "cannot carry one yet"
            )
        if len(self.source_ids) != 1:
            raise ValueError(
                "a driver model requires a single-source channel: the radiating "
                "area and surface pressure belong to exactly one source patch"
            )
        return self

    @field_validator("id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("drive channel id must not be empty")
        return normalized

    @field_validator("source_ids")
    @classmethod
    def validate_source_ids(cls, value: list[str]) -> list[str]:
        normalized = [source_id.strip() for source_id in value]
        if any(not source_id for source_id in normalized):
            raise ValueError("drive channel source_ids must not contain empty ids")
        if len(set(normalized)) != len(normalized):
            raise ValueError("drive channel source_ids must be unique")
        return normalized


FilterFamily = Literal["lr", "butterworth", "bessel", "linear_phase"]

# The one table of what a family is offered in; mirrored by
# ``server.solver.filters.FAMILY_ORDERS``, which is what actually evaluates it.
_FILTER_FAMILY_ORDERS: dict[str, tuple[int, ...]] = {
    "lr": (2, 4, 6, 8),
    "butterworth": (1, 2, 3, 4, 5, 6, 7, 8),
    "bessel": (2, 3, 4),
    "linear_phase": (2, 4, 8),
}


class FilterSpec(JobModel):
    """One high-pass or low-pass section of a drive channel."""

    family: FilterFamily
    order: int = Field(ge=1, le=8)
    fc_hz: float = Field(gt=0.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_order(self) -> "FilterSpec":
        allowed = _FILTER_FAMILY_ORDERS[self.family]
        if self.order not in allowed:
            raise ValueError(
                f"filter family {self.family!r} supports orders "
                f"{', '.join(str(value) for value in allowed)}, not {self.order}"
            )
        return self


class GainSpec(JobModel):
    """A channel's level: matched automatically, stated in dB, or driven to
    the driver's own ceiling.

    ``max`` is resolved by the solver, not here: it means "the loudest this
    channel's driver can be run before excursion, rated power or the amplifier
    binds", which needs the solved excursion and impedance and so cannot be a
    number on the wire. A channel whose members carry no driver model has no
    ceiling to find, and the solver reports that as a warning and falls back to
    0 dB rather than inventing one.
    """

    mode: Literal["auto", "manual", "max"] = "auto"
    db: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_manual(self) -> "GainSpec":
        if self.mode == "manual" and self.db is None:
            raise ValueError("a manual gain needs db")
        return self


class DelaySpec(JobModel):
    """A channel's delay: aligned automatically, or stated in ms."""

    mode: Literal["auto", "manual"] = "auto"
    ms: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_manual(self) -> "DelaySpec":
        if self.mode == "manual" and self.ms is None:
            raise ValueError("a manual delay needs ms")
        return self


class ChannelFilterSpec(JobModel):
    """Everything one member of the chain owns: band, level, delay, polarity."""

    hp: FilterSpec | None = None
    lp: FilterSpec | None = None
    gain: GainSpec = Field(default_factory=GainSpec)
    delay: DelaySpec = Field(default_factory=DelaySpec)
    invert: bool | None = None

    @model_validator(mode="after")
    def validate_band(self) -> "ChannelFilterSpec":
        if self.hp is not None and self.lp is not None and self.hp.fc_hz >= self.lp.fc_hz:
            raise ValueError(
                "a channel high-pass must sit below its low-pass: "
                f"{self.hp.fc_hz:g} Hz is not below {self.lp.fc_hz:g} Hz"
            )
        return self


class ChannelCombineSpec(JobModel):
    """Filtered time-aligned sum of drive-channel bases.

    ``members`` is the chain in band order, lowest first. Two spec forms are
    accepted (CADLINK-CROSSOVER-DRIVERS.md §2): the per-channel ``channels``
    map, and the legacy ``crossovers_hz``/``level_match``/``align`` triple,
    which means an LR4 chain with auto gain and auto delay. ``resolved()`` is
    the single place that turns the legacy form into the other one, so the
    solver only ever sees one shape. Structural defects refuse at submission;
    solve-time observations become metadata warnings, never silent skips.
    """

    id: str = "combined"
    members: list[str] = Field(min_length=2)
    crossovers_hz: list[float] | None = Field(default=None, min_length=1)
    level_match: bool = True
    align: bool = True
    reference: str | None = None
    channels: dict[str, ChannelFilterSpec] | None = None

    @field_validator("id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("combine id must not be empty")
        return normalized

    @field_validator("members")
    @classmethod
    def validate_members(cls, value: list[str]) -> list[str]:
        normalized = [member.strip() for member in value]
        if any(not member for member in normalized):
            raise ValueError("combine members must not contain empty ids")
        if len(set(normalized)) != len(normalized):
            raise ValueError("combine members must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_crossovers(self) -> "ChannelCombineSpec":
        if self.crossovers_hz is None and self.channels is None:
            raise ValueError(
                "combine needs either crossovers_hz or a per-channel channels map"
            )
        if self.crossovers_hz is not None:
            if len(self.crossovers_hz) != len(self.members) - 1:
                raise ValueError(
                    "combine needs exactly one crossover between each adjacent "
                    f"member pair: {len(self.members)} members require "
                    f"{len(self.members) - 1} crossovers_hz"
                )
            for value in self.crossovers_hz:
                if not math.isfinite(value) or value <= 0:
                    raise ValueError("crossovers_hz values must be finite and positive")
            if any(
                later <= earlier
                for earlier, later in zip(self.crossovers_hz, self.crossovers_hz[1:])
            ):
                raise ValueError("crossovers_hz must be strictly ascending")
        if self.channels is not None and set(self.channels) != set(self.members):
            raise ValueError(
                "combine channels must name exactly the members: "
                f"{sorted(self.members)} != {sorted(self.channels)}"
            )
        if self.reference is not None and self.reference not in self.members:
            raise ValueError(
                f"combine reference {self.reference!r} is not one of the members"
            )
        return self

    @property
    def resolved_reference(self) -> str:
        """The channel auto alignment pins at 0 ms: the highest band by default."""

        return self.reference or self.members[-1]

    def corner_frequencies_hz(self) -> list[float]:
        """Every corner this spec asks for, in the order it names them."""

        if self.channels is None:
            return [float(value) for value in (self.crossovers_hz or [])]
        corners: list[float] = []
        for member in self.members:
            channel = self.channels[member]
            corners.extend(
                float(section.fc_hz)
                for section in (channel.hp, channel.lp)
                if section is not None
            )
        return corners

    def linked_crossovers_hz(self) -> list[float | None]:
        """One entry per adjacent pair: its crossover, or ``None`` when unlinked.

        A pair is linked when the lower member's low-pass and the upper
        member's high-pass share a corner. Only then does "the crossover"
        name a single frequency.
        """

        if self.channels is None:
            return [float(value) for value in (self.crossovers_hz or [])]
        linked: list[float | None] = []
        for index in range(len(self.members) - 1):
            lower = self.channels[self.members[index]].lp
            upper = self.channels[self.members[index + 1]].hp
            linked.append(
                float(lower.fc_hz)
                if lower is not None
                and upper is not None
                and math.isclose(lower.fc_hz, upper.fc_hz, rel_tol=1.0e-9, abs_tol=0.0)
                else None
            )
        return linked

    def resolved(self) -> dict[str, Any]:
        """Return the per-channel form: ``{"reference", "channels"}``.

        A legacy spec expands to LR4 pairs with auto gain when level matching
        is on (manual 0 dB when it is off), auto delay when alignment is on
        (manual 0 ms when it is off) and automatic polarity.
        """

        if self.channels is not None:
            channels = {
                member: self.channels[member].model_dump(mode="json")
                for member in self.members
            }
        else:
            # One definition of the legacy meaning, shared with the solver.
            from server.solver.combine import expand_legacy_channels

            channels = expand_legacy_channels(
                list(self.members),
                [float(value) for value in (self.crossovers_hz or [])],
                level_match=self.level_match,
                align=self.align,
            )
        return {"reference": self.resolved_reference, "channels": channels}


class FieldPlaneSpec(JobModel):
    """A centred, orthonormal sampling plane in solver metres.

    Samples are generated as v-major rows with u varying fastest within each
    row. Both axes point in their positive grid directions.
    """

    origin_m: tuple[float, float, float]
    axis_u: tuple[float, float, float]
    axis_v: tuple[float, float, float]
    width_m: float = Field(gt=0.0, le=100.0, allow_inf_nan=False)
    height_m: float = Field(gt=0.0, le=100.0, allow_inf_nan=False)
    nx: int = Field(ge=2, le=256)
    ny: int = Field(ge=2, le=256)

    @field_validator("origin_m", "axis_u", "axis_v", mode="before")
    @classmethod
    def validate_vector(cls, value: Any, info: Any) -> tuple[float, float, float]:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ValueError(f"plane.{info.field_name} must contain exactly 3 numbers")
        if any(
            isinstance(component, bool)
            or not isinstance(component, (int, float))
            or not math.isfinite(float(component))
            for component in value
        ):
            raise ValueError(f"plane.{info.field_name} must contain finite numbers")
        return tuple(float(component) for component in value)

    @field_validator("width_m", "height_m", mode="before")
    @classmethod
    def validate_extent(cls, value: Any, info: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"plane.{info.field_name} must be a finite number in (0, 100]"
            )
        return value

    @field_validator("nx", "ny", mode="before")
    @classmethod
    def validate_resolution(cls, value: Any, info: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"plane.{info.field_name} must be an integer")
        return value

    @model_validator(mode="after")
    def validate_grid_and_axes(self) -> "FieldPlaneSpec":
        if self.nx * self.ny > 65_536:
            raise ValueError("plane grid may contain at most 65536 points")
        norm_u = math.sqrt(sum(component * component for component in self.axis_u))
        norm_v = math.sqrt(sum(component * component for component in self.axis_v))
        dot = sum(
            component_u * component_v
            for component_u, component_v in zip(self.axis_u, self.axis_v, strict=True)
        )
        tolerance = 1.0e-6
        if (
            abs(norm_u - 1.0) > tolerance
            or abs(norm_v - 1.0) > tolerance
            or abs(dot) > tolerance
        ):
            raise ValueError("plane axes must be finite, unit length, and orthogonal")
        return self


class FieldPlaneResponseSpec(JobModel):
    id: str = Field(min_length=1, max_length=256)

    @field_validator("id")
    @classmethod
    def validate_response_id(cls, value: str) -> str:
        normalized = value.strip()
        if normalized == "system":
            return normalized
        if normalized.startswith("channel:") and normalized.removeprefix("channel:"):
            return normalized
        if normalized.startswith("member:") and normalized.removeprefix("member:"):
            return normalized
        raise ValueError(
            "response.id must be 'system', 'channel:<id>', or 'member:<id>'"
        )


class FieldPlaneRequest(JobModel):
    version: Literal[1]
    request_id: str = Field(min_length=1, max_length=256)
    plane: FieldPlaneSpec
    frequency_index: int = Field(ge=0)
    response: FieldPlaneResponseSpec

    @field_validator("version", mode="before")
    @classmethod
    def validate_version(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("version must be the integer 1")
        return value

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id must not be empty")
        return normalized

    @field_validator("frequency_index", mode="before")
    @classmethod
    def validate_frequency_index(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("frequency_index must be an integer")
        return value


class ImportedMeshSizes(JobModel):
    rigid_size_mm: float = Field(gt=0, allow_inf_nan=False)
    transition_mm: float = Field(gt=0, allow_inf_nan=False)
    source_size_mm: dict[str, float]

    @field_validator("rigid_size_mm", "transition_mm", mode="before")
    @classmethod
    def reject_boolean_sizes(cls, value: Any, info: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"mesh.{info.field_name} must be a finite positive number")
        return value

    @field_validator("source_size_mm", mode="before")
    @classmethod
    def validate_source_sizes(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized: dict[str, float] = {}
        for source_id, raw_size in value.items():
            if not isinstance(source_id, str):
                raise ValueError("mesh.source_size_mm keys must be strings")
            normalized_id = source_id.strip()
            if not normalized_id:
                raise ValueError("mesh.source_size_mm keys must not be empty")
            if isinstance(raw_size, bool) or not isinstance(raw_size, (int, float)):
                raise ValueError(
                    f"mesh.source_size_mm[{source_id!r}] must be finite and positive"
                )
            size = float(raw_size)
            if not math.isfinite(size) or size <= 0.0:
                raise ValueError(
                    f"mesh.source_size_mm[{source_id!r}] must be finite and positive"
                )
            if normalized_id in normalized:
                raise ValueError("mesh.source_size_mm keys must be unique")
            normalized[normalized_id] = size
        return normalized


_INGEST_ID = re.compile(r"^wgi_[0-9A-HJKMNP-TV-Z]{26}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


#: One passive-cardioid aperture naming convention per row, tried in order;
#: the first row with any member present in a source tag map wins.
#: ``PASSIVE_CARDIOID`` is the name the WGLink add-in authors today; the rest
#: keep every model tagged before the rename working unchanged.
PORT_APERTURE_NAME_GROUPS: tuple[tuple[str, ...], ...] = (
    ("PASSIVE_CARDIOID",),
    ("PASSIVE_CARDIOID_L", "PASSIVE_CARDIOID_R"),
    ("PORT_EXIT",),
    ("PORT_EXIT_L", "PORT_EXIT_R"),
    ("MID_PORT_EXIT_LEFT", "MID_PORT_EXIT_RIGHT"),
)


class ImportedGeometrySource(JobModel):
    type: Literal["imported"]
    required_features: list[Literal["native-source-contour-v1", "native-front-baffle-woofer-v1", "native-shared-horn-woofer-v1"]] = Field(default_factory=list)
    ingest_id: str
    manifest_sha256: str
    artifact_sha256: str
    drive_channels: list[DriveChannel] = Field(min_length=1)
    combine: ChannelCombineSpec | None = None
    drive_voltage_v: float = Field(default=2.83, gt=0, allow_inf_nan=False)
    rg_ohm: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    #: Amplifier ceiling, RMS volts of generator EMF, shared by every channel.
    #: ``None`` means the drivers are the only limit, which is what a solve
    #: that has never been told about an amplifier should assume.
    max_drive_voltage_v: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    mesh: ImportedMeshSizes
    acknowledged_findings: list[str] = Field(default_factory=list)
    skipped_source_ids: list[str] = Field(default_factory=list)
    exterior_only: bool = False
    # Passive-cardioid is deliberately additive to the imported-job wire. A
    # missing rear volume is the opt-in boundary, so old requests take the
    # exact pre-campaign solve path. Keep the model and BEM areas separate:
    # the former drives the chamber/port physics while the latter records the
    # physical aperture represented by the radiation matrix.
    passive_cardioid_rear_volume_l: float | None = Field(
        default=None, gt=0, allow_inf_nan=False
    )
    passive_cardioid_port_length_mm: float | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    model_port_area_m2: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    bem_port_area_m2: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    port_area_source: Literal["user", "bem_aperture"] | None = None
    passive_cardioid_foam_resistance_pa_s_m3: float | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    passive_cardioid_invert_port: bool = True
    passive_cardioid_coupled: bool = False

    @field_validator("ingest_id")
    @classmethod
    def validate_ingest_id(cls, value: str) -> str:
        if _INGEST_ID.fullmatch(value) is None:
            raise ValueError("ingest_id must be a wgi_ ULID")
        return value

    @field_validator("manifest_sha256", "artifact_sha256")
    @classmethod
    def validate_sha256(cls, value: str, info: Any) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError(f"{info.field_name} must be a sha256: digest")
        return value

    @field_validator("skipped_source_ids")
    @classmethod
    def validate_skipped_sources(cls, value: list[str]) -> list[str]:
        normalized = [source_id.strip() for source_id in value]
        if any(not source_id for source_id in normalized):
            raise ValueError("skipped_source_ids must not contain empty ids")
        if len(set(normalized)) != len(normalized):
            raise ValueError("skipped_source_ids must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_channels_and_sizes(self) -> "ImportedGeometrySource":
        if any(c.patch_weights is not None or c.physical_source_id is not None for c in self.drive_channels) and "native-source-contour-v1" not in self.required_features:
            raise ValueError("weighted/grouped patches require native-source-contour-v1 negotiation")
        channel_ids = [channel.id for channel in self.drive_channels]
        if len(set(channel_ids)) != len(channel_ids):
            raise ValueError("drive channel ids must be unique")

        driven: set[str] = set()
        duplicate_sources: set[str] = set()
        for channel in self.drive_channels:
            for source_id in channel.source_ids:
                if source_id in driven:
                    duplicate_sources.add(source_id)
                driven.add(source_id)
        if duplicate_sources:
            raise ValueError(
                "every driven source must appear in exactly one drive channel; "
                f"duplicates: {sorted(duplicate_sources)}"
            )
        skipped = set(self.skipped_source_ids)
        overlap = driven & skipped
        if overlap:
            raise ValueError(
                "skipped sources cannot also be driven: " + ", ".join(sorted(overlap))
            )
        sized = set(self.mesh.source_size_mm)
        if sized != driven:
            missing = sorted(driven - sized)
            extra = sorted(sized - driven)
            details = []
            if missing:
                details.append(f"missing {missing}")
            if extra:
                details.append(f"extra {extra}")
            raise ValueError(
                "mesh.source_size_mm must cover exactly every non-skipped driven source: "
                + "; ".join(details)
            )
        if self.combine is not None:
            known = set(channel_ids)
            unknown = [name for name in self.combine.members if name not in known]
            if unknown:
                raise ValueError(
                    f"combine members name unknown drive channels: {unknown}"
                )
            if self.combine.id in known:
                raise ValueError(
                    f"combine id {self.combine.id!r} collides with a drive channel id"
                )
        return self

    @model_validator(mode="after")
    def validate_passive_cardioid(self) -> "ImportedGeometrySource":
        enabled = self.passive_cardioid_rear_volume_l is not None
        # Provenance consistency is checked whether or not the campaign runs.
        # A disabled submission that still carries contradictory areas is a
        # request nobody has read correctly, and serializing it unchallenged
        # means the contradiction reappears the moment somebody enables it.
        if (
            self.port_area_source == "bem_aperture"
            and self.model_port_area_m2 is not None
            and self.bem_port_area_m2 is not None
            and not math.isclose(
                float(self.model_port_area_m2),
                float(self.bem_port_area_m2),
                rel_tol=1.0e-12,
                abs_tol=0.0,
            )
        ):
            raise ValueError(
                "port_area_source='bem_aperture' requires model_port_area_m2 "
                "to equal bem_port_area_m2"
            )
        if not enabled:
            # Returning early here used to accept every other cardioid field
            # and silently ignore it: the values were serialized into the job
            # and never read. Name them instead, so a half-filled form is a
            # refusal rather than a setting that quietly does nothing.
            stray = [
                name
                for name in (
                    "passive_cardioid_port_length_mm",
                    "model_port_area_m2",
                    "bem_port_area_m2",
                    "port_area_source",
                    "passive_cardioid_foam_resistance_pa_s_m3",
                )
                if getattr(self, name) is not None
            ]
            if self.passive_cardioid_coupled:
                stray.append("passive_cardioid_coupled")
            if not self.passive_cardioid_invert_port:
                stray.append("passive_cardioid_invert_port")
            if stray:
                raise ValueError(
                    "passive cardioid fields require passive_cardioid_rear_volume_l: "
                    + ", ".join(stray)
                )
            return self

        if self.passive_cardioid_coupled:
            # The coupled campaign adds a derived channel under this id, so a
            # user channel or combine sharing it would be overwritten.
            reserved_id = "passive_cardioid"
            collides = any(
                channel.id == reserved_id for channel in self.drive_channels
            ) or (self.combine is not None and self.combine.id == reserved_id)
            if collides:
                raise ValueError(
                    "channel id 'passive_cardioid' is reserved for coupled output"
                )

        required = {
            "passive_cardioid_port_length_mm": self.passive_cardioid_port_length_mm,
            "model_port_area_m2": self.model_port_area_m2,
            "bem_port_area_m2": self.bem_port_area_m2,
            "port_area_source": self.port_area_source,
            "passive_cardioid_foam_resistance_pa_s_m3": (
                self.passive_cardioid_foam_resistance_pa_s_m3
            ),
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(
                "passive cardioid requires " + ", ".join(missing)
            )
        if self.port_area_source == "bem_aperture" and not math.isclose(
            float(self.model_port_area_m2),
            float(self.bem_port_area_m2),
            rel_tol=1.0e-12,
            abs_tol=0.0,
        ):
            raise ValueError(
                "port_area_source='bem_aperture' requires model_port_area_m2 "
                "to equal bem_port_area_m2"
            )
        return self

    @property
    def passive_cardioid_enabled(self) -> bool:
        return self.passive_cardioid_rear_volume_l is not None


GeometrySource = Annotated[
    ParametricGeometrySource | ImportedGeometrySource,
    Field(discriminator="type"),
]


class SolveRequest(JobModel):
    geometry: GeometrySource
    options: SolveOptions = Field(default_factory=SolveOptions)
    label: str | None = None
    parent_job_id: str | None = None
    client_request_id: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Durable submission key: an identical replay returns the original job; "
            "the same key with a different normalized request is refused."
        ),
    )
    client_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def rewrite_legacy_parametric_wire(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        result = dict(value)
        legacy_fields = {
            name: result[name]
            for name in ("design", "design_revision", "design_snapshot")
            if name in result
        }
        if "geometry" in result and legacy_fields:
            raise ValueError(
                "geometry cannot be combined with legacy design/design_revision/design_snapshot fields"
            )
        if legacy_fields:
            if "design" not in legacy_fields:
                raise ValueError("legacy parametric requests require design")
            result["geometry"] = {"type": "parametric", **legacy_fields}
            for name in legacy_fields:
                result.pop(name, None)
        return result

    @model_validator(mode="after")
    def validate_combine_band(self) -> "SolveRequest":
        # A crossover outside the solved band would make the combine module
        # extrapolate the alignment measurement; refuse at submission per
        # CAD-LINK-PHASE3.md §3 instead of degrading at completion.
        geometry = self.geometry
        if not isinstance(geometry, ImportedGeometrySource) or geometry.combine is None:
            return self
        if self.options.frequencies_hz is not None:
            band = (self.options.frequencies_hz[0], self.options.frequencies_hz[-1])
        elif self.options.frequency_range is not None:
            band = (self.options.frequency_range[0], self.options.frequency_range[1])
        else:
            return self
        outside = [
            value
            for value in geometry.combine.corner_frequencies_hz()
            if value < band[0] or value > band[1]
        ]
        if outside:
            raise ValueError(
                f"combine crossovers_hz {outside} lie outside the solved band "
                f"[{band[0]:g}, {band[1]:g}] Hz"
            )
        return self

    @field_validator("label", "client_request_id")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("client_metadata")
    @classmethod
    def validate_client_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("client_metadata must contain finite JSON values") from exc
        if len(encoded) > 16 * 1024:
            raise ValueError("client_metadata must not exceed 16384 UTF-8 bytes")
        return value

    @model_validator(mode="after")
    def validate_design_sweep_controls(self) -> "SolveRequest":
        """Reject design sweep values that would otherwise silently default."""

        if isinstance(self.geometry, ImportedGeometrySource):
            generated = self.options.frequency_range is not None
            generated_count = self.options.num_frequencies is not None
            if self.options.frequencies_hz is None and not (generated and generated_count):
                raise ValueError(
                    "imported geometry requires an explicit sweep: frequencies_hz or "
                    "frequency_range together with num_frequencies"
                )
            return self

        root = self.geometry.design.root
        simulation = root.simulation

        def scalar(expr: Any, default: float, field: str) -> float:
            if expr is None:
                return default
            number = expr.constant_value()
            if number is None:
                raise ValueError(f"design.simulation.{field} must be a scalar number")
            return float(number)

        if (
            self.options.frequencies_hz is None
            and self.options.frequency_range is None
        ):
            start = scalar(simulation.f1, default_sweep_start_hz(), "f1")
            end = scalar(simulation.f2, 20_000.0, "f2")
            if start <= 0.0 or end <= start:
                raise ValueError(
                    "design simulation frequency bounds must be positive and increasing"
                )

        if self.options.frequencies_hz is None and self.options.num_frequencies is None:
            count = scalar(simulation.num_frequencies, 32.0, "num_frequencies")
            if not count.is_integer() or not 1 <= int(count) <= 401:
                raise ValueError(
                    "design.simulation.num_frequencies must be an integer from 1 to 401"
                )

        structural_controls = [
            (root.scale, "design.scale"),
            (root.mesh.quadrants, "design.mesh.quadrants"),
            (root.mesh.wall_thickness, "design.mesh.wall_thickness"),
            (root.mesh.max_edge, "design.mesh.max_edge"),
            (root.source.shape, "design.source.shape"),
            (
                root.enclosure.depth if root.enclosure is not None else None,
                "design.enclosure.depth",
            ),
        ]
        if root.source.velocity_convention in {None, "legacy"}:
            structural_controls.append(
                (root.source.velocity, "design.source.velocity")
            )
        for expression, field in structural_controls:
            if expression is not None and expression.constant_value() is None:
                raise ValueError(f"{field} must be a scalar number")
        return self

    @property
    def design(self) -> DesignConfig:
        """Return the parametric design; imported callers must use ``geometry``."""

        if not isinstance(self.geometry, ParametricGeometrySource):
            raise AttributeError(
                "geometry type 'imported' has no parametric design; use "
                "request.geometry (ImportedGeometrySource)"
            )
        return self.geometry.design

    @property
    def design_revision(self) -> int:
        return (
            self.geometry.design_revision
            if isinstance(self.geometry, ParametricGeometrySource)
            else 0
        )

    @property
    def design_snapshot(self) -> DesignSnapshot | None:
        return (
            self.geometry.design_snapshot
            if isinstance(self.geometry, ParametricGeometrySource)
            else None
        )


class CadApprovalsRequest(JobModel):
    preparation_id: str = Field(min_length=1)
    finding_ids: list[str] = Field(min_length=1)


class CadSolveAgainRequest(JobModel):
    setup_revision_id: str | None = None
    frame_axis: str | None = None
    approvals: CadApprovalsRequest | None = None
    submit: bool = True

    @field_validator("frame_axis")
    @classmethod
    def known_axis(cls, value: str | None) -> str | None:
        from server.cadlink.solver_frame import AXES as SOLVER_FRAME_AXES

        if value is not None and value not in SOLVER_FRAME_AXES:
            raise ValueError(f"frame_axis must be one of {', '.join(SOLVER_FRAME_AXES)}")
        return value

    def press(self) -> dict[str, Any]:
        return {
            "setup_revision_id": self.setup_revision_id,
            "frame_axis": self.frame_axis,
            "approve_preparation_id": self.approvals.preparation_id if self.approvals else None,
            "approve_finding_ids": tuple(self.approvals.finding_ids) if self.approvals else (),
            "submit": self.submit,
        }


class CadSolveRequest(CadSolveAgainRequest):
    # The manual prefix is separate from the delivery id's charset and bound.
    client_request_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
        description="Stable manual Solve id; an exact ingest replay recovers its original job.",
    )
    ingest_id: str = Field(min_length=1)
    label: str | None = None


class CadStateSnapshot(JobModel):
    document_name: str | None
    manifest_sha256: str | None
    artifact_sha256: str | None
    project_lineage_id: str | None


class CadStatePreparation(JobModel):
    ingest_id: str | None
    preparation_id: str
    blocking_finding_ids: list[str]
    report_sha256: str | None


class CadStateApproval(JobModel):
    preparation_id: str
    finding_id: str


class CadState(JobModel):
    """The CAD solve's state, derived entirely from this job's record."""

    operation_id: str | None
    state: str
    stage: str | None
    reason: str | None
    message: str | None
    job_id: str | None
    snapshot: CadStateSnapshot | None
    preparation: CadStatePreparation | None
    approvals: list[CadStateApproval]
    setup_defaults: bool
    frame_axis_automatic: str | None
    received_at: str | None
    updated_at: str | None


class SolveAccepted(JobModel):
    job_id: str
    client_request_id: str | None = None


class StopResponse(JobModel):
    message: str
    status: Literal["cancelled", "cancelling"]


#: ``preparing`` is a CAD solve WG has accepted but not yet bound to a request;
#: its stored config is a ``cad_intent``, not a ``SolveRequest``.
JobStatusName = Literal["preparing", "queued", "running", "complete", "error", "cancelled"]


class CadIdentityInstance(JobModel):
    """CAD-authored addresses for one linked placement in an imported run."""

    instance_id: str
    design_id: str | None = None
    body_object_ids: list[str] = Field(default_factory=list)
    assembly_from_link: list[list[float]]
    source_ids: list[str] = Field(default_factory=list)
    default_drive_channel_ids: list[str] = Field(default_factory=list)


class CadDriveChannelIdentity(JobModel):
    """The submitted channel address resolved against immutable return sources."""

    drive_channel_id: str
    source_ids: list[str]
    instance_ids: list[str] = Field(default_factory=list)


class CadIdentityProvenance(JobModel):
    """Versioned CAD placement/body/source/drive graph retained with a run."""

    schema_version: Literal[1]
    ingest_id: str
    selected_instance_id: str | None = None
    solver_anchor_instance_id: str | None = None
    instances: list[CadIdentityInstance] = Field(default_factory=list)
    drive_channels: list[CadDriveChannelIdentity]


class CadSource(JobModel):
    """Where an imported run came from, kept for the run archive.

    A CAD run used to be traceable only through the ingestion record, which is
    addressed by content and says nothing about which document a person opened.
    """

    ingest_id: str | None
    design_id: str | None
    lineage_id: str | None
    #: The folder this design's runs are archived under -- the name its
    #: ``.wglink`` bundle already owns, so a rename does not start a second one.
    archive_stem: str | None
    manifest_sha256: str | None
    transformed_geometry_hash: str | None = None
    solve_model_sha256: str | None = None
    document_name: str | None
    return_state_hash: str | None
    identity: CadIdentityProvenance | None = None
    #: The domain decision the run was submitted and solved under, as the job
    #: recorded it (``server/cadlink/domain_decision.py:decision_summary``):
    #: what was read, the planes solved, any reflection, and the frame. Null
    #: for a run saved before decisions existed.
    domain_decision: dict[str, Any] | None = None


class ChannelSolveExecution(JobModel):
    """Execution identity emitted for each solved channel."""

    accuracy: Literal["fast", "accurate"]
    engine: str
    formulation: str | None


class SolveExecution(ChannelSolveExecution):
    # The runtime adds this key only when a fallback occurred. Avoid inserting
    # a null key into responses for executions that never had a fallback.
    fallback_reason: str | None = Field(default=None, exclude_if=lambda value: value is None)


class JobItem(JobModel):
    id: str
    client_request_id: str | None = None
    client_metadata: dict[str, Any]
    #: Null for a CAD solve that is still being prepared or ended refused: a
    #: run number names a run, and it is assigned when the solve is queued.
    run_number: int | None
    parent_job_id: str | None
    status: JobStatusName
    progress: float
    stage: str | None
    stage_message: str | None
    created_at: str
    queued_at: str
    started_at: str | None
    completed_at: str | None
    config_summary: dict[str, Any]
    solve_options: SolveOptionsResponse
    solve_accuracy: Literal["fast", "accurate"] = "fast"
    solve_execution: SolveExecution | None
    channel_solve_executions: dict[str, ChannelSolveExecution]
    has_results: bool
    has_mesh_artifact: bool
    has_pressure_basis_artifact: bool = False
    pressure_basis_artifact_bytes: int | None = None
    # A completed job could claim cardioid success and then 404 on the
    # download, because a storage failure was only a server log line. These
    # three carry the artifact's real state to the client: whether it exists,
    # how big it is, and anything that went wrong persisting it.
    has_radiation_impedance_artifact: bool = False
    radiation_impedance_artifact_bytes: int | None = None
    persistence_warnings: list[str] = Field(default_factory=list)
    # Field-plane readiness distinguishes retained traces from jobs that must
    # be re-solved or use a solve mode unsupported by post-solve evaluation.
    field_plane_available: bool = False
    field_trace_bytes: int | None = None
    unavailable_reason: str | None = None
    label: str | None
    error_message: str | None
    cancellation_requested: bool
    mesh_stats: dict[str, Any] | None
    script_snapshot: dict[str, Any] | None
    design_availability: DesignAvailability
    design_revision: int
    polar_grid: dict[str, Any]
    rating: int | None
    exported_files: list[str]
    auto_export_completed_at: str | None
    auto_export_formats: dict[str, Any]
    #: When this run was written to the run archive. The job database can lose
    #: results (removal, or the optional 30-day cleanup); the archive survives.
    archived_at: str | None = None
    raw_results_file: str | None
    mesh_artifact_file: str | None
    results_discarded_at: str | None = None
    mesh_discarded_at: str | None = None
    log_tail: list[str]
    symmetry: dict[str, Any] = Field(default_factory=dict)
    solve_path: Literal["full-3d", "axisymmetric-meridian"] | None = None
    axisymmetric_eligibility_reasons: list[str] = Field(default_factory=list)
    solve_wall_time_seconds: float | None = None
    cad_source: CadSource | None = None
    #: The exact imported-geometry request persisted with a CAD run. This is
    #: deliberately a JSON object rather than today's ImportedGeometrySource:
    #: old rows must remain inspectable even if their accepted wire predates a
    #: later additive field or stricter validation rule.
    cad_setup: dict[str, Any] | None = None
    #: What the CAD operation that made this run resolved, kept on the job
    #: (``task_metadata.cad``): ``operation_id``, ``setup`` (``revision_id``,
    #: ``digest``, ``origin`` = ``wg_defaults`` or ``user``), ``frame`` (``axis``,
    #: ``provenance``, ``confirmed``, ``requirement``) and ``preparation``. Null
    #: for a run made without one.
    cad_provenance: dict[str, Any] | None = None
    #: State and preparation read from this job alone, without its delivery ledger.
    cad_state: CadState | None = None
    #: The CAD intent a ``preparing`` job holds instead of a request (``type`` is
    #: ``cad_intent``, with the operation, return and setup it was accepted
    #: with). Null for every job that has a request.
    cad_intent: dict[str, Any] | None = None


class JobStatusResponse(JobItem):
    updated_at: str
    message: str | None = None


class JobListResponse(JobModel):
    items: list[JobItem]
    total: int
    limit: int
    offset: int


class MetadataResponse(JobModel):
    status: Literal["ok"]


class ClearFailedResponse(JobModel):
    deleted: bool
    deleted_count: int
    deleted_ids: list[str]


class DeleteResponse(JobModel):
    deleted: Literal[True]
    job_id: str


class JobMetadataPatch(JobModel):
    """V1 task metadata retained by ``server/api/routes_simulation.py:253-277``."""

    label: str | None = None
    script_snapshot: dict[str, Any] | None = None
    rating: int | None = Field(default=None, ge=0, le=5)
    exported_files: list[str] | None = None
    auto_export_completed_at: str | None = None
    auto_export_formats: dict[str, Any] | None = None
    archived_at: str | None = None
    raw_results_file: str | None = None
    mesh_artifact_file: str | None = None

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("exported_files")
    @classmethod
    def normalize_exported_files(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @field_validator(
        "auto_export_completed_at", "archived_at", "raw_results_file", "mesh_artifact_file"
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


__all__ = [
    "CadApprovalsRequest",
    "CadSolveAgainRequest",
    "CadSolveRequest",
    "CadState",
    "CadStateApproval",
    "CadStatePreparation",
    "CadStateSnapshot",
    "ClearFailedResponse",
    "DeleteResponse",
    "DesignAvailability",
    "DesignSnapshot",
    "DriveChannel",
    "FieldPlaneRequest",
    "FieldPlaneResponseSpec",
    "FieldPlaneSpec",
    "ImportedGeometrySource",
    "ImportedMeshSizes",
    "JobItem",
    "JobListResponse",
    "JobMetadataPatch",
    "JobStatusResponse",
    "MetadataResponse",
    "PolarConfig",
    "ParametricGeometrySource",
    "SolveAccepted",
    "SolveOptions",
    "SolveRequest",
    "StopResponse",
]
