import { beforeEach, describe, expect, it, vi } from 'vitest';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { outerBodyUpdates } from './outerBodyUpdates';

const store = () => useDesignStore.getState();
const enclosed = (depth: number) => { const design = designForFamily('R-OSSE'); design.enclosure.depth = depth; return design; };
const depthOffered = () => outerBodyUpdates(store().design, 'enclosure', null, true)['enclosure.depth'];

describe('the depth an enclosure comes back with', () => {
  beforeEach(() => resetDesignStore());

  it('is the latest positive depth of this document', () => {
    store().replaceDocument(enclosed(280));
    store().updateValues({ 'enclosure.depth': 400 });
    store().updateValues({ 'enclosure.depth': 0 });
    expect(depthOffered()).toBe(400);
  });

  it('does not follow into another document', () => {
    store().replaceDocument(enclosed(400));
    store().replaceDocument(designForFamily('R-OSSE'));
    expect(depthOffered()).toBe(280);
  });

  it('does not bring a shown run\'s depth back through Undo to the first design', async () => {
    vi.resetModules();
    const fresh = await import('../stores/design');
    const { outerBodyUpdates: offer } = await import('./outerBodyUpdates');
    const run = fresh.designForFamily('R-OSSE'); run.enclosure.depth = 500;
    fresh.useDesignStore.getState().replaceDocument(run, { keepHistory: true });
    fresh.useDesignStore.getState().undo();
    const { design } = fresh.useDesignStore.getState();
    expect(design.enclosure.depth).toBe(0);
    expect(offer(design, 'enclosure', null, true)['enclosure.depth']).toBe(280);
  });

  it('keeps each document its own across Undo over a load', () => {
    store().replaceDocument(enclosed(300));
    store().updateValues({ 'enclosure.depth': 0 });
    // Browsing to a run keeps history, so Undo can cross this load.
    store().replaceDocument(enclosed(500), { keepHistory: true });
    store().updateValues({ 'enclosure.depth': 0 });
    expect(depthOffered()).toBe(500);
    store().undo();
    store().undo();
    expect(store().design.enclosure.depth).toBe(0);
    expect(depthOffered()).toBe(300);
  });
});
