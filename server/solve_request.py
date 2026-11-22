"""What a solve asks for: geometry sources, drive channels and the solve request.

Pure request and channel schemas shared by the job system, the solvers and run
provenance. They sit below all three (and above the design schema they embed);
the job system's response models stay in ``server/jobs/models.py``.
"""

from __future__ import annotations

import math
import re
from typing import Annotated, Any, Literal, Mapping, Self, cast

from pydantic import Field, SerializerFunctionWrapHandler, field_validator, model_serializer, model_validator

from server.contracts import DriverSpec, JobModel
from server.contracts.canonical import canonical_utf8_strict_json
from server.contracts.solve_options import SolveOptions
from server.contracts.source_roles import normalize_source_role
from server.design.defaults import default_sweep_start_hz
from server.design.schema import AssemblyWiringGroup, DesignConfig, validate_wiring_groups
from server.design.uid import DesignUid
from server.drivers.models import ExteriorDriverInputs


# Serializer return annotations intentionally remain absent: Pydantic uses them
# to replace the public serialization schema, hiding the model fields. The
# narrow no-untyped-def exceptions below preserve the existing wire contract.


def expand_legacy_channels(
    members: list[str],
    crossovers_hz: list[float],
    *,
    level_match: bool = True,
    align: bool = True,
) -> dict[str, dict[str, Any]]:
    """Expand the legacy LR4 chain into the per-channel wire form.

    The one definition of what a legacy spec means: LR4 pairs, auto gain when
    level matching is on and a manual 0 dB when it is off, auto delay when
    alignment is on and a manual 0 ms when it is off, polarity from the pair.
    """

    crossovers = [float(value) for value in crossovers_hz]
    if len(crossovers) != len(members) - 1:
        raise ValueError(
            "combine needs exactly one crossover between each adjacent member "
            f"pair: {len(members)} members require {len(members) - 1} crossovers_hz"
        )
    gain: dict[str, Any] = {"mode": "auto"} if level_match else {"mode": "manual", "db": 0.0}
    delay: dict[str, Any] = {"mode": "auto"} if align else {"mode": "manual", "ms": 0.0}
    expanded: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(members):
        expanded[name] = {
            "hp": (
                {"family": "lr", "order": 4, "fc_hz": crossovers[index - 1]}
                if index > 0
                else None
            ),
            "lp": (
                {"family": "lr", "order": 4, "fc_hz": crossovers[index]}
                if index < len(crossovers)
                else None
            ),
            "gain": dict(gain),
            "delay": dict(delay),
            "invert": None,
        }
    return expanded


class DesignSnapshot(JobModel):
    version: Literal[1] = 1
    design: DesignConfig


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


PositiveTransducerScalar = Annotated[float, Field(gt=0, strict=True, allow_inf_nan=False)]


class SemiInductanceSpec(JobModel):
    """Engine Thorborg–Futtrup model; Ke has units H/sqrt(s)."""

    enabled: bool = Field(default=False, strict=True)
    re_prime_ohm: PositiveTransducerScalar | None = None
    leb_h: PositiveTransducerScalar | None = None
    le_h: PositiveTransducerScalar | None = None
    ke_semi_h: PositiveTransducerScalar | None = None
    rss_ohm: PositiveTransducerScalar | None = None

    @model_validator(mode="after")
    def require_enabled_parameters(self) -> Self:
        if self.enabled and any(getattr(self, name) is None for name in type(self).model_fields if name != "enabled"):
            raise ValueError("enabled semi_inductance requires Re′, Leb, Le, Ke and Rss")
        return self

    @model_serializer(mode="wrap")
    def omit_absent_parameters(self, handler: SerializerFunctionWrapHandler):  # type: ignore[no-untyped-def]
        return {key: value for key, value in handler(self).items() if value is not None}


class LumpedSealedRearChamberSpec(JobModel):
    """Lossless sealed compliance; Sd is derived from the moving mesh faces."""

    enabled: bool = Field(default=False, strict=True)
    volume_m3: PositiveTransducerScalar | None = None

    @model_validator(mode="after")
    def require_enabled_volume(self) -> Self:
        if self.enabled and self.volume_m3 is None:
            raise ValueError("enabled lumped_sealed_rear_chamber requires volume_m3")
        return self

    @model_serializer(mode="wrap")
    def omit_absent_parameters(self, handler: SerializerFunctionWrapHandler):  # type: ignore[no-untyped-def]
        return {key: value for key, value in handler(self).items() if value is not None}


class ExteriorTransducerSpec(JobModel):
    """Opt-in BEAT bare-driver coordinate; SI scalars, global axis, RMS volts."""
    version: Literal[1, 2] = 1
    sd_m2: PositiveTransducerScalar | None = None
    power_w: PositiveTransducerScalar | None = None
    xmax_mm: PositiveTransducerScalar | None = None
    semi_inductance: SemiInductanceSpec | None = None
    lumped_sealed_rear_chamber: LumpedSealedRearChamberSpec | None = None
    re_ohm: float = Field(gt=0, strict=True, allow_inf_nan=False)
    le_h: float = Field(ge=0, strict=True, allow_inf_nan=False)
    bl_n_per_a: float = Field(gt=0, strict=True, allow_inf_nan=False)
    mmd_kg: float = Field(gt=0, strict=True, allow_inf_nan=False)
    cms_m_per_n: float = Field(gt=0, strict=True, allow_inf_nan=False)
    rms_n_s_per_m: float = Field(gt=0, strict=True, allow_inf_nan=False)
    motion_axis: tuple[Annotated[float, Field(strict=True, allow_inf_nan=False)],
                       Annotated[float, Field(strict=True, allow_inf_nan=False)],
                       Annotated[float, Field(strict=True, allow_inf_nan=False)]]

    @model_serializer(mode="wrap")
    def omit_absent_options(self, handler: SerializerFunctionWrapHandler):  # type: ignore[no-untyped-def]
        wire = handler(self)
        for name in ("semi_inductance", "lumped_sealed_rear_chamber", "power_w", "xmax_mm", "sd_m2"):
            if getattr(self, name) is None:
                wire.pop(name, None)
        return wire

    @model_validator(mode="after")
    def nonzero_axis(self) -> Self:
        if self.version == 1 and any(getattr(self, name) is not None for name in
                                     ("semi_inductance", "lumped_sealed_rear_chamber", "power_w", "xmax_mm", "sd_m2")):
            raise ValueError("transducer options require version 2")
        if not any(self.motion_axis):
            raise ValueError("transducer motion_axis must be nonzero")
        return self


class DriveChannel(JobModel):
    id: str = Field(min_length=1)
    source_ids: list[str]
    motion: Literal["normal", "axial"] = "normal"
    physical_source_id: str | None = Field(default=None, min_length=1)
    patch_weights: dict[str, float] | None = None
    driver: DriverSpec | None = None
    exterior_transducer: ExteriorTransducerSpec | None = None
    exterior_driver_inputs: ExteriorDriverInputs | None = None

    @model_serializer(mode="wrap")
    def omit_absent_transducer(self, handler: SerializerFunctionWrapHandler):  # type: ignore[no-untyped-def]
        wire = handler(self)
        if self.exterior_transducer is None:
            wire.pop("exterior_transducer", None)
        if self.exterior_driver_inputs is None:
            wire.pop("exterior_driver_inputs", None)
        if self.physical_source_id is None:
            wire.pop("physical_source_id", None)
        if self.patch_weights is None:
            wire.pop("patch_weights", None)
        return wire

    @model_validator(mode="after")
    def validate_patch_weights(self) -> "DriveChannel":
        if self.patch_weights is not None:
            if set(self.patch_weights) != set(self.source_ids):
                raise ValueError("patch_weights must cover exactly source_ids")
            if not all(math.isfinite(w) for w in self.patch_weights.values()):
                raise ValueError("patch_weights must be finite literal relative weights")
            if self.driver is not None or self.exterior_transducer is not None:
                raise ValueError("weighted prescribed patches cannot carry a driver model")
        return self

    @model_validator(mode="after")
    def validate_driver_applicability(self) -> "DriveChannel":
        if not self.source_ids and self.exterior_transducer is None:
            raise ValueError("a channel without painted sources requires an electrodynamic transducer")
        if self.exterior_transducer is not None:
            if self.driver is not None or self.motion != "axial":
                raise ValueError("an exterior transducer requires axial motion and no legacy driver model")
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
            channels = expand_legacy_channels(
                list(self.members),
                [float(value) for value in (self.crossovers_hz or [])],
                level_match=self.level_match,
                align=self.align,
            )
        return {"reference": self.resolved_reference, "channels": channels}


class ImportedMeshSizes(JobModel):
    """CAD sizes in mm; ``transition_mm`` is gmsh Threshold's DistMax distance.

    It is the distance over which source sizing grows to the wall size, not
    another target element size. The stored field name preserves setup digests.
    """

    rigid_size_mm: float = Field(gt=0, allow_inf_nan=False)
    transition_mm: float = Field(gt=0, allow_inf_nan=False)
    source_size_mm: dict[str, float]
    surface_groups: list[dict[str, Any]] | None = None

    @model_serializer(mode="wrap")
    def omit_legacy_surface_groups(self, handler: SerializerFunctionWrapHandler):  # type: ignore[no-untyped-def]
        wire = handler(self)
        if self.surface_groups is None:
            wire.pop("surface_groups", None)
        return wire

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


def passive_cardioid_apertures(
    source_tags: Mapping[str, Any],
    record: Mapping[str, Any],
) -> tuple[dict[str, list[int]], list[str], str]:
    """Resolve port and MF faces by canonical role, then legacy source-id names.

    Port keys retain their source ids; MF retains its historical display key
    unless that would collide with a port. The returned MF source id binds the
    diaphragm to its drive channel independently of the display key.
    """

    entries = [(str(name), int(tag)) for name, tag in source_tags.items()]
    by_upper = {name.strip().upper(): (name, tag) for name, tag in entries}

    sources = [
        (str(source.get("id") or ""), normalize_source_role(source.get("role") or ""))
        for source in record.get("sources") or [] if isinstance(source, Mapping)
    ]
    port_entries = [
        (source_id, int(source_tags[source_id]))
        for source_id, role in sources
        if role == "PASSIVE_CARDIOID" and source_id in source_tags
    ]
    if port_entries:
        name_order = {name: index for index, name in enumerate(name for group in PORT_APERTURE_NAME_GROUPS for name in group)}
        port_entries.sort(key=lambda entry: name_order.get(entry[0].strip().upper(), len(name_order)))
    if not port_entries:
        for group in PORT_APERTURE_NAME_GROUPS:
            port_entries = [by_upper[name] for name in group if name in by_upper]
            if port_entries:
                break
    if not port_entries:
        raise ValueError(
            "passive cardioid requires PASSIVE_CARDIOID or PORT_EXIT aperture sources"
        )

    mf_candidates: list[tuple[str, str, int]] = []
    for source_id, role in sources:
        if role != "MF":
            continue
        if source_id in source_tags:
            mf_candidates.append(("MF", source_id, int(source_tags[source_id])))
    if not mf_candidates and "MF" in by_upper:
        name, tag = by_upper["MF"]
        mf_candidates.append((name, name, tag))
    if len(mf_candidates) != 1:
        raise ValueError(
            "passive cardioid requires exactly one MF diaphragm source"
        )
    mf_name, mf_source_id, mf_tag = mf_candidates[0]

    port_tags = [tag for _name, tag in port_entries]
    if len(set(port_tags)) != len(port_tags) or mf_tag in port_tags:
        raise ValueError("passive cardioid aperture tag mapping contains duplicate faces")
    aperture_tags = {name: [tag] for name, tag in port_entries}
    if mf_name in aperture_tags:
        mf_name = mf_source_id
    aperture_tags[mf_name] = [mf_tag]
    return aperture_tags, [name for name, _tag in port_entries], mf_source_id


class MemberProcessing(JobModel):
    """One member's weight, delay and polarity, applied before its group's sum.

    Engineering convention: the member contributes
    ``weight * (-1 if invert) * exp(-j*2*pi*f*delay)`` times its own response.
    """

    weight: float = Field(default=1.0, ge=0, le=100, allow_inf_nan=False)
    delay_ms: float = Field(default=0.0, ge=0, le=1_000, allow_inf_nan=False)
    invert: bool = False


class DriverGroup(JobModel):
    """Physical channels sharing one crossover branch.

    Without ``member_processing`` the members also share one driver setting
    and motion. A signal group states each member's processing instead; its
    members may then differ in driver and motion, and are summed after their
    own weight, delay and polarity.
    """

    id: str = Field(min_length=1)
    channel_ids: list[str] = Field(min_length=1)
    label: str | None = Field(default=None, min_length=1)
    member_processing: dict[str, MemberProcessing] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def processing_names_members(self) -> "DriverGroup":
        if self.member_processing is not None and set(self.member_processing) != set(self.channel_ids):
            raise ValueError("member_processing must name exactly the group's channels")
        return self

    @field_validator("id", "label")
    @classmethod
    def nonblank_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("driver group names must not be blank")
        return value.strip() if value is not None else None

    @field_validator("channel_ids")
    @classmethod
    def unique_channels(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value) or len(set(value)) != len(value):
            raise ValueError("driver group channel ids must be distinct and nonempty")
        return value


class ImportedGeometrySource(JobModel):
    type: Literal["imported"]
    # Missing fields retain their original wire bytes and use the legacy
    # decoder below. Adapters state the convention for new requests.
    excitation_normalization: Literal["rms_velocity", "acceleration_legacy"] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    excitation_normalization_by_engine: dict[Literal["bempp"], Literal["rms_velocity", "acceleration_legacy"]] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    required_features: list[Literal["native-source-contour-v1", "native-front-baffle-woofer-v1", "native-shared-horn-woofer-v1", "native-phase-plug-passages-v1", "native-general-horn-attachment-v1", "wg-design-assembly-v1"]] = Field(default_factory=list)
    ingest_id: str
    manifest_sha256: str
    artifact_sha256: str
    drive_channels: list[DriveChannel]
    driver_groups: list[DriverGroup] | None = None
    wiring_groups: list[AssemblyWiringGroup] = Field(default_factory=list, exclude_if=lambda value: not value)
    combine: ChannelCombineSpec | None = None
    drive_voltage_v: float = Field(default=2.83, gt=0, allow_inf_nan=False)
    rg_ohm: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    #: Amplifier ceiling, RMS volts of generator EMF, shared by every channel.
    #: ``None`` means the drivers are the only limit, which is what a solve
    #: that has never been told about an amplifier should assume.
    max_drive_voltage_v: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    # Ready file meshes have no generation sizes. CAD admission still requires
    # these and verifies them against the prepared record.
    mesh: ImportedMeshSizes | None = None
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

    @model_serializer(mode="wrap")
    def omit_empty_native_features(self, handler: SerializerFunctionWrapHandler):  # type: ignore[no-untyped-def]
        wire = handler(self)
        if not self.required_features:
            wire.pop("required_features", None)
        if self.driver_groups is None:
            wire.pop("driver_groups", None)
        return wire

    def crossover_branches(self, members: list[str]) -> tuple[dict[str, list[str]], dict[str, str | None]]:
        """Resolve branches and labels, refusing a partial shared driver group."""
        groups = {group.id: group for group in self.driver_groups or []}
        for group in groups.values():
            if len(group.channel_ids) > 1 and set(group.channel_ids).intersection(members):
                raise ValueError(
                    "A crossover must use the whole driver group; select the group "
                    "instead of an individual channel."
                )
        return (
            {member: list(groups[member].channel_ids) if member in groups else [member] for member in members},
            {member: groups[member].label for member in members if member in groups},
        )

    def branch_processing(self, members: list[str]) -> dict[str, dict[str, Any]] | None:
        """Each physical channel's processing in these crossover branches, if any states it."""

        processing = {
            channel_id: value.model_dump(mode="json")
            for group in self.driver_groups or [] if group.id in members and group.member_processing
            for channel_id, value in group.member_processing.items()
        }
        return processing or None

    def normalization_for_engine(self, engine: str | None = None) -> str:
        """Decode old excitation conventions only when the request omits them.

        BEMPP historically left every producer on its acceleration basis;
        the other prescribed adapters converted design assemblies to RMS
        velocity. This is a replay decoder, never admission or capability.
        """
        by_engine = self.excitation_normalization_by_engine or {}
        if engine in by_engine:
            return by_engine[engine]
        if self.excitation_normalization is not None:
            return self.excitation_normalization
        return ("rms_velocity" if engine != "bempp" and "wg-design-assembly-v1" in self.required_features
                else "acceleration_legacy")

    @property
    def uses_rms_velocity(self) -> bool:
        return self.normalization_for_engine() == "rms_velocity"

    def physical_members(self, members: list[str]) -> list[str]:
        """Resolve crossover branches to the physical channels they contain."""
        branches, _labels = self.crossover_branches(members)
        return [channel for member in members for channel in branches[member]]

    @field_validator("ingest_id")
    @classmethod
    def validate_ingest_id(cls, value: str) -> str:
        if _INGEST_ID.fullmatch(value) is None:
            raise ValueError("WG cannot find this model. Send it again from Fusion.")
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
        if self.wiring_groups:
            if self.combine is not None and self.combine.id in {group.id for group in self.wiring_groups}:
                raise ValueError("The combined result and wiring networks need distinct ids; rename the combined result.")
            validate_wiring_groups(self.wiring_groups, {
                channel.id for channel in self.drive_channels if channel.exterior_transducer is not None
            }, {group.id: {name: (group.member_processing or {}).get(name, {}) for name in group.channel_ids}
                for group in self.driver_groups or []}, {channel.id for channel in self.drive_channels})
        if any(c.patch_weights is not None or c.physical_source_id is not None for c in self.drive_channels) and not {"native-source-contour-v1", "wg-design-assembly-v1"}.intersection(self.required_features):
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
        sized = set(self.mesh.source_size_mm) if self.mesh is not None else driven
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
        known = set(channel_ids)
        groups = self.driver_groups or []
        group_ids = [group.id for group in groups]
        if len(set(group_ids)) != len(group_ids):
            raise ValueError("driver group ids must be unique")
        grouped: set[str] = set()
        by_id = {channel.id: channel for channel in self.drive_channels}
        for group in groups:
            unknown = set(group.channel_ids) - known
            if unknown:
                raise ValueError(f"driver group names unknown drive channels: {sorted(unknown)}")
            if grouped.intersection(group.channel_ids):
                raise ValueError("drive channels may belong to only one driver group")
            grouped.update(group.channel_ids)
            if group.id in known and group.channel_ids != [group.id]:
                raise ValueError("driver group id collides with a drive channel id")
            channels = [by_id[channel_id] for channel_id in group.channel_ids]
            first = channels[0]
            if len(channels) > 1 and self.rg_ohm != 0:
                raise ValueError(
                    "Set generator resistance to 0 ohms or drive the surfaces separately "
                    "when a driver group contains multiple channels."
                )
            if group.member_processing is None and any(
                (channel.motion, channel.driver, channel.exterior_transducer)
                != (first.motion, first.driver, first.exterior_transducer) for channel in channels[1:]
            ):
                raise ValueError("driver group channels must have identical motion and drivers")
        if self.combine is not None:
            known_branches = known | set(group_ids)
            unknown_members = [name for name in self.combine.members if name not in known_branches]
            if unknown_members:
                raise ValueError(
                    f"combine members name unknown drive channels or groups: {unknown_members}"
                )
            physical = self.physical_members(self.combine.members)
            if len(physical) != len(set(physical)):
                raise ValueError("combine branches must not overlap physical drive channels")
            if self.combine.id in known_branches:
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
            cast(float, self.model_port_area_m2),
            cast(float, self.bem_port_area_m2),
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


class JobDesignIdentity(JobModel):
    # Document identity is separate from geometry and CadLink edit versions.
    # Omit absent identity fields in every encoding, including nested models.
    design_uid: DesignUid | None = Field(default=None, exclude_if=lambda value: value is None)
    design_name: str | None = Field(default=None, exclude_if=lambda value: value is None)


class SolveRequest(JobDesignIdentity):
    design_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
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
    def validate_exterior_transducer_mode(self) -> Self:
        geometry = self.geometry
        if not isinstance(geometry, ImportedGeometrySource) or not any(c.exterior_transducer for c in geometry.drive_channels):
            return self
        # The ingestion record determines whether a mixed system uses FEM.
        # Explicit exterior-only jobs retain their restrictions here. With the
        # default exterior_only=False, record admission applies those same
        # restrictions unless retained FEM regions establish a coupled job.
        # BEAT v3 supports prescribed velocity and voltage ports in one system,
        # with member processing and crossover over the retained pressure bases.
        if geometry.combine is not None and any(
            channel.gain.mode == "max" for channel in (geometry.combine.channels or {}).values()
        ):
            raise ValueError("maximum-output gain is not available with coupled exterior drivers")
        if (geometry.passive_cardioid_enabled or geometry.rg_ohm != 0
                or geometry.max_drive_voltage_v is not None or self.options.adaptive_frequency_sampling
                or self.options.mesh_ladder != "off" or self.options.ground_plane.enabled):
            raise ValueError("exterior transducer jobs do not yet support passive cardioid, generator resistance, ceilings, adaptive sampling, mesh ladders or ground")
        return self

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
            encoded = canonical_utf8_strict_json(value).encode("utf-8")
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
        if root.assembly is not None:
            raise ValueError("Solve a Sources and baffle assembly through its compiled model so every radiator is included.")
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


__all__ = [
    "ChannelCombineSpec",
    "ChannelFilterSpec",
    "DelaySpec",
    "DesignSnapshot",
    "DriveChannel",
    "DriverGroup",
    "ExteriorTransducerSpec",
    "FilterFamily",
    "FilterSpec",
    "GainSpec",
    "GeometrySource",
    "ImportedGeometrySource",
    "ImportedMeshSizes",
    "JobDesignIdentity",
    "LumpedSealedRearChamberSpec",
    "MemberProcessing",
    "PORT_APERTURE_NAME_GROUPS",
    "ParametricGeometrySource",
    "PositiveTransducerScalar",
    "SemiInductanceSpec",
    "SolveRequest",
    "expand_legacy_channels",
    "passive_cardioid_apertures",
]
