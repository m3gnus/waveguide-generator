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
  })();
}
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
function retire(s: SlvsModule) { if (live === s) { live = undefined; current = undefined; } }
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
    c.constraints.radius = emulated('Native diameter = 2 × radius.');
    c.constraints.distanceX = emulated('Native signed projected distance onto a fixed unit X axis.');
    c.constraints.distanceY = emulated('Native signed projected distance onto a fixed unit Y axis.');
    c.constraints.pointOnArc = unsupported('PT_ON_CIRCLE constrains the supporting circle, not the finite CCW arc span.');
    c.constraints.tangent.detail = 'Native at shared arc endpoints only; disjoint supporting-curve tangencies return unsupported.';
    c.diagnostics.redundant = unsupported('solveSketch exposes bad handles, without a separate redundancy category.');
    c.diagnostics.partiallyRedundant = unsupported('No partial redundancy API.');
    c.diagnostics.conflicting.detail += ' Signed-angle guard failures name the public angle ID. SolveSpace bad handles include overconstraints; no minimal-set guarantee.';
    c.drag = 'Reachable targets use native dragged()/C_WHERE_DRAGGED hard cursor equations. On native failure, rebuild from the last successful seed without pins and project the cursor seed onto hard constraints. This fallback is not a guaranteed least-movement cursor projection. Handles are reused while cursor pins succeed; fallback and global-owner changes rebuild the system. calculateFaileds:false during drag. WHERE_DRAGGED is a hard pin.';
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
  private prepared?: { pinned: boolean; handles: Map<string, SEntity>; constraints: Map<number, string> };
  protected invalidatePrepared() { this.prepared = undefined; }
  protected prepareDrag(targets: DragTarget[]) { this.native(() => this.build(targets)); }
  /** A trap leaves the instance unusable: retire it so the next createSolver() recovers. */
  private native<T>(call: () => T): T {
    try { return call(); } catch (e) { if (e instanceof WebAssembly.RuntimeError) retire(this.s); throw e; }
  }
  protected release() { if (owners.get(this.s) === this) { this.s.clearSketch(); owners.delete(this.s); } }
  private build(targets: DragTarget[] = []) {
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
        case 'pointOnLine': add(c.id, s.C_PT_ON_LINE, 0, h(c.point), n, h(c.line)); break;
        case 'pointOnArc': throw new Unsupported('Finite arc membership is unavailable');
        case 'fix': { const p = entity(this.sketch, c.point, 'point');
          add(c.id, s.C_POINTS_COINCIDENT, 0, h(c.point), s.addPoint2D(1, p.x, p.y, wp)); break; }
        case 'distance': add(c.id, s.C_PT_PT_DISTANCE, c.value, h(c.a), h(c.b)); break;
        case 'distanceX': case 'distanceY': add(c.id, s.C_PROJ_PT_DISTANCE, -c.value, h(c.a), h(c.b), axis(c.kind === 'distanceX')); break;
        case 'pointLineDistance': add(c.id, s.C_PT_LINE_DISTANCE, c.value * this.sides.get(c.id)!, h(c.point), n, h(c.line)); break;
        case 'radius': add(c.id, s.C_DIAMETER, 2 * c.value, n, n, h(c.arc)); break;
      }
    }
    for (const target of targets) if (!entity(this.sketch, target.pointId, 'point').fixed) s.dragged(group, h(target.pointId), wp);
    this.prepared = { pinned: targets.length > 0, handles, constraints }; owners.set(s, this);
  }
  protected run(options: SolveOptions): NativeResult { return this.native(() => this.runNative(options)); }
  private runNative(options: SolveOptions): NativeResult {
    const updateStart = performance.now();
    const targets = options.dragTargets ?? [];
    if (!this.dragPointIds || !this.prepared || owners.get(this.s) !== this || (targets.length && !this.prepared.pinned)) this.build(targets);
    const s = this.s, group = 2;
    const seed = () => {
      for (const target of targets) {
        if (entity(this.sketch, target.pointId, 'point').fixed) continue;
        const handle = this.prepared!.handles.get(target.pointId)!;
        s.setParamValue(handle.param[0], target.x); s.setParamValue(handle.param[1], target.y);
      }
    };
    seed();
    let t = performance.now(), r = s.solveSketch(group, options.mode === 'full'), solveMs = performance.now() - t;
    // SLVS_RESULT_OKAY (0) and SLVS_RESULT_REDUNDANT_OKAY (4) are both solved states.
    const solved = (code: number) => code === 0 || code === 4;
    if (!solved(r.result) && targets.length) {
      // Hard cursor equations can be infeasible or redundant with sketch constraints.
      // Discard the failed native seed before trying unconstrained cursor seeding.
      this.build(); seed(); t = performance.now();
      r = s.solveSketch(group, options.mode === 'full'); solveMs += performance.now() - t;
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
      ...(options.mode === 'full' ? r.result === 4 ? { dof: r.dof, conflicting: [], redundant: conflicting } : { dof: r.dof, conflicting } : {}) };
  }
}
