#!/usr/bin/env python3
"""Regenerate CAD return endpoint and geometry fixtures with --regenerate.

Run on a clean main when the Stage 2 endpoint contract intentionally changes.
The committed geometry oracle keeps stable verdict, identity, and plane fields;
the domain unit tests cover the evolving decision record.
"""

from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server/tests"))

from server.cadlink.wgreturn import read_wgreturn, validate_manifest  # noqa: E402
from server.tests.test_cadlink_wgreturn import _manifest  # noqa: E402
from server.tests.tools.oracle_geometry import geometry_result  # noqa: E402

DEST = ROOT / "server/tests/fixtures/cadlink-endpoint-oracle"
STEP = b"STEP"
BASE = _manifest(STEP)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _same_step_geometry(old: bytes, new: bytes) -> bool:
    timestamp = rb"'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'"
    return re.sub(timestamp, b"'<timestamp>'", old) == re.sub(
        timestamp, b"'<timestamp>'", new
    )


def _same_manifest_contract(old: dict, new: dict) -> bool:
    old = deepcopy(old)
    new = deepcopy(new)
    for manifest in (old, new):
        manifest["return"].pop("id", None)
        manifest["return"].pop("created_at", None)
        for member in manifest["files"].values():
            if member["media_type"] == "model/step":
                member.pop("sha256", None)
    return old == new


def _verdict(call) -> dict[str, object]:
    try:
        call()
    except Exception as exc:
        return {"accepted": False, "message": str(exc)}
    return {"accepted": True, "message": None}


def _cases():
    base = deepcopy(BASE)
    base["wgreturn_version"] = "1.1"
    base["assembly"]["signature_hash"] = "sha256:" + "4" * 64
    base["scope"]["included"][0].update(component="speaker", external_reference="none")
    base["required_features"].append("source-identity-v1")
    yield "base-1-1", base, {"assembly.step": STEP}, None

    legacy = deepcopy(base)
    legacy["wgreturn_version"] = "1.0"
    legacy["assembly"].pop("signature_hash")
    yield "legacy-1-0", legacy, {"assembly.step": STEP}, None

    changes = {
        "missing-signature": lambda m: m["assembly"].pop("signature_hash"),
        "major-2": lambda m: m.__setitem__("wgreturn_version", "2.0"),
        "leading-zero-version": lambda m: m.__setitem__("wgreturn_version", "01.0"),
        "unknown-feature": lambda m: m["required_features"].append("future-physics-v1"),
        "missing-base-feature": lambda m: m["required_features"].remove("checksummed-files-v1"),
        "missing-acoustics": lambda m: m.pop("acoustics"),
        "non-null-acoustics": lambda m: m.__setitem__("acoustics", {}),
        "mirrored-chirality": lambda m: m["instances"][0].__setitem__("chirality", "mirrored"),
        "source-id-space": lambda m: m["sources"][0].__setitem__("id", " source-hf"),
        "visible-false": lambda m: m["scope"]["included"][0].__setitem__("visible", False),
        "forbidden-source-tag": lambda m: m["sources"][0].__setitem__("tag", 101),
        "upper-source-tag": lambda m: m["sources"][0].__setitem__("TAG", 101),
        "zero-source-area": lambda m: m["sources"][0]["observed"].__setitem__("total_area_mm2", 0),
        "automatic-without-feature": lambda m: m["assembly"].__setitem__(
            "domain", {"kind": "automatic"}
        ),
        "automatic-empty-cut-planes": lambda m: (
            m["required_features"].append("domain-automatic-v1"),
            m["assembly"].__setitem__("domain", {"kind": "automatic", "cut_planes": []}),
        ),
        "document-up-without-feature": lambda m: m["coordinate_system"].__setitem__(
            "document_up", "+y"
        ),
        "degraded-without-skip": lambda m: m["scope"].__setitem__("status", "degraded"),
        "invalid-member-digest": lambda m: m["files"]["assembly.step"].__setitem__(
            "sha256", "sha256:SHORT"
        ),
        "duplicate-portable-name": lambda m: m["files"].__setitem__(
            "ASSEMBLY.step", deepcopy(m["files"]["assembly.step"])
        ),
        "first-error-chirality": lambda m: (
            m["instances"][0].__setitem__("chirality", "mirrored"),
            m.__setitem__("acoustics", {}),
        ),
    }
    for name, mutate in changes.items():
        manifest = deepcopy(base)
        mutate(manifest)
        yield name, manifest, {"assembly.step": STEP}, None

    repeated_channel = deepcopy(base)
    another = deepcopy(repeated_channel["sources"][0])
    another["id"] = "source-hf-two"
    repeated_channel["sources"].append(another)
    yield "duplicate-drive-channel", repeated_channel, {"assembly.step": STEP}, None

    missing_fem = deepcopy(base)
    missing_fem["required_features"].append("fem-air-volume-v1")
    fem = b"FEM STEP"
    missing_fem["files"]["fem/air.step"] = {
        "sha256": "sha256:" + _sha(fem),
        "size_bytes": len(fem),
        "media_type": "model/step",
        "purpose": "fem-air-volume",
    }
    missing_fem["scope"]["fem_air_volumes"] = [{"file": "fem/air.step", "n_bodies_expected": 1}]
    yield "missing-fem-member", missing_fem, {"assembly.step": STEP}, None

    yield "checksum-mismatch", deepcopy(base), {"assembly.step": b"WRNG"}, None
    yield "undeclared-member", deepcopy(base), {"assembly.step": STEP, "extra.txt": b"extra"}, None
    yield "missing-member", deepcopy(base), {}, None
    yield "symlink-member", deepcopy(base), {"assembly.step": STEP}, None
    yield "oversized-json", deepcopy(base), {"assembly.step": STEP}, b" " * (1024 * 1024 + 1)
    yield "nonfinite-json", deepcopy(base), {"assembly.step": STEP}, b'{"value":NaN}'
    yield (
        "duplicate-json-key",
        deepcopy(base),
        {"assembly.step": STEP},
        b'{"wgreturn_version":1,"wgreturn_version":2}',
    )
    yield "non-utf8-json", deepcopy(base), {"assembly.step": STEP}, b"\xff"


def _geometry_cases(root: Path):
    """Copy actual emitted returns; these members are never synthetic STEP."""

    from test_cadlink_domain_automatic import (
        HORN_THROAT,
        _bundle,
        _capped_half,
        _horn,
        _negative_half,
        _open_half,
        _open_quarter,
        provenance,
    )
    from test_onshape_return import _outbound
    from server.cadlink.onshape.return_leg import write_return_bundle
    from server.mesh.gmsh_worker import _run_in_gmsh_session

    configurations = (
        ("automatic-full", _horn, "automatic", None),
        ("automatic-half-provenance", _open_half, "automatic", [provenance("body-0")]),
        ("automatic-half-as-shown", _open_half, "automatic", None),
        ("declared-half", _open_half, ("x0",), None),
        (
            "automatic-quarter",
            _open_quarter,
            "automatic",
            [provenance("body-0", "x0"), provenance("body-0", "y0", name="Split Body 4")],
        ),
        ("automatic-negative-half", _negative_half, "automatic", None),
        ("automatic-capped-half", _capped_half, "automatic", [provenance("body-0")]),
    )
    for name, geometry, domain, cut in configurations:
        source = _bundle(root, name, geometry, HORN_THROAT, domain=domain, cut=cut)
        members = {p.name: p.read_bytes() for p in source.iterdir() if p.name != "wgreturn.json"}
        yield (
            name,
            json.loads((source / "wgreturn.json").read_text()),
            members,
            (source / "wgreturn.json").read_bytes(),
        )

    outbound, step = _outbound(root)
    returned = _run_in_gmsh_session(
        write_return_bundle,
        step,
        link={
            "document_id": "DID",
            "workspace_id": "WID",
            "part_studio_element_id": "PART",
            "document_name": "Demo Horn",
        },
        export_row={"manifest_json": json.dumps(outbound)},
        data_dir=root / "onshape-data",
    )
    members = {p.name: p.read_bytes() for p in returned.iterdir() if p.name != "wgreturn.json"}
    yield (
        "onshape-linked",
        json.loads((returned / "wgreturn.json").read_text()),
        members,
        (returned / "wgreturn.json").read_bytes(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regenerate", action="store_true", help="replace committed oracle")
    args = parser.parse_args()
    if not args.regenerate:
        parser.error("pass --regenerate to replace the committed oracle")
    current_main = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "main"], text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"],
        text=True,
    ).strip()
    branch = subprocess.check_output(
        ["git", "-C", str(ROOT), "branch", "--show-current"], text=True
    ).strip()
    if dirty and branch == "main":
        raise SystemExit("Regenerate on a clean main, or in an isolated topic worktree")
    old_members = {
        p.relative_to(DEST).as_posix(): p.read_bytes()
        for p in DEST.rglob("*")
        if p.is_file()
    } if DEST.exists() else {}
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    (DEST / ".gitattributes").write_text(
        "*.step binary\noversized-json.wgreturn/wgreturn.json binary\n"
    )
    results = {}
    bare_for_invalid_json = {}
    disk_setup = {"symlink-member": {"link": "linked.bin", "target": "assembly.step"}}
    geometry = {}
    with tempfile.TemporaryDirectory(prefix="cadlink-oracle-") as generated:
        for name, manifest, members, raw in (*_cases(), *_geometry_cases(Path(generated))):
            folder = DEST / f"{name}.wgreturn"
            folder.mkdir()
            # STEP writers put wall-clock timestamps in headers. Reuse the committed
            # member and manifest for unchanged geometry, avoiding byte churn.
            old_manifest = old_members.get(f"{name}.wgreturn/wgreturn.json")
            if old_manifest and name.startswith(("automatic-", "declared-")):
                old = json.loads(old_manifest)
                if all(
                    (old_member := old_members.get(f"{name}.wgreturn/{key}")) is not None
                    and _same_step_geometry(old_member, value)
                    for key, value in members.items()
                ) and _same_manifest_contract(old, manifest):
                    manifest = old
                    raw = old_manifest
                    members = {
                        key: old_members[f"{name}.wgreturn/{key}"] for key in members
                    }
            if name == "onshape-linked":
                # This case checks the project gate, before STEP parsing.
                members["assembly.step"] = STEP
                manifest["files"]["assembly.step"].update(
                    sha256="sha256:" + _sha(STEP), size_bytes=len(STEP)
                )
                if old_manifest:
                    old = json.loads(old_manifest)
                    if old["files"]["assembly.step"] == manifest["files"]["assembly.step"]:
                        manifest = old
                        raw = old_manifest
                    else:
                        raw = None
                else:
                    raw = None
            payload = (
                raw
                if raw is not None
                else (
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
                ).encode()
            )
            (folder / "wgreturn.json").write_bytes(payload)
            for member, data in members.items():
                (folder / member).write_bytes(data)
            if name in {"duplicate-json-key", "nonfinite-json", "non-utf8-json", "oversized-json"}:
                bare_for_invalid_json[name] = manifest
            link = folder / "linked.bin" if name == "symlink-member" else None
            if link is not None:
                link.symlink_to("assembly.step")
            results[name] = {
                "validate_manifest": _verdict(lambda m=manifest: validate_manifest(m)),
                "read_wgreturn": _verdict(lambda f=folder: read_wgreturn(f)),
            }
            if link is not None:
                link.unlink()
            if (
                name.startswith(("automatic-", "declared-"))
                and name != "automatic-without-feature"
                or name == "onshape-linked"
            ):
                from server.cadlink.ingest import IngestRefusal, ingest_bundle
                from server.cadlink.store import CadLinkStore
                from server.mesh.gmsh_worker import _run_in_gmsh_session

                data_dir = Path(generated) / f"data-{name}"
                data_dir.mkdir()
                sizes = {
                    "rigid_size_mm": 20,
                    "transition_mm": 30,
                    "source_size_mm": {
                        source["id"]: 4 if name == "onshape-linked" else 8
                        for source in manifest["sources"]
                    },
                }
                store = CadLinkStore(data_dir / "cadlink.db")
                try:
                    try:
                        record = _run_in_gmsh_session(
                            ingest_bundle,
                            folder,
                            sizes,
                            [],
                            store,
                            data_dir,
                            prep_options={"symmetry_mode": "auto"},
                        )
                    except IngestRefusal as exc:
                        geometry[name] = {"accepted": False, "message": str(exc)}
                        continue
                finally:
                    store.close()
                geometry[name] = geometry_result(record)
    (DEST / "ORACLE.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    (DEST / "BARE.json").write_text(
        json.dumps(bare_for_invalid_json, indent=2, sort_keys=True) + "\n"
    )
    (DEST / "DISK_SETUP.json").write_text(json.dumps(disk_setup, indent=2, sort_keys=True) + "\n")
    (DEST / "GEOMETRY.json").write_text(json.dumps(geometry, indent=2, sort_keys=True) + "\n")
    files = {
        p.relative_to(DEST).as_posix(): _sha(p.read_bytes())
        for p in sorted(DEST.rglob("*"))
        if p.is_file()
    }
    source = json.loads((ROOT / "integrations/wglink/source.json").read_text())
    provenance = {
        "schema": 1,
        "generator": "server/tests/tools/generate_cadlink_endpoint_oracle.py",
        "wg_commit": current_main,
        "addin_commit": source["commit"],
        "files": files,
    }
    (DEST / "PROVENANCE.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    print(f"Captured {len(results)} endpoint cases")


if __name__ == "__main__":
    main()
