import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { workspaceModeStore } from '../stores/workspaceMode';
import { workspaceNavigation } from './workspaceNavigation';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DockviewComponent, type SerializedDockview } from 'dockview';
import { createDefaultLayout, LAYOUT_KEY, LAYOUT_MODE_KEY, layoutMode, restoreDisposition, Workspace } from './Workspace';
import { migrateWorkspaceLayout } from './layoutMigration';

type Node = SerializedDockview['grid']['root'];
type Group = Exclude<Node['data'], Node[]>;
const groups = (node: Node): Group[] => Array.isArray(node.data) ? node.data.flatMap(groups) : [node.data];
function oldLayout(width = 1440): SerializedDockview {
  const layout = createDefaultLayout(width, 900, 'cad');
  layout.panels.cadlink = { id: 'cadlink', contentComponent: 'cadlink', title: 'Imported mesh' };
  const right = groups(layout.grid.root).find(group => group.views.includes('jobs'))!;
  right.views.push('cadlink'); right.activeView = 'cadlink'; layout.activeGroup = right.id;
  return layout;
}
let dock: DockviewComponent;
let host: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  host = document.createElement('div'); document.body.append(host);
  dock = new DockviewComponent(host, { createComponent: () => ({ element: document.createElement('div'), init() {} }) });
});
afterEach(() => { dock.dispose(); host.remove(); vi.unstubAllGlobals(); });

describe('saved imported-workspace layout migration', () => {
  it('keeps the owner’s groups, split sizes and active Jobs tab', () => {
    const saved = oldLayout();
    const migrated = migrateWorkspaceLayout(saved);
    const expected = structuredClone(saved);
    delete expected.panels.cadlink;
    const right = groups(expected.grid.root).find(group => group.views.includes('jobs'))!;
    right.views = ['jobs']; right.activeView = 'jobs';
    expect(migrated).toEqual(expected);
    expect(groups(saved.grid.root).at(-1)?.views).toEqual(['jobs', 'cadlink']);
    dock.api.fromJSON(migrated); dock.api.layout(1440, 900);
    expect(dock.api.getPanel('jobs')?.api.isVisible).toBe(true);
    expect(dock.api.getPanel('cadlink')).toBeUndefined();
    expect(dock.api.groups.map(group => group.width)).toContain(320);
  });

  it.each([720, 960, 1440])('restores old responsive layouts at %d px', width => {
    const saved = migrateWorkspaceLayout(oldLayout(width));
    dock.api.fromJSON(saved); dock.api.layout(width, 900);
    expect(dock.api.panels.map(panel => panel.id).sort()).toEqual(['geometry', 'jobs', 'results', 'simulation', 'viewport']);
    expect(dock.api.getPanel('jobs')?.api.isVisible).toBe(true);
  });

  it('prunes a standalone legacy panel and repairs the active group without resetting custom sizes', () => {
    const saved = oldLayout();
    const root = saved.grid.root.data as Node[];
    const original = structuredClone(root);
    root.push({ type: 'branch', size: 187, data: [{ type: 'leaf', size: 900, data: { id: 'old-only', views: ['cadlink'], activeView: 'cadlink' } }] });
    groups(saved.grid.root).find(group => group.views.includes('jobs'))!.views = ['jobs'];
    saved.activeGroup = 'old-only';
    const result = migrateWorkspaceLayout(saved);
    expect((result.grid.root.data as Node[]).map(node => node.size)).toEqual(original.map(node => node.size));
    expect(groups(result.grid.root).map(group => group.id)).not.toContain('old-only');
    expect(groups(result.grid.root).some(group => group.id === result.activeGroup)).toBe(true);
    dock.api.fromJSON(result);
    expect(dock.api.totalPanels).toBe(5);
  });

  it('preserves custom group order and active tabs when another tab was selected', () => {
    const saved = oldLayout(960);
    const analysis = groups(saved.grid.root).find(group => group.views.includes('jobs'))!;
    analysis.views = ['cadlink', 'jobs', 'results']; analysis.activeView = 'results';
    const result = migrateWorkspaceLayout(saved);
    expect(groups(result.grid.root).find(group => group.id === analysis.id)).toMatchObject({ views: ['jobs', 'results'], activeView: 'results' });
    expect(migrateWorkspaceLayout(result)).toEqual(result);
  });

  it('reads the existing v4 mode key without invalidating an owner layout', () => {
    expect(LAYOUT_MODE_KEY).toBe('wg2.dockview.mode.v4');
    expect(restoreDisposition('wide', 1440)).toBe('restore');
    expect(restoreDisposition(null, 1440)).toBe('restore');
    expect(restoreDisposition('compact', 1440)).toBe('rebuild');
  });
  it('removes legacy references from floating, popout and edge groups too', () => {
    const saved = oldLayout();
    saved.floatingGroups = [{ data: { id: 'float', views: ['cadlink'], activeView: 'cadlink' }, position: { left: 0, top: 0, width: 300, height: 400 } }];
    saved.popoutGroups = [{ grid: { ...structuredClone(saved.grid), root: { type: 'leaf', data: { id: 'popout', views: ['cadlink'], activeView: 'cadlink' } } }, position: null }];
    saved.edgeGroups = { left: { size: 200, visible: true, group: { id: 'edge', views: ['cadlink'], activeView: 'cadlink' } } };
    const result = migrateWorkspaceLayout(saved);
    expect(result.floatingGroups).toEqual([]);
    expect(result.popoutGroups).toEqual([]);
    expect(result.edgeGroups).toEqual({});
    expect(JSON.stringify(result)).not.toContain('cadlink');
  });

  it('rejects a layout with only the removed tab so Workspace can use its default', () => {
    const saved = oldLayout();
    saved.grid.root = { type: 'leaf', data: { id: 'only', views: ['cadlink'], activeView: 'cadlink' } };
    expect(() => migrateWorkspaceLayout(saved)).toThrow('Saved layout has no panels');
  });

  it('migrates the persisted v4 layout and mode through the actual Workspace restore path', async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    workspaceModeStore.setMode('parametric');
    const saved = oldLayout(window.innerWidth);
    const right = groups(saved.grid.root).find(group => group.views.includes('jobs'))!;
    right.id = 'owner-custom-right'; saved.activeGroup = right.id;
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(saved));
    localStorage.setItem(LAYOUT_MODE_KEY, layoutMode(window.innerWidth));
    const container = document.createElement('div'); host.append(container);
    const root = createRoot(container);
    try {
      await act(async () => root.render(<Workspace resetKey={0}/>));
      expect(workspaceNavigation.isVisible('jobs')).toBe(true);
      expect(container.querySelector('.dock-panel-cadlink')).toBeNull();
      expect(container.querySelectorAll('.dock-panel-geometry')).toHaveLength(1);
    } finally {
      await act(async () => root.unmount());
    }
    const stored = JSON.parse(localStorage.getItem(LAYOUT_KEY)!) as SerializedDockview;
    expect(groups(stored.grid.root).find(group => group.id === right.id)).toMatchObject({ views: right.views.filter(id => id !== 'cadlink'), activeView: 'jobs' });
    expect(localStorage.getItem(LAYOUT_MODE_KEY)).toBe(layoutMode(window.innerWidth));
    expect(stored.panels).not.toHaveProperty('cadlink');
    localStorage.clear();
  });

});
