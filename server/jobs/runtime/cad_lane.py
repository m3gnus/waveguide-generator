"""CAD composition records, provenance, and imported submission validation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping

from server.jobs.models import (
    PORT_APERTURE_NAME_GROUPS,
    CadIdentityProvenance,
    ImportedGeometrySource,
    SolveRequest,
)
from server.solver.imported import (
    ImportedSymmetryUnsupportedError,
    imported_domain_planes,
    imported_symmetry_from_cut_planes,
)

from .imported_refusals import ImportedSolveRefusal


@dataclass(frozen=True)
class _ImportedSubmission:
    record: dict[str, Any]
    msh_text: str
    mesh_stats: dict[str, Any]
    symmetry_metadata: dict[str, Any]
    anchor_design_id: str | None
    anchor_lineage_id: str | None
    archive_stem: str | None
    document: dict[str, Any]
    identity: dict[str, Any] | None
    anchor_snapshot: dict[str, Any] | None


@dataclass(frozen=True)
class _ComposedJob:
    """A request that passed every submission check, with what its job stores of it."""

    #: The request after engine resolution: what the job's ``config_json`` holds.
    request: SolveRequest
    config_summary: dict[str, Any]
    script_snapshot: dict[str, Any] | None
    task_metadata: dict[str, Any]
    mesh_stats: dict[str, Any] | None
    mesh_artifact: str | None
    imported: _ImportedSubmission | None


async def _cad_authored_project(
    store: Any, record: Mapping[str, Any]
) -> tuple[str | None, str | None]:
    """The project of a return with no design to anchor to.

    Geometry authored in CAD has no WG design behind it, so its project is the
    Fusion document, claimed during ingestion. Without this the run carried no
    lineage at all and dropped out of the project history it belongs to, and
    its archive folder was derived a second time from the run's own label.
    """

    project = record.get("project")
    project = project if isinstance(project, Mapping) else {}
    lineage_id = str(project.get("lineage_id") or "") or None
    archive_stem = str(project.get("archive_stem") or "") or None
    if lineage_id is not None:
        return lineage_id, archive_stem
    # Returns ingested before projects existed carry no project block. The
    # claim is keyed on the document, so the document can still find it.
    native_id = str((record.get("document") or {}).get("native_id") or "")
    if not native_id:
        return None, None
    names = await asyncio.to_thread(store.get_lineage_for_cad_document, native_id)
    if not names:
        return None, None
    return (
        str(names.get("lineage_id") or "") or None,
        str(names.get("archive_stem") or "")
        or str(names.get("document_name") or "")
        or None,
    )


def _imported_record_sources(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    sources = record.get("sources")
    if not isinstance(sources, list) or not all(
        isinstance(source, Mapping) for source in sources
    ):
        raise ImportedSolveRefusal(
            "ingest_record_incomplete",
            "the ingestion record has no authoritative source inventory; re-ingest the CAD return",
        )
    return [dict(source) for source in sources]


def _cad_identity_provenance(
    record: Mapping[str, Any], geometry: ImportedGeometrySource
) -> dict[str, Any] | None:
    """Resolve submitted drive addresses against the immutable CAD graph.

    Old ingestion rows did not retain this graph and remain solvable, but a row
    that claims to carry identity must be internally complete.  In particular,
    WG never substitutes a design ID, body fingerprint, name, or source order
    for the CAD-authored placement ID.
    """

    raw_identity = record.get("identity")
    if raw_identity is None:
        return None
    if not isinstance(raw_identity, Mapping):
        raise ImportedSolveRefusal(
            "cad_identity_invalid",
            "the ingestion record's CAD identity graph is unreadable; re-ingest the CAD return",
        )
    version = raw_identity.get("schema_version")
    if version not in {None, 1}:
        raise ImportedSolveRefusal(
            "cad_identity_unsupported",
            f"the ingestion record uses unsupported CAD identity schema {version!r}; upgrade WG",
        )
    raw_instances = raw_identity.get("instances")
    if not isinstance(raw_instances, list) or not all(
        isinstance(instance, Mapping) for instance in raw_instances
    ):
        raise ImportedSolveRefusal(
            "cad_identity_invalid",
            "the ingestion record has no authoritative CAD instance inventory; re-ingest the CAD return",
        )

    instance_ids = [str(instance.get("instance_id") or "") for instance in raw_instances]
    if any(not instance_id for instance_id in instance_ids) or len(set(instance_ids)) != len(instance_ids):
        raise ImportedSolveRefusal(
            "cad_identity_invalid",
            "the ingestion record's CAD instance IDs are empty or duplicated; re-ingest the CAD return",
        )
    known_instances = set(instance_ids)
    for address in ("selected_instance_id", "solver_anchor_instance_id"):
        value = raw_identity.get(address)
        if value is not None and str(value) not in known_instances:
            raise ImportedSolveRefusal(
                "cad_identity_invalid",
                f"the ingestion record's {address} does not address one retained instance; re-ingest the CAD return",
            )

    sources = _imported_record_sources(record)
    source_by_id = {str(source.get("id") or ""): source for source in sources}
    inventory_owner: dict[str, str] = {}
    for instance in raw_instances:
        instance_id = str(instance["instance_id"])
        inventory_sources = instance.get("source_ids")
        if not isinstance(inventory_sources, list):
            raise ImportedSolveRefusal(
                "cad_identity_invalid",
                "the ingestion record's CAD source inventory is unreadable; re-ingest the CAD return",
            )
        for raw_source_id in inventory_sources:
            source_id = str(raw_source_id)
            if source_id not in source_by_id or source_id in inventory_owner:
                raise ImportedSolveRefusal(
                    "cad_identity_invalid",
                    "the ingestion record's CAD sources are unknown or assigned to multiple instances; re-ingest the CAD return",
                )
            inventory_owner[source_id] = instance_id

    drive_channels: list[dict[str, Any]] = []
    for channel in geometry.drive_channels:
        owners: set[str] = set()
        for source_id in channel.source_ids:
            source = source_by_id[source_id]
            declared_owner = source.get("instance_id")
            declared_owner = str(declared_owner) if declared_owner is not None else None
            inventory_instance = inventory_owner.get(source_id)
            if declared_owner is not None and declared_owner not in known_instances:
                raise ImportedSolveRefusal(
                    "cad_identity_invalid",
                    f"source {source_id!r} names unknown CAD instance {declared_owner!r}; re-ingest the CAD return",
                )
            if declared_owner != inventory_instance and (
                declared_owner is not None or inventory_instance is not None
            ):
                raise ImportedSolveRefusal(
                    "cad_identity_invalid",
                    f"source {source_id!r} contradicts the retained CAD instance inventory; re-ingest the CAD return",
                )
            owner = inventory_instance
            if owner is not None:
                owners.add(owner)
        drive_channels.append(
            {
                "drive_channel_id": channel.id,
                "source_ids": list(channel.source_ids),
                "instance_ids": sorted(owners),
            }
        )

    payload = {
        "schema_version": 1,
        "ingest_id": geometry.ingest_id,
        "selected_instance_id": raw_identity.get("selected_instance_id"),
        "solver_anchor_instance_id": raw_identity.get("solver_anchor_instance_id"),
        "instances": [dict(instance) for instance in raw_instances],
        "drive_channels": drive_channels,
    }
    try:
        return CadIdentityProvenance.model_validate(payload).model_dump(mode="json")
    except Exception as exc:
        raise ImportedSolveRefusal(
            "cad_identity_invalid",
            "the ingestion record's CAD identity graph is incomplete; re-ingest the CAD return",
            details={"validation_error": str(exc)},
        ) from exc


def _validate_passive_cardioid_topology(
    geometry: ImportedGeometrySource,
    record: Mapping[str, Any],
) -> None:
    """Reject a cardioid topology that the late solver backstop cannot couple."""

    if not geometry.passive_cardioid_enabled:
        return
    source_tags = record.get("source_tags")
    if not isinstance(source_tags, Mapping):
        raise ImportedSolveRefusal(
            "passive_cardioid_topology",
            "passive cardioid requires an ingestion source tag map",
        )
    by_upper = {
        str(source_id).strip().upper(): str(source_id)
        for source_id in source_tags
    }
    port_source_ids: list[str] = []
    for group in PORT_APERTURE_NAME_GROUPS:
        port_source_ids = [by_upper[name] for name in group if name in by_upper]
        if port_source_ids:
            break
    if not port_source_ids:
        raise ImportedSolveRefusal(
            "passive_cardioid_topology",
            "passive cardioid requires PASSIVE_CARDIOID or PORT_EXIT aperture sources",
        )

    mf_source_ids = [
        str(source.get("id") or "")
        for source in record.get("sources") or []
        if isinstance(source, Mapping)
        and str(source.get("role") or "").strip().upper() == "MF"
        and str(source.get("id") or "") in source_tags
    ]
    if not mf_source_ids and "MF" in by_upper:
        mf_source_ids = [by_upper["MF"]]
    if len(mf_source_ids) != 1:
        raise ImportedSolveRefusal(
            "passive_cardioid_topology",
            "passive cardioid requires exactly one MF diaphragm source",
        )
    if not geometry.passive_cardioid_coupled:
        return

    mf_source_id = mf_source_ids[0]
    mf_channels = [
        channel
        for channel in geometry.drive_channels
        if mf_source_id in channel.source_ids
    ]
    if len(mf_channels) != 1 or mf_channels[0].driver is None:
        raise ImportedSolveRefusal(
            "passive_cardioid_topology",
            "coupled passive cardioid requires the MF diaphragm's drive channel "
            "to carry one driver model",
        )
    port_channels = [
        channel
        for channel in geometry.drive_channels
        if set(port_source_ids).issubset(set(channel.source_ids))
    ]
    if len(port_channels) != 1:
        raise ImportedSolveRefusal(
            "passive_cardioid_topology",
            "coupled passive cardioid requires all PORT_EXIT patches in one drive channel",
        )


def _imported_symmetry_metadata(
    record: Mapping[str, Any], requested: str, ground_axis: str | None = None
) -> dict[str, Any]:
    """Resolve the domain the ingestion artifact actually describes.

    The planes read here are the **domain** planes -- what the solver must
    mirror -- not the subset WG's own cutter removed during preparation. A
    return the CAD author cut before exporting has nothing left for the cutter
    to take, so its ``cut_planes`` are empty while its domain is a half or a
    quarter; reading the cut list would resolve it to ``full`` and solve an open
    shell. Older records carry only ``cut_planes``, where the two are the same
    list.
    """

    cut_planes = list(imported_domain_planes(record))
    try:
        resolved = imported_symmetry_from_cut_planes(cut_planes)
    except ImportedSymmetryUnsupportedError as exc:
        raise ImportedSolveRefusal(
            "imported_symmetry_unsupported",
            str(exc),
            details={"cut_planes": cut_planes},
        ) from exc
    if requested not in {"auto", resolved.mode}:
        raise ImportedSolveRefusal(
            "imported_symmetry_mismatch",
            f"requested symmetry {requested!r} does not match the ingestion artifact's "
            f"actual cut planes {cut_planes} ({resolved.mode})",
            details={"requested": requested, "resolved": resolved.mode, "cut_planes": cut_planes},
        )
    # A reduced mesh is cut *on* its mirror plane and touches it; a model above
    # a rigid ground plane must not reach it. The two cannot name the same
    # plane. The design path subtracts the blocked plane before meshing
    # (``restrict_for_ground_plane``), but an imported mesh already exists and
    # cannot be re-cut, so the conflict is refused with the plane named instead
    # of being handed to an engine that would mirror into the floor.
    if ground_axis is not None and resolved.native_plane:
        from server.solver.ground_plane import GroundPlane

        blocked = GroundPlane(axis=ground_axis, height_m=0.0).blocked_symmetry_plane
        mirrored = set(str(resolved.native_plane).split("+"))
        if blocked in mirrored:
            raise ImportedSolveRefusal(
                "imported_symmetry_ground_plane_conflict",
                f"this CAD return is a {resolved.mode} domain mirrored on {blocked}, "
                f"and a {ground_axis}-axis ground plane bounds that same plane. A "
                "reduced domain touches its mirror plane and a grounded model must "
                "stand clear of the floor, so the two cannot be combined. Turn the "
                "ground plane off, or return the model whole from CAD.",
                details={
                    "resolved": resolved.mode,
                    "native_symmetry_plane": resolved.native_plane,
                    "ground_axis": ground_axis,
                    "blocked_symmetry_plane": blocked,
                    "cut_planes": cut_planes,
                },
            )
    return {
        "requested": requested,
        "resolved": resolved.mode,
        "resolved_quadrants": resolved.quadrants,
        "native_symmetry_plane": resolved.native_plane,
        "cut_planes": cut_planes,
        "source": "cad-ingestion-domain-planes",
    }


def _validate_imported_polar_grid(
    geometry: ImportedGeometrySource,
    request: SolveRequest,
    record: Mapping[str, Any],
) -> None:
    del geometry
    derivation = record.get("polar_grid_derivation")
    derivation = derivation if isinstance(derivation, Mapping) else {}
    axes = derivation.get("axes")
    if not isinstance(axes, Mapping) or set(axes) != {
        "horizontal",
        "vertical",
        "diagonal",
    }:
        raise ImportedSolveRefusal(
            "ingest_record_incomplete",
            "ingestion polar derivation must contain horizontal, vertical, and diagonal axes; re-ingest the CAD return",
        )
    requested_start, requested_end, _count = request.options.polar_config.angle_range
    enabled_axes = set(request.options.polar_config.enabled_axes)
    for axis_name, raw_axis in axes.items():
        if not isinstance(raw_axis, Mapping):
            raise ImportedSolveRefusal(
                "ingest_record_incomplete",
                f"ingestion polar derivation axis {axis_name!r} is invalid; re-ingest the CAD return",
            )
        minimum = float(raw_axis.get("minimum_deg", -180.0))
        maximum = float(raw_axis.get("maximum_deg", 180.0))
        pinned = minimum <= -180.0 and maximum >= 180.0
        if pinned and axis_name not in enabled_axes:
            raise ImportedSolveRefusal(
                "polar_grid_narrowing",
                f"polar request disables pinned axis {axis_name!r}; ingestion requires it enabled over [-180, 180] degrees",
                details={
                    "axis": str(axis_name),
                    "required": [minimum, maximum],
                    "enabled_axes": sorted(enabled_axes),
                },
            )
        if requested_start > minimum or requested_end < maximum:
            raise ImportedSolveRefusal(
                "polar_grid_narrowing",
                f"polar request narrows pinned axis {axis_name!r}: ingestion requires "
                f"[{minimum:g}, {maximum:g}] degrees but request gives "
                f"[{requested_start:g}, {requested_end:g}]",
                details={
                    "axis": str(axis_name),
                    "required": [minimum, maximum],
                    "requested": [requested_start, requested_end],
                },
            )
