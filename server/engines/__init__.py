"""Solver-engine discovery seam."""

from .registry import EngineInfo, detect_engines, get_engine, resolve_auto_engine

__all__ = ["EngineInfo", "detect_engines", "get_engine", "resolve_auto_engine"]
