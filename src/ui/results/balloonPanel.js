/**
 * 3D balloon viewer for results-dock panels.
 *
 * Renders the solve's spherical balloon block (results.balloon: theta/phi
 * grid of normalized SPL per frequency) as a shaded 3D surface whose radius
 * and color follow SPL, with a frequency slider. One three.js renderer per
 * panel, created on demand and disposed when the panel switches away — the
 * dock rebuilds panels often and WebGL contexts are a limited resource.
 *
 * Balloon axes match the solver's observation frame: x = horizontal (phi=0),
 * y = vertical (phi=90), z = forward (theta=0). The camera starts on +z, so
 * the initial view is the front-facing bubble.
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// SPL span mapped onto the balloon radius/colors: 0 dB (on-axis) at full
// radius down to -30 dB collapsed to the origin.
const BALLOON_RANGE_DB = 30;
const HORIZONTAL_AXIS_COLOR = 0xe05c4f;
const VERTICAL_AXIS_COLOR = 0x4f8fe0;

// Compact viridis ramp; perceptual, readable on light and dark panels.
const COLOR_STOPS = [
  [0.267, 0.005, 0.329],
  [0.283, 0.141, 0.458],
  [0.254, 0.265, 0.53],
  [0.207, 0.372, 0.553],
  [0.164, 0.471, 0.558],
  [0.128, 0.567, 0.551],
  [0.135, 0.659, 0.518],
  [0.267, 0.749, 0.441],
  [0.478, 0.821, 0.318],
  [0.741, 0.873, 0.15],
  [0.993, 0.906, 0.144],
];

function colorForLevel(normalized) {
  const t = Math.max(0, Math.min(1, normalized)) * (COLOR_STOPS.length - 1);
  const index = Math.min(Math.floor(t), COLOR_STOPS.length - 2);
  const f = t - index;
  const a = COLOR_STOPS[index];
  const b = COLOR_STOPS[index + 1];
  return [
    a[0] + (b[0] - a[0]) * f,
    a[1] + (b[1] - a[1]) * f,
    a[2] + (b[2] - a[2]) * f,
  ];
}

function formatFrequencyHz(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  if (numeric >= 1000) {
    const kilo = numeric / 1000;
    return `${kilo >= 10 ? kilo.toFixed(1) : kilo.toFixed(2)} kHz`;
  }
  return `${Math.round(numeric)} Hz`;
}

export function isBalloonChartKey(chartKey) {
  return chartKey === 'balloon';
}

export function hasBalloonData(results) {
  const balloon = results?.balloon;
  return Boolean(
    balloon &&
      Array.isArray(balloon.theta_deg) &&
      balloon.theta_deg.length >= 2 &&
      Array.isArray(balloon.phi_deg) &&
      balloon.phi_deg.length >= 3 &&
      Array.isArray(balloon.spl_norm_db) &&
      balloon.spl_norm_db.length > 0
  );
}

function balloonDirections(thetaDeg, phiDeg) {
  // Wrapped phi column closes the seam; directions are (T * (P+1)) vec3s.
  const thetaRad = thetaDeg.map((value) => (value * Math.PI) / 180);
  const phiRad = [...phiDeg, phiDeg[0] + 360].map((value) => (value * Math.PI) / 180);
  const directions = new Float32Array(thetaRad.length * phiRad.length * 3);
  let cursor = 0;
  for (const theta of thetaRad) {
    const sinTheta = Math.sin(theta);
    const cosTheta = Math.cos(theta);
    for (const phi of phiRad) {
      directions[cursor] = sinTheta * Math.cos(phi);
      directions[cursor + 1] = sinTheta * Math.sin(phi);
      directions[cursor + 2] = cosTheta;
      cursor += 3;
    }
  }
  return { directions, rows: thetaRad.length, columns: phiRad.length };
}

function balloonIndices(rows, columns) {
  const indices = [];
  for (let row = 0; row < rows - 1; row += 1) {
    for (let column = 0; column < columns - 1; column += 1) {
      const a = row * columns + column;
      const b = a + 1;
      const c = a + columns;
      const d = c + 1;
      indices.push(a, c, b, b, c, d);
    }
  }
  return indices;
}

function applyFrequency(view, frequencyIndex) {
  const { balloon, geometry, rows, columns, directions } = view;
  const grid = balloon.spl_norm_db[frequencyIndex];
  if (!grid) return;

  const positions = geometry.getAttribute('position');
  const colors = geometry.getAttribute('color');
  let vertex = 0;
  for (let row = 0; row < rows; row += 1) {
    const splRow = grid[row] || [];
    for (let column = 0; column < columns; column += 1) {
      const sourceColumn = column === columns - 1 ? 0 : column;
      const spl = Number(splRow[sourceColumn]);
      const level = Number.isFinite(spl) ? spl : -BALLOON_RANGE_DB;
      const normalized = Math.max(0, Math.min(1, 1 + level / BALLOON_RANGE_DB));
      const base = vertex * 3;
      positions.array[base] = directions[base] * normalized;
      positions.array[base + 1] = directions[base + 1] * normalized;
      positions.array[base + 2] = directions[base + 2] * normalized;
      const [red, green, blue] = colorForLevel(normalized);
      colors.array[base] = red;
      colors.array[base + 1] = green;
      colors.array[base + 2] = blue;
      vertex += 1;
    }
  }
  positions.needsUpdate = true;
  colors.needsUpdate = true;
  geometry.computeVertexNormals();
  view.frequencyIndex = frequencyIndex;
}

function updateReadout(view) {
  const { balloon, beamShape, frequencyIndex, readout } = view;
  if (!readout) return;
  const frequency = balloon.frequencies?.[frequencyIndex];
  const parts = [formatFrequencyHz(frequency)];
  if (beamShape) {
    const p = beamShape.shape_exponent?.[frequencyIndex];
    const di = beamShape.spherical_di_db?.[frequencyIndex];
    if (p !== null && p !== undefined) parts.push(`p ${Number(p).toFixed(1)}`);
    if (di !== null && di !== undefined) parts.push(`DI ${Number(di).toFixed(1)} dB`);
  }
  readout.textContent = parts.join(' · ');
}

function axisGuides() {
  const group = new THREE.Group();
  const makeLine = (from, to, color) => {
    const geometry = new THREE.BufferGeometry().setFromPoints([from, to]);
    return new THREE.Line(
      geometry,
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.55 })
    );
  };
  group.add(
    makeLine(
      new THREE.Vector3(-1.25, 0, 0),
      new THREE.Vector3(1.25, 0, 0),
      HORIZONTAL_AXIS_COLOR
    )
  );
  group.add(
    makeLine(
      new THREE.Vector3(0, -1.25, 0),
      new THREE.Vector3(0, 1.25, 0),
      VERTICAL_AXIS_COLOR
    )
  );
  return group;
}

function renderOnce(view) {
  if (!view.renderer) return;
  view.renderer.render(view.scene, view.camera);
}

function buildView(state, results) {
  const balloon = results.balloon;
  const thetaDeg = balloon.theta_deg.map(Number);
  const phiDeg = balloon.phi_deg.map(Number);
  const { directions, rows, columns } = balloonDirections(thetaDeg, phiDeg);

  const container = document.createElement('div');
  container.className = 'balloon-panel';

  const canvasHost = document.createElement('div');
  canvasHost.className = 'balloon-panel-canvas';
  container.appendChild(canvasHost);

  const controls = document.createElement('div');
  controls.className = 'balloon-panel-controls';
  const slider = document.createElement('input');
  slider.type = 'range';
  slider.min = '0';
  slider.max = String(balloon.spl_norm_db.length - 1);
  slider.step = '1';
  slider.setAttribute('aria-label', 'Balloon frequency');
  const readout = document.createElement('span');
  readout.className = 'balloon-panel-readout';
  controls.appendChild(slider);
  controls.appendChild(readout);
  container.appendChild(controls);
  state.body.appendChild(container);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 50);
  camera.position.set(0, 0, 3.1);
  camera.lookAt(0, 0, 0);
  scene.add(new THREE.AmbientLight(0xffffff, 0.75));
  const keyLight = new THREE.DirectionalLight(0xffffff, 1.4);
  camera.add(keyLight);
  keyLight.position.set(0.6, 0.9, 1.2);
  scene.add(camera);
  scene.add(axisGuides());

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    'position',
    new THREE.BufferAttribute(new Float32Array(rows * columns * 3), 3)
  );
  geometry.setAttribute(
    'color',
    new THREE.BufferAttribute(new Float32Array(rows * columns * 3), 3)
  );
  geometry.setIndex(balloonIndices(rows, columns));
  const material = new THREE.MeshStandardMaterial({
    vertexColors: true,
    side: THREE.DoubleSide,
    roughness: 0.42,
    metalness: 0.0,
  });
  scene.add(new THREE.Mesh(geometry, material));

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  canvasHost.appendChild(renderer.domElement);

  const orbit = new OrbitControls(camera, renderer.domElement);
  orbit.enableDamping = false;
  orbit.enablePan = false;
  orbit.minDistance = 1.4;
  orbit.maxDistance = 8;

  const view = {
    container,
    canvasHost,
    slider,
    readout,
    scene,
    camera,
    renderer,
    orbit,
    geometry,
    material,
    directions,
    rows,
    columns,
    balloon,
    beamShape: results.beam_shape || null,
    frequencyIndex: 0,
    resizeObserver: null,
  };

  orbit.addEventListener('change', () => renderOnce(view));
  slider.addEventListener('input', () => {
    const index = Number(slider.value) || 0;
    applyFrequency(view, index);
    updateReadout(view);
    renderOnce(view);
  });

  const resize = () => {
    const width = canvasHost.clientWidth;
    const height = canvasHost.clientHeight;
    if (width < 2 || height < 2) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderOnce(view);
  };
  if (typeof ResizeObserver !== 'undefined') {
    view.resizeObserver = new ResizeObserver(resize);
    view.resizeObserver.observe(canvasHost);
  }

  // Start at the sample closest to 1 kHz — usually inside pattern control.
  const frequencies = (balloon.frequencies || []).map(Number);
  let startIndex = 0;
  let bestDistance = Infinity;
  frequencies.forEach((frequency, index) => {
    const distance = Math.abs(Math.log((frequency || 1) / 1000));
    if (Number.isFinite(distance) && distance < bestDistance) {
      bestDistance = distance;
      startIndex = index;
    }
  });
  slider.value = String(startIndex);
  applyFrequency(view, startIndex);
  updateReadout(view);
  resize();
  renderOnce(view);
  return view;
}

export function disposeBalloonPanel(state) {
  const view = state?.balloonView;
  if (!view) return;
  state.balloonView = null;
  try {
    view.resizeObserver?.disconnect();
    view.orbit?.dispose();
    view.geometry?.dispose();
    view.material?.dispose();
    view.scene?.traverse?.((object) => {
      if (object.geometry && object.geometry !== view.geometry) {
        object.geometry.dispose();
      }
      if (object.material && object.material !== view.material) {
        object.material.dispose?.();
      }
    });
    view.renderer?.dispose();
    view.container?.remove();
  } catch {
    // Disposal is best-effort; a lost WebGL context can throw mid-teardown.
  }
}

/**
 * Render (or refresh) the balloon panel. Returns true when a viewer is
 * showing, false when the results carry no balloon data (caller shows the
 * status message).
 */
export function renderBalloonPanel(state, results) {
  if (!hasBalloonData(results)) {
    disposeBalloonPanel(state);
    return false;
  }

  // Same results object and still in the DOM: keep the live viewer
  // (camera + slider positions survive dock refreshes).
  if (
    state.balloonView &&
    state.balloonView.balloon === results.balloon &&
    state.balloonView.container?.isConnected
  ) {
    return true;
  }

  disposeBalloonPanel(state);
  state.body.textContent = '';
  state.balloonView = buildView(state, results);
  return true;
}
