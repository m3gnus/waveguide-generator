"""
Deterministic symmetry benchmark fixtures and harness.

This module is intentionally pure-Numpy so it can be used in tests and manual
research without requiring gmsh or bempp-cl.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

from .symmetry import evaluate_symmetry_policy

SOURCE_TAG = 2
WALL_TAG = 1


@dataclass(frozen=True)
class SymmetryBenchmarkCase:
    name: str
    description: str
    vertices: np.ndarray
    indices: np.ndarray
    surface_tags: np.ndarray
    throat_elements: np.ndarray
    expected_reason: str
    expected_symmetry_type: str
    expected_reduction_factor: float
    expected_applied: bool


def _build_case(
    *,
    name: str,
    description: str,
    x_bounds: Sequence[float],
    z_bounds: Sequence[float],
    include_yz_plane: bool,
    include_xy_plane: bool,
    source_mode: str = "full_face",
    expected_reason: str,
    expected_symmetry_type: str,
    expected_reduction_factor: float,
    expected_applied: bool,
) -> SymmetryBenchmarkCase:
    min_x, max_x = float(x_bounds[0]), float(x_bounds[1])
    min_y, max_y = 0.0, 1.0
    min_z, max_z = float(z_bounds[0]), float(z_bounds[1])

    vertices: List[List[float]] = []
    triangles: List[List[int]] = []
    surface_tags: List[int] = []

    def split_axis(min_value: float, max_value: float) -> List[float]:
        values = [float(min_value)]
        if min_value < 0.0 < max_value:
            values.append(0.0)
        values.append(float(max_value))
        return values

    def add_rect(
        a: Sequence[float],
        b: Sequence[float],
        c: Sequence[float],
        d: Sequence[float],
        tag_a: int,
        tag_b: Optional[int] = None,
    ) -> None:
        base = len(vertices)
        vertices.extend(
            [
                [float(a[0]), float(a[1]), float(a[2])],
                [float(b[0]), float(b[1]), float(b[2])],
                [float(c[0]), float(c[1]), float(c[2])],
                [float(d[0]), float(d[1]), float(d[2])],
            ]
        )
        triangles.append([base, base + 1, base + 2])
        triangles.append([base, base + 2, base + 3])
        surface_tags.append(int(tag_a))
        surface_tags.append(int(tag_a if tag_b is None else tag_b))

    x_points = split_axis(min_x, max_x)
    z_points = split_axis(min_z, max_z)
    source_triangle_emitted = False

    for x0, x1 in zip(x_points[:-1], x_points[1:]):
        for z0, z1 in zip(z_points[:-1], z_points[1:]):
            tag_a = WALL_TAG
            tag_b = WALL_TAG
            if source_mode == "full_face":
                tag_a = SOURCE_TAG
                tag_b = SOURCE_TAG
            elif source_mode == "off_center_triangle" and not source_triangle_emitted:
                tag_a = SOURCE_TAG
                source_triangle_emitted = True
            elif source_mode != "off_center_triangle":
                raise ValueError(f"Unsupported source_mode: {source_mode}")

            add_rect(
                (x0, min_y, z0),
                (x1, min_y, z0),
                (x1, min_y, z1),
                (x0, min_y, z1),
                tag_a,
                tag_b,
            )
            add_rect(
                (x0, max_y, z0),
                (x1, max_y, z0),
                (x1, max_y, z1),
                (x0, max_y, z1),
                WALL_TAG,
            )

    for x0, x1 in zip(x_points[:-1], x_points[1:]):
        add_rect(
            (x0, min_y, min_z),
            (x0, max_y, min_z),
            (x1, max_y, min_z),
            (x1, min_y, min_z),
            WALL_TAG,
        )
        add_rect(
            (x1, min_y, max_z),
            (x1, max_y, max_z),
            (x0, max_y, max_z),
            (x0, min_y, max_z),
            WALL_TAG,
        )

    for z0, z1 in zip(z_points[:-1], z_points[1:]):
        add_rect(
            (min_x, min_y, z1),
            (min_x, max_y, z1),
            (min_x, max_y, z0),
            (min_x, min_y, z0),
            WALL_TAG,
        )
        add_rect(
            (max_x, min_y, z0),
            (max_x, max_y, z0),
            (max_x, max_y, z1),
            (max_x, min_y, z1),
            WALL_TAG,
        )

    if include_yz_plane:
        for z0, z1 in zip(z_points[:-1], z_points[1:]):
            add_rect(
                (0.0, min_y, z0),
                (0.0, max_y, z0),
                (0.0, max_y, z1),
                (0.0, min_y, z1),
                WALL_TAG,
            )
    if include_xy_plane:
        for x0, x1 in zip(x_points[:-1], x_points[1:]):
            add_rect(
                (x0, min_y, 0.0),
                (x1, min_y, 0.0),
                (x1, max_y, 0.0),
                (x0, max_y, 0.0),
                WALL_TAG,
            )

    vertices_array = np.array(vertices, dtype=float).T
    indices_array = np.array(triangles, dtype=np.int32)
    surface_tags_array = np.array(surface_tags, dtype=np.int32)
    throat_elements = np.where(surface_tags_array == SOURCE_TAG)[0]

    return SymmetryBenchmarkCase(
        name=name,
        description=description,
        vertices=vertices_array,
        indices=indices_array,
        surface_tags=surface_tags_array,
        throat_elements=throat_elements,
        expected_reason=expected_reason,
        expected_symmetry_type=expected_symmetry_type,
        expected_reduction_factor=expected_reduction_factor,
        expected_applied=expected_applied,
    )


def get_symmetry_benchmark_cases() -> Dict[str, SymmetryBenchmarkCase]:
    return {
        "full_reference": _build_case(
            name="full_reference",
            description="Asymmetric reference mesh with no geometric symmetry.",
            x_bounds=(0.0, 2.0),
            z_bounds=(0.0, 3.0),
            include_yz_plane=False,
            include_xy_plane=False,
            expected_reason="no_geometric_symmetry",
            expected_symmetry_type="full",
            expected_reduction_factor=1.0,
            expected_applied=False,
        ),
        "half_yz": _build_case(
            name="half_yz",
            description="Mesh symmetric about X=0 with a centered source face.",
            x_bounds=(-2.0, 2.0),
            z_bounds=(0.0, 3.0),
            include_yz_plane=True,
            include_xy_plane=False,
            expected_reason="post_tessellation_clipping_disabled",
            expected_symmetry_type="half_x",
            expected_reduction_factor=1.0,
            expected_applied=False,
        ),
        "quarter_xz": _build_case(
            name="quarter_xz",
            description="Mesh symmetric about X=0 and Z=0 with a centered source face.",
            x_bounds=(-2.0, 2.0),
            z_bounds=(-3.0, 3.0),
            include_yz_plane=True,
            include_xy_plane=True,
            expected_reason="post_tessellation_clipping_disabled",
            expected_symmetry_type="quarter_xz",
            expected_reduction_factor=1.0,
            expected_applied=False,
        ),
        "quarter_xz_off_center_source": _build_case(
            name="quarter_xz_off_center_source",
            description="Quarter-symmetric geometry whose source tag is intentionally off-center.",
            x_bounds=(-2.0, 2.0),
            z_bounds=(-3.0, 3.0),
            include_yz_plane=True,
            include_xy_plane=True,
            source_mode="off_center_triangle",
            expected_reason="excitation_off_center",
            expected_symmetry_type="quarter_xz",
            expected_reduction_factor=1.0,
            expected_applied=False,
        ),
    }


def evaluate_symmetry_benchmark_case(case: SymmetryBenchmarkCase) -> Dict[str, object]:
    result = evaluate_symmetry_policy(
        vertices=case.vertices,
        indices=case.indices,
        surface_tags=case.surface_tags,
        throat_elements=case.throat_elements,
        enable_symmetry=True,
    )
    policy = result["policy"]
    reduced_indices = result["reduced_indices"]
    reduced_triangles = int(reduced_indices.shape[0]) if isinstance(reduced_indices, np.ndarray) else 0
    summary = {
        "name": case.name,
        "description": case.description,
        "policy": policy,
        "symmetry": result["symmetry"],
        "mesh": {
            "vertices": int(case.vertices.shape[1]),
            "triangles": int(case.indices.shape[0]),
            "source_triangles": int(case.throat_elements.size),
            "reduced_triangles": reduced_triangles,
        },
        "expected": {
            "reason": case.expected_reason,
            "detected_symmetry_type": case.expected_symmetry_type,
            "reduction_factor": float(case.expected_reduction_factor),
            "applied": bool(case.expected_applied),
        },
    }
    summary["passed"] = bool(
        policy["reason"] == case.expected_reason
        and policy["detected_symmetry_type"] == case.expected_symmetry_type
        and float(policy["reduction_factor"]) == float(case.expected_reduction_factor)
        and bool(policy["applied"]) == bool(case.expected_applied)
    )
    return summary


def benchmark_symmetry_cases(
    *,
    case_names: Optional[Iterable[str]] = None,
    iterations: int = 25,
) -> Dict[str, object]:
    available = get_symmetry_benchmark_cases()
    requested_names = list(case_names or available.keys())
    unknown = sorted(set(requested_names) - set(available.keys()))
    if unknown:
        raise ValueError(f"Unknown symmetry benchmark case(s): {', '.join(unknown)}")

    normalized_iterations = max(int(iterations), 1)
    cases: List[Dict[str, object]] = []
    for name in requested_names:
        case = available[name]
        start = perf_counter()
        summary: Dict[str, object] = {}
        for _ in range(normalized_iterations):
            summary = evaluate_symmetry_benchmark_case(case)
        elapsed_ms = (perf_counter() - start) * 1000.0
        summary["timing"] = {
            "iterations": normalized_iterations,
            "total_ms": elapsed_ms,
            "avg_ms": elapsed_ms / normalized_iterations,
        }
        cases.append(summary)

    return {
        "cases": cases,
        "all_passed": all(bool(case["passed"]) for case in cases),
        "iterations": normalized_iterations,
        "available_cases": sorted(available.keys()),
    }
