import { getViewportStateCacheKey } from '../app/viewportCacheKey.js';
import { AppEvents } from '../events.js';
import { GlobalState } from '../state.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const INSET_SIZE = 240;
const INSET_PADDING = 24;

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function finite(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function normalizedQuadrants(value) {
  const match = /^[+-]?\d+/.exec(String(value ?? '1234').trim());
  const numeric = match ? Number(match[0]) : 1;
  if (numeric === 1234 || numeric === 12 || numeric === 14) return String(numeric);
  return '1';
}

function createSvgNode(documentRef, tagName, attributes = {}) {
  const node = documentRef.createElementNS
    ? documentRef.createElementNS(SVG_NS, tagName)
    : documentRef.createElement(tagName);
  for (const [name, value] of Object.entries(attributes)) {
    if (value !== undefined && value !== null) node.setAttribute(name, value);
  }
  return node;
}

function listen(node, type, handler) {
  if (typeof node?.addEventListener === 'function') node.addEventListener(type, handler);
  else if (node) node[`on${type}`] = handler;
}

function clearNode(node) {
  if (typeof node?.replaceChildren === 'function') node.replaceChildren();
  else node.innerHTML = '';
}

function gridPoint(grid, phiIndex, axialIndex, nLength) {
  const points = grid.inner_points;
  const nested = Array.isArray(points?.[0]);
  const point = nested ? points[phiIndex]?.[axialIndex] : null;
  const offset = (phiIndex * (nLength + 1) + axialIndex) * 3;
  const x = Number(nested ? point?.[0] : points?.[offset]);
  const y = Number(nested ? point?.[1] : points?.[offset + 1]);
  const z = Number(nested ? point?.[2] : points?.[offset + 2]);
  return Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z) ? [x, y, z] : null;
}

function dedupeAndSort(points) {
  const unique = new Map();
  for (const [x, y] of points) {
    const cleanX = Math.abs(x) < 1e-10 ? 0 : x;
    const cleanY = Math.abs(y) < 1e-10 ? 0 : y;
    unique.set(`${cleanX.toFixed(9)}:${cleanY.toFixed(9)}`, [cleanX, cleanY]);
  }
  return [...unique.values()].sort((left, right) => {
    const leftAngle = (Math.atan2(left[1], left[0]) + Math.PI * 2) % (Math.PI * 2);
    const rightAngle = (Math.atan2(right[1], right[0]) + Math.PI * 2) % (Math.PI * 2);
    return leftAngle - rightAngle;
  });
}

function completeReducedRing(points, quadrants) {
  const normalized = normalizedQuadrants(quadrants);
  const mirrored = [];
  for (const [x, y] of points) {
    if (normalized === '12') {
      mirrored.push([x, y], [x, -y]);
    } else if (normalized === '14') {
      mirrored.push([x, y], [-x, y]);
    } else {
      mirrored.push([x, y], [x, -y], [-x, y], [-x, -y]);
    }
  }
  return dedupeAndSort(mirrored);
}

/**
 * Pick the two axial rings bracketing `t` and linearly interpolate their
 * sampled x/y coordinates. Reduced-domain grids are reflected into the full
 * front-view outline using their declared quadrant symmetry.
 */
export function sampleFreeformGridRing(grid, t) {
  const nPhi = Math.round(Number(grid?.grid_n_phi));
  const nLength = Math.round(Number(grid?.grid_n_length));
  if (!Number.isInteger(nPhi) || nPhi < 2 || !Number.isInteger(nLength) || nLength < 1) {
    return null;
  }
  const nested = Array.isArray(grid?.inner_points?.[0]);
  const expectedLength = nPhi * (nLength + 1) * 3;
  if (
    !Array.isArray(grid?.inner_points) ||
    (!nested && grid.inner_points.length !== expectedLength)
  ) {
    return null;
  }

  const normalizedT = clamp(finite(t, 0), 0, 1);
  const axialPosition = normalizedT * nLength;
  const lowerIndex = Math.min(nLength, Math.floor(axialPosition));
  const upperIndex = Math.min(nLength, lowerIndex + 1);
  const mix = axialPosition - lowerIndex;
  const sampled = [];
  let depthTotal = 0;
  for (let phiIndex = 0; phiIndex < nPhi; phiIndex += 1) {
    const lower = gridPoint(grid, phiIndex, lowerIndex, nLength);
    const upper = gridPoint(grid, phiIndex, upperIndex, nLength);
    if (!lower || !upper) return null;
    const x = lower[0] + (upper[0] - lower[0]) * mix;
    const y = lower[1] + (upper[1] - lower[1]) * mix;
    const z = lower[2] + (upper[2] - lower[2]) * mix;
    sampled.push([x, y]);
    depthTotal += z;
  }

  const points =
    grid.full_circle === false || normalizedQuadrants(grid.quadrants) !== '1234'
      ? completeReducedRing(sampled, grid.quadrants)
      : dedupeAndSort(sampled);
  if (points.length < 3) return null;
  return {
    points,
    depthMm: depthTotal / nPhi,
    t: normalizedT,
    lowerIndex,
    upperIndex,
    mix,
    radiusH: Math.max(...points.map(([x]) => Math.abs(x))),
    radiusV: Math.max(...points.map(([, y]) => Math.abs(y))),
  };
}

export function createFreeformScrubState(initialT = 0) {
  let value = clamp(finite(initialT, 0), 0, 1);
  const listeners = new Set();
  return {
    get() {
      return value;
    },
    set(nextT, source = null) {
      const next = clamp(finite(nextT, value), 0, 1);
      if (next === value) return value;
      value = next;
      for (const listener of [...listeners]) listener(value, source);
      return value;
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export class FreeformCrossSectionInset {
  constructor(container, options = {}) {
    if (!container) throw new Error('FREEFORM cross-section inset requires a container.');
    this.container = container;
    this.document = options.document || container.ownerDocument || globalThis.document;
    this.state = options.state || GlobalState;
    this.params = JSON.parse(JSON.stringify(options.params || this.state.get()?.params || {}));
    this.scrubState = options.scrubState || createFreeformScrubState(0);
    this.scrubT = this.scrubState.get();
    this.cacheKey = getViewportStateCacheKey({ type: 'FREEFORM', params: this.params });
    this.grid = null;
    this.destroyed = false;
    this._unsubscribeScrub = this.scrubState.subscribe((t, source) => {
      this.scrubT = t;
      if (source !== this) this.draw();
    });
    this._onStateUpdated = (nextState) => {
      if (this.destroyed || nextState?.type !== 'FREEFORM') return;
      this.params = JSON.parse(JSON.stringify(nextState.params || {}));
      const nextCacheKey = getViewportStateCacheKey(nextState);
      if (nextCacheKey !== this.cacheKey) this.grid = null;
      this.cacheKey = nextCacheKey;
      this.draw();
    };
    this._onAuthoritative = (payload) => this.setAuthoritative(payload);
    this.mount();
    AppEvents.on('state:updated', this._onStateUpdated);
    AppEvents.on('freeform:authoritative', this._onAuthoritative);
  }

  mount() {
    this.root = this.document.createElement('div');
    this.root.className = 'freeform-cross-section-inset';
    this.root.setAttribute('data-freeform-cross-section-inset', '');

    const header = this.document.createElement('div');
    header.className = 'freeform-cross-section-header';
    const title = this.document.createElement('span');
    title.textContent = 'Sampled cross-section';
    header.appendChild(title);
    this.readout = this.document.createElement('span');
    this.readout.className = 'freeform-cross-section-readout';
    header.appendChild(this.readout);
    this.root.appendChild(header);

    this.svg = createSvgNode(this.document, 'svg', {
      viewBox: `0 0 ${INSET_SIZE} ${INSET_SIZE}`,
      role: 'img',
      'aria-label': 'Sampled FREEFORM cross-section at the selected depth',
    });
    this.svg.setAttribute('class', 'freeform-cross-section-svg');
    this.root.appendChild(this.svg);

    this.message = this.document.createElement('p');
    this.message.className = 'freeform-cross-section-message';
    this.message.textContent = 'outline appears after the first build';
    this.root.appendChild(this.message);

    const scrubberRow = this.document.createElement('label');
    scrubberRow.className = 'freeform-cross-section-scrubber-row';
    const scrubberLabel = this.document.createElement('span');
    scrubberLabel.textContent = 'Depth';
    scrubberRow.appendChild(scrubberLabel);
    this.scrubber = this.document.createElement('input');
    this.scrubber.type = 'range';
    this.scrubber.min = '0';
    this.scrubber.max = '1';
    this.scrubber.step = '0.001';
    this.scrubber.value = String(this.scrubT);
    this.scrubber.setAttribute('aria-label', 'Cross-section depth');
    this.scrubber.setAttribute('data-freeform-scrubber', '');
    listen(this.scrubber, 'input', (event) => this.onScrub(event));
    scrubberRow.appendChild(this.scrubber);
    this.root.appendChild(scrubberRow);

    this.container.appendChild(this.root);
    this.draw();
  }

  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    this._unsubscribeScrub?.();
    AppEvents.off('state:updated', this._onStateUpdated);
    AppEvents.off('freeform:authoritative', this._onAuthoritative);
    this.root?.remove?.();
  }

  setAuthoritative(payload) {
    if (this.destroyed || payload?.cacheKey !== this.cacheKey || !payload?.grid) return false;
    if (!sampleFreeformGridRing(payload.grid, this.scrubT)) return false;
    this.grid = payload.grid;
    this.draw();
    return true;
  }

  onScrub(event) {
    this.scrubT = this.scrubState.set(event?.target?.value, this);
    this.draw();
  }

  appendSvg(tagName, attributes) {
    const node = createSvgNode(this.document, tagName, attributes);
    this.svg.appendChild(node);
    return node;
  }

  draw() {
    if (this.destroyed || !this.svg) return;
    clearNode(this.svg);
    this.scrubT = this.scrubState.get();
    this.scrubber.value = String(this.scrubT);
    const ring = sampleFreeformGridRing(this.grid, this.scrubT);
    if (!ring) {
      this.message.hidden = false;
      this.readout.textContent = '';
      return;
    }
    this.message.hidden = true;
    const radiusMax = Math.max(1e-9, ring.radiusH, ring.radiusV);
    const scale = (INSET_SIZE / 2 - INSET_PADDING) / radiusMax;
    const center = INSET_SIZE / 2;
    const sx = (x) => center + x * scale;
    const sy = (y) => center - y * scale;
    const outline = ring.points
      .map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${sx(x).toFixed(2)},${sy(y).toFixed(2)}`)
      .join(' ');

    this.appendSvg('line', {
      class: 'freeform-cross-section-axis',
      x1: sx(-ring.radiusH),
      y1: center,
      x2: sx(ring.radiusH),
      y2: center,
      'data-crosshair': 'H',
    });
    this.appendSvg('line', {
      class: 'freeform-cross-section-axis',
      x1: center,
      y1: sy(-ring.radiusV),
      x2: center,
      y2: sy(ring.radiusV),
      'data-crosshair': 'V',
    });
    this.appendSvg('path', {
      class: 'freeform-cross-section-outline',
      d: `${outline} Z`,
      'data-ring-lower': ring.lowerIndex,
      'data-ring-upper': ring.upperIndex,
      'data-ring-mix': ring.mix.toFixed(3),
    });
    const horizontalTick = this.appendSvg('text', {
      class: 'freeform-cross-section-tick',
      x: sx(ring.radiusH) - 3,
      y: center - 5,
      'text-anchor': 'end',
    });
    horizontalTick.textContent = `r_H ${ring.radiusH.toFixed(1)}`;
    const verticalTick = this.appendSvg('text', {
      class: 'freeform-cross-section-tick',
      x: center + 5,
      y: sy(ring.radiusV) + 11,
    });
    verticalTick.textContent = `r_V ${ring.radiusV.toFixed(1)}`;
    this.readout.textContent = `depth ${ring.depthMm.toFixed(1)} mm · t ${ring.t.toFixed(2)} · ${(2 * ring.radiusH).toFixed(1)} x ${(2 * ring.radiusV).toFixed(1)} mm`;
  }
}

export function mountFreeformCrossSectionInset(container, options) {
  return new FreeformCrossSectionInset(container, options);
}
