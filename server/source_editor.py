"""Author source presets through the native canonical contour, without a solve."""

from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Annotated, Any, Literal
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from server.design.schema import DesignConfig
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, field_validator

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


class AssemblyDimensions(WireModel):
    width_mm: Number
    height_mm: Number
    depth_mm: Number
    front_z_mm: Number
    horn_xy_mm: tuple[Number, Number]
    horn_length_mm: Number | None = None
    mouth_radius_mm: Number | None = None
    woofer_xy_mm: tuple[Number, Number] = (0, 0)
    aperture_radius_mm: Number = 0


class PhasePlug(WireModel):
    id: Identity
    z0_mm: Number
    z1_mm: Number
    inner0_mm: Number
    outer0_mm: Number
    inner1_mm: Number
    outer1_mm: Number


class AssemblyRequest(WireModel):
    horn: SourceDocument
    woofer: SourceDocument | None = None
    horn_config: dict[str, Any] | None = None
    dimensions: AssemblyDimensions
    mesh_size_mm: Annotated[Number, Field(gt=0)] = 2
    phase_plugs: Annotated[list[PhasePlug], Field(max_length=8)] = Field(default_factory=list)
    passage_refinement: Literal[1, 2, 4] = 1

    @field_validator("passage_refinement", mode="before")
    @classmethod
    def exact_refinement(cls, value):
        if type(value) is not int or value not in (1, 2, 4):
            raise ValueError("passage refinement must be the integer 1, 2 or 4")
        return value


def native_assembly(body: AssemblyRequest):
    try:
        from hornlab_mesher.source_assembly import SourceAssembly, assembly_channels
        from hornlab_mesher.phase_plug import PhasePlug as NativePlug
    except ImportError as exc:
        raise HTTPException(
            503, "This mesher does not support the assembly editor's passage contract. Update the mesher."
        ) from exc
    horn, hf = native(body.horn)
    woofer, lf = native(body.woofer) if body.woofer is not None else (None, None)
    drives = [hf] + ([lf] if lf is not None else [])
    try:
        if body.horn_config is not None:
            from hornlab_mesher.general_horn import GeneralHornWall
            dimensions = body.dimensions.model_dump()
            if dimensions.pop("horn_length_mm") is not None or dimensions.pop("mouth_radius_mm") is not None:
                raise ValueError("general horn length and mouth radius derive from the resolved profile; omit both dimensions")
            model = SourceAssembly.attach(horn, body.horn_config, woofer=woofer,
                **dimensions, phase_plugs=tuple(NativePlug(**p.model_dump()) for p in body.phase_plugs))
        elif body.phase_plugs:
            model = SourceAssembly(
                horn,
                woofer,
                **body.dimensions.model_dump(),
                phase_plugs=tuple(NativePlug(**p.model_dump()) for p in body.phase_plugs),
            )
        else:
            model = SourceAssembly(horn, woofer, **body.dimensions.model_dump())
        return model, drives, assembly_channels(model, drives)
    except ImportError as exc:
        raise HTTPException(503, "This mesher does not support general horn attachment. Update the mesher.") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def native(document: SourceDocument):
    # Lazy imports keep an older installed mesher from breaking app startup.
    try:
        from hornlab_mesher.source_contour import ContourDrive, SourceContour
    except ImportError as exc:
        raise HTTPException(503, "This mesher does not support native source contours.") from exc
    try:
        contour = SourceContour.from_dict(document.contour.model_dump())
        drive = ContourDrive(
            document.drive.channel_id,
            tuple(document.drive.weights.items()),
            document.drive.motion,
        )
        drive.validate(contour)
        return contour, drive
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def canonical_document(contour, drive) -> dict[str, Any]:
    return {
        "contour": json.loads(json.dumps(contour.to_dict())),
        "drive": {
            "channel_id": drive.channel_id,
            "weights": dict(drive.weights),
            "motion": drive.motion,
        },
    }


def validated(document: SourceDocument) -> dict[str, Any]:
    contour, drive = native(document)
    return {
        "document": canonical_document(contour, drive),
        "geometry_sha256": contour.geometry_sha256,
        "excitation_sha256": drive.excitation_sha256,
        "meridian": {
            s.id: [contour.evaluate(i, j / 64) for j in range(65)]
            for i, s in enumerate(contour.segments)
        },
    }


class ExpandRequest(WireModel):
    kind: Literal["flat", "dome", "cone"]
    dimensions: dict[str, Number]
    physical_source_id: Identity = "diaphragm"
    rim_id: Identity = "source.rim"
    channel_id: Identity = "motor"
    motion: Literal["normal", "axial"] = "normal"


def expand(body: ExpandRequest) -> dict[str, Any]:
    try:
        from hornlab_mesher.source_contour import ContourDrive, cone, dome, flat
    except ImportError as exc:
        raise HTTPException(503, "This mesher does not support native source contours.") from exc
    try:
        contour = {"flat": flat, "dome": dome, "cone": cone}[body.kind](
            **body.dimensions, physical_source_id=body.physical_source_id, rim_id=body.rim_id
        )
        drive = ContourDrive(
            body.channel_id,
            tuple((s.id, 1) for s in contour.segments if s.role == "moving"),
            body.motion,
        )
        return validated(SourceDocument.model_validate(canonical_document(contour, drive)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


class SplitRequest(WireModel):
    document: SourceDocument
    segment_index: Annotated[StrictInt, Field(ge=0, le=255)]


def split(body: SplitRequest) -> dict[str, Any]:
    contour, drive = native(body.document)
    index = body.segment_index
    if index >= len(contour.segments) or len(contour.segments) == 256:
        raise HTTPException(422, "Choose an existing patch; at most 256 patches are supported.")
    recipe = canonical_document(contour, drive)
    r, z = contour.evaluate(index, 0.5)
    point_id, patch_id = "point." + uuid4().hex, "patch." + uuid4().hex
    left = recipe["contour"]["segments"][index]
    right = {**left, "id": patch_id, "start": point_id}
    left["end"] = point_id
    recipe["contour"]["points"].insert(index + 1, {"id": point_id, "r_mm": r, "z_mm": z})
    recipe["contour"]["segments"].insert(index + 1, right)
    if left["role"] == "moving":
        recipe["drive"]["weights"][patch_id] = recipe["drive"]["weights"][left["id"]]
    return validated(SourceDocument.model_validate(recipe))


class PresetWrite(WireModel):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    document: SourceDocument
    expected_revision: str | None = None


class PresetStore:
    """One process-owned library; immutable revisions reject stale replacements."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("oversized library")
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if value["version"] != 1 or not isinstance(value["presets"], dict):
                raise ValueError("unknown library version")
            if len(value["presets"]) > 128:
                raise ValueError("oversized library")
            for key, item in value["presets"].items():
                if (
                    set(item) != {"id", "revision", "name", "document"}
                    or item["id"] != key
                    or not isinstance(item["revision"], str)
                    or not isinstance(item["name"], str)
                ):
                    raise ValueError("malformed saved preset")
                SourceDocument.model_validate(item["document"])
            return value["presets"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(
                500, "Source preset library is unreadable; recover it before editing."
            ) from exc

    def _write(self, presets):
        raw = json.dumps({"version": 1, "presets": presets}, allow_nan=False).encode()
        if len(raw) > 8 * 1024 * 1024:
            raise HTTPException(422, "Source preset library exceeds 8 MiB.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".source-presets-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        except OSError as exc:
            raise HTTPException(
                503,
                "Source preset could not be persisted. Try again after restoring storage access.",
            ) from exc
        finally:
            Path(name).unlink(missing_ok=True)

    def list(self):
        with self.lock:
            return list(self._read().values())

    def put(self, body: PresetWrite, preset_id: str | None = None):
        result = validated(body.document)
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "Name the preset.")
        with self.lock:
            values = self._read()
            if preset_id is not None:
                previous = values.get(preset_id)
                if previous is None:
                    raise HTTPException(404, "Preset no longer exists.")
                if body.expected_revision != previous["revision"]:
                    raise HTTPException(409, "Preset changed. Reload it before replacing.")
            elif body.expected_revision is not None:
                raise HTTPException(422, "A new preset cannot replace a revision.")
            elif len(values) >= 128:
                raise HTTPException(422, "At most 128 saved source presets are supported.")
            preset_id = preset_id or uuid4().hex
            item = {
                "id": preset_id,
                "revision": uuid4().hex,
                "name": name,
                "document": result["document"],
            }
            values[preset_id] = item
            self._write(values)
            return item

    def delete(self, preset_id: str, revision: str):
        with self.lock:
            values = self._read()
            if preset_id not in values:
                raise HTTPException(404, "Preset no longer exists.")
            if values[preset_id]["revision"] != revision:
                raise HTTPException(409, "Preset changed. Reload it before deleting.")
            del values[preset_id]
            self._write(values)


class Attachment(WireModel):
    kind: Literal["baffle", "horn"]
    # Explicit independent dimensions; irrelevant fields cannot silently alter geometry.
    dimensions: dict[str, Number | tuple[Number, Number, Number]]


class ExportRequest(WireModel):
    document: SourceDocument
    attachment: Attachment
    mesh_size_mm: Annotated[Number, Field(gt=0)] = 2


def export_bundle(body: ExportRequest, root: Path) -> bytes:
    contour, drive = native(body.document)
    try:
        from hornlab_mesher.contour_artifact import export_contour
        from hornlab_mesher.front_baffle import FrontBaffle
        from hornlab_mesher.woofer_artifact import export_woofer
    except ImportError as exc:
        raise HTTPException(503, "This mesher does not support native source attachments.") from exc
    root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=root, prefix="source-editor-") as stage:
            destination = Path(stage) / "artifact"
            if body.attachment.kind == "baffle":
                baffle = FrontBaffle(**body.attachment.dimensions)
                export_woofer(contour, drive, baffle, destination, mesh_size_mm=body.mesh_size_mm)
            else:
                export_contour(
                    contour,
                    drive,
                    destination,
                    mesh_size_mm=body.mesh_size_mm,
                    **body.attachment.dimensions,
                )
            result = BytesIO()
            with ZipFile(result, "w", ZIP_DEFLATED) as archive:
                for path in sorted(destination.iterdir()):
                    archive.write(path, path.name)
            return result.getvalue()
    except (TypeError, ValueError) as exc:
        # Native subprocess diagnostics may include temporary host paths.
        detail = str(exc)
        if "export failed" in detail:
            detail = "Native geometry export failed. Check the contour and attachment clearances."
        raise HTTPException(422, detail) from exc


def create_router(data_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/api/source-editor", tags=["source-editor"])
    store = PresetStore(data_dir / "source_presets.json")
    export_lock = threading.Lock()

    @router.post("/assembly/horn-profile")
    def assembly_horn_profile_endpoint(design: DesignConfig) -> dict[str, Any]:
        """Copy the current horn profile onto the assembly's new enclosure.

        The action deliberately selects the full horn wall and the assembly
        enclosure, independently of the old design's source, shell and mesh
        symmetry. Profile/morph/guide/scale/axis changes remain authoritative.
        """
        try:
            from server.preview.translate import design_to_mesher_config
            from hornlab_mesher.general_horn import GeneralHornWall
            config = design_to_mesher_config(design)
            config["mode"] = "bare"
            config.pop("enclosure", None)
            config["mesh"]["wallThickness"] = 0
            config["mesh"]["quadrants"] = 1234
            config["mesh"]["verticalOffset"] = 0
            wall = GeneralHornWall.from_config(config)
            return {"horn_config": config, "throat_radius_mm": wall.throat_radius_mm,
                    "horn_length_mm": wall.length_mm, "mouth_radius_mm": wall.mouth_radius_mm}
        except ImportError as exc:
            raise HTTPException(503, "This mesher does not support general horn attachment. Update the mesher.") from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/assembly/validate")
    def assembly_validate_endpoint(body: AssemblyRequest) -> dict[str, Any]:
        model, _, channels = native_assembly(body)
        from hornlab_mesher.phase_plug import passage_contract, rigid_edges

        return {
            "recipe": model.to_dict(),
            "geometry_sha256": model.geometry_sha256,
            "channels": channels,
            "observation_origin_mm": [0, 0, model.front_z_mm],
            "source_origins_mm": {
                c.physical_source_id: list(origin) for c, origin, _ in model.parts
            },
            "passage_contract": passage_contract(model),
            "horn_section_mm": {
                **{
                    s.id: [list(model.horn.evaluate(i, j / 32)) for j in range(33)]
                    for i, s in enumerate(model.horn.segments)
                },
                **{role: [list(a), list(b)] for role, (a, b) in rigid_edges(model).items()},
                **({"horn-wall": model.horn_wall.evaluate([j / 256 for j in range(257)]).tolist()}
                   if model.horn_wall is not None else {}),
            },
        }

    @router.post(
        "/assembly/export",
        response_class=Response,
        responses={
            200: {
                "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}
            }
        },
    )
    def assembly_export_endpoint(body: AssemblyRequest) -> Response:
        model, drives, _ = native_assembly(body)
        try:
            from hornlab_mesher.assembly_artifact import export_assembly
        except ImportError as exc:
            raise HTTPException(
                503, "This mesher does not support native assembly export."
            ) from exc
        if not export_lock.acquire(blocking=False):
            raise HTTPException(
                409, "A source export is already running. Try again when it finishes."
            )
        try:
            root = data_dir / "tmp"
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=root, prefix="assembly-export-") as stage:
                destination = Path(stage) / "artifact"
                export_assembly(
                    model,
                    drives,
                    destination,
                    mesh_size_mm=body.mesh_size_mm,
                    passage_refinement=body.passage_refinement,
                )
                result = BytesIO()
                with ZipFile(result, "w", ZIP_DEFLATED) as bundle:
                    for name in ("source.json", "geometry.step", "preview.msh"):
                        bundle.write(destination / name, arcname=name)
                return Response(
                    result.getvalue(),
                    media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="native-assembly.zip"'},
                )
        except (TypeError, ValueError) as exc:
            detail = (
                "Native assembly could not be exported. Check clearances, density and resource limits."
                if "export failed" in str(exc)
                else str(exc)
            )
            raise HTTPException(422, detail) from exc
        finally:
            export_lock.release()

    @router.post("/assembly/ingest")
    def assembly_ingest_endpoint(body: AssemblyRequest, request: Request) -> dict[str, Any]:
        model, drives, _ = native_assembly(body)
        try:
            from hornlab_mesher.assembly_artifact import export_assembly
            from server.cadlink.native_source import ingest_native_source
        except ImportError as exc:
            raise HTTPException(
                503, "This mesher does not support shared horn and woofer geometry."
            ) from exc
        if not export_lock.acquire(blocking=False):
            raise HTTPException(
                409, "A source export is already running. Try again when it finishes."
            )
        try:
            root = data_dir / "tmp"
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=root, prefix="source-assembly-") as stage:
                destination = Path(stage) / "artifact"
                manifest = export_assembly(
                    model,
                    drives,
                    destination,
                    mesh_size_mm=body.mesh_size_mm,
                    passage_refinement=body.passage_refinement,
                )
                record = ingest_native_source(
                    destination,
                    store=request.app.state.cadlink_store,
                    data_dir=data_dir,
                    sizes={
                        "rigid_size_mm": body.mesh_size_mm,
                        "transition_mm": body.mesh_size_mm,
                        "source_size_mm": {
                            p["id"]: body.mesh_size_mm
                            for p in manifest["patches"]
                            if p["role"] == "moving"
                        },
                    },
                )
            geometry = {
                "type": "imported",
                "ingest_id": record["ingest_id"],
                "manifest_sha256": record["manifest_sha256"],
                "artifact_sha256": record["artifact_sha256"],
                "required_features": manifest["required_features"],
                "mesh": record["mesh_sizes"],
                "drive_channels": record["native_source"]["channels"],
            }
            return {
                "geometry": geometry,
                "ingestion": {
                    key: record[key]
                    for key in (
                        "ingest_id",
                        "created_at",
                        "return_id",
                        "acoustic_domain",
                        "scope",
                        "freshness",
                        "manifest_sha256",
                        "artifact_sha256",
                        "report_sha256",
                        "mesh_content_sha256",
                        "solve_model_sha256",
                        "sources",
                        "mesh_sizes",
                        "mesh",
                        "skipped_source_ids",
                        "findings",
                        "symmetry",
                        "healing",
                        "sizing_estimate",
                        "polar_grid_derivation",
                        "tag_map",
                        "native_source",
                        "identity",
                        "normalisation",
                        "domain_interpretation",
                    )
                },
                "native_source": record["native_source"],
                "mesh": record["mesh"]["stats"],
                "geometric_quality": record["mesh"]["geometric_quality"],
            }
        except (TypeError, ValueError) as exc:
            detail = str(exc)
            if "export failed" in detail or "ingestion failed" in detail:
                detail = "Native assembly could not be built. Check source clearances, mesh size and resource limits."
            raise HTTPException(422, detail) from exc
        finally:
            export_lock.release()

    @router.post("/validate")
    def validate_endpoint(body: SourceDocument) -> dict[str, Any]:
        return validated(body)

    @router.post("/expand")
    def expand_endpoint(body: ExpandRequest) -> dict[str, Any]:
        return expand(body)

    @router.post("/split")
    def split_endpoint(body: SplitRequest) -> dict[str, Any]:
        return split(body)

    @router.get("/presets")
    def list_endpoint() -> list[dict[str, Any]]:
        return store.list()

    @router.post("/presets")
    def create_endpoint(body: PresetWrite) -> dict[str, Any]:
        return store.put(body)

    @router.put("/presets/{preset_id}")
    def update_endpoint(preset_id: str, body: PresetWrite) -> dict[str, Any]:
        return store.put(body, preset_id)

    @router.delete("/presets/{preset_id}")
    def delete_endpoint(preset_id: str, revision: str) -> dict[str, bool]:
        store.delete(preset_id, revision)
        return {"deleted": True}

    @router.post(
        "/export",
        response_class=Response,
        responses={
            200: {
                "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}
            }
        },
    )
    def export_endpoint(body: ExportRequest) -> Response:
        # Bound concurrent isolated Gmsh builds rather than multiplying process budgets.
        if not export_lock.acquire(blocking=False):
            raise HTTPException(
                409, "A source export is already running. Try again when it finishes."
            )
        try:
            return Response(
                export_bundle(body, data_dir / "tmp"),
                media_type="application/zip",
                headers={"Content-Disposition": 'attachment; filename="native-source.zip"'},
            )
        finally:
            export_lock.release()

    return router


def mount_source_editor(application: FastAPI) -> None:
    application.include_router(create_router(Path(application.state.data_dir)))
