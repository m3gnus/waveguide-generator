"""REST surface for the driver library (CADLINK-CROSSOVER-DRIVERS.md §4)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query

from server.drivers.library import DriverLibrary
from server.drivers.models import (
    DriverDetail,
    DriverLibraryInfo,
    DriverSearchResponse,
)
from server.drivers.paths import resolve_driver_library_dir


def create_drivers_router(library: DriverLibrary) -> APIRouter:
    router = APIRouter(prefix="/api/drivers", tags=["drivers"])

    @router.get("", response_model=DriverSearchResponse)
    async def search_drivers(
        q: str = Query(default=""),
        kind: Literal["lf", "cd", "all"] = Query(default="all"),
        z: float | None = Query(default=None, gt=0),
        limit: int = Query(default=20, ge=1, le=100),
        complete: bool = Query(default=False),
    ) -> dict[str, object]:
        page = library.search_page(q=q, kind=kind, z=z, limit=limit, complete=complete)
        return {
            "items": page["items"],
            "total": len(page["items"]),
            "hidden_incomplete": page["hidden_incomplete"],
            "matches_by_kind": page["matches_by_kind"],
        }

    # Static paths ("/library", "/library/rescan") must be declared before the
    # dynamic "/{driver_id}" catch-all below, or FastAPI would try to resolve
    # "library" itself as a driver id.
    @router.get("/library", response_model=DriverLibraryInfo)
    async def library_info() -> dict[str, object]:
        library.ensure_indexed()
        return library.info()

    @router.post("/library/rescan", response_model=DriverLibraryInfo)
    async def library_rescan() -> dict[str, object]:
        return library.rescan()

    @router.get("/{driver_id}", response_model=DriverDetail)
    async def get_driver(
        driver_id: str,
        complete: bool = Query(default=False),
    ) -> dict[str, object]:
        record = library.get(driver_id, complete=complete)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown driver id {driver_id!r}")
        return record

    return router


def mount_drivers(application: FastAPI) -> DriverLibrary:
    """Attach the driver library router without touching the filesystem.

    The library folder is only resolved here; it is created and scanned on
    first real use (search, detail lookup, or an explicit rescan) so that
    constructing an app for an unrelated test never creates a real user's
    driver library folder on disk.
    """

    library = DriverLibrary(resolve_driver_library_dir())
    application.state.driver_library = library
    application.include_router(create_drivers_router(library))
    return library


__all__ = ["create_drivers_router", "mount_drivers"]
