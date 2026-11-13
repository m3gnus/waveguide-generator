"""CadLink text-format, hashing, and open-classification contracts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from server.cadlink.identity import CadLink, SaveIdentity, design_hash, mint_id
from server.cadlink.store import CadLinkStore
from server.design.schema import Expr
from server.design.textcfg import TextConfigError, parse, serialize
from server.design_io.api import create_design_io_router, open_design
from server.jobs.design_reader import read_design


SOURCE = """; Parameter config
; unrelated comment
OSSE = {
}
Coverage.Angle = 45
Length = 120
Report = {
first row
Value = one
}
"""


def register_design(
    store: CadLinkStore,
    design_payload: object,
    *,
    identity: dict[str, object] | None = None,
    filename: str = "design.cfg",
    design_uid: str | None = None,
) -> dict[str, object]:
    """Commit a design to the CAD-link registry the way a WGLink export does."""

    design = read_design(design_payload)
    committed = store.save(
        requested=SaveIdentity.model_validate(identity) if identity is not None else None,
        design_hash=design_hash(design),
        filename=filename,
        snapshot_builder=lambda linked: serialize(design, cadlink=linked, design_uid=design_uid),
    )
    linked = committed["identity"]
    return {
        "text": committed["text"],
        "identity": {
            "designId": linked.design_id,
            "lineageId": linked.lineage_id,
            "baseEditVersion": linked.edit_version,
        },
    }


def _identity(*, version: int = 3, saved_hash: str = "sha256:0123456789abcdef") -> CadLink:
    return CadLink(
        design_id=mint_id("wgd_"),
        lineage_id=mint_id("wgl_"),
        edit_version=version,
        saved_at="2026-08-10T14:22:31Z",
        saved_design_hash=saved_hash,
    )


def test_cadlink_block_is_typed_canonical_and_not_passthrough() -> None:
    identity = _identity()
    emitted = serialize(parse(SOURCE).design, cadlink=identity)

    lines = emitted.splitlines()
    assert lines.count("CadLink = {") == 1
    block_start = lines.index("CadLink = {")
    assert lines[block_start:block_start + len(identity.block_lines())] == identity.block_lines()
    reopened = parse(emitted)
    assert reopened.cadlink == identity
    assert "CadLink" not in reopened.extra_blocks
    assert reopened.extra_blocks["Report"].entries == ["first row", "Value = one"]


def test_tagged_and_unrelated_untagged_sources_round_trip_byte_exactly() -> None:
    tagged = serialize(parse(SOURCE).design, cadlink=_identity())
    for source in (SOURCE, tagged):
        assert serialize(parse(source)) == source


def test_replacing_identity_forces_reserialize_without_duplicate_block() -> None:
    first = _identity()
    parsed = parse(serialize(parse(SOURCE).design, cadlink=first))
    replacement = first.model_copy(update={"edit_version": 4})
    emitted = serialize(parsed, cadlink=replacement)

    assert emitted.count("CadLink = {") == 1
    assert parse(emitted).cadlink == replacement


@pytest.mark.parametrize(
    "bad_row",
    ["Schema = 2", "EditVersion = 0", "Unknown = value", "; comment"],
)
def test_invalid_cadlink_blocks_fail_as_typed_parse_errors(bad_row: str) -> None:
    rows = _identity().block_lines()
    if bad_row.startswith("Schema"):
        rows[-2] = bad_row
    elif bad_row.startswith("EditVersion"):
        rows[3] = bad_row
    else:
        rows.insert(-1, bad_row)
    source = SOURCE.replace("OSSE = {", "\n".join(rows) + "\nOSSE = {")
    with pytest.raises(TextConfigError, match="invalid CadLink block"):
        parse(source)


def test_open_classification_matrix_and_open_is_read_only(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    design = parse(SOURCE).semantic_data()
    first = register_design(store, design, filename="matrix.cfg")
    current_text = first["text"]
    first_identity = first["identity"]

    assert asyncio.run(open_design(SOURCE, store=store))["cadlink"]["classification"] == "missing"
    assert (
        asyncio.run(open_design(current_text, store=store))["cadlink"]["classification"]
        == "current"
    )

    changed = parse(current_text).design
    changed.root.L = Expr(value=121)  # type: ignore[union-attr]
    second = register_design(store, changed.model_dump(mode="json"), identity=first_identity)
    assert (
        asyncio.run(open_design(current_text, store=store))["cadlink"]["classification"]
        == "stale_copy"
    )
    externally_edited = second["text"].replace("Length = 121", "Length = 122")
    assert (
        asyncio.run(open_design(externally_edited, store=store))["cadlink"]["classification"]
        == "externally_edited"
    )
    foreign = serialize(parse(SOURCE).design, cadlink=_identity())
    assert (
        asyncio.run(open_design(foreign, store=store))["cadlink"]["classification"]
        == "foreign"
    )
    assert store.get_design(first_identity["designId"])["edit_version"] == 2  # type: ignore[index]


def test_stale_copy_with_externally_edited_body_is_not_misclassified(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    design = parse(SOURCE).semantic_data()
    first = register_design(store, design, filename="stale.cfg")

    changed = parse(first["text"]).design
    changed.root.L = Expr(value=121)  # type: ignore[union-attr]
    register_design(store, changed.model_dump(mode="json"), identity=first["identity"])

    edited_stale = first["text"].replace("Length = 120", "Length = 122")
    opened = asyncio.run(open_design(edited_stale, store=store))

    assert opened["cadlink"]["classification"] == "externally_edited"


def test_serialize_endpoint_never_adds_or_advances_registry_rows(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    design = parse(SOURCE).semantic_data()
    saved = register_design(store, design, filename="registered.cfg")
    before = store._read_one(
        "SELECT COUNT(*) AS design_count, SUM(edit_version) AS version_total FROM designs",
        (),
    )
    router = create_design_io_router(store)
    endpoint = next(
        route.endpoint for route in router.routes if route.path == "/api/design/serialize"
    )

    serialized = [
        json.loads(asyncio.run(endpoint({"design": design, "filename": "portable.cfg"})).body)
        for _ in range(2)
    ]

    assert store._read_one(
        "SELECT COUNT(*) AS design_count, SUM(edit_version) AS version_total FROM designs",
        (),
    ) == before == {"design_count": 1, "version_total": 1}
    assert store.get_design(saved["identity"]["designId"])["edit_version"] == 1  # type: ignore[index]
    assert [item["suggestedFilename"] for item in serialized] == [
        "portable.cfg",
        "portable.cfg",
    ]
    assert all(parse(item["text"]).cadlink is None for item in serialized)


def test_open_without_registry_does_not_create_one(tmp_path: Path) -> None:
    store = CadLinkStore.for_data_dir(tmp_path)

    opened = asyncio.run(open_design(SOURCE, store=store))

    assert opened["cadlink"]["classification"] == "missing"
    assert not (tmp_path / "db" / "cadlink.db").exists()


V1 = Path(__file__).resolve().parents[2].parent / "Waveguide Generator"


@pytest.mark.skipif(
    not (V1 / "src/config/index.js").exists() or shutil.which("node") is None,
    reason="the v1 checkout and Node are required for the compatibility fixture",
)
def test_cadlink_survives_a_real_v1_parse_and_reserialize() -> None:
    tagged = serialize(parse(SOURCE).design, cadlink=_identity())
    script = """
import fs from 'node:fs';
import { MWGConfigParser } from './src/config/index.js';
import { getDefaults } from './src/config/defaults.js';
import { generateMWGConfigContent } from './src/export/mwgConfig.js';
const parsed = MWGConfigParser.parse(fs.readFileSync(0, 'utf8'));
const params = {...getDefaults(parsed.type), ...parsed.params, _blocks: parsed.blocks, type: parsed.type};
process.stdout.write(generateMWGConfigContent(params));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=V1,
        input=tagged,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert parse(completed.stdout).cadlink == parse(tagged).cadlink
