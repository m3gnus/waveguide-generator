import type { SourceDocument, SourcePatch, SourcePoint } from '../../api/sourceEditor';
import { presetDefaults, type PresetShape } from './sourcePreset';

/** Test oracle transcribed from source_contour.py at 232dff4, not the seeder. */
export function expandedPreset(shape: PresetShape, overrides: Record<string, number> = {}): SourceDocument {
  const d: Record<string, number> = { ...presetDefaults[shape], ...overrides };
  const point = (id: string, r_mm: number, z_mm: number): SourcePoint => ({ id, r_mm: r_mm || 0, z_mm: z_mm || 0 });
  const line = (id: string, start: string, end: string): SourcePatch => ({ id, start, end, kind: 'line', center_mm: null, direction: 'ccw', role: 'moving' });
  let points: SourcePoint[], segments: SourcePatch[];
  if (shape === 'flat') {
    points = [point('pole', 0, 0), point('rim', d.radius_mm, 0)]; segments = [line('piston', 'pole', 'rim')];
  } else {
    const height = shape === 'dome' ? d.height_mm : d.cap_height_mm;
    const radius = shape === 'dome' ? d.radius_mm : d.cap_radius_mm;
    const depth = shape === 'cone' ? d.depth_mm : 0;
    points = [point('pole', 0, height - depth), point(shape === 'dome' ? 'dome.rim' : 'cap.rim', radius, -depth)];
    segments = [{ ...line(shape === 'dome' ? 'dome' : 'dust-cap', 'pole', points[1].id), kind: 'arc', center_mm: [0, (height ** 2 - radius ** 2) / (2 * height) - depth], direction: 'cw' }];
    if (shape === 'cone') { points.push(point('cone.rim', d.radius_mm, 0)); segments.push(line('cone', 'cap.rim', 'cone.rim')); }
    if (d.surround_width_mm) {
      const start = points.at(-1)!;
      points.push(point('surround.rim', start.r_mm + d.surround_width_mm, 0));
      const surround = line('surround', start.id, 'surround.rim');
      if (d.surround_depth_mm) Object.assign(surround, { kind: 'arc', center_mm: [start.r_mm + d.surround_width_mm / 2, (d.surround_depth_mm ** 2 - (d.surround_width_mm / 2) ** 2) / (2 * d.surround_depth_mm)], direction: d.surround_depth_mm > 0 ? 'cw' : 'ccw' });
      segments.push(surround);
    }
    if (d.land_width_mm) { const start = points.at(-1)!; points.push(point('rim', start.r_mm + d.land_width_mm, 0)); segments.push({ ...line('land', start.id, 'rim'), role: 'rigid' }); }
  }
  return { contour: { version: 1, physical_source_id: 'diaphragm', rim_id: 'horn.throat', points, segments }, drive: { channel_id: 'source', motion: 'normal', weights: Object.fromEntries(segments.filter(s => s.role === 'moving').map(s => [s.id, 1])) } };
}

/** The shared preset round-trip corpus: defaults, alternate dimensions, signed surround. */
export const presetCorpus: Record<string, number>[] = [
  {},
  { radius_mm: 12, height_mm: 4, depth_mm: 5, cap_radius_mm: 4, cap_height_mm: 1.5, surround_width_mm: 4, surround_depth_mm: .8, land_width_mm: 2 },
  { radius_mm: 15.123, height_mm: 2.3, depth_mm: 3.1, cap_radius_mm: 2.4, cap_height_mm: 1.1, surround_width_mm: 3, surround_depth_mm: -.7, land_width_mm: .75 },
];
export const optionalPresetCorpus: Record<string, number>[] = [
  { surround_width_mm: 0, surround_depth_mm: 0, land_width_mm: 0 }, { surround_depth_mm: 0 },
];
