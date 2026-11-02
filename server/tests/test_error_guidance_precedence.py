"""Outer domain guidance wins; an internal invariant always remains private."""
from hornlab_mesher.errors import InternalInvariantError, UserRefusal
from server.design.textcfg import TextConfigError
from server.design.text_import import ath_import_failure
from server.preview.errors import PublicMessage, public_error_message


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
