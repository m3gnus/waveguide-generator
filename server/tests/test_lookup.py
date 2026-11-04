"""Consumer coverage for the pinned experimental whole-meridian profile."""
from __future__ import annotations

import re

import pytest
from hornlab_mesher.profile_formulas import lookup_profile_array
from pydantic import ValidationError

from server.design.schema import LookupConfig
from server.design_io.reader import parse, read_design
from server.design.textcfg import TextConfigError

TABLE = [[0, 12.7], [20, 25], [80, 60]]


def input_design(**changes):
    return LookupConfig.model_validate({"formula": "LOOKUP", "lookup_profile": TABLE, **changes})


def design(**changes):
    return read_design({"formula": "LOOKUP", "lookup_profile": TABLE, **changes})


@pytest.mark.parametrize("table,reason", [
    ([], "at least two"), ([[0, 1]], "at least two"),
    ([[0, 1, 2], [1, 2, 3]], "at least two"),
    ([[0, float('nan')], [1, 2]], "only finite"),
    ([[0, 1], [float('inf'), 2]], "only finite"),
    ([[0, 1], [0, 2]], "strictly increasing"),
    ([[1, 1], [0, 2]], "strictly increasing"),
])
def test_table_validation_matches_pin(table, reason):
    with pytest.raises(ValueError, match=reason) as pinned:
        lookup_profile_array({"lookupProfile": table})
    with pytest.raises(ValidationError, match=re.escape(str(pinned.value))):
        input_design(lookup_profile=table)


def test_only_z_is_monotonic_and_alias_is_supported():
    table = [[-10, 0], [0, -2], [10, 1]]
    assert input_design(lookup_profile=table).lookup_profile == [tuple(row) for row in table]
    assert read_design({"formula": "LOOKUP", "lookupProfile": TABLE}).formula == "FREEFORM"


@pytest.mark.parametrize("key", ["L", "R", "n", "s", "s1", "s2", "rotation", "tmax", "m", "r", "b", "a", "k", "q", "h", "throat_adapter"])
def test_analytic_keys_refused_even_at_zero(key):
    with pytest.raises(ValidationError, match="formula LOOKUP does not (accept|support)"):
        input_design(**{key: 0})


@pytest.mark.parametrize("key", ["throat_ext_angle", "throat_ext_length", "slot_length"])
def test_extensions_refused(key):
    with pytest.raises(ValidationError, match="defines the whole meridian"):
        input_design(**{key: 10})
    assert input_design(**{key: 0})


def test_guiding_curve_refused_with_a_lookup_message():
    with pytest.raises(ValidationError, match="formula LOOKUP does not support guiding_curve"):
        input_design(guiding_curve={"curve_type": 1, "width": 100})


@pytest.mark.parametrize("changes", [
    {"ath_nac": 3}, {"ath_nac_present": False}, {"ath_axial_sampling_rule": "ath-v2025-06"},
])
def test_ath_only_fields_refused(changes):
    with pytest.raises(ValidationError, match="LOOKUP does not accept imported ATH geometry"):
        input_design(**changes)


@pytest.mark.parametrize("table", [
    [[True, 1], [2, 3]], [[0, 1], ["2", 3]], [[0, 1], [2, None]],
])
def test_non_number_cells_refused_before_numpy_coercion(table):
    with pytest.raises(ValidationError, match="cells must be numbers"):
        input_design(lookup_profile=table)


def test_cli_reads_a_lookup_json_design_and_refuses_other_extensions(tmp_path):
    import argparse

    from server.cli.solve import _read_request

    original = input_design()
    path = tmp_path / "lookup.json"
    path.write_text(original.model_dump_json(indent=2) + "\n", encoding="utf-8")
    args = argparse.Namespace(request=None, design=path, overlay=None, engine=None)
    assert _read_request(args).design.formula == "FREEFORM"
    other = tmp_path / "lookup.txt"
    other.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match=r"\.mwg, \.cfg or \.json"):
        _read_request(argparse.Namespace(request=None, design=other, overlay=None, engine=None))


@pytest.mark.parametrize("text", ['{"formula": "OSSE"}\n', '{\n "setting": 1\n}\n', "{ not json"])
def test_only_lookup_json_takes_the_json_design_path(text: str) -> None:
    # Anything else starting with "{" keeps the config parser's own refusal.
    with pytest.raises(TextConfigError) as raised:
        parse(text)
    assert "LOOKUP" not in str(raised.value)


def test_schema_validation_requires_explicit_import_without_fitting(monkeypatch):
    from server.design.schema import DesignConfig
    import server.jobs.freeform_conversion as conversion

    def unexpected(*args, **kwargs):
        pytest.fail('Schema validation must not sample or fit geometry')

    monkeypatch.setattr(conversion, 'convert_design', unexpected)
    with pytest.raises(ValidationError) as raised:
        DesignConfig.model_validate({'formula': 'LOOKUP', 'lookup_profile': TABLE})
    error = raised.value.errors()[0]
    assert error['type'] == 'lookup_import_required'
    assert error['ctx']['key'] == 'Family'
    assert 'Open design' in error['msg']


def test_low_level_text_reader_requires_explicit_import():
    import json
    from server.design.textcfg import parse as parse_schema_text

    with pytest.raises(TextConfigError, match='Open design'):
        parse_schema_text(json.dumps({'formula': 'LOOKUP', 'lookup_profile': TABLE}))
