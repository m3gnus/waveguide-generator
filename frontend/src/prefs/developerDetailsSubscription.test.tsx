import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { preferencesStore, STORAGE_VERSION, useDeveloperDetails } from './preferences';

let root: Root;
let host: HTMLDivElement;
let renders: number;

function DetailsFlag() {
  const shown = useDeveloperDetails();
  renders += 1;
  return <span>{String(shown)}</span>;
}

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  preferencesStore.resetForTests();
  renders = 0;
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  preferencesStore.resetForTests();
});

it('does not repaint detail consumers for unrelated preference edits', () => {
  act(() => root.render(<>{Array.from({ length: 64 }, (_, index) => <DetailsFlag key={index}/>)}</>));
  const initialRenders = renders;
  const html = host.innerHTML;
  const started = performance.now();
  for (let index = 0; index < 20; index += 1) {
    act(() => preferencesStore.update({ smoothing: index % 2 === 0 ? '1/3' : 'none', runSequenceNext: index + 2 }));
  }
  console.info(JSON.stringify({ benchmark: 'developer details', updates: 20, consumers: 64,
    renders: renders - initialRenders, ms: performance.now() - started, identicalHtml: host.innerHTML === html }));
  expect(host.innerHTML).toBe(html);
  expect(preferencesStore.getSnapshot()).toMatchObject({ smoothing: 'none', runSequenceNext: 21 });
  expect(renders).toBe(initialRenders);
});

it('updates detail consumers for direct edits and durable preference hydration', () => {
  act(() => root.render(<DetailsFlag/>));
  expect(host.textContent).toBe('false');
  act(() => preferencesStore.update({ showDeveloperDetails: true }));
  expect(host.textContent).toBe('true');
  expect(renders).toBe(2);
  act(() => preferencesStore.adoptDurable(JSON.stringify({ version: STORAGE_VERSION,
    preferences: { showDeveloperDetails: false, smoothing: '1/3' } })));
  expect(host.textContent).toBe('false');
  expect(renders).toBe(3);
});
