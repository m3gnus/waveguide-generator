import type { Entity as SEntity, SlvsModule } from 'slvs';
import { Adapter, capabilities, emulated, entity, equivalent, pointsOf, unsupported, type NativeResult } from './common';
import { Unsupported, type DragTarget, type SolveOptions, type Sketch } from './types';
import { wasmInput } from './wasm';
// Adapters share one module instance: each instance reserves a large block of address
// space for its growable memory, so creating one per adapter exhausts the browser's
// reservation long before GC frees them. A trap retires the instance, and the next
// create() gets a fresh one compiled from the cached wasm module.
let compiled: Promise<WebAssembly.Module> | undefined;
function wasmModule() {
  return compiled ??= (async () => {
    const { default: url } = await import('slvs/slvs.wasm?url');
    const input = await wasmInput(url);
    return WebAssembly.compile(typeof input === 'string' ? await (await fetch(input)).arrayBuffer() : input as Uint8Array<ArrayBuffer>);
  })().catch(e => { compiled = undefined; throw e; });
}
type DragModule = SlvsModule & { markDragged(point: SEntity): void };
type Instantiate = (imports: WebAssembly.Imports, done: (instance: WebAssembly.Instance, module: WebAssembly.Module) => void) => object;
async function instantiate(): Promise<SlvsModule> {
  const [{ default: factory }, wasm] = await Promise.all([import('slvs'), wasmModule()]);
  let fail!: (reason: unknown) => void;
  const failed = new Promise<never>((_, reject) => { fail = reject; });
  const instantiateWasm: Instantiate = (imports, done) => {
    WebAssembly.instantiate(wasm, imports).then(instance => done(instance, wasm), fail);
    return {};
  };
  return Promise.race([(factory as (options: { instantiateWasm: Instantiate }) => Promise<SlvsModule>)({ instantiateWasm }), failed]);
}
let current: Promise<SlvsModule> | undefined, live: SlvsModule | undefined;
export async function create() {
  current ??= instantiate().then(m => (live = m), e => { current = undefined; throw e; });
  return new SolveSpace(await current);
}
// A trapped instance can keep answering, possibly from a corrupted heap, so every adapter
// still holding it refuses further work and must be recreated.
const retired = new WeakSet<SlvsModule>();
function retire(s: SlvsModule) { retired.add(s); if (live === s) { live = undefined; current = undefined; } }
// libslvs keeps one global sketch per module instance. Adapters sharing a module rebuild
// when another one solved in between.
const owners = new WeakMap<SlvsModule, SolveSpace>();
class SolveSpace extends Adapter {
  constructor(private s: SlvsModule) { super(); }
  capabilities() {
    const c = capabilities();
    c.diagnostics.conflicting = emulated('Native user-constraint diagnosis plus an exact equal-radius check for implicit arc rules, owned by the public arc ID.');
    c.constraints.fix = emulated('A fixed point in group 1 plus coincidence in solved group 2.');
    c.constraints.angle = emulated('Native cosine equation plus a signed-angle branch guard. Wrong signed branches return failed; no mirrored solution is accepted.');
    c.constraints.pointLineDistance = emulated('Unsigned distance converted to native signed distance using the point side at load time; the side is retained across rebuilds.');
    c.constraints.pointOnLine = emulated('Native signed point-line distance = 0; avoids the persistent PT_ON_LINE interpolation parameter being treated as known on repeat solves.');
    c.constraints.radius = emulated('Native diameter = 2 × radius.');
    c.constraints.distanceX = emulated('Native signed projected distance onto a fixed unit X axis.');
    c.constraints.distanceY = emulated('Native signed projected distance onto a fixed unit Y axis.');
    c.constraints.pointOnArc = unsupported('PT_ON_CIRCLE constrains the supporting circle, not the finite CCW arc span.');
    c.constraints.tangent.detail = 'Native at shared arc endpoints only; disjoint supporting-curve tangencies return unsupported.';
    c.diagnostics.redundant = unsupported('solveSketch exposes bad handles, without a separate redundancy category.');
    c.diagnostics.partiallyRedundant = unsupported('No partial redundancy API.');
    c.diagnostics.conflicting.detail += ' Signed-angle guard failures name the public angle ID. SolveSpace bad handles include overconstraints; no minimal-set guarantee.';
    c.drag = 'Seed free arc centers on the chord bisector, seed cursor coordinates and markDragged point parameters for native least-movement projection. Rebuild when dragged IDs change or on release; no hard cursor equations. At most six native passes refine cursor error while it falls by at least half. calculateFaileds:false during drag.';
    return c;
  }
  memoryBytes() { return (this.s as SlvsModule & { HEAPU8?: Uint8Array }).HEAPU8?.buffer.byteLength; }
  private sides = new Map<string, number>();
  override load(sketch: Sketch) {
    super.load(sketch); this.sides.clear();
    for (const c of sketch.constraints) if (c.kind === 'pointLineDistance') {
      const l = entity(sketch, c.line, 'line'), a = entity(sketch, l.p1, 'point'), b = entity(sketch, l.p2, 'point'), p = entity(sketch, c.point, 'point');
      // SolveSpace uses (point - line start) cross line direction.
      this.sides.set(c.id, Math.sign((p.x - a.x) * (b.y - a.y) - (p.y - a.y) * (b.x - a.x)) || 1);
    }
  }
  private prepared?: { dragged: string; handles: Map<string, SEntity>; constraints: Map<number, string> };
  protected invalidatePrepared() { this.prepared = undefined; }
  protected prepareDrag(targets: DragTarget[]) { this.native(() => this.build(targets)); }
  /** A trap leaves the instance unusable: retire it so the next createSolver() recovers. */
  private native<T>(call: () => T): T {
    if (retired.has(this.s)) throw new Error('The slvs instance was retired after a WASM trap; create a new solver.');
    // RuntimeError is a trap; RangeError here is a wasm call-stack overflow.
    try { return call(); } catch (e) { if (e instanceof WebAssembly.RuntimeError || e instanceof RangeError) retire(this.s); throw e; }
  }
  protected release() { if (owners.get(this.s) === this) { if (!retired.has(this.s)) this.s.clearSketch(); owners.delete(this.s); } }
  private build(targets: DragTarget[] = [], fixedPoint?: string) {
    const s = this.s; owners.delete(s); s.clearSketch();
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
        // PT_ON_LINE introduces a valP that this binding reuses as known after a solve.
        // Signed zero distance is the same locus without that persistent parameter.
        case 'pointOnLine': add(c.id, s.C_PT_LINE_DISTANCE, 0, h(c.point), n, h(c.line)); break;
        case 'pointOnArc': throw new Unsupported('Finite arc membership is unavailable');
        case 'fix': { const p = entity(this.sketch, c.point, 'point');
          add(c.id, s.C_POINTS_COINCIDENT, 0, h(c.point), s.addPoint2D(1, p.x, p.y, wp)); break; }
        case 'distance': add(c.id, s.C_PT_PT_DISTANCE, c.value, h(c.a), h(c.b)); break;
        case 'distanceX': case 'distanceY': add(c.id, s.C_PROJ_PT_DISTANCE, -c.value, h(c.a), h(c.b), axis(c.kind === 'distanceX')); break;
        case 'pointLineDistance': add(c.id, s.C_PT_LINE_DISTANCE, c.value * this.sides.get(c.id)!, h(c.point), n, h(c.line)); break;
        case 'radius': add(c.id, s.C_DIAMETER, 2 * c.value, n, n, h(c.arc)); break;
      }
    }
    if (fixedPoint) {
      const p = entity(this.sketch, fixedPoint, 'point');
      add(fixedPoint, s.C_POINTS_COINCIDENT, 0, h(fixedPoint), s.addPoint2D(1, p.x, p.y, wp));
    }
    for (const target of targets) if (!entity(this.sketch, target.pointId, 'point').fixed) (s as DragModule).markDragged(h(target.pointId));
    this.prepared = { dragged: JSON.stringify(targets.map(t => t.pointId).sort()), handles, constraints }; owners.set(s, this);
  }
  private determinationCache?: { key: string; points: Record<string, boolean> };
  protected run(options: SolveOptions): NativeResult {
    return this.native(() => {
      const result = this.runNative(options);
      if (options.mode !== 'full' || result.status !== 'ok' || this.sketch.entities.length > 64) return result;
      const solved = { ...this.sketch, entities: this.sketch.entities.map(e => e.kind === 'point' ? { ...e, ...result.points[e.id] } : e) };
      const key = JSON.stringify(solved);
      if (this.determinationCache?.key !== key) {
        const start = performance.now(); let solveMs = 0;
        const original = this.sketch, points: Record<string, boolean> = {};
        try {
          this.sketch = solved;
          for (const p of solved.entities) if (p.kind === 'point') {
            if (p.fixed || solved.constraints.some(c => c.kind === 'fix' && c.point === p.id)) { points[p.id] = true; continue; }
            this.build([], p.id);
            const solveStart = performance.now(), r = this.s.solveSketch(2, false); solveMs += performance.now() - solveStart;
            points[p.id] = (r.result === 0 || r.result === 4) && r.dof === result.dof;
          }
          this.determinationCache = { key, points };
        } finally {
          this.sketch = original; this.prepared = undefined;
          result.updateMs += performance.now() - start - solveMs; result.solveMs += solveMs;
        }
      }
      return { ...result, pointDetermined: { ...this.determinationCache!.points } };
    });
  }
  private runNative(options: SolveOptions): NativeResult {
    const updateStart = performance.now();
    const targets = (options.dragTargets ?? []).map(target => {
      let x = target.x, y = target.y;
      // Coordinate locks have no reachable cursor component along the locked axis.
      for (const c of this.sketch.constraints) if ((c.kind === 'distanceX' || c.kind === 'distanceY') && c.driving
        && (c.a === target.pointId || c.b === target.pointId)) {
        const other = entity(this.sketch, c.a === target.pointId ? c.b : c.a, 'point');
        if (other.fixed || this.sketch.constraints.some(c => c.kind === 'fix' && c.point === other.id)) {
          const offset = c.b === target.pointId ? c.value : -c.value;
          if (c.kind === 'distanceX') x = other.x + offset; else y = other.y + offset;
        }
      }
      return { ...target, x, y };
    });
    if (!this.dragPointIds || !this.prepared || owners.get(this.s) !== this || this.prepared.dragged !== JSON.stringify(targets.map(t => t.pointId).sort())) this.build(targets);
    const s = this.s, group = 2;
    const seed = () => {
      for (const target of targets) {
        if (entity(this.sketch, target.pointId, 'point').fixed) continue;
        const handle = this.prepared!.handles.get(target.pointId)!;
        s.setParamValue(handle.param[0], target.x); s.setParamValue(handle.param[1], target.y);
      }
    };
    const cursorError = () => targets.length ? Math.max(...targets.map(target => {
      const h = this.prepared!.handles.get(target.pointId)!;
      return Math.hypot(s.getParamValue(h.param[0]) - target.x, s.getParamValue(h.param[1]) - target.y);
    })) : 0;
    let lastError = cursorError();
    seed();
    // Keep an unconstrained arc's other endpoint still by seeding its free center
    // on the new chord bisector. Explicit center/arc constraints stay native-owned.
    const moved = new Set(targets.map(t => t.pointId));
    const constrained = new Set(this.sketch.constraints.flatMap(c => Object.entries(c).flatMap(([k, v]) => ['point', 'a', 'b', 'arc'].includes(k) ? [String(v)] : [])));
    for (const a of this.sketch.entities) if (a.kind === 'arc' && !moved.has(a.center)
      && (moved.has(a.start) || moved.has(a.end)) && !constrained.has(a.id) && !constrained.has(a.center)
      && !entity(this.sketch, a.center, 'point').fixed) {
      const get = (id: string) => { const h = this.prepared!.handles.get(id)!; return { x: s.getParamValue(h.param[0]), y: s.getParamValue(h.param[1]) }; };
      const p = get(a.start), q = get(a.end), c = get(a.center), dx = q.x - p.x, dy = q.y - p.y, d2 = dx * dx + dy * dy;
      if (d2 > 1e-18) {
        const offset = ((q.x * q.x + q.y * q.y - p.x * p.x - p.y * p.y) / 2 - c.x * dx - c.y * dy) / d2;
        const h = this.prepared!.handles.get(a.center)!;
        s.setParamValue(h.param[0], c.x + offset * dx); s.setParamValue(h.param[1], c.y + offset * dy);
      }
    }
    let t = performance.now(), r = s.solveSketch(group, options.mode === 'full'), solveMs = performance.now() - t;
    // SLVS_RESULT_OKAY (0) and SLVS_RESULT_REDUNDANT_OKAY (4) are both solved states.
    const solved = (code: number) => code === 0 || code === 4;
    for (let refine = 1; targets.length && solved(r.result) && refine < 6; refine++) {
      const error = cursorError();
      if (error <= Math.max(1e-8, options.tolerance ?? 0) || error > lastError / 2 || error >= lastError) break;
      lastError = error; seed(); t = performance.now(); r = s.solveSketch(group, options.mode === 'full'); solveMs += performance.now() - t;
    }
    const updateMs = performance.now() - updateStart - solveMs;
    const { handles, constraints } = this.prepared!;
    const h = (id: string) => handles.get(id)!;
    const points = pointsOf(this.sketch);
    for (const e of this.sketch.entities) if (e.kind === 'point') {
      const p = h(e.id); points[e.id] = { x: s.getParamValue(p.param[0]), y: s.getParamValue(p.param[1]) };
    }
    const conflicting = [...new Set(Array.from(r.bad ?? []).slice(0, r.nbad).flatMap(v => constraints.has(v) ? [constraints.get(v)!] : []))];
    const wrongAngles = this.sketch.constraints.filter(c => {
      if (!solved(r.result) || c.kind !== 'angle' || !c.driving) return false;
      const vector = (id: string) => { const l = entity(this.sketch, id, 'line');
        return { x: points[l.p2].x - points[l.p1].x, y: points[l.p2].y - points[l.p1].y }; };
      const a = vector(c.a), b = vector(c.b), actual = Math.atan2(a.x * b.y - a.y * b.x, a.x * b.x + a.y * b.y);
      return Math.abs(Math.atan2(Math.sin(actual - c.value), Math.cos(actual - c.value))) > 1e-7;
    });
    if (wrongAngles.length) return { status: 'failed', reason: 'Signed angle branch guard rejected native cosine solution',
      points, updateMs, solveMs, nativeStatus: r.result, ...(options.mode === 'full' ? { dof: r.dof, conflicting: [...new Set([...conflicting, ...wrongAngles.map(c => c.id)])] } : {}) };
    // With REDUNDANT_OKAY the reported handles are redundant (implied), not conflicting.
    return { status: solved(r.result) ? 'ok' : 'failed', nativeStatus: r.result, points, updateMs, solveMs,
      ...(options.mode === 'full' ? r.result === 4 ? { dof: r.dof, conflicting: [], redundant: conflicting, hasRedundancy: true } : { dof: r.dof, conflicting, hasRedundancy: false } : {}) };
  }
}
