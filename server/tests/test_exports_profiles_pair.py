"""The Fusion pair samples once and keeps both historical CSV byte streams."""

import asyncio
import json
from unittest.mock import Mock
from uuid import uuid4

from fastapi import FastAPI
from hornlab_mesher.errors import UserRefusal
import pytest

from server.design.schema import DesignConfig
from server.exports import api, core
from server.jobs.operation_progress import mount_operation_progress
from test_operation_stages import _http
from test_native_formulas import small_design


@pytest.mark.parametrize("formula", ["OSSE", "R-OSSE"])
@pytest.mark.parametrize("scale", [0.5, 2.5])
def test_pair_samples_once_and_matches_individual_csv_bytes(monkeypatch, formula, scale):
    design = DesignConfig.model_validate({"formula": formula, **({"L": 120} if formula == "OSSE" else {"R": 100}), "a": 45, "scale": scale,
                                         "mesh": {"length_segments": 6, "angular_segments": 12}})
    expected = {kind: core.build_profiles(design, kind).encode() for kind in ("profiles", "slices")}
    grid = Mock(wraps=core._inner_grid)
    monkeypatch.setattr(core, "_inner_grid", grid)

    async def scenario():
        app = FastAPI()
        app.include_router(api.router)
        mount_operation_progress(app)
        status, headers, payload = await _http(app, "POST", "/api/export/profiles?kind=pair", {
            "design": design.model_dump(mode="json"), "designRevision": 7, "baseName": "horn",
        }, operation_id=str(uuid4()))
        assert status == 200, payload
        assert json.loads(headers[b"x-wg-operation-stages"]) == {
            "stages": ["resolve", "mesh", "check", "write"], "total": 4,
        }
        files = json.loads(payload)["files"]
        assert [file["filename"] for file in files] == ["horn_profiles.csv", "horn_slices.csv"]
        for file, kind in zip(files, ("profiles", "slices"), strict=True):
            assert file["text"].encode() == expected[kind]
            assert b"\r\n\r\n" in expected[kind]
        slices = files[1]["text"].split("\r\n")
        assert slices[1] == slices[13]  # closing angular point, unchanged

    asyncio.run(scenario())
    grid.assert_called_once_with(design, profile_sampling=True)


def test_pair_cannot_return_one_file_if_second_formatter_fails(monkeypatch):
    formatter = core.profile_csv

    def fail_second(grid, kind):
        if kind == "slices":
            raise ValueError("slices failed")
        return formatter(grid, kind)

    monkeypatch.setattr(core, "profile_csv", fail_second)

    async def scenario():
        app = FastAPI()
        app.include_router(api.router)
        status, _, payload = await _http(app, "POST", "/api/export/profiles?kind=pair", {
            "design": {"formula": "OSSE", "L": 120, "a": 45}, "designRevision": 1, "baseName": "horn",
        })
        assert status == 422
        assert "files" not in json.loads(payload)

    asyncio.run(scenario())


@pytest.mark.parametrize("formula", sorted(core.NATIVE_FORMULAS))
def test_pair_keeps_native_family_refusal(monkeypatch, formula):
    grid = Mock(side_effect=AssertionError("native refusal must precede sampling"))
    monkeypatch.setattr(core, "_inner_grid", grid)
    design = small_design(formula)
    with pytest.raises(UserRefusal, match="cannot export profile CSVs"):
        core.build_profiles(design, "pair")
    grid.assert_not_called()
