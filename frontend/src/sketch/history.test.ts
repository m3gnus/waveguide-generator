// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { History } from './history';
import { emptySketch } from './model';

describe('snapshot transactions', () => {
  it('groups a drag and cancels Esc by restoring the exact snapshot reference', () => {
    const initial = emptySketch(), h = new History(initial), a = { ...initial, nextId: 2 }, b = { ...initial, nextId: 3 };
    h.begin('drag'); h.update(a); h.update(b); h.commit();
    expect(h.undo()).toBe(initial); expect(h.redo()).toBe(b);
    h.begin('drag'); h.update(a); expect(h.cancel()).toBe(b);
    expect(h.undo()).toBe(initial);
  });
  it('stores geometry and metadata together, caps 100 entries and clears redo on a new edit', () => {
    const h = new History({ sketch: emptySketch(), meta: { weight: 0 } });
    for (let i = 1; i <= 105; i++) { h.begin('edit'); h.update({ ...h.current, meta: { weight: i } }); h.commit(); }
    for (let i = 0; i < 100; i++) h.undo();
    expect(h.current.meta.weight).toBe(5); expect(h.canUndo).toBe(false); expect(h.canRedo).toBe(true);
    h.begin('new'); h.update({ ...h.current, meta: { weight: -1 } }); h.commit(); expect(h.canRedo).toBe(false);
  });
  it('does not create no-op entries and rejects unbalanced transactions', () => {
    const h = new History(emptySketch()); h.begin('no-op'); h.commit(); expect(h.canUndo).toBe(false);
    expect(() => h.commit()).toThrow(); expect(() => h.cancel()).toThrow(); expect(() => h.update(emptySketch())).toThrow();
    h.begin('active'); expect(() => h.begin('nested')).toThrow(); expect(() => h.undo()).toThrow(); expect(() => h.redo()).toThrow(); h.cancel();
  });
});
