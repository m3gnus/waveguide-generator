import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sourceEditorApi, type SourceDocument, type SourceValidation } from '../api/sourceEditor';
import { durableSettings, namespaceStorage, SETTINGS_NAMESPACES } from '../stores/durableSettings';
import { INITIAL_SOURCE } from './sourceContourEditing';
import { Editor, readSourceDraft, SourceContourDialog, SourceContourEditor } from './SourceContourEditor';

vi.mock('../api/sourceEditor', () => ({ sourceEditorApi: { validate: vi.fn(), presets: vi.fn(), save: vi.fn(), delete: vi.fn(), expand: vi.fn(), split: vi.fn(), export: vi.fn() } }));
const storage = namespaceStorage('sourceContourDraft');
function validation(document: SourceDocument): SourceValidation { return { document, geometry_sha256: 'geometry', excitation_sha256: 'excitation', meridian: Object.fromEntries(document.contour.segments.map((p, i) => [p.id, document.contour.points.slice(i, i + 2).map((point) => [point.r_mm, point.z_mm])])) }; }

describe('source editor interactions', () => {
  let host: HTMLDivElement; let root: Root;
  beforeEach(() => {
    vi.useFakeTimers(); vi.clearAllMocks();
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    storage.removeItem('');
    vi.mocked(sourceEditorApi.validate).mockImplementation(async (doc) => validation(doc));
    vi.mocked(sourceEditorApi.presets).mockResolvedValue([]);
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); vi.useRealTimers(); vi.restoreAllMocks(); });
  const mount = async () => { await act(async () => root.render(<Editor onClose={() => {}}/>)); await settle(); };
  const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(200); }); };
  const button = (text: string) => [...host.querySelectorAll('button')].find((b) => b.textContent === text)!;
  const input = (text: string) => { const direct = host.querySelector<HTMLInputElement>(`input[aria-label="${text}"]`); if (direct) return direct; const label = [...host.querySelectorAll('label')].find((l) => l.querySelector('.field-name')?.textContent === text)!; return document.getElementById(label.htmlFor) as HTMLInputElement; };
  async function enter(text: string, value: string) { await act(async () => { const field = input(text); field.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value); field.dispatchEvent(new Event('input', { bubbles: true })); }); await act(async () => input(text).blur()); }

  it('edits table coordinates, keeps IDs, persists draft, and can undo', async () => {
    await mount(); await enter('Point 2 radius', '10'); await settle();
    expect(sourceEditorApi.validate).toHaveBeenLastCalledWith(expect.objectContaining({ contour: expect.objectContaining({ points: [{ id: 'pole', r_mm: 0, z_mm: 0 }, { id: 'rim', r_mm: 10, z_mm: 0 }] }) }));
    expect(readSourceDraft().contour.points[1].r_mm).toBe(10);
    await act(async () => button('Undo edit').click()); await settle();
    expect(readSourceDraft()).toEqual(INITIAL_SOURCE);
  });
  it('keeps zero/negative weights as moving and refuses stale validation for an invalid draft', async () => {
    await mount(); await enter('Patch 1 weight', '-0.5'); await settle();
    expect(readSourceDraft().drive.weights.piston).toBe(-.5);
    await enter('Patch 1 weight', '0'); await settle();
    expect(readSourceDraft().contour.segments[0].role).toBe('moving');
    vi.mocked(sourceEditorApi.validate).mockRejectedValueOnce(new Error('Invalid rim radius'));
    await enter('Point 2 radius', '-1');
    expect(button('Export STEP + mesh bundle').disabled).toBe(true);
    await settle(); expect(host.textContent).toContain('Invalid rim radius');
    expect(host.querySelector('svg path')?.getAttribute('class')).toContain('draft');
  });
  it('ignores an older accepted response after a newer draft was refused', async () => {
    await mount(); let resolve!: (value: SourceValidation) => void;
    vi.mocked(sourceEditorApi.validate).mockImplementationOnce(() => new Promise((r) => { resolve = r; }));
    await enter('Point 2 radius', '12'); await settle();
    const old = readSourceDraft();
    vi.mocked(sourceEditorApi.validate).mockRejectedValueOnce(new Error('Newest draft invalid'));
    await enter('Point 2 radius', '-2'); await settle();
    await act(async () => resolve(validation(old)));
    expect(button('Export STEP + mesh bundle').disabled).toBe(true);
    expect(host.textContent).toContain('Newest draft invalid');
  });
  it('saves, loads and updates presets with the loaded revision', async () => {
    const saved = { id: 'saved', revision: 'r1', name: 'My source', document: structuredClone(INITIAL_SOURCE) };
    vi.mocked(sourceEditorApi.save).mockResolvedValue(saved);
    await mount(); await enter('Preset name', 'My source');
    await act(async () => button('Save new preset').click());
    expect(sourceEditorApi.save).toHaveBeenCalledWith('My source', INITIAL_SOURCE);
    await enter('Point 2 radius', '9'); await settle();
    await act(async () => button('Load My source').click()); await settle();
    expect(readSourceDraft()).toEqual(INITIAL_SOURCE);
    await act(async () => button('Update loaded preset').click());
    expect(sourceEditorApi.save).toHaveBeenLastCalledWith('My source', INITIAL_SOURCE, saved);
  });
  it('drawing creates explicit points and blocks export until the drawing is finished', async () => {
    await mount(); await act(async () => button('Draw lines').click());
    expect(button('Export STEP + mesh bundle').disabled).toBe(true);
    const svg = host.querySelector('svg')!;
    vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 640, height: 260 } as DOMRect);
    const pointer = async (x: number, y: number) => act(async () => svg.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: x, clientY: y })));
    await pointer(30, 190); await pointer(280, 170); await pointer(600, 100);
    await act(async () => button('Finish at rim').click()); await settle();
    const doc = readSourceDraft();
    expect(doc.contour.points).toHaveLength(3); expect(doc.contour.points[0].r_mm).toBe(0); expect(doc.contour.points[2].z_mm).toBe(0);
    expect(doc.contour.segments.every((p) => p.kind === 'line')).toBe(true);
    expect(Object.keys(doc.drive.weights)).toHaveLength(2);
  });
  it('recovers malformed cached drafts without crashing', () => {
    storage.setItem('', JSON.stringify({ contour: { points: [] } }));
    expect(readSourceDraft()).toEqual(INITIAL_SOURCE);
  });

  it('recovers a cached arc with a missing center before rendering its fields', async () => {
    const invalid = structuredClone(INITIAL_SOURCE);
    invalid.contour.segments[0].kind = 'arc';
    invalid.contour.segments[0].center_mm = null;
    storage.setItem('', JSON.stringify(invalid));
    expect(readSourceDraft()).toEqual(INITIAL_SOURCE);
    await mount(); expect(host.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it('adopts late durable-settings hydration before the next point edit', async () => {
    let adopt!: (raw: string | null) => void;
    vi.spyOn(durableSettings, 'subscribe').mockImplementation((namespace, listener) => {
      expect(namespace).toBe('sourceContourDraft'); adopt = listener; return () => {};
    });
    await mount();
    const remote = structuredClone(INITIAL_SOURCE); remote.contour.physical_source_id = 'remote-source'; remote.contour.points[1].r_mm = 24;
    await act(async () => { localStorage.setItem(SETTINGS_NAMESPACES.sourceContourDraft, JSON.stringify(remote)); adopt(JSON.stringify(remote)); });
    await settle(); expect(Number(input('Point 2 radius').value)).toBe(24);
    await enter('Point 2 radius', '10'); await settle();
    expect(readSourceDraft().contour.physical_source_id).toBe('remote-source');
    expect(readSourceDraft().contour.points[1].r_mm).toBe(10);
  });

  it('fits an accepted semicircle using its entire canonical meridian', async () => {
    const doc = structuredClone(INITIAL_SOURCE);
    doc.contour.segments[0] = { ...doc.contour.segments[0], kind: 'arc', center_mm: [4, 0], direction: 'ccw' };
    storage.setItem('', JSON.stringify(doc));
    vi.mocked(sourceEditorApi.validate).mockImplementation(async (document) => ({ ...validation(document), meridian: { piston: Array.from({ length: 65 }, (_, i) => { const t = Math.PI + i / 64 * Math.PI; return [4 + 4 * Math.cos(t), 4 * Math.sin(t)]; }) } }));
    await mount(); await act(async () => button('Fit drawing').click());
    const values = [...host.querySelector('svg path')!.getAttribute('d')!.matchAll(/[ML]([\d.e+-]+),([\d.e+-]+)/g)];
    expect(values).toHaveLength(65);
    expect(values.every((v) => Number(v[1]) >= 0 && Number(v[1]) <= 640 && Number(v[2]) >= 0 && Number(v[2]) <= 260)).toBe(true);
  });

  it('keeps the modal and editor-local fields when its geometry-panel launcher unmounts', async () => {
    await act(async () => root.render(<><SourceContourEditor/><SourceContourDialog key="persistent"/></>));
    await act(async () => button('Edit source contour…').click()); await settle();
    const name = document.querySelector<HTMLInputElement>('input[aria-label="Preset name"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'Unsaved name'); name.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => root.render(<><SourceContourDialog key="persistent"/></>));
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    expect(document.querySelector<HTMLInputElement>('input[aria-label="Preset name"]')!.value).toBe('Unsaved name');
    await act(async () => [...document.querySelectorAll('button')].find((b) => b.textContent === 'Close')!.click());
  });
});
