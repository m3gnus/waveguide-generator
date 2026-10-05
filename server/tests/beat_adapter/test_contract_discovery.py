"""Portable optional schema discovery, isolated from installed packages/checkouts."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from . import conftest as fixtures


@pytest.fixture
def frozen_contract(tmp_path):
    schema = {"$defs": {"compiled_system": {"properties": {"contract_version": {"enum": [1, 2]}}}}}

    def make(root):
        root.mkdir(parents=True)
        (root / "system-v1.schema.json").write_text(json.dumps(schema))
        (root / "__init__.py").write_text("def validate_solve_request(request):\n    assert request['schema_version'] == 1\n")
        return root
    return make


def missing_engine(name):
    raise ModuleNotFoundError(name)


def test_importable_engine_resources_take_precedence_over_source_and_workspace(
    monkeypatch, frozen_contract, tmp_path,
):
    installed = frozen_contract(tmp_path / "installed/beat_contract")
    monkeypatch.setattr(fixtures.importlib.resources, "files", lambda name: installed.parent)
    contract = SimpleNamespace(validate_solve_request=lambda request: None)
    monkeypatch.setattr(fixtures.importlib, "import_module", lambda name: contract)
    monkeypatch.setenv("WG_BEAT_ENGINE_SRC", str(tmp_path / "absent"))
    monkeypatch.setattr(fixtures, "_workspace_contract_roots", lambda: pytest.fail("Installed resource must win"))
    found, schema = fixtures._official_contract()
    assert found is contract
    assert schema["$defs"]["compiled_system"]["properties"]["contract_version"]["enum"] == [1, 2]


@pytest.mark.parametrize("relative", ["src/beat_engine/beat_contract", "beat_engine/beat_contract", "beat_contract"])
def test_source_environment_discovery_is_portable_without_an_engine_install(
    monkeypatch, frozen_contract, tmp_path, relative,
):
    monkeypatch.setattr(fixtures.importlib.resources, "files", missing_engine)
    frozen_contract(tmp_path / relative)
    monkeypatch.setenv("WG_BEAT_ENGINE_SRC", str(tmp_path))
    monkeypatch.setattr(fixtures, "_workspace_contract_roots", lambda: [])
    contract, _ = fixtures._official_contract()
    contract.validate_solve_request({"schema_version": 1})


def test_source_environment_takes_precedence_over_workspace(monkeypatch, frozen_contract, tmp_path):
    monkeypatch.setattr(fixtures.importlib.resources, "files", missing_engine)
    source = frozen_contract(tmp_path / "source/beat_contract")
    workspace = frozen_contract(tmp_path / "workspace/beat_contract")
    monkeypatch.setenv("WG_BEAT_ENGINE_SRC", str(source.parent))
    monkeypatch.setattr(fixtures, "_workspace_contract_roots", lambda: [workspace])
    contract, _ = fixtures._official_contract()
    assert contract.__file__ == str(source / "__init__.py")


def test_workspace_contract_is_the_last_discovery_fallback(monkeypatch, frozen_contract, tmp_path):
    monkeypatch.setattr(fixtures.importlib.resources, "files", missing_engine)
    monkeypatch.delenv("WG_BEAT_ENGINE_SRC", raising=False)
    workspace = frozen_contract(tmp_path / "workspace/beat_contract")
    monkeypatch.setattr(fixtures, "_workspace_contract_roots", lambda: [workspace])
    contract, _ = fixtures._official_contract()
    assert contract.__file__ == str(workspace / "__init__.py")


def test_missing_optional_contract_only_skips_schema_assertions(monkeypatch, durable_request):
    monkeypatch.setattr(fixtures.importlib.resources, "files", missing_engine)
    monkeypatch.delenv("WG_BEAT_ENGINE_SRC", raising=False)
    monkeypatch.setattr(fixtures, "_workspace_contract_roots", lambda: [])
    assert fixtures._official_contract() is None
    request = SimpleNamespace(wire={"schema_version": 1})
    assert durable_request(request) is request
    with pytest.raises(pytest.skip.Exception, match="Missing beat_contract/system-v1.schema.json and validator"):
        fixtures.official_contract.__wrapped__()


def test_missing_axial_schema_support_does_not_skip_normal_contract(monkeypatch):
    validated = []
    schema = {"$defs": {"compiled_system": {"properties": {"contract_version": {"const": 1}}}}}
    monkeypatch.setattr(fixtures, "_official_contract", lambda: (
        SimpleNamespace(validate_solve_request=validated.append), schema))
    validate = fixtures.official_contract.__wrapped__()
    request = SimpleNamespace(wire={"schema_version": 1, "compiled_system": {"contract_version": 1}})
    validate(request)
    assert validated == [request.wire]
    request.wire["compiled_system"]["contract_version"] = 2
    with pytest.raises(pytest.skip.Exception, match="Missing system-v1.schema.json support for compiled contract v2"):
        validate(request)
