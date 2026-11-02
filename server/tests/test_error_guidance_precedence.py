"""Outer domain guidance wins; an internal invariant always remains private."""
from hornlab_mesher.errors import InternalInvariantError, UserRefusal
from server.design.textcfg import TextConfigError
from server.design.text_import import ath_import_failure
from server.preview.errors import PublicMessage, public_error_message
from server.design.textcfg import parse
from hornlab_mesher.config_parser import ConfigError, parse_text_config
from server.cadlink.intake.ingest import IngestRefusal
import pytest


def test_explicit_outer_message_is_not_replaced_by_a_native_cause():
    native = UserRefusal("ATH text", "Native cause.", "Correct the native value.")
    outer = TextConfigError(PublicMessage("Change the imported setting, then re-import the file."))
    outer.__cause__ = native
    assert public_error_message(outer) == "Change the imported setting, then re-import the file."


def test_authored_ath_guidance_never_hides_an_internal_invariant_cause():
    native = UserRefusal("ATH text", "Native cause.", "Correct the native value.")
    native.__cause__ = InternalInvariantError("generated internal state is inconsistent")
    outer = TextConfigError(ath_import_failure(native))
    outer.__cause__ = native
    assert outer.public_message
    assert public_error_message(outer) is None


def test_unsupported_ath_setting_keeps_its_native_public_remedy():
    text = "R-OSSE = {\nR = 200\ns1 = .5\n}\nTerm.n = 6"
    with pytest.raises(ConfigError) as native:
        parse_text_config(text)
    with pytest.raises(TextConfigError) as outer:
        parse(text)
    assert str(outer.value.__cause__) == str(native.value)
    assert public_error_message(outer.value) == public_error_message(native.value)
    assert "“Term.n”" in public_error_message(outer.value)
    assert "Remove these settings from the ATH text, then import it again." in public_error_message(outer.value)


def test_untyped_native_program_diagnostic_does_not_become_ath_guidance():
    text = ath_import_failure(ConfigError("generated state sentinel"))
    assert "sentinel" not in text
    assert text.startswith("WG could not import this ATH configuration.")


def test_authored_expression_remedy_precedes_bare_library_diagnostic_filter():
    message = "Throat angle (a0): math domain error. Change the expression."
    assert public_error_message(ValueError(message)) == message
    assert public_error_message(ValueError("math domain error")) is None


def test_a_cad_stage_name_alone_does_not_make_diagnostics_public():
    error = IngestRefusal("stage 5 role resolution", "role resolution: generated state sentinel")
    error.__cause__ = TypeError("generated state sentinel")
    assert public_error_message(error) is None
