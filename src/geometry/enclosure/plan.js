function parsePlanBlock(planBlock) {
  if (!planBlock || !planBlock._lines) return null;

  const points = new Map();
  const cpoints = new Map();
  const segments = [];

  const addPoint = (id, x, y) => {
    points.set(id, { x: Number(x), y: Number(y) });
  };

  const addCPoint = (id, x, y) => {
    cpoints.set(id, { x: Number(x), y: Number(y) });
  };

  for (const rawLine of planBlock._lines) {
    const line = rawLine.trim();
    if (!line) continue;
    const parts = line.split(/\s+/);
    const keyword = parts[0];
    if (keyword === 'point' && parts.length >= 4) {
      addPoint(parts[1], parts[2], parts[3]);
    } else if (keyword === 'cpoint' && parts.length >= 4) {
      addCPoint(parts[1], parts[2], parts[3]);
    } else if (keyword === 'line' && parts.length >= 3) {
      segments.push({ type: 'line', ids: [parts[1], parts[2]] });
    } else if (keyword === 'arc' && parts.length >= 4) {
      segments.push({ type: 'arc', ids: [parts[1], parts[2], parts[3]] });
    } else if (keyword === 'ellipse' && parts.length >= 5) {
      segments.push({ type: 'ellipse', ids: [parts[1], parts[2], parts[3], parts[4]] });
    } else if (keyword === 'bezier' && parts.length >= 3) {
      segments.push({ type: 'bezier', ids: parts.slice(1) });
    }
  }

  return { points, cpoints, segments };
}

function sampleArc(p1, center, p2, steps = 16) {
  const a1 = Math.atan2(p1.y - center.y, p1.x - center.x);
  const a2 = Math.atan2(p2.y - center.y, p2.x - center.x);
  let delta = a2 - a1;
  if (delta > Math.PI) delta -= Math.PI * 2;
  if (delta < -Math.PI) delta += Math.PI * 2;

  const out = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const a = a1 + delta * t;
    out.push({
      x: center.x + Math.cos(a) * Math.hypot(p1.x - center.x, p1.y - center.y),
      y: center.y + Math.sin(a) * Math.hypot(p1.x - center.x, p1.y - center.y)
    });
  }
  return out;
}

function sampleEllipse(p1, center, major, p2, steps = 24) {
  const vx = major.x - center.x;
  const vy = major.y - center.y;
  const a = Math.hypot(vx, vy);
  if (a <= 0) return [p1, p2];

  const phi = Math.atan2(vy, vx);
  const cosP = Math.cos(phi);
  const sinP = Math.sin(phi);

  const toLocal = (pt) => {
    const dx = pt.x - center.x;
    const dy = pt.y - center.y;
    return {
      x: cosP * dx + sinP * dy,
      y: -sinP * dx + cosP * dy
    };
  };

  const p1l = toLocal(p1);
  const p2l = toLocal(p2);

  const calcB = (pl) => {
    const denom = 1 - (pl.x / a) ** 2;
    if (denom <= 0) return null;
    return Math.abs(pl.y) / Math.sqrt(denom);
  };

  const b1 = calcB(p1l);
  const b2 = calcB(p2l);
  const b = Number.isFinite(b1) && Number.isFinite(b2) ? (b1 + b2) / 2 : b1 || b2 || a;

  const t1 = Math.atan2(p1l.y / b, p1l.x / a);
  const t2 = Math.atan2(p2l.y / b, p2l.x / a);
  let delta = t2 - t1;
  if (delta > Math.PI) delta -= Math.PI * 2;
  if (delta < -Math.PI) delta += Math.PI * 2;

  const out = [];
  for (let i = 0; i <= steps; i++) {
    const t = t1 + delta * (i / steps);
    const x = a * Math.cos(t);
    const y = b * Math.sin(t);
    out.push({
      x: center.x + cosP * x - sinP * y,
      y: center.y + sinP * x + cosP * y
    });
  }
  return out;
}

function sampleBezier(points, steps = 24) {
  const out = [];
  const n = points.length - 1;
  const bernstein = (i, t) => {
    const binom = (n, k) => {
      let res = 1;
      for (let i = 1; i <= k; i++) {
        res = (res * (n - (k - i))) / i;
      }
      return res;
    };
    return binom(n, i) * Math.pow(1 - t, n - i) * Math.pow(t, i);
  };
  for (let s = 0; s <= steps; s++) {
    const t = s / steps;
    let x = 0;
    let y = 0;
    for (let i = 0; i <= n; i++) {
      const b = bernstein(i, t);
      x += points[i].x * b;
      y += points[i].y * b;
    }
    out.push({ x, y });
  }
  return out;
}

export function buildPlanOutline(params, quadrantInfo) {
  let planName = params.encPlan;
  if (typeof planName === 'string') {
    const trimmed = planName.trim();
    planName = trimmed.replace(/^\"(.*)\"$/, '$1').replace(/^\'(.*)\'$/, '$1');
  }
  if (!planName || !params._blocks) return null;

  const planBlock = params._blocks[planName];
  const parsed = parsePlanBlock(planBlock);
  if (!parsed) return null;

  const { points, cpoints, segments } = parsed;
  if (!segments.length) return null;

  const outline = [];
  segments.forEach((seg, index) => {
    let pts = [];
    if (seg.type === 'line') {
      const p1 = points.get(seg.ids[0]);
      const p2 = points.get(seg.ids[1]);
      if (p1 && p2) pts = [p1, p2];
    } else if (seg.type === 'arc') {
      const p1 = points.get(seg.ids[0]);
      const c = cpoints.get(seg.ids[1]) || points.get(seg.ids[1]);
      const p2 = points.get(seg.ids[2]);
      if (p1 && c && p2) pts = sampleArc(p1, c, p2);
    } else if (seg.type === 'ellipse') {
      const p1 = points.get(seg.ids[0]);
      const c = cpoints.get(seg.ids[1]) || points.get(seg.ids[1]);
      const major = points.get(seg.ids[2]);
      const p2 = points.get(seg.ids[3]);
      if (p1 && c && major && p2) pts = sampleEllipse(p1, c, major, p2);
    } else if (seg.type === 'bezier') {
      const bezPoints = seg.ids.map((id) => points.get(id)).filter(Boolean);
      if (bezPoints.length >= 2) pts = sampleBezier(bezPoints);
    }

    if (!pts.length) return;
    if (index > 0) pts = pts.slice(1);
    outline.push(...pts);
  });

  if (outline.length < 2) return null;

  const sL = parseFloat(params.encSpaceL) || 0;
  const sT = parseFloat(params.encSpaceT) || 0;
  const sR = parseFloat(params.encSpaceR) || 0;
  const sB = parseFloat(params.encSpaceB) || 0;

  const applySpacing = (pt) => {
    const x = pt.x >= 0 ? pt.x + sR : pt.x - sL;
    const z = pt.y >= 0 ? pt.y + sT : pt.y - sB;
    return { x, z };
  };

  const quarter = outline.map(applySpacing);
  const qMode = String(params.quadrants || '1234');

  if (qMode === '14') {
    const bottom = [...quarter].reverse().map((pt) => ({ x: pt.x, z: -pt.z }));
    const half = bottom.concat(quarter.slice(1));
    half.push({ x: 0, z: quarter[quarter.length - 1].z });
    half.push({ x: 0, z: -quarter[quarter.length - 1].z });
    return half;
  }

  if (qMode === '12') {
    const left = [...quarter].reverse().map((pt) => ({ x: -pt.x, z: pt.z }));
    const half = quarter.concat(left.slice(1));
    half.push({ x: -quarter[0].x, z: 0 });
    half.push({ x: quarter[0].x, z: 0 });
    return half;
  }

  if (qMode === '1') {
    const out = [...quarter];
    out.push({ x: 0, z: quarter[quarter.length - 1].z });
    out.push({ x: 0, z: 0 });
    out.push({ x: quarter[0].x, z: 0 });
    return out;
  }

  const top = quarter.concat(
    [...quarter].reverse().map((pt) => ({ x: -pt.x, z: pt.z })).slice(1)
  );
  const bottom = [...top].reverse().map((pt) => ({ x: pt.x, z: -pt.z }));
  return top.concat(bottom.slice(1));
}
