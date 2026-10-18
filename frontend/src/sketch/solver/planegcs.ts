import type { GcsWrapper, ModuleStatic, SketchPrimitive, Constraint as GConstraint } from '@salusoft89/planegcs';
import { Adapter, arcValues, capabilities, emulated, entity, pointsOf, publicIds, tangentRadii, unsupported, type NativeResult } from './common';
import type { Constraint, DragTarget, SolveOptions } from './types';
import { wasmInput } from './wasm';
let modulePromise: Promise<ModuleStatic> | undefined;
async function moduleInstance() {
  return modulePromise ??= (async () => {
    const { init_planegcs_module } = await import('@salusoft89/planegcs');
    const { default: url } = await import('@salusoft89/planegcs/dist/planegcs_dist/planegcs.wasm?url');
    const input = await wasmInput(url);
    // This AOT artifact ignores wasmBinary; Node needs a filesystem locateFile path.
    const config = { locateFile: () => typeof input === 'string' ? url : url.startsWith('/@fs/') ? url.slice(4) : `.${url}` };
    return init_planegcs_module(config);
  })();
}
export async function create() {
  const [mod, { GcsWrapper }] = await Promise.all([moduleInstance(), import('@salusoft89/planegcs')]);
  return new PlaneGCS(new GcsWrapper(new mod.GcsSystem(), mod), mod);
}
class PlaneGCS extends Adapter {
  constructor(private wrapper: GcsWrapper, private mod: ModuleStatic) { super(); wrapper.set_max_iterations(60); }
  capabilities() {
    const c = capabilities();
    c.diagnostics.conflicting = emulated('Native user-constraint diagnosis plus an exact equal-radius check for implicit arc rules, owned by the public arc ID.');
    c.constraints.pointOnArc = unsupported('Pinned point_on_arc accepts points on the complementary circle span; no exact finite-span composition is exposed.');
    c.constraints.midpoint = emulated('Point on supporting line + point on perpendicular bisector (two exact scalar equations).');
    c.constraints.fix = emulated('Two coordinate equations at the loaded point position.');
    c.constraints.tangent = emulated('At coincident endpoints: perpendicular line/radius or parallel radii. Otherwise native supporting-curve tangency.');
    c.drag = 'Session pushes once; fixed named coordinate goals update in place. Native solve still reinitializes and diagnoses on every frame.';
    c.constraints.distanceX = emulated('Difference of point x parameters (signed b.x - a.x).');
    c.constraints.distanceY = emulated('Difference of point y parameters (signed b.y - a.y).');
    return c;
  }
  memoryBytes() { return (this.mod as ModuleStatic & { HEAPU8?: Uint8Array }).HEAPU8?.buffer.byteLength; }
  protected release() { this.wrapper.destroy_gcs_module(); }
  private prepared?: { internal: Map<string, string>; ids: Map<string, string> };
  protected invalidatePrepared() { this.prepared = undefined; }
  protected prepareDrag(targets: DragTarget[]) { this.build({ mode: 'drag', dragTargets: targets }); }
  private build(options: SolveOptions) {
    const w = this.wrapper; w.clear_data();
    const primitives: SketchPrimitive[] = [], ids = new Map<string, string>();
    // Private IDs never depend on user spelling, avoiding collisions with arbitrary user IDs.
    const internal = new Map(this.sketch.entities.map((e, i) => [e.id, `e${i}`]));
    const id = (v: string) => { const result = internal.get(v); if (!result) throw new Error(`Unknown entity: ${v}`); return result; };
    let serial = 0;
    const add = (owner: string, c: Omit<GConstraint, 'id'>) => {
      const key = `c${serial++}`; ids.set(key, owner); primitives.push({ ...c, id: key } as GConstraint);
    };
    for (const e of this.sketch.entities) if (e.kind === 'point') {
      primitives.push({ type: 'point', id: id(e.id), x: e.x, y: e.y, fixed: e.fixed ?? false });
    }
    for (const e of this.sketch.entities) {
      if (e.kind === 'line') primitives.push({ type: 'line', id: id(e.id), p1_id: id(e.p1), p2_id: id(e.p2) });
      if (e.kind === 'arc') {
        const a = arcValues(this.sketch, e);
        primitives.push({ type: 'arc', id: id(e.id), c_id: id(e.center), start_id: id(e.start), end_id: id(e.end),
          radius: a.radius, start_angle: a.start, end_angle: a.end });
      }
    }
    for (const e of this.sketch.entities) if (e.kind === 'arc') add(e.id, { type: 'arc_rules', a_id: id(e.id) } as GConstraint);
    const emit = (c: Constraint, data: object) => add(c.id, { ...data, driving: true } as GConstraint);
    for (const c of this.sketch.constraints) {
      if ('driving' in c && !c.driving) continue;
      switch (c.kind) {
        case 'coincident': emit(c, { type: 'p2p_coincident', p1_id: id(c.a), p2_id: id(c.b) }); break;
        case 'horizontal': case 'vertical': emit(c, 'line' in c ? { type: `${c.kind}_l`, l_id: id(c.line) }
          : { type: `${c.kind}_pp`, p1_id: id(c.a), p2_id: id(c.b) }); break;
        case 'parallel': case 'perpendicular': case 'angle': emit(c, { type: c.kind === 'perpendicular' ? 'perpendicular_ll'
          : c.kind === 'angle' ? 'l2l_angle_ll' : 'parallel', l1_id: id(c.a), l2_id: id(c.b), ...('value' in c ? { angle: c.value } : {}) }); break;
        case 'tangent': {
          const join = tangentRadii(this.sketch, c.a, c.b);
          if (join) {
            const radial = `radial${serial++}`;
            primitives.push({ type: 'line', id: radial, p1_id: id(join.curve.center), p2_id: id(join.contact) });
            if (join.other.kind === 'line') emit(c, { type: 'perpendicular_ll', l1_id: radial, l2_id: id(join.other.id) });
            else {
              const radial2 = `radial${serial++}`;
              primitives.push({ type: 'line', id: radial2, p1_id: id(join.other.center), p2_id: id(join.second) });
              emit(c, { type: 'parallel', l1_id: radial, l2_id: radial2 });
            }
            break;
          }
          const a = this.sketch.entities.find(e => e.id === c.a), b = this.sketch.entities.find(e => e.id === c.b);
          if (a?.kind === 'arc' && b?.kind === 'arc') emit(c, { type: 'tangent_aa', a1_id: id(c.a), a2_id: id(c.b) });
          else { const arc = a?.kind === 'arc' ? c.a : c.b, line = a?.kind === 'line' ? c.a : c.b;
            entity(this.sketch, arc, 'arc'); entity(this.sketch, line, 'line');
            emit(c, { type: 'tangent_la', l_id: id(line), a_id: id(arc) }); } break;
        }
        case 'equal': {
          const a = this.sketch.entities.find(e => e.id === c.a);
          emit(c, a?.kind === 'line' ? { type: 'equal_length', l1_id: id(c.a), l2_id: id(c.b) }
            : { type: 'equal_radius_aa', a1_id: id(c.a), a2_id: id(c.b) }); break;
        }
        case 'midpoint': emit(c, { type: 'point_on_perp_bisector_pl', p_id: id(c.point), l_id: id(c.line) });
          emit(c, { type: 'point_on_line_pl', p_id: id(c.point), l_id: id(c.line) }); break;
        case 'pointOnLine': emit(c, { type: 'point_on_line_pl', p_id: id(c.point), l_id: id(c.line) }); break;
        case 'pointOnArc': emit(c, { type: 'point_on_arc', p_id: id(c.point), a_id: id(c.arc) }); break;
        case 'fix': { const p = entity(this.sketch, c.point, 'point');
          emit(c, { type: 'coordinate_x', p_id: id(c.point), x: p.x });
          emit(c, { type: 'coordinate_y', p_id: id(c.point), y: p.y }); break; }
        case 'distance': emit(c, { type: 'p2p_distance', p1_id: id(c.a), p2_id: id(c.b), distance: c.value }); break;
        case 'distanceX': case 'distanceY': emit(c, { type: 'difference', param1: { o_id: id(c.a), prop: c.kind === 'distanceX' ? 'x' : 'y' },
          param2: { o_id: id(c.b), prop: c.kind === 'distanceX' ? 'x' : 'y' }, difference: c.value }); break;
        case 'pointLineDistance': emit(c, { type: 'p2l_distance', p_id: id(c.point), l_id: id(c.line), distance: c.value }); break;
        case 'radius': emit(c, { type: 'arc_radius', a_id: id(c.arc), radius: c.value }); break;
      }
    }
    for (const [i, target] of (options.dragTargets ?? []).entries()) for (const axis of ['x', 'y'] as const) {
      const name = `goal${this.dragPointIds?.indexOf(target.pointId) ?? i}${axis}`;
      if (this.dragPointIds) w.push_sketch_param(name, target[axis]);
      const value = this.dragPointIds ? name : target[axis];
      primitives.push({ id: `drag${serial++}`, p_id: id(target.pointId), driving: true, temporary: true,
        ...(axis === 'x' ? { type: 'coordinate_x', x: value } : { type: 'coordinate_y', y: value }) });
    }
    w.push_primitives_and_params(primitives);
    this.prepared = { internal, ids };
  }
  protected run(options: SolveOptions): NativeResult {
    if (!this.dragPointIds || !this.prepared) this.build(options);
    const w = this.wrapper, { internal, ids } = this.prepared!;
    if (this.dragPointIds) for (const target of options.dragTargets ?? []) {
      const i = this.dragPointIds.indexOf(target.pointId);
      w.set_sketch_param(`goal${i}x`, target.x); w.set_sketch_param(`goal${i}y`, target.y);
    }
    const t = performance.now(), status = w.solve(); const solveMs = performance.now() - t;
    w.apply_solution();
    const points = pointsOf(this.sketch);
    for (const e of this.sketch.entities) if (e.kind === 'point') {
      const p = w.sketch_index.get_sketch_point(internal.get(e.id)!); points[e.id] = { x: p.x, y: p.y };
    }
    return { status: status <= 1 ? 'ok' : 'failed', nativeStatus: status, points, solveMs, ...(options.mode === 'full' ? {
      dof: w.gcs.dof(), conflicting: publicIds(w.get_gcs_conflicting_constraints(), ids),
      redundant: publicIds(w.get_gcs_redundant_constraints(), ids),
      partiallyRedundant: publicIds(w.get_gcs_partially_redundant_constraints(), ids) } : {}) };
  }
}
