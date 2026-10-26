// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';
import { PlainController } from './plainController';
import type { DomainAdapter } from './types';
import type { XY } from '../solver/types';

type State = { id: string; at: XY }[];
type Command = { id: string; at: XY };
const initial: State = [{ id: 'a', at: { x: 1, y: 2 } }, { id: 'b', at: { x: 4, y: 5 } }];
const adapter: DomainAdapter<State, State, Command> = {
  id: 'points', axes: { x: { label: 'X', unit: 'mm' }, y: { label: 'Y', unit: 'mm' } },
  seed: doc => doc, lower: document => ({ ok: true, document }), status: () => ({ kind: 'valid' }), serializeAuthoring: state => state,
  apply: (state, command) => command.at.x < 0 ? { error: 'Outside bounds' } : state.map(point => point.id === command.id ? command : point),
  remove: () => [], tools: [], properties: () => [], hits: () => [], marquee: () => [],
  display: state => ({ items: state.map(point => ({ ...point, kind: 'point', role: 'anchor', editable: true })), context: [], bounds: () => ({ minX: 0, maxX: 10, minY: 0, maxY: 10 }) }),
  dragSet: (_state, ids) => ({ ids, move: (state, delta) => state.filter(p => ids.includes(p.id)).map(p => ({ id: p.id, at: { x: p.at.x + delta.x, y: p.at.y + delta.y } })) }),
};
function setup() { const changed = vi.fn(), preview = vi.fn(); return { controller: new PlainController({ initial, adapter, changed, preview }), changed, preview }; }
describe('solver-free controller', () => {
  it('is ready without a solver and resolves queued reducers against the latest state', async () => {
    const { controller, changed } = setup();
    await controller.ready;
    const first = controller.command({ id: 'a', at: { x: 2, y: 3 } });
    const second = controller.command(state => ({ id: 'a', at: { x: state[0].at.x + 1, y: 4 } }));
    await Promise.all([first, second]);
    expect(controller.current[0].at).toEqual({ x: 3, y: 4 }); expect(changed).toHaveBeenCalledTimes(2);
    await controller.undo(); expect(controller.current[0].at.x).toBe(2);
    await controller.undo(); expect(controller.current).toBe(initial); controller.destroy();
  });
  it('commits a multi-frame group drag once and restores every point on undo', async () => {
    const { controller, changed } = setup();
    await controller.beginDrag(['a', 'b']);
    for (const x of [2, 3, 6]) await controller.dragTo([{ id: 'a', at: { x, y: 4 } }]);
    expect(changed).not.toHaveBeenCalled();
    await controller.endDrag(); expect(changed).toHaveBeenCalledTimes(1);
    expect(controller.current.map(p => p.at)).toEqual([{ x: 6, y: 4 }, { x: 9, y: 7 }]);
    await controller.undo(); expect(controller.current).toBe(initial);
    await controller.undo(true); expect(controller.current[0].at.x).toBe(6); controller.destroy();
  });
  it.each(['cancel', 'unchanged', 'rejected'])('%s drag restores the start without a commit', async mode => {
    const { controller, changed, preview } = setup();
    await controller.beginDrag(['a']);
    if (mode === 'cancel') await controller.dragTo([{ id: 'a', at: { x: 8, y: 9 } }]);
    if (mode === 'rejected') expect(await controller.dragTo([{ id: 'a', at: { x: -1, y: 0 } }])).toMatchObject({ stopped: true });
    await controller.endDrag(mode === 'cancel');
    expect(controller.current).toBe(initial); expect(controller.authority.canUndo).toBe(false);
    expect(changed).not.toHaveBeenCalled(); expect(preview).toHaveBeenLastCalledWith(initial); controller.destroy();
  });
  it('rolls back a rejected batch and continues its queue', async () => {
    const { controller, changed } = setup();
    await expect(controller.command([{ id: 'a', at: { x: 2, y: 3 } }, { id: 'b', at: { x: -1, y: 5 } }])).rejects.toThrow('Outside bounds');
    expect(controller.current).toBe(initial); expect(changed).not.toHaveBeenCalled();
    await controller.command({ id: 'a', at: { x: 3, y: 4 } }); expect(changed).toHaveBeenCalledTimes(1); controller.destroy();
  });
  it('preview publishes only on confirmation; rehydrate discards a preview or drag', async () => {
    const { controller, changed } = setup();
    await controller.preview({ id: 'a', at: { x: 3, y: 4 } }); expect(controller.current).toBe(initial); expect(changed).not.toHaveBeenCalled();
    await controller.confirm(); expect(changed).toHaveBeenCalledTimes(1);
    await controller.preview({ id: 'a', at: { x: 7, y: 8 } }); await controller.rehydrate(initial); await controller.confirm();
    expect(controller.current).toBe(initial); expect(changed).toHaveBeenCalledTimes(1);
    await controller.beginDrag(['a']); await controller.dragTo([{ id: 'a', at: { x: 7, y: 8 } }]); await controller.rehydrate(initial);
    expect(controller.current).toBe(initial); controller.destroy();
  });
});
