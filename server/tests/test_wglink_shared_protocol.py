"""Vendored protocol provenance and the add-in development pair."""

from __future__ import annotations

import ast
from copy import deepcopy
import dataclasses
import enum
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from server.cadlink import fusion_status, ingest, operations, step_evidence, wglink_protocol
from server.cadlink.fusion_delivery import capabilities, ipc_folder
from server.cadlink.store import CadLinkStore
from server.cadlink import solve_command
from server.cadlink.wgreturn import read_wgreturn, validate_manifest
from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.tests.test_wglink_packaged_pair import CORPUS, _from_package, _verdict

ROOT = Path(__file__).resolve().parents[2]
SHARED = ROOT / "server/tests/fixtures/wglink-shared-protocol"
VENDORED = ROOT / "server/cadlink/wglink_protocol.py"
SOURCE = json.loads((ROOT / "server/cadlink/wglink_protocol.source.json").read_text())


def _fetch_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    remote = os.environ.get("WGLINK_SOURCE_GIT") or SOURCE["repository"]
    result = subprocess.run(
        ["git", "fetch", "--quiet", "--no-tags", "--depth", "1", remote, SOURCE["commit"]],
        cwd=source, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Shared WGLink source {SOURCE['commit']} is unavailable from {remote}: {result.stderr}"
    subprocess.run(["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=source, check=True)
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip() == SOURCE["commit"]
    return source


@pytest.fixture(scope="module")
def addin(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _fetch_source(tmp_path_factory.mktemp("wglink-shared")) / "fusion-addins/WGLink"


def test_vendor_bytes_and_shared_fixtures(addin: Path):
    expected = VENDORED.resolve()
    assert Path(wglink_protocol.__file__).resolve() == expected
    data = VENDORED.read_bytes()
    assert hashlib.sha256(data).hexdigest() == SOURCE["sha256"]
    assert data == (addin / "wglink_protocol.py").read_bytes()
    manifest = {}
    for row in (SHARED / "MANIFEST.sha256").read_text().splitlines():
        digest, name = row.split("  ", 1)
        manifest[name] = digest
    assert set(manifest) == {p.name for p in SHARED.iterdir() if p.name != "MANIFEST.sha256"}
    for name, digest in manifest.items():
        assert hashlib.sha256((SHARED / name).read_bytes()).hexdigest() == digest
        assert (SHARED / name).read_bytes() == (addin.parents[1] / "tests/fixtures/wgreturn-endpoint-oracle" / name).read_bytes()


def test_shared_ingress_oracle():
    for case in json.loads((SHARED / "wg_ingress_cases.json").read_text()):
        assert _verdict(lambda: validate_manifest(case["manifest"])) == case["expected"], case["name"]


def test_production_hash_profiles_against_addin_goldens():
    for row in json.loads((SHARED / "goldens.json").read_text()):
        name = row["name"]
        value = {"nan": {"x": float("nan")}, "positive_infinity": {"x": float("inf")}, "negative_infinity": {"x": -float("inf")}}.get(name)
        if value is None:
            value = json.loads(row["core_json"]["value"])
        expected = row["core_json"]
        if "error_type" in expected:
            with pytest.raises(ValueError, match="Out of range float values"):
                step_evidence._canonical(value)
            with pytest.raises(ValueError, match="Out of range float values"):
                ingest._canonical(value)
        else:
            assert step_evidence._canonical(value).hex() == expected["canonical_bytes_hex"]
            assert ingest._canonical(value).hex() == expected["canonical_bytes_hex"]
            assert hashlib.sha256(step_evidence._canonical(value)).hexdigest() == expected["sha256"]
        if name in ("non_ascii", "negative_zero", "large_integer", "nested"):
            assert operations.canonical_json(value) == wglink_protocol.canonical_json(value, wglink_protocol.UTF8_STRICT)


def test_observation_fingerprint_equality(addin: Path):
    source = ast.parse((addin / "WGLink.py").read_text())
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "_fingerprint_hash")
    namespace = {"hashlib": hashlib, "json": json}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(addin / "WGLink.py"), "exec"), namespace)
    addin_hash = namespace["_fingerprint_hash"]
    api_tree = ast.parse((ROOT / "server/cadlink/api.py").read_text())
    observed_dump = next(
        node for node in ast.walk(api_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dumps" and node.args
        and isinstance(node.args[0], ast.Name) and node.args[0].id == "observed"
    )
    api_json = compile(ast.Expression(observed_dump), str(ROOT / "server/cadlink/api.py"), "eval")
    for value in ({"é": "雪", "x": -0.0}, {"x": float("nan")}, {"n": 2**100}):
        assert fusion_status._fingerprint_hash(value) == addin_hash(value)
        if isinstance(value.get("x"), float) and value["x"] != value["x"]:
            with pytest.raises(ValueError):
                eval(api_json, {"json": json, "observed": value})
        else:
            actual = "sha256:" + hashlib.sha256(eval(api_json, {"json": json, "observed": value}).encode("utf-8")).hexdigest()
            assert actual == addin_hash(value)


def test_development_writer_reader_claim_and_ingest(addin: Path, tmp_path: Path):
    with _from_package(addin, "wglink_return.py") as writer, _from_package(addin, "wglink_watch.py") as watcher:
        data = tmp_path / "data"
        data.mkdir()
        ipc = ipc_folder(data, create=True)
        (ipc / "wg-capabilities.json").write_text(json.dumps(capabilities()))
        store = CadLinkStore.for_data_dir(data)
        try:
            for name in ("automatic-full", "automatic-half-as-shown", "automatic-quarter"):
                fixture = CORPUS / f"{name}.wgreturn"
                fields = deepcopy(json.loads((fixture / "wgreturn.json").read_text()))
                fields["return_record"] = fields.pop("return")
                fields.pop("acoustics")
                for body in fields["scope"]["included"]:
                    body.update(component="Body1", external_reference="none")
                manifest = writer.build_return_manifest(**fields)
                bundle = tmp_path / "wgreturn" / f"{name}.wgreturn"
                bundle.mkdir(parents=True)
                shutil.copy2(fixture / "assembly.step", bundle / "assembly.step")
                (bundle / "wgreturn.json").write_text(writer.dumps_return_manifest(manifest))
                assert _verdict(lambda: read_wgreturn(bundle)) == json.loads((CORPUS / "ORACLE.json").read_text())[name]["read_wgreturn"]
                assert [item["id"] for item in read_wgreturn(bundle).manifest["sources"]] == [item["id"] for item in fields["sources"]]
                for kind in ("receive_snapshot", "prepare_and_solve"):
                    command_id = f"dev-{name}-{kind}"
                    request = watcher.write_wg_request(
                        ipc, kind=kind, command_id=command_id,
                        return_id=manifest["return"]["id"] if kind == "prepare_and_solve" else None,
                        bundle_relative=f"wgreturn/{name}.wgreturn",
                        manifest_sha256="sha256:" + hashlib.sha256((bundle / "wgreturn.json").read_bytes()).hexdigest(),
                        requested_at="2026-09-28T00:00:00Z",
                    )
                    payload = json.loads(request.read_text())
                    assert payload["schemaVersion"] == 4 and payload["kind"] == kind
                    refusals = []
                    solve_command.collect_solve_deliveries(data, store, refuse=refusals.append)
                    assert refusals == []
                    assert store.get_operation(command_id)["kind"] == kind
                if name == "automatic-full":
                    record = _run_in_gmsh_session(
                        ingest.ingest_bundle, bundle,
                        {"rigid_size_mm": 20, "transition_mm": 30, "source_size_mm": {"throat": 8}},
                        [], store, data, prep_options={"symmetry_mode": "auto"},
                    )
                    assert record["source_tags"] == {"throat": 101}
                    assert record["tag_map"]["101"]["role"] == "HF"
        finally:
            store.close()


# -- WG keeps no copy of the ingress contract --------------------------------

# Every contract value WG reads from ``wgreturn`` is the vendored WG ingress
# profile's own object. An equal literal is not enough: it would stop following
# the profile at the next re-vendor while every test stays green.
WGRETURN_PROFILE_ALIASES = (
    "SUPPORTED_MAJOR",
    "SUPPORTED_VERSION",
    "SUPPORTED_FEATURES",
    "EXPORT_FRAMES",
    "DOCUMENT_UP_FEATURE",
    "DOCUMENT_UP_AXES",
    "DOMAIN_PLANES",
    "DOMAIN_KIND_FOR_PLANES",
    "REDUCED_DOMAIN_FEATURE",
    "DOMAIN_AUTOMATIC_FEATURE",
    "DOMAIN_AUTOMATIC",
    "CUT_FEATURE_KINDS",
    "CUT_TOOL_KINDS",
    "CUT_ORIGIN_PLANES",
    "CUT_KEPT_SIDES",
    "SOURCE_IDENTITY_FEATURE",
    "GMSH_PHYSICAL_NAME_MAX_BYTES",
    "SOURCE_IDENTITY_MAX_BYTES",
    "WORST_CASE_SOURCE_TAG",
    "REQUIRED_BASE_FEATURES",
    "FORBIDDEN_VERDICT_KEYS",
    "_WINDOWS_DRIVE",
)
# ``read_wgreturn`` checks the member table with these, raising WG's own error
# type, so they stay local; they must remain the profile's helpers verbatim.
WGRETURN_LOCAL_HELPERS = ("_mapping", "_list", "_string", "_integer", "_required", "_portable_member_name")
WGRETURN = ROOT / "server/cadlink/wgreturn.py"


def test_wgreturn_contract_values_are_the_profile_objects():
    from server.cadlink import wgreturn

    for name in WGRETURN_PROFILE_ALIASES:
        assert getattr(wgreturn, name) is getattr(wglink_protocol, "_wg_" + name), name
    tree = ast.parse(WGRETURN.read_text())
    for node in tree.body:
        targets = [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else getattr(node, "targets", [])
        for target in targets:
            if isinstance(target, ast.Name) and target.id in WGRETURN_PROFILE_ALIASES:
                assert ast.unparse(node.value) == f"protocol._wg_{target.id}", (
                    f"wgreturn.{target.id} must alias the vendored profile, not redefine it"
                )


def _without_docstring_and_annotations(function: ast.FunctionDef, prefix: str) -> str:
    function = deepcopy(function)
    body = function.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        function.body = body[1:]
    function.returns = None
    for argument in (*function.args.args, *function.args.kwonlyargs):
        argument.annotation = None
    for node in (function, *ast.walk(function)):
        for field in ("id", "name"):
            name = getattr(node, field, None)
            if isinstance(name, str) and name.startswith(prefix):
                setattr(node, field, name[len(prefix):])
    return ast.dump(function)


def test_wgreturn_keeps_only_verbatim_profile_helpers():
    wg = {node.name: node for node in ast.parse(WGRETURN.read_text()).body if isinstance(node, ast.FunctionDef)}
    vendored = {node.name: node for node in ast.parse(VENDORED.read_text()).body if isinstance(node, ast.FunctionDef)}
    for name in WGRETURN_LOCAL_HELPERS:
        assert _without_docstring_and_annotations(wg[name], "_wg_") == _without_docstring_and_annotations(vendored["_wg_" + name], "_wg_"), name
    # No other copy of a profile validator survives in WG. ``_fail`` raises WG's
    # own error type and ``validate_manifest`` delegates to the profile.
    copies = {name for name in wg if "_wg_" + name in vendored} - set(WGRETURN_LOCAL_HELPERS) - {"_fail", "validate_manifest"}
    assert copies == set()


def _profile_literals(source: str, prefix: str, namespace: dict) -> dict:
    functions = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith(prefix):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                body = body[1:]
            functions[node.name[len(prefix):]] = [
                sub.value
                for statement in body
                for sub in ast.walk(statement)
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
            ]

    def jsonable(value):
        if isinstance(value, (frozenset, set)):
            return sorted(value)
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, dict):
            return sorted([jsonable(key), jsonable(item)] for key, item in value.items())
        if hasattr(value, "pattern"):
            return value.pattern
        return value

    constants = {
        name[len(prefix):]: jsonable(value)
        for name, value in namespace.items()
        if name.startswith(prefix) and name[len(prefix):].lstrip("_")[:1].isupper()
    }
    return {"functions": functions, "constants": constants}


def test_wg_ingress_literals_match_the_pre_consolidation_reader():
    """Every message and constant of the WG ingress profile, pinned side by side.

    ``wg-ingress-literals.json`` was extracted from WG's own reader at 129085bd,
    before the validator moved into the shared module, with the same function as
    this test. The committed oracles cover only some messages; this covers every
    string the profile can emit, so a re-vendor that rewords one fails here even
    when the vendored hash was updated with it.
    """

    expected = json.loads((ROOT / "server/tests/fixtures/wg-ingress-literals.json").read_text())
    actual = _profile_literals(VENDORED.read_text(), "_wg_", vars(wglink_protocol))
    assert actual == expected


# -- Hash sites: byte goldens through the real call sites -------------------

# Computed at 129085bd, before the hash sites moved to named profiles.
REQUEST_DIGEST_NON_ASCII = "sha256:ff2005fe471198911fc020ffd008d5fb521d494421ab1cfd04e4f6b8e0ff9638"
DESIGN_HASH_NON_ASCII = "sha256:1e90c0011be979e9a43b113ea8e28037b070be6e9def0b1d762250aac1c2d1e3"
DESIGN_HASH_NAN = "sha256:74485b0126616c29f67e48fb9033a771406b9f9f51ce8a0dcedc2e94551ac519"
MESHING_SEMANTICS_FINGERPRINT = "sha256:476d21c20c85d17301052ecf8df78fc19f8c42918dcf6c26e74c82e94a1fe9fb"
BASELINE_MESHING_SEMANTICS = "sha256:79aa14dff0812302be2bd64c494913fd39f370466ca80458ce285f6a63c173c8"
INGEST_NORMALISED = (
    '{"array":[1.5,-0.0],"band":"HF","flt":0.25,'
    '"point":{"name":"H\\u00f6rn \\u96ea","xyz":[1,2.5]},"scalar":3,"tup":[1,"\\u00e9"]}'
)
NON_ASCII_DESIGN = (
    "; Parameter config\n; Waveguide Generator design-format: 2\nOSSE = {\n}\n"
    "Coverage.Angle = 45\nLength = 120\nThroat.Profile = 1\nNotiz = Hörn 雪\n"
)


def test_request_digest_golden_with_non_ascii_input():
    digest = operations.request_digest(
        "prepare_and_solve",
        {},
        {"return_id": "wgr_é", "bundle_path": "wgreturn/Hörn 雪.wgreturn", "manifest_sha256": "sha256:abc"},
    )
    assert digest == REQUEST_DIGEST_NON_ASCII
    with pytest.raises(ValueError, match="Out of range float values"):
        operations.canonical_json({"x": float("nan")})


def test_design_hash_golden_non_ascii_and_nan_permitted(monkeypatch: pytest.MonkeyPatch):
    from server.cadlink import identity
    from server.design import textcfg

    assert identity.design_hash(textcfg.parse(NON_ASCII_DESIGN).design) == DESIGN_HASH_NON_ASCII

    # A design payload carrying NaN still hashes (the historical json.dumps
    # default), through design_hash's own profile call.
    class Payload:
        def model_dump(self, mode: str) -> dict:
            assert mode == "json"
            return {"x": float("nan"), "é": "雪", "n": -0.0}

    monkeypatch.setattr(textcfg, "serialize", lambda design: "")
    monkeypatch.setattr(textcfg, "parse", lambda text: SimpleNamespace(design=Payload()))
    assert identity.design_hash(object()) == DESIGN_HASH_NAN


def test_meshing_semantics_goldens():
    assert ingest._BASELINE_MESHING_SEMANTICS == BASELINE_MESHING_SEMANTICS
    assert ingest.meshing_semantics_fingerprint() == MESHING_SEMANTICS_FINGERPRINT


def test_ingest_canonical_normalises_python_values():
    class Band(enum.Enum):
        HIGH = "HF"

    @dataclasses.dataclass
    class Point:
        name: str
        xyz: tuple

    value = {
        "point": Point("Hörn 雪", (1, 2.5)),
        "band": Band.HIGH,
        "array": np.array([1.5, -0.0]),
        "scalar": np.int64(3),
        "flt": np.float32(0.25),
        "tup": (1, "é"),
    }
    assert ingest._canonical(value).decode("ascii") == INGEST_NORMALISED
    with pytest.raises(ValueError, match="Out of range float values"):
        ingest._canonical({"x": np.float64("nan")})
