"""Driver wire contract shared by job requests and solver adapters."""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from .base import JobModel


class DriverSpec(JobModel):
    """Thiele-Small driver model for one drive channel, Hornresp units.

    The wire keeps Hornresp's units (Sd cm², Le/Le2 mH, Mmd/Mms g, Cms m/N,
    Vas L, Rms kg/s, Xmax mm) exactly as the Fusion add-in documents them;
    conversion to SI happens once, in ``server/solver/driver_lem.py``.
    """

    sd_cm2: float = Field(gt=0, allow_inf_nan=False)
    bl_t_m: float = Field(gt=0, allow_inf_nan=False)
    re_ohm: float = Field(gt=0, allow_inf_nan=False)
    le_mh: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    #: Semi-inductance (LR-2) branch: ``Le2 || Re2`` in series with Re+jwLe,
    #: which is what a measured voice coil actually does above a few hundred
    #: hertz. Optional, and both or neither -- ``bandpass.Driver`` refuses a
    #: half-stated pair, and refusing it here names the field in a 422 instead
    #: of failing the solve after the mesh.
    le2_mh: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    re2_ohm: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    mmd_g: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    mms_g: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    cms_m_per_n: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    vas_l: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    fs_hz: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    rms_kg_per_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    qms: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    xmax_mm: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    #: Rated continuous power for ONE driver, watts, as the datasheet quotes it
    #: (AES for the library's compression drivers). ``count`` drivers share the
    #: channel, so the channel's ceiling is ``power_w * count`` -- the same
    #: parallel reduction ``hornlab-sim`` applies to Re.
    power_w: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    #: Nominal impedance, ohms. Only ever a stated figure: it is what the power
    #: rating is quoted against, so it is the one honest divisor for turning a
    #: drive voltage into the watts a datasheet can be compared with.
    z_nom_ohm: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    count: int = Field(default=1, ge=1, le=64)
    rear_volume_l: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    #: A human name for this driver (e.g. from the driver library, or typed by
    #: hand), carried through to solve metadata so results can name it.
    label: str | None = Field(default=None, max_length=120)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_completeness(self) -> "DriverSpec":
        if (self.mmd_g is None) == (self.mms_g is None):
            raise ValueError("driver needs exactly one of mmd_g or mms_g")
        if self.cms_m_per_n is None and self.vas_l is None and self.fs_hz is None:
            raise ValueError("driver needs one of cms_m_per_n, vas_l, or fs_hz")
        if (self.le2_mh is None) != (self.re2_ohm is None):
            raise ValueError(
                "driver semi-inductance needs both le2_mh and re2_ohm, or neither"
            )
        return self
