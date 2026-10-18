import type { SourceDocument } from '../../api/sourceEditor';
import { applyCommand } from '../ops';
import type { NewConstraint } from '../model';
import { seed } from './sourceContour';

export type PresetShape = 'flat' | 'dome' | 'cone';
export const presetDefaults = {
  flat: { radius_mm: 8 },
  dome: { radius_mm: 8, height_mm: 3, surround_width_mm: 2, surround_depth_mm: .5, land_width_mm: 1 },
  cone: { radius_mm: 10, depth_mm: 4, cap_radius_mm: 3, cap_height_mm: 2, surround_width_mm: 2, surround_depth_mm: .6, land_width_mm: 1 },
};
export const presetLabels: Record<string, string> = {
  radius_mm: 'Radius', height_mm: 'Dome height', depth_mm: 'Cone depth', cap_radius_mm: 'Dust-cap radius',
  cap_height_mm: 'Dust-cap height', surround_width_mm: 'Surround width', surround_depth_mm: 'Surround depth', land_width_mm: 'Rigid land width',
};

export function fitPresetDimensions(dimensions: Record<string, number>, radius: number) {
  const next = { ...dimensions, radius_mm: radius - (dimensions.surround_width_mm ?? 0) - (dimensions.land_width_mm ?? 0) };
  if (!(next.radius_mm > (dimensions.cap_radius_mm ?? 0))) throw new Error('Limit is too small for the dust cap, surround and land.');
  return next;
}

/** Restate builder intent on the exact /expand document; never regenerate its contour.
 * The pinned builders deliberately make corners between every physical patch.
 */
export function seedPreset(document: SourceDocument, shape: PresetShape, dimensions: Record<string, number>) {
  const state = seed(document), { meta } = state;
  let sketch = state.sketch;
  const main = shape === 'flat' ? 'piston' : shape === 'dome' ? 'dome' : 'dust-cap';
  const expected = shape === 'flat' ? ['piston'] : [main, ...(shape === 'cone' ? ['cone'] : []),
    ...(dimensions.surround_width_mm ? ['surround'] : []), ...(dimensions.land_width_mm ? ['land'] : [])];
  // Non-preset documents (including older server responses) keep generic import semantics.
  if (document.contour.segments.map(e => e.id).join('|') !== expected.join('|')) return state;
  const axis = meta.references.find(r => r.kind === 'axis')!.id, baffle = meta.references.find(r => r.kind === 'baffle')!.id;
  const mapping: Record<string, string> = {};
  const add = (constraint: NewConstraint) => {
    const result = applyCommand(sketch, { kind: 'addConstraint', constraint });
    if ('error' in result) throw new Error(result.error);
    sketch = result.sketch; return result.created[0];
  };
  const onBaffle = (point: string) => {
    if (!sketch.constraints.some(c => c.kind === 'pointOnLine' && c.point === point && c.line === baffle)) add({ kind: 'pointOnLine', point, line: baffle });
  };
  const dimension = (parameter: string, kind: 'distanceX' | 'distanceY', a: string, b: string) => {
    mapping[parameter] = add({ kind, a, b, value: dimensions[parameter], driving: true, label: presetLabels[parameter] });
  };
  if (shape === 'flat') {
    onBaffle('pole'); dimension('radius_mm', 'distanceX', 'pole', 'rim');
  } else {
    const arc = sketch.entities.find(e => e.id === main);
    if (arc?.kind !== 'arc') return state;
    add({ kind: 'pointOnLine', point: arc.center, line: axis });
    const rim = `${shape}.rim`;
    onBaffle(rim); dimension('radius_mm', 'distanceX', 'pole', rim);
    if (shape === 'dome') dimension('height_mm', 'distanceY', rim, 'pole');
    else {
      dimension('cap_radius_mm', 'distanceX', 'pole', 'cap.rim');
      dimension('cap_height_mm', 'distanceY', 'cap.rim', 'pole');
      dimension('depth_mm', 'distanceY', 'cap.rim', rim);
    }
    if (dimensions.surround_width_mm) {
      onBaffle('surround.rim'); dimension('surround_width_mm', 'distanceX', rim, 'surround.rim');
      const surround = sketch.entities.find(e => e.id === 'surround')!;
      if (surround.kind === 'arc') {
        const start = sketch.points.find(p => p.id === rim)!;
        const result = applyCommand(sketch, { kind: 'addPoint', x: start.x + dimensions.surround_width_mm / 2, y: dimensions.surround_depth_mm });
        if ('error' in result) throw new Error(result.error);
        sketch = result.sketch;
        const crown = result.created[0];
        // A construction sub-arc supplies the equal-radius equation at the crown without
        // relying on finite point-on-arc support in the solver. It shares the real centre.
        const helper = `preset-surround-crown${sketch.nextId++}`;
        sketch.entities.push({ kind: 'arc', id: helper, start: surround.start, end: crown, center: surround.center,
          ccw: surround.ccw, construction: true, data: { dimensionHelper: true } });
        add({ kind: 'vertical', a: surround.center, b: crown });
        dimension('surround_depth_mm', 'distanceY', rim, crown);
      }
    }
    if (dimensions.land_width_mm) dimension('land_width_mm', 'distanceX', dimensions.surround_width_mm ? 'surround.rim' : rim, 'rim');
  }
  return { sketch, meta: { ...meta, preset: { shape, dimensions: mapping, values: { ...dimensions } } } };
}
