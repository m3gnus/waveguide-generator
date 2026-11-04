import type { Authoring } from '../../controller';
import type { SourceMeta } from '../sourceContour';

/** Frozen pre-expression authoring and scale result (8 mm rim → 12.7 mm throat). */
export const sourceScaleLegacy: Authoring<SourceMeta> = {
  sketch: {
    version: 'wg-sketch-v1', nextId: 100,
    points: [
      { id: 'pole', x: 0, y: -4 }, { id: 'elbow', x: 3, y: -4 }, { id: 'rim', x: 8, y: 0 },
      { id: 'a0', x: 0, y: 0 }, { id: 'a1', x: 0, y: 1 },
      { id: 'b0', x: 0, y: 0 }, { id: 'b1', x: 1, y: 0 },
    ],
    entities: [
      { kind: 'line', id: 'cap', p1: 'pole', p2: 'elbow' },
      { kind: 'line', id: 'cone', p1: 'elbow', p2: 'rim' },
      { kind: 'line', id: 'axis', p1: 'a0', p2: 'a1', reference: true, construction: true },
      { kind: 'line', id: 'baffle', p1: 'b0', p2: 'b1', reference: true, construction: true },
    ],
    constraints: [
      { kind: 'pointOnLine', id: 'pole-axis', point: 'pole', line: 'axis' },
      { kind: 'pointOnLine', id: 'rim-plane', point: 'rim', line: 'baffle' },
      { kind: 'horizontal', id: 'cap-horizontal', line: 'cap' },
      { kind: 'distanceX', id: 'radius', a: 'pole', b: 'rim', value: 8, driving: true, label: 'Radius' },
      { kind: 'distanceX', id: 'cap-width', a: 'pole', b: 'elbow', value: 3, driving: true, label: 'Cap width' },
      { kind: 'distanceY', id: 'depth', a: 'elbow', b: 'rim', value: 4, driving: true, label: 'Depth' },
    ],
  },
  meta: { physical_source_id: 'diaphragm', rim_id: 'horn.throat', channel_id: 'source', motion: 'normal',
    weights: { cap: 1, cone: 1 }, references: [{ id: 'axis', kind: 'axis' }, { id: 'baffle', kind: 'baffle' }] },
};
export const legacyScaledValues = '[12.7,4.762499999999999,6.35]';
export const legacyScaledContour = '[{"id":"pole","r_mm":0,"z_mm":-6.35},{"id":"elbow","r_mm":4.762499999999999,"z_mm":-6.35},{"id":"rim","r_mm":12.7,"z_mm":0}]';
