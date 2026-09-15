"""The ingest-level qualification must judge what it records.

``scripts/qualify_ingest_level.py`` puts real CAD returns through WG's own
ingest, the engines and the job runtime. That takes minutes and a provisioned
BEAT CPU runtime, so none of its verdicts had been seen to fail: the horn's
tolerances came from each engine's own refinement ladder, so an engine whose
ladder did not converge widened the bound it was judged by, and the
fresh-install job passed on its status, its engine name and a byte-equal
reopen -- a job that stored nothing but zeros passed all three.

Here every ingest, solve, plan and job is a stub with a known answer, so each
verdict meets an input that violates it. Nothing is meshed or solved.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from scripts import imported_ingest_fixtures as fixtures
from scripts import qualify_imported_same_mesh as qual
from scripts import qualify_ingest_level as ingest

ENGINES = ["metal", "beat-cpu"]
PLANES = ["horizontal", "vertical", "diagonal"]
ANGLES = np.linspace(-180.0, 180.0, 73)
FREQUENCIES = list(ingest.HORN_FREQUENCIES_HZ)
FRESH = "fresh app data dir: import, prepare, solve, store, reopen"
Y_ONLY = "y-only half: BEAT-CPU refused at submission with the reason"
REAR_CAP = "source on the plug's rear: WG's quarter vs its full domain"
PLACED = "horn: placed in CAD (rotated + translated) vs unplaced"


def _field(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    shape = (len(FREQUENCIES), len(PLANES), len(ANGLES))
    return rng.normal(size=shape) + 1j * rng.normal(size=shape)


FIELDS = {"round": _field(1), "rearcap": _field(2)}

#: Each engine's answer on the round return, as a factor on one field, per
#: density. Metal's moves 4 % and then 2 % towards its finest level, about as
#: the recorded ladder did (4.24e-2 and 2.00e-2); BEAT-CPU's is tighter, and
#: the two differ by about 1.6 % at the reference density (recorded: 1.82e-2).
NOMINAL: dict[str, dict[str, complex]] = {
    "metal": {"coarse": 1.04, "reference": 1.02, "fine": 1.0, "full": 1.01},
    "beat-cpu": {"coarse": 1.012, "reference": 1.004, "fine": 1.0005, "full": 1.0045},
}
#: The recorded run's mesh sizes, for the facts the harness keeps.
TRIANGLES = {"coarse": 248, "reference": 470, "fine": 628, "full": 1856}


def _solved(engine: str, field: np.ndarray) -> qual.Solved:
    return qual.Solved(
        engine=engine,
        channel_ids=["drive-hf"],
        frequencies_hz=np.asarray(FREQUENCIES, dtype=float),
        angles_deg=ANGLES,
        planes=list(PLANES),
        pressure={"drive-hf": field},
        sphere={"drive-hf": None},
        sphere_theta_deg=None,
        sphere_phi_deg=None,
        wall_seconds=0.0,
        metadata={"solver_engine": {"engine": engine}},
    )


def _job_results(
    reported: str, *, zero: bool = False, channel: str = "drive-hf", count: int | None = None
) -> dict[str, Any]:
    """A stored imported result, shaped as the job runtime stores one.

    *channel* and *count* let it answer another channel, or fewer of the
    frequencies asked, with every axis still aligned and every number sound.
    """

    frequencies = list(FREQUENCIES)[:count]
    level, off_axis = (0.0, 0.0) if zero else (91.5, -6.0)
    payload = {
        "frequencies": frequencies,
        "spl_on_axis": {
            "frequencies": frequencies,
            "spl": [level] * len(frequencies),
            "phase_degrees": [0.0] * len(frequencies),
        },
        "directivity": {
            plane: [[[-90.0, off_axis], [0.0, 0.0], [90.0, off_axis]] for _ in frequencies]
            for plane in PLANES
        },
    }
    return {
        "result_kind": "multi_channel",
        "result_contract_version": 2,
        "channels": {channel: payload},
        "channel_order": [channel],
        "frequencies": frequencies,
        "metadata": {"geometry_type": "imported", "solver_engine": {"engine": reported}},
    }


class Stubs:
    """Every ingest, solve, plan and job ``run_ingest_level`` makes, answered here."""

    def __init__(self) -> None:
        self.factors = copy.deepcopy(NOMINAL)
        #: (return, density) -> an extra factor on every engine's answer there.
        self.shifts: dict[tuple[str, str], float] = {}
        self.plan_beat: dict[str, Any] | None = {
            "name": "beat-cpu",
            "solves": False,
            "stage": "capability",
            "code": "imported_symmetry_unsupported_by_engine",
            "reason": "it cannot mirror this return's y-only half (mirrored on y = 0)",
        }
        self.job_status: dict[str, str] = {}
        self.zero_results: set[str] = set()
        self.reported: dict[str, str] = {}
        self.result_channel: dict[str, str] = {}
        self.result_count: dict[str, int] = {}
        self.reopen_changes: set[str] = set()
        self.stored: dict[str, dict[str, Any]] = {}

    def linked_return(self, root: Path, name: str, **_options: Any) -> Path:
        bundle = root / f"{name}.wgreturn"
        bundle.mkdir(parents=True)
        (bundle / "wgreturn.json").write_text("{}", encoding="utf-8")
        return bundle

    def ingest(
        self,
        bundle: Path,
        data_dir: Path,
        *,
        sizes: Any = None,
        symmetry_mode: str = "auto",
        surface_deviation_mm: float | None = None,
    ) -> fixtures.Ingested:
        name = bundle.name.removesuffix(".wgreturn")
        if name not in ("round", "rearcap", "placed", "skewed", "edited"):
            name = "fresh"
        if symmetry_mode == "full":
            label = "full"
        elif surface_deviation_mm is not None:
            label = {deviation: level for level, deviation in ingest.HORN_LADDER}[surface_deviation_mm]
        else:
            label = "reference"
        domain = [] if label == "full" else ["x0", "y0"]
        record = {
            "_stub": (name, label),
            "ingest_id": "wgi_" + "0" * 26,
            "manifest_sha256": "sha256:" + "1" * 64,
            "artifact_sha256": "sha256:" + "2" * 64,
            "report_sha256": "sha256:" + "3" * 64,
            "findings": [],
            "polar_grid_derivation": {"angle_range": [-180.0, 180.0, 73]},
            "anchor": {"instance_id": "anchor", "design_id": None, "throat_frame": dict(qual.IDENTITY_FRAME)},
            "symmetry": {"domain_planes": domain, "cut_planes": domain},
            "mesh": {
                "stats": {"triangle_count": TRIANGLES[label], "warnings": []},
                "metadata": {"postprocess": {"flipped_global": 0}},
                "integrity": {"orientation_valid": True},
            },
        }
        return fixtures.Ingested(record=record, store=None, data_dir=data_dir, sizes=fixtures.mesh_sizes())

    def solve_record(self, engine: str, ingested: fixtures.Ingested, motion: str) -> qual.Solved:
        name, label = ingested.record["_stub"]
        if name == "rearcap":
            field, factor = FIELDS["rearcap"], 1.0
        else:
            field = FIELDS["round"]
            factor = self.factors[engine][label if name == "round" else "reference"]
        factor = factor * self.shifts.get((name, label), 1.0)
        if motion == "axial":
            factor = factor * 0.5j
        return _solved(engine, factor * field)

    async def plan(self, _data_dir: Path, _store: Any, _request: Any) -> dict[str, Any]:
        metal = {"name": "metal", "solves": True, "stage": None, "code": None, "reason": None}
        return {"engine": "metal", "engines": [metal] + ([self.plan_beat] if self.plan_beat else [])}

    async def run_job(self, _data_dir: Path, _store: Any, request: Any) -> dict[str, Any]:
        engine = request.options.engine
        job = f"job-{engine}"
        self.stored[job] = _job_results(
            self.reported.get(engine, engine),
            zero=engine in self.zero_results,
            channel=self.result_channel.get(engine, "drive-hf"),
            count=self.result_count.get(engine),
        )
        return {
            "job_id": job,
            "row": {"status": self.job_status.get(engine, "complete"), "error_message": None},
            "results": copy.deepcopy(self.stored[job]),
        }

    def reopen(self, _data_dir: Path, job_id: str) -> dict[str, Any]:
        results = copy.deepcopy(self.stored[job_id])
        if job_id in self.reopen_changes:
            results["metadata"]["reopened"] = True
        return results


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> Stubs:
    answers = Stubs()
    monkeypatch.setattr(fixtures, "linked_return", answers.linked_return)
    monkeypatch.setattr(fixtures, "ingest", answers.ingest)
    monkeypatch.setattr(ingest, "_solve_record", answers.solve_record)
    monkeypatch.setattr(ingest, "_plan", answers.plan)
    monkeypatch.setattr(ingest, "_run_job", answers.run_job)
    monkeypatch.setattr(ingest, "_reopen", answers.reopen)
    return answers


def _run(tmp_path: Path) -> tuple[list[qual.Row], dict[str, Any]]:
    rows: list[qual.Row] = []

    def record_row(row: qual.Row) -> qual.Row:
        rows.append(row)
        return row

    facts = ingest.run_ingest_level(ENGINES, tmp_path, record_row)
    return rows, facts


def _row(rows: list[qual.Row], fixture: str, engine: str | None = None) -> qual.Row:
    (found,) = [row for row in rows if row.fixture == fixture and engine in (None, row.engine)]
    return found


def test_engines_that_agree_on_a_converged_horn_pass_every_judged_row(
    stubs: Stubs, tmp_path: Path
) -> None:
    rows, facts = _run(tmp_path)

    judged = [row for row in rows if row.passed is not None]
    failed = [(row.fixture, row.engine, row.worst, row.tolerance) for row in judged if not row.passed]
    assert judged and not failed, failed
    # The default density is judged against the finest, not only recorded.
    for engine in ENGINES:
        assert _row(rows, "horn return, reference vs fine density", engine).passed is True
    # And every horn bound is the stated one, not one the engines' answers set.
    for fixture in ("same mesh: horn quarter return, normal", "same mesh: horn quarter return, axial"):
        assert _row(rows, fixture).tolerance == ingest.HORN_TOLERANCE
    for engine in ENGINES:
        for fixture in ("horn: quarter return vs forced full domain", REAR_CAP, PLACED):
            assert _row(rows, fixture, engine).tolerance == ingest.HORN_TOLERANCE, fixture
    assert facts["horn_same_mesh_tolerance"] == ingest.HORN_TOLERANCE
    assert _row(rows, FRESH, "beat-cpu").passed is True
    assert _row(rows, Y_ONLY).passed is True


@pytest.mark.parametrize(
    "beat",
    (
        # Its answer moves 50 % between the default and the finest density, so
        # a tolerance taken from its own ladder grew past its 32 % disagreement
        # with Metal.
        pytest.param({"coarse": 1.8, "reference": 1.5, "fine": 1.0, "full": 1.5}, id="ladder-does-not-converge"),
        pytest.param({key: 1.3 * value for key, value in NOMINAL["beat-cpu"].items()}, id="thirty-percent-high"),
        pytest.param({key: -value for key, value in NOMINAL["beat-cpu"].items()}, id="minus-metal"),
    ),
)
def test_an_engine_that_disagrees_with_metal_on_the_horn_fails(
    stubs: Stubs, tmp_path: Path, beat: dict[str, complex]
) -> None:
    stubs.factors["beat-cpu"] = beat

    rows, _facts = _run(tmp_path)

    assert _row(rows, "same mesh: horn quarter return, normal").passed is False
    assert _row(rows, "same mesh: horn quarter return, axial").passed is False


def test_a_horn_ladder_that_does_not_converge_fails_on_its_own(stubs: Stubs, tmp_path: Path) -> None:
    stubs.factors["beat-cpu"] = {"coarse": 1.8, "reference": 1.5, "fine": 1.0, "full": 1.5}

    rows, _facts = _run(tmp_path)

    assert _row(rows, "horn return, reference vs fine density", "beat-cpu").passed is False
    assert _row(rows, "horn return, reference vs fine density", "metal").passed is True


@pytest.mark.parametrize(
    ("shifted", "fixture"),
    (
        pytest.param(("rearcap", "reference"), REAR_CAP, id="rear-cap-quarter-differs-from-its-full-domain"),
        pytest.param(("placed", "reference"), PLACED, id="placed-return-differs-from-the-unplaced-one"),
        pytest.param(("round", "full"), "horn: quarter return vs forced full domain", id="quarter-differs-from-the-full-domain"),
    ),
)
def test_two_meshes_of_one_horn_that_disagree_fail(
    stubs: Stubs, tmp_path: Path, shifted: tuple[str, str], fixture: str
) -> None:
    """Every engine's answer on one of the two meshes is 20 % off the other."""

    stubs.shifts[shifted] = 1.2

    rows, _facts = _run(tmp_path)

    for engine in ENGINES:
        assert _row(rows, fixture, engine).passed is False, engine


@pytest.mark.parametrize(
    "change",
    (
        "all-zero-results",
        "another-engine-ran",
        "job-ended-in-error",
        "reopened-results-differ",
        "result-on-another-channel",
        "result-at-fewer-frequencies",
    ),
)
def test_a_fresh_install_job_is_judged_on_its_data_not_only_its_status(
    stubs: Stubs, tmp_path: Path, change: str
) -> None:
    """One engine's job goes wrong; its row fails and the other's still passes."""

    if change == "all-zero-results":
        stubs.zero_results = {"beat-cpu"}
    elif change == "another-engine-ran":
        stubs.reported = {"beat-cpu": "metal"}
    elif change == "job-ended-in-error":
        stubs.job_status = {"beat-cpu": "error"}
    elif change == "reopened-results-differ":
        stubs.reopen_changes = {"job-beat-cpu"}
    elif change == "result-on-another-channel":
        stubs.result_channel = {"beat-cpu": "drive-other"}
    else:
        stubs.result_count = {"beat-cpu": 2}

    rows, _facts = _run(tmp_path)

    assert _row(rows, FRESH, "beat-cpu").passed is False
    assert _row(rows, FRESH, "metal").passed is True


@pytest.mark.parametrize(
    "beat",
    (
        pytest.param(
            {"name": "beat-cpu", "solves": True, "stage": None, "code": None, "reason": None},
            id="beat-offered-for-the-y-only-half",
        ),
        pytest.param(
            {"name": "beat-cpu", "solves": False, "stage": "availability",
             "code": "imported_engine_unavailable", "reason": "not provisioned"},
            id="refused-for-another-reason",
        ),
        pytest.param(None, id="beat-absent-from-the-plan"),
    ),
)
def test_the_y_only_half_passes_only_when_beat_is_refused_for_its_symmetry(
    stubs: Stubs, tmp_path: Path, beat: dict[str, Any] | None
) -> None:
    stubs.plan_beat = beat

    rows, _facts = _run(tmp_path)

    assert _row(rows, Y_ONLY).passed is False


class _Adapter:
    """An engine adapter whose answer names *reported* as the engine that ran."""

    def __init__(self, reported: str) -> None:
        self.reported = reported

    async def run(self, request: Any, **_callbacks: Any) -> Any:
        from server.solver.base import EngineRunResult
        from server.solver.combine import serialize_channel_bases

        frequencies = np.asarray(request.options.frequencies_hz, dtype=float)
        bases = {
            channel.id: SimpleNamespace(
                frequencies_hz=frequencies,
                observation_angles_deg=ANGLES,
                observation_planes=PLANES,
                pressure_complex=FIELDS["round"][: len(frequencies)],
                sphere_pressure_complex=None,
            )
            for channel in request.geometry.drive_channels
        }
        return EngineRunResult(
            results={"metadata": {"solver_engine": {"engine": self.reported}}, "channels": {}},
            channel_bases=serialize_channel_bases(bases),
        )


def test_a_horn_solve_answered_by_another_engine_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from server.engines import registry

    ingested = Stubs().ingest(tmp_path / "round.wgreturn", tmp_path / "data")

    monkeypatch.setattr(registry, "create_engine", lambda _name: _Adapter("beat-cpu"))
    assert ingest._solve_record("beat-cpu", ingested, "normal").engine == "beat-cpu"

    monkeypatch.setattr(registry, "create_engine", lambda _name: _Adapter("metal"))
    with pytest.raises(qual.EngineAnswerMismatch, match="'metal'"):
        ingest._solve_record("beat-cpu", ingested, "normal")
