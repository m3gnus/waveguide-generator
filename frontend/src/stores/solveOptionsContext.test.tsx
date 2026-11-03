import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { resetSolveOptionsStore, useImportedSolveOptionsStore, useSolveOptionsStore } from './solveOptions';
import { SolveOptionsStoreProvider, useRunNormAngle } from './solveOptionsContext';
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
afterEach(() => { act(() => root.unmount()); host.remove(); workspaceModeStore.setMode('parametric'); workspaceModeStore.setImportedInput('cad'); });

function Angle({ job }: { job?: Parameters<typeof useRunNormAngle>[0] }) {
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

it('follows the legacy run\'s geometry, and the workspace mode only without a run', () => {
  act(() => workspaceModeStore.setMode('parametric'));
  act(() => root.render(<Angle job={{ config_summary: { geometry_type: 'imported' } }}/>));
  expect(host.textContent).toBe('30');
  act(() => root.render(<Angle job={{ config_summary: { geometry_type: 'osse' } }}/>));
  expect(host.textContent).toBe('10');
  act(() => root.render(<Angle job={{}}/>));
  expect(host.textContent).toBe('10');
  act(() => root.render(<Angle/>));
  act(() => workspaceModeStore.setMode('parametric'));
  expect(host.textContent).toBe('10');
  act(() => workspaceModeStore.setMode('cad'));
  expect(host.textContent).toBe('30');
  act(() => root.render(<Angle job={{ config_summary: { geometry_type: 'osse' } }}/>));
  expect(host.textContent).toBe('10');
  act(() => useSolveOptionsStore.getState().updatePolar({ normAngle: 12 }));
  expect(host.textContent).toBe('12');
  act(() => root.render(<Angle job={{ config_summary: { geometry_type: 'imported' } }}/>));
  expect(host.textContent).toBe('30');
  act(() => useImportedSolveOptionsStore.getState().updatePolar({ normAngle: 45 }));
  expect(host.textContent).toBe('45');
});

it('does not repaint scoped controls when only the imported input changes', () => {
  let renders = 0;
  function Control() {
    const angle = useRunNormAngle();
    renders += 1;
    return <span>{angle}</span>;
  }
  act(() => root.render(<>{Array.from({ length: 64 }, (_, index) => <Control key={index}/>)}</>));
  const initialRenders = renders;
  const html = host.innerHTML;
  const options = JSON.stringify([useSolveOptionsStore.getState(), useImportedSolveOptionsStore.getState()]);
  const started = performance.now();
  for (let index = 0; index < 20; index += 1) {
    act(() => workspaceModeStore.setImportedInput(index % 2 === 0 ? 'file' : 'cad'));
  }
  console.info(JSON.stringify({ benchmark: 'scoped solve options', updates: 20, consumers: 64,
    renders: renders - initialRenders, ms: performance.now() - started, identicalHtml: host.innerHTML === html }));
  expect(host.innerHTML).toBe(html);
  expect(JSON.stringify([useSolveOptionsStore.getState(), useImportedSolveOptionsStore.getState()])).toBe(options);
  expect(renders).toBe(initialRenders);
});

it('does not repaint provided controls on workspace changes and follows a changed provider', () => {
  let renders = 0;
  function Control() {
    const angle = useRunNormAngle();
    renders += 1;
    return <span>{angle}</span>;
  }
  act(() => workspaceModeStore.setMode('parametric'));
  act(() => root.render(<SolveOptionsStoreProvider mode="parametric"><Control/></SolveOptionsStoreProvider>));
  expect(host.textContent).toBe('10');
  const initialRenders = renders;
  act(() => workspaceModeStore.setMode('cad'));
  expect(host.textContent).toBe('10');
  expect(renders).toBe(initialRenders);
  act(() => root.render(<SolveOptionsStoreProvider mode="cad"><Control/></SolveOptionsStoreProvider>));
  expect(host.textContent).toBe('30');
  act(() => useImportedSolveOptionsStore.getState().updatePolar({ normAngle: 42 }));
  expect(host.textContent).toBe('42');
});
