import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { sourceEditorApi, type SourceAssemblyDocument } from '../api/sourceEditor';
import { durableSettings, SETTINGS_NAMESPACES } from '../stores/durableSettings';
import { AssemblyEditor, INITIAL_ASSEMBLY, parseAssemblyDraft } from './SourceAssemblyEditor';

vi.mock('../api/sourceEditor', () => ({ sourceEditorApi: { validateAssembly: vi.fn(), validate: vi.fn(), presets: vi.fn() } }));
vi.mock('./nativeAssemblyPreparation', () => ({ prepareNativeAssembly: vi.fn() }));
let host: HTMLDivElement; let root: Root;
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks(); localStorage.clear();
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.mocked(sourceEditorApi.validateAssembly).mockResolvedValue({ geometry_sha256: 'g', passage_contract: null, horn_section_mm: {} });
  vi.mocked(sourceEditorApi.validate).mockImplementation(async (document) => ({ document, geometry_sha256: 'g', excitation_sha256: 'e', meridian: Object.fromEntries(document.contour.segments.map((patch, i) => [patch.id, document.contour.points.slice(i, i + 2).map((point) => [point.r_mm, point.z_mm])])) }));
  vi.mocked(sourceEditorApi.presets).mockResolvedValue([]);
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const settle = async () => act(async () => { await vi.advanceTimersByTimeAsync(200); });
const button = (text: string) => [...host.querySelectorAll('button')].find((b) => b.textContent === text)!;
const read = () => parseAssemblyDraft(localStorage.getItem(SETTINGS_NAMESPACES.sourceAssemblyDraft)!);
function weightInput() {
  const label = [...host.querySelectorAll('label')].find((l) => l.querySelector('.field-name')?.textContent === 'Patch 1 weight')!;
  return document.getElementById(label.htmlFor) as HTMLInputElement;
}
async function editWeight(value: string) {
  await act(async () => {
    const input = weightInput(); input.focus();
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => weightInput().blur()); await settle();
}
async function deferredHydration() {
  localStorage.setItem(SETTINGS_NAMESPACES.sourceAssemblyDraft, JSON.stringify(INITIAL_ASSEMBLY));
  const unrelated = 'standalone contour remains separate';
  localStorage.setItem(SETTINGS_NAMESPACES.sourceContourDraft, unrelated);
  let resolve!: (response: Response) => void;
  const uploads: { url: string; body: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string, options?: RequestInit) => url === '/api/settings'
    ? new Promise<Response>((r) => { resolve = r; })
    : (uploads.push({ url, body: options?.body }), Promise.resolve({ ok: true } as Response))));
  const hydration = durableSettings.hydrate({ timeoutMs: 1500 });
  await vi.advanceTimersByTimeAsync(1500); await hydration;
  await act(async () => root.render(<AssemblyEditor onClose={() => {}}/>)); await settle();
  const remote = structuredClone(INITIAL_ASSEMBLY); remote.dimensions.width_mm = 82;
  remote.horn.drive.weights.piston = .75; remote.woofer.drive.weights.piston = .5;
  return { remote, uploads, unrelated, reply: async () => act(async () => {
    resolve({ ok: true, json: async () => ({ namespaces: { sourceAssemblyDraft: JSON.stringify(remote) } }) } as Response);
    await Promise.resolve(); await Promise.resolve();
  }) };
}
it('adopts late server assembly settings when the draft has no local changes', async () => {
  const pending = await deferredHydration(); await pending.reply(); await settle();
  expect(read()).toEqual(pending.remote);
  await act(async () => button('Edit horn contour…').click()); await settle();
  expect(Number(weightInput().value)).toBe(.75);
});
it.each(['horn', 'woofer'] as const)('preserves nested %s edits, undo and subsequent assembly uploads across delayed hydration', async (source) => {
  const pending = await deferredHydration();
  await act(async () => button(`Edit ${source} contour…`).click()); await settle();
  await editWeight('-.25');
  expect(read()[source].drive.weights.piston).toBe(-.25);
  await pending.reply(); await settle();
  expect(Number(weightInput().value)).toBe(-.25);
  expect(button('Use contour in assembly').disabled).toBe(false);
  expect(read().dimensions.width_mm).toBe(INITIAL_ASSEMBLY.dimensions.width_mm);
  await act(async () => button('Undo edit').click()); await settle();
  expect(Number(weightInput().value)).toBe(1);
  await editWeight('-.5');
  await act(async () => button('Use contour in assembly').click()); await settle();
  expect(button('Prepare assembly in WG').disabled).toBe(false);
  await act(async () => button(`Edit ${source} contour…`).click()); await settle();
  expect(Number(weightInput().value)).toBe(-.5);
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  const writes = pending.uploads.filter((u) => u.url === '/api/settings/sourceAssemblyDraft')
    .map((u) => JSON.parse(JSON.parse(String(u.body))) as SourceAssemblyDocument);
  expect(writes.length).toBeGreaterThan(0);
  expect(writes.at(-1)![source].drive.weights.piston).toBe(-.5);
  expect(writes.every((doc) => doc.dimensions.width_mm === INITIAL_ASSEMBLY.dimensions.width_mm)).toBe(true);
  expect(localStorage.getItem(SETTINGS_NAMESPACES.sourceContourDraft)).toBe(pending.unrelated);
  // Hydration may migrate the independent cache, but nested edits never alter it.
  expect(pending.uploads.filter((u) => u.url === '/api/settings/sourceContourDraft')
    .every((u) => JSON.parse(String(u.body)) === pending.unrelated)).toBe(true);
});
