"""Persisted counterpart of the mesher's bounded text geometry contract."""

from typing import Literal, get_args

TextImportVersion = Literal["ath-2026-08c-v1", "ath-2026-09a-v2", "ath-2026-09a-v3"]
TEXT_IMPORT_V1, TEXT_IMPORT_V2, *_NEW_IMPORT_VERSIONS = get_args(TextImportVersion)
TEXT_IMPORT_VERSION = get_args(TextImportVersion)[-1]
TEXT_IMPORT_VERSIONS = frozenset(get_args(TextImportVersion))
NATIVE_GEOMETRY_VERSION = "native-v1"
GEOMETRY_STAMP_LABEL = "Waveguide Generator geometry-interpretation:"


def uses_current_ath_import(version: str | None) -> bool:
    """The V2 rules inherited by later imports, without migrating saved V1."""
    return version in TEXT_IMPORT_VERSIONS - {TEXT_IMPORT_V1}


def uses_ath_v3_import(version: str | None) -> bool:
    """Corrections that must never change saved V1 or V2 geometry."""
    return version in _NEW_IMPORT_VERSIONS


ATH_EXTENSION_COUNT_MESSAGE = "The throat extension count must be a whole number from 1 to 4096. Set Mesh.ThroatExtSegments within that range."
ATH_EXTENSION_SAMPLING_MESSAGE = "The throat extension count needs current ATH sampling. Remove Mesh.ThroatExtSegments from this older ATH file."
ATH_AXIAL_SURFACE_MESSAGE = "Axial scale cannot be combined with the ATH mouth lip or Opus rear. Remove Scale.Z or turn those shapes off."


def ath_extension_segments(value: object) -> int:
    """Reuse the mesher's exact count parser with WG's actionable wording."""
    from hornlab_mesher.text_import import ath_throat_extension_segments

    try:
        return ath_throat_extension_segments({"athThroatExtSegments": value})
    except ValueError as exc:
        raise ValueError(ATH_EXTENSION_COUNT_MESSAGE) from exc
