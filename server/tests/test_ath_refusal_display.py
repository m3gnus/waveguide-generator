"""ATH refusal contracts shared with the final client display boundary."""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from server.design.text_import import ath_import_failure
from server.design_io.api import open_design

CASES = json.loads((Path(__file__).parents[2] / "shared/ath-refusal-fixtures.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_exact_ath_input_reaches_the_display_contract(case, caplog):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(open_design(case["text"]))
    assert caught.value.status_code == 422
    assert caught.value.detail == {"type": "parse_error", "message": case["message"]}
    assert case["diagnostic"] in caplog.text
    # The errors backend branch recognises this explicit domain-owner marker.
    assert caught.value.__cause__.public_message == case["message"]


def test_axial_scale_does_not_also_select_global_scale_guidance():
    message = ath_import_failure(ValueError("Scale.Z must be a positive finite constant"))
    assert 'Scale must be used' not in message
    assert '“Scale.Z” must be a positive finite constant' in message


@pytest.mark.parametrize("stage", ["dimensions", "sources"])
def test_other_native_text_readers_share_sanitization(stage, monkeypatch, caplog):
    from hornlab_mesher import config_parser as native
    from server.design.textcfg import TextConfigError, parse

    original = native.ath_enclosure_sources
    calls = 0
    detail = "private engine_field: unsupported expression node List"

    def refuse(*args, **kwargs):
        nonlocal calls
        calls += 1
        # Exercise WG's second source read, after native text validation.
        if stage == "sources" and calls == 1:
            return original(*args, **kwargs)
        raise native.ConfigError(detail)

    function = "ath_enclosure_dim" if stage == "dimensions" else "ath_enclosure_sources"
    monkeypatch.setattr(native, function, refuse)
    member = "Dim=100,100,100,100" if stage == "dimensions" else "LFSource.B.Radius=60"
    with pytest.raises(TextConfigError) as caught:
        parse("OSSE = {\nL=80\n}\nMesh.Enclosure = {\n" + member + "\n}\n")
    message = str(caught.value)
    assert message == ("WG could not import this ATH configuration. Change or remove unsupported "
                       "ATH settings in the source file, then re-import it.")
    assert caught.value.public_message == message
    assert detail in caplog.text
    assert calls == (1 if stage == "dimensions" else 2)


def test_only_authored_guidance_gets_the_public_marker():
    from server.design.textcfg import TextConfigError

    assert TextConfigError("private engine_field").public_message is None
