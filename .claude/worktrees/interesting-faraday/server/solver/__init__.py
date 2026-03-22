try:
    from .bem_solver import BEMSolver
except ImportError:
    BEMSolver = None  # type: ignore[assignment,misc]

__all__ = ["BEMSolver"]
