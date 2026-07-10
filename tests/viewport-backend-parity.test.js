/**
 * Backend viewport geometry vs JS engine parity.
 *
 * Runs the real server adapter (scripts/eval_profiles.py mode
 * `viewport_geometry` → solver.mesher_adapter.build_viewport_geometry),
 * tessellates the returned grids/rings with the browser tessellator, and
 * compares against the in-browser JS engine at the same sampling density.
 *
 * This is the deletion gate for src/geometry/engine/: the JS engine viewport
 * code must not be removed until these comparisons hold for OSSE and R-OSSE
 * in freestanding and enclosure modes, with and without morph targets.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { buildWaveguideMesh } from '../src/geometry/engine/buildWaveguideMesh.js';
import {
  buildMorphTargets,
  evaluateInnerProfileAt,
} from '../src/geometry/engine/mesh/horn.js';
import {
  applyMorphing,
  isMorphActive,
} from '../src/geometry/engine/morphing.js';
import { tessellateViewportGeometry } from '../src/geometry/viewportTessellator.js';
import { analyzeBemMeshIntegrity } from '../src/geometry/meshIntegrity.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const EVAL_SCRIPT = join(__dirname, '..', 'scripts', 'eval_profiles.py');

// Horn grid vertices travel through 6-decimal backend rounding; everything
// else is identical math, so this stays tight.
const GRID_TOL = 1e-4;
// Box-derived extents (enclosure planes, wall offsets) share formulas but not
// float-op order between the two pipelines.
const BBOX_TOL = 1e-3;

function callPython(payload) {
  const input = JSON.stringify(payload);
  const result = execFileSync('python3', [EVAL_SCRIPT], {
    input,
    encoding: 'utf-8',
    timeout: 60_000,
    maxBuffer: 64 * 1024 * 1024,
  });
  return JSON.parse(result);
}

function isBackendGeometryAvailable() {
  try {
    callPython({
      mode: 'viewport_geometry',
      payload: { formula_type: 'OSSE', n_angular: 8, n_length: 4 },
    });
    return true;
  } catch {
    return false;
  }
}

const hasBackend = isBackendGeometryAvailable();

// -------------------------------------------------------------------------
// Fixtures: one object carries both naming families — snake_case for the
// backend payload, camelCase for the JS engine (same style as
// geometry-parity.test.js fixtures).
// -------------------------------------------------------------------------

const DENSITY = {
  n_angular: 64,
  n_length: 32,
  corner_segments: 4,
  angularSegments: 64,
  lengthSegments: 32,
  cornerSegments: 4,
};

const OSSE_BASE = {
  ...DENSITY,
  formula_type: 'OSSE',
  type: 'OSSE',
  L: 120, a: 45, a0: 15.5, r0: 12.7,
  s: 0.6, n: 4.158, q: 0.991, k: 7, h: 0, rot: 0,
  throatProfile: 1, throat_profile: 1,
  throatExtLength: 0, throat_ext_length: 0,
  throatExtAngle: 0, throat_ext_angle: 0,
  slotLength: 0, slot_length: 0,
  gcurveType: 0, gcurve_type: 0,
  morphTarget: 0, morph_target: 0,
  sourceShape: 2, source_shape: 2,
  wallThickness: 0, wall_thickness: 0,
  encDepth: 0, enc_depth: 0,
};

const ROSSE_BASE = {
  ...DENSITY,
  formula_type: 'R-OSSE',
  type: 'R-OSSE',
  R: 140, a: 45, a0: 15.5, r0: 12.7,
  k: 2, r: 0.4, b: 0.2, m: 0.85, q: 3.4, tmax: 1.0,
  morphTarget: 0, morph_target: 0,
  sourceShape: 2, source_shape: 2,
  wallThickness: 0, wall_thickness: 0,
  encDepth: 0, enc_depth: 0,
};

const RECT_MORPH = {
  morphTarget: 1, morph_target: 1,
  morphWidth: 320, morph_width: 320,
  morphHeight: 240, morph_height: 240,
  morphCorner: 30, morph_corner: 30,
  morphRate: 3, morph_rate: 3,
  morphFixed: 0.2, morph_fixed: 0.2,
  morphAllowShrinkage: 0, morph_allow_shrinkage: 0,
};

const IMPLICIT_CIRCLE_MORPH = {
  morphTarget: 2, morph_target: 2,
  morphWidth: 0, morph_width: 0,
  morphHeight: 0, morph_height: 0,
  morphRate: 2, morph_rate: 2,
  morphFixed: 0, morph_fixed: 0,
  morphAllowShrinkage: 0, morph_allow_shrinkage: 0,
};

const ENCLOSURE = {
  encDepth: 220, enc_depth: 220,
  encSpaceL: 25, enc_space_l: 25,
  encSpaceT: 30, enc_space_t: 30,
  encSpaceR: 25, enc_space_r: 25,
  encSpaceB: 40, enc_space_b: 40,
  encEdge: 18, enc_edge: 18,
  encEdgeType: 1, enc_edge_type: 1,
};

const CASES = [
  { name: 'OSSE bare', config: OSSE_BASE },
  { name: 'OSSE freestanding wall', config: { ...OSSE_BASE, wallThickness: 6, wall_thickness: 6 } },
  { name: 'OSSE enclosure', config: { ...OSSE_BASE, ...ENCLOSURE } },
  { name: 'OSSE rect morph', config: { ...OSSE_BASE, ...RECT_MORPH } },
  { name: 'OSSE implicit circle morph', config: { ...OSSE_BASE, ...IMPLICIT_CIRCLE_MORPH } },
  { name: 'OSSE enclosure with rect morph', config: { ...OSSE_BASE, ...ENCLOSURE, ...RECT_MORPH } },
  { name: 'OSSE spherical source cap', config: { ...OSSE_BASE, sourceShape: 1, source_shape: 1 } },
  { name: 'R-OSSE bare', config: ROSSE_BASE },
  { name: 'R-OSSE rect morph', config: { ...ROSSE_BASE, ...RECT_MORPH } },
];

function buildBackendMesh(config) {
  const geometry = callPython({ mode: 'viewport_geometry', payload: config });
  return { geometry, mesh: tessellateViewportGeometry(geometry) };
}

function buildEngineMesh(config) {
  return buildWaveguideMesh(config, {
    includeEnclosure: Number(config.encDepth || 0) > 0,
    collectGroups: true,
  });
}

function boundingBox(vertices) {
  const box = {
    minX: Infinity, minY: Infinity, minZ: Infinity,
    maxX: -Infinity, maxY: -Infinity, maxZ: -Infinity,
  };
  for (let v = 0; v < vertices.length; v += 3) {
    box.minX = Math.min(box.minX, vertices[v]);
    box.maxX = Math.max(box.maxX, vertices[v]);
    box.minY = Math.min(box.minY, vertices[v + 1]);
    box.maxY = Math.max(box.maxY, vertices[v + 1]);
    box.minZ = Math.min(box.minZ, vertices[v + 2]);
    box.maxZ = Math.max(box.maxZ, vertices[v + 2]);
  }
  return box;
}

function assertBoxesClose(actual, expected, tolerance, label) {
  for (const key of Object.keys(expected)) {
    assert.ok(
      Math.abs(actual[key] - expected[key]) <= tolerance,
      `${label} ${key}: backend ${actual[key]} vs engine ${expected[key]} (tol ${tolerance})`
    );
  }
}

/**
 * Evaluate the JS engine profile + morph math at the backend grid's own
 * (slice, angle) sample points and compare each grid point. This checks the
 * morph-target contract (commit 6310fa0) at the canonical mesher's own sample
 * points. Current ATH and both implementations redistribute the same fixed
 * angular budget for explicit rounded-rectangle morphs.
 */
function assertEngineMatchesBackendGrid(config, geometry) {
  const grid = geometry.grid;
  const nPhi = grid.grid_n_phi;
  const nLength = grid.grid_n_length;
  const angles = grid.angle_list;
  assert.equal(angles.length, nPhi, 'angle list length');

  // The mesher snaps the morph onset to the nearest grid slice at or after
  // morphFixed so the morph begins exactly on a ring; mirror that here.
  const sliceMap = grid.slice_map || [];
  const configuredStart = Number(config.morphFixed || 0);
  const snappedStart =
    sliceMap.find((t) => t >= configuredStart - 1e-12) ?? sliceMap[sliceMap.length - 1] ?? 0;
  const evalConfig = { ...config, morphFixed: snappedStart };

  const context = { coverageCache: new Map() };
  // The engine resolves morph-target half-dimensions (explicit/implicit
  // derivation + no-shrinkage dimension floor) for every active morph and
  // threads them through applyMorphing; mirror that here so the grid check
  // sees the same blend the engine produces.
  const morphTargets = isMorphActive(evalConfig, 0)
    ? buildMorphTargets(evalConfig, nLength, angles, null, context)
    : null;

  for (let j = 0; j <= nLength; j += 1) {
    const t = j / nLength;
    for (let i = 0; i < nPhi; i += 1) {
      const p = angles[i];
      const profile = evaluateInnerProfileAt(t, p, evalConfig, context);
      const mouthProfile = j === nLength ? profile : evaluateInnerProfileAt(1, p, evalConfig, context);
      const r = applyMorphing(profile.y, mouthProfile.y, t, p, evalConfig, morphTargets?.[j] || null);

      const base = (i * (nLength + 1) + j) * 3;
      const label = `grid point i=${i}, j=${j}`;
      assert.ok(
        Math.abs(grid.inner_points[base] - r * Math.cos(p)) <= GRID_TOL,
        `${label} x: backend ${grid.inner_points[base]} vs engine ${r * Math.cos(p)}`
      );
      assert.ok(
        Math.abs(grid.inner_points[base + 1] - r * Math.sin(p)) <= GRID_TOL,
        `${label} y: backend ${grid.inner_points[base + 1]} vs engine ${r * Math.sin(p)}`
      );
      assert.ok(
        Math.abs(grid.inner_points[base + 2] - profile.x) <= GRID_TOL,
        `${label} axial: backend ${grid.inner_points[base + 2]} vs engine ${profile.x}`
      );
    }
  }
}

for (const { name, config } of CASES) {
  test(`backend viewport matches JS engine: ${name}`, { skip: !hasBackend && 'Python backend not available' }, () => {
    const { geometry, mesh: backendMesh } = buildBackendMesh(config);
    const engineMesh = buildEngineMesh(config);

    const nPhi = geometry.grid.grid_n_phi;
    const nLength = geometry.grid.grid_n_length;
    assert.equal(nLength, config.lengthSegments, 'length segments');
    assert.ok(nPhi >= config.angularSegments, 'angular density at least the requested segments');

    // Morph + profile contract at the backend's own sample points.
    assertEngineMatchesBackendGrid(config, geometry);

    // When both pipelines use the same angle list, the horn grids must be
    // vertex-for-vertex equal.
    const sameAngleList = nPhi === engineMesh.ringCount;
    if (sameAngleList) {
      const hornVertexCount = nPhi * (nLength + 1) * 3;
      for (let v = 0; v < hornVertexCount; v += 1) {
        const diff = Math.abs(backendMesh.vertices[v] - engineMesh.vertices[v]);
        assert.ok(
          diff <= GRID_TOL,
          `horn vertex component ${v}: backend ${backendMesh.vertices[v]} vs engine ${engineMesh.vertices[v]}`
        );
      }
    }

    // Display groups contract.
    assert.ok(backendMesh.groups.horn, 'horn group');
    assert.ok(backendMesh.groups.throat_disc, 'throat_disc group');
    if (Number(config.encDepth || 0) > 0) {
      assert.ok(backendMesh.groups.enclosure, 'enclosure group');
      assert.ok(engineMesh.groups.enclosure, 'engine enclosure group');
    } else if (Number(config.wallThickness || 0) > 0) {
      assert.ok(backendMesh.groups.freestandingWall, 'freestandingWall group');
    }

    // Whole-model envelope: enclosure boxes and wall offsets derive from the
    // same formulas, so the outer extents must line up even though the two
    // pipelines triangulate the enclosure differently. Differing angle lists
    // sample mouth corners at slightly different azimuths, so those cases get
    // a display-level tolerance.
    assertBoxesClose(
      boundingBox(backendMesh.vertices),
      boundingBox(engineMesh.vertices),
      sameAngleList ? BBOX_TOL : 0.5,
      'bounding box'
    );

    // Throat source cap: same center height (flat disc or spherical cap).
    const backendCenterY = backendMesh.vertices[backendMesh.vertices.length - 2];
    assert.ok(engineMesh.groups.source, 'engine source group');
    const engineCenterY = engineMesh.vertices[engineMesh.vertices.length - 2];
    assert.ok(
      Math.abs(backendCenterY - engineCenterY) <= GRID_TOL,
      `throat cap center: backend ${backendCenterY} vs engine ${engineCenterY}`
    );

    // The tessellated backend mesh must be consistently oriented, and — when
    // a wall or enclosure closes the mouth — fully watertight. Bare horns are
    // open at the mouth ring by design.
    const closed = Number(config.encDepth || 0) > 0 || Number(config.wallThickness || 0) > 0;
    const report = analyzeBemMeshIntegrity(backendMesh.vertices, backendMesh.indices, {
      requireClosed: closed,
      requireSingleComponent: true,
    });
    assert.deepEqual(report.errors, [], `mesh integrity (${name})`);
  });
}
