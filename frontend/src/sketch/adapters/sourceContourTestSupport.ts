import type { SourceDocument } from '../../api/sourceEditor';
import { drawnSource, editPatch, editPoint, INITIAL_SOURCE, removePoint } from '../../design/sourceContourEditing';

export function editorFixtures(): SourceDocument[] {
  const drawn = drawnSource(INITIAL_SOURCE, [{ id: 'a', r_mm: 3, z_mm: -4 }, { id: 'b', r_mm: 5, z_mm: -2 }, { id: 'c', r_mm: 9, z_mm: 1 }], (() => { let i = 0; return () => `patch-${++i}`; })());
  const merged = drawnSource(INITIAL_SOURCE, [{ id: 'a', r_mm: 0, z_mm: 0 }, { id: 'b', r_mm: 4, z_mm: 1 }, { id: 'c', r_mm: 8, z_mm: 0 }], (() => { let i = 0; return () => `${++i}`; })());
  const exact = structuredClone(INITIAL_SOURCE); exact.contour.points[1].r_mm = 8.123456789; exact.drive.weights.piston = .123456789;
  exact.contour.segments[0] = { ...exact.contour.segments[0], kind: 'arc', center_mm: [4.0617283945, .000123456], direction: 'ccw' };
  const arc = structuredClone(INITIAL_SOURCE); arc.contour.segments[0] = { ...arc.contour.segments[0], kind: 'arc', center_mm: [4, 0] };
  const typed = structuredClone(INITIAL_SOURCE); typed.contour.points[1].r_mm = 9.525;
  typed.contour.segments[0] = { ...arc.contour.segments[0], center_mm: [6.123457, -.000123] };
  const remote = structuredClone(INITIAL_SOURCE); remote.contour.physical_source_id = 'remote-source'; remote.contour.points[1].r_mm = 24;
  return [INITIAL_SOURCE, drawn, merged, removePoint(merged, 1), exact, arc, typed, remote,
    editPoint(INITIAL_SOURCE, 0, 5, 2), editPoint(INITIAL_SOURCE, 1, 10, 7), editPatch(INITIAL_SOURCE, 0, { kind: 'arc' }),
    { ...INITIAL_SOURCE, drive: { ...INITIAL_SOURCE.drive, weights: { piston: -.5 } } },
    { ...INITIAL_SOURCE, drive: { ...INITIAL_SOURCE.drive, weights: { piston: 0 } } },
    editPatch(INITIAL_SOURCE, 0, { role: 'rigid' })];
}
