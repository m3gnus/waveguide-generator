"""Whether a Solve anyway answer carries to a press: the one owner of that rule.

Solve anyway answers the triangle ceiling for one preparation -- the mesh the
user saw, or, answered in Fusion before sending (``acknowledged``), the mesh
the delivery would make -- and for nothing else. A press that would make
another mesh is asked again. Every caller asks :func:`carries`:

- Solve again of a held job (``cad_intent.solve_again_intent``);
- the first press of a manual-waiting job (``JobRuntime.prepare_cad_solve``);
- admission of a delivery with an answer from Fusion (``job_shims``);
- a model Solve from WG's model page (``model_solve``).

The rule has two forms of evidence, and the strongest one available decides:

- **The mesh.** When the answer was given on a prepared mesh (its ingest
  record's ``mesh_lookup_key`` and domain plan), the press's own preparation,
  normalised as ingestion normalises it, must have that same cache address
  and domain plan. A changed solve option (an engine) that leaves the mesh
  alone still carries; any change to what is meshed (sizes, skipped sources,
  symmetry, deviation, frame, domain reading) does not. An answer whose
  record predates the key, or a press whose mesh cannot be worked out, is not
  known to be the same, so it does not carry.
- **The preparation's identity.** Before any mesh exists -- a Fusion answer
  at admission, a manual first press, a Solve again whose parent mesh the
  caller does not read -- only the setup revision and the frame axis are
  known: the press must keep both (a revision the answer's record says it was
  prepared with counts as kept). Naming another revision asks again even when
  its sizes happen to match, since nothing shows the mesh would be the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: A domain plan nobody stated (identity evidence carries none).
_UNSTATED = object()


@dataclass(frozen=True)
class Answered:
    """What a Solve anyway answer was given for."""

    setup_revision_id: str | None = None
    frame_axis: str | None = None
    #: The setup revision the answer's record says it was prepared with.
    recorded_setup_revision_id: str | None = None
    #: Mesh evidence: the prepared mesh's cache address and domain plan.
    #: ``mesh_known``: the answer was given on a prepared mesh.
    mesh_known: bool = False
    mesh_lookup_key: str | None = None
    domain: Any = _UNSTATED


@dataclass(frozen=True)
class Pressed:
    """What a press would prepare."""

    setup_revision_id: str | None = None
    frame_axis: str | None = None
    mesh_lookup_key: str | None = None
    domain: Any = _UNSTATED


def carries(answered: Answered | None, pressed: Pressed) -> bool:
    """Whether ``answered`` (None: no answer) carries to ``pressed``. See the module."""

    if answered is None:
        return False
    if answered.mesh_known:
        return (
            bool(answered.mesh_lookup_key)
            and pressed.mesh_lookup_key == answered.mesh_lookup_key
            and pressed.domain == answered.domain
        )
    return (
        pressed.setup_revision_id is None
        or pressed.setup_revision_id in {answered.setup_revision_id, answered.recorded_setup_revision_id}
    ) and (pressed.frame_axis is None or pressed.frame_axis == answered.frame_axis)
