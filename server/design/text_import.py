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


class AthImportError(ValueError):
    """A native ATH refusal with WG's next-step guidance."""


def ath_import_failure(reason: Exception) -> str:
    """Keep native detail intact; never select wording by matching its text."""
    return f"{str(reason).rstrip('. ')}. Change or remove the named ATH setting, then try again."


def validate_ath_mesher_config(config: dict) -> None:
    """Use the native geometry owner for both text and dictionary clients."""
    from hornlab_mesher.config_builder import build_geometry_params

    try:
        params, formula, _ = build_geometry_params(config)
        if formula == "R-OSSE" and uses_ath_v3_import(config.get("_textImportVersion")) and "rot" in params:
            from hornlab_mesher.profile_formulas import calculate_rosse

            # Native parsing permits expressions. Evaluate through the same
            # public profile owner so unknown names in Rot cannot be saved as
            # an import that only fails when preview eventually samples it.
            try:
                calculate_rosse(0, 0, params)
            except (ValueError, ArithmeticError) as exc:
                raise ValueError(f"The R-OSSE profile could not be evaluated with Rot: {exc}") from exc
    except (ValueError, ArithmeticError) as exc:
        raise AthImportError(ath_import_failure(exc)) from exc


def ath_extension_segments(value: object) -> int:
    """Reuse the mesher's exact count parser with WG's next-step guidance."""
    from hornlab_mesher.text_import import ath_throat_extension_segments

    try:
        return ath_throat_extension_segments({"athThroatExtSegments": value})
    except ValueError as exc:
        raise ValueError(ath_import_failure(exc)) from exc
