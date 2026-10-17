"""V11: WG's coupled FEM/BEM on Boundary Lab's SAWMOD fixture meshes, full against x and xy.

Doubly opt-in, through the compute broker only: WG2_RUN_LIVE=1 AND
WG2_FEM_BL_SAWMOD=1, plus an authenticated ``gh`` with network access. Without
any of these the module skips; it cannot fail a default gate.

Boundary Lab is GPL-3.0. Its meshes are fetched read-only at test time with
``gh api`` at a pinned commit into pytest's temporary directory and used as
data only; nothing fetched is kept in-tree. ``bl_sawmod_adapter`` is WG's own
reader (it documents the mesh semantics and the exterior rim repair).

The published reduced meshes are independent remeshes, not cuts of the full
mesh (about 14-56% of their nodes coincide with full-mesh nodes), and the full
mesh itself is not mirror symmetric. Two gates follow:

* exact symmetry self-consistency (the plan's V7/V11 1e-4 gate): each
  published fundamental domain is mirrored into its own exact full and half
  models (``xy -> x -> full`` and ``x -> full``); exterior polar pressure,
  FEM nodal pressure at the shared fundamental-domain nodes and the full
  models' mirror symmetry must agree to 1e-4;
* discretization consistency of the three published meshes: exterior polar
  pressure and P1-interpolated FEM pressure at the points every model
  represents, normalized by the per-frequency maximum, within
  max(5e-3, 2 (k h)²), h the largest 95th-percentile edge of the six meshes.
  Two independent P1 meshes each err by O((kh)²) for these smooth fields, so
  their difference is bounded by twice that; the floor covers the published
  meshes' 0.05% volume differences.

WG2_FEM_VALIDATION_DIR optionally receives JSON evidence.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from server.tests import bl_sawmod_adapter as bl
from server.tests.fem_validation import C, write_evidence
from server.tests.test_component_symmetry import reflect_inputs
from server.tests.test_fem_coupled_beat_live import worker as worker_fixture

_pytest_config = getattr(pytest.mark, "_config", None)
if _pytest_config is not None:
    _pytest_config.addinivalue_line("markers", "live: real native solver qualification")

pytestmark = [pytest.mark.live, pytest.mark.slow, pytest.mark.real_runtime,
              pytest.mark.skipif(os.environ.get("WG2_RUN_LIVE") != "1"
                                 or os.environ.get("WG2_FEM_BL_SAWMOD") != "1",
                                 reason="set WG2_RUN_LIVE=1 and WG2_FEM_BL_SAWMOD=1; run V11 through the compute broker")]

worker = worker_fixture
REPOSITORY = "m3gnus/boundary-lab"
REF = "bb9030c4"
FIXTURE_DIR = "tests/fixtures/SAWMOD"
# Git blob ids at REF, checked against the fetched bytes.
BLOBS = {
    "SMexterior_full.msh": "ce19295de483d87da431cf6e7a057c6ff222fa3a",
    "SMexterior_reduced_x.msh": "0e8d05ea0ac805349171cb3b739c8b0c3616301d",
    "SMexterior_reduced_xy.msh": "8892ca99624020238218f577913ded0fa9c0cbb5",
    "SMfemvolume_full.msh": "f57af8b67324d392c28593a597f22bb226aff88a",
    "SMfemvolume_reduced_x.msh": "78f3652ba49dcb8de02126ee769ebe46b4239610",
    "SMfemvolume_reduced_xy.msh": "2a8155ae8180c23e6f52b512ad24265cfeabf546",
}
FREQUENCIES = [100., 200.]  # Below the cavity's first mode (~470 Hz) and BEM's interior resonances.
EXACT_TOLERANCE = 1e-4
# BEAT's own CPU float64 coupled tests require these on conforming interfaces.
CONTINUITY_LIMIT, FLUX_LIMIT = 1e-8, 1e-10
FRAME = {"origin": [0., 0., .13], "axis": [0., 0., 1.], "u": [1., 0., 0.], "v": [0., 1., 0.]}
CHANNELS = ("drive-hf", "drive-mf")


def _gh_environment():
    """The suite sandboxes HOME; gh must read the account's own config and keyring."""
    import pwd

    home = pwd.getpwuid(os.getuid()).pw_dir
    return {**os.environ, "HOME": home,
            "GH_CONFIG_DIR": os.environ.get("GH_CONFIG_DIR") or os.path.join(home, ".config", "gh")}


def _gh(*args, timeout=60, attempts=3):
    """Read-only ``gh`` call; GitHub's HTTP/2 stream resets are retried."""
    for attempt in range(attempts):
        try:
            return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout, check=True,
                                  env=_gh_environment()).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == attempts - 1:
                raise


@pytest.fixture(scope="module")
def sawmod_dir(tmp_path_factory):
    """Fetch the six meshes read-only; skip with a reason when gh cannot."""
    if shutil.which("gh") is None:
        pytest.skip("gh is not installed; V11 fetches Boundary Lab meshes with gh api")
    try:
        status = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=30,
                                env=_gh_environment())
    except subprocess.TimeoutExpired:
        pytest.skip("gh auth status timed out (offline?)")
    if status.returncode != 0:
        pytest.skip(f"gh is not authenticated: {(status.stderr or status.stdout).strip()[:200]}")
    directory = tmp_path_factory.mktemp("bl-sawmod")
    for name, blob in BLOBS.items():
        try:
            listed = _gh("api", f"repos/{REPOSITORY}/contents/{FIXTURE_DIR}/{name}?ref={REF}", "--jq", ".sha").strip()
            content = _gh("api", f"repos/{REPOSITORY}/git/blobs/{listed}", "--jq", ".content", timeout=120)
        except subprocess.TimeoutExpired:
            pytest.skip(f"gh api timed out fetching {name} (offline?)")
        except subprocess.CalledProcessError as exc:
            pytest.skip(f"gh api could not fetch {name} (offline or unauthorized): {exc.stderr.strip()[:200]}")
        data = base64.b64decode(content)
        assert listed == blob, f"{name}: {REF} lists blob {listed}, expected {blob}"
        assert hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == blob, f"{name}: fetched bytes differ"
        (directory / name).write_bytes(data)
    return directory


def _layout():
    from server.solver.beat_adapter.observations import build_observations

    # 15 degree steps; every angle has its mirror, and both planes cross both cuts.
    return build_observations(angle_range=(-180., 180., 25), planes=("horizontal", "vertical"),
                              origin_m=FRAME["origin"], sphere_grid=None, precision="float64")


@pytest.fixture(scope="module")
def solved(sawmod_dir, worker):
    """Six models, each solved once on the managed CPU float64 worker."""
    from server.solver.beat_adapter.coupled_request import build_coupled_request
    from server.solver.official_beat import solve_coupled_compiled

    published = {variant: bl.load_model(sawmod_dir, variant) for variant in ("full", "reduced_x", "reduced_xy")}
    models = {"bl-full": published["full"][:3], "bl-x": published["reduced_x"][:3],
              "bl-xy": published["reduced_xy"][:3]}
    models["xy-x"] = (*reflect_inputs(*models["bl-xy"][:2], 1), "yz")
    models["xy-full"] = (*reflect_inputs(*models["xy-x"][:2], 0), "full")
    models["x-full"] = (*reflect_inputs(*models["bl-x"][:2], 0), "full")
    manager = SimpleNamespace(get_worker=lambda *a, **k: worker)
    layout = _layout()
    results = {}
    for name, (exterior, fem, mode) in models.items():
        request = build_coupled_request(exterior, [fem], layout=layout, frame=FRAME,
                                        frequencies_hz=FREQUENCIES, symmetry=mode)
        assert tuple(request.channel_ports) == CHANNELS
        sweep = solve_coupled_compiled(request, worker_manager=manager)
        assert not sweep.cancelled
        results[name] = SimpleNamespace(request=request, sweep=sweep, fem=request.regions[0])
    return SimpleNamespace(models=results, repairs={k: v[3] for k, v in published.items()}, directory=sawmod_dir)


def polar(result, channel):
    return result.sweep.channels[channel].pressure_complex  # frequency, plane, angle; solver convention


def nodal(result, channel):
    ports = result.request.wire["excitation_port_ids"]
    return result.sweep.nodal_pressure[:, ports.index(result.request.channel_ports[channel][0])]


def normalized_error(computed, reference):
    """Worst per-frequency max|difference| / max|reference| over everything else."""
    computed, reference = np.asarray(computed), np.asarray(reference)
    axes = tuple(range(1, reference.ndim))
    return float(np.max(np.max(np.abs(computed - reference), axis=axes) / np.max(np.abs(reference), axis=axes)))


def mirrored_angles(result):
    angles = result.request.layout.angles_deg
    order = np.argsort(angles)
    assert np.array_equal(angles[order][::-1], -angles[order])
    return order, order[::-1]


MIRROR_PLANE = {0: "horizontal", 1: "vertical"}  # x0 maps +-theta in x-z; y0 in y-z.


def interface_diagnostics(result):
    from server.solver.result_mapping import coupled_interface_diagnostics

    diagnostics = coupled_interface_diagnostics(result.sweep, result.request)
    continuity = [v for item in diagnostics["interfaces"] for v in item["pressure_continuity_error"]]
    flux = [v for item in diagnostics["interfaces"] for v in item["flux_conservation_error"]]
    return {"pressure_continuity_error": continuity, "flux_conservation_error": flux,
            "continuity_limit": CONTINUITY_LIMIT, "flux_limit": FLUX_LIMIT,
            "passed": bool(all(v is not None and v <= CONTINUITY_LIMIT for v in continuity)
                           and all(v is not None and v <= FLUX_LIMIT for v in flux))}


def mesh_record(solved):
    regions = {name: {"region_id": name, "stats": {"nodes": len(r.fem.domain.points_mm),
                                                   "tetrahedra": len(r.fem.domain.tetrahedra)}}
               for name, r in solved.models.items()}
    return {"fem_regions": list(regions.values())}


def provenance(solved):
    return {"repository": REPOSITORY, "ref": REF, "blobs": BLOBS, "exterior_rim_repair": solved.repairs,
            "license_note": "Boundary Lab meshes used as fetched test data only; not stored in WG"}


def finite_nonzero(result, channel):
    for values in (polar(result, channel), nodal(result, channel)):
        assert np.isfinite(values).all(), f"{channel}: non-finite response"
        assert np.all(np.abs(values) > 0), f"{channel}: zero response"


def test_v11_sawmod_mirror_completed_full_x_xy_agree(solved):
    """Exact: models mirrored from one published fundamental domain agree to 1e-4."""
    m = solved.models
    checks, computed, reference = {}, [], []
    for channel in CHANNELS:
        for name in ("bl-xy", "xy-x", "xy-full", "bl-x", "x-full"):
            finite_nonzero(m[name], channel)
        count_xy, count_x = len(m["bl-xy"].fem.domain.points_mm), len(m["bl-x"].fem.domain.points_mm)
        pairs = {"xy-full_vs_xy-x": ("xy-full", "xy-x", count_xy), "xy-full_vs_bl-xy": ("xy-full", "bl-xy", count_xy),
                 "x-full_vs_bl-x": ("x-full", "bl-x", count_x)}
        for label, (full, reduced, count) in pairs.items():
            # reflect_inputs keeps the original nodes first, in order.
            np.testing.assert_array_equal(m[full].fem.domain.points_mm[:count], m[reduced].fem.domain.points_mm[:count])
            exterior_error = float(np.max(np.abs(polar(m[reduced], channel) - polar(m[full], channel))
                                          / np.abs(polar(m[full], channel))))
            fem_error = normalized_error(nodal(m[reduced], channel)[:, :count], nodal(m[full], channel)[:, :count])
            checks[f"{channel}:{label}"] = {
                "exterior_pointwise_relative_error": exterior_error, "fem_normalized_error": fem_error,
                "fem_pointwise_relative_error": float(np.max(
                    np.abs(nodal(m[reduced], channel)[:, :count] - nodal(m[full], channel)[:, :count])
                    / np.abs(nodal(m[full], channel)[:, :count]))),
                "tolerance": EXACT_TOLERANCE,
                "passed": bool(exterior_error <= EXACT_TOLERANCE and fem_error <= EXACT_TOLERANCE)}
            computed.append(polar(m[reduced], channel).ravel())
            reference.append(polar(m[full], channel).ravel())
        for full in ("xy-full", "x-full"):
            # Mirror symmetry of an exactly symmetric full model: polar +-theta
            # in both planes, and FEM nodes against their exact mirror nodes.
            from scipy.spatial import cKDTree

            # x-full is exact only across x0: its source mesh was never cut at y0.
            axes = (0, 1) if full == "xy-full" else (0,)
            planes = [MIRROR_PLANE[axis] for axis in axes]
            layout_planes = list(m[full].request.layout.planes)
            pressure = polar(m[full], channel)[:, [layout_planes.index(p) for p in planes]]
            order, mirrored = mirrored_angles(m[full])
            exterior_error = float(np.max(np.abs(pressure[:, :, order] - pressure[:, :, mirrored])
                                          / np.abs(pressure[:, :, order])))
            points = m[full].fem.domain.points_mm
            tree = cKDTree(points)
            fem_error = 0.
            for axis in axes:
                image = points.copy()
                image[:, axis] *= -1
                gap, partner = tree.query(image)
                assert gap.max() == 0, f"{full}: mirror nodes are not exact"
                fem_error = max(fem_error, normalized_error(nodal(m[full], channel)[:, partner], nodal(m[full], channel)))
            checks[f"{channel}:{full}:mirror_symmetry"] = {
                "planes": planes, "exterior_pointwise_relative_error": exterior_error,
                "fem_normalized_error": fem_error, "tolerance": EXACT_TOLERANCE,
                "passed": bool(exterior_error <= EXACT_TOLERANCE and fem_error <= EXACT_TOLERANCE)}
    for name in ("xy-full", "xy-x", "bl-xy", "x-full", "bl-x"):
        checks[f"{name}:interface_diagnostics"] = interface_diagnostics(m[name])
    write_evidence("v11-sawmod-mirror-completed", FREQUENCIES, np.concatenate(computed), np.concatenate(reference),
                   EXACT_TOLERANCE, mesh_record(solved), quantity="exterior_polar_pressure_reduced_vs_full_solver_convention",
                   provenance=provenance(solved), checks=checks)
    failed = {k: v for k, v in checks.items() if not v["passed"]}
    assert not failed, f"V11 exact symmetry self-consistency: {failed}"


def _locate(points_mm, tetrahedra, queries, tolerance=1e-6):
    """Containing tetrahedron and P1 weights per query; -1 where no element contains it."""
    from scipy.spatial import cKDTree

    corners = points_mm[tetrahedra]
    tree = cKDTree(corners.mean(axis=1))
    found = np.full(len(queries), -1)
    weights = np.zeros((len(queries), 4))
    _, candidates = tree.query(queries, k=min(64, len(tetrahedra)))
    for row, (query, nearby) in enumerate(zip(queries, candidates)):
        for element in nearby:
            matrix = (corners[element, 1:] - corners[element, 0]).T
            local = np.linalg.solve(matrix, query - corners[element, 0])
            bary = np.r_[1 - local.sum(), local]
            if bary.min() >= -tolerance:
                found[row], weights[row] = element, bary
                break
    return found, weights


def _interpolate(result, channel, found, weights):
    values = nodal(result, channel)[:, result.fem.domain.tetrahedra[found]]  # frequency, query, 4
    return np.einsum("fqk,qk->fq", values, weights)


def discretization_bound(solved):
    from server.tests.bl_sawmod_adapter import read_gmsh

    longest = 0.
    for name in BLOBS:
        mesh = read_gmsh(Path(solved.directory) / name)
        cells = mesh.tetrahedra if len(mesh.tetrahedra) else np.concatenate(list(mesh.triangles.values()))
        width = cells.shape[1]
        edges = np.concatenate([np.linalg.norm(mesh.points_mm[cells[:, i]] - mesh.points_mm[cells[:, j]], axis=1)
                                for i in range(width) for j in range(i + 1, width)])
        longest = max(longest, float(np.percentile(edges, 95)))
    k = 2 * np.pi * np.asarray(FREQUENCIES) / C
    return np.maximum(5e-3, 2 * (k * longest * 1e-3) ** 2), longest


def test_v11_sawmod_published_full_x_xy_meshes_agree_within_discretization(solved):
    """Independent published meshes: agreement at shared evaluation points within the P1 bound."""
    m = solved.models
    bound, edge = discretization_bound(solved)
    full, x, xy = m["bl-full"], m["bl-x"], m["bl-xy"]
    # Evaluation points: quarter-mesh nodes off both cut planes that the half
    # and full meshes also contain (most nodes of this thin cavity are on its
    # boundary; curved-wall nodes outside another mesh's chords are skipped).
    domain = xy.fem.domain
    candidates = np.flatnonzero((domain.points_mm[:, 0] > 1) & (domain.points_mm[:, 1] > 1))
    queries = domain.points_mm[candidates]
    in_full = _locate(full.fem.domain.points_mm, full.fem.domain.tetrahedra, queries)
    in_x = _locate(x.fem.domain.points_mm, x.fem.domain.tetrahedra, queries)
    shared = (in_full[0] >= 0) & (in_x[0] >= 0)
    assert shared.sum() >= 50, f"Only {shared.sum()} shared FEM evaluation points"
    weights_full = (in_full[0][shared], in_full[1][shared])
    weights_x = (in_x[0][shared], in_x[1][shared])
    checks, computed, reference = {}, [], []
    for channel in CHANNELS:
        for result in (full, x, xy):
            finite_nonzero(result, channel)
        fem_full = _interpolate(full, channel, *weights_full)
        fem = {"bl-x": _interpolate(x, channel, *weights_x), "bl-xy": nodal(xy, channel)[:, candidates[shared]]}
        for name, result in (("bl-x", x), ("bl-xy", xy)):
            exterior = np.array([normalized_error(polar(result, channel)[i], polar(full, channel)[i])
                                 for i in range(len(FREQUENCIES))])
            interior = np.array([normalized_error(fem[name][i:i + 1], fem_full[i:i + 1]) for i in range(len(FREQUENCIES))])
            checks[f"{channel}:{name}_vs_bl-full"] = {
                "exterior_normalized_error": exterior.tolist(), "fem_normalized_error": interior.tolist(),
                "bound": bound.tolist(), "shared_fem_points": int(shared.sum()),
                "passed": bool(np.all(exterior <= bound) and np.all(interior <= bound))}
            # Headline in the gate's own normalization: max|p_full| + max|difference|
            # against max|p_full|, per frequency and plane.
            scale = np.abs(polar(full, channel)).max(axis=-1)
            computed.append((scale + np.abs(polar(result, channel) - polar(full, channel)).max(axis=-1)).ravel())
            reference.append(scale.ravel())
        # The published full mesh is not mirror symmetric; its pressure is,
        # to discretization: polar +-theta and nodes against P1 at their image.
        layout_planes = list(full.request.layout.planes)
        pressure = polar(full, channel)[:, [layout_planes.index(MIRROR_PLANE[a]) for a in (0, 1)]]
        order, mirrored = mirrored_angles(full)
        exterior = np.array([normalized_error(pressure[i][:, mirrored], pressure[i][:, order])
                             for i in range(len(FREQUENCIES))])
        points = full.fem.domain.points_mm
        interior = np.zeros(len(FREQUENCIES))
        for axis in (0, 1):
            nodes = np.flatnonzero(points[:, axis] > 1)
            image = points[nodes].copy()
            image[:, axis] *= -1
            found, weights = _locate(points, full.fem.domain.tetrahedra, image)
            keep = found >= 0
            mirrored_values = _interpolate(full, channel, found[keep], weights[keep])
            own = nodal(full, channel)[:, nodes[keep]]
            interior = np.maximum(interior, [normalized_error(mirrored_values[i:i + 1], own[i:i + 1])
                                             for i in range(len(FREQUENCIES))])
        checks[f"{channel}:bl-full:mirror_symmetry"] = {
            "exterior_normalized_error": exterior.tolist(), "fem_normalized_error": interior.tolist(),
            "bound": bound.tolist(), "passed": bool(np.all(exterior <= bound) and np.all(interior <= bound))}
    for name in ("bl-full", "bl-x", "bl-xy"):
        checks[f"{name}:interface_diagnostics"] = interface_diagnostics(m[name])
    write_evidence("v11-sawmod-published-meshes", FREQUENCIES, np.concatenate(computed), np.concatenate(reference),
                   float(bound.max()), mesh_record(solved),
                   quantity="max_abs_full_plus_max_difference_pa_per_frequency_and_plane",
                   bound_formula="max(5e-3, 2 (k h95)^2)", bound_per_frequency=bound.tolist(), h95_mm=edge,
                   provenance=provenance(solved), checks=checks)
    failed = {k: v for k, v in checks.items() if not v["passed"]}
    assert not failed, f"V11 published-mesh consistency: {failed}"
