"""Transport-independent directivity and field-plane observation contracts."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .base import JobModel


def _default_polar_axes() -> list[Literal["horizontal", "vertical", "diagonal"]]:
    return ["horizontal", "vertical", "diagonal"]


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
        default_factory=_default_polar_axes,
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
        return float(value[0]), float(value[1]), float(value[2])

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


# Keep the historical public identity for pickle lookup and Pydantic/FastAPI
# schema references. jobs.models re-exports these exact class objects.
for _model in (PolarConfig, FieldPlaneSpec, FieldPlaneResponseSpec, FieldPlaneRequest):
    _model.__module__ = "server.jobs.models"
    _model.model_rebuild(force=True)
del _model
