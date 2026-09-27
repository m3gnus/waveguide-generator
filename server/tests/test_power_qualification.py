"""Radiated-power qualification: marking, propagation and read-time flags."""

from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3

import pytest

from server.jobs.result_contracts import RESULT_ENVELOPE_ADAPTER
from server.jobs.store import JobStore
from server.solver.power_qualification import (
    PERSISTED_MARKER,
    UNQUALIFIED_MESSAGE,
    annotate_results,
    annotate_stored_text,
    qualify_channel,
)

DATA = Path(__file__).parent / "data"
SPEAKER2 = DATA / "power-qualification-speaker2-v4.json"
SPEAKER2_READ_TIME = DATA / "power-qualification-speaker2-v4.read-time.json"


def _channel(
    agreement_db: list[float | None],
    *,
    frequencies: list[float] | None = None,
    surface: list[float | None] | None = None,
    sphere: list[float | None] | None = None,
    formulation: str | None = "complex_k",
    validity_hz: float | None = None,
) -> dict:
    frequencies = frequencies or [100.0 * (index + 1) for index in range(len(agreement_db))]
    if surface is None:
        surface = [1.0e-5] * len(agreement_db)
    if sphere is None:
        sphere = [
            None
            if face is None or value is None or face <= 0.0
            else face * 10.0 ** (value / 10.0)
            for face, value in zip(surface, agreement_db)
        ]
    metadata: dict = {
        "source_ids": ["src"],
        "radiated_power": {
            "surface_w": surface,
            "sphere_w": sphere,
            "sphere_coverage_sr": 4.0 * math.pi,
            "definition": "test",
            "agreement_db": agreement_db,
        },
    }
    if formulation is not None:
        metadata["metal"] = {"formulation": formulation, "complex_k_shift": 0.005}
    if validity_hz is not None:
        metadata["per_source_frequency_validity"] = {
            "src": {"effective_max_valid_frequency_hz": validity_hz}
        }
    return {"frequencies": frequencies, "metadata": metadata}


def _statuses(flags: dict) -> list[str]:
    return list(flags["frequency_status"])


# --- per-frequency marking -------------------------------------------------


def test_threshold_is_strictly_greater_than_half_a_decibel() -> None:
    flags = qualify_channel(_channel([0.5, -0.5, 0.5001, -0.51, 0.0]))

    assert _statuses(flags) == [
        "qualified",
        "qualified",
        "unqualified",
        "unqualified",
        "qualified",
    ]
    assert flags["status"] == "unqualified"
    assert flags["frequency_reasons"][2:4] == ["power_mismatch", "power_mismatch"]
    assert flags["unqualified_ranges"] == [
        {
            "start_hz": 300.0,
            "end_hz": 400.0,
            "count": 2,
            "reasons": ["power_mismatch"],
            "channels": [],
        }
    ]
    assert flags["message"] == UNQUALIFIED_MESSAGE
    assert flags["worst"] == {"frequency_hz": 400.0, "agreement_db": -0.51}


def test_all_within_threshold_is_qualified_with_no_message() -> None:
    flags = qualify_channel(_channel([0.1, -0.49, 0.5]))

    assert flags["status"] == "qualified"
    assert flags["unqualified_ranges"] == []
    assert flags["message"] is None
    assert flags["provenance"]["formulation"] == "complex_k"
    assert flags["provenance"]["complex_k_shift"] == 0.005


@pytest.mark.parametrize(
    ("face", "reason"),
    [
        (0.0, "nonpositive_face_power"),
        (-4.58e-9, "nonpositive_face_power"),
        (None, "nonfinite_face_power"),
        (float("nan"), "nonfinite_face_power"),
    ],
)
def test_nonpositive_or_missing_face_power_is_a_visible_failure(
    face: float | None, reason: str
) -> None:
    # A ratio cannot be formed here; that must never read as "nothing to say".
    channel = _channel(
        [0.1, None, 0.1],
        surface=[1.0e-5, face, 1.0e-5],
        sphere=[1.0e-5, 2.0e-9, 1.0e-5],
    )

    flags = qualify_channel(channel)

    assert _statuses(flags) == ["qualified", "unqualified", "qualified"]
    assert flags["frequency_reasons"][1] == reason
    assert flags["status"] == "unqualified"
    assert flags["reasons"] == [reason]


def test_nonpositive_sphere_power_is_unqualified() -> None:
    channel = _channel(
        [0.1, None], surface=[1.0e-5, 1.0e-5], sphere=[1.0e-5, -1.0e-9]
    )

    flags = qualify_channel(channel)

    assert flags["frequency_reasons"] == [None, "invalid_sphere_power"]


def test_frequencies_above_the_validity_limit_are_ignored() -> None:
    channel = _channel(
        [0.1, 0.2, -9.0, -16.0],
        frequencies=[500.0, 1000.0, 2500.0, 4900.0],
        surface=[1.0e-5, 1.0e-5, 1.0e-5, -1.0e-9],
        validity_hz=2106.0,
    )

    flags = qualify_channel(channel)

    assert _statuses(flags) == [
        "qualified",
        "qualified",
        "outside_validity",
        "outside_validity",
    ]
    assert flags["status"] == "qualified"
    assert flags["validity_max_hz"] == 2106.0


def test_frequency_exactly_at_the_validity_limit_is_checked() -> None:
    channel = _channel(
        [-1.0], frequencies=[2106.0], validity_hz=2106.0
    )

    assert _statuses(qualify_channel(channel)) == ["unqualified"]


def test_missing_power_check_is_unknown_not_qualified() -> None:
    absent = {"frequencies": [100.0], "metadata": {"metal": {"formulation": "complex_k"}}}
    never_computed = {
        "frequencies": [100.0],
        "metadata": {"radiated_power": {"definition": "no series returned"}},
    }

    for payload in (absent, never_computed):
        flags = qualify_channel(payload)
        assert flags["status"] == "unknown"
        assert flags["unknown_reason"] == "power_check_unavailable"
        assert _statuses(flags) == ["unchecked"]
        assert flags["message"] is None


@pytest.mark.parametrize(
    ("surface", "sphere", "reason"),
    [
        # Review reproducers: an entirely invalid computed series is a
        # failure in band, not an unavailable check.
        ([None], [1.0], "nonfinite_face_power"),
        ([-1.0e-6], [None], "nonpositive_face_power"),
        ([None], [None], "nonfinite_face_power"),
        ([1.0e-6], [None], "invalid_sphere_power"),
    ],
)
def test_an_all_invalid_computed_series_is_unqualified_not_unknown(
    surface: list[float | None], sphere: list[float | None], reason: str
) -> None:
    flags = qualify_channel(
        _channel([None], surface=surface, sphere=sphere, validity_hz=1000.0)
    )

    assert flags["status"] == "unqualified"
    assert _statuses(flags) == ["unqualified"]
    assert flags["frequency_reasons"] == [reason]
    assert flags["message"] == UNQUALIFIED_MESSAGE


def test_missing_formulation_provenance_is_unknown_unless_a_check_failed() -> None:
    clean = qualify_channel(_channel([0.1], formulation=None))
    failed = qualify_channel(_channel([-3.0], formulation=None))

    assert clean["status"] == "unknown"
    assert clean["unknown_reason"] == "provenance_missing"
    assert clean["provenance"]["recorded"] is False
    # The failure is its own evidence; missing provenance cannot excuse it.
    assert failed["status"] == "unqualified"


def test_ranges_follow_ascending_frequency_not_storage_order() -> None:
    channel = _channel(
        [-1.0, 0.1, -2.0, -3.0],
        frequencies=[400.0, 100.0, 300.0, 200.0],
    )

    flags = qualify_channel(channel)

    assert [(item["start_hz"], item["end_hz"], item["count"]) for item in flags["unqualified_ranges"]] == [
        (200.0, 400.0, 3)
    ]


# --- envelopes and combined propagation -----------------------------------


def _multi(channels: dict, *, validity: dict | None = None) -> dict:
    return {
        "result_kind": "multi_channel",
        "result_contract_version": 2,
        "channel_order": list(channels),
        "channels": channels,
        "metadata": {
            "solver_engine": {"formulation": "complex_k", "complex_k_shift": 0.005},
            **({"per_source_frequency_validity": validity} if validity else {}),
        },
        "frequencies": [100.0, 200.0, 300.0],
    }


def _combined(members: list[str]) -> dict:
    return {
        "frequencies": [100.0, 200.0, 300.0],
        "metadata": {
            "source_ids": ["lf", "hf"],
            "combine": {"members": members, "crossovers_hz": [150.0]},
            "derived_from_channels": members,
        },
    }


def _member(source: str, agreement: list[float]) -> dict:
    channel = _channel(agreement, frequencies=[100.0, 200.0, 300.0], formulation=None)
    channel["metadata"]["source_ids"] = [source]
    return channel


def test_combined_result_inherits_and_names_an_unqualified_member() -> None:
    results = _multi(
        {
            "drive-lf": _member("lf", [-1.0, 0.1, -2.0]),
            "drive-hf": _member("hf", [0.1, 0.1, 0.1]),
            "combined": _combined(["drive-lf", "drive-hf"]),
        }
    )

    annotated = annotate_results(results)
    combined = annotated["channels"]["combined"]["metadata"]["power_qualification"]

    assert combined["status"] == "unqualified"
    assert combined["unqualified_channels"] == ["drive-lf"]
    assert combined["members"] == {"drive-lf": "unqualified", "drive-hf": "qualified"}
    assert _statuses(combined) == ["unqualified", "qualified", "unqualified"]
    assert [item["channels"] for item in combined["unqualified_ranges"]] == [
        ["drive-lf"],
        ["drive-lf"],
    ]
    assert combined["message"] == UNQUALIFIED_MESSAGE
    assert annotated["metadata"]["power_qualification_summary"]["channels"] == {
        "drive-lf": "unqualified",
        "drive-hf": "qualified",
        "combined": "unqualified",
    }


def test_combined_member_flags_outside_its_validity_do_not_propagate() -> None:
    results = _multi(
        {
            "drive-lf": _member("lf", [0.1, 0.1, -9.0]),
            "drive-hf": _member("hf", [0.1, 0.1, 0.1]),
            "combined": _combined(["drive-lf", "drive-hf"]),
        },
        validity={
            "lf": {"effective_max_valid_frequency_hz": 250.0},
            "hf": {"effective_max_valid_frequency_hz": 1000.0},
        },
    )

    annotated = annotate_results(results)

    lf = annotated["channels"]["drive-lf"]["metadata"]["power_qualification"]
    combined = annotated["channels"]["combined"]["metadata"]["power_qualification"]
    assert _statuses(lf) == ["qualified", "qualified", "outside_validity"]
    assert combined["status"] == "qualified"


def test_combined_judges_members_only_inside_its_own_validity_band() -> None:
    # Review reproducer: the sum's ceiling is LF's 250 Hz, and HF's only
    # failure, at 300 Hz, lies above it.
    results = _multi(
        {
            "drive-lf": _member("lf", [0.1, 0.1, 0.1]),
            "drive-hf": _member("hf", [0.1, 0.1, -3.0]),
            "combined": _combined(["drive-lf", "drive-hf"]),
        },
        validity={
            "lf": {"effective_max_valid_frequency_hz": 250.0},
            "hf": {"effective_max_valid_frequency_hz": 1000.0},
        },
    )

    annotated = annotate_results(results)

    hf = annotated["channels"]["drive-hf"]["metadata"]["power_qualification"]
    combined = annotated["channels"]["combined"]["metadata"]["power_qualification"]
    assert hf["status"] == "unqualified"
    assert combined["validity_max_hz"] == 250.0
    assert _statuses(combined) == ["qualified", "qualified", "outside_validity"]
    assert combined["unqualified_channels"] == []
    assert combined["members"] == {"drive-lf": "qualified", "drive-hf": "qualified"}
    assert combined["status"] == "qualified"


def test_combined_is_unknown_when_a_member_carries_no_power_check() -> None:
    hf = {"frequencies": [100.0, 200.0, 300.0], "metadata": {"source_ids": ["hf"]}}
    results = _multi(
        {
            "drive-lf": _member("lf", [0.1, 0.1, 0.1]),
            "drive-hf": hf,
            "combined": _combined(["drive-lf", "drive-hf"]),
        }
    )

    combined = annotate_results(results)["channels"]["combined"]["metadata"][
        "power_qualification"
    ]

    assert combined["status"] == "unknown"
    assert combined["unknown_reason"] == "member_unknown"


def test_annotation_recomputes_stale_flags_and_never_mutates_its_input() -> None:
    results = _multi(
        {
            "drive-lf": _member("lf", [-1.0, 0.1, 0.1]),
            "drive-hf": _member("hf", [0.1, 0.1, 0.1]),
            "combined": _combined(["drive-hf"]),
        }
    )
    results["channels"]["combined"]["metadata"]["power_qualification"] = {"status": "unqualified"}
    before = copy.deepcopy(results)

    annotated = annotate_results(results)

    assert results == before
    # A recombine that drops the LF member leaves a qualified sum.
    assert annotated["channels"]["combined"]["metadata"]["power_qualification"]["status"] == "qualified"
    assert annotated["metadata"]["power_qualification_version"] == 1


def test_annotation_never_changes_any_level() -> None:
    fixture = json.loads(SPEAKER2.read_text())

    annotated = annotate_results(fixture)

    for channel_id, channel in fixture["channels"].items():
        assert annotated["channels"][channel_id]["spl_on_axis"] == channel["spl_on_axis"]
        assert annotated["channels"][channel_id]["metadata"].get("radiated_power") == channel[
            "metadata"
        ].get("radiated_power")


# --- the archived Speaker2 v4 run ------------------------------------------


def test_archived_speaker2_lf_is_flagged_from_200_hz_and_hf_is_clean() -> None:
    fixture = json.loads(SPEAKER2.read_text())

    annotated = annotate_results(fixture, evaluated="read_time")
    lf = annotated["channels"]["drive-lf"]["metadata"]["power_qualification"]
    hf = annotated["channels"]["drive-hf"]["metadata"]["power_qualification"]
    combined = annotated["channels"]["combined"]["metadata"]["power_qualification"]

    assert lf["status"] == "unqualified"
    assert lf["evaluated"] == "read_time"
    assert lf["validity_max_hz"] == pytest.approx(2106.215334780063)
    checked = [state for state in lf["frequency_status"] if state in {"qualified", "unqualified"}]
    assert (checked.count("unqualified"), len(checked)) == (11, 12)
    assert lf["unqualified_ranges"][0]["start_hz"] == 200.0
    assert [
        (round(item["start_hz"]), round(item["end_hz"]), item["count"])
        for item in lf["unqualified_ranges"]
    ] == [(200, 1212, 10), (1809, 1809, 1)]
    assert lf["worst"]["frequency_hz"] == pytest.approx(1809.4714484698588)
    assert lf["worst"]["agreement_db"] == pytest.approx(-7.456, abs=1e-3)
    assert lf["provenance"] == {
        "engine": "metal",
        "package": "hornlab-metal-bem",
        "formulation": "complex_k",
        "complex_k_shift": 0.005,
        "package_version": "0.1.0",
        "solver_pin": "e7e32d0530d41ae8482ea156f2d9aca8f10b8623",
        "recorded": True,
    }

    assert hf["status"] == "qualified"
    assert hf["unqualified_ranges"] == []
    assert hf["validity_max_hz"] == pytest.approx(5970.140381091339)
    # 6.0 kHz and above sit beyond the HF validity limit.
    assert hf["frequency_status"].count("outside_validity") == 7

    assert combined["status"] == "unqualified"
    assert combined["unqualified_channels"] == ["drive-lf"]
    assert annotated["metadata"]["power_qualification_summary"]["status"] == "unqualified"
    assert "power_qualification_version" not in annotated["metadata"]


def test_archived_speaker2_read_time_flags_match_the_shared_golden() -> None:
    # The frontend tests read the same golden, so the two sides agree on shape.
    fixture = json.loads(SPEAKER2.read_text())
    golden = json.loads(SPEAKER2_READ_TIME.read_text())

    assert annotate_results(fixture, evaluated="read_time") == golden


# --- storage: solve-time persistence and read-time archived flags ----------


def _envelope(kind: str = "parametric") -> dict:
    digest = "a" * 64
    envelope: dict = {
        "result_kind": kind,
        "result_contract_version": 1 if kind == "parametric" else 2,
        "client_request_id": None,
        "client_metadata": {},
        "provenance": {
            "schema_version": 1,
            "wg_version": "test",
            "dependency_shas": {},
            "request_sha256": digest,
            "geometry_sha256": digest,
            "solve_options_sha256": digest,
            "request_identity": "execution",
            "execution_request_sha256": digest,
            "execution_geometry_sha256": digest,
            "execution_solve_options_sha256": digest,
            "effective_request_sha256": digest,
            "effective_geometry_sha256": digest,
            "effective_solve_options_sha256": digest,
            "resolved_engine": "test",
        },
        "metadata": {"field_plane_available": False, "field_trace_bytes": None},
        "frequencies": [100.0],
    }
    return envelope


def _job(job_id: str, status: str = "running") -> dict:
    now = datetime.now().isoformat()
    return {
        "id": job_id,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "queued_at": now,
        "progress": 0.0,
        "stage": status,
        "stage_message": status,
        "config_json": {"design": {"formula": "OSSE", "L": 120}},
        "config_summary_json": {"formula_type": "OSSE"},
        "task_metadata": {},
    }


def _raw_row(store_path: Path, job_id: str) -> tuple[str, str]:
    with sqlite3.connect(store_path) as conn:
        text, digest = conn.execute(
            "SELECT results_json, results_sha256 FROM simulation_results WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    return text, digest


def test_completed_results_persist_solve_time_flags_and_serve_them_verbatim(
    tmp_path: Path,
) -> None:
    path = tmp_path / "simulations.db"
    store = JobStore(path)
    store.initialize()
    store.create_job(_job("fresh"))
    results = _envelope()
    results["metadata"].update(_channel([-1.0])["metadata"])
    store.complete_job(
        "fresh",
        results,
        {"status": "complete", "completed_at": datetime.now().isoformat()},
        {"status": "complete", "progress": 1.0},
    )

    stored_text, stored_digest = _raw_row(path, "fresh")
    served_text, served_digest = store.get_results_payload("fresh")

    assert PERSISTED_MARKER in stored_text
    assert (served_text, served_digest) == (stored_text, stored_digest)
    flags = json.loads(served_text)["metadata"]["power_qualification"]
    assert flags["status"] == "unqualified"
    assert flags["evaluated"] == "solve"
    store.close()


def test_archived_record_is_flagged_at_read_time_without_rewriting_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "simulations.db"
    store = JobStore(path)
    store.initialize()
    store.create_job(_job("archived", "complete"))
    fixture = json.loads(SPEAKER2.read_text())
    archived = {**_envelope("multi_channel"), **{
        key: fixture[key] for key in ("channels", "channel_order", "frequencies")
    }}
    archived["metadata"] = {**archived["metadata"], **fixture["metadata"]}
    archived["provenance"] = {**archived["provenance"], **fixture["provenance"]}
    # Written the way a pre-qualification build stored it: no flags at all.
    text = json.dumps(archived, allow_nan=False)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO simulation_results (job_id, results_json, results_sha256) VALUES (?, ?, ?)",
            ("archived", text, digest),
        )

    served_text, served_digest = store.get_results_payload("archived")

    assert _raw_row(path, "archived") == (text, digest)
    assert served_digest == hashlib.sha256(served_text.encode("utf-8")).hexdigest()
    served = json.loads(served_text)
    RESULT_ENVELOPE_ADAPTER.validate_python(served)
    lf = served["channels"]["drive-lf"]["metadata"]["power_qualification"]
    assert lf["status"] == "unqualified"
    assert lf["evaluated"] == "read_time"
    assert served["channels"]["drive-hf"]["metadata"]["power_qualification"]["status"] == "qualified"
    assert served["channels"]["combined"]["metadata"]["power_qualification"][
        "unqualified_channels"
    ] == ["drive-lf"]
    # Nothing about a level moved: every stored field is served unchanged.
    for channel_id, channel in archived["channels"].items():
        assert served["channels"][channel_id]["spl_on_axis"] == channel["spl_on_axis"]
    # A second read is identical and still leaves the row alone.
    assert store.get_results_payload("archived") == (served_text, served_digest)
    assert _raw_row(path, "archived") == (text, digest)
    store.close()


def test_old_record_without_power_or_provenance_reads_as_unknown(
    tmp_path: Path,
) -> None:
    path = tmp_path / "simulations.db"
    store = JobStore(path)
    store.initialize()
    store.create_job(_job("old", "complete"))
    text = json.dumps(_envelope())
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO simulation_results (job_id, results_json, results_sha256) VALUES (?, ?, ?)",
            ("old", text, hashlib.sha256(text.encode()).hexdigest()),
        )

    served = json.loads(store.get_results_payload("old")[0])

    RESULT_ENVELOPE_ADAPTER.validate_python(served)
    flags = served["metadata"]["power_qualification"]
    assert flags["status"] == "unknown"
    assert flags["unknown_reason"] == "power_check_unavailable"
    assert flags["provenance"]["recorded"] is False
    store.close()


def test_unreadable_or_already_flagged_text_is_left_alone() -> None:
    assert annotate_stored_text("not json") is None
    assert annotate_stored_text("[1, 2]") is None
    flagged = json.dumps(annotate_results(_envelope()))
    assert annotate_stored_text(flagged) is None


def test_a_payload_without_a_frequency_axis_carries_no_flags() -> None:
    bare = {"metadata": {"field_plane_available": False}}

    assert annotate_results(bare) == bare
    assert annotate_stored_text(json.dumps(bare)) is None


def test_recombining_an_archived_record_keeps_it_marked_read_time() -> None:
    fixture = json.loads(SPEAKER2.read_text())
    opened = json.loads(annotate_stored_text(json.dumps(fixture)))

    # The recombine path re-annotates what the read path served, then stores it.
    persisted = annotate_results(opened)

    lf = persisted["channels"]["drive-lf"]["metadata"]["power_qualification"]
    assert lf["evaluated"] == "read_time"
    assert lf["status"] == "unqualified"
    # Stored flags are served verbatim from now on.
    assert persisted["metadata"]["power_qualification_version"] == 1
    assert annotate_stored_text(json.dumps(persisted)) is None


def test_reopening_an_archived_record_reuses_its_read_time_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import server.jobs.store as store_module

    path = tmp_path / "simulations.db"
    store = JobStore(path)
    store.initialize()
    store.create_job(_job("reopened", "complete"))
    envelope = _envelope()
    # Unique content, so no earlier test has warmed the cache for it.
    envelope["metadata"].update(_channel([-2.5], frequencies=[123.456])["metadata"])
    envelope["frequencies"] = [123.456]
    text = json.dumps(envelope)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO simulation_results (job_id, results_json, results_sha256) VALUES (?, ?, ?)",
            ("reopened", text, hashlib.sha256(text.encode()).hexdigest()),
        )
    calls: list[str] = []
    real = store_module.annotate_stored_text

    def counting(value: str) -> str | None:
        calls.append(value)
        return real(value)

    monkeypatch.setattr(store_module, "annotate_stored_text", counting)

    first = store.get_results_payload("reopened")
    second = store.get_results_payload("reopened")

    assert first == second
    assert len(calls) == 1
    assert json.loads(first[0])["metadata"]["power_qualification"]["status"] == "unqualified"
    store.close()
