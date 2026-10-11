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
from server.tests._ath_contract import saved_v1

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
        "Morph.TargetShape = 1\nMorph.Width = 300\n",
        "ABEC.SimProfile = 1\n",
        "Mesh.SubdomainSlices = 5\nMesh.InterfaceOffset = 5\n",
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


def test_typo_fixture_is_refused_by_name_with_the_near_miss() -> None:
    with pytest.raises(TextConfigError, match=r"Coverage\.Angel \(did you mean Coverage\.Angle\?\)"):
        parse(fixture("typo"))


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
    assert parse(BASE + report + report).dialect == "ath"


def test_a_wg_stamped_file_keeps_native_passthrough_for_unsupported_keys() -> None:
    # A file WG wrote (geometry stamp, native header) is never refused on reopen.
    native = parse("; Parameter config\n" + BASE + "Scale.Z = 0.5\n")
    assert parse(serialize(native)).extra_keys["Scale.Z"] == "0.5"
    imported = parse(BASE + "Gmsh.Algorithm = 5\n")
    saved = serialize(imported)
    assert parse(saved).extra_keys["Gmsh.Algorithm"] == "5"
    # A stamped file keeps even a key an unstamped ATH file would be refused for.
    stamped = "; Waveguide Generator geometry-interpretation: native-v1\n" + BASE + "Coverage.Angel = 60\n"
    assert parse(stamped).extra_keys["Coverage.Angel"] == "60"
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
        "HornGeometry = 1\n",
        "HornGeometry = 2-1\n",
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
        ("HornGeometry = 1+1\n", "HornGeometry"),
        ("HornGeometry = foo\n", "HornGeometry"),
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
    assert parse(BASE + "Report = {\n Title = a\n}\nReport = {\n Title = b\n}\n").dialect == "ath"


@pytest.mark.parametrize("name", ["GridExport:a", "Report", "ABEC.Project"])
def test_repeated_ignored_blocks_import(name: str) -> None:
    text = BASE + f"{name} = {{\n x = 1\n}}\n{name} = {{\n y = 2\n}}\n"
    assert parse(text).dialect == "ath"


def test_repeated_empty_mesh_blocks_import_like_the_mesher() -> None:
    from hornlab_mesher.config_parser import parse_text_config

    text = BASE + "Mesh = {\n}\nMesh = {\n ; empty\n}\n"
    assert parse(text).dialect == "ath"
    assert parse_text_config(text)["formula"] == "OSSE"


@pytest.mark.parametrize("sections", [
    "Mesh = {\n AngularSegments = 32\n}\nMesh = {\n}\n",
    "Mesh = {\n}\nMesh = {\n AngularSegments = 32\n}\n",
    "Mesh = {\n}\nMesh = {\n AngularSegments = 32\n}\nMesh = {\n}\n",
    "OSSE = {\n L = 200\n}\n",
])
def test_repeated_blocks_that_would_lose_members_are_still_refused(sections: str) -> None:
    with pytest.raises(TextConfigError, match="more than once"):
        parse(BASE + sections)


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
    source = path.read_text(errors="replace")
    # Retain the original no-new-refusal assertions for saved v1 documents.
    try:
        parse(saved_v1(source))
    except TextConfigError as exc:
        # Files that are not designs, or that fail for older reasons, are not
        # this module's concern; only its own refusals count.
        assert "unsupported item" not in str(exc), f"{path.name}: {exc}"
        assert "appears more than once" not in str(exc), f"{path.name}: {exc}"
    # Fresh v2 documents also obey the mesher's bounded import refusals.
    from hornlab_mesher.config_parser import ConfigError, parse_text_config

    try:
        parse_text_config(source)
    except ConfigError as reference:
        with pytest.raises(TextConfigError) as actual:
            parse(source)
        if "Source.Contours" in str(reference):
            # WG diagnoses contour sources before the final mesher validation.
            assert "Source.Contours" in str(actual.value)
            assert "contour-drawn (dome) sources are not implemented" in str(actual.value)
        else:
            assert str(actual.value) == str(reference)
    else:
        parse(source)


RBASE = """R-OSSE = {
 R = 150
 a = 45
 r0 = 12.7
 a0 = 10
 k = 1
 m = 0.8
 b = 0.3
 r = 0.4
 q = 3.4
}
ABEC.SimType = 2
"""

# Keys from the ATH feature-gap study that WG used to drop without a word.
# None carries s1/s2, so the refusal does not depend on throat stretch.
GAP_STUDY_KEYS = {
    "Throat.Ext.Ctrl": "Throat.Ext.Ctrl = 1,2,3,4,5,6\n",
    "Throat.Ext.r0": "Throat.Ext.r0 = 5\n",
    "Throat.Ext.Included": "Throat.Ext.Included = 1\n",
    "Throat.Ext.Segments": "Throat.Ext.Segments = 4\n",
    "NAC": "NAC = 4\n",
    "Mesh.InterfaceDraw": "Mesh.InterfaceDraw = 1\n",
    "MEH": "MEH = {\n x = 1\n}\n",
    "ZFix": "ZFix = 3\n",
    "ZFix.Stretched": "ZFix.Stretched = 3\n",
    "Profile": "Profile = foo.txt\n",
    "Coverage.Angel": "Coverage.Angel = 60\n",
    "Throat.Profile": "Throat.Profile = 3\n",
    "Mesh.Enclosure.Plan": "Mesh.Enclosure = {\n Depth = 200\n Plan = x\n}\n",
    "Mesh.Enclosure.Dim": "Mesh.Enclosure = {\n Depth = 200\n Dim = 3,4\n}\n",
    "Source.Contours": "Source.Contours = {\n dome 1 2\n}\n",
    "SourceCover.Radius": "SourceCover.Radius = 3\n",
    "Source.DuctResolution": "Source.DuctResolution = 3\n",
    "Source.SaveAs": "Source.SaveAs = x\n",
    "Source.VelocityProfile": "Source.VelocityProfile = 2\n",
    "AxiMorph": "AxiMorph = {\n a = 1\n}\n",
    "Superslot": "Superslot = 1\n",
    "Mod": "Mod = {\n a = 1\n}\n",
    "EXAR": "EXAR = 1\n",
    "Vanes": "Vanes = {\n a = 1\n}\n",
    "WallImpedance": "WallImpedance = 1\n",
    "FusionRotaryProfile": "FusionRotaryProfile = x\n",
    "Mesh.MouthArc": "Mesh.MouthArc = 1\n",
    "Mesh.LFEnclosure": "Mesh.LFEnclosure = 1\n",
    "Mesh.RearCubic": "Mesh.RearCubic = 1\n",
    "Mesh.ThroatExtSegments": "Mesh.ThroatExtSegments = 3\n",
    "Mesh.ZMapElementSize": "Mesh.ZMapElementSize = 3\n",
    "Mesh.WG": "Mesh.WG = 0\n",
    "Mesh.OpusRear": "Mesh.OpusRear = 1\n",
    "Mesh.RoundedEdge": "Mesh.RoundedEdge = 1\n",
    "Tickler": "Tickler = 1\n",
    "System4": "System4.a = 1\n",
    "Scale.Z": "Scale.Z = 2\n",
    "HornGeometry": "HornGeometry = 2\n",
    "Horn.Adapter": "Horn.Adapter = 1\n",
    "Horn.Part": "Horn.Part:1 = {\n H = 1\n}\n",
    "Rollback": "Rollback = 1\nRollback.Angle = 90\n",
    "LFSource": "LFSource.Below = {\n a = 1\n}\n",
    "BEE": "BEE = {\n a = 1\n}\n",
    "Source.Array": "Source.Array = 3\n",
    "Mesh.Roundover": "Mesh.Roundover = {\n Radius = 5\n}\n",
    "Mesh.RearShape": "Mesh.RearShape = 3\n",
}


@pytest.mark.parametrize("family, base", [("OSSE", BASE), ("R-OSSE", RBASE)])
@pytest.mark.parametrize("key", sorted(GAP_STUDY_KEYS))
def test_every_gap_study_key_has_transport_or_a_named_refusal(family: str, base: str, key: str) -> None:
    # These three controls now have qualified v2 transport. Check their exact
    # payloads here; the unsupported family composition still refuses by name.
    if key in {"NAC", "Mesh.RearShape", "Mesh.Roundover"}:
        from server.preview.translate import design_to_mesher_config
        if key == "Mesh.Roundover" and family == "R-OSSE":
            with pytest.raises(TextConfigError, match="Mesh.Roundover with R-OSSE"):
                parse(base + GAP_STUDY_KEYS[key])
            return
        config = design_to_mesher_config(parse(base + GAP_STUDY_KEYS[key]).design)
        assert config["_textImportVersion"] == "ath-2026-09a-v2"
        if key == "NAC":
            assert config["_athNacPresent"] is True
        elif key == "Mesh.RearShape":
            assert config["mesh"]["athRearShape"] == 3
            assert config["mesh"]["athRearFrontOffset"] == 40
            assert config["mesh"]["athRearStep"] == 25
        else:
            assert config["mesh"]["athRoundover"] == {"radius": 5, "segments": 5}
        return
    with pytest.raises(TextConfigError, match="unsupported item") as caught:
        parse(base + GAP_STUDY_KEYS[key])
    assert key.split(".")[-1] in str(caught.value) or key in str(caught.value)


@pytest.mark.parametrize("value", ["0", "0*1", "2-2", "sin(0)"])
def test_rot_beside_r_osse_is_refused_unless_zero(value: str) -> None:
    with pytest.raises(TextConfigError, match="Rot"):
        parse(RBASE + "Rot = 10\n")
    assert parse(RBASE + f"Rot = {value}\n").dialect == "ath"
    assert parse(BASE + "Rot = 10\n").dialect == "ath"  # OSSE reads it


@pytest.mark.parametrize("value", ["1+1", "foo", "1/0", "1e309"])
def test_nonzero_or_invalid_unread_expressions_are_refused_by_name(value: str) -> None:
    with pytest.raises(TextConfigError, match="Rot"):
        parse(RBASE + f"Rot = {value}\n")


@pytest.mark.parametrize("key", ["Mesh.Algorithm", "Mesh.Binary", "Mesh.MshFileVersion", "Mesh.Wireframe"])
def test_mesh_output_only_keys_import(key: str) -> None:
    assert parse(BASE + f"{key} = 2\n").dialect == "ath"
    assert parse(BASE + "Mesh = {\n" + f" {key[5:]} = 2\n" + "}\n").dialect == "ath"


@pytest.mark.parametrize("stretch", ["", "s1 = 0.5\ns2 = 0.5\n"])
@pytest.mark.parametrize("value", ["0", "0*1"])
def test_disabled_rollback_members_import(value: str, stretch: str) -> None:
    text = BASE.replace(" L = 120\n", " L = 120\n" + stretch)
    text += f"Rollback = {value}\nRollback.Angle = 180\nRollback.StartAt = 0.8\n"
    parsed = parse(text)
    assert parsed.dialect == "ath"
    assert parsed.extra_keys["Rollback.Angle"] == "180"


@pytest.mark.parametrize("switch", ["", "Rollback = 1\n", "Rollback = 1+1\n", "Rollback = foo\n"])
def test_rollback_members_are_refused_unless_explicitly_disabled(switch: str) -> None:
    with pytest.raises(TextConfigError, match=r"Rollback\.Angle"):
        parse(BASE + switch + "Rollback.Angle = 180\n")


@pytest.mark.parametrize("key", ["Mesh.SubdomainSlices", "Mesh.InterfaceOffset"])
def test_abec_interface_keys_import_and_are_named_in_the_report(key: str) -> None:
    parsed = parse(BASE + f"{key} = 5\n")
    assert [item.key for item in parsed.ignored_settings] == [key]
    assert "ABEC subdomain interfaces" in parsed.ignored_settings[0].note


def test_unknown_morph_and_gcurve_keys_are_ignored_as_ath_ignores_them() -> None:
    parsed = parse(BASE + "Morph.Widht = 3\nGCurve.Widht = 3\n")
    assert {item.key for item in parsed.ignored_settings} == {"Morph.Widht", "GCurve.Widht"}


def test_refusals_do_not_depend_on_throat_stretch() -> None:
    assert "s1" not in BASE and "s2" not in BASE
    for key in ("Rollback", "LFSource", "Throat.Profile"):
        with pytest.raises(TextConfigError):
            parse(BASE + GAP_STUDY_KEYS[key])


def test_parked_unknown_items_and_known_ignorable_items_import() -> None:
    text = BASE + "_NAC = 4\n_Vanes = {\n a = 1\n}\nGmsh.Algorithm = 5\nOutput.STL = 1\nLE.Rg = 3\nReport = {\n Title = a\n}\n"
    assert parse(text).dialect == "ath"


@pytest.mark.parametrize("key", ["Mesh.ThroatSegments", "Mesh.ThroatSliceDensity", "Coverage.Mode"])
def test_keys_wg_stores_but_never_builds_are_refused_by_name(key: str) -> None:
    with pytest.raises(TextConfigError, match=key.replace(".", r"\.")):
        parse(BASE + f"{key} = 3\n")
    assert parse("; Parameter config\n" + BASE + f"{key} = 3\n").dialect == "mwg"
