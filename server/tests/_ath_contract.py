"""Versioned ATH test inputs and the pinned mesher's fresh-import oracle."""

import pytest

from server.design.text_import import GEOMETRY_STAMP_LABEL, TEXT_IMPORT_V1
from server.design.textcfg import TextConfigError, parse


def saved_v1(text: str) -> str:
    return f"; {GEOMETRY_STAMP_LABEL} {TEXT_IMPORT_V1}\n{text}"


def assert_v2_refusal(text: str) -> None:
    from hornlab_mesher.config_parser import ConfigError, parse_text_config

    with pytest.raises(ConfigError) as reference:
        parse_text_config(text)
    with pytest.raises(TextConfigError) as actual:
        parse(text)
    assert str(actual.value) == str(reference.value)
