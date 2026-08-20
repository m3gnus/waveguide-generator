"""The hard refusal limits for untrusted CAD input.

``docs/plans/STEP-PARSER-ISOLATION.md`` is the accepted gate these numbers come
from.  They live in one module because a limit that is written twice is a limit
that drifts: the downloader, the bundle reader, the STEP text scanner, and the
two child-process invocations all quote the same table.

These are refusal limits, not allocation targets.  Nothing here reserves memory
or preallocates a buffer; each value is the point at which WG stops reading and
reports a stage-labelled ingest refusal.  Raising one is a contract revision
with measured fixtures behind it, not a config knob.
"""

from __future__ import annotations


_KIB = 1024
_MIB = 1024 * 1024
_GIB = 1024 * 1024 * 1024

#: One downloaded STEP body, enforced while streaming.
MAX_DOWNLOAD_BYTES = 64 * _MIB

#: One ``wgreturn.json`` manifest.
MAX_WGRETURN_JSON_BYTES = 1 * _MIB

#: Entity records in one STEP Part 21 file.
MAX_STEP_RECORDS = 1_000_000

#: One STEP entity record, measured over its raw bytes.
MAX_STEP_RECORD_BYTES = 8 * _MIB

#: One STEP string literal after Part 21 control-directive decoding.
MAX_STEP_LABEL_CHARS = 4 * _KIB

#: Wall time and resident memory for the inspect child.
INSPECT_TIMEOUT_S = 60.0
INSPECT_MEMORY_BYTES = 2 * _GIB

#: Wall time and resident memory for the mesh child.
MESH_TIMEOUT_S = 600.0
MESH_MEMORY_BYTES = 4 * _GIB

#: One child's structured JSON result.
MAX_CHILD_RESULT_BYTES = 8 * _MIB

#: One staged mesh or viewport artifact.
MAX_STAGED_ARTIFACT_BYTES = 512 * _MIB

#: External-STEP children that may run at once, across the whole process.
MAX_CONCURRENT_STEP_CHILDREN = 1

#: Native stderr is diagnostic text, never a verdict.  Only a tail is retained.
MAX_RETAINED_STDERR_BYTES = 8 * _KIB


__all__ = [
    "INSPECT_MEMORY_BYTES",
    "INSPECT_TIMEOUT_S",
    "MAX_CHILD_RESULT_BYTES",
    "MAX_CONCURRENT_STEP_CHILDREN",
    "MAX_DOWNLOAD_BYTES",
    "MAX_RETAINED_STDERR_BYTES",
    "MAX_STAGED_ARTIFACT_BYTES",
    "MAX_STEP_LABEL_CHARS",
    "MAX_STEP_RECORDS",
    "MAX_STEP_RECORD_BYTES",
    "MAX_WGRETURN_JSON_BYTES",
    "MESH_MEMORY_BYTES",
    "MESH_TIMEOUT_S",
]
