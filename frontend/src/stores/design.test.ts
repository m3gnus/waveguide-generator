import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  registerRevisionTimer,
  resetDesignStore,
  seedDesign,
  subscribeRevision,
  useDesignStore,
  type RevisionEvent,
} from './design';

describe('design store revision semantics', () => {
  beforeEach(() => resetDesignStore());

  it('groups a drag into one undo step while revisions remain monotonic', () => {
    const revisions: RevisionEvent[] = [];
    const unsubscribe = subscribeRevision((event) => revisions.push(event));
    const initialRevision = useDesignStore.getState().designRevision;
    useDesignStore.getState().beginDrag();
    useDesignStore.getState().updateField('a', 43);
    useDesignStore.getState().updateField('a', 44);
    useDesignStore.getState().updateField('a', 45);
    useDesignStore.getState().endDrag();

    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
    expect(useDesignStore.getState().designRevision).toBe(initialRevision + 3);
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().design.a).toBe(seedDesign.a);
    expect(useDesignStore.getState().designRevision).toBe(initialRevision + 4);
    expect(revisions.map((event) => event.revision)).toEqual([2, 3, 4, 5]);
    unsubscribe();
  });

  it('undo during a drag cancels pending timers and restores the drag snapshot', () => {
    const cancel = vi.fn();
    const unregister = registerRevisionTimer(cancel);
    useDesignStore.getState().beginDrag();
    useDesignStore.getState().updateField('R', 175);
    useDesignStore.getState().undo();

    expect(cancel).toHaveBeenCalledOnce();
    expect(useDesignStore.getState().design.R).toBe(seedDesign.R);
    expect(useDesignStore.getState().dragSnapshot).toBeNull();
    expect(useDesignStore.temporal.getState().isTracking).toBe(true);
    unregister();
  });

  it('bumps the revision for edit, undo, redo, load, and family switch', () => {
    const seen: number[] = [];
    const unsubscribe = subscribeRevision((event) => seen.push(event.revision));
    useDesignStore.getState().updateField('a', 43);
    useDesignStore.getState().undo();
    useDesignStore.getState().redo();
    useDesignStore.getState().loadDesign({ ...structuredClone(seedDesign), a: 41 });
    useDesignStore.getState().setFamily('OSSE');
    expect(seen).toEqual([2, 3, 4, 5, 6]);
    unsubscribe();
  });

  it.each(['family', 'load'] as const)('finalizes active drag history before %s replacement', (operation) => {
    useDesignStore.getState().beginDrag();
    useDesignStore.getState().updateField('R', 175);
    if (operation === 'family') useDesignStore.getState().setFamily('OSSE');
    else useDesignStore.getState().loadDesign({ ...structuredClone(seedDesign), R: 190 });

    expect(useDesignStore.getState().dragSnapshot).toBeNull();
    expect(useDesignStore.temporal.getState().isTracking).toBe(true);
    expect(useDesignStore.temporal.getState().pastStates.length).toBeGreaterThanOrEqual(2);
  });
});
