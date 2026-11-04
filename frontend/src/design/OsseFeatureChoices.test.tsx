import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import * as designIo from '../api/designIo';
import { designForFamily, resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { OsseFeatureChoices } from './OsseFeatureChoices';

let host: HTMLDivElement;
let root: Root;
let changed: ReturnType<typeof vi.fn<() => void>>;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  changed = vi.fn();
  vi.spyOn(designIo, 'serializeDesignDocument').mockResolvedValue({ text: '', suggestedFilename: '' });
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  resetDesignStore();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function render() {
  act(() => root.render(<OsseFeatureChoices design={useDesignStore.getState().design} onChanged={changed} />));
}

async function choose(key = 'mouth', value = 'arc') {
  const select = host.querySelector<HTMLSelectElement>(`#osse-feature-${key}`)!;
  await act(async () => {
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

function resolveTo(values: Record<string, number | null>) {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(values), { status: 200 }));
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

it.each([
  ['throat', 'authored', 'OSSE-ADAPTER'],
  ['mouth', 'arc', 'OSSE-ARC'],
  ['mouth', 'roundover', 'OSSE-ROUNDOVER'],
  ['axial', 'on', 'OSSE-AXIAL'],
])('switches the default OSSE design using %s without expression resolution', async (key, value, formula) => {
  const original = designForFamily('OSSE');
  useDesignStore.getState().replaceDocument(original);
  const fetcher = resolveTo({});
  const confirm = vi.spyOn(window, 'confirm');
  render();
  await choose(key, value);
  expect(useDesignStore.getState().design.formula).toBe(formula);
  expect(fetcher).not.toHaveBeenCalled();
  expect(confirm).not.toHaveBeenCalled();
  act(() => useDesignStore.getState().undo());
  expect(useDesignStore.getState().design).toEqual(original);
});

it.each(['L', 'a0'])('confirms a constant %s expression before switching and restores it with Undo', async (path) => {
  const original = designForFamily('OSSE');
  original._expressions = { [path]: { raw: '2 * 12.7', value: null } };
  useDesignStore.getState().replaceDocument(original);
  const wire = JSON.stringify(serializeDesign(original));
  const fetcher = resolveTo({ [path]: 25.4 });
  const confirm = vi.spyOn(window, 'confirm').mockImplementation((message) => {
    expect(message).toContain(`${path} = 2 * 12.7 will become 25.4`);
    expect(useDesignStore.getState().design).toEqual(original);
    return true;
  });
  render();
  await choose();
  expect(fetcher).toHaveBeenCalledOnce();
  expect(confirm).toHaveBeenCalledOnce();
  expect(useDesignStore.getState().design).toMatchObject({ formula: 'OSSE-ARC', [path]: 25.4 });
  expect(changed).toHaveBeenCalledOnce();
  act(() => useDesignStore.getState().undo());
  expect(JSON.stringify(serializeDesign(useDesignStore.getState().design))).toBe(wire);
  act(() => useDesignStore.getState().redo());
  expect(useDesignStore.getState().design[path as 'L' | 'a0']).toBe(25.4);
});

it.each([
  ['throat', 'authored', 'OSSE-ADAPTER'], ['mouth', 'arc', 'OSSE-ARC'],
  ['mouth', 'roundover', 'OSSE-ROUNDOVER'], ['axial', 'on', 'OSSE-AXIAL'],
])('converts numeric ATH source spellings for %s = %s only after confirmation', async (key, value, formula) => {
  const original = designIo.hydrateDesignDocument({
    formula: 'OSSE', text_import_version: 'ath-2026-09a-v3',
    L: { value: 120, raw: '120' }, r0: { value: 12.7, raw: '12.7' },
    a: { value: 40, raw: '40' }, a0: { value: 10, raw: '10' },
    k: { value: 1, raw: '1' }, s: { value: 0, raw: '0' }, scale: { value: 1, raw: '1' },
    mesh: { throat_resolution: { value: 6, raw: '6' } },
  });
  useDesignStore.getState().replaceDocument(original);
  resolveTo({ L: 120, r0: 12.7, a: 40, a0: 10, k: 1, scale: 1, 'mesh.throat_resolution': 6 });
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  render();
  await choose(key, value);
  expect(confirm).toHaveBeenCalledOnce();
  expect(useDesignStore.getState().design).toMatchObject({ formula, L: 120, a0: 10, mesh: { throat_resolution: 6 } });
  act(() => useDesignStore.getState().undo());
  expect(useDesignStore.getState().design).toEqual(original);
});

it.each(['L', 'a0', 'a', 'mesh.throat_resolution'])('names the unresolved %s expression, even when it has a cached numeric sample', async (path) => {
  const original = designForFamily('OSSE');
  original._expressions = { [path]: { raw: '12 + sin(p)', value: 12 } };
  useDesignStore.getState().replaceDocument(original);
  resolveTo({ [path]: null });
  const confirm = vi.spyOn(window, 'confirm');
  render();
  await choose();
  expect(host.querySelector('[role="alert"]')?.textContent).toContain(`${path}) uses the expression “12 + sin(p)”`);
  expect(host.textContent).toContain('Enter a number');
  expect(confirm).not.toHaveBeenCalled();
  expect(designIo.serializeDesignDocument).not.toHaveBeenCalled();
  expect(useDesignStore.getState().design).toEqual(original);
});

it('keeps the full design and history when conversion is cancelled', async () => {
  const original = designForFamily('OSSE');
  original._expressions = { L: { raw: '60 * 2', value: 120 } };
  useDesignStore.getState().replaceDocument(original);
  resolveTo({ L: 120 });
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  render();
  await choose();
  expect(confirm).toHaveBeenCalledOnce();
  expect(useDesignStore.getState().design).toEqual(original);
  expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
  expect(changed).not.toHaveBeenCalled();
});

it('keeps expressions if the resolved native candidate fails validation', async () => {
  const original = designForFamily('OSSE');
  original._expressions = { a0: { raw: '45 * 2', value: 90 } };
  useDesignStore.getState().replaceDocument(original);
  resolveTo({ a0: 90 });
  vi.mocked(designIo.serializeDesignDocument).mockRejectedValue(new Error('Throat angle must stay below 80 degrees. Reduce Throat angle.'));
  const confirm = vi.spyOn(window, 'confirm');
  render();
  await choose();
  expect(confirm).not.toHaveBeenCalled();
  expect(useDesignStore.getState().design).toEqual(original);
  expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
  expect(host.textContent).toContain('Reduce Throat angle');
});

it('ignores expressions in fields that the native switch resets', async () => {
  const original = designForFamily('OSSE');
  original._expressions = { s: { raw: '.5 + .1*sin(p)', value: .5 }, n: { raw: '4 + sin(p)', value: 4 } };
  useDesignStore.getState().replaceDocument(original);
  const fetcher = resolveTo({});
  render();
  await choose();
  expect(fetcher).not.toHaveBeenCalled();
  expect(useDesignStore.getState().design.formula).toBe('OSSE-ARC');
});

it('confirms actual missing-field defaults instead of copying the displayed template values', async () => {
  const original = { ...designForFamily('OSSE'), L: 130, scale: 2, _absent: ['L', 'scale'] };
  useDesignStore.getState().replaceDocument(original);
  resolveTo({ L: 120, scale: 1 });
  const confirm = vi.spyOn(window, 'confirm').mockImplementation((message) => {
    expect(message).toContain('Horn length (L) is unset; its current default will become 120');
    expect(message).toContain('Scale (scale) is unset; its current default will become 1');
    return true;
  });
  render();
  await choose();
  expect(confirm).toHaveBeenCalledOnce();
  expect(useDesignStore.getState().design).toMatchObject({ formula: 'OSSE-ARC', L: 120, scale: 1 });
  act(() => useDesignStore.getState().undo());
  expect(useDesignStore.getState().design).toEqual(original);
});

it('discards expression resolution if the design changed while it was pending', async () => {
  const original = designForFamily('OSSE');
  original._expressions = { L: { raw: '60 * 2', value: 120 } };
  useDesignStore.getState().replaceDocument(original);
  let finish!: (response: Response) => void;
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise<Response>((resolve) => { finish = resolve; })));
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  render();
  await choose();
  act(() => useDesignStore.getState().updateField('L', 190));
  await act(async () => { finish(new Response(JSON.stringify({ L: 120 }), { status: 200 })); });
  expect(confirm).not.toHaveBeenCalled();
  expect(designIo.serializeDesignDocument).not.toHaveBeenCalled();
  expect(useDesignStore.getState().design).toMatchObject({ formula: 'OSSE', L: 190 });
});
