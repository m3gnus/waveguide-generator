"""Author source presets through the native canonical contour, without a solve."""

from __future__ import annotations

from server.preview.errors import user_error_message

from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
from typing import Annotated, Any, Literal
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, FastAPI, HTTPException, Response
from server.platform.json_file import json_bytes, staged_json, write_json_atomic
from pydantic import Field, StrictInt

from server.design.source_document import (
    Number, Identity, WireModel, Point as Point, Segment as Segment, Contour as Contour,
    Drive as Drive, SourceDocument, canonical_document as canonical_document,
)


def native(document: SourceDocument):
    try:
        from hornlab_mesher.source_contour import ContourDrive, SourceContour
    except ImportError as exc:
        raise HTTPException(503, user_error_message(exc)) from exc
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
        raise HTTPException(422, user_error_message(exc)) from exc


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
    from hornlab_mesher.source_contour import ContourDrive, cone, dome, flat
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
        raise HTTPException(422, user_error_message(exc)) from exc


class SplitRequest(WireModel):
    document: SourceDocument
    segment_index: Annotated[StrictInt, Field(ge=0, le=255)]


def split(body: SplitRequest) -> dict[str, Any]:
    contour, drive = native(body.document)
    index = body.segment_index
    if index >= len(contour.segments) or len(contour.segments) == 256:
        raise HTTPException(422, "Choose a patch from the list.")
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
    authoring: Any = None


class PresetStore:
    """One process-owned library; immutable revisions reject stale replacements."""

    AUTHORING_ENTRY_BYTES = 256 * 1024
    MAX_ENTRIES = 128

    def __init__(self, path: Path):
        self.path = path
        self.authoring_path = path.with_name("source_preset_authoring.json")
        self.recovery_path = path.with_name("source_preset_recovery.json")
        self.lock = threading.Lock()

    def _recover(self):
        """Finish a committed pair or restore the companion of the old library.

        The library's atomic replacement is the commit point. Keep the previous
        companion until that point, including across process interruption.
        """
        if not self.recovery_path.exists():
            return
        try:
            recovery = json.loads(self.recovery_path.read_text(encoding="utf-8"))
            if (set(recovery) != {"library", "authoring"}
                    or not isinstance(recovery["library"], str)
                    or recovery["authoring"] is not None and not isinstance(recovery["authoring"], str)):
                raise ValueError("malformed preset recovery")
            current = self.path.read_bytes() if self.path.exists() else None
            if current != recovery["library"].encode():
                previous = bytes.fromhex(recovery["authoring"]) if recovery["authoring"] is not None else None
                if previous is None:
                    self.authoring_path.unlink(missing_ok=True)
                elif not self.authoring_path.exists() or self.authoring_path.read_bytes() != previous:
                    write_json_atomic(self.authoring_path, previous, durable_directory=True)
            self.recovery_path.unlink()
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(500, "WG could not read the source preset library. Close WG, restore the library file, then reopen WG.") from exc
        except OSError as exc:
            raise HTTPException(503, "WG cannot reach the source preset library. Reconnect its drive, then try again.") from exc

    def _read(self):
        self._recover()
        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("oversized library")
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if value["version"] != 1 or not isinstance(value["presets"], dict):
                raise ValueError("unknown library version")
            if len(value["presets"]) > self.MAX_ENTRIES:
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
                500, "WG could not read the source preset library. Close WG, restore the library file, then reopen WG."
            ) from exc

    # The companion is optional, untrusted authoring data. It cannot make a
    # canonical library unreadable, including after an older app rewrites it.
    def _read_authoring(self, presets):
        try:
            if self.authoring_path.stat().st_size > self.MAX_ENTRIES * (self.AUTHORING_ENTRY_BYTES + 1024):
                return {}
            values = json.loads(self.authoring_path.read_text(encoding="utf-8"))
            if not isinstance(values, dict) or len(values) > self.MAX_ENTRIES:
                return {}
            return {
                key: entry for key, entry in values.items()
                if key in presets and isinstance(entry, dict)
                and entry.get("revision") == presets[key]["revision"]
                and "authoring" in entry
                and len(json.dumps(entry, allow_nan=False).encode()) <= self.AUTHORING_ENTRY_BYTES
            }
        except (OSError, ValueError, TypeError, RecursionError):
            return {}

    def _write(self, presets, authoring):
        library = json.dumps({"version": 1, "presets": presets}, allow_nan=False).encode()
        if len(library) > 8 * 1024 * 1024:
            raise HTTPException(422, "The source preset library is full. Delete a preset, then save again.")
        authoring = {key: entry for key, entry in authoring.items()
                     if key in presets and entry["revision"] == presets[key]["revision"]}
        if len(authoring) > self.MAX_ENTRIES:
            raise HTTPException(422, "The source preset library is full. Delete a preset, then save again.")
        try:
            self._recover()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            previous = self.authoring_path.read_bytes() if self.authoring_path.exists() else None
            companion = json_bytes(authoring, indent=None, allow_nan=False, trailing_newline=False)
            # Legacy saves need no companion write when there is no authoring.
            if previous == companion or previous is None and not authoring:
                write_json_atomic(self.path, library, durable_directory=True)
                return
            # Stage both files before recording the recoverable transaction.
            with staged_json(self.path, library, durable_directory=True) as publish_library:
                with staged_json(self.authoring_path, companion, durable_directory=True) as publish_authoring:
                    write_json_atomic(self.recovery_path, {
                        "library": library.decode(),
                        "authoring": previous.hex() if previous is not None else None,
                    }, durable_directory=True)
                    publish_authoring()
                    publish_library()
            # A cleanup failure cannot undo a committed pair. The next read
            # verifies the commit point before removing the recovery record.
            try:
                self.recovery_path.unlink()
            except OSError:
                pass
        except OSError as exc:
            self._recover()
            raise HTTPException(
                503,
                "WG could not save this preset and kept the previous one. Check that the disk has space, then try again.",
            ) from exc

    def list(self):
        with self.lock:
            presets = self._read()
            authoring = self._read_authoring(presets)
            return [{**item, **({"authoring": authoring[key]["authoring"]} if key in authoring else {})}
                    for key, item in presets.items()]

    def put(self, body: PresetWrite, preset_id: str | None = None):
        result = validated(body.document)
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "Name the preset.")
        with self.lock:
            values = self._read()
            authoring = self._read_authoring(values)
            if preset_id is not None:
                previous = values.get(preset_id)
                if previous is None:
                    raise HTTPException(404, "Preset no longer exists.")
                if body.expected_revision != previous["revision"]:
                    raise HTTPException(409, "Preset changed. Reload it before replacing.")
            elif body.expected_revision is not None:
                raise HTTPException(422, "This preset already exists. Save it under another name.")
            elif len(values) >= self.MAX_ENTRIES:
                raise HTTPException(422, "The source preset library is full. Delete a preset, then save again.")
            preset_id = preset_id or uuid4().hex
            item = {
                "id": preset_id,
                "revision": uuid4().hex,
                "name": name,
                "document": result["document"],
            }
            values[preset_id] = item
            authoring.pop(preset_id, None)
            if "authoring" in body.model_fields_set:
                entry = {"revision": item["revision"], "authoring": body.authoring}
                try:
                    entry_size = len(json.dumps(entry, allow_nan=False).encode())
                except (ValueError, TypeError, RecursionError) as exc:
                    raise HTTPException(422, "WG could not read this sketch. Undo your last change, then save again.") from exc
                if entry_size > self.AUTHORING_ENTRY_BYTES:
                    raise HTTPException(422, "This preset is too large to save. Simplify the sketch, then save again.")
                authoring[preset_id] = entry
            self._write(values, authoring)
            return {**item, **({"authoring": body.authoring} if preset_id in authoring else {})}

    def delete(self, preset_id: str, revision: str):
        with self.lock:
            values = self._read()
            authoring = self._read_authoring(values)
            if preset_id not in values:
                raise HTTPException(404, "Preset no longer exists.")
            if values[preset_id]["revision"] != revision:
                raise HTTPException(409, "Preset changed. Reload it before deleting.")
            del values[preset_id]
            self._write(values, authoring)


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
    from hornlab_mesher.contour_artifact import export_contour
    from hornlab_mesher.front_baffle import FrontBaffle
    from hornlab_mesher.woofer_artifact import export_woofer
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
        raise HTTPException(422, user_error_message(exc)) from exc


def create_router(data_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/api/source-editor", tags=["source-editor"])
    store = PresetStore(data_dir / "source_presets.json")
    export_lock = threading.Lock()

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
