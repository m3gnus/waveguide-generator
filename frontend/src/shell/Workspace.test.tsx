import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SerializedDockview } from 'dockview-core';
import {
  addDefaultLayout,
  createDefaultLayout,
  createResizeLayoutHandler,
  isTrustworthySize,
  nextLayoutAction,
  ReactPanelRenderer,
  LEGACY_LAYOUT_KEY,
  seedSize,
  Workspace,
} from './Workspace';
import { jobsSocket } from '../api/jobsSocket';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const sized = (width: number, height: number) => ({
  getBoundingClientRect: () => ({ width, height }) as DOMRect,
  clientWidth: width,
  clientHeight: height,
}) as unknown as HTMLElement;

const fakeApi = () => ({ fromJSON: vi.fn(), layout: vi.fn(), width: 0, height: 0 });

describe('Workspace', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.stubGlobal('ResizeObserver', ResizeObserverStub);
    localStorage.clear();
    host = document.createElement('div');
    host.style.width = '1200px';
    host.style.height = '800px';
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => {
    act(() => root.unmount());
    vi.unstubAllGlobals();
    host.remove();
  });

  it('creates the five persisted dock panels without runtime errors', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    await act(async () => {
      root.render(<Workspace resetKey={0}/>);
      await Promise.resolve();
    });
    expect(host.querySelector('.dv-dockview')).not.toBeNull();
    expect(host.textContent).toContain('Geometry');
    expect(host.textContent).toContain('Simulation');
    expect(host.textContent).toContain('Viewport');
    expect(host.textContent).toContain('Results');
    expect(host.textContent).toContain('Jobs');
    expect(error).not.toHaveBeenCalled();
    error.mockRestore();
  });

  it('ignores a stored old-key parameter layout and falls back to the new default', async () => {
    localStorage.setItem(LEGACY_LAYOUT_KEY, JSON.stringify({ panels: { parameters: { id: 'parameters', contentComponent: 'parameters', title: 'Parameters' } } }));
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    await act(async () => {
      root.render(<Workspace resetKey={0}/>);
      await Promise.resolve();
    });
    expect(host.textContent).toContain('Geometry');
    expect(host.textContent).toContain('Simulation');
    expect(host.textContent).not.toContain('Parameters');
    expect(error).not.toHaveBeenCalled();
    error.mockRestore();
  });

  it('states the size a seeded layout was built for', () => {
    // Deserializing a layout leaves dockview believing it is whatever size it
    // last measured, which puts every panel at its 100px minimum and never
    // recovers — what a first-run visitor saw. The explicit layout() is the fix.
    const measured = fakeApi();
    addDefaultLayout(measured as never, sized(1200, 800));
    expect(measured.layout).toHaveBeenCalledWith(1200, 800);
    const columns = (measured.fromJSON.mock.calls[0][0] as SerializedDockview).grid.root.data as Array<{ size: number }>;
    expect(columns.map((column) => column.size)).toEqual([300, 580, 320]);

    // An unmeasurable host falls back to the window rather than a fixed guess,
    // so the pinned side panels are not scaled to someone else's screen.
    const windowSize: [number, number] = [window.innerWidth, window.innerHeight - 84];
    const unmeasured = fakeApi();
    addDefaultLayout(unmeasured as never, sized(0, 0));
    expect(unmeasured.layout).toHaveBeenCalledWith(...windowSize);

    const settling = fakeApi();
    addDefaultLayout(settling as never, sized(10, 100));
    expect(settling.layout).toHaveBeenCalledWith(...windowSize);
    const settlingColumns = (settling.fromJSON.mock.calls[0][0] as SerializedDockview).grid.root.data as Array<{ size: number }>;
    expect(settlingColumns[0].size).toBe(300);
    expect(settlingColumns[2].size).toBe(320);
  });

  it('corrects the dock when the host reports a size the mount-time read could not', () => {
    // The host really does measure 10x100 while the surrounding layout resolves:
    // not zero, so it cannot be rejected as unmeasured, and not real either.
    expect(nextLayoutAction([1268, 640], [10, 100])).toBe('layout');
    // Unchanged or unmeasurable sizes do nothing, so this cannot loop.
    expect(nextLayoutAction([1268, 640], [1268, 640])).toBe('none');
    expect(nextLayoutAction([0, 0], [10, 100])).toBe('none');
  });

  it('seeds cold-start JSON once while ResizeObserver measurements only lay out', () => {
    const api = fakeApi();
    addDefaultLayout(api as never, sized(1268, 640));
    // Seeded at a trustworthy size, so no correction is owed and no resize may
    // ever deserialize again — that is what destroys the viewport's WebGL root.
    const resize = createResizeLayoutHandler(api as never, [1268, 640]);

    resize([1268, 640]);
    resize([1272, 640]);
    resize([1272, 640]);
    resize([1600, 900]);

    expect(api.fromJSON).toHaveBeenCalledTimes(1);
    expect(api.layout.mock.calls).toEqual([
      [1268, 640],
      [1272, 640],
      [1600, 900],
    ]);
  });

  it('seeds an unmeasurable dock from the window, not a fixed guess', () => {
    // The seed pins the 300px/320px side panels to whatever size it is handed,
    // and later resizes scale the dock rather than rebuild it — so a fixed
    // 1440x900 guess would leave a 300px panel at 533px on a 2560px monitor.
    expect(seedSize([1268, 640])).toEqual([1268, 640]);
    expect(seedSize([10, 100])).toEqual([window.innerWidth, window.innerHeight - 84]);
    expect(isTrustworthySize([10, 100])).toBe(false);
    expect(isTrustworthySize([1268, 640])).toBe(true);
  });

  it('does not let a deferred dispose unmount a re-initialized panel root', async () => {
    const renderer = new ReactPanelRenderer('jobs');
    document.body.append(renderer.element);

    await act(async () => {
      renderer.init({} as never);
      await Promise.resolve();
    });
    expect(renderer.element.querySelector('.jobs-panel')).not.toBeNull();

    await act(async () => {
      renderer.dispose();
      renderer.init({} as never);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(renderer.element.querySelector('.jobs-panel')).not.toBeNull();

    await act(async () => {
      renderer.dispose();
      await Promise.resolve();
      await Promise.resolve();
    });
    renderer.element.remove();
  });

  it('can close and reset the Jobs panel without transferring global socket ownership', async () => {
    const start = vi.spyOn(jobsSocket, 'start');
    const stop = vi.spyOn(jobsSocket, 'stop');
    await act(async () => {
      root.render(<Workspace resetKey={0}/>);
      await Promise.resolve();
    });
    const jobsTab = [...host.querySelectorAll<HTMLElement>('.dv-default-tab')]
      .find((tab) => tab.querySelector('.dv-default-tab-content')?.textContent === 'Jobs');
    expect(jobsTab).toBeDefined();
    await act(async () => jobsTab?.querySelector<HTMLElement>('.dv-default-tab-action')?.click());
    expect([...host.querySelectorAll<HTMLElement>('.dv-default-tab-content')].some((tab) => tab.textContent === 'Jobs')).toBe(false);
    expect(start).not.toHaveBeenCalled();
    expect(stop).not.toHaveBeenCalled();

    await act(async () => {
      root.render(<Workspace resetKey={1}/>);
      await Promise.resolve();
    });
    expect([...host.querySelectorAll<HTMLElement>('.dv-default-tab-content')].some((tab) => tab.textContent === 'Jobs')).toBe(true);
    expect(start).not.toHaveBeenCalled();
    expect(stop).not.toHaveBeenCalled();
  });

  it('seeds a pinned JSON layout with a flex viewport branch', () => {
    expect(createDefaultLayout(1440, 900)).toMatchInlineSnapshot(`
      {
        "activeGroup": "viewport",
        "grid": {
          "height": 900,
          "orientation": "HORIZONTAL",
          "root": {
            "data": [
              {
                "data": {
                  "activeView": "geometry",
                  "id": "parameters-group",
                  "views": [
                    "geometry",
                    "simulation",
                  ],
                },
                "size": 300,
                "type": "leaf",
              },
              {
                "data": [
                  {
                    "data": {
                      "activeView": "viewport",
                      "id": "viewport-group",
                      "views": [
                        "viewport",
                      ],
                    },
                    "size": 560,
                    "type": "leaf",
                  },
                  {
                    "data": {
                      "activeView": "results",
                      "id": "results-group",
                      "views": [
                        "results",
                      ],
                    },
                    "size": 340,
                    "type": "leaf",
                  },
                ],
                "size": 820,
                "type": "branch",
              },
              {
                "data": {
                  "activeView": "jobs",
                  "id": "jobs-group",
                  "views": [
                    "jobs",
                  ],
                },
                "size": 320,
                "type": "leaf",
              },
            ],
            "size": 900,
            "type": "branch",
          },
          "width": 1440,
        },
        "panels": {
          "geometry": {
            "contentComponent": "geometry",
            "id": "geometry",
            "title": "Geometry",
          },
          "jobs": {
            "contentComponent": "jobs",
            "id": "jobs",
            "title": "Jobs",
          },
          "results": {
            "contentComponent": "results",
            "id": "results",
            "title": "Results",
          },
          "simulation": {
            "contentComponent": "simulation",
            "id": "simulation",
            "title": "Simulation",
          },
          "viewport": {
            "contentComponent": "viewport",
            "id": "viewport",
            "title": "Viewport",
          },
        },
      }
    `);
  });

  it.each([[1440, 900], [1280, 720]])('preserves rail sizes at %d×%d', (width, height) => {
    const layout = createDefaultLayout(width, height);
    const columns = layout.grid.root.data as Array<{ size?: number; data: unknown }>;
    const center = columns[1].data as Array<{ size?: number }>;
    expect(columns.map((column) => column.size)).toEqual([300, width - 620, 320]);
    expect(center.map((row) => row.size)).toEqual([height - 340, 340]);
    expect(layout.grid.width).toBe(width);
  });
});
