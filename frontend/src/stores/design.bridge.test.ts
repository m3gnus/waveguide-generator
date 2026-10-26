import { beforeEach, describe, expect, it } from 'vitest';
import { AUTOSAVE_KEY, restoreAutosave, writeAutosave } from './autosave';
import { currentDocumentLoad, getAtPath, resetDesignStore, useDesignStore } from './design';
import { resetDocumentStore, useDocumentStore } from './document';
import v035 from './__fixtures__/autosave/v0.3.5.json';
import preW4 from './__fixtures__/autosave/pre-w4.json';
import currentDev from './__fixtures__/autosave/current-dev.json';

beforeEach(() => { localStorage.clear(); resetDesignStore(); resetDocumentStore(); });
describe('autosave compatibility without a project bridge', () => {
  it.each([['v0.3.5 release', v035], ['pre-W4 dev', preW4], ['current dev', currentDev]])('restores %s autosave without changing any persisted design value', (_name, fixture) => {
    const raw = JSON.stringify(fixture); localStorage.setItem(AUTOSAVE_KEY, raw);
    const load = currentDocumentLoad();
    expect(restoreAutosave(localStorage)).toBe(true);
    expect(JSON.stringify(useDesignStore.getState().design)).toBe(JSON.stringify(fixture.design));
    expect(useDesignStore.getState().designRevision).toBe(fixture.designRevision);
    expect(useDesignStore.getState().dragSnapshot).toBeNull();
    expect(useDesignStore.temporal.getState().pastStates).toEqual([]);
    expect(useDesignStore.temporal.getState().futureStates).toEqual([]);
    expect(useDesignStore.temporal.getState().isTracking).toBe(true);
    expect(currentDocumentLoad()).toBe(load + 1);
    expect(useDocumentStore.getState()).toMatchObject({ designName: fixture.designName, identity: fixture.identity,
      classification: fixture.classification, openedContentKey: fixture.openedContentKey });
    if ('designUid' in fixture && 'legacyDesignName' in fixture) {
      expect(useDocumentStore.getState()).toMatchObject({ designUid: fixture.designUid, legacyDesignName: fixture.legacyDesignName });
    }
    expect(localStorage.getItem(AUTOSAVE_KEY)).toBe(raw);
    expect(writeAutosave(localStorage)).toBe(true);
    const rewritten = JSON.parse(localStorage.getItem(AUTOSAVE_KEY)!);
    expect(JSON.stringify(rewritten.design)).toBe(JSON.stringify(fixture.design));
    // Current dev's writer shape stays exactly the same, including the v1 reader field.
    expect(Object.keys(rewritten)).toEqual(Object.keys(currentDev));
    expect(rewritten.savedRevision).toBeNull();
  });
});

it('shares the parameter-panel path reader, including array indices, $last and absent objects', () => {
  const design = useDesignStore.getState().design;
  expect(getAtPath(design, 'source')).toBe(design.source);
  expect(getAtPath(design, 'quadrants.0')).toBe(design.quadrants[0]);
  expect(getAtPath(design, 'quadrants.$last')).toBe(design.quadrants.at(-1));
  expect(getAtPath(design, 'profile_h.points.$last')).toBeUndefined();
  expect(getAtPath(design, undefined)).toBeUndefined();
});
