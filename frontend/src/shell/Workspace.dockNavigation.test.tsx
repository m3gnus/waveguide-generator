/**
 * The real dock, reporting to the navigation seam: which panels are on screen,
 * and which dock input is the user navigating. Panel contents are stand-ins;
 * the layout is the compact one, where every panel shares one tab strip and
 * Results is behind another tab by default.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DockviewComponent } from 'dockview';
import { bindDockNavigation, createDefaultLayout } from './Workspace';
import { resetSolveAttentionForTests, solveAttention } from './solveAttention';
import { navigationGeneration, resetWorkspaceNavigationForTests, workspaceNavigation } from './workspaceNavigation';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

describe('the dock reports to the navigation seam', () => {
  let host: HTMLDivElement;
  let dock: DockviewComponent;
  let unbind: () => void;

  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', ResizeObserverStub);
    resetWorkspaceNavigationForTests();
    resetSolveAttentionForTests();
    host = document.createElement('div');
    document.body.append(host);
    dock = new DockviewComponent(host, {
      createComponent: () => ({ element: document.createElement('div'), init: () => undefined }),
      disableFloatingGroups: true,
    });
    dock.api.fromJSON(createDefaultLayout(700, 700, 'cad'));
    dock.api.layout(700, 700);
    unbind = bindDockNavigation(dock.api, host);
  });

  afterEach(() => {
    unbind();
    dock.dispose();
    host.remove();
    vi.unstubAllGlobals();
  });

  const tab = (title: string) => [...host.querySelectorAll<HTMLElement>('.dv-tab')]
    .find((element) => element.textContent?.includes(title))!;

  it('says Results is hidden behind a tab, and on screen once revealed, with CAD Link covered', () => {
    expect(workspaceNavigation.isVisible('viewport')).toBe(true);
    expect(workspaceNavigation.isVisible('results')).toBe(false);
    solveAttention.armSolve();
    solveAttention.bindRun('job-1');
    expect(solveAttention.resultsReady('job-1')).toBe('revealed');
    expect(workspaceNavigation.isVisible('results')).toBe(true);
    expect(workspaceNavigation.isVisible('viewport')).toBe(false);
    // Fronting a panel on the application's behalf is not the user navigating.
    expect(navigationGeneration()).toBe(0);
  });

  it('counts a tab chosen by pointer as explicit navigation, which disarms the reveal', () => {
    solveAttention.armSolve();
    tab('Jobs').dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(navigationGeneration()).toBe(1);
    solveAttention.bindRun('job-1');
    expect(solveAttention.resultsReady('job-1')).toBe('indicated');
    expect(workspaceNavigation.isVisible('results')).toBe(false);
  });

  it('counts a tab chosen by keyboard, but not other keys or clicks outside the tab strip', () => {
    tab('Jobs').dispatchEvent(new KeyboardEvent('keydown', { key: 'Shift', bubbles: true }));
    host.querySelector<HTMLElement>('.dv-content-container')?.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(navigationGeneration()).toBe(0);
    tab('Jobs').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(navigationGeneration()).toBe(1);
  });
});
