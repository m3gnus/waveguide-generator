import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { designForFamily, recordCommittedAthPolars, resetDesignStore, subscribeRevision, useDesignStore, type FreeformProfile } from '../../stores/design';
import { AUTOSAVE_KEY, restoreAutosave, writeAutosave } from '../../stores/autosave';
import { useDocumentStore } from '../../stores/document';
import { EditCore, StaleWorkspaceWork } from './editCore';
import { editRecord } from './editRecord';
import type { EditRecord } from '../../stores/designEdit';
import { PlainController } from './plainController';
import { ProjectHistoryAuthority } from './projectHistoryAuthority';

import { adapter, initial, profileBinding, type State } from './profileTestSupport';
const record = () => useDesignStore.getState().design.profile_h as unknown as EditRecord<FreeformProfile>;
const past = () => useDesignStore.temporal.getState().pastStates;
let disposers: (() => void)[];
beforeEach(() => {
  disposers = []; resetDesignStore(); localStorage.clear();
  useDesignStore.getState().replaceDocument(designForFamily('FREEFORM'));
  useDesignStore.getState().commitEmbedded({ profile_h: editRecord(adapter, initial, null) }, 'amend');
});
afterEach(() => { disposers.reverse().forEach(dispose => dispose()); resetDesignStore(); });
function authority(extraPath = false) {
  const onMissing = vi.fn();
  const bridge = new ProjectHistoryAuthority(profileBinding(onMissing, extraPath));
  disposers.push(() => bridge.dispose()); return { bridge, onMissing };
}
function setup(extraPath = false) {
  const { bridge, onMissing } = authority(extraPath), changed = vi.fn(), preview = vi.fn();
  const controller = new PlainController({ initial, authority: bridge, adapter, changed, preview });
  disposers.push(() => controller.destroy());
  return { controller, bridge, onMissing, changed, preview };
}
// A barrier behind the queued rehydrate, with no edit or publish.
async function settle(controller: EditCore<State, object>) { await controller.enqueue(async () => {}); }

describe('W4 project bridge against profile_h', () => {
  it('1: writes document, authoring, status and key atomically, with one past state and no echo', async () => {
    const { controller, bridge } = setup(), external = vi.fn(), snapshots: unknown[] = [];
    bridge.subscribe(external);
    const unsubscribe = subscribeRevision(() => snapshots.push(structuredClone(record()))); disposers.push(unsubscribe);
    await controller.command({ complete: false, label: 'draft' });
    const before = structuredClone(record()), count = past().length;
    await controller.command({ r: 160, complete: true, label: 'finished' });
    expect(past()).toHaveLength(count + 1);
    expect(past()[count].design!.profile_h).toEqual(before);
    expect(record()).toEqual({ document: { points: controller.current.points }, authoring: controller.current, status: 'valid', draftKey: JSON.stringify(controller.current) });
    expect(record().document).not.toEqual(before.document); expect(record().authoring).not.toEqual(before.authoring);
    expect(record().status).not.toBe(before.status);
    expect(snapshots).toHaveLength(2); expect(external).not.toHaveBeenCalled();
  });
  it('2: publishes N live drag frames, commits once, and undo restores both store and editor', async () => {
    const { controller, preview } = setup(); const before = structuredClone(record());
    await controller.beginDrag(['1']);
    for (const r of [145, 155, 170]) {
      await controller.dragTo([{ id: '1', at: { x: 1, y: r } }]);
      expect(record().document!.points[1].r).toBe(r); expect(past()).toHaveLength(0);
    }
    expect(preview).toHaveBeenCalledTimes(3);
    await controller.endDrag(); expect(past()).toHaveLength(1);
    useDesignStore.getState().undo(); await settle(controller);
    expect(record()).toEqual(before); expect(controller.current).toEqual(before.authoring);
    expect(past()).toHaveLength(0);
  });
  it('3: Escape mid-drag restores without adding history or losing the redo stack', async () => {
    const { controller } = setup(); await controller.command({ r: 151 });
    await controller.undo(); await settle(controller);
    const before = structuredClone(record());
    await controller.beginDrag(['1']); await controller.dragTo([{ id: '1', at: { x: 1, y: 180 } }]);
    await controller.endDrag(true);
    expect(record()).toEqual(before); expect(controller.current).toEqual(before.authoring);
    expect(past()).toHaveLength(0); expect(useDesignStore.temporal.getState().futureStates).toHaveLength(1);
    expect(useDesignStore.temporal.getState().isTracking).toBe(true);
  });
  it('4: TopBar store undo/redo rehydrates the open editor and reports causes', async () => {
    const { controller, bridge } = setup(), navigation = vi.fn(); bridge.subscribe(navigation);
    await controller.command({ r: 151 }); const edited = controller.current;
    useDesignStore.getState().undo(); await settle(controller);
    expect(controller.current).toEqual(initial); expect(controller.current).toEqual(record().authoring);
    expect(navigation).toHaveBeenLastCalledWith(initial, 'undo');
    useDesignStore.getState().redo(); await settle(controller);
    expect(controller.current).toEqual(edited); expect(controller.current).toEqual(record().authoring);
    expect(navigation).toHaveBeenLastCalledWith(edited, 'redo');
  });
  it('keeps the current document identity through embedded edits, drag cancellation, undo and redo', async () => {
    const identity = { designId: 'wgd_fixture', lineageId: 'wgl_fixture', baseEditVersion: 7 };
    const documentState = { designUid: 'wgu_01ARZ3NDEKTSV4RRFFQ69G5FAV', designName: 'Profile', identity,
      classification: 'current' as const, openedContentKey: 'v1:kept' };
    useDocumentStore.getState().restoreDocumentState(documentState);
    const { controller } = setup();
    await controller.command({ r: 151 });
    await controller.beginDrag(['1']);
    await controller.dragTo([{ id: '1', at: { x: 1, y: 180 } }]);
    await controller.endDrag(true);
    expect(useDocumentStore.getState()).toMatchObject(documentState);
    useDesignStore.getState().undo(); await settle(controller);
    expect(useDocumentStore.getState()).toMatchObject(documentState);
    useDesignStore.getState().redo(); await settle(controller);
    expect(useDocumentStore.getState()).toMatchObject(documentState);
  });
  it('5: drops a queued solve result when project Undo wins, and rehydrates the solver hook', async () => {
    const { bridge } = authority();
    const restoreState = vi.fn(async (state: State) => state);
    const core = new EditCore<State, object>({ initial, authority: bridge, restore: { cancelGesture: async () => {}, restoreState } });
    core.session = {}; disposers.push(() => core.close(() => {}));
    await core.enqueue(() => core.transact('First', async () => ({ ...initial, label: 'first' })));
    let resolve!: (state: State) => void, started!: () => void;
    const running = new Promise<void>(r => { started = r; });
    const solve = core.enqueue(() => core.transact('Delayed solve', () => { started(); return new Promise<State>(r => { resolve = r; }); }));
    await running;
    useDesignStore.getState().undo();
    resolve({ ...initial, label: 'stale solve', complete: false });
    await solve; await settle(core);
    expect(core.current).toEqual(initial); expect(record().authoring).toEqual(initial);
    expect(past()).toHaveLength(0); expect(restoreState).toHaveBeenLastCalledWith(initial);
  });
  it('keeps pending and queued commands across unrelated edits without notifying or rehydrating', async () => {
    const { bridge } = authority(), listener = vi.fn(), restoreState = vi.fn(async (state: State) => state);
    bridge.subscribe(listener);
    const core = new EditCore<State, object>({ initial, authority: bridge, restore: { cancelGesture: vi.fn(async () => {}), restoreState } });
    core.session = {}; disposers.push(() => core.close(() => {}));
    let resolve!: (state: State) => void, started!: () => void;
    const running = new Promise<void>(r => { started = r; });
    const pending = core.enqueue(() => core.transact('Pending command', () => {
      started(); return new Promise<State>(r => { resolve = r; });
    }));
    await running;
    const queued = core.enqueue(() => core.transact('Queued command', async () => ({ ...core.current, label: 'queued' })));
    const bound = record();
    useDesignStore.getState().updateValue('length', 123);
    expect(record()).not.toBe(bound); expect(record()).toEqual(bound);
    resolve({ ...initial, label: 'pending', complete: false });
    expect(await pending).toMatchObject({ label: 'pending', complete: false });
    expect(await queued).toMatchObject({ label: 'queued', complete: false });
    await settle(core);
    expect(record().authoring).toEqual(core.current); expect(useDesignStore.getState().design.length).toBe(123);
    expect(past()).toHaveLength(3); expect(listener).not.toHaveBeenCalled(); expect(restoreState).not.toHaveBeenCalled();
  });
  it('commits after an unrelated edit between begin and commit', () => {
    const { bridge } = authority(), listener = vi.fn(); bridge.subscribe(listener);
    bridge.begin('Buffered edit'); bridge.update({ ...initial, label: 'buffered' });
    useDesignStore.getState().updateValue('length', 123);
    expect(bridge.commit()).toMatchObject({ label: 'buffered' });
    expect(record().authoring).toEqual(bridge.current); expect(past()).toHaveLength(2);
    expect(useDesignStore.getState().design.length).toBe(123); expect(listener).not.toHaveBeenCalled();
  });
  it('serializes each bound value once per external revision, including changed and absent paths', () => {
    const { bridge } = authority(true), listener = vi.fn(); bridge.subscribe(listener);
    const stringify = vi.spyOn(JSON, 'stringify');
    try {
      for (const updates of [{ length: 123 }, { profile_v: { points: initial.points } }, { length: 124 }]) {
        stringify.mockClear();
        useDesignStore.getState().commitEmbedded(updates, 'amend');
        const { profile_h, profile_v } = useDesignStore.getState().design;
        for (const value of [profile_h, profile_v]) {
          expect(stringify.mock.calls.filter(([input]) => input === value)).toHaveLength(1);
        }
      }
      expect(listener).toHaveBeenCalledExactlyOnceWith(initial, 'external');
    } finally { stringify.mockRestore(); }
  });
  it('detects a real external change in any bound path and discards the buffered edit', () => {
    useDesignStore.getState().commitEmbedded({ profile_v: { points: initial.points } }, 'amend');
    const { bridge } = authority(true), listener = vi.fn(); bridge.subscribe(listener);
    bridge.begin('Buffered edit'); bridge.update({ ...initial, label: 'must not commit' });
    useDesignStore.getState().updateValue('profile_v.points.1.r', 99);
    const count = past().length;
    expect(bridge.commit()).toEqual(initial); expect(record().authoring).toEqual(initial);
    expect(past()).toHaveLength(count); expect(listener).toHaveBeenCalledExactlyOnceWith(initial, 'external');
  });
  it('6: Open rehydrates by path presence; Open without the path and New close', async () => {
    const { controller, onMissing, bridge } = setup(), navigation = vi.fn(); bridge.subscribe(navigation);
    const open = structuredClone(useDesignStore.getState().design);
    const next = { ...initial, label: 'opened', complete: false };
    // An incomplete historic state must be displayed too.
    (open as unknown as Record<string, unknown>).profile_h = editRecord(adapter, next, record().document);
    useDesignStore.getState().replaceDocument(open); await settle(controller);
    expect(controller.current).toEqual(next); expect(navigation).toHaveBeenLastCalledWith(next, 'load');
    expect(onMissing).not.toHaveBeenCalled();
    delete open.profile_h;
    useDesignStore.getState().replaceDocument(open); await settle(controller); expect(onMissing).toHaveBeenCalledOnce();
    resetDesignStore(); await settle(controller); expect(onMissing).toHaveBeenCalledTimes(2);
    await controller.command({ label: 'cannot resurrect a closed object' }); expect(useDesignStore.getState().design.profile_h).toBeUndefined();
  });
  it('7: autosave/reload restores unfinished draft authoring and its last valid document exactly', async () => {
    const { controller } = setup(); await controller.command({ r: 155 });
    await controller.command({ complete: false, r: 165, label: 'unfinished' });
    const draft = JSON.stringify(record()); expect(writeAutosave(localStorage)).toBe(true);
    const saved = localStorage.getItem(AUTOSAVE_KEY)!;
    resetDesignStore(); expect(restoreAutosave(localStorage)).toBe(true); await settle(controller);
    expect(JSON.stringify(record())).toBe(draft); expect(controller.current).toEqual(record().authoring);
    expect(record().document!.points[1].r).toBe(155); expect(controller.current.points[1].r).toBe(165);
    expect(localStorage.getItem(AUTOSAVE_KEY)).toBe(saved); expect(past()).toHaveLength(0);
  });
  it('8: amend adds no entry and survives undo/redo of the step it amends', async () => {
    const { controller } = setup(); await controller.command({ r: 160 });
    await controller.enqueue(() => controller.transact('Typed value', async () => ({ ...controller.current, label: 'typed' }), true));
    expect(past()).toHaveLength(1); expect(record().authoring).toMatchObject({ label: 'typed' });
    useDesignStore.getState().undo(); await settle(controller); expect(controller.current).toEqual(initial);
    useDesignStore.getState().redo(); await settle(controller); expect(controller.current.label).toBe('typed');
    expect(controller.current.points[1].r).toBe(160);
  });
  it('binds two paths in one write and one history step (solver-free membership contract)', async () => {
    const { controller } = setup(true), before = structuredClone(useDesignStore.getState().design);
    await controller.command({ r: 178 });
    expect(past()).toHaveLength(1); expect(useDesignStore.getState().design.profile_v).toEqual(record().document);
    useDesignStore.getState().undo(); await settle(controller);
    expect(useDesignStore.getState().design).toEqual(before); expect(controller.current).toEqual(initial);
    useDesignStore.getState().redo(); await settle(controller);
    expect(useDesignStore.getState().design.profile_v!.points[1].r).toBe(178);
  });
  it('drops old-generation queued work across consecutive loads', async () => {
    const { controller } = setup();
    let release!: () => void;
    const blocker = controller.enqueue(() => new Promise<void>(r => { release = r; }));
    await vi.waitFor(() => expect(release).toBeTypeOf('function'));
    const stale = controller.command({ label: 'from old file' }); const rejected = expect(stale).rejects.toBeInstanceOf(StaleWorkspaceWork);
    const open = structuredClone(useDesignStore.getState().design);
    useDesignStore.getState().replaceDocument(open);
    const next = { ...initial, label: 'newest file' };
    (open as unknown as Record<string, unknown>).profile_h = editRecord(adapter, next, record().document);
    useDesignStore.getState().replaceDocument(open);
    release(); await blocker; await rejected; await settle(controller);
    expect(controller.current).toEqual(next); expect(record().authoring).toEqual(next); expect(past()).toHaveLength(0);
  });
  it('project Undo mid-drag cancels frames, restores the editor and creates no history', async () => {
    const { controller } = setup(); await controller.command({ r: 160 });
    const before = controller.current, history = [...past()];
    await controller.beginDrag(['1']);
    await controller.dragTo([{ id: '1', at: { x: 1, y: 180 } }]);
    useDesignStore.getState().undo(); await settle(controller);
    expect(controller.dragging).toBe(false); expect(controller.current).toEqual(before); expect(record().authoring).toEqual(before);
    // Undo cancels the drag; its live state must never be pushed into pastStates.
    expect(past()).toEqual(history); expect(past()).toHaveLength(1);
    expect(useDesignStore.getState().dragSnapshot).toBeNull();
    useDesignStore.getState().undo(); await settle(controller); expect(controller.current).toEqual(initial);
  });
  it('amend and polar bookkeeping leave an active store drag paused and preserve its one step', () => {
    const store = useDesignStore.getState(); store.beginDrag();
    store.commitEmbedded({ profile_h: editRecord(adapter, { ...initial, label: 'dragging' }, null) }, 'drag');
    store.commitEmbedded({ profile_v: { points: [{ t: 0, r: 13 }, { t: 1, r: 141 }] } }, 'amend');
    expect(useDesignStore.temporal.getState().isTracking).toBe(false);
    recordCommittedAthPolars({ angle_range: [0, 180, 37], distance: 2, norm_angle: 10, inclination: 45, enabled_axes: ['horizontal', 'vertical'] }); expect(useDesignStore.temporal.getState().isTracking).toBe(false);
    expect(useDesignStore.getState().design.extra_blocks).toHaveProperty(['ABEC.Polars:SPL_H']);
    store.endDrag(); expect(past()).toHaveLength(1);
    store.undo(); expect(record().authoring).toEqual(initial);
  });
  it('supports native profile records without adding authoring or bridge fields', async () => {
    useDesignStore.getState().replaceDocument(designForFamily('FREEFORM'));
    const before = structuredClone(useDesignStore.getState().design);
    const bridge = new ProjectHistoryAuthority<State>({ paths: ['profile_h'], onMissing: () => {},
      read: values => values.profile_h ? adapter.seed(values.profile_h as FreeformProfile, undefined) : undefined,
      write: state => ({ profile_h: { points: state.points } }),
    });
    const controller = new PlainController({ initial: bridge.current, adapter, authority: bridge });
    disposers.push(() => controller.destroy());
    await controller.command({ r: 167 });
    expect(useDesignStore.getState().design.profile_h).toEqual({ points: [{ t: 0, r: 12.7 }, { t: 1, r: 167 }] });
    expect(past()).toHaveLength(1);
    useDesignStore.getState().undo(); await settle(controller);
    expect(useDesignStore.getState().design).toEqual(before);
  });
  it('CAS also rehydrates after an unannounced write, without adding a stale step', () => {
    const { bridge } = authority(), external = { ...initial, label: 'another host' }, listener = vi.fn();
    bridge.subscribe(listener); bridge.begin('Pending solve'); bridge.update({ ...initial, label: 'pending' });
    const design = structuredClone(useDesignStore.getState().design);
    (design as unknown as Record<string, unknown>).profile_h = editRecord(adapter, external, record().document);
    useDesignStore.setState({ design });
    const count = past().length;
    expect(bridge.commit()).toEqual(external); expect(record().authoring).toEqual(external);
    expect(past()).toHaveLength(count); expect(listener).toHaveBeenLastCalledWith(external, 'external');
  });
  it('releases a cancelled drag when another writer replaces its bound value', async () => {
    const { controller } = setup(); await controller.beginDrag(['1']);
    await controller.dragTo([{ id: '1', at: { x: 1, y: 160 } }]);
    const external = { ...initial, label: 'external edit' };
    useDesignStore.getState().commitEmbedded({ profile_h: editRecord(adapter, external, record().document) }, 'amend');
    await settle(controller);
    expect(controller.current).toEqual(external); expect(controller.dragging).toBe(false);
    expect(useDesignStore.getState().dragSnapshot).toBeNull(); expect(useDesignStore.temporal.getState().isTracking).toBe(true);
    await controller.endDrag(); expect(record().authoring).toEqual(external);
  });
  it('requires an active snapshot for drag writes', () => {
    expect(() => useDesignStore.getState().commitEmbedded({ R: 999 }, 'drag')).toThrow('No active project drag');
  });
});
