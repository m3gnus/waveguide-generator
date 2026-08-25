"""The driver library: CSV loading, indexing, search ranking, and routes.

CSV fixtures below use invented brand/model names (never a real
manufacturer's catalogue data) purely to exercise the alias table, the
kind/completeness classifiers, and the token-prefix search ranking from
CADLINK-CROSSOVER-DRIVERS.md §4.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from server.drivers.api import create_drivers_router, mount_drivers
from server.drivers.csv_loader import parse_numeric
from server.drivers.library import DriverLibrary
from server.drivers.paths import (
    DRIVER_LIBRARY_DIR_ENV,
    ensure_driver_library_dir,
    resolve_driver_library_dir,
)


def _write_csv(folder: Path, name: str, header: list[str], rows: list[list[str]]) -> Path:
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join(row))
    path = folder / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _routes(router):
    return {
        (route.path, tuple(sorted(route.methods - {"HEAD", "OPTIONS"}))): route
        for route in router.routes
    }


# --- numeric parsing --------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("8", 8.0), ("8.0", 8.0), ("8 Ohm", 8.0), ("  8  ", 8.0), ("", None), ("   ", None), (None, None)],
)
def test_parse_numeric_tolerates_units_and_blanks(raw: str | None, expected: float | None) -> None:
    assert parse_numeric(raw) == expected


def test_parse_numeric_never_invents_a_value_for_garbage() -> None:
    assert parse_numeric("n/a") is None
    assert parse_numeric("TBD") is None


# --- alias columns, blanks, extras ------------------------------------------


def test_alias_columns_case_insensitive_and_blanks_are_missing(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        "acme.csv",
        ["brand", "MODEL", "sd", "bl", "RE_OHM", "mms", "fs_hz", "xmax", "some_unknown"],
        [["Acme", "8LF", "300", "9.5", "6.0", "18", "", "5.5", "widget"]],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()
    hits = library.search(q="Acme 8LF", kind="all", z=None, limit=20)
    assert len(hits) == 1
    hit = hits[0]
    assert hit["brand"] == "Acme"
    assert hit["model"] == "8LF"
    assert hit["spec"]["sd_cm2"] == 300.0
    assert hit["spec"]["bl_t_m"] == 9.5
    assert hit["spec"]["re_ohm"] == 6.0
    assert hit["spec"]["mms_g"] == 18.0
    # Fs was blank: it must read as missing, never a guessed zero.
    assert "fs_hz" not in hit["spec"]
    assert hit["display"]["fs_hz"] is None

    detail = library.get(hit["id"])
    assert detail is not None
    assert detail["extras"] == {"some_unknown": "widget"}


def test_unknown_columns_are_kept_verbatim_as_extras(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        "extras.csv",
        ["Brand", "Model", "Magnet_Type", "Basket_Material"],
        [["Radian", "Sample1", "Ferrite", "Cast"]],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()
    hit = library.search(q="Radian Sample1", kind="all", z=None, limit=20)[0]
    detail = library.get(hit["id"])
    assert detail["extras"] == {"Magnet_Type": "Ferrite", "Basket_Material": "Cast"}
    # No spec fields at all: this row is catalogue-only.
    assert detail["spec"] == {}
    assert detail["completeness"] == "catalogue"


# --- impedance variants -----------------------------------------------------


def test_impedance_variants_group_into_one_driver(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        "variants.csv",
        ["Brand", "Model", "Z_ohm", "Sd_cm2", "Re_ohm"],
        [
            ["Vector", "10FX", "4", "300", "3.1"],
            ["Vector", "10FX", "8 Ohm", "300", "6.2"],
        ],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()
    hits = library.search(q="Vector 10FX", kind="all", z=None, limit=20)
    assert len(hits) == 1
    hit = hits[0]
    assert {v["z_ohm"] for v in hit["variants"]} == {4.0, 8.0}
    # The primary hit is the lowest impedance by default.
    assert hit["z_ohm"] == 4.0
    ids = {v["id"] for v in hit["variants"]}
    assert len(ids) == 2  # each variant keeps a distinct, addressable id

    for variant in hit["variants"]:
        detail = library.get(variant["id"])
        assert detail is not None
        assert detail["z_ohm"] == variant["z_ohm"]


def test_row_missing_brand_or_model_is_dropped(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        "bad.csv",
        ["Brand", "Model", "Sd_cm2"],
        [["", "Ghost", "300"], ["Ghost Co", "", "300"], ["Acme", "OK1", "300"]],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()
    hits = library.search(q="", kind="all", z=None, limit=100)
    assert [h["model"] for h in hits] == ["OK1"]


# --- kind classification ----------------------------------------------------


def test_kind_classification_lf_cd_and_unknown(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        "kinds.csv",
        [
            "Brand",
            "Model",
            "Size_in",
            "Throat_in",
            "Diameter_mm",
            "Sd_cm2",
            "XO_min_Hz",
        ],
        [
            # Size_in present -> lf.
            ["Acme", "LFOnly", "12", "", "", "", ""],
            # Throat_in present -> cd.
            ["Acme", "CDThroat", "", "1", "", "", ""],
            # Diameter_mm without Sd_cm2 -> cd.
            ["Acme", "CDDiaphragm", "", "", "44", "", ""],
            # Diameter_mm WITH Sd_cm2 -> the Sd rule wins: lf, not cd.
            ["Acme", "WooferWithDiameter", "", "", "44", "300", ""],
            # XO_min_Hz alone -> cd.
            ["Acme", "CDCrossoverOnly", "", "", "", "", "1200"],
            # None of the classifying columns -> unknown.
            ["Acme", "Mystery", "", "", "", "", ""],
        ],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()

    def kind_of(model: str) -> str:
        hits = library.search(q=f"Acme {model}", kind="all", z=None, limit=5)
        matching = [h for h in hits if h["model"] == model]
        assert len(matching) == 1
        return matching[0]["kind"]

    assert kind_of("LFOnly") == "lf"
    assert kind_of("CDThroat") == "cd"
    assert kind_of("CDDiaphragm") == "cd"
    assert kind_of("WooferWithDiameter") == "lf"
    assert kind_of("CDCrossoverOnly") == "cd"
    assert kind_of("Mystery") == "unknown"


# --- completeness ------------------------------------------------------------


def test_completeness_levels(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        "completeness.csv",
        ["Brand", "Model", "Sd_cm2", "Bl_Tm", "Re_ohm", "Mms_g", "Cms_mm_per_N", "Fs_Hz"],
        [
            # Full: sd, bl, re, one mass, one compliance source (cms here).
            ["Acme", "Full1", "300", "9.5", "6.0", "18", "300", ""],
            # Partial: only Sd and Re given -- some T/S data, not a full set.
            ["Acme", "Partial1", "300", "", "6.0", "", "", ""],
            # Catalogue: no T/S fields at all.
            ["Acme", "Catalogue1", "", "", "", "", "", ""],
        ],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()

    def completeness_of(model: str) -> str:
        hits = library.search(q=f"Acme {model}", kind="all", z=None, limit=5)
        matching = [h for h in hits if h["model"] == model]
        return matching[0]["completeness"]

    assert completeness_of("Full1") == "full"
    assert completeness_of("Partial1") == "partial"
    assert completeness_of("Catalogue1") == "catalogue"


def test_complete_filter_withholds_rows_that_cannot_drive_a_channel(tmp_path: Path) -> None:
    """``complete`` offers only what ``DriverSpec`` will accept.

    A partial or catalogue row is dropped on the way to the wire, so a channel
    filled in from one solves undriven -- no power, current or excursion. The
    picker asks for this filter so the choice cannot be made at all.
    """

    _write_csv(
        tmp_path,
        "mixed.csv",
        ["Brand", "Model", "Sd_cm2", "Bl_Tm", "Re_ohm", "Mms_g", "Fs_Hz", "Throat_in"],
        [
            ["Acme", "DrivableCD", "26", "12.4", "6.2", "2.4", "620", "1"],
            ["Acme", "CatalogueCD", "", "", "", "", "", "1"],
            ["Acme", "PartialCD", "26", "", "6.2", "", "", "1"],
        ],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()

    everything = library.search(q="Acme", kind="cd", z=None, limit=20)
    assert sorted(hit["model"] for hit in everything) == ["CatalogueCD", "DrivableCD", "PartialCD"]

    page = library.search_page(q="Acme", kind="cd", z=None, limit=20, complete=True)
    assert [hit["model"] for hit in page["items"]] == ["DrivableCD"]
    # The two it withheld are counted rather than silently absent: a search
    # that answers nothing has to be able to say why.
    assert page["hidden_incomplete"] == 2


def test_complete_filter_keeps_a_driver_for_its_usable_windings_only(tmp_path: Path) -> None:
    """A driver whose 8-ohm row has T/S and whose 16-ohm row does not.

    The record stays offered, but only through the winding that can be driven
    -- including in the ``variants`` list the picker turns into its winding
    buttons, so switching impedance cannot land on the empty row.
    """

    _write_csv(
        tmp_path,
        "windings.csv",
        ["Brand", "Model", "Z_ohm", "Sd_cm2", "Bl_Tm", "Re_ohm", "Mms_g", "Fs_Hz"],
        [
            ["Acme", "SplitLF", "8", "300", "9.5", "6.0", "18", "40"],
            ["Acme", "SplitLF", "16", "", "", "", "", ""],
        ],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()

    page = library.search_page(q="Acme SplitLF", kind="all", z=None, limit=5, complete=True)
    assert page["hidden_incomplete"] == 0
    hit = page["items"][0]
    assert hit["z_ohm"] == 8.0
    assert [variant["z_ohm"] for variant in hit["variants"]] == [8.0]
    # Unfiltered, both windings are still there.
    unfiltered = library.search(q="Acme SplitLF", kind="all", z=None, limit=5)[0]
    assert [variant["z_ohm"] for variant in unfiltered["variants"]] == [8.0, 16.0]


def test_get_complete_always_lists_the_winding_it_was_asked_for(tmp_path: Path) -> None:
    """An incomplete winding already on a channel keeps its own button.

    Dropping it from its own variant list would leave the sheet showing
    winding buttons with none of them selected.
    """

    _write_csv(
        tmp_path,
        "windings.csv",
        ["Brand", "Model", "Z_ohm", "Sd_cm2", "Bl_Tm", "Re_ohm", "Mms_g", "Fs_Hz"],
        [
            ["Acme", "SplitLF", "8", "300", "9.5", "6.0", "18", "40"],
            ["Acme", "SplitLF", "16", "", "", "", "", ""],
        ],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()

    detail = library.get("Acme::SplitLF::16", complete=True)
    assert detail is not None
    assert detail["z_ohm"] == 16.0
    assert [variant["z_ohm"] for variant in detail["variants"]] == [8.0, 16.0]

    usable = library.get("Acme::SplitLF::8", complete=True)
    assert usable is not None
    assert [variant["z_ohm"] for variant in usable["variants"]] == [8.0]


def test_router_search_reports_withheld_matches(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        "mixed.csv",
        ["Brand", "Model", "Sd_cm2", "Bl_Tm", "Re_ohm", "Mms_g", "Fs_Hz", "Throat_in"],
        [
            ["Acme", "DrivableCD", "26", "12.4", "6.2", "2.4", "620", "1"],
            ["Acme", "CatalogueCD", "", "", "", "", "", "1"],
        ],
    )
    library = DriverLibrary(tmp_path)
    router = create_drivers_router(library)
    search = _routes(router)[("/api/drivers", ("GET",))].endpoint

    unfiltered = asyncio.run(search(q="Acme", kind="cd", z=None, limit=20, complete=False))
    assert unfiltered["total"] == 2
    assert unfiltered["hidden_incomplete"] == 0

    filtered = asyncio.run(search(q="Acme", kind="cd", z=None, limit=20, complete=True))
    assert filtered["total"] == 1
    assert filtered["items"][0]["model"] == "DrivableCD"
    assert filtered["hidden_incomplete"] == 1


def test_cms_mm_per_n_converts_to_si_m_per_n(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        "cms.csv",
        ["Brand", "Model", "Sd_cm2", "Bl_Tm", "Re_ohm", "Mms_g", "Cms_mm_per_N"],
        [["Acme", "CmsCheck", "300", "9.5", "6.0", "18", "300"]],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()
    hit = library.search(q="Acme CmsCheck", kind="all", z=None, limit=5)[0]
    assert hit["spec"]["cms_m_per_n"] == pytest.approx(0.3)


def test_mmd_and_mms_never_both_emitted(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        "mass.csv",
        ["Brand", "Model", "Sd_cm2", "Bl_Tm", "Re_ohm", "Mms_g", "Mmd_g", "Fs_Hz"],
        [["Acme", "BothMasses", "300", "9.5", "6.0", "18", "16", "40"]],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()
    hit = library.search(q="Acme BothMasses", kind="all", z=None, limit=5)[0]
    assert hit["spec"]["mms_g"] == 18.0
    assert "mmd_g" not in hit["spec"]


# --- search ranking ----------------------------------------------------------


def _ranking_fixture(tmp_path: Path) -> DriverLibrary:
    _write_csv(
        tmp_path,
        "ranking.csv",
        ["Brand", "Model", "Z_ohm", "Sd_cm2"],
        [
            # Ampersand in the brand: "ab 12" must still find it (punctuation
            # is stripped before token comparison).
            ["A&B Acoustics", "12ND", "8", "500"],
            # A model number alone must find it by itself.
            ["Radian", "12ND76", "8", "500"],
            # Multi-word brand + a prefix of a longer model number.
            ["Vector Pro", "10FX500", "8", "300"],
            # A decoy that should not match any of the above queries.
            ["Other Co", "9XY", "8", "300"],
        ],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()
    return library


def test_search_matches_punctuation_stripped_brand_abbreviation(tmp_path: Path) -> None:
    library = _ranking_fixture(tmp_path)
    hits = library.search(q="ab 12", kind="all", z=None, limit=20)
    assert hits
    assert hits[0]["model"] == "12ND"


def test_search_matches_bare_model_number(tmp_path: Path) -> None:
    library = _ranking_fixture(tmp_path)
    hits = library.search(q="12nd76", kind="all", z=None, limit=20)
    assert hits
    assert hits[0]["model"] == "12ND76"


def test_search_matches_multiword_brand_and_model_prefix(tmp_path: Path) -> None:
    library = _ranking_fixture(tmp_path)
    hits = library.search(q="vector 10fx", kind="all", z=None, limit=20)
    assert hits
    assert hits[0]["model"] == "10FX500"


def test_search_ranks_impedance_match_above_others(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        "impedance_rank.csv",
        ["Brand", "Model", "Z_ohm", "Sd_cm2"],
        [
            ["Acme", "Twin4", "4", "300"],
            ["Acme", "Twin8", "8", "300"],
        ],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()
    hits = library.search(q="Acme Twin", kind="all", z=8.0, limit=20)
    assert [h["model"] for h in hits] == ["Twin8", "Twin4"]
    assert hits[0]["z_ohm"] == 8.0


def test_search_ranks_completeness_as_final_tiebreak(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        "completeness_rank.csv",
        ["Brand", "Model", "Sd_cm2", "Bl_Tm", "Re_ohm", "Mms_g", "Fs_Hz"],
        [
            ["Acme", "RankCatalogue", "", "", "", "", ""],
            ["Acme", "RankFull", "300", "9.5", "6.0", "18", "40"],
            ["Acme", "RankPartial", "300", "", "6.0", "", ""],
        ],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()
    hits = library.search(q="Acme Rank", kind="all", z=None, limit=20)
    assert [h["completeness"] for h in hits] == ["full", "partial", "catalogue"]


def test_search_kind_filter(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        "kind_filter.csv",
        ["Brand", "Model", "Size_in", "Throat_in"],
        [["Acme", "Woofer1", "12", ""], ["Acme", "Comp1", "", "1"]],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()
    lf_hits = library.search(q="Acme", kind="lf", z=None, limit=20)
    cd_hits = library.search(q="Acme", kind="cd", z=None, limit=20)
    assert [h["model"] for h in lf_hits] == ["Woofer1"]
    assert [h["model"] for h in cd_hits] == ["Comp1"]


def test_search_limit_is_respected(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        "many.csv",
        ["Brand", "Model", "Sd_cm2"],
        [["Acme", f"M{i}", "300"] for i in range(5)],
    )
    library = DriverLibrary(tmp_path)
    library.rescan()
    hits = library.search(q="Acme", kind="all", z=None, limit=3)
    assert len(hits) == 3


# --- mtime / file-set-triggered reindex --------------------------------------


def test_reindex_triggers_on_file_mtime_change(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, "live.csv", ["Brand", "Model", "Sd_cm2"], [["Acme", "V1", "300"]])
    library = DriverLibrary(tmp_path)
    assert [h["model"] for h in library.search(q="", kind="all", z=None, limit=20)] == ["V1"]

    # Touch the mtime forward and rewrite with different content; a plain
    # ensure_indexed() (no explicit rescan) must pick it up.
    time.sleep(0.01)
    _write_csv(tmp_path, "live.csv", ["Brand", "Model", "Sd_cm2"], [["Acme", "V2", "300"]])
    os.utime(path, (time.time() + 5, time.time() + 5))
    hits = library.search(q="", kind="all", z=None, limit=20)
    assert [h["model"] for h in hits] == ["V2"]


def test_reindex_triggers_on_new_file_added(tmp_path: Path) -> None:
    _write_csv(tmp_path, "first.csv", ["Brand", "Model", "Sd_cm2"], [["Acme", "First", "300"]])
    library = DriverLibrary(tmp_path)
    library.search(q="", kind="all", z=None, limit=20)  # force the first scan

    _write_csv(tmp_path, "second.csv", ["Brand", "Model", "Sd_cm2"], [["Acme", "Second", "300"]])
    hits = library.search(q="", kind="all", z=None, limit=20)
    assert {h["model"] for h in hits} == {"First", "Second"}


def test_rescan_reflects_a_removed_file(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, "gone.csv", ["Brand", "Model", "Sd_cm2"], [["Acme", "Gone", "300"]])
    library = DriverLibrary(tmp_path)
    library.rescan()
    path.unlink()
    library.rescan()
    assert library.search(q="", kind="all", z=None, limit=20) == []


# --- library info / rescan ----------------------------------------------------


def test_library_info_reports_files_counts_and_last_scan(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        "counts.csv",
        ["Brand", "Model", "Sd_cm2"],
        [["Acme", "A1", "300"], ["Acme", "A2", "300"]],
    )
    library = DriverLibrary(tmp_path)
    info = library.rescan()
    assert info["folder"] == str(tmp_path)
    assert info["files"] == [{"name": "counts.csv", "rows": 2}]
    assert info["total_drivers"] == 2
    assert info["last_scan"] is not None


def test_library_creates_its_folder_on_first_use(tmp_path: Path) -> None:
    folder = tmp_path / "not-yet-created" / "driver-databases"
    assert not folder.exists()
    library = DriverLibrary(folder)
    library.ensure_indexed()
    assert folder.is_dir()
    assert library.search(q="", kind="all", z=None, limit=20) == []


# --- id lookup / 404 ----------------------------------------------------------


def test_get_unknown_id_returns_none(tmp_path: Path) -> None:
    library = DriverLibrary(tmp_path)
    library.rescan()
    assert library.get("Nobody::Nothing::8") is None


def test_router_404s_for_an_unknown_id(tmp_path: Path) -> None:
    library = DriverLibrary(tmp_path)
    library.rescan()
    router = create_drivers_router(library)
    routes = _routes(router)
    get_driver = routes[("/api/drivers/{driver_id}", ("GET",))]
    with pytest.raises(Exception) as excinfo:
        asyncio.run(get_driver.endpoint(driver_id="Nobody::Nothing::8"))
    assert getattr(excinfo.value, "status_code", None) == 404


def test_router_search_and_get_round_trip(tmp_path: Path) -> None:
    _write_csv(
        tmp_path,
        "round_trip.csv",
        ["Brand", "Model", "Sd_cm2", "Bl_Tm", "Re_ohm", "Mms_g", "Fs_Hz"],
        [["Acme", "RoundTrip", "300", "9.5", "6.0", "18", "40"]],
    )
    library = DriverLibrary(tmp_path)
    router = create_drivers_router(library)
    routes = _routes(router)
    search = routes[("/api/drivers", ("GET",))]

    result = asyncio.run(search.endpoint(q="Acme RoundTrip", kind="all", z=None, limit=20))
    assert result["total"] == 1
    driver_id = result["items"][0]["id"]

    get_driver = routes[("/api/drivers/{driver_id}", ("GET",))]
    detail = asyncio.run(get_driver.endpoint(driver_id=driver_id))
    assert detail["model"] == "RoundTrip"
    assert detail["completeness"] == "full"


def test_router_library_and_rescan_routes(tmp_path: Path) -> None:
    _write_csv(tmp_path, "lib.csv", ["Brand", "Model", "Sd_cm2"], [["Acme", "Lib1", "300"]])
    library = DriverLibrary(tmp_path)
    router = create_drivers_router(library)
    routes = _routes(router)
    library_info = routes[("/api/drivers/library", ("GET",))]
    library_rescan = routes[("/api/drivers/library/rescan", ("POST",))]

    info = asyncio.run(library_info.endpoint())
    assert info["total_drivers"] == 1

    _write_csv(tmp_path, "lib2.csv", ["Brand", "Model", "Sd_cm2"], [["Acme", "Lib2", "300"]])
    rescanned = asyncio.run(library_rescan.endpoint())
    assert rescanned["total_drivers"] == 2


# --- library folder resolution -------------------------------------------------


def test_env_override_wins_for_the_library_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DRIVER_LIBRARY_DIR_ENV, str(tmp_path / "custom-library"))
    resolved = resolve_driver_library_dir()
    assert resolved == (tmp_path / "custom-library").resolve()


def test_explicit_override_wins_over_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DRIVER_LIBRARY_DIR_ENV, str(tmp_path / "from-env"))
    resolved = resolve_driver_library_dir(tmp_path / "from-arg")
    assert resolved == (tmp_path / "from-arg").resolve()


def test_resolve_does_not_create_the_folder(tmp_path: Path) -> None:
    target = tmp_path / "unmade"
    resolved = resolve_driver_library_dir(target)
    assert resolved == target.resolve()
    assert not target.exists()


def test_ensure_creates_the_folder(tmp_path: Path) -> None:
    target = tmp_path / "made-on-demand"
    resolved = ensure_driver_library_dir(target)
    assert resolved.is_dir()


def test_mount_drivers_does_not_touch_the_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Constructing an app for an unrelated test must never create a real
    user's driver library folder as a side effect."""

    from fastapi import FastAPI

    unused_target = tmp_path / "should-not-exist"
    monkeypatch.setenv(DRIVER_LIBRARY_DIR_ENV, str(unused_target))
    application = FastAPI()
    application.state.data_dir = str(tmp_path)
    library = mount_drivers(application)
    assert library.folder == unused_target.resolve()
    assert not unused_target.exists()


def test_full_app_wires_the_drivers_router(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from server.app import create_app

    monkeypatch.setenv(DRIVER_LIBRARY_DIR_ENV, str(tmp_path / "app-library"))
    application = create_app(data_dir=tmp_path / "data")
    paths = {route.path for route in application.routes}
    assert "/api/drivers" in paths
    assert "/api/drivers/library" in paths
    assert "/api/drivers/library/rescan" in paths
    assert "/api/drivers/{driver_id}" in paths


def test_search_matches_mid_word_fragments_below_prefix_hits(tmp_path: Path) -> None:
    # "ndl" is how a 12NDL76 is actually referred to; prefix-only matching
    # returned nothing for it. A substring hit ranks under a prefix hit.
    header = ["Brand", "Model", "Z_ohm", "Sd_cm2", "Bl_Tm", "Re_ohm", "Mms_g", "Fs_Hz", "Qms"]
    _write_csv(tmp_path, "drivers.csv", header, [
        ["Acme", "12NDL76", "8", "522", "19.7", "5.1", "64", "52", "6.1"],
        ["Acme", "NDL99", "8", "522", "19.7", "5.1", "64", "52", "6.1"],
        ["Acme", "12XW76", "8", "522", "19.7", "5.1", "64", "52", "6.1"],
    ])
    library = DriverLibrary(tmp_path)
    hits = library.search(q="ndl", kind="all", z=None, limit=10)
    assert [hit["model"] for hit in hits] == ["NDL99", "12NDL76"]
    assert library.search(q="12nd", kind="all", z=None, limit=10)[0]["model"] == "12NDL76"
