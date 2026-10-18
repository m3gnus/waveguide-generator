import type * as ACS from 'acs-solver';
import { Adapter, arcValues, capabilities, emulated, entity, pointsOf, publicIds, tangentRadii, unsupported, type NativeResult } from './common';
import type { DragTarget, SolveOptions } from './types';
import { wasmInput } from './wasm';
let initialized: Promise<{ api: typeof ACS; memory: WebAssembly.Memory }> | undefined;
export async function create() {
  initialized ??= (async () => {
    const api = await import('acs-solver');
    const { default: url } = await import('acs-solver/acs_bg.wasm?url');
    const input = await wasmInput(url);
    const { memory } = await api.default({ module_or_path: input });
    return { api, memory };
  })();
  const { api, memory } = await initialized;
  return new Acs(api, memory);
}
type Primitive = Record<string, unknown> & { id: string; type: string };
interface Response {
  status: string; error?: string; primitives: (Primitive & { x?: number; y?: number })[];
  dof?: number; conflicting?: string[]; redundant?: string[]; stats?: { iterations?: number };
}
class Acs extends Adapter {
  constructor(private api: typeof ACS, private memory: WebAssembly.Memory) { super(); }
  capabilities() {
    const c = capabilities();
    c.diagnostics.conflicting = emulated('Native user-constraint diagnosis plus an exact equal-radius check for implicit arc rules, owned by the public arc ID.');
    c.constraints.angle.detail = 'Native angle equation; pinned 0.1.17 can accept supplementary signed branches from mirrored seeds (retained conformance findings).';
    c.constraints.fix = emulated('Two native x/y constraints at the loaded coordinates.');
    c.diagnostics.partiallyRedundant = unsupported('ACS JSON reports redundant IDs, without a separate partial category.');
    c.constraints.tangent = emulated('At coincident endpoints: perpendicular line/radius or parallel radii. Otherwise native finite-curve tangency.');
    c.drag = 'Session reuses a request object and solved geometry seeds; JSON serialization and native reconstruction remain per frame. Temporary x/y soft goals; analyze:false disables expensive diagnosis in drag mode.';
    return c;
  }
  memoryBytes() { return this.memory.buffer.byteLength; }
  protected release() { /* JSON calls own and free their Rust sketch; shared module remains cached. */ }
  private prepared?: { request: { version: number; primitives: Primitive[]; analyze: boolean; maxIterations: number };
    ids: Map<string, string>; reverse: Map<string, string>; goals: Map<string, Primitive[]> };
  protected invalidatePrepared() { this.prepared = undefined; }
  protected prepareDrag(targets: DragTarget[]) { this.build({ mode: 'drag', dragTargets: targets }); }
  private build(options: SolveOptions) {
    const primitives: Primitive[] = [], ids = new Map<string, string>();
    const index = new Map(this.sketch.entities.map((e, i) => [e.id, `e${i}`]));
    const id = (key: string) => { const v = index.get(key); if (!v) throw new Error(`Unknown entity: ${key}`); return v; };
    let serial = 0;
    const add = (owner: string, data: Record<string, unknown> & { type: string }) => {
      const key = `c${serial++}`; ids.set(key, owner); primitives.push({ ...data, id: key });
    };
    for (const e of this.sketch.entities) {
      if (e.kind === 'point') primitives.push({ type: 'point', id: id(e.id), x: e.x, y: e.y, fixed: e.fixed ?? false });
    }
    for (const e of this.sketch.entities) {
      if (e.kind === 'line') primitives.push({ type: 'line', id: id(e.id), p1_id: id(e.p1), p2_id: id(e.p2) });
      if (e.kind === 'arc') {
        const a = arcValues(this.sketch, e);
        primitives.push({ type: 'arc', id: id(e.id), c_id: id(e.center), start_id: id(e.start), end_id: id(e.end),
          radius: a.radius, start_angle: a.start, end_angle: a.end, fixed: false });
        // ACS's implicit arc equations are owned by this public arc entity.
        ids.set(id(e.id), e.id);
      }
    }
    for (const c of this.sketch.constraints) {
      if ('driving' in c && !c.driving) continue;
      switch (c.kind) {
        case 'coincident': case 'parallel': case 'perpendicular': case 'equal':
          add(c.id, { type: c.kind, a: id(c.a), b: id(c.b) }); break;
        case 'tangent': {
          const join = tangentRadii(this.sketch, c.a, c.b);
          if (!join) { add(c.id, { type: 'tangent', a: id(c.a), b: id(c.b) }); break; }
          const radial = `radial${serial++}`;
          primitives.push({ type: 'line', id: radial, p1_id: id(join.curve.center), p2_id: id(join.contact) });
          if (join.other.kind === 'line') add(c.id, { type: 'perpendicular', a: radial, b: id(join.other.id) });
          else {
            const radial2 = `radial${serial++}`;
            primitives.push({ type: 'line', id: radial2, p1_id: id(join.other.center), p2_id: id(join.second) });
            add(c.id, { type: 'parallel', a: radial, b: radial2 });
          }
          break;
        }
        case 'horizontal': case 'vertical': add(c.id, { type: c.kind,
          ...('line' in c ? { line: id(c.line) } : { a: id(c.a), b: id(c.b) }) }); break;
        case 'midpoint': add(c.id, { type: 'midpoint', entities: [id(c.point), id(c.line)] }); break;
        case 'pointOnLine': add(c.id, { type: 'on', point: id(c.point), curve: id(c.line), extension: true }); break;
        case 'pointOnArc': add(c.id, { type: 'on', point: id(c.point), curve: id(c.arc) }); break;
        case 'fix': { const p = entity(this.sketch, c.point, 'point');
          add(c.id, { type: 'x', point: id(c.point), value: p.x });
          add(c.id, { type: 'y', point: id(c.point), value: p.y }); break; }
        case 'distance': case 'distanceX': case 'distanceY': case 'angle': add(c.id, {
          type: c.kind === 'distanceX' ? 'horizontal_distance' : c.kind === 'distanceY' ? 'vertical_distance' : c.kind,
          a: id(c.a), b: id(c.b), value: c.value }); break;
        case 'pointLineDistance': add(c.id, { type: 'distance', a: id(c.point), b: id(c.line), value: c.value, extension: true }); break;
        case 'radius': add(c.id, { type: 'radius', curve: id(c.arc), value: c.value }); break;
      }
    }
    const goals = new Map<string, Primitive[]>();
    for (const target of options.dragTargets ?? []) {
      const pair = (['x', 'y'] as const).map(axis => ({ type: axis, id: `drag${serial++}`, point: id(target.pointId), value: target[axis], temporary: true }));
      primitives.push(...pair); goals.set(target.pointId, pair);
    }
    this.prepared = { request: { version: 1, primitives, analyze: options.mode === 'full', maxIterations: 60 },
      ids, reverse: new Map([...index].map(([user, key]) => [key, user])), goals };
  }
  protected run(options: SolveOptions): NativeResult {
    const updateStart = performance.now();
    if (!this.dragPointIds || !this.prepared) this.build(options);
    const { request, ids, reverse, goals } = this.prepared!;
    for (const target of options.dragTargets ?? []) {
      const pair = goals.get(target.pointId)!; pair[0].value = target.x; pair[1].value = target.y;
    }
    const input = JSON.stringify(request);
    const updateMs = performance.now() - updateStart;
    const t = performance.now(); const output = this.api.acsSolveSketch(input); const solveMs = performance.now() - t;
    const r: Response = JSON.parse(output);
    if (r.status === 'invalid') throw new Error(`ACS rejected adapter request: ${r.error}`);
    const points = pointsOf(this.sketch);
    // Keep all solved geometric variables, including arc radius/angles, as the next seed.
    if (r.status === 'converged' && this.dragPointIds) {
      const solved = new Map(r.primitives.map(p => [p.id, p]));
      for (const p of request.primitives) if (['point', 'arc'].includes(p.type)) {
        const next = solved.get(p.id);
        for (const key of ['x', 'y', 'radius', 'start_angle', 'end_angle']) {
          if (typeof next?.[key] === 'number') p[key] = next[key];
        }
      }
    }
    for (const p of r.primitives ?? []) if (p.type === 'point' && typeof p.x === 'number' && typeof p.y === 'number') {
      const key = reverse.get(p.id); if (key) points[key] = { x: p.x, y: p.y };
    }
    return { status: r.status === 'converged' ? 'ok' : 'failed', nativeStatus: r.status, points, updateMs, solveMs, iterations: r.stats?.iterations,
      ...(options.mode === 'full' ? { dof: r.dof, conflicting: publicIds(r.conflicting ?? [], ids),
        redundant: publicIds(r.redundant ?? [], ids) } : {}) };
  }
}
