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

function Angle({ job }: { job?: { solve_options?: { polar_config?: unknown } } | null }) {
  return <span>{useRunNormAngle(job)}</span>;
}

it('keeps a run reference when a different imported draft is shown or edited', () => {
  const job = { solve_options: { polar_config: { norm_angle: 0 } } };
  act(() => root.render(<Angle job={job}/>));
  expect(host.textContent).toBe('0');
  act(() => workspaceModeStore.setMode('cad'));
  act(() => useImportedSolveOptionsStore.getState().updatePolar({ normAngle: 45 }));
  expect(host.textContent).toBe('0');
  act(() => useSolveOptionsStore.getState().updatePolar({ normAngle: 12 }));
  expect(host.textContent).toBe('0');
  act(() => root.render(<Angle job={{ solve_options: { polar_config: { norm_angle: 17 } } }}/>));
  expect(host.textContent).toBe('17');
});

it('uses the portable reference for legacy runs and the scoped draft without a run', () => {
  act(() => root.render(<Angle job={{}}/>));
  expect(host.textContent).toBe('5');
  act(() => root.render(<Angle/>));
  act(() => workspaceModeStore.setMode('parametric'));
  expect(host.textContent).toBe('10');
  act(() => workspaceModeStore.setMode('cad'));
  expect(host.textContent).toBe('30');
});
