"""Provision and inspect WG-owned CPU/GPU runtimes for setup hooks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import assets, hardware, readiness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("provision", "status", "clear-cache"), default="provision")
    parser.add_argument("--backend", choices=("auto", *readiness.BACKENDS), default="auto")
    parser.add_argument("--if-gpu", action="store_true")
    parser.add_argument("--if-nvidia-gpu", action="store_true", help="only provision when NVIDIA hardware is detected")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--dir", type=Path)
    parser.add_argument("--julia", dest="julia_executable")
    parser.add_argument("--project", dest="julia_project", type=Path)
    parser.add_argument("--threads", dest="julia_threads", default="auto")
    parser.add_argument("--depot", type=Path)
    args = parser.parse_args(argv)
    if args.backend == "cpu" and (args.if_gpu or args.if_nvidia_gpu):
        parser.error("--backend cpu cannot be combined with --if-gpu/--if-nvidia-gpu")
    if args.julia_threads != "auto":
        try:
            args.julia_threads = int(args.julia_threads)
            if args.julia_threads < 1:
                raise ValueError
        except ValueError:
            parser.error("--threads must be auto or a positive integer")
    options = {name: getattr(args, name) for name in (
        "julia_executable", "julia_project", "julia_threads", "depot",
    )}
    if args.command == "clear-cache":
        readiness.probe_cache_clear(directory=args.dir, persist=True)
        return 0
    if args.command == "status":
        statuses = (readiness.beat_backend_statuses(args.dir, **options) if args.backend == "auto"
                    else {args.backend: readiness.backend_status(args.backend, args.dir, **options)})
        print(json.dumps(statuses, sort_keys=True))
        return 0
    only = None if args.backend == "auto" or args.if_nvidia_gpu else args.backend
    inventory = hardware.gpu_hardware(only=only) if args.backend != "cpu" else {}
    if args.if_nvidia_gpu and not inventory["cuda"]["available"]:
        return 0
    backend = hardware.detect_gpu_backend(inventory=inventory) if args.backend == "auto" else args.backend
    if backend is None:
        if not args.if_gpu and not args.if_nvidia_gpu:
            print("No supported GPU detected; nothing to provision.")
        return 0
    if backend in hardware.GPU_BACKENDS and not inventory[backend]["available"]:
        if not args.if_gpu and not args.if_nvidia_gpu:
            print(f"BEAT {backend}: no eligible hardware; nothing to provision.")
        return 0
    try:
        # An optional missing engine must not create state or fetch Julia.
        assets.engine_assets(backend)
        action = getattr(readiness, f"provision_{backend}")
        if backend in hardware.GPU_BACKENDS:
            options["hardware_facts"] = inventory[backend]
        record = action(args.dir, force=args.force, retry=args.retry, **options)
        return 0 if record.get("status") in {"ready", "skipped"} else 1
    except Exception as exc:
        print(f"BEAT {backend} provisioning unavailable: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
