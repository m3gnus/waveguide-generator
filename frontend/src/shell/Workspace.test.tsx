import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SerializedDockview } from 'dockview-core';
import { addDefaultLayout, createDefaultLayout, Workspace } from './Workspace';
import { jobsSocket } from '../api/jobsSocket';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

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

  it('creates the four persisted dock panels without runtime errors', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    await act(async () => {
      root.render(<Workspace resetKey={0}/>);
      await Promise.resolve();
    });
    expect(host.querySelector('.dv-dockview')).not.toBeNull();
    expect(host.textContent).toContain('Parameters');
    expect(host.textContent).toContain('Viewport');
    expect(host.textContent).toContain('Results');
    expect(host.textContent).toContain('Jobs');
    expect(error).not.toHaveBeenCalled();
    error.mockRestore();
  });

  it('states the true size when seeding a measured host, and cannot when unmeasured', () => {
    // Deserializing a layout while dockview still believes it is zero-sized puts
    // every panel at its 100px minimum and never recovers, which is what a
    // first-run visitor saw. The explicit layout() call is the fix.
    const sized = (width: number, height: number) => ({
      getBoundingClientRect: () => ({ width, height }) as DOMRect,
      clientWidth: width,
      clientHeight: height,
    }) as unknown as HTMLElement;
    const api = () => ({ fromJSON: vi.fn(), layout: vi.fn(), width: 0, height: 0 });

    const measured = api();
    addDefaultLayout(measured as never, sized(1200, 800));
    expect(measured.layout).toHaveBeenCalledWith(1200, 800);
    const columns = (measured.fromJSON.mock.calls[0][0] as SerializedDockview).grid.root.data as Array<{ size: number }>;
    expect(columns.map((column) => column.size)).toEqual([300, 580, 320]);

    const unmeasured = api();
    addDefaultLayout(unmeasured as never, sized(0, 0));
    expect(unmeasured.fromJSON).toHaveBeenCalled();
    expect(unmeasured.layout).not.toHaveBeenCalled();
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
                  "activeView": "parameters",
                  "id": "parameters-group",
                  "views": [
                    "parameters",
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
          "jobs": {
            "contentComponent": "jobs",
            "id": "jobs",
            "title": "Jobs",
          },
          "parameters": {
            "contentComponent": "parameters",
            "id": "parameters",
            "title": "Parameters",
          },
          "results": {
            "contentComponent": "results",
            "id": "results",
            "title": "Results",
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
