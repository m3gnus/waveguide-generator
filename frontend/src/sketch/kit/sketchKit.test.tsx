import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import type { Sketch } from '../model';
import type { DisplayUI } from '../workspace/types';
import { SketchKit } from './sketchKit';
import { seed } from '../adapters/sourceContour';
import { sourceDisplay, sourceInitialView } from '../adapters/sourceDisplay';
import { INITIAL_SOURCE } from '../../design/sourceContourEditing';

it('reuses the display model during cursor-only frames', async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const state = seed(INITIAL_SOURCE), host = document.createElement('div'), root = createRoot(host);
  const display = vi.fn((sketch: Sketch, ui: DisplayUI) => sourceDisplay({ sketch, meta: state.meta }, ui));
  const presentation = { axes: { x: { label: 'x', unit: 'mm' as const }, y: { label: 'y', unit: 'mm' as const } }, display, initialView: sourceInitialView };
  document.body.append(host);
  try {
    await act(async () => root.render(<SketchKit presentation={presentation} sketch={state.sketch} references={state.meta.references} problems={[]}/>));
    const svg = host.querySelector<SVGSVGElement>('svg[role="application"]')!;
    vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 640, height: 360 } as DOMRect);
    expect(display).toHaveBeenCalledOnce();
    for (const x of [30, 40, 50]) await act(async () => {
      svg.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: x, clientY: 20 }));
    });
    expect(display).toHaveBeenCalledOnce();
    expect(host.querySelector('.sketch-status')?.textContent).not.toContain('x 0.00, y 0.00');
  } finally { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); }
});
