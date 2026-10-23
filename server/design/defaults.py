"""Values shared by parametric requests and CAD solve defaults."""

from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def _defaults() -> dict:
    path = Path(__file__).resolve().parents[2] / "shared" / "solve-defaults.json"
    return json.loads(path.read_text(encoding="utf-8"))


def default_sweep_start_hz() -> float:
    return float(_defaults()["sweep"]["start_hz"])


def default_cad_wall_size_mm() -> float:
    return float(_defaults()["cad"]["mesh"]["rigid_size_mm"])
