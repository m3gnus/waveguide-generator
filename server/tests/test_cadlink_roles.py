from __future__ import annotations

import pytest

from server.cadlink.roles import canonical_source_role


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("LF", "LF"),
        ("mf", "MF"),
        (" hf ", "HF"),
        ("rigid", "rigid"),
        ("interface", "interface"),
        (" Rigid ", " Rigid "),
    ],
)
def test_canonical_source_role_only_rewrites_driver_bands(
    role: str, expected: str
) -> None:
    assert canonical_source_role(role) == expected
