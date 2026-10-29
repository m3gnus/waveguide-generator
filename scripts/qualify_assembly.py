"""Qualification of the design assembly: the horn and its front-baffle woofers.

Every case is a real design compiled by the design-assembly producer
(``server/cadlink/intake/design_assembly.py``) with the real design mesher,
then turned into the request each engine would receive through the same
composition the model Solve uses (``compose_solve_request``):

- ``horn-only``: the horn's classic source alone. Its compiled mesh must be
  the ordinary design mesh -- identical points, connectivity and throat
  source mask after the file normalisation every prepared mesh gets.
- ``horn-woofer``: one flat piston below the mouth, in its own LF group.
- ``two-woofers``: a piston below and one spaced from it, in one LF group with
  different member delay and polarity.
- ``controls``: a recessed piston and an ATH cone, each with an exterior
  driver: the BEAT v3 request carries the horn as a prescribed ideal source
  beside the woofers' voltage ports, in one coupled request.

The geometry phase runs anywhere the design mesher does. The solve phase is
opt-in (``--solve``): it runs each case on every engine available here,
through the job runtime, and checks what comes back. Run it on a host with
Metal and a provisioned BEAT runtime:

    python scripts/qualify_assembly.py --json assembly.json
    python scripts/qualify_assembly.py --solve --require beat-cpu --require metal --json assembly.json
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.contracts.assembly import ASSEMBLY_RECORD_FEATURE, HF_RADIATOR_ID  # noqa: E402

DESIGN_UID = "wgu_01JQ4A5SEMB7Y0000000000000"
W1 = "00000000-0000-4000-8000-00000000a001"
W2 = "00000000-0000-4000-8000-00000000a002"
G_HF = "00000000-0000-4000-8000-00000000b001"
G_LF = "00000000-0000-4000-8000-00000000b002"

HORN: dict[str, Any] = {
    "formula": "R-OSSE", "R": 100, "r0": 12.7, "a0": 15.5, "a": 25, "k": 2, "m": 0.85,
    "b": 0.2, "r": 0.4, "q": 3.4, "tmax": 1,
    "mesh": {"quadrants": 1234, "throat_resolution": 8, "mouth_resolution": 20,
             "rear_resolution": 40, "angular_segments": 32, "length_segments": 12},
    "enclosure": {"depth": 200, "space_l": 30, "space_t": 30, "space_r": 30, "space_b": 450,
                  "front_resolution": 30, "back_resolution": 50, "edge_radius": 10},
}

#: A bare driver with plausible small-woofer parameters; its axis is the assembly's.
DRIVER: dict[str, Any] = {
    "version": 1, "re_ohm": 6.0, "le_h": 0.0005, "bl_n_per_a": 7.0, "mmd_kg": 0.02,
    "cms_m_per_n": 0.001, "rms_n_s_per_m": 1.5,
}
MOTION_AXIS = [0.0, 0.0, 1.0]

#: The engines this harness can run, and the ones that solve exterior drivers.
QUALIFIED_ENGINES = ("beat-cpu", "beat-metal", "metal", "bempp")
COUPLED_ENGINES = frozenset({"beat-cpu", "beat-metal"})
FREQUENCIES_HZ = (200.0, 500.0, 1000.0, 2000.0)


def _woofer(identity: str, placement: Mapping[str, Any], *, shape: Mapping[str, Any] | None = None,
            radius_mm: float = 80.0) -> dict[str, Any]:
    return {"id": identity, "label": f"Woofer {identity[-1]}", "radius_mm": radius_mm,
            "shape": dict(shape or {"kind": "piston"}), "placement": dict(placement), "resolution_mm": 25}


def _assembly(woofers: Sequence[Mapping[str, Any]], members: Sequence[Mapping[str, Any]] = (),
              drivers: Mapping[str, Any] | None = None) -> dict[str, Any]:
    groups = [{"id": G_HF, "label": "HF", "members": [{"radiator_id": HF_RADIATOR_ID}]}]
    if woofers:
        groups.append({"id": G_LF, "label": "LF", "members": list(members) or [
            {"radiator_id": woofer["id"]} for woofer in woofers]})
    assembly: dict[str, Any] = {"woofers": [dict(woofer) for woofer in woofers], "signal_groups": groups}
    if drivers:
        assembly["drivers"] = {key: {"exterior_transducer": dict(value)} for key, value in drivers.items()}
    return assembly


def cases() -> dict[str, dict[str, Any]]:
    """The qualification designs, by case name."""

    below = {"anchor": "below", "gap_mm": 10}
    spaced = {"anchor": "free", "x_mm": 0, "y_mm": -400, "spacing_to": {"radiator_id": W1, "distance_mm": 175}}
    assemblies = {
        "horn-only": _assembly([]),
        "horn-woofer": _assembly([_woofer(W1, below)]),
        "two-woofers": _assembly(
            [_woofer(W1, below), _woofer(W2, spaced)],
            members=[{"radiator_id": W1}, {"radiator_id": W2, "delay_ms": 0.25, "invert": True, "weight": 0.8}],
        ),
        "controls": _assembly(
            [_woofer(W1, below, shape={"kind": "piston", "recess_mm": 10}), _woofer(W2, spaced, shape={"kind": "ath_cone"})],
            drivers={W1: DRIVER, W2: DRIVER},
        ),
    }
    return {name: {**copy.deepcopy(HORN), "assembly": assembly} for name, assembly in assemblies.items()}


# ---------------------------------------------------------------- compile


def compile_case(store: Any, data_dir: Path, dump: Mapping[str, Any], *, revision: int) -> dict[str, Any]:
    """Compile one design with the design-assembly producer; returns its sealed record."""

    from server.cadlink.intake.design_assembly import AssemblyBuildFence, compile_design_assembly

    outcome = asyncio.run(compile_design_assembly(
        store, data_dir, dict(dump), design_uid=DESIGN_UID, design_name="Assembly qualification",
        design_revision=revision, fence=AssemblyBuildFence(),
    ))
    if outcome.get("status") != "published":
        raise RuntimeError(f"the compile was not published: {outcome.get('status')}")
    return outcome["record"]


def compiled_mesh_text(record: Mapping[str, Any]) -> str:
    return Path(record["mesh_store_path"]).read_text(encoding="utf-8")


def horn_only_identity(record: Mapping[str, Any], dump: Mapping[str, Any]) -> dict[str, bool]:
    """Whether a horn-only compile is the ordinary design mesh, after file normalisation.

    The ordinary build goes through the same normalisation every prepared
    mesh gets; the throat is tag 2 in both, and the compiled record names it.
    """

    from server.design.schema import DesignConfig
    from server.mesh.builder import _build_sync
    from server.mesh.file_import import parse_file_mesh, prepare_file_mesh

    horn = {key: value for key, value in dump.items() if key != "assembly"}
    ordinary = _build_sync(DesignConfig.model_validate(horn).model_dump(mode="json"))
    expected, _ = prepare_file_mesh(parse_file_mesh(ordinary["msh_text"]), "m", "+z", "+y")
    return compare_meshes(parse_file_mesh(compiled_mesh_text(record)), expected,
                          compiled_throat_tag=int(record["source_tags"][HF_RADIATOR_ID]))


def compare_meshes(compiled: Any, ordinary: Any, *, compiled_throat_tag: int, ordinary_throat_tag: int = 2) -> dict[str, bool]:
    """Identical points, connectivity and throat source masks, exactly."""

    same_shape = compiled.points.shape == ordinary.points.shape and compiled.triangles.shape == ordinary.triangles.shape
    return {
        "points": bool(same_shape and np.array_equal(compiled.points, ordinary.points)),
        "connectivity": bool(same_shape and np.array_equal(compiled.triangles, ordinary.triangles)),
        "source_mask": bool(same_shape and np.array_equal(
            np.asarray(compiled.tags) == compiled_throat_tag, np.asarray(ordinary.tags) == ordinary_throat_tag)),
    }


def inventory(record: Mapping[str, Any]) -> dict[str, Any]:
    """What a compiled record offers a solve, without its paths or hashes."""

    return {
        "triangles": int(record["mesh"]["stats"]["triangle_count"]),
        "sources": {source["id"]: {"role": source["role"], "area_mm2": source["observed"]["total_area_mm2"]}
                    for source in record["sources"]},
        "radiators": {radiator["radiator_id"]: radiator["state"] for radiator in record["radiators"]},
        "excitations": [excitation["id"] for excitation in record["excitations"]],
        "signal_groups": {group["label"]: list(group["members"]) for group in record["signal_groups"]},
        "symmetry": record["symmetry"].get("solved_domain"),
    }


# ---------------------------------------------------------------- setups


def solve_setup(record: Mapping[str, Any], assembly: Mapping[str, Any], engine: str) -> tuple[dict[str, Any], str | None]:
    """The setup the front end sends for one engine, with its explanation if drivers fall back.

    Drivers bind to radiators by identity. Engines without coupled exterior
    drivers solve every radiator at its prescribed velocity, and say so.
    """

    drivers = assembly.get("drivers") or {}
    coupled = engine in COUPLED_ENGINES
    channels = []
    for excitation in record["excitations"]:
        channel: dict[str, Any] = {"id": excitation["id"], "source_ids": list(excitation["source_ids"]),
                                   "motion": excitation["motion"]}
        if coupled and excitation["id"] in drivers:
            channel["exterior_transducer"] = {**drivers[excitation["id"]]["exterior_transducer"], "motion_axis": MOTION_AXIS}
        channels.append(channel)
    compiled = {channel["id"] for channel in channels}
    groups = []
    for group in assembly["signal_groups"]:
        members = [member for member in group["members"] if member["radiator_id"] in compiled]
        if not members:
            continue
        processing = {member["radiator_id"]: {key: member[key] for key in ("weight", "delay_ms", "invert") if key in member}
                      for member in members}
        groups.append({"id": group["id"], "label": group["label"], "channel_ids": [m["radiator_id"] for m in members],
                       "member_processing": processing})
    geometry: dict[str, Any] = {"required_features": [ASSEMBLY_RECORD_FEATURE], "drive_channels": channels,
                                "driver_groups": groups}
    if any("exterior_transducer" in channel for channel in channels):
        geometry["drive_voltage_v"] = 2.83
    if len(groups) == 2:
        hf, lf = groups[0]["id"], groups[1]["id"]
        geometry["combine"] = {"members": [lf, hf], "reference": hf, "channels": {
            lf: {"lp": {"family": "lr", "order": 4, "fc_hz": 800}},
            hf: {"hp": {"family": "lr", "order": 4, "fc_hz": 800}}}}
    notice = None
    if drivers and not coupled:
        notice = f"{engine} solves every radiator at its prescribed velocity; the drivers need BEAT."
    setup = {"schema_version": 1, "geometry": geometry, "options": {
        "engine": engine, "frequencies_hz": list(FREQUENCIES_HZ),
        "polar_config": {"enabled_axes": ["horizontal", "vertical"], "spherical_theta_count": 5, "spherical_phi_count": 8}}}
    return setup, notice


def compose(record: Mapping[str, Any], setup: Mapping[str, Any], *, client_request_id: str) -> Any:
    """The request the model Solve makes of this record and setup."""

    from server.cadlink.joblane.project_setup import compose_solve_request
    from server.jobs.imported_setup import validate_setup

    return compose_solve_request(validate_setup(dict(setup)), record, client_request_id=client_request_id)


def coupled_plan(record: Mapping[str, Any], request: Any) -> dict[str, Any]:
    """The one BEAT v3 request a coupled solve sends: prescribed sources beside driver ports."""

    from server.solver import beat_transducer_imported as transducers

    msh_text = compiled_mesh_text(record)
    channels = list(request.geometry.drive_channels)
    sources, ports, indices = transducers.excitation_plan(record, msh_text, channels)
    drivers = transducers.drivers_for(record, channels)
    return {
        "ideal_source_count": len(sources),
        "ports": list(ports),
        "driver_ports": [driver.port_id for driver in drivers],
        "channel_ports": {key: list(value) for key, value in indices.items()},
        "preflight": transducers.preflight(record, msh_text, channels, backend="cpu"),
    }


def geometry_phase(data_dir: Path, engines: Sequence[str] = QUALIFIED_ENGINES) -> dict[str, Any]:
    """Compile every case and compose every engine's request; nothing is solved."""

    from server.cadlink.store import CadLinkStore

    store = CadLinkStore.for_data_dir(data_dir)
    store.initialize()
    report: dict[str, Any] = {"cases": {}}
    try:
        for revision, (name, dump) in enumerate(cases().items(), start=1):
            record = compile_case(store, data_dir, dump, revision=revision)
            entry: dict[str, Any] = {"record": record, "dump": dump, "inventory": inventory(record), "requests": {}}
            if name == "horn-only":
                entry["horn_only_identity"] = horn_only_identity(record, dump)
            for engine in engines:
                setup, notice = solve_setup(record, dump["assembly"], engine)
                request = compose(record, setup, client_request_id=f"qualify-{name}-{engine}")
                summary: dict[str, Any] = {"setup": setup, "notice": notice, "request": request,
                                           "transducer_channels": [channel.id for channel in request.geometry.drive_channels
                                                                   if channel.exterior_transducer is not None]}
                if summary["transducer_channels"]:
                    summary["coupled_plan"] = coupled_plan(record, request)
                entry["requests"][engine] = summary
            report["cases"][name] = entry
    finally:
        store.close()
    return report


def geometry_verdicts(report: Mapping[str, Any]) -> list[str]:
    """Every geometry-phase failure, as a sentence; empty when the phase passes."""

    failures = []
    for name, entry in report["cases"].items():
        identity = entry.get("horn_only_identity")
        if identity is not None:
            failures += [f"{name}: compiled horn-only {key} differ from the ordinary design mesh"
                         for key, same in identity.items() if not same]
        assembly = entry["dump"]["assembly"]
        wanted = [HF_RADIATOR_ID, *(woofer["id"] for woofer in assembly["woofers"] if woofer.get("enabled", True))]
        if entry["inventory"]["excitations"] != wanted:
            failures.append(f"{name}: excitations {entry['inventory']['excitations']} are not the radiators {wanted}")
        for engine, summary in entry["requests"].items():
            drivers = set((assembly.get("drivers") or {}))
            if engine in COUPLED_ENGINES and set(summary["transducer_channels"]) != drivers:
                failures.append(f"{name}/{engine}: drivers {sorted(drivers)} are not all coupled")
            if engine not in COUPLED_ENGINES and summary["transducer_channels"]:
                failures.append(f"{name}/{engine}: a prescribed-velocity engine was sent exterior drivers")
            if drivers and engine not in COUPLED_ENGINES and not summary["notice"]:
                failures.append(f"{name}/{engine}: the prescribed-velocity fallback is not explained")
            wanted_processing = {member["radiator_id"]: {"weight": float(member.get("weight", 1.0)),
                                                         "delay_ms": float(member.get("delay_ms", 0.0)),
                                                         "invert": bool(member.get("invert", False))}
                                 for group in assembly["signal_groups"] for member in group["members"]}
            sent = {channel: item.model_dump() for group in summary["request"].geometry.driver_groups or []
                    for channel, item in (group.member_processing or {}).items()}
            for channel, item in sent.items():
                if item != wanted_processing.get(channel):
                    failures.append(f"{name}/{engine}: member processing of {channel} is {item}, not {wanted_processing.get(channel)}")
            if set(sent) != set(entry["inventory"]["excitations"]):
                failures.append(f"{name}/{engine}: member processing names {sorted(sent)}, not every compiled radiator")
            plan = summary.get("coupled_plan")
            if plan is not None:
                if plan["preflight"] is not None:
                    failures.append(f"{name}/{engine}: the coupled preflight refused: {plan['preflight']}")
                if plan["ideal_source_count"] < 1:
                    failures.append(f"{name}/{engine}: the horn is not a prescribed ideal source of the coupled request")
                if not set(plan["driver_ports"]) <= set(plan["ports"]):
                    failures.append(f"{name}/{engine}: a driver port is missing from the excitation order")
                if len(set(plan["ports"])) != len(plan["ports"]):
                    failures.append(f"{name}/{engine}: the excitation order repeats a port")
    return failures


# ---------------------------------------------------------------- solve


def available_engines() -> dict[str, str]:
    from server.engines.registry import detect_engines

    status = {}
    for info in detect_engines():
        if info.name in QUALIFIED_ENGINES:
            status[info.name] = "available" if info.available else f"unavailable: {info.reason}"
    return status


def result_findings(results: Mapping[str, Any], *, channel_ids: Sequence[str], driver_ids: Sequence[str],
                    combined_id: str | None, frequencies_hz: Sequence[float] = FREQUENCIES_HZ,
                    polar_axes: Sequence[str] = ("horizontal", "vertical"), angle_count: int | None = None) -> list[str]:
    """What is wrong with one finished job's results, as sentences."""

    findings: list[str] = []
    expected = np.asarray(frequencies_hz, dtype=float)

    def finite(value: Any, shape: tuple[int, ...]) -> bool:
        try:
            array = np.asarray(value, dtype=object)
            return (array.shape == shape and array.size > 0
                    and all(type(item) in {float, int} and math.isfinite(item) for item in array.flat))
        except (TypeError, ValueError):
            return False

    def axis(block: Mapping[str, Any]) -> bool:
        values = block.get("frequencies")
        return finite(values, expected.shape) and np.allclose(values, expected, rtol=0, atol=1e-8)

    channels = results.get("channels") if isinstance(results.get("channels"), Mapping) else {}
    for channel_id in [*channel_ids, *([combined_id] if combined_id else [])]:
        if channel_id not in channels:
            findings.append(f"channel {channel_id} is missing from the results")
            continue
        channel = channels[channel_id] if isinstance(channels[channel_id], Mapping) else {}
        spl = channel.get("spl_on_axis")
        if (not isinstance(spl, Mapping) or not axis(channel) or not axis(spl)
                or not finite(spl.get("spl"), expected.shape) or not finite(spl.get("phase_degrees"), expected.shape)):
            findings.append(f"channel {channel_id} lacks finite SPL/phase at every requested frequency")
        patterns = channel.get("directivity")
        phases = channel.get("directivity_phase")
        for plane in polar_axes:
            for label, block in (("directivity", patterns), ("directivity phase", phases)):
                values = block.get(plane) if isinstance(block, Mapping) else None
                try:
                    points = angle_count if angle_count is not None else len(values[0])
                except (TypeError, IndexError):
                    points = 0
                if not finite(values, (len(expected), points, 2)):
                    findings.append(f"channel {channel_id} lacks finite {plane} {label} with the requested dimensions")
        metadata = channel.get("metadata")
        if isinstance(metadata, Mapping) and (metadata.get("failures") or metadata.get("failure_count")
                                              or metadata.get("partial_success")):
            findings.append(f"channel {channel_id} reports solve/postprocessing failures")
    metadata = results.get("metadata")
    if isinstance(metadata, Mapping) and (metadata.get("failures") or metadata.get("failure_count") or metadata.get("partial_success")):
        findings.append("the result envelope reports solve/postprocessing failures")
    for driver_id in driver_ids:
        channel = channels.get(driver_id) if isinstance(channels.get(driver_id), Mapping) else {}
        impedance = channel.get("impedance")
        metadata = channel.get("metadata") if isinstance(channel.get("metadata"), Mapping) else {}
        if (not isinstance(impedance, Mapping) or not axis(impedance)
                or not finite(impedance.get("real"), expected.shape)
                or not finite(impedance.get("imaginary"), expected.shape)
                or metadata.get("impedance_quantity") != "electrical_input_impedance"
                or metadata.get("impedance_units") != "ohms"):
            findings.append(f"driver channel {driver_id} lacks finite electrical impedance at every requested frequency")
    return findings


async def _solve(data_dir: Path, report: Mapping[str, Any], engines: Sequence[str], timeout_s: float) -> dict[str, Any]:
    from server.cadlink.store import CadLinkStore
    from server.engines.registry import EngineRegistry
    from server.jobs.runtime import JobRuntime
    from server.jobs.store import JobStore

    cad = CadLinkStore.for_data_dir(data_dir)
    cad.initialize()
    runtime = JobRuntime(JobStore.for_data_dir(data_dir), engine_registry=EngineRegistry(), cadlink_store=cad)
    runtime.store.initialize()
    await runtime.start()
    solved: dict[str, Any] = {}
    try:
        for name, entry in report["cases"].items():
            for engine in engines:
                summary = entry["requests"][engine]
                request = summary["request"]
                started = time.monotonic()
                outcome: dict[str, Any] = {"status": "failed", "notice": summary["notice"], "findings": []}
                try:
                    # The same read-only capability/admission owner as the app.
                    # Installed modules alone do not establish support for this case.
                    plan = await runtime.plan_imported(request)
                    if plan["engine"] != engine:
                        outcome.update(status="not-applicable", reason=plan["reason"], code=plan.get("code"))
                        solved[f"{name}/{engine}"] = outcome
                        continue
                    job_id = await runtime.submit(request)
                    job = await runtime.get_job(job_id)
                    while job["status"] not in {"complete", "error", "cancelled"}:
                        if time.monotonic() - started > timeout_s:
                            await runtime.stop(job_id)
                            outcome["findings"] = ["the solve exceeded its qualification timeout"]
                            break
                        await asyncio.sleep(0.5)
                        job = await runtime.get_job(job_id)
                    outcome["status"] = job["status"]
                    if job["status"] == "complete":
                        combine = request.geometry.combine
                        polar = request.options.polar_config
                        outcome["findings"] = result_findings(
                            await runtime.get_results(job_id),
                            channel_ids=[channel.id for channel in request.geometry.drive_channels],
                            driver_ids=summary["transducer_channels"],
                            combined_id=getattr(combine, "id", None) if combine is not None else None,
                            frequencies_hz=request.options.frequencies_hz, polar_axes=polar.enabled_axes,
                            angle_count=polar.angle_range[2])
                    elif not outcome["findings"]:
                        outcome["findings"] = [f"the job ended {job['status']}: {job.get('error') or ''}".strip()]
                except Exception as exc:
                    outcome["findings"] = [f"{type(exc).__name__}: {exc}"]
                outcome["wall_seconds"] = round(time.monotonic() - started, 2)
                solved[f"{name}/{engine}"] = outcome
    finally:
        await runtime.shutdown()
        cad.close()
    return solved


# ---------------------------------------------------------------- report


def public_report(report: Mapping[str, Any], solved: Mapping[str, Any] | None, failures: Sequence[str]) -> dict[str, Any]:
    """The JSON evidence: no local paths, no hashes of this host's files."""

    return {
        "verdict": "pass" if not failures else "fail",
        "failures": list(failures),
        "cases": {name: {
            "inventory": entry["inventory"],
            **({"horn_only_identity": entry["horn_only_identity"]} if "horn_only_identity" in entry else {}),
            "requests": {engine: {
                "notice": summary["notice"],
                "transducer_channels": summary["transducer_channels"],
                **({"coupled_plan": {key: value for key, value in summary["coupled_plan"].items()}}
                   if "coupled_plan" in summary else {}),
            } for engine, summary in entry["requests"].items()},
        } for name, entry in report["cases"].items()},
        **({"solves": dict(solved)} if solved is not None else {}),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--solve", action="store_true", help="also solve every case on the engines available here")
    parser.add_argument("--require", action="append", default=[], choices=QUALIFIED_ENGINES,
                        help="fail unless this engine is available (repeatable)")
    parser.add_argument("--timeout", type=float, default=1800.0, help="seconds allowed per solve")
    parser.add_argument("--json", type=Path, help="write the evidence here")
    args = parser.parse_args(argv)

    from server.platform.temp_session import TemporarySession

    session = TemporarySession.create()
    session.activate()
    try:
        return _run(args, session.path)
    finally:
        session.close(remove=True)


def _run(args: argparse.Namespace, session_path: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="wg-qualify-assembly-", dir=session_path) as folder:
        data_dir = Path(folder)
        report = geometry_phase(data_dir)
        failures = geometry_verdicts(report)
        solved = None
        if args.solve:
            status = available_engines()
            missing = [engine for engine in args.require if status.get(engine) != "available"]
            failures += [f"{engine} is required but {status.get(engine, 'not detected')}" for engine in missing]
            engines = [engine for engine in QUALIFIED_ENGINES if status.get(engine) == "available"]
            solved = asyncio.run(_solve(data_dir, report, engines, args.timeout)) if engines else {}
            for name in report["cases"]:
                for engine in QUALIFIED_ENGINES:
                    key = f"{name}/{engine}"
                    if key not in solved:
                        solved[key] = {"status": "unavailable", "reason": status.get(engine, "not detected"), "findings": []}
                    if engine in args.require and solved[key]["status"] != "complete":
                        failures.append(f"{key}: required engine did not complete this case ({solved[key]['status']})")
            failures += [f"{key}: {finding}" for key, outcome in solved.items() for finding in outcome["findings"]]
        evidence = public_report(report, solved, failures)
    text = json.dumps(evidence, indent=2, sort_keys=True)
    if args.json:
        args.json.write_text(text + "\n", encoding="utf-8")
    print(text if not args.json else f"{evidence['verdict']}: {len(failures)} failure(s); evidence in {args.json.name}")
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
