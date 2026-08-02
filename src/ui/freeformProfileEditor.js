import { AppEvents } from '../events.js';
import { buildFreeformDisplayCurve } from '../modules/design/freeformCurve.js';
import { GlobalState } from '../state.js';
import { normalizeAnchorList } from '../config/freeformModel.js';
import { getViewportStateCacheKey } from '../app/viewportCacheKey.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const VIEW_WIDTH = 720;
const VIEW_HEIGHT = 240;
const MARGIN = Object.freeze({ left: 48, right: 24, top: 14, bottom: 30 });
const PLANES = Object.freeze(['H', 'V']);

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function finite(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function cloneParams(params) {
  return JSON.parse(JSON.stringify(params || {}));
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
  if (typeof node.replaceChildren === 'function') node.replaceChildren();
  else node.innerHTML = '';
}

function addClass(node, className) {
  node.classList?.add?.(className);
}

function removeClass(node, className) {
  node.classList?.remove?.(className);
}

function hasParam(node, key) {
  if (!node || !key) return false;
  const direct = node.getAttribute?.('data-param');
  const related = node.getAttribute?.('data-params') || '';
  return direct === key || related.split(/\s+/).includes(key);
}

function walkNodes(root, callback) {
  if (!root) return;
  callback(root);
  for (const child of root.children || []) walkNodes(child, callback);
}

function adaptiveGridStep(span) {
  const desired = Math.max(0, span) / 6;
  if (desired <= 10) return 10;
  if (desired <= 20) return 20;
  return 50;
}

function clampedAnchorFlashKeys(previousParams, nextParams) {
  const previousLength = finite(previousParams?.length, 120);
  const nextLength = finite(nextParams?.length, 120);
  if (previousLength === nextLength || nextLength < 2) return new Set();

  const maximumZ = nextLength - 1;
  const flashes = new Set();
  for (const plane of PLANES) {
    const key = `interior${plane}`;
    const previous = normalizeAnchorList(previousParams?.[key], { length: previousLength });
    const next = normalizeAnchorList(nextParams?.[key], { length: nextLength });
    next.forEach((point, index) => {
      const wasClampedHere = previous.some((oldPoint) => {
        const clampedZ = clamp(oldPoint.z, 1, maximumZ);
        return clampedZ === point.z && oldPoint.z !== clampedZ;
      });
      if (wasClampedHere) flashes.add(`${plane}:${index}`);
    });
  }
  return flashes;
}

function compactInteriorPoint(point) {
  const row = [point.z, point.r];
  if (point.angleDeg === null) return row;
  row.push(point.angleDeg);
  if (point.strength !== null) row.push(point.strength);
  return row;
}

function shapeLabel(shape) {
  if (shape === 'rounded_rectangle') return 'round rect';
  return String(shape || 'ellipse').replaceAll('_', ' ');
}

function normalizedCurveSamples(value) {
  return (Array.isArray(value) ? value : [])
    .filter(
      (point) =>
        Array.isArray(point) &&
        point.length >= 2 &&
        Number.isFinite(Number(point[0])) &&
        Number.isFinite(Number(point[1]))
    )
    .map((point) => [Number(point[0]), Number(point[1])]);
}

function normalizedInflectionSpans(value) {
  return (Array.isArray(value) ? value : [])
    .map((span) => ({
      zStartMm: Number(span?.zStartMm),
      zEndMm: Number(span?.zEndMm),
      tangentDropDeg: Number(span?.tangentDropDeg),
    }))
    .filter(
      (span) =>
        Number.isFinite(span.zStartMm) &&
        Number.isFinite(span.zEndMm) &&
        Number.isFinite(span.tangentDropDeg)
    );
}

function normalizeAuthoritativeFreeform(freeform) {
  if (!freeform || typeof freeform !== 'object') return null;
  const planes = {};
  for (const plane of PLANES) {
    const curve = normalizedCurveSamples(freeform.curveSamples?.[plane]);
    if (curve.length < 2) return null;
    planes[plane] = {
      curve,
      inflectionSpans: normalizedInflectionSpans(freeform.inflectionSpans?.[plane]),
      maxNormalDeviationMm: Number(freeform.maxNormalDeviationMm?.[plane]),
    };
  }
  return { planes };
}

export class FreeformProfileEditor {
  constructor(container, options = {}) {
    if (!container) throw new Error('FREEFORM profile editor requires a container.');
    this.container = container;
    this.document = options.document || container.ownerDocument || globalThis.document;
    this.state = options.state || GlobalState;
    this.commitHandler = options.onCommit || ((patch) => this.state.update(patch));
    this.visibility = options.visibility || { H: true, V: true };
    this.onVisibilityChange = options.onVisibilityChange || null;
    this.params = cloneParams(options.params || this.state.get()?.params);
    this.selectedAnchor = null;
    this.drag = null;
    this.highlightedParams = new Set();
    this.pendingClampFlashes = new Set();
    this.authoritative = null;
    this.cacheKey = getViewportStateCacheKey({ type: 'FREEFORM', params: this.params });
    this.destroyed = false;
    this._onStateUpdated = (nextState) => {
      if (this.destroyed || nextState?.type !== 'FREEFORM') return;
      const previousParams = this.params;
      this.params = cloneParams(nextState.params);
      const nextCacheKey = getViewportStateCacheKey(nextState);
      if (nextCacheKey !== this.cacheKey) this.authoritative = null;
      this.cacheKey = nextCacheKey;
      if (!this.flashClampedAnchors(previousParams)) this.draw();
    };
    this._onAuthoritative = (payload) => this.setAuthoritative(payload);
    this.mount();
    AppEvents.on('state:updated', this._onStateUpdated);
    AppEvents.on('freeform:authoritative', this._onAuthoritative);
  }

  mount() {
    this.root = this.document.createElement('div');
    this.root.className = 'freeform-profile-editor';
    this.root.setAttribute('data-freeform-profile-editor', '');

    const toolbar = this.document.createElement('div');
    toolbar.className = 'freeform-profile-toolbar';
    this.toolbar = toolbar;
    const title = this.document.createElement('span');
    title.className = 'freeform-profile-editor-title';
    title.textContent = '2-D meridian profile';
    toolbar.appendChild(title);

    const legend = this.document.createElement('div');
    legend.className = 'freeform-profile-legend';
    legend.setAttribute('aria-label', 'Profile plane visibility');
    for (const plane of PLANES) {
      const chip = this.document.createElement('button');
      chip.type = 'button';
      chip.className = `freeform-legend-chip freeform-plane-${plane.toLowerCase()}`;
      chip.textContent = plane === 'H' ? 'H horizontal' : 'V vertical';
      chip.setAttribute('data-plane-toggle', plane);
      chip.setAttribute('aria-pressed', String(this.visibility[plane] !== false));
      listen(chip, 'click', (event) => {
        event.preventDefault?.();
        this.togglePlane(plane);
      });
      legend.appendChild(chip);
    }
    toolbar.appendChild(legend);
    this.root.appendChild(toolbar);

    this.svg = createSvgNode(this.document, 'svg', {
      viewBox: `0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`,
      role: 'img',
      'aria-label': 'Interactive horizontal and vertical FREEFORM meridian curves',
      preserveAspectRatio: 'none',
    });
    // SVGElement.className is a read-only SVGAnimatedString in real browsers;
    // only setAttribute works here.
    this.svg.setAttribute('class', 'freeform-profile-svg');
    listen(this.svg, 'pointerdown', (event) => this.onPointerDown(event));
    listen(this.svg, 'pointermove', (event) => this.onPointerMove(event));
    listen(this.svg, 'pointerup', (event) => this.onPointerUp(event));
    listen(this.svg, 'pointercancel', (event) => this.onPointerUp(event));
    listen(this.svg, 'click', (event) => this.onClick(event));
    listen(this.svg, 'dblclick', (event) => this.onDoubleClick(event));
    listen(this.svg, 'mouseover', (event) => this.onHandleHover(event, true));
    listen(this.svg, 'mouseout', (event) => this.onHandleHover(event, false));
    this.root.appendChild(this.svg);
    this.container.appendChild(this.root);
    this.draw();
  }

  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    AppEvents.off('state:updated', this._onStateUpdated);
    AppEvents.off('freeform:authoritative', this._onAuthoritative);
    this.root?.remove?.();
  }

  setAuthoritative(payload) {
    if (this.destroyed || payload?.cacheKey !== this.cacheKey) return false;
    const authoritative = normalizeAuthoritativeFreeform(payload.freeform);
    if (!authoritative) return false;
    this.authoritative = authoritative;
    this.draw();
    return true;
  }

  commit(patch) {
    if (!patch || typeof patch !== 'object') return false;
    return this.commitHandler(patch);
  }

  commitParam(key, value) {
    return this.commit({ [key]: value });
  }

  flashClampedAnchors(previousParams) {
    this.pendingClampFlashes = clampedAnchorFlashKeys(previousParams, this.params);
    if (this.pendingClampFlashes.size === 0) return false;
    this.draw();
    return true;
  }

  togglePlane(plane) {
    if (!PLANES.includes(plane)) return;
    this.visibility[plane] = this.visibility[plane] === false;
    this.onVisibilityChange?.(plane, this.visibility[plane]);
    const chip = this.findByAttribute('data-plane-toggle', plane);
    chip?.setAttribute?.('aria-pressed', String(this.visibility[plane]));
    this.draw();
  }

  findByAttribute(attribute, value) {
    let match = null;
    walkNodes(this.root, (node) => {
      if (!match && node.getAttribute?.(attribute) === value) match = node;
    });
    return match;
  }

  getGeometry() {
    const length = Math.max(1, finite(this.params.length, 120));
    const throatRadius = finite(this.params.throatRadius, 12.7);
    const buildPlane = (plane) => {
      const suffix = plane;
      const interior = normalizeAnchorList(this.params[`interior${suffix}`], { length });
      const mouthRadius = finite(this.params[`mouthRadius${suffix}`], 140);
      const anchors = [
        [0, throatRadius],
        ...interior.map(compactInteriorPoint),
        [length, mouthRadius],
      ];
      const previewCurve = buildFreeformDisplayCurve({
        points: anchors,
        throatAngleDeg: finite(this.params.throatAngle, 15.5),
        mouthAngleDeg: finite(this.params[`mouthAngle${suffix}`], 60),
        throatTangentScale: finite(this.params[`throatTangentScale${suffix}`], 1),
        mouthTangentScale: finite(this.params[`mouthTangentScale${suffix}`], 1),
      });
      const authoritative = this.drag ? null : this.authoritative?.planes?.[plane];
      return {
        plane,
        interior,
        mouthRadius,
        anchors,
        curve: authoritative?.curve || previewCurve,
        previewCurve,
        pending: !authoritative,
        inflectionSpans: authoritative?.inflectionSpans || [],
        maxNormalDeviationMm: authoritative?.maxNormalDeviationMm,
      };
    };
    const planes = { H: buildPlane('H'), V: buildPlane('V') };
    const radii = [1, throatRadius];
    for (const plane of PLANES) {
      radii.push(planes[plane].mouthRadius);
      for (const point of planes[plane].curve) radii.push(point[1]);
    }
    const maximumRadius = Math.max(...radii.filter(Number.isFinite));
    const radiusStep = adaptiveGridStep(maximumRadius);
    const radiusMax = Math.max(
      radiusStep * 2,
      Math.ceil((maximumRadius * 1.08) / radiusStep) * radiusStep
    );
    return { length, throatRadius, planes, radiusMax };
  }

  getTransforms(geometry) {
    const plotWidth = VIEW_WIDTH - MARGIN.left - MARGIN.right;
    const plotHeight = VIEW_HEIGHT - MARGIN.top - MARGIN.bottom;
    return {
      x: (z) => MARGIN.left + (finite(z, 0) / geometry.length) * plotWidth,
      y: (radius) =>
        MARGIN.top + plotHeight - (finite(radius, 0) / geometry.radiusMax) * plotHeight,
      z: (x) => ((x - MARGIN.left) / plotWidth) * geometry.length,
      radius: (y) => ((MARGIN.top + plotHeight - y) / plotHeight) * geometry.radiusMax,
      plotWidth,
      plotHeight,
    };
  }

  appendSvg(tagName, attributes, parent = this.svg) {
    const node = createSvgNode(this.document, tagName, attributes);
    parent.appendChild(node);
    return node;
  }

  draw() {
    if (this.destroyed || !this.svg) return;
    clearNode(this.svg);
    const geometry = this.getGeometry();
    const transforms = this.getTransforms(geometry);
    this.geometry = geometry;
    this.transforms = transforms;
    this.updateInflectionBadge(geometry);
    this.updateDeviationReadout(geometry);

    this.appendSvg('rect', {
      class: 'freeform-profile-background',
      x: MARGIN.left,
      y: MARGIN.top,
      width: transforms.plotWidth,
      height: transforms.plotHeight,
    });
    this.drawGrid(geometry, transforms);
    this.drawStations(geometry, transforms);
    this.drawCurves(geometry, transforms);
    this.drawTangentHandles(geometry, transforms);
    this.drawAnchorHandles(geometry, transforms);
    if (this.drag) this.drawDragGuides(transforms);
  }

  drawGrid(geometry, transforms) {
    const zStep = adaptiveGridStep(geometry.length);
    const radiusStep = adaptiveGridStep(geometry.radiusMax);
    for (let z = 0; z <= geometry.length + 1e-9; z += zStep) {
      const x = transforms.x(z);
      this.appendSvg('line', {
        class: 'freeform-profile-grid',
        x1: x,
        y1: MARGIN.top,
        x2: x,
        y2: MARGIN.top + transforms.plotHeight,
      });
      const label = this.appendSvg('text', {
        class: 'freeform-profile-tick',
        x,
        y: VIEW_HEIGHT - 13,
        'text-anchor': 'middle',
      });
      label.textContent = String(Math.round(z));
    }
    if (geometry.length % zStep > 1e-9) {
      const label = this.appendSvg('text', {
        class: 'freeform-profile-tick',
        x: transforms.x(geometry.length),
        y: VIEW_HEIGHT - 13,
        'text-anchor': 'middle',
      });
      label.textContent = String(Math.round(geometry.length));
    }
    for (let radius = 0; radius <= geometry.radiusMax + 1e-9; radius += radiusStep) {
      const y = transforms.y(radius);
      this.appendSvg('line', {
        class: 'freeform-profile-grid',
        x1: MARGIN.left,
        y1: y,
        x2: MARGIN.left + transforms.plotWidth,
        y2: y,
      });
      const label = this.appendSvg('text', {
        class: 'freeform-profile-tick',
        x: MARGIN.left - 6,
        y: y + 3,
        'text-anchor': 'end',
      });
      label.textContent = String(Math.round(radius));
    }
    const zLabel = this.appendSvg('text', {
      class: 'freeform-profile-axis-label',
      x: VIEW_WIDTH - MARGIN.right,
      y: VIEW_HEIGHT - 2,
      'text-anchor': 'end',
    });
    zLabel.textContent = 'Depth from throat (mm)';
    const radiusLabel = this.appendSvg('text', {
      class: 'freeform-profile-axis-label',
      x: 4,
      y: MARGIN.top + 4,
    });
    radiusLabel.textContent = 'Half-width / half-height (mm)';
  }

  drawStations(geometry, transforms) {
    const stations = Array.isArray(this.params.crossSections) ? this.params.crossSections : [];
    stations.forEach((station, index) => {
      const t = clamp(finite(station?.t, 0), 0, 1);
      const x = transforms.x(t * geometry.length);
      this.appendSvg('line', {
        class: 'freeform-profile-station',
        x1: x,
        y1: MARGIN.top,
        x2: x,
        y2: MARGIN.top + transforms.plotHeight,
        'data-station-index': index,
      });
      const label = this.appendSvg('text', {
        class: 'freeform-profile-station-label',
        x: x + 3,
        y: MARGIN.top + 9 + (index % 2) * 10,
      });
      label.textContent = shapeLabel(station?.shape);
    });
  }

  curvePath(curve, transforms) {
    return curve
      .map(
        (point, index) =>
          `${index === 0 ? 'M' : 'L'}${transforms.x(point[0]).toFixed(2)},${transforms.y(point[1]).toFixed(2)}`
      )
      .join(' ');
  }

  updateInflectionBadge(geometry) {
    const warnings = PLANES.flatMap((plane) =>
      geometry.planes[plane].inflectionSpans.map((span) => ({ plane, span }))
    ).sort((left, right) => right.span.tangentDropDeg - left.span.tangentDropDeg);
    if (warnings.length === 0) {
      this.inflectionBadge?.remove?.();
      this.inflectionBadge = null;
      return;
    }

    if (!this.inflectionBadge) {
      this.inflectionBadge = this.document.createElement('span');
      this.inflectionBadge.className = 'freeform-profile-inflection-badge';
      this.inflectionBadge.setAttribute('data-inflection-warning', '');
      this.toolbar.appendChild(this.inflectionBadge);
    }
    this.inflectionBadge.textContent = `S-curve ${warnings
      .map(({ plane, span }) => `${plane} ${span.tangentDropDeg.toFixed(1)}°`)
      .join(' · ')}`;
    const tooltip =
      "The tangent turns backward here. Adjust the point's tangent handle, add a point, or change Curve Direction.";
    this.inflectionBadge.title = tooltip;
    this.inflectionBadge.setAttribute(
      'aria-label',
      `${this.inflectionBadge.textContent}. ${tooltip}`
    );
  }

  updateDeviationReadout(geometry) {
    const deviations = PLANES.map((plane) => ({
      plane,
      value: geometry.planes[plane].maxNormalDeviationMm,
    })).filter(({ value }) => Number.isFinite(value));
    if (deviations.length === 0) {
      this.deviationReadout?.remove?.();
      this.deviationReadout = null;
      return;
    }
    if (!this.deviationReadout) {
      this.deviationReadout = this.document.createElement('span');
      this.deviationReadout.className = 'freeform-profile-deviation-readout';
      this.deviationReadout.setAttribute('data-curve-deviation', '');
      this.toolbar.appendChild(this.deviationReadout);
    }
    this.deviationReadout.textContent = `Belly ${deviations
      .map(({ plane, value }) => `${plane} ${value.toFixed(1)}`)
      .join(' · ')} mm`;
    const tooltip = deviations
      .map(({ plane, value }) => `${plane} curve bellies ${value.toFixed(1)} mm from the polyline.`)
      .join(' ');
    this.deviationReadout.title = tooltip;
    this.deviationReadout.setAttribute('aria-label', tooltip);
    if (this.inflectionBadge) {
      this.inflectionBadge.title = `${this.inflectionBadge.title} ${tooltip}`;
      this.inflectionBadge.setAttribute(
        'aria-label',
        `${this.inflectionBadge.getAttribute('aria-label')} ${tooltip}`
      );
    }
  }

  inflectionCurvePortion(curve, span) {
    const start = Math.min(span.zStartMm, span.zEndMm);
    const end = Math.max(span.zStartMm, span.zEndMm);
    const tolerance = Math.max(1, Math.abs(end - start)) * 1e-9;
    return curve.filter((point) => point[0] >= start - tolerance && point[0] <= end + tolerance);
  }

  drawCurves(geometry, transforms) {
    for (const plane of PLANES) {
      if (this.visibility[plane] === false) continue;
      const params =
        plane === 'H'
          ? 'length throatRadius throatAngle mouthRadiusH mouthAngleH interiorH throatTangentScaleH mouthTangentScaleH'
          : 'length throatRadius throatAngle mouthRadiusV mouthAngleV interiorV throatTangentScaleV mouthTangentScaleV';
      const path = this.appendSvg('path', {
        class: `freeform-profile-curve freeform-plane-${plane.toLowerCase()}`,
        d: this.curvePath(geometry.planes[plane].curve, transforms),
        fill: 'none',
        'data-plane': plane,
        'data-curve-source': geometry.planes[plane].pending ? 'preview' : 'authoritative',
        'data-params': params,
      });
      if (geometry.planes[plane].pending) addClass(path, 'freeform-profile-curve-pending');
      if (params.split(' ').some((key) => this.highlightedParams.has(key))) {
        addClass(path, 'freeform-profile-linked-highlight');
      }
      for (const span of geometry.planes[plane].inflectionSpans) {
        const portion = this.inflectionCurvePortion(geometry.planes[plane].curve, span);
        if (portion.length < 2) continue;
        this.appendSvg('path', {
          class: `freeform-profile-inflection-overlay freeform-plane-${plane.toLowerCase()}`,
          d: this.curvePath(portion, transforms),
          fill: 'none',
          'data-inflection-plane': plane,
          'aria-label': `S-curve in ${plane}: ${span.tangentDropDeg.toFixed(1)} degrees`,
        });
      }
    }
  }

  tangentKnob(endpoint, angleDeg, direction, param, plane, label, geometry, transforms) {
    const radians = (angleDeg * Math.PI) / 180;
    const armLength = Math.max(8, Math.min(geometry.length, geometry.radiusMax) * 0.18);
    const knob = [
      endpoint[0] + direction * armLength * Math.cos(radians),
      endpoint[1] + direction * armLength * Math.sin(radians),
    ];
    const classes = `freeform-profile-tangent ${plane ? `freeform-plane-${plane.toLowerCase()}` : 'freeform-plane-shared'}`;
    this.appendSvg('line', {
      class: `${classes} freeform-profile-tangent-arm`,
      x1: transforms.x(endpoint[0]),
      y1: transforms.y(endpoint[1]),
      x2: transforms.x(knob[0]),
      y2: transforms.y(knob[1]),
      'data-param': param,
    });
    const node = this.appendSvg('circle', {
      class: `${classes} freeform-profile-tangent-knob`,
      cx: transforms.x(knob[0]),
      cy: transforms.y(knob[1]),
      r: 5,
      tabindex: 0,
      title: label,
      'aria-label': label,
      'data-handle': 'angle',
      'data-param': param,
      'data-plane': plane || '',
      'data-angle-direction': direction,
    });
    const title = this.appendSvg('title', {}, node);
    title.textContent = label;
    if (this.highlightedParams.has(param)) addClass(node, 'freeform-profile-linked-highlight');
  }

  tangentAngleAtAnchor(curve, anchor) {
    if (!Array.isArray(curve) || curve.length < 2) return 0;
    let anchorIndex = 0;
    let bestDistance = Infinity;
    curve.forEach((point, index) => {
      const distance = Math.hypot(point[0] - anchor[0], point[1] - anchor[1]);
      if (distance < bestDistance) {
        bestDistance = distance;
        anchorIndex = index;
      }
    });
    const before = curve[Math.max(0, anchorIndex - 1)];
    const after = curve[Math.min(curve.length - 1, anchorIndex + 1)];
    return (Math.atan2(after[1] - before[1], after[0] - before[0]) * 180) / Math.PI;
  }

  tangentBaseArmLength(geometry) {
    return Math.max(8, Math.min(geometry.length, geometry.radiusMax) * 0.18);
  }

  interiorTangentKnob(plane, index, point, planeGeometry, geometry, transforms) {
    const anchor = [point.z, point.r];
    const automaticAngle = this.tangentAngleAtAnchor(planeGeometry.curve, anchor);
    const angleDeg = point.angleDeg === null ? automaticAngle : point.angleDeg;
    const strength = point.angleDeg === null ? 1 : (point.strength ?? 1);
    const baseArmLength = this.tangentBaseArmLength(geometry);
    const armLength = baseArmLength * strength;
    const radians = (angleDeg * Math.PI) / 180;
    const direction = [Math.cos(radians), Math.sin(radians)];
    const knob = [anchor[0] + armLength * direction[0], anchor[1] + armLength * direction[1]];
    const back = [anchor[0] - armLength * direction[0], anchor[1] - armLength * direction[1]];
    const classes = `freeform-profile-tangent freeform-plane-${plane.toLowerCase()}`;
    this.appendSvg('line', {
      class: `${classes} freeform-profile-tangent-arm freeform-profile-interior-tangent-arm`,
      x1: transforms.x(back[0]),
      y1: transforms.y(back[1]),
      x2: transforms.x(knob[0]),
      y2: transforms.y(knob[1]),
      'data-param': `interior${plane}`,
    });
    const label = `${plane} interior tangent ${index + 1}${point.angleDeg === null ? ' (automatic)' : ''}`;
    const node = this.appendSvg('circle', {
      class: `${classes} freeform-profile-tangent-knob freeform-profile-interior-tangent-knob`,
      cx: transforms.x(knob[0]),
      cy: transforms.y(knob[1]),
      r: 5,
      tabindex: 0,
      title: label,
      'aria-label': label,
      'data-handle': 'interior-tangent',
      'data-param': `interior${plane}`,
      'data-plane': plane,
      'data-index': index,
      'data-base-arm-length': baseArmLength,
    });
    const title = this.appendSvg('title', {}, node);
    title.textContent = `${label}; drag for angle and strength, double-click to reset`;
  }

  drawTangentHandles(geometry, transforms) {
    const throat = [0, geometry.throatRadius];
    this.tangentKnob(
      throat,
      clamp(finite(this.params.throatAngle, 15.5), -90, 90),
      1,
      'throatAngle',
      null,
      'Throat Angle',
      geometry,
      transforms
    );
    for (const plane of PLANES) {
      if (this.visibility[plane] === false) continue;
      this.tangentKnob(
        [geometry.length, geometry.planes[plane].mouthRadius],
        clamp(finite(this.params[`mouthAngle${plane}`], 60), -90, 90),
        -1,
        `mouthAngle${plane}`,
        plane,
        `${plane} Mouth Angle`,
        geometry,
        transforms
      );
    }
    const selected = this.selectedAnchor;
    if (selected && this.visibility[selected.plane] !== false) {
      const planeGeometry = geometry.planes[selected.plane];
      const point = planeGeometry?.interior?.[selected.index];
      if (point) {
        this.interiorTangentKnob(
          selected.plane,
          selected.index,
          point,
          planeGeometry,
          geometry,
          transforms
        );
      }
    }
  }

  anchorHandle(point, options, transforms) {
    const isCircle = options.kind === 'interior';
    const attributes = {
      class: `freeform-profile-handle freeform-profile-${options.kind}-handle ${
        options.plane ? `freeform-plane-${options.plane.toLowerCase()}` : 'freeform-plane-shared'
      }`,
      tabindex: 0,
      title: options.label,
      'aria-label': options.label,
      'data-handle': options.kind,
      'data-param': options.param,
      'data-plane': options.plane || '',
      'data-index': options.index,
    };
    let node;
    if (isCircle) {
      node = this.appendSvg('circle', {
        ...attributes,
        cx: transforms.x(point[0]),
        cy: transforms.y(point[1]),
        r: 5,
      });
    } else {
      node = this.appendSvg('rect', {
        ...attributes,
        x: transforms.x(point[0]) - 5,
        y: transforms.y(point[1]) - 5,
        width: 10,
        height: 10,
        rx: 1,
      });
    }
    const title = this.appendSvg('title', {}, node);
    title.textContent = options.label;
    if (this.highlightedParams.has(options.param))
      addClass(node, 'freeform-profile-linked-highlight');
    return node;
  }

  drawAnchorHandles(geometry, transforms) {
    this.anchorHandle(
      [0, geometry.throatRadius],
      { kind: 'radius', param: 'throatRadius', label: 'Throat Radius' },
      transforms
    );
    for (const plane of PLANES) {
      if (this.visibility[plane] === false) continue;
      const planeGeometry = geometry.planes[plane];
      this.anchorHandle(
        [geometry.length, planeGeometry.mouthRadius],
        {
          kind: 'radius',
          plane,
          param: `mouthRadius${plane}`,
          label: `${plane} Mouth Radius`,
        },
        transforms
      );
      planeGeometry.interior.forEach((point, index) => {
        const node = this.anchorHandle(
          [point.z, point.r],
          {
            kind: 'interior',
            plane,
            index,
            param: `interior${plane}`,
            label: `${plane} Interior Anchor ${index + 1}`,
          },
          transforms
        );
        if (this.selectedAnchor?.plane === plane && this.selectedAnchor?.index === index) {
          addClass(node, 'freeform-profile-anchor-selected');
          const remove = this.appendSvg('text', {
            class: 'freeform-profile-anchor-delete',
            x: transforms.x(point.z) + 9,
            y: transforms.y(point.r) - 8,
            tabindex: 0,
            role: 'button',
            'aria-label': `Delete ${plane} interior anchor ${index + 1}`,
            'data-action': 'delete-anchor',
            'data-plane': plane,
            'data-index': index,
          });
          remove.textContent = '×';
        }
        const flashKey = `${plane}:${index}`;
        if (this.pendingClampFlashes.delete(flashKey)) {
          addClass(node, 'freeform-point-clamped');
          const timer = setTimeout(() => removeClass(node, 'freeform-point-clamped'), 450);
          timer?.unref?.();
        }
      });
    }
  }

  drawDragGuides(transforms) {
    const guide = this.drag.guide;
    if (!guide) return;
    const x = transforms.x(guide[0]);
    const y = transforms.y(guide[1]);
    this.appendSvg('line', {
      class: 'freeform-profile-drag-guide',
      x1: MARGIN.left,
      y1: y,
      x2: x,
      y2: y,
    });
    this.appendSvg('line', {
      class: 'freeform-profile-drag-guide',
      x1: x,
      y1: y,
      x2: x,
      y2: MARGIN.top + transforms.plotHeight,
    });
    const bubble = this.appendSvg('g', { class: 'freeform-profile-readout' });
    const isAngle = this.drag.kind === 'angle';
    const isInteriorTangent = this.drag.kind === 'interior-tangent';
    const radiusTerm =
      this.drag.plane === 'H' ? 'half-width' : this.drag.plane === 'V' ? 'half-height' : 'radius';
    const textValue = isAngle
      ? `${Math.round(this.drag.value)} deg`
      : isInteriorTangent
        ? `${Math.round(this.drag.value.angleDeg)} deg · strength ${this.drag.value.strength.toFixed(2)}`
        : `depth ${guide[0].toFixed(1)} · ${radiusTerm} ${guide[1].toFixed(1)} mm`;
    const bubbleWidth = Math.max(58, textValue.length * 6.1 + 12);
    const bubbleX = clamp(x + 9, MARGIN.left, VIEW_WIDTH - MARGIN.right - bubbleWidth);
    const bubbleY = clamp(y - 27, MARGIN.top, VIEW_HEIGHT - MARGIN.bottom - 22);
    this.appendSvg(
      'rect',
      { x: bubbleX, y: bubbleY, width: bubbleWidth, height: 20, rx: 4 },
      bubble
    );
    const text = this.appendSvg('text', { x: bubbleX + 6, y: bubbleY + 14 }, bubble);
    text.textContent = textValue;
  }

  eventPosition(event) {
    const bounds = this.svg.getBoundingClientRect?.() || {
      left: 0,
      top: 0,
      width: VIEW_WIDTH,
      height: VIEW_HEIGHT,
    };
    const width = bounds.width || VIEW_WIDTH;
    const height = bounds.height || VIEW_HEIGHT;
    return [
      ((finite(event.clientX, bounds.left) - bounds.left) / width) * VIEW_WIDTH,
      ((finite(event.clientY, bounds.top) - bounds.top) / height) * VIEW_HEIGHT,
    ];
  }

  targetData(event, key) {
    let node = event?.target;
    while (node && node !== this.svg) {
      const value = node.getAttribute?.(key);
      if (value !== null && value !== undefined) return { node, value };
      node = node.parentNode;
    }
    return null;
  }

  onPointerDown(event) {
    const handle = this.targetData(event, 'data-handle');
    if (!handle) return;
    event.preventDefault?.();
    const node = handle.node;
    const plane = node.getAttribute('data-plane') || null;
    const index = Number(node.getAttribute('data-index'));
    const param = node.getAttribute('data-param');
    if (handle.value === 'interior') {
      this.selectedAnchor = { plane, index };
    }
    this.drag = {
      kind: handle.value,
      param,
      plane,
      index,
      angleDirection: Number(node.getAttribute('data-angle-direction')) || 1,
      baseArmLength: Number(node.getAttribute('data-base-arm-length')) || null,
      pointerId: event.pointerId,
      params: cloneParams(this.params),
      guide: null,
      value: null,
    };
    this.svg.setPointerCapture?.(event.pointerId);
    this.onPointerMove(event);
  }

  onPointerMove(event) {
    if (!this.drag || (this.drag.pointerId != null && event.pointerId !== this.drag.pointerId))
      return;
    event.preventDefault?.();
    const [screenX, screenY] = this.eventPosition(event);
    const z = this.transforms.z(screenX);
    const radius = this.transforms.radius(screenY);
    const length = this.geometry.length;
    const plane = this.drag.plane;
    if (this.drag.kind === 'radius') {
      const nextRadius = Math.round(Math.max(1, radius) * 10) / 10;
      this.drag.params[this.drag.param] = nextRadius;
      const fixedZ = this.drag.param === 'throatRadius' ? 0 : length;
      this.drag.guide = [fixedZ, nextRadius];
      this.drag.value = nextRadius;
    } else if (this.drag.kind === 'interior') {
      const key = `interior${plane}`;
      const points = normalizeAnchorList(this.drag.params[key], { length });
      const nextPoint = {
        ...points[this.drag.index],
        z: Math.round(clamp(z, 0.5, Math.max(0.5, length - 0.5)) * 10) / 10,
        r: Math.round(Math.max(1, radius) * 10) / 10,
      };
      points[this.drag.index] = nextPoint;
      this.drag.params[key] = points;
      this.drag.guide = [nextPoint.z, nextPoint.r];
      this.drag.value = nextPoint;
    } else if (this.drag.kind === 'interior-tangent') {
      const key = `interior${plane}`;
      const points = normalizeAnchorList(this.drag.params[key], { length });
      const point = points[this.drag.index];
      if (!point) return;
      const vectorZ = z - point.z;
      const vectorRadius = radius - point.r;
      const angleDeg = Math.round(
        clamp((Math.atan2(vectorRadius, vectorZ) * 180) / Math.PI, -89, 89)
      );
      const baseArmLength = this.drag.baseArmLength || this.tangentBaseArmLength(this.geometry);
      const strength =
        Math.round(clamp(Math.hypot(vectorZ, vectorRadius) / baseArmLength, 0.1, 3) * 100) / 100;
      const nextPoint = { ...point, angleDeg, strength };
      points[this.drag.index] = nextPoint;
      this.drag.params[key] = points;
      this.drag.guide = [z, radius];
      this.drag.value = { angleDeg, strength };
    } else if (this.drag.kind === 'angle') {
      const endpoint =
        this.drag.param === 'throatAngle'
          ? [0, finite(this.drag.params.throatRadius, 12.7)]
          : [length, finite(this.drag.params[`mouthRadius${plane}`], 140)];
      const vectorZ = this.drag.angleDirection * (z - endpoint[0]);
      const vectorRadius = this.drag.angleDirection * (radius - endpoint[1]);
      const angle = Math.round(clamp((Math.atan2(vectorRadius, vectorZ) * 180) / Math.PI, -90, 90));
      this.drag.params[this.drag.param] = angle;
      this.drag.guide = [z, radius];
      this.drag.value = angle;
    }
    this.params = cloneParams(this.drag.params);
    this.draw();
  }

  onPointerUp(event) {
    if (!this.drag || (this.drag.pointerId != null && event.pointerId !== this.drag.pointerId))
      return;
    event.preventDefault?.();
    const activeDrag = this.drag;
    this.drag = null;
    this.svg.releasePointerCapture?.(event.pointerId);
    const value = activeDrag.params[activeDrag.param];
    if (!this.commitParam(activeDrag.param, value)) this.draw();
  }

  onClick(event) {
    const remove = this.targetData(event, 'data-action');
    if (remove?.value === 'delete-anchor') {
      event.preventDefault?.();
      this.deleteAnchor(
        remove.node.getAttribute('data-plane'),
        Number(remove.node.getAttribute('data-index'))
      );
      return;
    }
    const handle = this.targetData(event, 'data-handle');
    if (handle?.value === 'interior') {
      this.selectedAnchor = {
        plane: handle.node.getAttribute('data-plane'),
        index: Number(handle.node.getAttribute('data-index')),
      };
      this.draw();
    }
  }

  radiusAtZ(plane, z) {
    const curve = this.geometry.planes[plane].curve;
    let best = curve[0] || [z, 1];
    let bestDistance = Math.abs(best[0] - z);
    for (let index = 0; index < curve.length - 1; index += 1) {
      const left = curve[index];
      const right = curve[index + 1];
      const minimum = Math.min(left[0], right[0]);
      const maximum = Math.max(left[0], right[0]);
      if (z >= minimum && z <= maximum && Math.abs(right[0] - left[0]) > 1e-12) {
        const ratio = (z - left[0]) / (right[0] - left[0]);
        return left[1] + ratio * (right[1] - left[1]);
      }
      for (const candidate of [left, right]) {
        const distance = Math.abs(candidate[0] - z);
        if (distance < bestDistance) {
          best = candidate;
          bestDistance = distance;
        }
      }
    }
    return best[1];
  }

  onDoubleClick(event) {
    const handle = this.targetData(event, 'data-handle');
    if (handle?.value === 'interior-tangent') {
      event.preventDefault?.();
      this.resetInteriorTangent(
        handle.node.getAttribute('data-plane'),
        Number(handle.node.getAttribute('data-index'))
      );
      return;
    }
    const [screenX, screenY] = this.eventPosition(event);
    const z = clamp(this.transforms.z(screenX), 0.5, Math.max(0.5, this.geometry.length - 0.5));
    const targetPlane = this.targetData(event, 'data-plane')?.value;
    const candidates = PLANES.filter((plane) => this.visibility[plane] !== false).map((plane) => {
      const radius = this.radiusAtZ(plane, z);
      return { plane, radius, distance: Math.abs(this.transforms.y(radius) - screenY) };
    });
    candidates.sort((left, right) => left.distance - right.distance);
    const candidate = candidates.find((item) => item.plane === targetPlane) || candidates[0];
    if (!candidate || candidate.distance > 16) return;
    event.preventDefault?.();
    this.insertAnchor(candidate.plane, z, candidate.radius);
  }

  insertAnchor(plane, z, radius = null) {
    if (!PLANES.includes(plane)) return false;
    const key = `interior${plane}`;
    const length = Math.max(1, finite(this.params.length, 120));
    const points = normalizeAnchorList(this.params[key], { length });
    if (points.length >= 62) return false;
    const nextZ =
      Math.round(clamp(finite(z, length / 2), 0.5, Math.max(0.5, length - 0.5)) * 10) / 10;
    const nextRadius =
      Math.round(Math.max(1, finite(radius, this.radiusAtZ(plane, nextZ))) * 10) / 10;
    const inserted = { z: nextZ, r: nextRadius, angleDeg: null, strength: null };
    const nextPoints = [...points, inserted].sort((left, right) => left.z - right.z);
    this.selectedAnchor = {
      plane,
      index: nextPoints.indexOf(inserted),
    };
    return this.commitParam(key, nextPoints);
  }

  resetInteriorTangent(plane, index) {
    if (!PLANES.includes(plane)) return false;
    const key = `interior${plane}`;
    const length = Math.max(1, finite(this.params.length, 120));
    const points = normalizeAnchorList(this.params[key], { length });
    if (!Number.isInteger(index) || index < 0 || index >= points.length) return false;
    points[index] = { ...points[index], angleDeg: null, strength: null };
    return this.commitParam(key, points);
  }

  deleteAnchor(plane, index) {
    if (!PLANES.includes(plane)) return false;
    const key = `interior${plane}`;
    const length = Math.max(1, finite(this.params.length, 120));
    const points = normalizeAnchorList(this.params[key], { length });
    if (!Number.isInteger(index) || index < 0 || index >= points.length) return false;
    this.selectedAnchor = null;
    return this.commitParam(
      key,
      points.filter((_point, pointIndex) => pointIndex !== index)
    );
  }

  onHandleHover(event, active) {
    const handle = this.targetData(event, 'data-handle');
    const param = handle?.node?.getAttribute?.('data-param');
    if (!param) return;
    this.highlightNumericInput(param, active);
  }

  highlightNumericInput(param, active) {
    walkNodes(this.container, (node) => {
      if (node.getAttribute?.('data-param') !== param) return;
      if (active) addClass(node, 'freeform-profile-linked-highlight');
      else removeClass(node, 'freeform-profile-linked-highlight');
    });
  }

  highlightHandle(param, active) {
    if (active) this.highlightedParams.add(param);
    else this.highlightedParams.delete(param);
    walkNodes(this.root, (node) => {
      if (!hasParam(node, param)) return;
      if (active) addClass(node, 'freeform-profile-linked-highlight');
      else removeClass(node, 'freeform-profile-linked-highlight');
    });
  }
}

export function mountFreeformProfileEditor(container, options) {
  return new FreeformProfileEditor(container, options);
}
