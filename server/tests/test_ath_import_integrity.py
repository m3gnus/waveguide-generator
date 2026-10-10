"""ATH text import refuses what ATH applies and WG cannot build.

Fixtures in ``fixtures/ath_integrity`` are the configs run through ATH
(V2025-12, key rows repeated on V2026-09a) in the model-integrity triage.
Mouth radii quoted below are ATH's own ``profiles`` output for those configs.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from server.design.textcfg import TextConfigError, parse, serialize
from server.design_io.api import open_design
from server.preview.translate import design_to_mesher_config

FIXTURES = Path(__file__).parent / "fixtures" / "ath_integrity"

BASE = """OSSE = {
 L = 120
 r0 = 12.7
 a0 = 7
 k = 1.5
 a = 45
 s = 0.7
 n = 4
 q = 0.995
}
ABEC.SimType = 2
"""


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.cfg").read_text()


def mouth_radius(text: str) -> float:
    from hornlab_mesher.profile_formulas import calculate_osse

    profile = design_to_mesher_config(parse(text).design)["profile"]
    return calculate_osse(float(profile["L"]), 0.0, profile)[1]


UNSUPPORTED = {
    "Scale.Z": BASE + "Scale.Z = 0.5\n",
    "Horn.Part:1": BASE + "Horn.Part:1 = {\n L = 50\n}\n",
    "Throat.Ext.Ctrl": BASE + "Throat.Ext.Ctrl = 1\n",
    "Rollback": BASE + "Rollback.StartAt = 0.8\nRollback.Angle = 180\n",
    "Mesh.RearShape": BASE + "Mesh.RearShape = 2\n",
    "LFSource.Below": BASE + "LFSource.Below = {\n Spacing = 20\n Radius = 10\n}\n",
    "Source.Velocity.1": BASE + "Source.Velocity.1 = 1\n",
    "Source.Array": BASE + "Source = {\n Shape = 1\n Array = 3\n}\n",
    "BEE": BASE + "BEE = {\n x = 1\n}\n",
    "Mesh.Roundover": BASE + "Mesh = {\n Roundover = 10\n}\n",
    "Mesh.Enclosure.Plan": BASE + "Mesh.Enclosure = {\n Depth = 200\n Plan = bigbox\n}\n",
    "Mesh.Enclosure.Dim": BASE + "Mesh.Enclosure = {\n Depth = 200\n Dim = 300,400\n}\n",
    "Throat.Profile": BASE.replace(" q = 0.995\n", " q = 0.995\n Throat.Profile = 3\n"),
}


@pytest.mark.parametrize("key", sorted(UNSUPPORTED))
def test_unsupported_ath_items_are_refused_by_name_without_stretch(key: str) -> None:
    with pytest.raises(TextConfigError, match="unsupported item") as caught:
        parse(UNSUPPORTED[key])
    assert key.split(":")[0] in str(caught.value)


def test_the_scale_z_fixture_is_refused_through_the_open_endpoint() -> None:
    with pytest.raises(HTTPException) as caught:
        asyncio.run(open_design(fixture("scalez")))
    assert caught.value.status_code == 422
    assert "Scale.Z" in caught.value.detail["message"]


def test_parked_items_do_not_refuse() -> None:
    assert parse(BASE + "_Scale.Z = 0.5\n_BEE = {\n x = 1\n}\n").dialect == "ath"


@pytest.mark.parametrize(
    "extra",
    [
        "Coverage.Angel = 60\n",  # a typo: ATH ignores it too
        "Morph.TargetShape = 1\nMorph.Width = 300\n",
        "ABEC.SimProfile = 1\n",
        "Mesh.SubdomainSlices = 5\nMesh.InterfaceOffset = 5\n",
        "Source.VelocityProfile = 2\n",
        "Gmsh.Algorithm = 5\nMeshCmd = x\n",
    ],
)
def test_harmless_and_ignored_ath_keys_still_import(extra: str) -> None:
    assert parse(BASE + extra).dialect == "ath"


def test_native_wg_files_keep_their_passthrough_semantics() -> None:
    text = "; Parameter config\n" + BASE + "Scale.Z = 0.5\n"
    parsed = parse(text)
    assert parsed.dialect == "mwg"
    assert parsed.extra_keys["Scale.Z"] == "0.5"
    assert "Scale.Z = 0.5" in serialize(parsed)


def test_typo_fixture_imports_and_names_the_key_as_preserved() -> None:
    parsed = parse(fixture("typo"))
    assert "Coverage.Angel" in parsed.extra_keys


def test_a_repeated_block_is_refused_not_replaced() -> None:
    with pytest.raises(TextConfigError, match=r"block 'OSSE' appears more than once"):
        parse(fixture("repeated"))
    with pytest.raises(HTTPException):
        asyncio.run(open_design(fixture("repeated")))
    # The merged spelling ATH writes to config.txt imports, with ATH's radius.
    assert mouth_radius(fixture("merged")) == pytest.approx(83.743, abs=1e-3)


def test_a_parked_repeat_is_not_a_repeat() -> None:
    assert parse(BASE + "_OSSE = {\n L = 1\n}\n_OSSE = {\n L = 2\n}\n").dialect == "ath"


@pytest.mark.parametrize(("name", "key"), [("r_arcterm", "arcterm"), ("r_trunc", "trunc")])
def test_r_osse_arcterm_and_trunc_are_refused(name: str, key: str) -> None:
    for text in (fixture(name), fixture(name).replace("s1 = 0.5", "s1 = 0").replace("s2 = 0.05", "s2 = 0")):
        with pytest.raises(TextConfigError) as caught:
            parse(text)
        assert f"R-OSSE.{key}" in str(caught.value)


def test_r_osse_rot_stays_ignored_with_an_accurate_note() -> None:
    parsed = parse(fixture("r_rot"))
    assert parsed.design == parse(fixture("r_base")).design
    (item,) = parsed.ignored_settings
    assert item.key == "R-OSSE.Rot"
    assert "has no effect" in item.note
    assert "does not change the imported geometry" not in item.note


def test_an_omitted_term_s_defaults_to_aths_0_5() -> None:
    omitted = parse(fixture("second"))
    assert omitted.design.root.s.constant_value() == 0.5
    explicit = fixture("second").replace(" a = 30\n", " a = 30\n s = 0.5\n")
    assert mouth_radius(fixture("second")) == mouth_radius(explicit)
    # ATH: device width 144.74 mm, i.e. a mouth radius of 72.37 mm.
    assert mouth_radius(fixture("second")) == pytest.approx(72.369, abs=1e-3)
    assert mouth_radius(fixture("second").replace(" a = 30\n", " a = 30\n s = 0.7\n")) > 82.0


@pytest.mark.parametrize(
    "extra",
    [
        "Source.Contours = {\n dome 1 2\n}\n",
        "Source.Contours = dome\n",
        "Source = {\n Shape = 1\n Contours = dome\n}\n",
    ],
)
def test_source_contours_are_refused_by_name_without_stretch(extra: str) -> None:
    with pytest.raises(TextConfigError, match="Source.Contours"):
        parse(BASE + extra)


def test_a_top_level_trunc_is_ignored_as_ath_ignores_it() -> None:
    # ATH ignores a top-level trunc; only the R-OSSE block member applies.
    assert parse(BASE + "trunc = 41\n").dialect == "ath"
    with pytest.raises(TextConfigError, match=r"R-OSSE\.trunc"):
        parse(fixture("r_trunc"))


def test_native_files_keep_repeated_passthrough_blocks_byte_for_byte() -> None:
    report = "Report = {\n Title = a\n}\n"
    text = "; Parameter config\n" + BASE + report + report.replace("= a", "= b")
    parsed = parse(text)
    assert parsed.dialect == "mwg"
    assert serialize(parsed) == serialize(parse(serialize(parsed)))
    with pytest.raises(TextConfigError, match="more than once"):
        parse(BASE + report + report)


def test_a_wg_stamped_file_keeps_native_passthrough_for_unsupported_keys() -> None:
    # A file WG wrote (geometry stamp, native header) is never refused on reopen.
    native = parse("; Parameter config\n" + BASE + "Scale.Z = 0.5\n")
    assert parse(serialize(native)).extra_keys["Scale.Z"] == "0.5"
    imported = parse(BASE + "Coverage.Angel = 60\n")
    saved = serialize(imported)
    assert parse(saved).extra_keys["Coverage.Angel"] == "60"
    stamped = "; Waveguide Generator geometry-interpretation: native-v1\n" + BASE + "Scale.Z = 0.5\n"
    assert parse(stamped).extra_keys["Scale.Z"] == "0.5"


def test_top_level_enclosure_keys_build_the_enclosure_like_the_block() -> None:
    flat = parse(BASE + "Mesh.Enclosure.Depth = 200\nMesh.Enclosure.Spacing = 20,10,20,10\n")
    block = parse(BASE + "Mesh.Enclosure = {\n Depth = 200\n Spacing = 20,10,20,10\n}\n")
    assert flat.design.root.enclosure is not None
    assert flat.design.root.enclosure == block.design.root.enclosure
    assert not any(key.startswith("Mesh.Enclosure.") for key in flat.extra_keys)
    both = parse(BASE + "Mesh.Enclosure.Depth = 100\nMesh.Enclosure = {\n Depth = 200\n}\n")
    assert both.design.root.enclosure.depth.value == 200


@pytest.mark.parametrize("member", ["Plan", "Dim", "Bogus"])
def test_top_level_unsupported_enclosure_members_are_refused_by_name(member: str) -> None:
    with pytest.raises(TextConfigError, match=rf"Mesh\.Enclosure\.{member}"):
        parse(BASE + f"Mesh.Enclosure.Depth = 200\nMesh.Enclosure.{member} = 3\n")


def test_a_stamped_wg_file_keeps_flat_enclosure_keys_as_passthrough() -> None:
    # A file WG saved before flat Mesh.Enclosure.* keys were read must keep its
    # model: the stamp, not the new reader, decides the geometry.
    text = ("; Parameter config\n; Waveguide Generator geometry-interpretation: ath-2026-08c-v1\n"
            + BASE + "Mesh.Enclosure.Depth = 300\n")
    parsed = parse(text)
    assert parsed.design.root.enclosure is None
    assert parsed.extra_keys["Mesh.Enclosure.Depth"] == "300"


@pytest.mark.parametrize(
    "extra",
    [
        "Scale.Z = 1\n",
        "Scale.Z = 0.5+0.5\n",
        "Mesh.Roundover = 0\n",
        "Rollback = 0\n",
        "Mesh.RearShape = 1\n",
        "Mesh.RearShape = 3-2\n",
        "Throat.Profile = 1\n",
        "BEE = {\n}\n",
        "BEE = {\n _x = 1\n}\n",
        "LFSource.Below = {\n}\n",
    ],
)
def test_no_op_values_of_unsupported_items_still_import(extra: str) -> None:
    assert parse(BASE + extra).dialect == "ath"


@pytest.mark.parametrize(
    "extra, key",
    [
        ("Mesh.RearShape = 1+1\n", "Mesh.RearShape"),
        ("Scale.Z = 0.5+0.5+1\n", "Scale.Z"),
        ("Scale.Z = foo\n", "Scale.Z"),
        ("Mesh.Roundover = 2*5\n", "Mesh.Roundover"),
    ],
)
def test_expression_values_are_evaluated_before_the_check(extra: str, key: str) -> None:
    with pytest.raises(TextConfigError, match="unsupported item") as caught:
        parse(BASE + extra)
    assert key in str(caught.value)


def test_throat_profile_expression_is_evaluated() -> None:
    with pytest.raises(TextConfigError, match="Throat.Profile"):
        parse(BASE.replace(" q = 0.995\n", " q = 0.995\n Throat.Profile = 1+2\n"))
    assert parse(BASE.replace(" q = 0.995\n", " q = 0.995\n Throat.Profile = 0+1\n")).dialect == "ath"


def test_a_stamped_file_is_not_refused_for_a_repeated_block() -> None:
    stamp = "; Waveguide Generator geometry-interpretation: ath-2026-08c-v1\n"
    text = stamp + BASE + "Report = {\n Title = a\n}\nReport = {\n Title = b\n}\n"
    assert parse(text) is not None
    with pytest.raises(TextConfigError, match="more than once"):
        parse(BASE + "Report = {\n Title = a\n}\nReport = {\n Title = b\n}\n")


def _corpus_files() -> list[Path]:
    import os

    root = Path(__file__).resolve().parents[2]
    files = [root / "docs/validation/abec-g8-circsym-ib/g8_osse_circsym_ib.cfg",
             root / "docs/validation/abec-g8-circsym-ib/ath-config-as-run.txt",
             root / "server/tests/data/260308tritonia-q.txt"]
    # Optional corpora, skipped when absent: the mesher's ATH import fixtures
    # (sibling checkout), the v1 ATH fixtures and any extra directory, named in
    # HORNLAB_ATH_CORPUS (os.pathsep-separated), scanned recursively for *.cfg.
    files += sorted((root.parent / "hornlab-waveguide-mesher/tests/fixtures/ath_import").rglob("*.cfg"))
    for entry in filter(None, os.environ.get("HORNLAB_ATH_CORPUS", "").split(os.pathsep)):
        files += sorted(Path(entry).rglob("*.cfg"))
    return [path for path in files if path.is_file()]


@pytest.mark.parametrize("path", _corpus_files(), ids=lambda p: p.name)
def test_no_ath_corpus_file_is_refused_by_the_integrity_checks(path: Path) -> None:
    try:
        parse(path.read_text(errors="replace"))
    except TextConfigError as exc:
        # Files that are not designs, or that fail for older reasons, are not
        # this module's concern; only its own refusals count.
        assert "unsupported item" not in str(exc), f"{path.name}: {exc}"
        assert "appears more than once" not in str(exc), f"{path.name}: {exc}"
