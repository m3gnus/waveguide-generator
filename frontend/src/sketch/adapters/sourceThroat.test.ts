// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { SketchController } from '../controller';
import { applySourceCommand, fitSourcePreset, lower, scaleSourceToLimit, seed, withSourceLimit, type SourceMeta } from './sourceContour';
import { seedPreset, presetDefaults } from './sourcePreset';
import { expandedPreset } from './presetTestSupport';
import { INITIAL_SOURCE } from '../../design/sourceContourEditing';
const limit = (radius: number) => ({ kind: 'throat' as const, radius, hard:true });
function controllerFor(state: ReturnType<typeof seed>) {
  return new SketchController<SourceMeta>(state, state => state.meta.references, (state, command) => {
    const next = applySourceCommand(state.sketch,state.meta,command); return 'error' in next ? next : {sketch:next.sketch,meta:next.meta};
  }, () => {});
}
describe('throat intent policy', () => {
  it('follows the throat while retaining an exact typed 3 mm driving dimension', async () => {
    const doc = structuredClone(INITIAL_SOURCE);
    doc.contour.points = [{id:'pole',r_mm:0,z_mm:1},{id:'joint',r_mm:3,z_mm:1},{id:'rim',r_mm:12.7,z_mm:0}];
    doc.contour.segments = [{...doc.contour.segments[0],id:'first',end:'joint'},{...doc.contour.segments[0],id:'last',start:'joint'}]; doc.drive.weights = {first:1,last:1};
    const controller = controllerFor(withSourceLimit(seed(doc),limit(12.7)));
    try {
      await controller.constraint({kind:'distanceX',a:'pole',b:'joint',value:3,driving:true,label:'Typed width'});
      const dimension = controller.current.sketch.constraints.at(-1)!;
      await controller.replaceContext(state => withSourceLimit(state,limit(13)), 'Throat changed 12.70 → 13.00 mm; dimensions kept', 'The new throat conflicts with: ');
      expect(controller.current.sketch.constraints.find(c => c.id === dimension.id)).toMatchObject({value:3,driving:true});
      expect(controller.current.sketch.points.find(p => p.id === 'rim')!.x).toBeCloseTo(13,9);
      expect(controller.notice).toBe('Throat changed 12.70 → 13.00 mm; dimensions kept');
      await controller.undo(); // Context is external: undoing the dimension keeps the new throat.
      expect(controller.current.meta.limit).toEqual(limit(13));
    } finally { controller.destroy(); }
  });
  it('rolls an infeasible throat back, names the conflict, then scales explicitly in one undo step', async () => {
    const doc = structuredClone(INITIAL_SOURCE); doc.contour.points[1].r_mm = 12.7; doc.contour.points[0].z_mm = Math.sqrt(13**2-12.7**2);
    const controller = controllerFor(withSourceLimit(seed(doc),limit(12.7)));
    try {
      await controller.constraint({kind:'distance',a:'pole',b:'rim',value:13,driving:true,label:'Typed chord'});
      const before = controller.current;
      await expect(controller.replaceContext(state => withSourceLimit(state,limit(14)),undefined,'The new throat conflicts with: ')).rejects.toThrow('The new throat conflicts with:');
      expect(controller.current).toBe(before);
      expect(controller.rejection).toMatchObject({overconstrained:true,message:expect.stringContaining('Typed chord')});
      await controller.adopt(scaleSourceToLimit(controller.current,limit(14)),'Scale to fit throat',true);
      const scaled = controller.current;
      expect(scaled.sketch.constraints.find(c => c.label === 'Typed chord')).toMatchObject({value:13*14/12.7,driving:true});
      expect(scaled.sketch.points.find(p => p.id === 'rim')!.x).toBeCloseTo(14,9);
      await controller.undo();
      // Undo restores typed intent, but retries the authoritative 14 mm host.
      expect(controller.current).toBe(before);
      expect(controller.rejection).toMatchObject({overconstrained:true,message:expect.stringContaining('Typed chord')});
      expect(controller.current.meta.limit).not.toEqual(limit(14));
      await controller.undo(true); expect(controller.current).toEqual(scaled);
      expect(controller.current.meta.limit).toEqual(limit(14));
      expect(controller.rejection).toBeUndefined();
    } finally { controller.destroy(); }
  });
  it('only fresh preset seeding automatically scales, preserving construction references and angles', () => {
    const dimensions = presetDefaults.cone, state = seedPreset(expandedPreset('cone', dimensions),'cone',dimensions);
    const original = structuredClone(state);
    const followed = withSourceLimit(state,limit(20));
    for (const c of state.sketch.constraints.filter(c => 'value' in c && c.driving)) expect(followed.sketch.constraints.find(v => v.id === c.id)).toEqual(c);
    const fitted = fitSourcePreset(state,limit(20)), result = lower(fitted.sketch,fitted.meta);
    const factor = 20 / (dimensions.radius_mm + dimensions.surround_width_mm + dimensions.land_width_mm);
    for (const [key, value] of Object.entries(dimensions)) expect(fitted.meta.preset!.values![key]).toBeCloseTo(value * factor, 9);
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.document.contour.points.at(-1)!.r_mm).toBeCloseTo(20,9);
    expect(state).toEqual(original);
    for (const c of state.sketch.constraints.filter(c => c.kind === 'angle')) expect(fitted.sketch.constraints.find(v => v.id === c.id)).toEqual(c);
    for (const e of state.sketch.entities.filter(e => e.reference)) expect(fitted.sketch.entities.find(v => v.id === e.id)).toEqual(e);
  });
});
