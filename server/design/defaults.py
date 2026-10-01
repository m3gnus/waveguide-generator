"""Sweep start shared by parametric requests and CAD solve defaults."""

from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def default_sweep_start_hz() -> float:
    path = Path(__file__).resolve().parents[2] / "shared" / "solve-defaults.json"
    return float(json.loads(path.read_text(encoding="utf-8"))["sweep"]["start_hz"])
