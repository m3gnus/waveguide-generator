"""The single-scan STEP text budget.

``docs/plans/STEP-PARSER-ISOLATION.md`` requires that record counts, record
lengths, and decoded labels are refused at their stated boundaries before OCC
is handed the file.  These tests drive the scanner directly, which is where
those numbers are decided.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.cadlink.limits import (
    MAX_STEP_LABEL_CHARS,
    MAX_STEP_RECORDS,
    MAX_STEP_RECORD_BYTES,
)
from server.cadlink.step_text import (
    StepBudgetExceeded,
    StepTextError,
    decode_step_string,
    scan_step_text,
)


def _step(tmp_path: Path, body: str, *, name: str = "a.step") -> Path:
    path = tmp_path / name
    path.write_text(
        "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('x'),'2;1');\nENDSEC;\n"
        f"DATA;\n{body}\nENDSEC;\nEND-ISO-10303-21;\n",
        encoding="latin-1",
    )
    return path


# -- what a healthy file looks like ------------------------------------------


def test_a_plain_step_file_is_measured_not_parsed(tmp_path: Path) -> None:
    path = _step(
        tmp_path,
        "#1=ADVANCED_FACE('front',(#2),#3,.T.);\n#2=CARTESIAN_POINT('p',(0.,0.,0.));",
    )
    evidence = scan_step_text(path)
    # Seven framing records -- the two ISO markers, HEADER, FILE_DESCRIPTION,
    # and two ENDSECs -- plus the two entities. A record is a terminated
    # statement, not specifically an entity instance.
    assert evidence.record_count == 9
    assert evidence.advanced_face_occurrences == 1
    assert evidence.string_count == 4
    assert evidence.max_label_chars == len("front")
    assert evidence.size_bytes == path.stat().st_size
    assert set(evidence.as_dict()) == {
        "size_bytes",
        "record_count",
        "max_record_bytes",
        "max_label_chars",
        "string_count",
        "advanced_face_occurrences",
    }


def test_semicolons_inside_strings_and_comments_do_not_end_a_record(tmp_path: Path) -> None:
    path = _step(
        tmp_path,
        "#1=PRODUCT('a;b;c','d',(#2),$);\n/* a comment; with; semicolons */\n#2=X('y');",
    )
    evidence = scan_step_text(path)
    # Seven framing records plus the two entities. The comment and the
    # semicolons inside the quoted name contribute none.
    assert evidence.record_count == 9


def test_a_doubled_quote_is_an_escaped_quote_not_a_terminator(tmp_path: Path) -> None:
    path = _step(tmp_path, "#1=PRODUCT('it''s fine; really',$);")
    evidence = scan_step_text(path)
    assert evidence.record_count == 8


def test_a_string_spanning_a_read_boundary_is_still_one_string(tmp_path: Path) -> None:
    """The scanner reads in chunks, so seams are where a state machine breaks.

    The label is deliberately larger than the read chunk, and the escaped quote
    sits inside it, so both the string state and the ``''`` lookahead have to
    survive a boundary.
    """

    long_label = "a" * (2 * 1024 * 1024) + "''" + "b" * 16
    path = _step(tmp_path, f"#1=PRODUCT('{long_label}',$);")
    evidence = scan_step_text(path, max_label_chars=8 * 1024 * 1024)
    assert evidence.record_count == 8
    assert evidence.max_label_chars == 2 * 1024 * 1024 + 1 + 16


# -- the budgets -------------------------------------------------------------


def test_the_record_count_limit_refuses_at_its_boundary(tmp_path: Path) -> None:
    path = _step(tmp_path, "#1=X('a');\n#2=X('b');\n#3=X('c');")
    # Seven framing records plus three entities is ten.
    assert scan_step_text(path, max_records=10).record_count == 10
    with pytest.raises(StepBudgetExceeded, match="more than 9 entity records"):
        scan_step_text(path, max_records=9)


def test_the_record_length_limit_refuses_at_its_boundary(tmp_path: Path) -> None:
    path = _step(tmp_path, "#1=X(" + "0," * 4000 + "0);")
    largest = scan_step_text(path).max_record_bytes
    assert largest > 8000
    with pytest.raises(StepBudgetExceeded, match="single record exceeds"):
        scan_step_text(path, max_record_bytes=largest - 1)


def test_the_decoded_label_limit_refuses_at_its_boundary(tmp_path: Path) -> None:
    path = _step(tmp_path, "#1=PRODUCT('" + "n" * 500 + "',$);")
    assert scan_step_text(path, max_label_chars=500).max_label_chars == 500
    with pytest.raises(StepBudgetExceeded, match="decoded string label exceeds"):
        scan_step_text(path, max_label_chars=499)


def test_a_label_is_measured_after_decoding_not_before(tmp_path: Path) -> None:
    """``\\X2\\...\\X0\\`` shrinks: 4 raw hex digits become one character.

    Measuring the raw bytes would refuse a 100-character name for being 400
    bytes long, which is a budget gate lying about what it budgets.
    """

    encoded = "\\X2\\" + "00E9" * 100 + "\\X0\\"
    path = _step(tmp_path, f"#1=PRODUCT('{encoded}',$);")
    evidence = scan_step_text(path, max_label_chars=120)
    assert evidence.max_label_chars == 100


def test_the_shipped_limits_are_the_gate_s_table() -> None:
    assert MAX_STEP_RECORDS == 1_000_000
    assert MAX_STEP_RECORD_BYTES == 8 * 1024 * 1024
    assert MAX_STEP_LABEL_CHARS == 4 * 1024


# -- malformed input ---------------------------------------------------------


def test_a_file_without_the_part_21_marker_is_refused_on_its_first_chunk(
    tmp_path: Path,
) -> None:
    path = tmp_path / "not.step"
    path.write_bytes(b"<html>not a step file at all</html>\n" + b"x" * 4096)
    with pytest.raises(StepTextError, match="ISO-10303-21 marker"):
        scan_step_text(path)


def test_an_unterminated_string_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "open.step"
    path.write_text("ISO-10303-21;\nDATA;\n#1=X('never closed\n", encoding="latin-1")
    with pytest.raises(StepTextError, match="never closed"):
        scan_step_text(path)


def test_an_unterminated_comment_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "open.step"
    path.write_text("ISO-10303-21;\nDATA;\n/* never closed\n", encoding="latin-1")
    with pytest.raises(StepTextError, match="comment is never closed"):
        scan_step_text(path)


def test_a_file_with_no_records_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "empty.step"
    path.write_text("ISO-10303-21 but nothing else\n", encoding="latin-1")
    with pytest.raises(StepTextError, match="no terminated records"):
        scan_step_text(path)


# -- the decoder -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", "plain"),
        ("it''s", "it's"),
        ("back\\\\slash", "back\\slash"),
        ("\\X\\41", "A"),
        ("\\S\\A", "Á"),
        ("\\X2\\00E9\\X0\\", "é"),
        ("\\X4\\0001F600\\X0\\", "\U0001f600"),
        ("\\PA\\text", "text"),
    ],
)
def test_the_part_21_string_decoder(raw: str, expected: str) -> None:
    assert decode_step_string(raw) == expected


def test_the_decoder_stops_once_the_limit_is_already_exceeded() -> None:
    """A hostile label must not be expanded in full merely to be measured."""

    decoded = decode_step_string("\\X2\\" + "0041" * 100_000 + "\\X0\\", limit=16)
    assert 16 < len(decoded) < 1000
