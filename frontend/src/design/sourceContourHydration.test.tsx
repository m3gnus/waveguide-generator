import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { sourceEditorApi, type SourceDocument, type SourceValidation } from '../api/sourceEditor';
import { durableSettings, SETTINGS_NAMESPACES } from '../stores/durableSettings';
import { INITIAL_SOURCE } from './sourceContourEditing';
import { Editor, readSourceDraft } from './SourceContourEditor';

vi.mock('../api/sourceEditor', () => ({ sourceEditorApi: { validate: vi.fn(), presets: vi.fn(), save: vi.fn(), delete: vi.fn(), expand: vi.fn(), split: vi.fn(), export: vi.fn() } }));
let host: HTMLDivElement; let root: Root;
function validation(document: SourceDocument): SourceValidation { return { document, geometry_sha256: 'g', excitation_sha256: 'e', meridian: Object.fromEntries(document.contour.segments.map((p, i) => [p.id, document.contour.points.slice(i, i + 2).map((point) => [point.r_mm, point.z_mm])])) }; }
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks(); localStorage.clear();
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.mocked(sourceEditorApi.validate).mockImplementation(async (doc) => validation(doc)); vi.mocked(sourceEditorApi.presets).mockResolvedValue([]);
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const settle = async () => act(async () => { await vi.advanceTimersByTimeAsync(200); });
function radiusInput() { const label = [...host.querySelectorAll('label')].find((l) => l.querySelector('.field-name')?.textContent === 'Point 2 radius')!; return document.getElementById(label.htmlFor) as HTMLInputElement; }
async function editRadius() { await act(async () => { const field = radiusInput(); field.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, '10'); field.dispatchEvent(new Event('input', { bubbles: true })); }); await act(async () => radiusInput().blur()); }

async function deferredHydration() {
  localStorage.setItem(SETTINGS_NAMESPACES.sourceContourDraft, JSON.stringify(INITIAL_SOURCE));
  let resolve!: (response: Response) => void;
  const uploads: { url: string; body: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string, options?: RequestInit) => url === '/api/settings'
    ? new Promise<Response>((r) => { resolve = r; })
    : (uploads.push({ url, body: options?.body }), Promise.resolve({ ok: true } as Response))));
  const hydration = durableSettings.hydrate({ timeoutMs: 1500 });
  await vi.advanceTimersByTimeAsync(1500); await hydration;
  await act(async () => root.render(<Editor onClose={() => {}}/>)); await settle();
  const remote = structuredClone(INITIAL_SOURCE); remote.contour.physical_source_id = 'remote-source'; remote.contour.points[1].r_mm = 24;
  return { remote, uploads, reply: async () => act(async () => { resolve({ ok: true, json: async () => ({ namespaces: { sourceContourDraft: JSON.stringify(remote) } }) } as Response); await Promise.resolve(); await Promise.resolve(); }) };
}

it('restores a server reply after the startup timeout and uploads the restored identity on the next edit', async () => {
  const pending = await deferredHydration(); expect(Number(radiusInput().value)).toBe(8);
  await pending.reply(); await settle();
  expect(Number(radiusInput().value)).toBe(24); expect(host.querySelector<HTMLInputElement>('input[aria-label="Physical source ID"]')!.value).toBe('remote-source');
  await editRadius(); await settle(); await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(readSourceDraft().contour.physical_source_id).toBe('remote-source');
  expect(pending.uploads.some((u) => u.url === '/api/settings/sourceContourDraft' && JSON.parse(JSON.parse(String(u.body))).contour.physical_source_id === 'remote-source')).toBe(true);
});

it('keeps genuine source edits made while the server hydration reply is in flight', async () => {
  const pending = await deferredHydration(); await editRadius(); await pending.reply(); await settle(); await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(Number(radiusInput().value)).toBe(10); expect(readSourceDraft().contour.physical_source_id).toBe('diaphragm');
  const writes = pending.uploads.filter((u) => u.url === '/api/settings/sourceContourDraft').map((u) => JSON.parse(JSON.parse(String(u.body))) as SourceDocument);
  expect(writes.length).toBeGreaterThan(0); expect(writes.every((doc) => doc.contour.points[1].r_mm === 10 && doc.contour.physical_source_id === 'diaphragm')).toBe(true);
});
