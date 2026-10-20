"""FIFO asyncio job scheduler and solve lifecycle.

The single-slot FIFO queue, queued/running recovery, active-delete rules, and
strong task references port v1 ``server/services/job_runtime.py:23-41,92-208,
259-285,497-574,629-686``. The dry-run solve itself is dispatched with
``asyncio.to_thread`` just like the retained in-process v1 execution model.
"""

import logging

# Construct the shared logger before core loads to retain its original category.
logger = logging.getLogger(__name__)

from .core import (  # noqa: E402
    EngineUnavailableError as EngineUnavailableError,
    ImportedSolveRefusal as ImportedSolveRefusal,
    JobConflictError as JobConflictError,
    JobMeshDiscardedError as JobMeshDiscardedError,
    JobNotFoundError as JobNotFoundError,
    JobResourceUnavailableError as JobResourceUnavailableError,
    JobRuntime as JobRuntime,
    RemovedSolverError as RemovedSolverError,
    SymmetryValidationError as SymmetryValidationError,
    UnknownEngineError as UnknownEngineError,
    resolve_submission as resolve_submission,
)
