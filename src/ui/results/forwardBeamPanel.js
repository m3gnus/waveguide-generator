/**
 * Front-facing directivity map for a loudspeaker aimed at the viewer.
 *
 * The forward hemisphere is shown with an azimuthal-equidistant projection:
 * distance from the centre is off-axis angle and direction around the centre
 * is observation azimuth. Unlike the old superellipse summary, the heatmap is
 * drawn from the solved balloon grid and therefore keeps asymmetry, shoulders,
 * and secondary lobes. Contour lines are direct first crossings, not a fit.
 */

import { hasBalloonData } from './balloonPanel.js';

const MAP_RANGE_DB = 30;
const CONTOUR_LEVELS_DB = [-6, -12, -18];
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

function colorForDb(db) {
  const normalized = Math.max(0, Math.min(1, 1 + Number(db) / MAP_RANGE_DB));
  const position = normalized * (COLOR_STOPS.length - 1);
  const index = Math.min(Math.floor(position), COLOR_STOPS.length - 2);
  const fraction = position - index;
  const a = COLOR_STOPS[index];
  const b = COLOR_STOPS[index + 1];
  return a.map((value, channel) => Math.round(255 * (value + (b[channel] - value) * fraction)));
}

function formatFrequencyHz(value) {
  const frequency = Number(value);
  if (!Number.isFinite(frequency)) return '—';
  if (frequency >= 1000) {
    const kilo = frequency / 1000;
    return `${kilo >= 10 ? kilo.toFixed(1) : kilo.toFixed(2)} kHz`;
  }
  return `${Math.round(frequency)} Hz`;
}

function wrappedPhiInterval(phiDeg, queryDeg) {
  const first = Number(phiDeg[0]);
  const query = ((((Number(queryDeg) - first) % 360) + 360) % 360) + first;
  let lower = phiDeg.length - 1;
  for (let index = 0; index < phiDeg.length - 1; index += 1) {
    if (query >= Number(phiDeg[index]) && query < Number(phiDeg[index + 1])) {
      lower = index;
      break;
    }
  }
  const upper = (lower + 1) % phiDeg.length;
  const lowerPhi = Number(phiDeg[lower]);
  const upperPhi = upper === 0 ? first + 360 : Number(phiDeg[upper]);
  const weight = upperPhi > lowerPhi ? (query - lowerPhi) / (upperPhi - lowerPhi) : 0;
  return { lower, upper, weight: Math.max(0, Math.min(1, weight)) };
}

/** Bilinear sample of one frequency's regular theta/phi balloon grid. */
export function sampleBalloonGrid(thetaDeg, phiDeg, grid, thetaQuery, phiQuery) {
  if (!Array.isArray(thetaDeg) || thetaDeg.length < 2 || !Array.isArray(phiDeg)) return NaN;
  const theta = Math.max(
    Number(thetaDeg[0]),
    Math.min(Number(thetaQuery), Number(thetaDeg.at(-1)))
  );
  let lowerTheta = thetaDeg.length - 2;
  for (let index = 0; index < thetaDeg.length - 1; index += 1) {
    if (theta >= Number(thetaDeg[index]) && theta <= Number(thetaDeg[index + 1])) {
      lowerTheta = index;
      break;
    }
  }
  const upperTheta = lowerTheta + 1;
  const thetaSpan = Number(thetaDeg[upperTheta]) - Number(thetaDeg[lowerTheta]);
  const thetaWeight = thetaSpan > 0 ? (theta - Number(thetaDeg[lowerTheta])) / thetaSpan : 0;
  const phi = wrappedPhiInterval(phiDeg, phiQuery);
  const value = (row, column) => Number(grid?.[row]?.[column]);
  const a = value(lowerTheta, phi.lower);
  const b = value(lowerTheta, phi.upper);
  const c = value(upperTheta, phi.lower);
  const d = value(upperTheta, phi.upper);
  if (![a, b, c, d].every(Number.isFinite)) return NaN;
  const lower = a + (b - a) * phi.weight;
  const upper = c + (d - c) * phi.weight;
  return lower + (upper - lower) * thetaWeight;
}

export function firstLevelCrossing(thetaDeg, values, levelDb, thetaLimit = 90) {
  const count = Math.min(thetaDeg?.length || 0, values?.length || 0);
  for (let index = 1; index < count; index += 1) {
    const theta = Number(thetaDeg[index]);
    if (theta > thetaLimit) break;
    const before = Number(values[index - 1]);
    const after = Number(values[index]);
    if (!Number.isFinite(before) || !Number.isFinite(after)) continue;
    if (before > levelDb && after <= levelDb) {
      const span = before - after;
      const fraction = span > 0 ? (before - levelDb) / span : 0;
      return Number(thetaDeg[index - 1]) + (theta - Number(thetaDeg[index - 1])) * fraction;
    }
  }
  return null;
}

function drawContours(context, balloon, grid, centerX, centerY, radius, thetaLimit) {
  const styles = ['rgba(255,255,255,0.96)', 'rgba(255,255,255,0.68)', 'rgba(255,255,255,0.42)'];
  CONTOUR_LEVELS_DB.forEach((level, levelIndex) => {
    const points = [];
    balloon.phi_deg.forEach((phi, column) => {
      const values = grid.map((row) => row?.[column]);
      const theta = firstLevelCrossing(balloon.theta_deg, values, level, thetaLimit);
      if (theta === null) return;
      const radians = (Number(phi) * Math.PI) / 180;
      const distance = (theta / thetaLimit) * radius;
      points.push([centerX + Math.cos(radians) * distance, centerY - Math.sin(radians) * distance]);
    });
    if (points.length < 3) return;
    context.beginPath();
    points.forEach(([x, y], index) => (index === 0 ? context.moveTo(x, y) : context.lineTo(x, y)));
    context.closePath();
    context.strokeStyle = styles[levelIndex];
    context.lineWidth = levelIndex === 0 ? 2 : 1;
    context.setLineDash(levelIndex === 0 ? [] : [4, 4]);
    context.stroke();
  });
  context.setLineDash([]);
}

function drawMap(view) {
  const { canvas, balloon, frequencyIndex } = view;
  const context = canvas.getContext('2d');
  if (!context) return;
  const bounds = canvas.getBoundingClientRect();
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(2, Math.round(bounds.width * pixelRatio));
  const height = Math.max(2, Math.round(bounds.height * pixelRatio));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  const cssWidth = width / pixelRatio;
  const cssHeight = height / pixelRatio;
  context.clearRect(0, 0, cssWidth, cssHeight);

  const radius = Math.max(10, Math.min(cssWidth, cssHeight) * 0.41);
  const centerX = cssWidth / 2;
  const centerY = cssHeight / 2;
  const thetaLimit = Math.min(90, Number(balloon.theta_deg.at(-1)));
  const grid = balloon.spl_norm_db[frequencyIndex];

  const rasterSize = Math.max(180, Math.min(520, Math.round(radius * 2 * pixelRatio)));
  const raster = document.createElement('canvas');
  raster.width = rasterSize;
  raster.height = rasterSize;
  const rasterContext = raster.getContext('2d');
  const image = rasterContext.createImageData(rasterSize, rasterSize);
  const rasterRadius = rasterSize / 2;
  for (let y = 0; y < rasterSize; y += 1) {
    for (let x = 0; x < rasterSize; x += 1) {
      const dx = (x + 0.5 - rasterRadius) / rasterRadius;
      const dy = (rasterRadius - y - 0.5) / rasterRadius;
      const radial = Math.hypot(dx, dy);
      const offset = (y * rasterSize + x) * 4;
      if (radial > 1) {
        image.data[offset + 3] = 0;
        continue;
      }
      const theta = radial * thetaLimit;
      const phi = ((Math.atan2(dy, dx) * 180) / Math.PI + 360) % 360;
      const db = sampleBalloonGrid(balloon.theta_deg, balloon.phi_deg, grid, theta, phi);
      const [red, green, blue] = colorForDb(Number.isFinite(db) ? db : -MAP_RANGE_DB);
      image.data[offset] = red;
      image.data[offset + 1] = green;
      image.data[offset + 2] = blue;
      image.data[offset + 3] = 255;
    }
  }
  rasterContext.putImageData(image, 0, 0);
  context.drawImage(raster, centerX - radius, centerY - radius, radius * 2, radius * 2);

  context.strokeStyle = 'rgba(210,220,230,0.32)';
  context.fillStyle = 'rgba(210,220,230,0.72)';
  context.lineWidth = 1;
  context.font = '11px system-ui, sans-serif';
  context.textAlign = 'left';
  for (let angle = 30; angle <= thetaLimit; angle += 30) {
    const ringRadius = (angle / thetaLimit) * radius;
    context.beginPath();
    context.arc(centerX, centerY, ringRadius, 0, Math.PI * 2);
    context.stroke();
    context.fillText(`${angle}\u00b0`, centerX + 4, centerY - ringRadius + 12);
  }
  context.beginPath();
  context.moveTo(centerX - radius, centerY);
  context.lineTo(centerX + radius, centerY);
  context.moveTo(centerX, centerY - radius);
  context.lineTo(centerX, centerY + radius);
  context.stroke();
  drawContours(context, balloon, grid, centerX, centerY, radius, thetaLimit);

  context.font = '600 12px system-ui, sans-serif';
  context.fillStyle = '#e05c4f';
  context.textAlign = 'center';
  context.fillText('H−', centerX - radius - 18, centerY + 4);
  context.fillText('H+', centerX + radius + 18, centerY + 4);
  context.fillStyle = '#4f8fe0';
  context.fillText('V+', centerX, centerY - radius - 9);
  context.fillText('V−', centerX, centerY + radius + 18);
}

function updateReadout(view) {
  const frequency = view.balloon.frequencies?.[view.frequencyIndex];
  const beam = view.beamShape;
  const horizontal = beam?.horizontal_beamwidth_deg?.[view.frequencyIndex];
  const vertical = beam?.vertical_beamwidth_deg?.[view.frequencyIndex];
  const di = beam?.spherical_di_db?.[view.frequencyIndex];
  const parts = [formatFrequencyHz(frequency)];
  if (horizontal != null && vertical != null) {
    parts.push(`−6 dB ${Number(horizontal).toFixed(0)}° H × ${Number(vertical).toFixed(0)}° V`);
  }
  if (di != null) parts.push(`DI ${Number(di).toFixed(1)} dB`);
  view.readout.textContent = parts.join(' · ');
}

function closestFrequencyIndex(frequencies, target = 1000) {
  let bestIndex = 0;
  let bestDistance = Infinity;
  frequencies.forEach((value, index) => {
    const distance = Math.abs(Math.log((Number(value) || 1) / target));
    if (Number.isFinite(distance) && distance < bestDistance) {
      bestIndex = index;
      bestDistance = distance;
    }
  });
  return bestIndex;
}

function buildView(state, results) {
  const balloon = results.balloon;
  const container = document.createElement('div');
  container.className = 'forward-beam-panel';
  const canvas = document.createElement('canvas');
  canvas.className = 'forward-beam-panel-canvas';
  canvas.setAttribute('aria-label', 'Front-facing directivity map');
  container.appendChild(canvas);

  const controls = document.createElement('div');
  controls.className = 'balloon-panel-controls';
  const slider = document.createElement('input');
  slider.type = 'range';
  slider.min = '0';
  slider.max = String(balloon.spl_norm_db.length - 1);
  slider.step = '1';
  slider.setAttribute('aria-label', 'Forward beam frequency');
  const readout = document.createElement('span');
  readout.className = 'balloon-panel-readout';
  controls.append(slider, readout);
  container.appendChild(controls);
  state.body.appendChild(container);

  const view = {
    container,
    canvas,
    slider,
    readout,
    balloon,
    beamShape: results.beam_shape || null,
    frequencyIndex: closestFrequencyIndex((balloon.frequencies || []).map(Number)),
    resizeObserver: null,
  };
  slider.value = String(view.frequencyIndex);
  slider.addEventListener('input', () => {
    view.frequencyIndex = Number(slider.value) || 0;
    updateReadout(view);
    drawMap(view);
  });
  if (typeof ResizeObserver !== 'undefined') {
    view.resizeObserver = new ResizeObserver(() => drawMap(view));
    view.resizeObserver.observe(canvas);
  }
  updateReadout(view);
  drawMap(view);
  return view;
}

export function isForwardBeamChartKey(chartKey) {
  return chartKey === 'beam_map';
}

export function disposeForwardBeamPanel(state) {
  const view = state?.forwardBeamView;
  if (!view) return;
  state.forwardBeamView = null;
  view.resizeObserver?.disconnect();
  view.container?.remove();
}

export function renderForwardBeamPanel(state, results) {
  if (!hasBalloonData(results)) {
    disposeForwardBeamPanel(state);
    return false;
  }
  if (
    state.forwardBeamView?.balloon === results.balloon &&
    state.forwardBeamView.container?.isConnected
  ) {
    return true;
  }
  disposeForwardBeamPanel(state);
  state.body.textContent = '';
  state.forwardBeamView = buildView(state, results);
  return true;
}
