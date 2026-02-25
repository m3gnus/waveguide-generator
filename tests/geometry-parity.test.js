/**
 * Cross-pipeline geometry parity tests.
 *
 * Verifies that the JS viewport profile functions produce identical results
 * to the Python OCC builder profile functions at the same (t, phi) points.
 *
 * Uses scripts/eval_profiles.py as a CLI bridge to evaluate the Python side.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { calculateOSSE } from '../src/geometry/engine/profiles/osse.js';
import { calculateROSSE } from '../src/geometry/engine/profiles/rosse.js';
import { getGuidingCurveRadius } from '../src/geometry/engine/profiles/guidingCurve.js';
import { getRoundedRectRadius, applyMorphing } from '../src/geometry/engine/morphing.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const EVAL_SCRIPT = join(__dirname, '..', 'scripts', 'eval_profiles.py');

// Tolerance for composed profile outputs (multi-step float path differences)
const PROFILE_TOL = 1e-6;
// Tolerance for individual function outputs (single-step float math)
const FUNC_TOL = 1e-9;

// Standard (t, phi) grid for profile comparison
const T_VALUES = [0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0];
const PHI_VALUES = [0, Math.PI / 4, Math.PI / 2, Math.PI, 1.5 * Math.PI];

// -------------------------------------------------------------------------
// Helper: call Python CLI bridge
// -------------------------------------------------------------------------

function callPython(payload) {
  const input = JSON.stringify(payload);
  const result = execFileSync('python3', [EVAL_SCRIPT], {
    input,
    encoding: 'utf-8',
    timeout: 30_000,
    maxBuffer: 4 * 1024 * 1024,
  });
  return JSON.parse(result);
}

function isPythonAvailable() {
  try {
    execFileSync('python3', ['-c', 'import server.solver.waveguide_builder'], {
      cwd: join(__dirname, '..'),
      encoding: 'utf-8',
      timeout: 10_000,
    });
    return true;
  } catch {
    return false;
  }
}

// -------------------------------------------------------------------------
// Reference parameter sets
// -------------------------------------------------------------------------

const OSSE_BASIC = {
  formula_type: 'OSSE',
  type: 'OSSE',
  L: 120, a: 45, a0: 15.5, r0: 12.7,
  s: 0.6, n: 4.158, q: 0.991, k: 7, h: 0,
  throatExtLength: 0, throatExtAngle: 0, slotLength: 0,
  throatProfile: 1, rot: 0, gcurveType: 0,
  morphTarget: 0,
  // Python snake_case aliases
  throat_ext_length: 0, throat_ext_angle: 0, slot_length: 0,
  throat_profile: 1, gcurve_type: 0, morph_target: 0,
};

const ROSSE_BASIC = {
  formula_type: 'R-OSSE',
  type: 'R-OSSE',
  R: 140, a: 45, a0: 15.5, r0: 12.7,
  k: 2, r: 0.4, b: 0.2, m: 0.85, q: 3.4, tmax: 1.0,
};

const OSSE_WITH_EXT = {
  ...OSSE_BASIC,
  throatExtLength: 10, throatExtAngle: 5, slotLength: 5,
  throat_ext_length: 10, throat_ext_angle: 5, slot_length: 5,
};

const OSSE_WITH_ROT = {
  ...OSSE_BASIC,
  rot: 15,
};

const OSSE_WITH_H = {
  ...OSSE_BASIC,
  h: 3,
};

// -------------------------------------------------------------------------
// JS-only unit tests (no Python dependency)
// -------------------------------------------------------------------------

test('OSSE profile returns finite values at all grid points', () => {
  for (const t of T_VALUES) {
    for (const phi of PHI_VALUES) {
      const z = t * OSSE_BASIC.L;
      const { x, y } = calculateOSSE(z, phi, OSSE_BASIC);
      assert.ok(Number.isFinite(x), `x not finite at t=${t}, phi=${phi}`);
      assert.ok(Number.isFinite(y), `y not finite at t=${t}, phi=${phi}`);
      assert.ok(y > 0, `radius should be positive at t=${t}, phi=${phi}`);
    }
  }
});

test('R-OSSE profile returns finite values at all grid points', () => {
  for (const t of T_VALUES) {
    for (const phi of PHI_VALUES) {
      const { x, y } = calculateROSSE(t, phi, ROSSE_BASIC);
      assert.ok(Number.isFinite(x), `x not finite at t=${t}, phi=${phi}`);
      assert.ok(Number.isFinite(y), `y not finite at t=${t}, phi=${phi}`);
      assert.ok(y > 0, `radius should be positive at t=${t}, phi=${phi}`);
    }
  }
});

test('R-OSSE length calculation handles near-zero coverage angle', () => {
  // This tests the fixed safeDiv edge case: c3 ≈ 0, c2 ≠ 0
  const params = { ...ROSSE_BASIC, a: 0.001 };
  const { x, y } = calculateROSSE(0.5, 0, params);
  assert.ok(Number.isFinite(x), 'x should be finite for near-zero angle');
  assert.ok(Number.isFinite(y), 'y should be finite for near-zero angle');
  assert.ok(y > 0, 'radius should be positive for near-zero angle');
});

test('rounded rect radius matches known values', () => {
  const halfW = 100, halfH = 60, cornerR = 10;

  // At phi=0 (pure X axis), should return halfW
  assert.ok(Math.abs(getRoundedRectRadius(0, halfW, halfH, cornerR) - halfW) < FUNC_TOL);
  // At phi=pi/2 (pure Y axis), should return halfH
  assert.ok(Math.abs(getRoundedRectRadius(Math.PI / 2, halfW, halfH, cornerR) - halfH) < FUNC_TOL);
});

test('OSSE with throat extension has correct radius at extension zone', () => {
  const params = OSSE_WITH_EXT;
  const z = 5; // midway through extension
  const { y } = calculateOSSE(z, 0, params);
  const expected = params.r0 + z * Math.tan(params.throatExtAngle * Math.PI / 180);
  assert.ok(Math.abs(y - expected) < FUNC_TOL, `extension zone radius: got ${y}, expected ${expected}`);
});

test('OSSE with rotation rotates the profile', () => {
  const noRot = calculateOSSE(60, 0, OSSE_BASIC);
  const withRot = calculateOSSE(60, 0, OSSE_WITH_ROT);
  // With rotation, the axial and radial coordinates should differ
  assert.ok(Math.abs(noRot.x - withRot.x) > 0.1, 'rotation should change x');
  assert.ok(Math.abs(noRot.y - withRot.y) > 0.01, 'rotation should change y');
});

// -------------------------------------------------------------------------
// Cross-pipeline profile comparison (requires Python)
// -------------------------------------------------------------------------

const hasPython = isPythonAvailable();

test('OSSE profiles match between JS and Python', { skip: !hasPython && 'Python not available' }, () => {
  const pyResults = callPython({
    mode: 'profiles',
    config: OSSE_BASIC,
    t_values: T_VALUES,
    phi_values: PHI_VALUES,
  });

  let idx = 0;
  for (const phi of PHI_VALUES) {
    for (const t of T_VALUES) {
      const z = t * OSSE_BASIC.L;
      const js = calculateOSSE(z, phi, OSSE_BASIC);
      const py = pyResults[idx];

      assert.ok(
        Math.abs(js.x - py.x) < PROFILE_TOL,
        `OSSE x mismatch at t=${t}, phi=${phi}: JS=${js.x}, PY=${py.x}, diff=${Math.abs(js.x - py.x)}`
      );
      assert.ok(
        Math.abs(js.y - py.y) < PROFILE_TOL,
        `OSSE y mismatch at t=${t}, phi=${phi}: JS=${js.y}, PY=${py.y}, diff=${Math.abs(js.y - py.y)}`
      );
      idx++;
    }
  }
});

test('R-OSSE profiles match between JS and Python', { skip: !hasPython && 'Python not available' }, () => {
  const pyResults = callPython({
    mode: 'profiles',
    config: ROSSE_BASIC,
    t_values: T_VALUES,
    phi_values: PHI_VALUES,
  });

  let idx = 0;
  for (const phi of PHI_VALUES) {
    for (const t of T_VALUES) {
      const js = calculateROSSE(t, phi, ROSSE_BASIC);
      const py = pyResults[idx];

      assert.ok(
        Math.abs(js.x - py.x) < PROFILE_TOL,
        `R-OSSE x mismatch at t=${t}, phi=${phi}: JS=${js.x}, PY=${py.x}, diff=${Math.abs(js.x - py.x)}`
      );
      assert.ok(
        Math.abs(js.y - py.y) < PROFILE_TOL,
        `R-OSSE y mismatch at t=${t}, phi=${phi}: JS=${js.y}, PY=${py.y}, diff=${Math.abs(js.y - py.y)}`
      );
      idx++;
    }
  }
});

test('OSSE with throat extension matches Python', { skip: !hasPython && 'Python not available' }, () => {
  const pyResults = callPython({
    mode: 'profiles',
    config: OSSE_WITH_EXT,
    t_values: T_VALUES,
    phi_values: PHI_VALUES,
  });

  const totalLength = OSSE_WITH_EXT.L + OSSE_WITH_EXT.throatExtLength + OSSE_WITH_EXT.slotLength;

  let idx = 0;
  for (const phi of PHI_VALUES) {
    for (const t of T_VALUES) {
      const z = t * totalLength;
      const js = calculateOSSE(z, phi, OSSE_WITH_EXT);
      const py = pyResults[idx];

      assert.ok(
        Math.abs(js.x - py.x) < PROFILE_TOL,
        `OSSE+ext x mismatch at t=${t}, phi=${phi}: JS=${js.x}, PY=${py.x}`
      );
      assert.ok(
        Math.abs(js.y - py.y) < PROFILE_TOL,
        `OSSE+ext y mismatch at t=${t}, phi=${phi}: JS=${js.y}, PY=${py.y}`
      );
      idx++;
    }
  }
});

test('OSSE with rotation matches Python', { skip: !hasPython && 'Python not available' }, () => {
  const pyResults = callPython({
    mode: 'profiles',
    config: OSSE_WITH_ROT,
    t_values: T_VALUES,
    phi_values: PHI_VALUES,
  });

  let idx = 0;
  for (const phi of PHI_VALUES) {
    for (const t of T_VALUES) {
      const z = t * OSSE_WITH_ROT.L;
      const js = calculateOSSE(z, phi, OSSE_WITH_ROT);
      const py = pyResults[idx];

      assert.ok(
        Math.abs(js.x - py.x) < PROFILE_TOL,
        `OSSE+rot x mismatch at t=${t}, phi=${phi}: JS=${js.x}, PY=${py.x}`
      );
      assert.ok(
        Math.abs(js.y - py.y) < PROFILE_TOL,
        `OSSE+rot y mismatch at t=${t}, phi=${phi}: JS=${js.y}, PY=${py.y}`
      );
      idx++;
    }
  }
});

test('OSSE with h-bulge matches Python', { skip: !hasPython && 'Python not available' }, () => {
  const pyResults = callPython({
    mode: 'profiles',
    config: OSSE_WITH_H,
    t_values: T_VALUES,
    phi_values: PHI_VALUES,
  });

  let idx = 0;
  for (const phi of PHI_VALUES) {
    for (const t of T_VALUES) {
      const z = t * OSSE_WITH_H.L;
      const js = calculateOSSE(z, phi, OSSE_WITH_H);
      // JS doesn't apply h-bulge inside calculateOSSE — it's applied externally.
      // Add it manually for comparison.
      const jsY = js.y + OSSE_WITH_H.h * Math.sin(t * Math.PI);
      const py = pyResults[idx];

      assert.ok(
        Math.abs(js.x - py.x) < PROFILE_TOL,
        `OSSE+h x mismatch at t=${t}, phi=${phi}: JS=${js.x}, PY=${py.x}`
      );
      assert.ok(
        Math.abs(jsY - py.y) < PROFILE_TOL,
        `OSSE+h y mismatch at t=${t}, phi=${phi}: JS=${jsY}, PY=${py.y}`
      );
      idx++;
    }
  }
});

test('R-OSSE near-zero angle matches Python', { skip: !hasPython && 'Python not available' }, () => {
  // Tests the fixed safeDiv edge case
  const config = { ...ROSSE_BASIC, a: 0.001 };
  const tValues = [0, 0.25, 0.5, 0.75, 1.0];

  const pyResults = callPython({
    mode: 'profiles',
    config,
    t_values: tValues,
    phi_values: [0],
  });

  for (let j = 0; j < tValues.length; j++) {
    const t = tValues[j];
    const js = calculateROSSE(t, 0, config);
    const py = pyResults[j];

    assert.ok(
      Math.abs(js.x - py.x) < PROFILE_TOL,
      `R-OSSE edge x mismatch at t=${t}: JS=${js.x}, PY=${py.x}`
    );
    assert.ok(
      Math.abs(js.y - py.y) < PROFILE_TOL,
      `R-OSSE edge y mismatch at t=${t}: JS=${js.y}, PY=${py.y}`
    );
  }
});

test('rounded rect radius matches Python', { skip: !hasPython && 'Python not available' }, () => {
  const phiValues = [0, Math.PI / 6, Math.PI / 4, Math.PI / 3, Math.PI / 2];
  const halfW = 100, halfH = 60, cornerR = 10;

  const pyResults = callPython({
    mode: 'functions',
    config: {
      rounded_rect: { half_w: halfW, half_h: halfH, corner_r: cornerR, phi_values: phiValues },
    },
  });

  for (let i = 0; i < phiValues.length; i++) {
    const jsR = getRoundedRectRadius(phiValues[i], halfW, halfH, cornerR);
    const pyR = pyResults.rounded_rect[i].r;
    assert.ok(
      Math.abs(jsR - pyR) < FUNC_TOL,
      `rounded rect mismatch at phi=${phiValues[i]}: JS=${jsR}, PY=${pyR}`
    );
  }
});

test('guiding curve superellipse matches Python', { skip: !hasPython && 'Python not available' }, () => {
  const phiValues = [0, Math.PI / 4, Math.PI / 2, Math.PI, 3 * Math.PI / 2];
  const gcParams = {
    gcurve_type: 1,
    gcurve_width: 300,
    gcurve_aspect_ratio: 0.8,
    gcurve_se_n: 3,
    gcurve_rot: 0,
  };
  // JS params use camelCase
  const jsParams = {
    gcurveType: 1,
    gcurveWidth: 300,
    gcurveAspectRatio: 0.8,
    gcurveSeN: 3,
    gcurveRot: 0,
  };

  const pyResults = callPython({
    mode: 'functions',
    config: {
      guiding_curve: { params: gcParams, phi_values: phiValues },
    },
  });

  for (let i = 0; i < phiValues.length; i++) {
    const jsR = getGuidingCurveRadius(phiValues[i], jsParams);
    const pyR = pyResults.guiding_curve[i].r;
    assert.ok(
      Math.abs(jsR - pyR) < FUNC_TOL,
      `guiding curve SE mismatch at phi=${phiValues[i]}: JS=${jsR}, PY=${pyR}`
    );
  }
});
