import { applyCommand } from './ops';
import type { Command } from './commands';
import type { Arc } from './model';
import { arcGeometry } from './geometry';
import { seedPreset, presetDefaults } from './adapters/sourcePreset';
import { expandedPreset } from './adapters/presetTestSupport';
import { applySourceCommand } from './adapters/sourceContour';

export function trimmedArc(dx = 1, radius: number | null = 2.57) {
  const state = seedPreset(expandedPreset('cone'), 'cone', presetDefaults.cone);
  const source = applySourceCommand(state.sketch, state.meta, { kind: 'convertEntity', id: 'dust-cap', to: 'line' });
  if ('error' in source) throw new Error(source.error);
  let s = source.sketch;
  const run = (command: Command) => { const r = applyCommand(s, command); if ('error' in r) throw new Error(r.error); s = r.sketch; return r; };
  run({ kind: 'addLine', from: { x: 2, y: -8 }, to: { x: 10, y: -8 } });
  const crossing = run({ kind: 'addLine', from: { x: 5 - dx / 3, y: -9 }, to: { x: 5 + dx * .943, y: -5.171 } });
  const line = crossing.created.find(id => s.entities.some(e => e.id === id))!;
  run({ kind: 'trim', entity: line, x: 5 - dx * .2, y: -8.6 });
  const stub = s.entities.find(e => !state.sketch.entities.some(old => old.id === e.id) && e.kind === 'line' && s.constraints.some(c => c.kind === 'pointOnLine' && [e.p1, e.p2].includes(c.point)))!;
  run({ kind: 'convertEntity', id: stub.id, to: 'arc' });
  const arc = s.entities.find(e => e.id === stub.id) as Arc;
  const scale = radius === null ? 1 : radius / arcGeometry(s, arc).radius;
  // Match the reported radius, including a steep, very shallow trimmed stub.
  s.points = s.points.map(p => state.sketch.points.some(old => old.id === p.id) ? p
    : { ...p, x: 5 + (p.x - 5) * scale, y: -8 + (p.y + 8) * scale });
  return { sketch: s, references: state.meta.references, arc };
}
