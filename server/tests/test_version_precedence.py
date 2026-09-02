"""Release-tag parsing and pre-release precedence (SemVer rule 11)."""

from __future__ import annotations

import pytest

from server.updates.service import (
    STABLE_TAG_RE,
    TAG_RE,
    _is_update_layer_carrier,
    _version,
)


@pytest.mark.parametrize(
    "tag",
    [
        "v0.4.0",
        "v0.4.0-beta.1",
        "v1.0.0-alpha",
        "v1.0.0-alpha.beta",
        "v1.0.0-0.3.7",
        "v1.0.0-x-y-z.--",
        "v10.20.30",
    ],
)
def test_supported_tags_parse(tag: str):
    assert TAG_RE.fullmatch(tag) is not None
    _version(tag)


@pytest.mark.parametrize(
    "tag",
    [
        "v0.4",
        "v0.4.0.1",
        "v0.4.0-",  # empty pre-release label
        "v0.4.0-beta..1",  # empty identifier
        "v0.4.0+build",  # build metadata is not accepted
        "v0.4.0-beta 1",
    ],
)
def test_unsupported_tags_are_refused(tag: str):
    assert TAG_RE.fullmatch(tag) is None
    with pytest.raises(ValueError):
        _version(tag)


@pytest.mark.parametrize("tag", ["v0.4.0-updates", "v0.4.0-beta.1-updates"])
def test_a_companion_tag_is_refused_as_a_version(tag: str):
    """`updates` is a valid SemVer identifier, so the tag shape alone accepts it.

    A companion carries another version's update layers; parsing it as a version
    would sort it immediately below the release it belongs to and let it stand in
    for that release.
    """

    assert TAG_RE.fullmatch(tag) is not None
    with pytest.raises(ValueError):
        _version(tag)


def test_a_bare_version_without_the_v_prefix_still_parses():
    assert _version("0.4.0") == _version("v0.4.0")


def test_a_prerelease_sorts_below_the_release_it_precedes():
    """SemVer rule 11: 0.4.0-beta.1 < 0.4.0."""

    assert _version("v0.4.0-beta.1") < _version("v0.4.0")
    assert _version("v0.4.0") > _version("v0.4.0-rc.9")


def test_a_prerelease_still_outranks_the_previous_release():
    assert _version("v0.4.0-beta.1") > _version("v0.3.0")
    assert _version("v0.4.0-beta.1") > _version("v0.3.9")


def test_prerelease_identifiers_compare_left_to_right():
    assert _version("v1.0.0-alpha") < _version("v1.0.0-alpha.1")
    assert _version("v1.0.0-alpha.1") < _version("v1.0.0-alpha.beta")
    assert _version("v1.0.0-alpha.beta") < _version("v1.0.0-beta")
    assert _version("v1.0.0-beta") < _version("v1.0.0-beta.2")
    assert _version("v1.0.0-beta.2") < _version("v1.0.0-beta.11")
    assert _version("v1.0.0-beta.11") < _version("v1.0.0-rc.1")
    assert _version("v1.0.0-rc.1") < _version("v1.0.0")


def test_numeric_identifiers_compare_numerically_not_as_text():
    """`beta.11` must outrank `beta.2`; string order would invert that."""

    assert _version("v0.4.0-beta.11") > _version("v0.4.0-beta.2")


def test_a_numeric_identifier_ranks_below_an_alphanumeric_one():
    assert _version("v1.0.0-1") < _version("v1.0.0-alpha")


def test_core_numbers_still_decide_first():
    assert _version("v0.3.0") < _version("v0.4.0") < _version("v1.0.0")
    assert _version("v0.4.1") > _version("v0.4.0-rc.1")


def test_the_strict_tag_shape_refuses_every_prerelease():
    """The validators that must not admit a beta keep the narrow shape."""

    assert STABLE_TAG_RE.fullmatch("v0.4.0") is not None
    assert STABLE_TAG_RE.fullmatch("v0.4.0-beta.1") is None
    assert STABLE_TAG_RE.fullmatch("v0.4.0-updates") is None


@pytest.mark.parametrize(
    ("tag", "carries"),
    [
        ("v0.4.0", True),  # today: the layers live on the release itself
        ("v0.4.0-updates", True),  # #57: the companion pre-release
        ("v0.4.0-beta.1-updates", True),  # #58: a beta's companion
        ("v0.4.0-beta.1", False),  # a beta is not a companion
        ("v0.4.0-rc.1", False),
        ("nightly", False),
        ("v0.4-updates", False),
    ],
)
def test_only_releases_and_their_companions_carry_update_layers(tag: str, carries: bool):
    assert _is_update_layer_carrier(tag) is carries
