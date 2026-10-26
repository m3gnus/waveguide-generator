// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import { designForFamily, resetDesignStore, useDesignStore } from '../../stores/design';
import { SketchController, type Authoring } from '../controller';
import type { Sketch } from '../model';
import { applyCommand, importedSketch } from '../ops';
import type { EditRecord } from '../../stores/designEdit';
import { ProjectHistoryAuthority } from './projectHistoryAuthority';
import { SketchSolveHandle } from './sketchSolver';
import * as sketchSolver from './sketchSolver';

let controller: SketchController<null> | undefined;
afterEach(() => { controller?.destroy(); controller = undefined; vi.restoreAllMocks(); resetDesignStore(); });
function record(state: Authoring<null>): EditRecord<Sketch> {
  return { document: state.sketch, authoring: state, status: 'valid', draftKey: JSON.stringify(state) };
}
async function setup() {
  resetDesignStore(); useDesignStore.getState().replaceDocument(designForFamily('FREEFORM'));
  const initial: Authoring<null> = { sketch: importedSketch([{ id: 'p', x: 0, y: 0 }, { id: 'q', x: 10, y: 0 }], [{ kind: 'line', id: 'line', p1: 'p', p2: 'q' }]), meta: null };
  useDesignStore.getState().commitEmbedded({ profile_h: record(initial) }, 'amend');
  const authority = new ProjectHistoryAuthority<Authoring<null>>({ paths: ['profile_h'], onMissing: () => {},
    read: values => (values.profile_h as EditRecord<Sketch> | undefined)?.authoring as Authoring<null> | undefined,
    write: state => ({ profile_h: record(state) }),
  });
  const preview = vi.fn();
  controller = new SketchController({ initial, authority, references: [], preview, changed: () => {}, apply: (state, command) => {
    const result = applyCommand(state.sketch, command); return 'error' in result ? result : { ...state, sketch: result.sketch };
  } });
  await controller.ready;
  return { c: controller, initial, preview, authority };
}
async function restored(preview: ReturnType<typeof vi.fn>, state: Authoring<null>) {
  await vi.waitFor(() => expect(preview).toHaveBeenLastCalledWith(state, expect.anything()));
}
it('rebuilds the real solver on project Undo/Redo and publishes context refresh as amend', async () => {
  const { c, initial, preview } = await setup();
  await c.command({ kind: 'movePoint', id: 'q', x: 12, y: 0 }); const edited = c.current;
  await c.replaceContext(state => ({ ...state, meta: null }));
  expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
  useDesignStore.getState().undo(); await restored(preview, initial);
  expect(c.current).toEqual(initial); expect(c.diagnosis?.status).toBe('ok');
  useDesignStore.getState().redo(); await restored(preview, edited);
  expect(c.current).toEqual(edited);
  // The rebuilt session must accept a subsequent edit too.
  await c.command({ kind: 'movePoint', id: 'q', x: 14, y: 0 });
  expect(c.current.sketch.points.find(p => p.id === 'q')!.x).toBe(14);
});
it('drops an in-flight real solver edit when TopBar Undo arrives', async () => {
  const { c, initial, preview } = await setup();
  await c.command({ kind: 'movePoint', id: 'q', x: 12, y: 0 });
  let release!: () => void, started!: () => void;
  const solving = new Promise<void>(r => { started = r; }), gate = new Promise<void>(r => { release = r; });
  const diagnose = SketchSolveHandle.prototype.diagnose;
  vi.spyOn(SketchSolveHandle.prototype, 'diagnose').mockImplementationOnce(async function(this: SketchSolveHandle<null>, state) {
    const result = await diagnose.call(this, state); started(); await gate; return result;
  });
  const pending = c.command({ kind: 'movePoint', id: 'q', x: 20, y: 0 }); await solving;
  useDesignStore.getState().undo(); release(); await pending; await restored(preview, initial);
  expect(c.current).toEqual(initial);
  expect((useDesignStore.getState().design.profile_h as unknown as EditRecord<Sketch>).authoring).toEqual(initial);
  expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
});
it('shows an unsolvable historic sketch without refusing the project state', async () => {
  const { c, initial, preview } = await setup();
  const conflict: Authoring<null> = { ...initial, sketch: { ...initial.sketch, constraints: [
    { kind: 'distance', id: 'short', a: 'p', b: 'q', value: 10, driving: true },
    { kind: 'distance', id: 'long', a: 'p', b: 'q', value: 20, driving: true },
  ] } };
  useDesignStore.getState().commitEmbedded({ profile_h: { ...record(conflict), status: 'invalid' } }, 'step');
  await restored(preview, conflict);
  expect(c.current).toEqual(conflict); expect(c.diagnosis?.status).not.toBe('ok');
  useDesignStore.getState().undo(); await restored(preview, initial);
  useDesignStore.getState().redo(); await restored(preview, conflict);
  expect(c.current).toEqual(conflict); expect(c.diagnosis?.status).not.toBe('ok');
});

it.each(['open', 'solve', 'replace'] as const)('cancels the project context transaction when %s throws', async failure => {
  const { c, initial, authority } = await setup(), cancel = vi.spyOn(authority, 'cancel');
  const error = new Error(`context ${failure} failed`);
  if (failure === 'open') vi.spyOn(sketchSolver, 'openSketchHandle').mockRejectedValueOnce(error);
  if (failure === 'solve') vi.spyOn(SketchSolveHandle.prototype, 'solveFull').mockRejectedValueOnce(error);
  await expect(c.replaceContext(state => { if (failure === 'replace') throw error; return state; })).rejects.toBe(error);
  expect(cancel).toHaveBeenCalledOnce(); expect(c.current).toEqual(initial);
  expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
  await c.command({ kind: 'movePoint', id: 'q', x: 14, y: 0 });
  expect(c.current.sketch.points.find(p => p.id === 'q')!.x).toBe(14);
  expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
});
