"""ATH ``Mesh.Enclosure.LFSource.A``/``B`` import into the design assembly.

The design mesher's own LF reader is the parser; WG keeps its placement
relations, mints radiator identities and reports the mesher's normalisation.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from server.cadlink.intake.design_assembly import AssemblyBuildFence, compile_design_assembly
from server.cadlink.store import CadLinkStore
from server.design.textcfg import TextConfigError, parse, serialize
from server.preview.translate import design_to_mesher_config

HORN = """OSSE = {
 L = 120
 r0 = 12.7
 a0 = 7
 k = 1.5
 a = 45
 s = 0.7
 n = 4
 q = 0.995
}
"""
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def ath(enclosure: str, extra: str = "") -> str:
    return HORN + "Mesh.Enclosure = {\n" + enclosure + "}\n" + extra


def assembly_of(text: str) -> dict:
    return parse(text).design.model_dump(mode="json")["assembly"]


def notes(text: str) -> dict[str, str]:
    return {item.key: item.note for item in parse(text).ignored_profile}


def test_a_above_and_b_below_become_ath_cone_woofers_in_their_own_groups():
    assembly = assembly_of(ath(" Spacing = 20,250,20,250\n LFSource.A.Radius = 60\n LFSource.A.Spacing = 12\n"
                               " LFSource.B.Radius = 70\n LFSource.B.Resolution = 8\n"))
    a, b = assembly["woofers"]
    assert (a["label"], a["placement"], a["radius_mm"], a["resolution_mm"]) == ("LF A", {"anchor": "above", "gap_mm": 12.0}, 60.0, 10.0)
    assert (b["label"], b["placement"], b["radius_mm"], b["resolution_mm"]) == ("LF B", {"anchor": "below", "gap_mm": 10.0}, 70.0, 8.0)
    assert {w["shape"]["kind"] for w in assembly["woofers"]} == {"ath_cone"}
    assert all(UUID.match(w["id"]) for w in assembly["woofers"]) and a["id"] != b["id"]
    assert [[m["radiator_id"] for m in g["members"]] for g in assembly["signal_groups"]] == [["horn-throat"], [a["id"]], [b["id"]]]
    assert {w["ath"]["key"] for w in assembly["woofers"]} == {"A", "B"}


def test_a_zero_or_omitted_radius_is_no_woofer_and_is_reported():
    text = ath(" Spacing = 20,20,20,250\n LFSource.A.Spacing = 15\n LFSource.B.Radius = 0\n")
    assert parse(text).design.root.assembly is None
    reported = notes(text)
    assert "no woofer imported" in reported["Mesh.Enclosure.LFSource.A.Radius"]
    assert "no woofer imported" in reported["Mesh.Enclosure.LFSource.B.Radius"]


@pytest.mark.parametrize(("lines", "drive", "member", "note"), [
    ("", "prescribed", {}, "ATH treats an omitted weight as 0"),
    (" LFSource.B.DrivingWeight = 0\n", "rigid", {}, None),
    (" LFSource.B.DrivingWeight = 0.5\n", "prescribed", {"weight": 0.5}, None),
    (" LFSource.B.DrivingWeight = -1\n", "prescribed", {"invert": True}, None),
])
def test_driving_weight_defaults_to_driven_and_zero_stays_rigid(lines, drive, member, note):
    text = ath(" Spacing = 20,20,20,250\n LFSource.B.Radius = 70\n" + lines)
    assembly = assembly_of(text)
    woofer = assembly["woofers"][0]
    assert woofer["drive"] == drive
    group = assembly["signal_groups"][1]["members"][0]
    assert {key: value for key, value in group.items() if key != "radiator_id"} == member
    reported = notes(text).get("Mesh.Enclosure.LFSource.B.DrivingWeight")
    assert (note in reported) if note else reported is None


def test_sids_are_kept_when_positive_and_unique_and_otherwise_reported():
    text = ath(" Spacing = 20,250,20,250\n LFSource.A.Radius = 60\n LFSource.A.SID = 3\n"
               " LFSource.B.Radius = 60\n LFSource.B.SID = 3\n")
    a, b = assembly_of(text)["woofers"]
    assert a["ath"] == {"key": "A", "sid": 3} and b["ath"] == {"key": "B"}
    assert "already selects LFSource.A" in notes(text)["Mesh.Enclosure.LFSource.B.SID"]
    zero = ath(" Spacing = 20,20,20,250\n LFSource.B.Radius = 60\n LFSource.B.SID = 0\n")
    assert assembly_of(zero)["woofers"][0]["ath"] == {"key": "B"}
    assert "reserved for the waveguide" in notes(zero)["Mesh.Enclosure.LFSource.B.SID"]
    with pytest.raises(TextConfigError, match="nonnegative integer SID"):
        parse(ath(" LFSource.B.Radius = 60\n LFSource.B.SID = 1.5\n"))


def test_quarter_symmetry_keeps_b_in_the_full_domain_and_says_so():
    text = ath(" Spacing = 20,250,20,250\n LFSource.A.Radius = 60\n LFSource.B.Radius = 60\n", "Mesh.Quadrants = 1\n")
    parsed = parse(text)
    assert parsed.design.root.mesh.quadrants.constant_value() == 1234
    assert len(parsed.design.root.assembly.woofers) == 2
    assert "remain independent woofers in full domain" in notes(text)["Mesh.Quadrants"]


def test_the_box_is_aths_own_with_the_meshers_default_depth_and_margins():
    from hornlab_mesher.config_parser import parse_text_config

    defaulted = parse(ath(" LFSource.B.Radius = 60\n")).design.root.enclosure
    native = parse_text_config(ath(" LFSource.B.Radius = 60\n"))["enclosure"]
    assert defaulted.depth.value == pytest.approx(native["depth_mm"])
    assert [defaulted.space_l.value, defaulted.space_t.value, defaulted.space_r.value, defaulted.space_b.value] == [10.0] * 4
    stated = parse(ath(" Depth = 300\n Spacing = 20,30,40,250\n LFSource.B.Radius = 60\n")).design.root.enclosure
    assert stated.depth.value == 300 and stated.space_b.value == 250


def test_dim_is_read_by_the_mesher_contract_translated_and_round_tripped():
    text = ath(" Depth = 300\n Dim = 150,160,150,400\n LFSource.B.Radius = 60\n")
    parsed = parse(text)
    assert parsed.design.root.enclosure.dim_mm == (150.0, 160.0, 150.0, 400.0)
    assert design_to_mesher_config(parsed.design)["enclosure"]["dim_mm"] == [150.0, 160.0, 150.0, 400.0]
    written = serialize(parsed)
    assert "Dim = 150,160,150,400" in written and "LFSource" not in written
    assert parse(written).design.root.enclosure.dim_mm == (150.0, 160.0, 150.0, 400.0)
    with pytest.raises(TextConfigError, match="four positive finite distances"):
        parse(ath(" Depth = 200\n Dim = 300,400\n"))


def test_save_and_reopen_keep_radiator_identities_and_ath_provenance():
    parsed = parse(ath(" Spacing = 20,20,20,250\n LFSource.B.Radius = 70\n LFSource.B.SID = 2\n"))
    written = serialize(parsed)
    assert "geometry-interpretation" in written and "WG.Geometry" in written
    reopened = parse(written)
    assert reopened.dialect == "mwg"
    assert reopened.design.model_dump(mode="json")["assembly"] == parsed.design.model_dump(mode="json")["assembly"]
    assert serialize(reopened) == written


@pytest.mark.parametrize("key", ["A", "B"])
@pytest.mark.parametrize("weight", [0, -0.5, 0.75])
def test_nested_lf_members_use_the_dotted_import_and_round_trip(key, weight):
    members = f"Radius = 70\n Spacing = 35\n DrivingWeight = {weight}\n SID = 1\n"
    prefix = " Depth = 280\n Spacing = 25,250,25,300\n"
    nested = parse(ath(prefix + f" LFSource.{key} = {{ ; source\n {members} }}\n"))
    dotted = parse(ath(prefix + "\n".join(f" LFSource.{key}.{line.strip()}" for line in members.splitlines()) + "\n"))
    actual = nested.design.root.assembly
    expected = dotted.design.root.assembly
    assert actual is not None and expected is not None
    assert actual.woofers[0].model_dump(exclude={"id"}) == expected.woofers[0].model_dump(exclude={"id"})
    assert actual.signal_groups[1].members[0].model_dump(exclude={"radiator_id"}) == expected.signal_groups[1].members[0].model_dump(exclude={"radiator_id"})
    assert actual.woofers[0].drive == ("rigid" if weight == 0 else "prescribed")
    assert actual.woofers[0].ath.sid == 1
    written = serialize(nested)
    assert parse(written).design.root.assembly == actual
    assert serialize(parse(written)) == written


def test_nested_sources_close_back_into_the_enclosure_and_keep_disabled_members():
    parsed = parse(ath(" LFSource.A = {\n _Radius = 50\n}\n LFSource.B = {\n Radius = 70\n}\n Depth = 280\n Spacing = 25,250,25,300\n"))
    assert parsed.design.root.enclosure.depth.value == 280
    assert [w.label for w in parsed.design.root.assembly.woofers] == ["LF B"]
    assert "no woofer imported" in {item.key: item.note for item in parsed.ignored_profile}["Mesh.Enclosure.LFSource.A.Radius"]
    assert "no woofer imported" in notes(ath(" LFSource.B = {\n}\n"))["Mesh.Enclosure.LFSource.B.Radius"]
    assert parse(ath(" LFSource.Below = {\n Radius = 70\n}\n")).design.root.assembly is None


@pytest.mark.parametrize(("enclosure", "message"), [
    (" LFSource.B = {\n Radius = 70\n}\n LFSource.B = {\n Radius = 80\n}\n", "appears more than once"),
    (" Other = {\n Radius = 70\n}\n", "nested block"),
    (" LFSource.B = {\n Other = {\n}\n}\n", "nested block"),
    (" LFSource.B = {\n Radius 70\n}\n", "cannot read member"),
    (" LFSource.B = {\n Unsupported = 1\n}\n", "unsupported"),
])
def test_nested_lf_support_does_not_relax_other_syntax_refusals(enclosure, message):
    with pytest.raises(TextConfigError, match=message):
        parse(ath(enclosure))


@pytest.mark.parametrize("ending", ["", "}\n"])
def test_nested_lf_and_enclosure_each_require_their_own_terminator(ending):
    with pytest.raises(TextConfigError, match="unterminated block"):
        parse(HORN + "Mesh.Enclosure = {\n LFSource.B = {\n Radius = 70\n" + ending)


def test_legacy_text_without_lf_sources_is_unchanged():
    text = ath(" Depth = 200\n Spacing = 20,20,20,20\n")
    parsed = parse(text)
    assert parsed.design.root.assembly is None
    assert serialize(parsed) == text


def test_below_is_ignored_as_ath_ignores_it_and_top_level_lf_stays_refused():
    text = ath(" Spacing = 20,20,20,250\n LFSource.Below.Radius = 60\n")
    assert parse(text).design.root.assembly is None
    assert "ignored by ATH" in notes(text)["Mesh.Enclosure.LFSource.Below"]
    with pytest.raises(TextConfigError, match="unsupported item"):
        parse(HORN + "LFSource.A = {\n Radius = 60\n}\n")


@pytest.mark.parametrize(("nested", "b_weight"), [(False, 1), (True, 1), (True, 0)])
def test_imported_woofers_compile_where_the_meshers_own_ath_import_places_them(tmp_path, nested, b_weight):
    """Same dependency, two routes: WG's relations and the mesher's ATH reading agree."""
    from hornlab_mesher.config_parser import parse_text_config

    # The design's own resolutions keep this test inside the dense-memory guard.
    text = ath(" Spacing = 20,250,20,250\n EdgeRadius = 5\n FrontResolution = 30\n BackResolution = 50\n"
               " LFSource.A.Radius = 25\n LFSource.A.Spacing = 12\n LFSource.A.Resolution = 25\n"
               f" LFSource.B.Radius = 30\n LFSource.B.Resolution = 25\n LFSource.B.DrivingWeight = {b_weight}\n",
               "Mesh.AngularSegments = 32\nMesh.LengthSegments = 12\nMesh.ThroatResolution = 8\n"
               "Mesh.MouthResolution = 20\n")
    if nested:
        for key in ("A", "B"):
            members = re.findall(rf" LFSource\.{key}\.([^\n]+)\n", text)
            text = re.sub(rf" LFSource\.{key}\.[^\n]+\n", "", text)
            text = text.replace("Mesh.Enclosure = {\n", "Mesh.Enclosure = {\n" + f" LFSource.{key} = {{\n" + "\n".join(members) + "\n }\n")
    parsed = parse(text)
    store = CadLinkStore.for_data_dir(tmp_path)
    store.initialize()
    try:
        outcome = asyncio.run(compile_design_assembly(
            store, tmp_path, parsed.design.model_dump(mode="json"), design_uid="wgu_01JABCDEFGHJKMNPQRSTVWXYZ0",
            design_name="lf", design_revision=1, fence=AssemblyBuildFence()))
    finally:
        store.close()
    record = outcome["record"]
    compiled = {r["label"]: r for r in record["radiators"] if r["radiator_id"] != "horn-throat"}
    native = {row["radiator_id"].removeprefix("ath-lf-"): row for row in parse_text_config(text)["enclosure"]["woofers"]}
    for key in ("A", "B"):
        assert compiled[f"LF {key}"]["center_mm"] == pytest.approx([native[key]["x_mm"], native[key]["y_mm"]], abs=1e-9)
        assert compiled[f"LF {key}"]["radius_mm"] == native[key]["radius_mm"]
        assert compiled[f"LF {key}"]["kind"] == "ath_cone"
        radiator = compiled[f"LF {key}"]
        rigid = key == "B" and b_weight == 0
        assert radiator["state"] == ("rigid" if rigid else "compiled")
        assert (radiator["radiator_id"] in {e["id"] for e in record["excitations"]}) is not rigid
        patches = [p for p in record["mesh"]["metadata"]["assembly"]["patches"] if p["radiator_id"] == radiator["radiator_id"]]
        assert [p["segment_id"] for p in patches] == ["cap", "cone"]
        if rigid:
            assert radiator["source_ids"] == []
            assert {p["physical_tag"] for p in patches} == {1}
    # The mesher's ATH LF compatibility box applies to these imported woofers.
    assert record["mesh"]["metadata"]["build_stats"]["triangle_count"] > 0
