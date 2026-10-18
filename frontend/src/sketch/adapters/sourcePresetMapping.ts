import type { Sketch } from '../model';
import type { SourceMeta } from './sourceContour';

export const reapplyPresetMessage = 'Re-apply the shape to edit this parameter';
export const presetTopologyMessage = "This changes the shape's topology — use Apply shape.";

/** A named dimension describes a whole preset patch, not just surviving point IDs. */
export function presetDimensionActive(sketch: Sketch, meta: SourceMeta, parameter: string): boolean {
  const preset = meta.preset, id = preset?.dimensions[parameter];
  const c = sketch.constraints.find(c => c.id === id);
  if (!preset || !c || !('value' in c)) return false;
  const rim = `${preset.shape}.rim`;
  const patch = (id: string, kind: 'arc' | 'line', start: string, end: string) => {
    const e = sketch.entities.find(e => e.id === id && !e.construction);
    return e?.kind === kind && (e.kind === 'arc' ? e.start === start && e.end === end : e.kind === 'line' && e.p1 === start && e.p2 === end);
  };
  const dimension = (kind: 'distanceX' | 'distanceY', a: string, b: string) => c.kind === kind && c.a === a && c.b === b;
  const surroundKind = (preset.values ? preset.values.surround_depth_mm : preset.dimensions.surround_depth_mm) ? 'arc' : 'line';
  const surround = () => patch('surround', surroundKind, rim, 'surround.rim');
  switch (parameter) {
    case 'radius_mm': return preset.shape === 'flat'
      ? patch('piston', 'line', 'pole', 'rim') && dimension('distanceX', 'pole', 'rim')
      : (preset.shape === 'dome' ? patch('dome', 'arc', 'pole', rim) : patch('cone', 'line', 'cap.rim', rim)) && dimension('distanceX', 'pole', rim);
    case 'height_mm': return patch('dome', 'arc', 'pole', rim) && dimension('distanceY', rim, 'pole');
    case 'cap_radius_mm': return patch('dust-cap', 'arc', 'pole', 'cap.rim') && dimension('distanceX', 'pole', 'cap.rim');
    case 'cap_height_mm': return patch('dust-cap', 'arc', 'pole', 'cap.rim') && dimension('distanceY', 'cap.rim', 'pole');
    case 'depth_mm': return patch('cone', 'line', 'cap.rim', rim) && dimension('distanceY', 'cap.rim', rim);
    case 'surround_width_mm': return surround() && dimension('distanceX', rim, 'surround.rim');
    case 'surround_depth_mm': {
      const e = sketch.entities.find(e => e.id === 'surround');
      if (!surround() || e?.kind !== 'arc' || c.kind !== 'distanceY' || c.a !== rim) return false;
      return sketch.entities.some(helper => helper.kind === 'arc' && helper.construction
        && helper.data && typeof helper.data === 'object' && !Array.isArray(helper.data) && helper.data.dimensionHelper === true
        && helper.center === e.center && helper.start === e.start && helper.end === c.b)
        && sketch.constraints.some(v => v.kind === 'vertical' && 'a' in v && v.a === e.center && v.b === c.b);
    }
    case 'land_width_mm': return c.kind === 'distanceX' && [rim, 'surround.rim'].includes(c.a)
      && patch('land', 'line', c.a, 'rim') && c.b === 'rim';
    default: return false;
  }
}

/** Called at the consumer boundary, before either editor can enter continuation. */
export function presetDimensionProblem(sketch: Sketch, meta: SourceMeta, id: string, value: number): string | undefined {
  const parameter = Object.keys(meta.preset?.dimensions ?? {}).find(key => meta.preset!.dimensions[key] === id);
  if (!parameter) return;
  if (!presetDimensionActive(sketch, meta, parameter)) return reapplyPresetMessage;
  const c = sketch.constraints.find(c => c.id === id)!;
  if (!('value' in c) || value === c.value) return;
  if (parameter === 'surround_depth_mm' && Math.sign(value) !== Math.sign(c.value)
    || ['surround_width_mm', 'land_width_mm'].includes(parameter) && value === 0) return presetTopologyMessage;
}
