"""Compatibility and dependency boundaries for shared observation values."""

from __future__ import annotations

import pickle
from pathlib import Path
import subprocess
import sys

import pytest

from server.contracts import observations
from server.jobs import models


@pytest.mark.parametrize(
    "name,body",
    [
        ("PolarConfig", {}),
        ("FieldPlaneSpec", {
            "origin_m": [0, 0, 0], "axis_u": [1, 0, 0], "axis_v": [0, 1, 0],
            "width_m": 2, "height_m": 3, "nx": 3, "ny": 4,
        }),
        ("FieldPlaneResponseSpec", {"id": "channel:left"}),
        ("FieldPlaneRequest", {
            "version": 1, "request_id": "plane-1", "frequency_index": 0,
            "response": {"id": "system"},
            "plane": {
                "origin_m": [0, 0, 0], "axis_u": [1, 0, 0], "axis_v": [0, 1, 0],
                "width_m": 2, "height_m": 3, "nx": 3, "ny": 4,
            },
        }),
    ],
)
def test_historical_class_identity_and_pickle(name, body):
    cls = getattr(observations, name)
    assert getattr(models, name) is cls
    assert cls.__module__ == "server.jobs.models"
    assert cls.__pydantic_core_schema__["ref"].startswith(f"server.jobs.models.{name}:")
    instance = cls.model_validate(body)
    restored = pickle.loads(pickle.dumps(instance))
    assert type(restored) is cls
    assert restored == instance
    assert restored.model_dump_json() == instance.model_dump_json()


def test_leaf_observations_import_without_application_or_native_backends():
    completed = subprocess.run(
        [sys.executable, "-c", """
import sys
from server.contracts.observations import (
    FieldPlaneRequest, FieldPlaneResponseSpec, FieldPlaneSpec, PolarConfig,
)

# This is the first application import in this process. No jobs facade,
# design/CAD orchestration or solver/native package may be needed by values.
for name in sys.modules:
    assert not name.startswith('server.') or name.startswith('server.contracts'), name
    assert name.split('.')[0] not in {
        'bempp', 'bempp_cl', 'gmsh', 'hornlab_bempp_bem', 'hornlab_metal_bem',
        'hornlab_sim', 'hornlab_mesher', 'beat_engine', 'numpy',
    }, name
assert PolarConfig().resolved_grid()['count'] == 37

# The historical names resolve to the very same objects when imported later.
from server.jobs import models
for cls in (FieldPlaneRequest, FieldPlaneResponseSpec, FieldPlaneSpec, PolarConfig):
    assert getattr(models, cls.__name__) is cls
"""],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
