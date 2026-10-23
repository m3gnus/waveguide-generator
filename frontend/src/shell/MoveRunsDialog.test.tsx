import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { RunMove } from '../api/runMoves';
import { MoveRunsDialog, RunMoveRecoveryNotice } from './MoveRunsDialog';
let host: HTMLDivElement, root: Root;
const target = { key: 'design:wgu_test', name: '250728solana' };
const preview = (selected = ['identity']): RunMove => ({
  id: 'preview-id', key: target.key, name: target.name, phase: 'planned', error: null,
  folder: '/WG/runs/Old', target: '/WG/runs/250728solana', selected, gather: true, rename: false,
  candidates: [
    { job_id: 'identity', source: '/older/Runs/model/1_run', destination: '/WG/runs/250728solana/1_run', preselected: true, reason: 'Same design or model', files: 4, bytes: 1024, method: 'rename' },
    { job_id: 'name-only', source: '/older/Runs/250728solana2/2_run', destination: '/WG/runs/250728solana/2_run', preselected: false, reason: 'Name only — check before moving', files: 3, bytes: 2048, method: 'copy_verify_remove' },
  ],
  summary: { runs: selected.length, files: 4, bytes: 1024, fusion_files: 2, duplicates: 1, saved_bytes: 100, rename_runs: 0, rename_files: 0, rename_bytes: 0 },
  operations: selected.map(id => ({ src: `/source/${id}`, dst: `/dest/${id}`, kind: 'run', state: 'pending', method: 'rename' })),
  old_folders: ['/older/Runs/250728solana2'],
});
const json = (body: unknown) => new Response(JSON.stringify(body));
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  fetcher = vi.fn(async (path: string, options?: RequestInit) => {
    if (path.endsWith('/status')) return json({ reports: [] });
    const body = options?.body ? JSON.parse(options.body as string) : {};
    if (path.endsWith('/preview')) return json(preview(body.selected));
    if (path.endsWith('/undo')) return json({ ...preview(), phase: 'undone' });
    return json({ ...preview(), phase: 'done' });
  });
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
const button = (text: string) => [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(item => item.textContent === text)!;
const mount = async (onClose = vi.fn()) => { await act(async () => root.render(<MoveRunsDialog target={target} onClose={onClose}/>)); };
it('only previews on open, shows paths/counts/methods and respects server selections', async () => {
  await mount();
  const body = document.body.textContent!;
  expect(body).toContain('/older/Runs/model/1_run');
  expect(body).toContain('/WG/runs/250728solana/1_run');
  expect(body).toContain('1 run · 4 files · 1.0 KB');
  expect(body).toContain('Moved on this drive (instant)');
  expect(body).toContain('Copied and verified, then originals removed');
  expect(body).toContain('whole move before removing the originals');
  expect([...document.querySelectorAll<HTMLInputElement>('input')].map(input => input.checked)).toEqual([true, false, true, false]);
  expect(fetcher.mock.calls.every(([path]) => !String(path).endsWith('/execute'))).toBe(true);
  expect(button('Move 1 run').disabled).toBe(false);
});
it('refreshes the preview on selection and confirms only its saved id', async () => {
  await mount();
  await act(async () => document.querySelectorAll<HTMLInputElement>('input')[3]!.click());
  const last = fetcher.mock.calls.filter(([path]) => String(path).endsWith('/preview')).at(-1)!;
  expect(JSON.parse(last[1].body)).toEqual({ ...target, selected: ['identity', 'name-only'], gather: true, rename: false });
  await act(async () => button('Move 2 runs').click());
  const execution = fetcher.mock.calls.find(([path]) => String(path).endsWith('/execute'))!;
  expect(execution[0]).toBe('/api/workspace/run-moves/preview-id/execute');
  expect(JSON.parse(execution[1].body)).toEqual({ confirmed: true });
  expect(document.body.textContent).toContain('Move complete');
  await act(async () => button('Undo this move').click());
  expect(document.body.textContent).toContain('Move undone');
});
it('does not execute on cancellation', async () => {
  const close = vi.fn(); await mount(close);
  await act(async () => button('Cancel').click());
  expect(close).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('/execute'))).toBe(false);
});
it('shows actionable refusal and disables confirmation', async () => {
  fetcher.mockImplementation(async (path: string) => path.endsWith('/status') ? json({ reports: [] })
    : new Response(JSON.stringify({ detail: 'Download them in Finder first' }), { status: 409 }));
  await mount();
  expect(document.querySelector('[role="alert"]')?.textContent).toContain('Download them in Finder first');
  expect(button('Apply folder changes').disabled).toBe(true);
});
it('surfaces recovered moves and offers retry without executing a new plan', async () => {
  fetcher.mockImplementation(async (path: string) => {
    if (path.endsWith('/status')) return json({ reports: [{ ...preview(), phase: 'verify_all', error: 'Verification failed; originals kept' }] });
    if (path.endsWith('/recover')) return json({ reports: [{ ...preview(), phase: 'done' }] });
    return json(preview());
  });
  await mount();
  expect(document.body.textContent).toContain('Move needs attention');
  await act(async () => button('Retry recovery').click());
  expect(document.body.textContent).toContain('Move complete');
  expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('/execute'))).toBe(false);
});

it('announces startup recovery before opening the tool', async () => {
  fetcher.mockImplementation(async () => json({ reports: [], recovered: [{ ...preview(), phase: 'done' }] }));
  await act(async () => root.render(<RunMoveRecoveryNotice/>));
  expect(document.body.textContent).toContain('Recovered 1 earlier-runs move');
  expect(document.body.textContent).toContain('Review move into /WG/runs/250728solana');
});
