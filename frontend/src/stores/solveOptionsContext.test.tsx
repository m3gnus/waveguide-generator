import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { resetSolveOptionsStore, useImportedSolveOptionsStore, useSolveOptionsStore } from './solveOptions';
import { useRunNormAngle } from './solveOptionsContext';
import { workspaceModeStore } from './workspaceMode';

let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  resetSolveOptionsStore();
  useSolveOptionsStore.getState().updatePolar({ normAngle: 10 });
  useImportedSolveOptionsStore.getState().updatePolar({ normAngle: 30 });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); workspaceModeStore.setMode('parametric'); });

function Angle({ job }: { job?: { config_summary?: Record<string, unknown> } | null }) {
  return <span>{useRunNormAngle(job)}</span>;
}

it('follows the run\'s geometry, and the workspace mode only without a run', () => {
  act(() => workspaceModeStore.setMode('parametric'));
  act(() => root.render(<Angle job={{ config_summary: { geometry_type: 'imported' } }}/>));
  expect(host.textContent).toBe('30');
  act(() => root.render(<Angle job={{ config_summary: { geometry_type: 'osse' } }}/>));
  expect(host.textContent).toBe('10');
  act(() => root.render(<Angle/>));
  expect(host.textContent).toBe('10');
  act(() => workspaceModeStore.setMode('cad'));
  expect(host.textContent).toBe('30');
  act(() => root.render(<Angle job={{ config_summary: { geometry_type: 'osse' } }}/>));
  expect(host.textContent).toBe('10');
  // It follows the field as it is typed.
  act(() => useSolveOptionsStore.getState().updatePolar({ normAngle: 12 }));
  expect(host.textContent).toBe('12');
});
