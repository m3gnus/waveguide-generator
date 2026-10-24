"""Replay a return through inbox preparation in an isolated, retained scratch directory.

Run as a separate process: the diagnostic taps temporarily wrap existing pipeline
functions. They observe calls/results and never replace a pipeline decision.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack, closing, contextmanager, redirect_stdout
from functools import wraps
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
import uuid
from types import SimpleNamespace
from typing import Any


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def scratch_directory(requested: Path | None, *, source: Path | None, live: Path | None) -> Path:
    """Check resolved paths before creating anything; never inherit WG2_DATA_DIR."""
    from server.platform.paths import resolve_data_dir

    automatic = requested is None
    destination = (Path(tempfile.gettempdir()) / f"wg-cad-replay-{uuid.uuid4().hex}"
                   if automatic else requested).expanduser().resolve()
    protected = [
        Path.home() / "Library" / "WG-Development" / "data",
        resolve_data_dir(environ={key: value for key, value in os.environ.items() if key != "WG2_DATA_DIR"}),
        Path.home() / "Library" / "Application Support" / "WaveguideGenerator2",
    ]
    configured = os.environ.get("WG2_DATA_DIR")
    if configured:
        protected.append(Path(configured))
    if live is not None:
        protected.append(live)
    if source is not None:
        protected.append(source)
    if any(_overlaps(destination, path.expanduser().resolve()) for path in protected):
        raise ValueError("replay data directory overlaps a live data directory or the source bundle")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("replay data directory must be new or empty")
    destination.mkdir(mode=0o700, parents=True, exist_ok=not automatic)
    return destination


def request_bundle(request_id: str, live: Path) -> tuple[Path, str]:
    """Read the inbox operation's retained digest without store initialization/migrations.

    Query a private copy of the database and WAL: opening even mode=ro on the
    live database can create shared-memory files. No application stores, inbox
    collectors, acknowledgements or schema migrations run against the source.
    """
    from server.cadlink.intake.ingest import retained_snapshot_path
    from server.platform.temp_session import temporary_directory_root

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", request_id):
        raise ValueError("invalid request id")
    database = live / "db" / "cadlink.db"
    with tempfile.TemporaryDirectory(prefix="wg-cad-request-", dir=temporary_directory_root()) as temporary:
        copied = Path(temporary) / database.name
        _copy_database(database, copied)
        with closing(sqlite3.connect(copied.as_uri() + "?mode=ro", uri=True)) as connection:
            row = connection.execute(
                "SELECT snapshot_json, inputs_json FROM cad_operations WHERE operation_id = ?",
                (request_id,),
            ).fetchone()
    if row is None:
        raise ValueError(f"inbox request {request_id!r} is not in the live operation ledger")
    snapshot, inputs = (json.loads(value or "{}") for value in row)
    digest = snapshot.get("manifest_sha256") or inputs.get("manifest_sha256")
    bundle = retained_snapshot_path(live, str(digest or "")).resolve()
    if not bundle.is_relative_to(live) or not bundle.is_dir():
        raise ValueError("request's retained bundle is unavailable in the live data directory")
    return bundle, str(digest)


def _copy_database(source: Path, destination: Path) -> None:
    """Take a stable file copy including committed WAL rows, with no live locks/writes.

    A checkpoint or writer overlapping the copy changes the file signatures;
    retry that copy rather than query an inconsistent pair. A busy source fails
    explicitly so the developer can retry when the inbox is quiet.
    """
    files = (source, source.with_name(source.name + "-wal"))

    def signatures():
        result = []
        for path in files:
            try:
                info = path.stat()
                result.append((info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
            except FileNotFoundError:
                result.append(None)
        return result

    for _attempt in range(3):
        before = signatures()
        if before[0] is None:
            raise FileNotFoundError("live CAD operation database is unavailable")
        try:
            for path, signature in zip(files, before, strict=True):
                target = destination.with_name(destination.name + path.name[len(source.name):])
                if signature is None:
                    target.unlink(missing_ok=True)
                else:
                    shutil.copyfile(path, target)
        except FileNotFoundError:
            continue
        if signatures() == before:
            return
    raise RuntimeError("live operation database changed while copying; retry when it is quiet")


def _exception(exc: Exception) -> dict[str, Any]:
    return {
        "type": type(exc).__name__, "message": str(exc),
        "stage": getattr(exc, "stage", None),
        "reason_code": getattr(exc, "reason_code", None),
        "details": getattr(exc, "details", None),
        "child_diagnostics": getattr(exc, "diagnostics", None),
        "child_returncode": getattr(exc, "returncode", None),
        "traceback": "".join(traceback.format_exception(exc)),
        "cause": _exception(exc.__cause__) if isinstance(exc.__cause__, Exception) else None,
    }


class Report:
    def __init__(self) -> None:
        self.payload: dict[str, Any] = {"version": 1, "outcome": "error", "stages": []}
        self.depth = 0

    @contextmanager
    def stage(self, name: str):
        from server.cadlink.intake.ingest import IngestRefusal
        from server.cadlink.wgreturn import WgReturnError

        entry: dict[str, Any] = {"stage": name, "depth": self.depth, "outcome": "ok"}
        self.payload["stages"].append(entry)
        self.depth += 1
        started = time.perf_counter()
        try:
            yield entry
        except Exception as exc:
            entry.update(outcome="refused" if isinstance(exc, (IngestRefusal, WgReturnError)) else "error",
                         exception=_exception(exc))
            raise
        finally:
            self.depth -= 1
            entry["seconds"] = round(time.perf_counter() - started, 6)

    def with_internal_detail(self, refusal: dict[str, Any] | None) -> dict[str, Any] | None:
        """The stored refusal plus the raw detail an internal failure records for developers.

        The job keeps only WG's calm words (``cad_preparation.calm_refusal_message``);
        the detail lives in that refusal's ``internal-failure`` diagnostic.
        """

        if refusal is None:
            return None
        details = [
            diagnostic["evidence"].get("detail")
            for message in self.payload.get("logs", [])
            if (diagnostic := message.get("cad_diagnostic") or {}).get("stage") == "internal-failure"
            and diagnostic["evidence"].get("code") == refusal.get("code")
        ]
        return {**refusal, "detail": details[-1]} if details else refusal

    @contextmanager
    def capture_logs(self):
        messages = self.payload.setdefault("logs", [])

        class Capture(logging.Handler):
            def emit(self, record):
                messages.append({
                    "logger": record.name, "level": record.levelname,
                    "message": record.getMessage(),
                    "traceback": "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else None,
                    **({"cad_diagnostic": record.cad_diagnostic} if hasattr(record, "cad_diagnostic") else {}),
                })

        handler = Capture()
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            yield
        finally:
            root.removeHandler(handler)

    def tap(self, stack: ExitStack, module: Any, name: str, stage: str, summarize=None) -> None:
        original = getattr(module, name)

        @wraps(original)
        def observed(*args, **kwargs):
            with self.stage(stage) as entry:
                result = original(*args, **kwargs)
                if summarize is not None:
                    entry["evidence"] = summarize(result)
                return result

        setattr(module, name, observed)
        stack.callback(setattr, module, name, original)

    @contextmanager
    def observe_pipeline(self):
        from server.cadlink.intake import ingest
        from server.cadlink.interpretation import frame_infer
        from server.jobs import cad_preparation as preparation

        with ExitStack() as stack:
            for name, stage in (
                ("read_snapshot", "bundle validation"),
                ("validate_scope_inventory", "scope validation"),
                ("validate_registry_echoes", "registry consistency"),
                ("build_imported_mesh_isolated", "solver mesh build"),
                ("_mesh_fem_regions", "FEM mesh build"),
                ("_mesh_coupled_regions", "coupled mesh build"),
            ):
                self.tap(stack, ingest, name, stage,
                         _mesh_evidence if name == "build_imported_mesh_isolated" else None)
            self.tap(stack, ingest, "assess_cut_recovery", "cut recovery", lambda value: value.to_json())
            self.tap(stack, ingest, "apply_evidence", "cut evidence", _dataclass_json)
            self.tap(stack, ingest, "decide_domain_and_frame", "domain decision",
                     lambda value: {"interpretation": value[0], "decision": value[1]})
            self.tap(stack, frame_infer, "infer_record_frame", "frame inference", lambda value: value.to_json())
            self.tap(stack, preparation, "ensure_frame_suggestion", "frame suggestion", lambda value: value)
            self.tap(stack, preparation, "_default_setup", "default setup")
            self.tap(stack, preparation, "_load_setup", "setup lookup")
            self.tap(stack, preparation, "record_frame_refusal", "frame guard", lambda value: {"refusal": value})
            self.tap(stack, ingest, "resolve_domain_plan", "domain plan", _dataclass_json)
            self.tap(stack, preparation, "resolve_domain_plan", "domain plan", _dataclass_json)
            yield


def _dataclass_json(value):
    from dataclasses import asdict

    return asdict(value)


def _mesh_evidence(value):
    return {key: value.get(key) for key in (
        "stats", "normalisation", "symmetry", "symmetry_verification", "role_resolution",
        "healing", "self_intersection", "occ_tessellation",
    )}


async def _solve(request, data: Path, store, report: Report) -> None:
    from server.jobs.runtime import JobRuntime
    from server.jobs.store import JobStore

    jobs = JobStore.for_data_dir(data)
    runtime = JobRuntime(jobs, cadlink_store=store)
    try:
        with report.stage("solve") as entry:
            job_id = await runtime.submit(request)
            while True:
                row = await runtime.get_job(job_id)
                if row["status"] in {"complete", "error", "cancelled"}:
                    break
                await asyncio.sleep(0.1)
            entry["evidence"] = {"job_id": job_id, "status": row["status"],
                                 "error_message": row.get("error_message"),
                                 "log": jobs.get_job_log(job_id)}
            if row["status"] != "complete":
                raise RuntimeError(row.get("error_message") or f"solve {row['status']}")
    finally:
        await runtime.shutdown()
        jobs.close()


def replay(bundle_path: Path, data: Path, report: Report, *, retained: bool = False,
           solve: bool = False, engine: str | None = None,
           expected_manifest_sha256: str | None = None) -> None:
    from server.cadlink.intake import ingest
    from server.cadlink.joblane.project_setup import SOLVER_SELECTION
    from server.cadlink.store import CadLinkStore
    from server.jobs.cad_intent import CadSolveIntent
    from server.jobs.cad_preparation import CadPreparationHost, _Run, prepare_job_sync
    from server.jobs.store import JobStore

    store = CadLinkStore.for_data_dir(data)
    jobs = JobStore.for_data_dir(data)
    try:
        store.initialize()
        jobs.initialize()
        if engine:
            store.set_setting(SOLVER_SELECTION, {"engine": engine})
        with report.observe_pipeline():
            with report.stage("validate source") as entry:
                bundle = ingest.read_snapshot(bundle_path, retained=retained)
                if expected_manifest_sha256 is not None and bundle.manifest_sha256.removeprefix("sha256:") != expected_manifest_sha256.removeprefix("sha256:"):
                    from server.cadlink.wgreturn import WgReturnIntegrityError

                    raise WgReturnIntegrityError("retained manifest does not match the inbox request's digest")
                entry["evidence"] = {"manifest_sha256": bundle.manifest_sha256,
                                     "artifact_sha256": bundle.artifact_sha256}
            with report.stage("retain in scratch"):
                destination = ingest.retained_snapshot_path(data, bundle.manifest_sha256)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(bundle.path, destination)
            intent = CadSolveIntent(
                operation_id="cad-replay", bundle_path="ingest/cad-replay",
                manifest_sha256=bundle.manifest_sha256,
                return_id=str(bundle.manifest["return"]["id"]),
            )
            now = "2000-01-01T00:00:00Z"
            jobs.create_job({
                "id": "cad-replay", "status": "preparing", "created_at": now,
                "updated_at": now, "queued_at": now, "config_json": intent.to_config(),
                "config_summary_json": {},
            })
            jobs.claim_preparing_job("cad-replay", stage="validating", stage_message="Replay", progress=0)
            run = _Run(
                port=SimpleNamespace(job_store=jobs, publish=lambda _event: None),
                job_id="cad-replay", intent=intent,
                cad={"snapshot": {"manifest_sha256": bundle.manifest_sha256,
                                  "artifact_sha256": bundle.artifact_sha256}},
            )

            def observed_ingest(*args, **kwargs):
                with report.stage("ingest") as stage:
                    record = ingest.ingest_bundle(*args, **kwargs)
                    stage["evidence"] = {key: record.get(key) for key in (
                        "ingest_id", "symmetry", "normalisation", "domain_interpretation",
                        "domain_decision", "findings", "report_sha256", "mesh_stats",
                    )}
                    return record

            with report.stage("preparation") as entry:
                step, value = prepare_job_sync(
                    CadPreparationHost(store, data, ingest=observed_ingest), run, intent,
                )
                row = jobs.get_job_row("cad-replay")
                refusal = row["task_metadata"].get("cad", {}).get("refusal")
                entry["evidence"] = {"step": step, "cad": run.cad, "refusal": refusal}
                if step != "bind":
                    entry["outcome"] = "refused"
                    report.payload.update(outcome="refused", refusal=report.with_internal_detail(refusal))
                    return
                request, revision, preparation = value
                entry["evidence"]["request"] = request.model_dump(mode="json")
                report.payload["preparation_id"] = preparation["preparation_id"]
                report.payload["setup_revision_id"] = revision
        if solve:
            asyncio.run(_solve(request, data, store, report))
        report.payload["outcome"] = "ok"
    finally:
        jobs.close()
        store.close()


def render(payload: dict[str, Any], stream) -> None:
    print(f"CAD replay: {payload['outcome']}", file=stream)
    print(f"Scratch data: {payload.get('data_dir', '(not created)')}", file=stream)
    for stage in payload["stages"]:
        print(f"{'  ' * stage['depth']}{stage['stage']}: {stage['outcome']} ({stage['seconds']:.3f}s)", file=stream)
        if stage.get("evidence") is not None:
            print(json.dumps(stage["evidence"], indent=2, default=str), file=stream)
        if stage.get("exception"):
            print(stage["exception"]["traceback"], file=stream)
    for message in payload.get("logs", []):
        print(f"{message['level']} {message['logger']}: {message['message']}", file=stream)
        if message.get("traceback"):
            print(message["traceback"], file=stream)
    if payload.get("refusal"):
        print("Refusal: " + json.dumps(payload["refusal"]), file=stream)
    if payload.get("exception"):
        print(payload["exception"]["traceback"], file=stream)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, nargs="?")
    parser.add_argument("--from-request", metavar="REQUEST_ID")
    parser.add_argument("--live-data-dir", type=Path, help="Read-only source for --from-request")
    parser.add_argument("--data-dir", type=Path, help="New or empty scratch data directory; default: retained temporary directory")
    parser.add_argument("--json", action="store_true", help="Emit one JSON report on stdout")
    parser.add_argument("--solve", action="store_true", help="Also submit the prepared request to the real job runtime")
    parser.add_argument("--engine", help="Engine for the default setup (required with --solve)")
    args = parser.parse_args(argv)
    if bool(args.bundle) == bool(args.from_request):
        parser.error("provide a bundle OR --from-request")
    if bool(args.from_request) != bool(args.live_data_dir):
        parser.error("--from-request requires --live-data-dir, used only as a read-only source")
    if args.solve and not args.engine:
        parser.error("--solve requires --engine")
    report = Report()
    original_env = {key: os.environ.get(key) for key in ("WG2_DATA_DIR", "WG2_WGLINK_REFRESH")}
    try:
        live = args.live_data_dir.expanduser().resolve() if args.live_data_dir else None
        # All pipeline imports happen after input isolation is selected. Child
        # processes and helpers using data_paths() inherit only the scratch root.
        data = scratch_directory(args.data_dir, source=args.bundle, live=live)
        report.payload["data_dir"] = str(data)
        os.environ.update(WG2_DATA_DIR=str(data), WG2_WGLINK_REFRESH="0")
        with report.capture_logs(), redirect_stdout(sys.stderr):
            with report.stage("locate source"):
                if live:
                    source, expected_digest = request_bundle(args.from_request, live)
                else:
                    source, expected_digest = args.bundle.expanduser().resolve(), None
                if _overlaps(data, source):
                    raise ValueError("scratch directory overlaps the source bundle")
                report.payload["bundle"] = source.name
                report.payload["request_id"] = args.from_request
            replay(source, data, report, retained=bool(live), solve=args.solve, engine=args.engine,
                   expected_manifest_sha256=expected_digest)
    except Exception as exc:
        from server.cadlink.intake.ingest import IngestRefusal
        from server.cadlink.wgreturn import WgReturnError

        report.payload.update(
            outcome="refused" if isinstance(exc, (IngestRefusal, WgReturnError)) else "error",
            exception=_exception(exc),
        )
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    if args.json:
        print(json.dumps(report.payload, indent=2, default=str))
    else:
        render(report.payload, sys.stdout)
    return 0 if report.payload["outcome"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
