import type { Entity as SEntity, SlvsModule } from 'slvs';
import { Adapter, capabilities, emulated, entity, equivalent, pointsOf, unsupported, type NativeResult } from './common';
import { Unsupported, type DragTarget, type SolveOptions } from './types';
let modulePromise: Promise<SlvsModule> | undefined;
export async function create() {
  modulePromise ??= import('slvs').then(m => m.default());
  return new SolveSpace(await modulePromise);
}
// libslvs owns one global sketch. An interleaving instance invalidates the handle cache.
let owner: SolveSpace | undefined;
class SolveSpace extends Adapter {
  constructor(private s: SlvsModule) { super(); }
  capabilities() {
    const c = capabilities();
    c.diagnostics.conflicting = emulated('Native user-constraint diagnosis plus an exact equal-radius check for implicit arc rules, owned by the public arc ID.');
    c.constraints.fix = emulated('A fixed point in group 1 plus coincidence in solved group 2.');
    c.constraints.radius = emulated('Native diameter = 2 × radius.');
    c.constraints.distanceX = emulated('Native signed projected distance onto a fixed unit X axis.');
    c.constraints.distanceY = emulated('Native signed projected distance onto a fixed unit Y axis.');
    c.constraints.pointOnArc = unsupported('PT_ON_CIRCLE constrains the supporting circle, not the finite CCW arc span.');
    c.constraints.tangent.detail = 'Native at shared arc endpoints only; disjoint supporting-curve tangencies return unsupported.';
    c.diagnostics.redundant = unsupported('solveSketch exposes bad handles, without a separate redundancy category.');
    c.diagnostics.partiallyRedundant = unsupported('No partial redundancy API.');
    c.diagnostics.conflicting.detail += ' SolveSpace bad handles include overconstraints; no minimal-set guarantee.';
    c.drag = 'Session builds once, updates setParamValue in place (rebuilds only after another instance uses the global sketch). Emulated by cursor seeding, then native projection onto constraints; no soft-goal API. calculateFaileds:false during drag. WHERE_DRAGGED is a hard pin.';
    return c;
  }
  memoryBytes() { return (this.s as SlvsModule & { HEAPU8?: Uint8Array }).HEAPU8?.buffer.byteLength; }
  private prepared?: { handles: Map<string, SEntity>; constraints: Map<number, string> };
  protected invalidatePrepared() { this.prepared = undefined; }
  protected prepareDrag(_targets: DragTarget[]) { this.build(); }
  protected release() { if (owner === this) { this.s.clearSketch(); owner = undefined; } }
  private build() {
    const s = this.s; owner = undefined; s.clearSketch();
    const wp = s.addBase2D(1), normal = s.addNormal3D(1, 1, 0, 0, 0), group = 2;
    const handles = new Map<string, SEntity>(), constraints = new Map<number, string>();
    const h = (id: string) => { const value = handles.get(id); if (!value) throw new Error(`Unknown entity: ${id}`); return value; };
    const n = s.E_NONE;
    const add = (owner: string, type: number, value = 0, pa = n, pb = n, a = n, b = n, other = false, other2 = false) => {
      const c = s.addConstraint(group, type, wp, value, pa, pb, a, b, n, n, other, other2);
      constraints.set(c.h, owner);
    };
    for (const e of this.sketch.entities) if (e.kind === 'point') {
      handles.set(e.id, s.addPoint2D(e.fixed ? 1 : group, e.x, e.y, wp));
    }
    for (const e of this.sketch.entities) {
      if (e.kind === 'line') handles.set(e.id, s.addLine2D(group, h(e.p1), h(e.p2), wp));
      if (e.kind === 'arc') handles.set(e.id, s.addArc(group, normal, h(e.center), h(e.start), h(e.end), wp));
    }
    let xAxis: SEntity | undefined, yAxis: SEntity | undefined;
    const axis = (x: boolean) => {
      const old = x ? xAxis : yAxis; if (old) return old;
      const a = s.addPoint2D(1, 0, 0, wp), b = s.addPoint2D(1, x ? 1 : 0, x ? 0 : 1, wp);
      const l = s.addLine2D(1, a, b, wp); if (x) xAxis = l; else yAxis = l; return l;
    };
    for (const c of this.sketch.constraints) {
      if ('driving' in c && !c.driving) continue;
      switch (c.kind) {
        case 'coincident': add(c.id, s.C_POINTS_COINCIDENT, 0, h(c.a), h(c.b)); break;
        case 'horizontal': case 'vertical': add(c.id, c.kind === 'horizontal' ? s.C_HORIZONTAL : s.C_VERTICAL,
          0, 'line' in c ? n : h(c.a), 'line' in c ? n : h(c.b), 'line' in c ? h(c.line) : n); break;
        case 'parallel': case 'perpendicular': case 'angle': add(c.id, c.kind === 'parallel' ? s.C_PARALLEL
          : c.kind === 'perpendicular' ? s.C_PERPENDICULAR : s.C_ANGLE,
          c.kind === 'angle' ? c.value * 180 / Math.PI : 0, n, n, h(c.a), h(c.b)); break;
        case 'equal': add(c.id, this.sketch.entities.find(e => e.id === c.a)?.kind === 'line'
          ? s.C_EQUAL_LENGTH_LINES : s.C_EQUAL_RADIUS, 0, n, n, h(c.a), h(c.b)); break;
        case 'tangent': {
          const a = this.sketch.entities.find(e => e.id === c.a), b = this.sketch.entities.find(e => e.id === c.b);
          if (a?.kind === 'arc' && b?.kind === 'arc') {
            const shared = [a.start, a.end].find(p => equivalent(this.sketch, p, b.start) || equivalent(this.sketch, p, b.end));
            if (!shared) throw new Unsupported('SolveSpace arc–arc tangency needs a shared endpoint');
            add(c.id, s.C_CURVE_CURVE_TANGENT, 0, n, n, h(a.id), h(b.id), shared === a.end, equivalent(this.sketch, shared, b.end));
          } else {
            const arc = entity(this.sketch, a?.kind === 'arc' ? c.a : c.b, 'arc');
            const line = entity(this.sketch, a?.kind === 'line' ? c.a : c.b, 'line');
            const shared = [arc.start, arc.end].find(p => equivalent(this.sketch, p, line.p1) || equivalent(this.sketch, p, line.p2));
            if (!shared) throw new Unsupported('SolveSpace line–arc tangency needs a shared endpoint');
            add(c.id, s.C_ARC_LINE_TANGENT, 0, n, n, h(arc.id), h(line.id), shared === arc.end);
          } break;
        }
        case 'midpoint': add(c.id, s.C_AT_MIDPOINT, 0, h(c.point), n, h(c.line)); break;
        case 'pointOnLine': add(c.id, s.C_PT_ON_LINE, 0, h(c.point), n, h(c.line)); break;
        case 'pointOnArc': throw new Unsupported('Finite arc membership is unavailable');
        case 'fix': { const p = entity(this.sketch, c.point, 'point');
          add(c.id, s.C_POINTS_COINCIDENT, 0, h(c.point), s.addPoint2D(1, p.x, p.y, wp)); break; }
        case 'distance': add(c.id, s.C_PT_PT_DISTANCE, c.value, h(c.a), h(c.b)); break;
        case 'distanceX': case 'distanceY': add(c.id, s.C_PROJ_PT_DISTANCE, -c.value, h(c.a), h(c.b), axis(c.kind === 'distanceX')); break;
        case 'pointLineDistance': add(c.id, s.C_PT_LINE_DISTANCE, c.value, h(c.point), n, h(c.line)); break;
        case 'radius': add(c.id, s.C_DIAMETER, 2 * c.value, n, n, h(c.arc)); break;
      }
    }
    this.prepared = { handles, constraints }; owner = this;
  }
  protected run(options: SolveOptions): NativeResult {
    if (!this.dragPointIds || !this.prepared || owner !== this) this.build();
    const s = this.s, group = 2, { handles, constraints } = this.prepared!;
    const h = (id: string) => handles.get(id)!;
    for (const target of options.dragTargets ?? []) {
      const p = entity(this.sketch, target.pointId, 'point'); if (p.fixed) continue;
      const handle = h(p.id); s.setParamValue(handle.param[0], target.x); s.setParamValue(handle.param[1], target.y);
      // WHERE_DRAGGED adds two hard equations in this package. Cursor seeding
      // preserves hard sketch constraints even when the cursor is unreachable.
    }
    const t = performance.now(); const r = s.solveSketch(group, options.mode === 'full'); const solveMs = performance.now() - t;
    const points = pointsOf(this.sketch);
    for (const e of this.sketch.entities) if (e.kind === 'point') {
      const p = h(e.id); points[e.id] = { x: s.getParamValue(p.param[0]), y: s.getParamValue(p.param[1]) };
    }
    const conflicting = [...new Set(Array.from(r.bad ?? []).slice(0, r.nbad).flatMap(v => constraints.has(v) ? [constraints.get(v)!] : []))];
    return { status: r.result === 0 ? 'ok' : 'failed', nativeStatus: r.result, points, solveMs,
      ...(options.mode === 'full' ? { dof: r.dof, conflicting } : {}) };
  }
}
