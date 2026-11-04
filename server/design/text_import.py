"""Persisted counterpart of the mesher's bounded text geometry contract."""

import logging
import re
from typing import Literal, get_args

logger = logging.getLogger(__name__)

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


class AthImportGuidance(str):
    """Only authored ATH guidance, preserved through exception wrappers."""


class AthImportError(ValueError):
    """A native ATH refusal with WG's next-step guidance."""

    @property
    def public_message(self) -> str | None:
        # The errors boundary recognises this marker. Arbitrary ValueError
        # text, including text syntax errors, does not become public here.
        message = self.args[0] if self.args else None
        return str(message) if isinstance(message, AthImportGuidance) else None


# Presentation hints only: native validation still decides what is accepted.
# Only these authored names and sentences may cross the display boundary.
_ATH_SETTING_FIXES = {
    "Throat.Ext.Ctrl": "Throat.Ext.Ctrl needs six finite coordinates, a scalar OSSE body with s = 0, "
    "Source.Shape = 2, ABEC.SimType = 2, zero walls and full-circle geometry. "
    "Remove extensions, slots, stretch and other features for this import.",
    "Rot": "Rot must be a finite number or an expression of p that gives a finite number.",
    "OSSE.Scale.Z": "Move Scale.Z out of the OSSE block and set it at the top level.",
    "Scale.Z": "Scale.Z must be a positive finite constant and used with a supported horn shape.",
    "Mesh.ThroatExtSegments": "Mesh.ThroatExtSegments must be an integer from 1 to 4096 and used with current ATH sampling.",
    "Mesh.Roundover": "Mesh.Roundover must be used with a supported horn shape and scale.",
    "Mesh.RearShape": "Mesh.RearShape must be used with a supported horn shape and scale.",
    "Source.Contours": "Source.Contours cannot be imported. Use a single cap or disc throat source.",
    "Scale": "Scale must be used with a supported horn shape.",
    "Throat.Ext.Length": "Throat.Ext.Length must be used with a supported horn shape.",
    "Throat.Ext.Angle": "Throat.Ext.Angle must be used with a supported horn shape.",
    "Mesh.VerticalOffset": "Mesh.VerticalOffset must be used with a supported horn shape.",
    "Source.Shape": "Source.Shape must be used with a supported horn shape.",
    "ABEC.SimType": "ABEC.SimType must select a supported baffle or free-standing arrangement.",
    "Mesh.Enclosure.Dim": "Mesh.Enclosure.Dim must contain four positive finite distances (+X, +Y, -X, -Y).",
    "LFSource.A": "LFSource.A needs finite values, a nonnegative integer SID, and positive Radius and Resolution.",
    "LFSource.B": "LFSource.B needs finite values, a nonnegative integer SID, and positive Radius and Resolution.",
    "Mesh.Enclosure": "Mesh.Enclosure must be used with a supported horn shape.",
    "Length": "The selected ATH profile must set Length in its profile block.",
}


def ath_import_failure(reason: Exception, *, setting: str | None = None) -> AthImportGuidance:
    """Log native diagnostics; display only authored setting names and remedies."""
    logger.warning("ATH import could not use the configuration: %s", reason,
                   exc_info=(type(reason), reason, reason.__traceback__))
    if setting == "ATH version comments":
        return AthImportGuidance(
            "The ATH version comments disagree. Keep one comment for the version "
            "that wrote this file, then re-import it.")
    if setting is None:
        # Matching a known name affects guidance only, never acceptance. Unknown
        # or reworded native diagnostics always receive a safe fallback.
        settings = [key for key in _ATH_SETTING_FIXES
                    if re.search(r"(?<![\w.])" + re.escape(key) + (r"(?!\w)" if "." in key else r"(?![\w.])"), str(reason))]
    else:
        settings = [setting] if setting in _ATH_SETTING_FIXES else []
    if not settings:
        from hornlab_mesher.errors import UserRefusal

        if isinstance(reason, UserRefusal):
            return AthImportGuidance(f"{reason.user_message} {reason.next_step}".strip())
        return AthImportGuidance(
            "WG could not import this ATH configuration. Change or remove unsupported "
            "ATH settings in the source file, then re-import it.")
    fixes = " ".join(_ATH_SETTING_FIXES[key] for key in settings)
    names = ", ".join(settings)
    message = f"{fixes} Change or remove {names} in the source file, then re-import it."
    # These are authored ATH file names, so use the existing client rule for
    # quoted user names. Unquoted internal names remain blocked by that owner.
    dotted_names = "|".join(re.escape(key) for key in sorted(_ATH_SETTING_FIXES, key=len, reverse=True) if "." in key)
    return AthImportGuidance(re.sub(r"\b(?:" + dotted_names + r")\b", lambda match: f"“{match[0]}”", message))


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
                raise AthImportError(ath_import_failure(exc, setting="Rot")) from exc
    except AthImportError:
        raise
    except (ValueError, ArithmeticError) as exc:
        raise AthImportError(ath_import_failure(exc)) from exc


def ath_extension_segments(value: object) -> int:
    """Reuse the mesher's exact count parser with WG's next-step guidance."""
    from hornlab_mesher.text_import import ath_throat_extension_segments

    try:
        return ath_throat_extension_segments({"athThroatExtSegments": value})
    except ValueError as exc:
        raise AthImportError(ath_import_failure(exc, setting="Mesh.ThroatExtSegments")) from exc
