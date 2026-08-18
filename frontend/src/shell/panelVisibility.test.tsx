import { act, createElement, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { dockviewPanelVisibility, PanelVisibilityContext, useVisibleRedraw, type PanelVisibility } from './panelVisibility';

const draw = vi.fn<(data: number[]) => void>();

/** Stands in for a chart: it records every time it is actually drawn. */
function Trace({ data }: { data: number[] }) {
  draw(data);
  return null;
}

function Panel({ data }: { data: number[] }): ReactElement {
  return useVisibleRedraw(createElement(Trace, { data }));
}

/** A dock panel whose visibility the test drives, standing in for a tab strip. */
function controllableVisibility(initial: boolean) {
  const listeners = new Set<() => void>();
  let visible = initial;
  const source: PanelVisibility = {
    get isVisible() {
      return visible;
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
  return {
    source,
    set(next: boolean) {
      visible = next;
      act(() => listeners.forEach((listener) => listener()));
    },
  };
}

describe('covered panel redraw deferral', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    draw.mockClear();
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
  });

  function publish(data: number[], source?: PanelVisibility) {
    const panel = createElement(Panel, { data });
    act(() => root.render(source
      ? createElement(PanelVisibilityContext.Provider, { value: source }, panel)
      : panel));
  }

  it('accumulates updates behind a covered tab and catches up once, from the latest', () => {
    const visibility = controllableVisibility(true);
    publish([1], visibility.source);
    expect(draw).toHaveBeenCalledTimes(1);
    expect(draw).toHaveBeenLastCalledWith([1]);

    // The tab goes behind a sibling. Data keeps arriving, as a live solve's
    // does; none of it is allowed to reach the chart.
    visibility.set(false);
    publish([1, 2], visibility.source);
    publish([1, 2, 3], visibility.source);
    publish([1, 2, 3, 4], visibility.source);
    expect(draw).toHaveBeenCalledTimes(1);

    // Brought forward: one repaint, holding the newest snapshot rather than
    // replaying the three it missed.
    visibility.set(true);
    expect(draw).toHaveBeenCalledTimes(2);
    expect(draw).toHaveBeenLastCalledWith([1, 2, 3, 4]);
  });

  it('still draws once for a panel that mounts already covered', () => {
    const visibility = controllableVisibility(false);
    publish([1], visibility.source);
    expect(draw).toHaveBeenCalledTimes(1);
    expect(draw).toHaveBeenLastCalledWith([1]);

    publish([1, 2], visibility.source);
    expect(draw).toHaveBeenCalledTimes(1);
  });

  it('redraws freely with no dock around it, as in a detail dialog', () => {
    publish([1]);
    publish([1, 2]);
    expect(draw).toHaveBeenCalledTimes(2);
    expect(draw).toHaveBeenLastCalledWith([1, 2]);
  });
});

describe('dockview visibility adapter', () => {
  it('reads the live panel flag and releases its subscription', () => {
    const listeners = new Set<(event: { isVisible: boolean }) => void>();
    const api = {
      isVisible: true,
      onDidVisibilityChange: (listener: (event: { isVisible: boolean }) => void) => {
        listeners.add(listener);
        return { dispose: () => { listeners.delete(listener); } };
      },
    };
    const visibility = dockviewPanelVisibility(api);
    expect(visibility.isVisible).toBe(true);

    // dockview mutates the api object in place rather than replacing it, so a
    // captured boolean would pin the panel to whatever it was at mount.
    api.isVisible = false;
    expect(visibility.isVisible).toBe(false);

    const listener = vi.fn();
    const unsubscribe = visibility.subscribe(listener);
    listeners.forEach((notify) => notify({ isVisible: false }));
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    expect(listeners.size).toBe(0);
  });
});
