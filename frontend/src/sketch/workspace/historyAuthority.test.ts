// @vitest-environment node
import { expect, it, vi } from 'vitest';
import { LocalHistoryAuthority } from './historyAuthority';

it('amends a placed dimension without adding a second undo step', async () => {
  const authority = new LocalHistoryAuthority(0), listener = vi.fn();
  const unsubscribe = authority.subscribe(listener);
  authority.begin('Place'); authority.update(1); authority.commit();
  authority.begin('Value'); authority.update(2); authority.commit({ amend: true });
  expect(listener).not.toHaveBeenCalled();
  await authority.undo(); expect(authority.current).toBe(0); expect(authority.canUndo).toBe(false);
  expect(listener).toHaveBeenLastCalledWith(0, 'undo');
  await authority.undo(true); expect(authority.current).toBe(2);
  expect(listener).toHaveBeenLastCalledWith(2, 'redo'); unsubscribe();
});
it.each([false, true])('keeps stacks intact when restoration fails before or after acceptance: %s', async accepted => {
  const authority = new LocalHistoryAuthority(0, async (_state, accept) => {
    if (accepted) accept(); throw new Error('Cannot restore');
  });
  authority.begin('Edit'); authority.update(1); authority.commit();
  await expect(authority.undo()).rejects.toThrow('Cannot restore');
  expect(authority.current).toBe(1); expect(authority.canUndo).toBe(true); expect(authority.canRedo).toBe(false);
});
