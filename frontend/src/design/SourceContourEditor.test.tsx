import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { sourceEditorApi, type SourceDocument, type SourceValidation } from '../api/sourceEditor';
import { durableSettings, namespaceStorage, SETTINGS_NAMESPACES } from '../stores/durableSettings';
import { INITIAL_SOURCE } from './sourceContourEditing';
import { Editor, readSourceDraft, readSourceAuthoring, readSourceAttachment, SourceContourDialog, SourceContourEditor } from './SourceContourEditor';

vi.mock('../api/sourceEditor', () => ({ sourceEditorApi: { validate: vi.fn(), presets: vi.fn(), save: vi.fn(), delete: vi.fn(), expand: vi.fn(), split: vi.fn(), export: vi.fn() } }));
import * as sourceAdapter from '../sketch/adapters/sourceContour';
import { expandedPreset } from '../sketch/adapters/presetTestSupport';
import { presetDefaults, presetLabels } from '../sketch/adapters/sourcePreset';
import { sketchViewKey } from '../sketch/viewPreferences';
import { realSessionReady } from '../sketch/testSupport';
import { SketchSession } from '../sketch/session';
import { readFileSync } from 'node:fs';
const tokensCss = new TextDecoder().decode(readFileSync('src/styles/tokens.css'));
const editorCss = new TextDecoder().decode(readFileSync('src/design/sourceContourEditor.css'));
const canvasCss = new TextDecoder().decode(readFileSync('src/sketch/sketchCanvas.css'));
beforeAll(realSessionReady);
const storage = namespaceStorage('sourceContourDraft');
function validation(document: SourceDocument): SourceValidation { return { document, geometry_sha256: 'geometry', excitation_sha256: 'excitation', meridian: Object.fromEntries(document.contour.segments.map((p, i) => [p.id, document.contour.points.slice(i, i + 2).map((point) => [point.r_mm, point.z_mm])])) }; }

describe('source editor interactions', () => {
  let host: HTMLDivElement; let root: Root;
  beforeEach(() => {
    vi.useFakeTimers(); vi.clearAllMocks();
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    storage.removeItem(''); localStorage.removeItem(sketchViewKey);
    vi.mocked(sourceEditorApi.validate).mockImplementation(async (doc) => validation(doc));
    vi.mocked(sourceEditorApi.presets).mockResolvedValue([]);
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); vi.useRealTimers(); vi.restoreAllMocks(); });
  const mount = async () => { await act(async () => root.render(<Editor onClose={() => {}}/>)); await settle(); };
  // Classic drawing is reachable only as the fallback for newer sketch data, which it keeps untouched.
  const mountClassic = async (doc = INITIAL_SOURCE) => {
    storage.setItem('', JSON.stringify({ version: 'wg-source-authoring-v1', document: doc, sketch: { version: 'wg-sketch-v99' }, meta: {} }));
    await mount();
  };
  const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(200); }); };
  const button = (text: string) => [...host.querySelectorAll('button')].find((b) => (b.getAttribute('aria-label') ?? b.textContent) === text)!;
  const input = (text: string) => { const direct = host.querySelector<HTMLInputElement>(`input[aria-label="${text}"]`); if (direct) return direct; const label = [...host.querySelectorAll('label')].find((l) => l.querySelector('.field-name')?.textContent === text)!; return document.getElementById(label.htmlFor) as HTMLInputElement; };
  async function enter(text: string, value: string) { await act(async () => { const field = input(text); field.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value); field.dispatchEvent(new Event('input', { bubbles: true })); }); await act(async () => input(text).blur()); }


  const limitX = () => Number(host.querySelector('.sketch-limit > path:not(.sketch-context-hit)')!.getAttribute('d')!.match(/M([^,]+)/)![1]);
  const pointer = async (element: Element, type = 'pointerdown', x = 0, y = 0) => act(async () => { element.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, buttons: type === 'pointerup' ? 0 : 1 })); });
  async function deletePoleLock() {
    const lock = [...host.querySelectorAll('[aria-label^="Constraint pointOnLine"]')][0];
    await pointer(lock); await act(async () => button('Delete selection').click());
  }
  async function splitCanvas() {
    await act(async () => host.querySelector('[aria-label="Sketch drawing"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 's', bubbles: true })));
    const line = host.querySelector('.sketch-entity[data-id="piston"]')!, values = line.getAttribute('d')!.match(/[\d.e+-]+/g)!.map(Number);
    await pointer(line, 'pointerdown', (values[0] + values[2]) / 2, (values[1] + values[3]) / 2);
  }
  it('fits a conical horn below the mouth clearance with no rule problems', async () => {
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (shape, dimensions) => validation(expandedPreset(shape, dimensions)));
    await mount();
    const selector = host.querySelector<HTMLSelectElement>('[aria-label="Source attachment"]')!;
    await act(async () => { selector.value = 'horn'; selector.dispatchEvent(new Event('change', { bubbles: true })); }); await settle();
    await act(async () => button('Fit to limit').click()); await settle();
    const document = readSourceDraft(), author = readSourceAuthoring(document);
    expect(document.contour.points.at(-1)!.r_mm).toBeCloseTo(20 - .1 - 1e-6, 8);
    expect(sourceAdapter.rules(author.sketch, author.meta)).toEqual([]);
    await act(async () => button('Fit all (Shift+F)').click());
    const marker = host.querySelector('.sketch-limit')!, label = marker.querySelector('text');
    expect(marker.querySelector('title')?.textContent).toContain('Mouth r 20.00');
    expect(marker.querySelector('title')?.textContent).toContain('max rim r = mouth − 0.10 mm');
    if (label) expect(Number(label.getAttribute('x')) + label.lastChild!.textContent!.length * 3.2).toBeLessThanOrEqual(640);
  });
  it('snaps a dragged horn rim to the allowed maximum while leaving the marker at the mouth', async () => {
    await mount();
    const selector = host.querySelector<HTMLSelectElement>('[aria-label="Source attachment"]')!;
    await act(async () => { selector.value = 'horn'; selector.dispatchEvent(new Event('change', { bubbles: true })); }); await settle();
    const svg = host.querySelector('[aria-label="Sketch drawing"]')!;
    const lineX = limitX();
    const rim = host.querySelector('.sketch-point[data-id="rim"]')!, y = Number(rim.getAttribute('cy'));
    await pointer(rim, 'pointerdown', Number(rim.getAttribute('cx')), y);
    await pointer(svg, 'pointermove', lineX - 3, y); await settle();
    await pointer(svg, 'pointerup'); await settle();
    expect(readSourceDraft().contour.points.at(-1)!.r_mm).toBeCloseTo(20 - .1 - 1e-6, 8);
    expect(host.querySelector('.sketch-domain-problem')).toBeNull();
    expect(limitX()).toBe(lineX);
  });
  it('keeps the attachment limit through Undo and Redo without adding sketch history', async () => {
    await mount(); await enter('Patch 1 weight', '.5'); await settle();
    await enter('Aperture radius attachment', '18'); await settle();
    await act(async () => button('Undo').click()); await settle();
    expect(Number(input('Aperture radius attachment').value)).toBe(18);
    expect(host.querySelector('.sketch-limit > title')?.textContent).toContain('Aperture r 18.00');
    expect(readSourceAuthoring(readSourceDraft()).meta.limit).toEqual({ kind: 'aperture', radius: 18 });
    expect(Number(input('Patch 1 weight').value)).toBe(1);
    await act(async () => button('Redo').click()); await settle();
    expect(host.querySelector('.sketch-limit > title')?.textContent).toContain('Aperture r 18.00');
    expect(Number(input('Patch 1 weight').value)).toBe(.5);
    await act(async () => button('Undo').click()); await settle();
    await enter('Aperture radius attachment', '19'); await settle();
    expect(button('Redo').disabled).toBe(false);
    await act(async () => button('Redo').click()); await settle();
    expect(host.querySelector('.sketch-limit > title')?.textContent).toContain('Aperture r 19.00');
    expect(Number(input('Patch 1 weight').value)).toBe(.5);
  });
  it('shows Valid contour alongside a soft limit warning while export stays enabled', async () => {
    await mount(); await enter('Point 2 radius', '15'); await settle();
    expect(host.querySelector('.sketch-domain-problem')?.textContent).toBe('Rim exceeds the baffle aperture');
    expect(button('Export STEP + mesh bundle').disabled).toBe(false);
    expect(host.querySelector('.source-validation')?.textContent).toContain('Valid contour');
  });
  it('highlights table rows without scrolling on canvas selection or hover', async () => {
    await mount();
    const row = host.querySelector<HTMLTableRowElement>('[data-row-id="piston"]')!;
    const scrolled = vi.fn(); row.scrollIntoView = scrolled;
    const curve = host.querySelector('.sketch-entity[data-id="piston"]')!;
    await pointer(curve, 'pointermove');
    expect(row.classList.contains('hovered')).toBe(true);
    expect(scrolled).not.toHaveBeenCalled();
    await pointer(curve);
    expect(scrolled).not.toHaveBeenCalled();
    scrolled.mockClear();
    await pointer(host.querySelector('.sketch-point[data-id="rim"]')!, 'pointermove');
    expect(scrolled).not.toHaveBeenCalled();
  });
  it('selects point 8 of an 11-point contour without opening Points or moving the editor', async () => {
    const doc = structuredClone(INITIAL_SOURCE);
    doc.contour.points = Array.from({ length: 11 }, (_, i) => ({ id: `point${i}`, r_mm: i, z_mm: 0 }));
    doc.contour.segments = doc.contour.points.slice(1).map((p, i) => ({ ...INITIAL_SOURCE.contour.segments[0], id: `segment${i}`, start: doc.contour.points[i].id, end: p.id }));
    doc.drive.weights = Object.fromEntries(doc.contour.segments.map(s => [s.id, 1]));
    storage.setItem('', JSON.stringify(doc)); await mount();
    const editor = host.querySelector<HTMLElement>('.source-editor')!;
    const canvas = host.querySelector('[aria-label="Sketch drawing"]')!;
    const points = host.querySelector<HTMLDetailsElement>('.source-points')!;
    const scroll = vi.fn();
    for (const row of host.querySelectorAll<HTMLTableRowElement>('[data-row-id]')) row.scrollIntoView = scroll;
    editor.scrollTop = 192; const top = canvas.getBoundingClientRect().top;
    await pointer(host.querySelector('.sketch-point[data-id="point7"]')!); await pointer(canvas, 'pointerup');
    expect(host.querySelector('[data-row-id="point7"]')!.getAttribute('aria-selected')).toBe('true');
    expect(points.open).toBe(false); expect(scroll).not.toHaveBeenCalled();
    expect(editor.scrollTop).toBe(192); expect(canvas.getBoundingClientRect().top).toBe(top);
  });
  it('renders an editable aperture limit above export and writes label edits back to the attachment', async () => {
    await mount();
    await act(async () => button('Fit all (Shift+F)').click());
    const label = host.querySelector('.sketch-limit text')!;
    expect(label.textContent).toContain('Aperture r 14.00');
    expect(host.querySelector('.sketch-limit > path:not(.sketch-context-hit)')!.getAttribute('d')).toContain('l4,4 -4,4 -4,-4 Z');
    expect(host.querySelector('.sketch-limit line')).toBeNull();
    const selector = host.querySelector('[aria-label="Source attachment"]')!;
    expect(selector.compareDocumentPosition(button('Apply shape')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await act(async () => label.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })));
    await enter('Limit radius', '16');
    await act(async () => button('Apply limit').click()); await settle();
    expect(Number(input('Aperture radius attachment').value)).toBe(16);
    expect(host.querySelector('.sketch-limit text')?.textContent).toContain('Aperture r 16.00');
    await enter('Aperture radius attachment', '18'); await settle();
    expect(host.querySelector('.sketch-limit > title')?.textContent).toContain('Aperture r 18.00');
  });
  it('snaps a dragged woofer rim to the aperture and warns live when dragged beyond it', async () => {
    await mount();
    const svg = host.querySelector('[aria-label="Sketch drawing"]')!;
    const x = limitX();
    let rim = host.querySelector('.sketch-point[data-id="rim"]')!;
    const y = Number(rim.getAttribute('cy'));
    await pointer(rim, 'pointerdown', Number(rim.getAttribute('cx')), y);
    await pointer(svg, 'pointermove', x - 3, y); await settle();
    await pointer(svg, 'pointerup'); await settle();
    expect(readSourceDraft().contour.points.at(-1)!.r_mm).toBeCloseTo(14, 8);
    rim = host.querySelector('.sketch-point[data-id="rim"]')!;
    await pointer(rim, 'pointerdown', Number(rim.getAttribute('cx')), y);
    await pointer(svg, 'pointermove', x + 30, y); await settle();
    expect(host.querySelector('.sketch-domain-problem')?.textContent).toBe('Rim exceeds the baffle aperture');
    await pointer(svg, 'pointerup'); await settle();
    expect(readSourceDraft().contour.points.at(-1)!.r_mm).toBeGreaterThan(14);
    expect(button('Export STEP + mesh bundle').disabled).toBe(false);
  });
  it('uses the conical mouth as the standalone horn limit', async () => {
    await mount();
    const selector = host.querySelector<HTMLSelectElement>('[aria-label="Source attachment"]')!;
    await act(async () => { selector.value = 'horn'; selector.dispatchEvent(new Event('change', { bubbles: true })); }); await settle();
    await act(async () => button('Fit all (Shift+F)').click());
    expect(host.querySelector('.sketch-limit text')?.textContent).toContain('Mouth r 20.00');
    await enter('Point 2 radius', '21'); await settle();
    expect(host.textContent).toContain('Rim exceeds the horn mouth');
  });
  it('fits a cone rim to the aperture including surround and land', async () => {
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (shape, dimensions) => validation(expandedPreset(shape, dimensions)));
    await mount(); await act(async () => button('Fit to limit').click()); await settle();
    expect(sourceEditorApi.expand).toHaveBeenCalledWith('cone', expect.objectContaining({ radius_mm: 11, surround_width_mm: 2, land_width_mm: 1 }), expect.anything());
    expect(readSourceDraft().contour.points.at(-1)!.r_mm).toBe(14);
    expect(Number(input('Radius preset').value)).toBe(11);
  });
  it('shows a read-only horn throat, holds its rim exactly, and disables determined coordinates', async () => {
    const used = vi.fn();
    await act(async () => root.render(<Editor initialDocument={INITIAL_SOURCE} onClose={() => {}} onDraftChange={used} limit={{ kind: 'throat', radius: 12.7, hard: true }}/>)); await settle();
    const label = host.querySelector('.sketch-limit text')!;
    expect(label.textContent).toContain('Throat r 12.70 🔒');
    expect(label.querySelector('title')?.textContent).toBe('Set by the horn design');
    await act(async () => label.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })));
    expect(host.querySelector('[aria-label="Edit limit"]')).toBeNull();
    expect(Number(input('Point 2 radius').value)).toBe(12.7);
    expect(input('Point 2 radius').disabled).toBe(true);
    expect(used.mock.calls.at(-1)![0].contour.points.at(-1).r_mm).toBe(12.7);
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (shape, dimensions) => validation(expandedPreset(shape, dimensions)));
    await act(async () => button('Fit to limit').click()); await settle();
    expect(used.mock.calls.at(-1)![0].contour.points.at(-1).r_mm).toBe(12.7);
    expect(input('Radius preset').disabled).toBe(true);
    expect(host.querySelector('.sketch-limit text')?.textContent).toContain('Throat r 12.70 🔒');
  });
  it('links point and patch hover and selection without opening or scrolling tables', async () => {
    await mount();
    const points = host.querySelector<HTMLDetailsElement>('.source-points')!;
    expect(points.open).toBe(false);
    expect(host.querySelector('[data-row-id="piston"]')!.textContent).toContain('Piston');
    const row = host.querySelector<HTMLTableRowElement>('[data-row-id="piston"]')!;
    const scrolled = vi.fn(); row.scrollIntoView = scrolled;
    await pointer(row, 'pointerover');
    expect(host.querySelector('.sketch-entity[data-id="piston"]')!.classList.contains('hovered')).toBe(true);
    await pointer(row, 'pointerout');
    const curve = host.querySelector('.sketch-entity[data-id="piston"]')!;
    await pointer(curve, 'pointermove');
    expect(row.classList.contains('hovered')).toBe(true);
    await pointer(curve);
    expect(host.querySelector('[data-row-id="piston"]')!.getAttribute('aria-selected')).toBe('true');
    expect(scrolled).not.toHaveBeenCalled();
    await act(async () => button('Select point 2').click());
    expect(points.open).toBe(false);
    expect(host.querySelector('.sketch-point[data-id="rim"]')!.classList.contains('selected')).toBe(true);
    const pointRow = host.querySelector('[data-row-id="rim"]')!;
    await pointer(pointRow, 'pointerover');
    expect(host.querySelector('.sketch-point[data-id="rim"]')!.classList.contains('hovered')).toBe(true);
    await pointer(pointRow, 'pointerout');
    await pointer(host.querySelector('.sketch-point[data-id="rim"]')!, 'pointermove');
    expect(pointRow.classList.contains('hovered')).toBe(true);
    const pole = host.querySelector('.sketch-point[data-id="pole"]')!;
    await pointer(pole); await pointer(host.querySelector('[aria-label="Sketch drawing"]')!, 'pointerup'); await settle();
    expect(host.querySelector('[data-row-id="pole"]')!.getAttribute('aria-selected')).toBe('true');
    await act(async () => button('Select patch 1').click());
    expect(host.querySelector('[data-row-id="piston"]')!.getAttribute('aria-selected')).toBe('true');
    expect(host.querySelector('.sketch-entity[data-id="piston"]')!.classList.contains('selected')).toBe(true);
  });

  it('applies a parametric cone, renders seven separated values with accessible names, and synchronizes both editors through continuation', async () => {
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (shape, dimensions) => validation(expandedPreset(shape, dimensions)));
    await mount(); await act(async () => button('Apply shape').click()); await settle();
    expect(readSourceDraft()).toEqual(expandedPreset('cone'));
    expect(host.textContent).toContain('Fully constrained');
    const labels = [...host.querySelectorAll<SVGTextElement>('.sketch-dimension text')];
    expect(labels).toHaveLength(7);
    for (const parameter of Object.keys(presetDefaults.cone)) {
      const label = labels.find(label => label.getAttribute('aria-label')?.includes(presetLabels[parameter] + ' '))!;
      expect(label).toBeDefined();
      expect(label.querySelector('title')?.textContent).toContain(presetLabels[parameter] + ' ');
      expect(label.lastChild?.textContent).toMatch(/^[-\d.]+ mm$/);
    }
    expect(host.querySelectorAll('.sketch-dimension-arrows')).toHaveLength(7);
    expect(host.querySelectorAll('.sketch-extension-lines')).toHaveLength(7);
    // The placement rule uses disjoint 24px rows in each gutter; text is 11px high.
    for (const [i, label] of labels.entries()) {
      const x = Number(label.getAttribute('x')), y = Number(label.getAttribute('y')), half = label.lastChild!.textContent!.length * 3.2;
      expect(x - half).toBeGreaterThanOrEqual(0); expect(x + half).toBeLessThanOrEqual(640);
      expect(y).toBeGreaterThanOrEqual(12); expect(y).toBeLessThanOrEqual(360);
      for (const other of labels.slice(i + 1)) {
        const ox = Number(other.getAttribute('x')), oy = Number(other.getAttribute('y')), oh = other.lastChild!.textContent!.length * 3.2;
        expect(Math.abs(y - oy) >= 20 || Math.abs(x - ox) >= half + oh).toBe(true);
      }
    }
    await enter('Surround depth preset', '1'); await settle();
    expect(sourceEditorApi.expand).toHaveBeenCalledTimes(1);
    const expected = expandedPreset('cone', { surround_depth_mm: 1 });
    const actual = readSourceDraft();
    actual.contour.segments.forEach((segment, i) => segment.center_mm?.forEach((v, axis) => expect(v).toBeCloseTo(expected.contour.segments[i].center_mm![axis], 6)));
    expect(host.textContent).toContain('Surround depth 1.00');
    const coneDepth = [...host.querySelectorAll('.sketch-dimension text')].find(label => label.getAttribute('aria-label')?.includes('Cone depth '))!;
    await act(async () => coneDepth.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })));
    await enter('Dimension value', '5');
    await act(async () => button('Apply dimension value').click()); await settle();
    expect(Number(input('Cone depth preset').value)).toBe(5);
    expect(readSourceDraft().contour.points.find(p => p.id === 'cap.rim')!.z_mm).toBeCloseTo(-5, 6);
    await act(async () => button('Undo').click()); await settle();
    expect(Number(input('Cone depth preset').value)).toBe(4);
    await act(async () => root.render(null)); await mount();
    // Restoring the envelope keeps the mapping and the labelled constraints.
    const shape = host.querySelector<HTMLSelectElement>('[aria-label="Source shape"]')!;
    expect(shape.value).toBe('cone'); expect(Number(input('Surround depth preset').value)).toBe(1);
  });

  it('disables surround fields and removes the crown after converting the surround to a line', async () => {
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (shape, dimensions) => validation(expandedPreset(shape, dimensions)));
    await mount(); await act(async () => button('Apply shape').click()); await settle();
    const before = readSourceAuthoring(readSourceDraft());
    const depth = before.sketch.constraints.find(c => c.id === before.meta.preset!.dimensions.surround_depth_mm)!;
    if (depth.kind !== 'distanceY') throw new Error('Expected surround depth');
    await pointer(host.querySelector('.sketch-entity[data-id="surround"]')!);
    await act(async () => button('Make line').click()); await settle();
    expect(input('Surround depth preset').disabled).toBe(true);
    expect(input('Surround depth preset').closest('.field-row')!.getAttribute('title')).toBe('Re-apply the shape to edit this parameter');
    const after = readSourceAuthoring(readSourceDraft());
    expect(after.meta.preset!.dimensions.surround_depth_mm).toBeUndefined();
    expect(after.sketch.entities.some(e => e.id.startsWith('preset-surround-crown'))).toBe(false);
    expect(after.sketch.points.some(p => p.id === depth.b)).toBe(false);
    expect(host.textContent).not.toContain('Surround depth 0.60');
    await act(async () => button('Undo').click()); await settle();
    expect(input('Surround depth preset').disabled).toBe(false);
    expect(readSourceAuthoring(readSourceDraft()).sketch).toEqual(before.sketch);
  });

  it.each([
    ['Surround depth preset', 'surround_depth_mm', -.6],
    ['Surround depth preset', 'surround_depth_mm', 0],
    ['Surround width preset', 'surround_width_mm', 0],
    ['Rigid land width preset', 'land_width_mm', 0],
  ] as const)('keeps %s (%s = %s) for Apply shape without continuation', async (label, parameter, value) => {
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (shape, dimensions) => validation(expandedPreset(shape, dimensions)));
    await mount(); await act(async () => button('Apply shape').click()); await settle();
    const before = readSourceDraft(), continuation = vi.spyOn(SketchSession.prototype, 'setDimensionContinuation');
    await enter(label, String(value)); await settle();
    const field = input(label);
    expect(field.closest('.field-row')!.querySelector('.field-error')?.textContent).toBe("This changes the shape's topology — use Apply shape.");
    expect(continuation).not.toHaveBeenCalled();
    expect(readSourceDraft()).toEqual(before);
    expect(Number(field.value)).toBe(value);
    await act(async () => button('Apply shape').click()); await settle();
    expect(sourceEditorApi.expand).toHaveBeenLastCalledWith('cone', expect.objectContaining({ [parameter]: value }), expect.anything());
    expect(host.textContent).not.toContain("This changes the shape's topology");
    expect(Number(input(label).value)).toBe(value);
    expect(input(label).disabled).toBe(false);
    await act(async () => root.render(null)); await mount();
    expect(Number(input(label).value)).toBe(value);
    await enter(label, String(presetDefaults.cone[parameter])); await settle();
    expect(input(label).closest('.field-row')!.querySelector('.field-error')?.textContent).toBe("This changes the shape's topology — use Apply shape.");
    expect(continuation).not.toHaveBeenCalled();
    await act(async () => button('Apply shape').click()); await settle();
    expect(readSourceDraft()).toEqual(before);
  });

  it('refuses a surround sign change from the sketch before continuation', async () => {
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (shape, dimensions) => validation(expandedPreset(shape, dimensions)));
    await mount(); await act(async () => button('Apply shape').click()); await settle();
    const continuation = vi.spyOn(SketchSession.prototype, 'setDimensionContinuation');
    const label = [...host.querySelectorAll('.sketch-dimension text')].find(e => e.getAttribute('aria-label')?.includes('Surround depth '))!;
    await act(async () => label.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })));
    await enter('Dimension value', '-0.6');
    await act(async () => button('Apply dimension value').click()); await settle();
    expect(host.querySelector('[aria-label="Edit dimension"] [role="alert"]')?.textContent).toBe("This changes the shape's topology — use Apply shape.");
    expect(continuation).not.toHaveBeenCalled();
  });

  it('refuses a restored depth mapping whose helper no longer shares the surround centre', async () => {
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (shape, dimensions) => validation(expandedPreset(shape, dimensions)));
    await mount(); await act(async () => button('Apply shape').click()); await settle();
    const envelope = JSON.parse(storage.getItem('')!);
    const helper = envelope.sketch.entities.find((e: { id: string }) => e.id.startsWith('preset-surround-crown'));
    const center = envelope.sketch.points.find((p: { id: string }) => p.id === helper.center);
    envelope.sketch.points.push({ ...center, id: 'detached-center' }); helper.center = 'detached-center';
    await act(async () => root.render(null)); storage.setItem('', JSON.stringify(envelope)); await mount();
    expect(input('Surround depth preset').disabled).toBe(true);
    const continuation = vi.spyOn(SketchSession.prototype, 'setDimensionContinuation');
    const label = [...host.querySelectorAll('.sketch-dimension text')].find(e => e.getAttribute('aria-label')?.includes('Surround depth '))!;
    await act(async () => label.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })));
    await enter('Dimension value', '1');
    await act(async () => button('Apply dimension value').click()); await settle();
    expect(host.querySelector('[aria-label="Edit dimension"] [role="alert"]')?.textContent).toBe('Re-apply the shape to edit this parameter');
    expect(continuation).not.toHaveBeenCalled();
  });

  it.each(['flat', 'dome'] as const)('keeps the active %s preset fields and dimensions after reopening', async shape => {
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (kind, dimensions) => validation(expandedPreset(kind, dimensions)));
    await mount();
    await act(async () => { const select = host.querySelector<HTMLSelectElement>('[aria-label="Source shape"]')!; select.value = shape; select.dispatchEvent(new Event('change', { bubbles: true })); });
    await act(async () => button('Apply shape').click()); await settle();
    await enter('Radius preset', '11'); await settle();
    expect(readSourceDraft().contour.points.find(p => p.id === (shape === 'dome' ? 'dome.rim' : 'rim'))!.r_mm).toBeCloseTo(11, 6);
    await act(async () => root.render(null)); await mount();
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Source shape"]')!.value).toBe(shape);
    expect(Number(input('Radius preset').value)).toBe(11);
    expect(host.textContent).toContain('Radius 11.00');
  });

  it('fix 1: preserves split weights when metadata changes while lowering fails', async () => {
    await mount(); await splitCanvas(); await deletePoleLock(); await splitCanvas();
    const before = JSON.parse(storage.getItem('')!);
    expect(Object.keys(before.meta.weights)).toHaveLength(3);
    await act(async () => {
      const motion = host.querySelector<HTMLSelectElement>('[aria-label="Prescribed motion"]')!;
      motion.value = 'axial'; motion.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(JSON.parse(storage.getItem('')!).meta.weights).toEqual(before.meta.weights);
    await act(async () => button('Lock pole to axis').click()); await settle();
    expect(Object.keys(readSourceDraft().drive.weights)).toHaveLength(3);
  });
  it('fix 1: fills a missing moving weight so the displayed default can be committed', async () => {
    const doc = structuredClone(INITIAL_SOURCE); doc.drive.weights = {};
    storage.setItem('', JSON.stringify(doc)); await mount();
    expect(input('Patch 1 weight').value).toBe('1.000');
    await enter('Patch 1 weight', '1'); await settle();
    await enter('Drive channel ID', 'filled'); await settle();
    expect(readSourceDraft().drive.weights).toEqual({ piston: 1 });
    expect(button('Export STEP + mesh bundle').disabled).toBe(false);
  });
  it('fix 2: keeps a weight field focused and enabled through two queued ArrowUp steps', async () => {
    await mount(); const field = input('Patch 1 weight');
    await act(async () => field.focus());
    act(() => field.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true })));
    expect(field.matches(':disabled')).toBe(false);
    expect(document.activeElement).toBe(field);
    await act(async () => field.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true })));
    await settle();
    expect(readSourceDraft().drive.weights.piston).toBe(1.2);
    expect(document.activeElement).toBe(field);
    expect(input('Point 2 z').matches(':disabled')).toBe(false);
  });
  it('fix 4: keeps newer sketch data untouched and uses the Classic fallback', async () => {
    const sketch = { version: 'wg-sketch-v99', future: { opaque: ['keep', 9] } };
    storage.setItem('', JSON.stringify({ version: 'wg-source-authoring-v1', document: INITIAL_SOURCE, sketch, meta: { future: true } }));
    await mount();
    expect(host.textContent).toContain('Newer sketch data');
    expect(host.querySelector('[aria-label="Sketch drawing"]')).toBeNull();
    expect(host.querySelector('[aria-label="Source meridian drawing"]')).not.toBeNull();
    await enter('Point 2 radius', '12'); await settle();
    expect(JSON.parse(storage.getItem('')!).sketch).toEqual(sketch);
    vi.mocked(sourceEditorApi.expand).mockResolvedValue(validation(INITIAL_SOURCE));
    await act(async () => button('Apply shape').click());
    expect(JSON.parse(storage.getItem('')!).sketch).toEqual(sketch);
  });
  it('keeps a newer authoring envelope untouched and uses the Classic fallback', async () => {
    const envelope = { version: 'wg-source-authoring-v99', document: INITIAL_SOURCE, sketch: { version: 'wg-sketch-v1', future: true }, extra: { opaque: 1 } };
    storage.setItem('', JSON.stringify(envelope));
    await mount();
    expect(host.querySelector('[aria-label="Sketch drawing"]')).toBeNull();
    await enter('Point 2 radius', '12'); await settle();
    const saved = JSON.parse(storage.getItem('')!);
    expect(saved.version).toBe('wg-source-authoring-v99'); expect(saved.sketch).toEqual(envelope.sketch); expect(saved.extra).toEqual(envelope.extra);
  });
  it('fix 5: persists and reopens unfinished sketches with the last valid document', async () => {
    await mount(); await deletePoleLock();
    const saved = JSON.parse(storage.getItem('')!);
    expect(saved.pending).toBe(true); expect(saved.document).toEqual(INITIAL_SOURCE);
    await act(async () => root.render(null)); await mount();
    expect(host.querySelector('.sketch-domain-problem')?.textContent).toContain('Lock the pole');
    expect(readSourceAuthoring(readSourceDraft()).sketch).toEqual(saved.sketch);
    expect(button('Export STEP + mesh bundle').disabled).toBe(true);
    await act(async () => button('Lock pole to axis').click()); await settle();
    expect(JSON.parse(storage.getItem('')!).pending).toBe(false);
  });
  it('fix 6: orders points and their labels along the contour after splitting, even unfinished', async () => {
    await mount(); await splitCanvas();
    expect(Number(input('Point 2 radius').value)).toBe(4);
    expect(Number(input('Point 3 radius').value)).toBe(8);
    await deletePoleLock();
    expect(Number(input('Point 1 radius').value)).toBe(0);
    expect(Number(input('Point 2 radius').value)).toBe(4);
    expect(Number(input('Point 3 radius').value)).toBe(8);
  });
  it('fix 7: publishes and writes storage once for a multi-frame drag', async () => {
    const callback = vi.fn();
    await act(async () => root.render(<Editor onClose={() => {}} onDraftChange={callback}/>)); await settle();
    const writes = vi.spyOn(durableSettings, 'set'), lowering = vi.spyOn(sourceAdapter, 'lower'), rules = vi.spyOn(sourceAdapter, 'rules'); callback.mockClear();
    const svg = host.querySelector<SVGSVGElement>('[aria-label="Sketch drawing"]')!, rim = host.querySelector('.sketch-point[data-id="rim"]')!;
    const x = Number(rim.getAttribute('cx')), y = Number(rim.getAttribute('cy'));
    await pointer(rim, 'pointerdown', x, y);
    for (let i = 1; i <= 3; i++) {
      rules.mockClear(); await pointer(svg, 'pointermove', x + i * 5, y); await act(async () => { await vi.advanceTimersByTimeAsync(20); });
      expect(rules.mock.calls.length).toBeLessThanOrEqual(1);
    }
    expect(writes).not.toHaveBeenCalled(); expect(callback).not.toHaveBeenCalled(); expect(lowering).not.toHaveBeenCalled();
    await pointer(svg, 'pointerup');
    expect(writes).toHaveBeenCalledTimes(1); expect(callback).toHaveBeenCalledTimes(1); expect(lowering).toHaveBeenCalledTimes(1);
    expect(readSourceDraft().contour.points[1].r_mm).toBeGreaterThan(8);
  });
  it.each([false, true])('restores acceptance after a rejected drag release (nested: %s)', async nested => {
    const changed = vi.fn(), use = vi.fn();
    await act(async () => root.render(<Editor onClose={() => {}} onDraftChange={changed} onUseDocument={nested ? use : undefined}/>)); await settle();
    const before = readSourceDraft(), accept = nested ? 'Use contour' : 'Done';
    expect(button(accept).disabled).toBe(false);
    const endDrag = SketchSession.prototype.endDrag;
    const release = vi.spyOn(SketchSession.prototype, 'endDrag').mockImplementationOnce(async function (this: SketchSession) {
      return { ...await endDrag.call(this), status: 'failed', reason: 'Release would change branch.' };
    });
    const svg = host.querySelector('[aria-label="Sketch drawing"]')!, rim = host.querySelector('.sketch-point[data-id="rim"]')!;
    const x = Number(rim.getAttribute('cx')), y = Number(rim.getAttribute('cy'));
    await pointer(rim, 'pointerdown', x, y);
    await pointer(svg, 'pointermove', x + 20, y); await settle();
    expect(Number(host.querySelector('.sketch-point[data-id="rim"]')!.getAttribute('cx'))).toBeGreaterThan(x);
    await pointer(svg, 'pointerup'); await settle();
    expect(release).toHaveBeenCalledOnce();
    expect(readSourceDraft()).toEqual(before);
    expect(button(accept).disabled).toBe(false);
    expect(changed.mock.calls.at(-1)![0]).toEqual(before);
    expect(host.querySelector('.source-validation')?.textContent).toContain('Valid contour');
    if (nested) { await act(async () => button(accept).click()); expect(use).toHaveBeenCalledWith(before); }
  });
  it('keeps acceptance blocked when a soft context install cannot be solved', async () => {
    await mount();
    expect(button('Done').disabled).toBe(false);
    const solve = SketchSession.prototype.solveFull;
    const failure = vi.spyOn(SketchSession.prototype, 'solveFull').mockImplementationOnce(async function (this: SketchSession) {
      return { ...await solve.call(this), status: 'failed', reason: 'Context could not be solved.' };
    });
    await enter('Aperture radius attachment', '18'); await settle();
    expect(failure).toHaveBeenCalledOnce();
    expect(readSourceAuthoring(readSourceDraft()).meta.limit).toEqual({ kind: 'aperture', radius: 18 });
    expect(button('Done').disabled).toBe(true);
  });
  it('fix 12: distinguishes rigid sections by weight and solid projected references in both canvases', () => {
    expect(canvasCss).toContain('.sketch-entity.rigid { stroke-width: 3.5; }');
    expect(canvasCss).not.toContain('stroke-dasharray: 4 4');
    expect(canvasCss).toContain('.sketch-context-layer { stroke: var(--sketch-context-color); stroke-width: 1; stroke-dasharray: none;');
    expect(editorCss).toContain('.source-meridian .rigid { stroke: var(--fg2); stroke-width: 3; }');
    expect(editorCss).toContain('.source-axis { stroke: var(--fg3); stroke-width: 1; stroke-dasharray: 4 4; }');
  });
  it('fix 14: sketch mode preserves concise exact values through a queued keyboard metadata edit', async () => {
    const doc = structuredClone(INITIAL_SOURCE); doc.contour.points[1].r_mm = 8.123456789; doc.drive.weights.piston = .123456789;
    storage.setItem('', JSON.stringify(doc)); await mount();
    for (const [label, displayed] of [['Point 2 radius', '8.12'], ['Patch 1 weight', '0.123']]) {
      expect(input(label).value).toBe(displayed); await act(async () => input(label).focus()); await act(async () => input(label).blur());
    }
    await enter('Drive channel ID', 'changed'); await settle();
    expect(readSourceDraft().contour).toEqual(doc.contour); expect(readSourceDraft().drive.weights).toEqual(doc.drive.weights);
    act(() => input('Patch 1 weight').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true })));
    expect(input('Point 2 radius').matches(':disabled')).toBe(false);
    await settle();
  });
  it('sketch mode keeps zero and negative weights moving and refuses stale validation', async () => {
    await mount(); await enter('Patch 1 weight', '-0.5'); await settle();
    expect(readSourceDraft().drive.weights.piston).toBe(-.5);
    await enter('Patch 1 weight', '0'); await settle();
    expect(readSourceDraft().contour.segments[0].role).toBe('moving');
    vi.mocked(sourceEditorApi.validate).mockRejectedValueOnce(new Error('Invalid rim radius'));
    await enter('Point 2 radius', '12');
    expect(button('Export STEP + mesh bundle').disabled).toBe(true);
    await settle(); expect(host.textContent).toContain('Invalid rim radius');
    expect(host.querySelector('.sketch-entity.moving')).not.toBeNull();
  });
  it('sketch mode ignores an older accepted response after a newer draft was refused', async () => {
    await mount(); let resolve!: (value: SourceValidation) => void;
    vi.mocked(sourceEditorApi.validate).mockImplementationOnce(() => new Promise(r => { resolve = r; }));
    await enter('Point 2 radius', '12'); await settle(); const old = readSourceDraft();
    vi.mocked(sourceEditorApi.validate).mockRejectedValueOnce(new Error('Newest draft invalid'));
    await enter('Point 2 radius', '13'); await settle();
    await act(async () => resolve(validation(old)));
    expect(button('Export STEP + mesh bundle').disabled).toBe(true); expect(host.textContent).toContain('Newest draft invalid');
  });
  it('keeps role classes and theme tokens in the portalled dialog in both themes', async () => {
    const doc = structuredClone(INITIAL_SOURCE);
    doc.contour.points.push({ id: 'land', r_mm: 10, z_mm: 0 });
    doc.contour.segments.push({ ...doc.contour.segments[0], id: 'rigid-land', start: 'rim', end: 'land', role: 'rigid' });
    storage.setItem('', JSON.stringify(doc));
    await act(async () => root.render(<><SourceContourEditor/><SourceContourDialog/></>));
    const launch = [...host.querySelectorAll('button')].find(b => b.textContent === 'Edit source contour…')!;
    await act(async () => launch.click()); await settle();
    try {
      for (const theme of ['light', 'dark']) {
        document.documentElement.dataset.theme = theme;
        const dialog = document.body.querySelector('[aria-label="Source contour editor"]')!;
        expect(dialog.closest('html')?.getAttribute('data-theme')).toBe(theme);
        expect(dialog.querySelector('.sketch-entity.moving')).not.toBeNull();
        expect(dialog.querySelector('.sketch-entity.rigid')).not.toBeNull();
        expect(dialog.querySelector('.sketch-role')).toBeNull();
        expect([...dialog.querySelectorAll('button')].some(b => ['Fit drawing', 'Undo edit', 'Draw lines'].includes(b.textContent ?? ''))).toBe(false);
        // Classic drawing is no longer a user choice (Magnus could not return from it); there is no overflow menu.
        expect(dialog.querySelector('[aria-label="More sketch actions"]')).toBeNull();
        expect([...dialog.querySelectorAll('button')].some(b => b.textContent === 'Classic drawing')).toBe(false);
      }
      for (const css of [editorCss, canvasCss]) {
        expect(css).not.toContain('--ctl-bg');
        expect(css).toContain('var(--surface-raised)');
        expect(css).toContain('var(--fg3)');
      }
      expect(editorCss).toContain('var(--ctl-grad)');
      expect(editorCss).toContain('var(--ctl-grad-h)');
      expect(editorCss).toContain('var(--surface-field)');
      expect(canvasCss).toContain('.sketch-entity.moving { stroke-width: 2; }');
      expect(canvasCss).toContain('.sketch-entity.rigid { stroke-width: 3.5; }');
      expect(tokensCss).toContain('--source-moving: #69d4ce');
      expect(tokensCss).toContain('--source-moving: #176d68');
    } finally {
      delete document.documentElement.dataset.theme;
      await act(async () => document.body.querySelector<HTMLButtonElement>('[aria-label="Source contour editor"] header button')!.click());
    }
  });
  it('exposes the gate hook only with the flag and loads through authoring adoption', async () => {
    const old = window.location.href;
    await act(async () => root.render(<SourceContourDialog/>));
    expect(window.__sketchTest).toBeUndefined();
    await act(async () => root.render(null));
    window.history.replaceState(null, '', '?sketchPerf=1');
    try {
      await act(async () => root.render(<SourceContourDialog/>));
      expect(window.__sketchTest).toBeDefined();
      let opened: Promise<void>;
      await act(async () => { opened = window.__sketchTest!.openEditor(); });
      await settle(); await opened!;
      const doc = structuredClone(INITIAL_SOURCE); doc.contour.points[1].r_mm = 12;
      let loaded: Promise<void>;
      await act(async () => { loaded = window.__sketchTest!.loadDocument(doc); });
      await settle();
      await act(async () => { await vi.advanceTimersByTimeAsync(100); });
      await loaded!;
      expect(readSourceDraft()).toEqual(doc);
      expect(readSourceAuthoring(doc).sketch.constraints.map(c => c.kind)).toEqual(['pointOnLine', 'pointOnLine']);
      const point = document.body.querySelector<SVGCircleElement>('.sketch-point[data-id="rim"]')!;
      vi.spyOn(point, 'getBoundingClientRect').mockReturnValue({ left: 100, top: 200, width: 8, height: 8 } as DOMRect);
      expect(window.__sketchTest!.pointScreen('rim')).toEqual({ x: 104, y: 204 });
      expect(() => window.__sketchTest!.pointScreen('p1')).toThrow('No point handle');
      await act(async () => document.body.querySelector<HTMLButtonElement>('[aria-label="Source contour editor"] header button')!.click());
      await act(async () => root.render(null));
      expect(window.__sketchTest).toBeUndefined();
    } finally { window.history.replaceState(null, '', old); }
  });
  it('shows concise coordinates, arc centers and weights without rounding untouched draft values', async () => {
    const doc = structuredClone(INITIAL_SOURCE);
    doc.contour.points[1].r_mm = 8.123456789;
    doc.contour.segments[0] = { ...doc.contour.segments[0], kind: 'arc', center_mm: [4.0617283945, .000123456], direction: 'ccw' };
    doc.drive.weights.piston = .123456789;
    await mountClassic(doc);
    for (const [label, displayed] of [['Point 2 radius', '8.12'], ['Arc center radius', '4.06'], ['Arc center z', '0.00'], ['Patch 1 weight', '0.123']]) {
      expect(input(label).value).toBe(displayed);
      await act(async () => input(label).focus());
      await act(async () => input(label).blur());
    }
    expect(input('Radius preset').value).toBe('10.00');
    expect(input('Mesh size').value).toBe('2.000');
    await enter('Drive channel ID', 'changed'); await settle();
    expect(readSourceDraft().contour).toEqual(doc.contour);
    expect(readSourceDraft().drive.weights).toEqual(doc.drive.weights);
  });
  it('stores typed rim radius and arc centre exactly despite the two-decimal display', async () => {
    const doc = structuredClone(INITIAL_SOURCE);
    doc.contour.segments[0] = { ...doc.contour.segments[0], kind: 'arc', center_mm: [4, 0], direction: 'ccw' };
    await mountClassic(doc);
    await enter('Point 2 radius', '9.525'); await settle();
    await enter('Arc center radius', '6.123457'); await enter('Arc center z', '-0.000123'); await settle();
    const stored = readSourceDraft().contour;
    expect(stored.points[1].r_mm).toBe(9.525);
    expect(stored.segments[0].center_mm).toEqual([6.123457, -0.000123]);
    expect(input('Point 2 radius').value).toBe('9.53');
  });
  it('opens Identifiers when validation names an identity or an ID is empty', async () => {
    await mount();
    const disclosure = host.querySelector<HTMLDetailsElement>('details.source-identifiers')!;
    expect(disclosure.open).toBe(false);
    vi.mocked(sourceEditorApi.validate).mockRejectedValueOnce(new Error('contour.rim_id: String should have at least 1 character'));
    await enter('Drive channel ID', 'x'); await settle();
    expect(disclosure.open).toBe(true);
    await enter('Drive channel ID', 'fixed'); await settle();
    await act(async () => { disclosure.open = false; disclosure.dispatchEvent(new Event('toggle')); });
    expect(disclosure.open).toBe(false);
    await enter('Drive channel ID', ''); await settle();
    expect(disclosure.open).toBe(true);
  });
  it.each(['Physical source ID', 'Rim ID', 'Drive channel ID'])('keeps %s editable and validated inside Identifiers', async (label) => {
    await mount();
    const disclosure = input(label).closest('details')!;
    expect(disclosure.open).toBe(false);
    expect(disclosure.querySelector('summary')?.textContent).toBe('Identifiers');
    await act(async () => { disclosure.open = true; input(label).focus(); });
    expect(document.activeElement).toBe(input(label));
    await enter(label, 'edited'); await settle();
    const doc = readSourceDraft();
    expect(label === 'Drive channel ID' ? doc.drive.channel_id : doc.contour[label === 'Rim ID' ? 'rim_id' : 'physical_source_id']).toBe('edited');
    vi.mocked(sourceEditorApi.validate).mockRejectedValueOnce(new Error('Invalid identifier'));
    await enter(label, ''); await settle();
    expect(host.textContent).toContain('Invalid identifier');
    expect(button('Export STEP + mesh bundle').disabled).toBe(true);
  });
  it('retains modal focus, Tab wrapping and Escape while editing disclosed identifiers', async () => {
    const close = vi.fn();
    await act(async () => root.render(<Editor onClose={close}/>)); await settle();
    expect(document.activeElement).toBe(button('Close'));
    const last = button('Done');
    await act(async () => { last.focus(); last.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true })); });
    expect(document.activeElement).toBe(button('Close'));
    await act(async () => { input('Rim ID').closest('details')!.open = true; input('Rim ID').focus(); input('Rim ID').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); });
    expect(close).toHaveBeenCalledOnce();
  });

  it('edits contextual point coordinates, keeps IDs, persists draft, and can undo', async () => {
    await mount(); await act(async () => button('Select point 2').click());
    expect(host.querySelector('[aria-label="Point properties"]')).not.toBeNull();
    await enter('Point 2 radius', '10'); await settle();
    expect(sourceEditorApi.validate).toHaveBeenLastCalledWith(expect.objectContaining({ contour: expect.objectContaining({ points: [{ id: 'pole', r_mm: 0, z_mm: 0 }, { id: 'rim', r_mm: 10, z_mm: 0 }] }) }));
    expect(readSourceDraft().contour.points[1].r_mm).toBe(10);
    await act(async () => button('Undo').click()); await settle();
    expect(readSourceDraft()).toEqual(INITIAL_SOURCE);
  });
  it('keeps zero/negative weights as moving and refuses stale validation for an invalid draft', async () => {
    await mountClassic(); await enter('Patch 1 weight', '-0.5'); await settle();
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
    await mountClassic(); let resolve!: (value: SourceValidation) => void;
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
    expect(sourceEditorApi.save).toHaveBeenCalledWith('My source', INITIAL_SOURCE, undefined, expect.objectContaining({ version: 'wg-source-authoring-v2', pending: false }));
    await enter('Point 2 radius', '9'); await settle();
    await act(async () => button('Load My source').click()); await settle();
    expect(readSourceDraft()).toEqual(INITIAL_SOURCE);
    await act(async () => button('Update loaded preset').click());
    expect(sourceEditorApi.save).toHaveBeenLastCalledWith('My source', INITIAL_SOURCE, saved, expect.objectContaining({ version: 'wg-source-authoring-v2', pending: false }));
  });
  it('saves and reloads the authoring companion with names, placement, construction and preset mappings', async () => {
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (shape, dimensions) => validation(expandedPreset(shape, dimensions)));
    vi.mocked(sourceEditorApi.save).mockImplementation(async (name, document, previous, authoring) => ({ id: previous?.id ?? 'saved', revision: 'r2', name, document, authoring }));
    await mount(); await act(async () => button('Apply shape').click()); await settle();
    const state = readSourceAuthoring(readSourceDraft()), id = state.meta.preset!.dimensions.depth_mm;
    await rename('Patch 1 name', 'Phase cap');
    await rename(`Dimension ${id} name`, 'My depth');
    const label = host.querySelector<SVGTextElement>(`[data-constraint-id="${id}"] text`)!;
    const svg = host.querySelector('[aria-label="Sketch drawing"]')!;
    const x = Number(label.getAttribute('x')), y = Number(label.getAttribute('y'));
    await pointer(label, 'pointerdown', x, y);
    await pointer(svg, 'pointermove', x - 30, y + 40); await pointer(svg, 'pointerup'); await settle();
    await enter('Preset name', 'Authored dome');
    await act(async () => button('Save new preset').click()); await settle();
    const saved = vi.mocked(sourceEditorApi.save).mock.results.at(-1)!;
    const item = await saved.value;
    const before = readSourceAuthoring(readSourceDraft());
    expect(item.document).toEqual(readSourceDraft());
    expect(item.authoring.sketch).toEqual(before.sketch);
    expect(before.sketch.constraints.find(c => c.id === id)!.placement).toBeDefined();
    expect(before.sketch.entities.some(e => e.construction && !e.reference)).toBe(true);
    await act(async () => root.render(null)); storage.removeItem('');
    vi.mocked(sourceEditorApi.presets).mockResolvedValue([item]); await mount();
    await act(async () => button('Load Authored dome').click()); await settle();
    expect(readSourceAuthoring(readSourceDraft()).sketch).toEqual(before.sketch);
    expect(readSourceAuthoring(readSourceDraft()).meta.preset).toEqual(before.meta.preset);
    expect(host.querySelector(`[data-row-id="${id}"]`)!.textContent).toContain('My depth');
    expect(host.querySelector('[data-row-id="dust-cap"]')!.textContent).toContain('Phase cap');
  });
  it('drawing creates explicit points and blocks export until the drawing is finished', async () => {
    await mountClassic(); await act(async () => button('Draw lines').click());
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
  it('persists and reopens an authoring envelope with dimensions, and reseeds on preset load', async () => {
    const saved = { id: 'saved', revision: 'r1', name: 'Original', document: structuredClone(INITIAL_SOURCE) };
    vi.mocked(sourceEditorApi.presets).mockResolvedValue([saved]);
    await mount();
    const line = host.querySelector('[data-id="piston"]')!;
    await act(async () => button('Dimension (D)').click());
    await act(async () => line.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))); await settle();
    await act(async () => {
      const canvas = host.querySelector('[role="application"]')!;
      canvas.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 620, clientY: 40 }));
      canvas.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 620, clientY: 40 }));
      canvas.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: 620, clientY: 40 }));
    }); await settle();
    const envelope = JSON.parse(storage.getItem('')!);
    expect(envelope.version).toBe('wg-source-authoring-v2');
    expect(envelope.sketch.constraints).toContainEqual(expect.objectContaining({ kind: 'distance', value: 8, driving: true }));
    expect(envelope.document).toEqual(INITIAL_SOURCE);
    await act(async () => root.render(null)); await mount();
    expect(host.querySelector('[aria-label^="Dimension distance"]')).not.toBeNull();
    await act(async () => button('Load Original').click()); await settle();
    expect(host.textContent).toContain('Constraints were reset');
    expect(readSourceAuthoring(readSourceDraft()).sketch.constraints.map(c => c.kind)).toEqual(['pointOnLine', 'pointOnLine']);
    await act(async () => button('Undo').click()); await settle();
    expect(host.querySelector('[aria-label^="Dimension distance"]')).not.toBeNull();
  });
  it('leaves the canonical document intact and disables save and export when lowering fails', async () => {
    await mount(); await enter('Preset name', 'Invalid sketch');
    await act(async () => host.querySelector('[data-id="piston"]')!.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true })));
    await act(async () => button('Delete selection').click()); await settle();
    expect(readSourceDraft()).toEqual(INITIAL_SOURCE);
    expect(host.querySelector('.sketch-status')?.textContent).toContain('unfinished');
    expect(host.querySelectorAll('.sketch-domain-problem')).toHaveLength(1);
    expect(host.querySelector('.source-validation')).toBeNull();
    expect(button('Save new preset').disabled).toBe(true);
    expect(button('Export STEP + mesh bundle').disabled).toBe(true);
    await act(async () => button('Undo').click()); await settle();
    expect(button('Export STEP + mesh bundle').disabled).toBe(false);
  });
  it.each(['pole', 'rim'] as const)('offers a %s lock after deleting the lock glyph, and keeps fixed references immutable', async endpoint => {
    await mount();
    const envelope = readSourceAuthoring(readSourceDraft());
    const rimLock = envelope.sketch.constraints.find(c => c.kind === 'pointOnLine' && c.point === endpoint)!;
    await act(async () => host.querySelector(`[data-id="${rimLock.id}"]`)!.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true })));
    await act(async () => button('Delete selection').click()); await settle();
    expect(button('Export STEP + mesh bundle').disabled).toBe(true);
    await enter(endpoint === 'pole' ? 'Point 1 radius' : 'Point 2 z', '1'); await settle();
    await act(async () => button(endpoint === 'pole' ? 'Lock pole to axis' : 'Lock rim to rim plane').click()); await settle();
    expect(button('Export STEP + mesh bundle').disabled).toBe(false);
    expect(endpoint === 'pole' ? readSourceDraft().contour.points[0].r_mm : readSourceDraft().contour.points.at(-1)!.z_mm).toBe(0);
    expect(host.querySelector('[data-id="axis"]')).toBeNull();
    expect(host.querySelector('[data-id="baffle"]')).toBeNull();
  });
  it('applies a shape with a fresh sketch and restores the previous constraints with Undo', async () => {
    vi.mocked(sourceEditorApi.expand).mockResolvedValue(validation({ ...INITIAL_SOURCE, contour: { ...INITIAL_SOURCE.contour, points: [{ id: 'pole', r_mm: 0, z_mm: 0 }, { id: 'rim', r_mm: 14, z_mm: 0 }] } }));
    await mount();
    await act(async () => button('Dimension (D)').click());
    await act(async () => host.querySelector('[data-id="piston"]')!.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))); await settle();
    await act(async () => {
      const canvas = host.querySelector('[role="application"]')!;
      canvas.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 620, clientY: 40 }));
      canvas.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 620, clientY: 40 }));
      canvas.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: 620, clientY: 40 }));
    }); await settle();
    await act(async () => button('Apply shape').click()); await settle();
    expect(readSourceDraft().contour.points[1].r_mm).toBe(14);
    expect(host.querySelector('[aria-label^="Dimension distance"]')).toBeNull();
    expect(host.textContent).toContain('Constraints were reset');
    await act(async () => button('Undo').click()); await settle();
    expect(readSourceDraft()).toEqual(INITIAL_SOURCE);
    expect(host.querySelector('[aria-label^="Dimension distance"]')).not.toBeNull();
  });
  it('shows exactness diagnoses once and treats a locked, inexact pole/rim as invalid', async () => {
    const doc = structuredClone(INITIAL_SOURCE);
    doc.contour.points[0].r_mm = 1; doc.contour.points[1].z_mm = 1;
    storage.setItem('', JSON.stringify(doc));
    await mount();
    const problems = [...host.querySelectorAll('.sketch-domain-problem')];
    expect(problems).toHaveLength(2);
    expect(problems.filter(p => p.textContent?.includes('pole'))).toHaveLength(1);
    expect(problems.filter(p => p.textContent?.includes('rim'))).toHaveLength(1);
    expect(host.querySelector('.sketch-status')?.textContent).toContain('invalid');
    expect(problems.every(problem => problem.classList.contains('invalid'))).toBe(true);
    expect(host.querySelector('.source-validation')).toBeNull();
    expect(sourceEditorApi.validate).not.toHaveBeenCalled();
  });
  it('shows an arc-fold diagnosis once without repeating the server validation message', async () => {
    const doc = structuredClone(INITIAL_SOURCE);
    doc.contour.points[0].z_mm = 1; doc.contour.points[1].r_mm = 1;
    doc.contour.segments[0] = { ...doc.contour.segments[0], kind: 'arc', center_mm: [0, 0], direction: 'ccw' };
    storage.setItem('', JSON.stringify(doc));
    vi.mocked(sourceEditorApi.validate).mockRejectedValue(new Error('arc folds in radius'));
    await mount();
    expect(host.querySelectorAll('.sketch-domain-problem')).toHaveLength(1);
    expect(host.querySelector('.sketch-domain-problem')?.textContent).toContain('Arc must increase');
    expect(host.querySelector('.sketch-status')?.textContent).toContain('1 problem');
    expect(host.textContent).not.toContain('arc folds in radius');
    expect(host.textContent).not.toContain('Sketch has problems');
    expect(sourceEditorApi.validate).not.toHaveBeenCalled();
  });
  it('fits restored drafts, loaded presets and applied shapes in the measured canvas', async () => {
    const OriginalObserver = globalThis.ResizeObserver;
    globalThis.ResizeObserver = class {
      constructor(private callback: ResizeObserverCallback) {}
      observe() { this.callback([{ contentRect: { width: 280, height: 360 } } as ResizeObserverEntry], this as unknown as ResizeObserver); }
      disconnect() {}
    } as unknown as typeof ResizeObserver;
    try {
      const large = structuredClone(INITIAL_SOURCE); large.contour.points[1].r_mm = 1000;
      storage.setItem('', JSON.stringify({ version: 'wg-source-authoring-v1', document: large, ...readSourceAuthoring(large) }));
      vi.mocked(sourceEditorApi.presets).mockResolvedValue([{ id: 'saved', revision: 'r1', name: 'Original', document: INITIAL_SOURCE }]);
      vi.mocked(sourceEditorApi.expand).mockResolvedValue(validation(large));
      const fitted = () => {
        const pole = host.querySelector('.sketch-point[data-id="pole"]')!, rim = host.querySelector('.sketch-point[data-id="rim"]')!;
        expect(Number(pole.getAttribute('cx'))).toBeCloseTo(32);
        expect(Number(rim.getAttribute('cx'))).toBeCloseTo(248);
      };
      await mount(); fitted();
      const svg = host.querySelector<SVGSVGElement>('[aria-label="Sketch drawing"]')!;
      await act(async () => svg.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: -2000 })));
      expect(Number(host.querySelector('.sketch-point[data-id="rim"]')!.getAttribute('cx'))).toBeGreaterThan(280);
      await enter('Aperture radius attachment', '8'); await settle();
      await act(async () => button('Load Original').click()); await settle(); fitted();
      expect(readSourceDraft()).toEqual(INITIAL_SOURCE);
      await act(async () => button('Apply shape').click()); await settle(); fitted();
      expect(readSourceDraft()).toEqual(large);
      await act(async () => root.render(null)); await mount(); fitted();
    } finally { globalThis.ResizeObserver = OriginalObserver; }
  });
  it('falls back to the canonical document for unknown envelopes or mismatching sketches', async () => {
    const document = structuredClone(INITIAL_SOURCE); document.contour.points[1].r_mm = 12;
    storage.setItem('', JSON.stringify({ version: 'future', document, sketch: { version: 'future' } }));
    expect(readSourceDraft()).toEqual(document);
    expect(readSourceAuthoring(document).sketch.points.find(p => p.id === 'rim')!.x).toBe(12);
    const authoring = readSourceAuthoring(INITIAL_SOURCE);
    storage.setItem('', JSON.stringify({ version: 'wg-source-authoring-v1', document, ...authoring }));
    expect(readSourceAuthoring(document).sketch.points.find(p => p.id === 'rim')!.x).toBe(12);
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
    vi.mocked(sourceEditorApi.validate).mockImplementation(async (document) => ({ ...validation(document), meridian: { piston: Array.from({ length: 65 }, (_, i) => { const t = Math.PI + i / 64 * Math.PI; return [4 + 4 * Math.cos(t), 4 * Math.sin(t)]; }) } }));
    await mountClassic(doc); await act(async () => button('Fit drawing').click());
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
  it('saves a dragged dimension in the local envelope and restores it when the dialog reopens', async () => {
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (shape, dimensions) => validation(expandedPreset(shape, dimensions)));
    await mount(); await act(async () => button('Apply shape').click()); await settle();
    const before = readSourceAuthoring(readSourceDraft()), id = before.meta.preset!.dimensions.depth_mm;
    const requests = vi.mocked(sourceEditorApi.validate).mock.calls.map(([doc]) => JSON.stringify(doc));
    const checkPlacement = () => {
      const boxes = [...host.querySelectorAll<SVGTextElement>('.sketch-dimension text')].map(text => {
        const x = Number(text.getAttribute('x')), y = Number(text.getAttribute('y'));
        const rect = text.previousElementSibling!, rx = Number(rect.getAttribute('x')), ry = Number(rect.getAttribute('y'));
        const width = Number(rect.getAttribute('width')), height = Number(rect.getAttribute('height'));
        if (text.parentElement!.getAttribute('transform')?.startsWith('rotate(-90')) return { x: x + ry - y, y: y - (rx - x + width), width: height, height: width };
        return { x: rx, y: ry, width, height };
      });
      for (const [i, box] of boxes.entries()) for (const other of boxes.slice(i + 1)) {
        expect(box.x + box.width <= other.x || other.x + other.width <= box.x || box.y + box.height <= other.y || other.y + other.height <= box.y).toBe(true);
      }
      const px = Number(host.querySelector('.sketch-point[data-id="pole"]')!.getAttribute('cx'));
      const scale = (Number(host.querySelector('.sketch-point[data-id="cap.rim"]')!.getAttribute('cx')) - px) / 3;
      for (const c of before.sketch.constraints) if (c.kind === 'distanceY') {
        const xs = [c.a, c.b].map(id => px + before.sketch.points.find(p => p.id === id)!.x * scale);
        const x = Number(host.querySelector(`[data-constraint-id="${c.id}"] text`)!.getAttribute('x'));
        expect(x).toBeGreaterThanOrEqual(Math.min(...xs) - 40);
        expect(x).toBeLessThanOrEqual(Math.max(...xs) + 40);
      }
    };
    checkPlacement(); await act(async () => button('Names').click()); checkPlacement();
    await act(async () => button('Names').click());
    const label = host.querySelector<SVGTextElement>(`[data-constraint-id="${id}"] text`)!;
    const svg = host.querySelector('[aria-label="Sketch drawing"]')!;
    const x = Number(label.getAttribute('x')), y = Number(label.getAttribute('y'));
    const solve = vi.spyOn(SketchSession.prototype, 'solveFull');
    await pointer(label, 'pointerdown', x, y);
    await pointer(svg, 'pointermove', x - 15, y + 25);
    await pointer(svg, 'pointermove', x - 30, y + 40);
    await pointer(svg, 'pointerup'); await settle();
    expect(solve).not.toHaveBeenCalled();
    const placed = readSourceAuthoring(readSourceDraft());
    const placement = placed.sketch.constraints.find(c => c.id === id)!.placement;
    expect(placement).toBeDefined(); expect(placement).not.toHaveProperty('angle');
    expect(sourceAdapter.lower(placed.sketch, placed.meta)).toEqual(sourceAdapter.lower(before.sketch, before.meta));
    const afterRequests = vi.mocked(sourceEditorApi.validate).mock.calls.map(([doc]) => JSON.stringify(doc));
    expect(afterRequests.at(-1)).toBe(requests.at(-1));
    expect(afterRequests.slice(requests.length).every(body => body === requests.at(-1))).toBe(true);
    await act(async () => root.render(null)); await mount();
    expect(readSourceAuthoring(readSourceDraft()).sketch.constraints.find(c => c.id === id)!.placement).toEqual(placement);
    await pointer(host.querySelector(`[data-constraint-id="${id}"] text`)!);
    await act(async () => button('Reset position').click()); await settle();
    expect(readSourceAuthoring(readSourceDraft()).sketch.constraints.find(c => c.id === id)).not.toHaveProperty('placement');
    await act(async () => button('Undo').click()); await settle();
    expect(readSourceAuthoring(readSourceDraft()).sketch.constraints.find(c => c.id === id)!.placement).toEqual(placement);
    await act(async () => button('Apply shape').click()); await settle();
    expect(readSourceAuthoring(readSourceDraft()).sketch.constraints.every(c => c.placement === undefined)).toBe(true);
  });
  it('toggles a dimension in the table both ways with one undo and persistence', async () => {
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (shape, dimensions) => validation(expandedPreset(shape, dimensions)));
    await mount(); await act(async () => button('Apply shape').click()); await settle();
    const before = readSourceAuthoring(readSourceDraft()), id = before.meta.preset!.dimensions.depth_mm;
    const constraint = () => readSourceAuthoring(readSourceDraft()).sketch.constraints.find(c => c.id === id)!;
    await act(async () => button('Make reference Cone depth').click()); await settle();
    expect(constraint()).toMatchObject({ id, driving: false });
    expect(input(`Dimension ${id} value`).disabled).toBe(true);
    await act(async () => button('Make driving Cone depth').click()); await settle();
    expect(constraint()).toMatchObject({ id, driving: true });
    await act(async () => button('Undo').click()); await settle();
    expect(constraint()).toMatchObject({ id, driving: false });
    await act(async () => root.render(null)); await mount();
    expect(button('Make driving Cone depth')).toBeDefined();
    expect(constraint()).toMatchObject({ id, driving: false });
  });
  it.each(['dismiss', 'undo'] as const)('keeps a refused table toggle in one place, and never revives it after %s and Refresh presets', async clear => {
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (shape, dimensions) => validation(expandedPreset(shape, dimensions)));
    await mount(); await act(async () => button('Apply shape').click()); await settle();
    const envelope = JSON.parse(storage.getItem('')!);
    const depth = envelope.sketch.constraints.find((c: { label?: string }) => c.label === 'Cone depth');
    envelope.sketch.constraints.push({ ...depth, id: 'measured-depth', label: 'Measured depth', driving: false });
    await act(async () => root.render(null)); storage.setItem('', JSON.stringify(envelope)); await mount();
    // Give Undo a real edit to restore after the refused toggle.
    await act(async () => button('Make reference Cone depth').click()); await settle();
    await act(async () => button('Make driving Cone depth').click()); await settle();
    const before = storage.getItem('');
    await act(async () => button('Make driving Measured depth').click()); await settle();
    expect(readSourceAuthoring(readSourceDraft()).sketch.constraints.find(c => c.id === 'measured-depth')).toMatchObject({ driving: false });
    expect(storage.getItem('')).toBe(before);
    expect(host.querySelector('.sketch-conflict-banner')?.textContent).toContain('Measured depth');
    expect(host.querySelector('.sketch-conflict-banner')?.textContent).toContain('Conflicts with');
    expect(host.querySelector('.sketch-conflict-banner')?.textContent).not.toContain('measured-depth');
    expect(host.querySelector('.sketch-status')?.textContent).not.toContain('Conflicts with');
    expect(host.querySelector('footer [role="alert"]')).toBeNull();
    expect(host.querySelectorAll('[role="alert"]')).toHaveLength(1);
    await act(async () => button(clear === 'dismiss' ? 'Dismiss' : 'Undo').click()); await settle();
    expect(host.querySelector('.sketch-conflict-banner')).toBeNull();
    await act(async () => button('Refresh presets').click()); await settle();
    expect(host.querySelector('.sketch-conflict-banner')).toBeNull();
  });
  async function rename(label: string, value: string, gesture: 'F2' | 'dblclick' = 'F2', cancel = false) {
    const cell = [...host.querySelectorAll('button')].find(b => b.getAttribute('aria-label')?.startsWith(`${label}:`))!;
    await act(async () => {
      if (gesture === 'F2') cell.dispatchEvent(new KeyboardEvent('keydown', { key: 'F2', bubbles: true, cancelable: true }));
      else cell.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    });
    const field = input(label);
    expect(document.activeElement).toBe(field);
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value);
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => field.dispatchEvent(new KeyboardEvent('keydown', { key: cancel ? 'Escape' : 'Enter', bubbles: true, cancelable: true })));
    await settle();
  }
  it('renames patches and dimensions with F2/double-click, undoes/redoes, and restores the local envelope', async () => {
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (shape, dimensions) => validation(expandedPreset(shape, dimensions)));
    await mount(); await act(async () => button('Apply shape').click()); await settle();
    const before = readSourceAuthoring(readSourceDraft()), id = before.meta.preset!.dimensions.depth_mm;
    const requests = vi.mocked(sourceEditorApi.validate).mock.calls.map(([doc]) => JSON.stringify(doc));
    expect(host.querySelector<HTMLDetailsElement>('.source-dimensions')!.open).toBe(false);
    await rename('Patch 1 name', '  Phase cap  ');
    expect(host.querySelector('[data-row-id="dust-cap"]')!.textContent).toContain('Phase cap');
    expect(host.querySelector('[data-row-id="dust-cap"] td[title="dust-cap"]')).not.toBeNull();
    expect(host.querySelector('.sketch-entity[data-id="dust-cap"] title')!.textContent).toContain('Phase cap');
    expect(host.querySelector('[aria-label="Patch 1 name: Phase cap (dust-cap)"]')).not.toBeNull();
    await rename(`Dimension ${id} name`, '  Custom depth  ', 'dblclick');
    expect(readSourceAuthoring(readSourceDraft()).sketch.constraints.find(c => c.id === id)).toMatchObject({ label: 'Custom depth', value: 4, driving: true });
    expect(sourceAdapter.lower(readSourceAuthoring(readSourceDraft()).sketch, readSourceAuthoring(readSourceDraft()).meta)).toEqual(sourceAdapter.lower(before.sketch, before.meta));
    expect(vi.mocked(sourceEditorApi.validate).mock.calls.at(-1) && JSON.stringify(vi.mocked(sourceEditorApi.validate).mock.calls.at(-1)![0])).toBe(requests.at(-1));
    await act(async () => button('Undo').click()); await settle();
    expect(readSourceAuthoring(readSourceDraft()).sketch.constraints.find(c => c.id === id)!.label).toBe('Cone depth');
    await act(async () => button('Undo').click()); await settle();
    expect(host.querySelector('[data-row-id="dust-cap"]')!.textContent).toContain('Dust cap');
    await act(async () => button('Redo').click()); await settle();
    await act(async () => button('Redo').click()); await settle();
    await act(async () => button('Names').click());
    expect(host.querySelector('.sketch-curve-name[data-id="dust-cap"]')!.textContent).toBe('Phase cap');
    await act(async () => root.render(null)); await mount();
    expect(host.querySelector('[data-row-id="dust-cap"]')!.textContent).toContain('Phase cap');
    expect(host.querySelector(`[data-row-id="${id}"]`)!.textContent).toContain('Custom depth');
    expect(button('Names').getAttribute('aria-pressed')).toBe('true');
    const continuation = vi.spyOn(SketchSession.prototype, 'setDimensionContinuation');
    await enter('Cone depth preset', '5'); await settle();
    expect(continuation).toHaveBeenCalledWith(id, 5);
    expect(readSourceAuthoring(readSourceDraft()).sketch.constraints.find(c => c.id === id)).toMatchObject({ label: 'Custom depth', value: 5, driving: true });
    await enter(`Dimension ${id} value`, '4.5'); await settle();
    expect(continuation).toHaveBeenLastCalledWith(id, 4.5);
    expect(Number(input('Cone depth preset').value)).toBe(4.5);
  });
  it('keeps patch names through role changes, cancels with Escape, clears names, and links dimension selection in both directions', async () => {
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (shape, dimensions) => validation(expandedPreset(shape, dimensions)));
    await mount(); await act(async () => button('Apply shape').click()); await settle();
    const id = readSourceAuthoring(readSourceDraft()).meta.preset!.dimensions.depth_mm;
    await rename('Patch 1 name', 'Phase cap');
    const role = host.querySelector<HTMLSelectElement>('[aria-label="Patch 1 role"]')!;
    await act(async () => { role.value = 'rigid'; role.dispatchEvent(new Event('change', { bubbles: true })); }); await settle();
    await rename('Patch 1 name', 'New cap');
    expect(readSourceAuthoring(readSourceDraft()).sketch.entities.find(e => e.id === 'dust-cap')!.data).toEqual({ role: 'rigid', name: 'New cap' });
    await rename('Patch 1 name', 'Cancelled', 'dblclick', true);
    expect(host.querySelector('[data-row-id="dust-cap"]')!.textContent).toContain('New cap');
    await rename('Patch 1 name', '   ');
    expect(host.querySelector('[data-row-id="dust-cap"]')!.textContent).toContain('Dust cap');
    await rename(`Dimension ${id} name`, 'Depth', 'F2');
    await rename(`Dimension ${id} name`, 'Cancelled', 'dblclick', true);
    expect(host.querySelector(`[data-row-id="${id}"]`)!.textContent).toContain('Depth');
    await rename(`Dimension ${id} name`, '  ');
    // An empty name reverts to the default, which for a preset dimension is the preset's name.
    expect(host.querySelector(`[data-row-id="${id}"]`)!.textContent).toContain('Cone depth');
    const row = host.querySelector(`[data-row-id="${id}"]`)!;
    await act(async () => row.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(host.querySelector(`[data-constraint-id="${id}"] text`)!.classList.contains('selected')).toBe(true);
    const other = host.querySelector('.sketch-dimension text')!, otherId = other.getAttribute('data-id');
    await pointer(other);
    expect(host.querySelector(`[data-row-id="${otherId}"]`)!.getAttribute('aria-selected')).toBe('true');
  });

  it('persists a 30 mm baffle and a pole-to-back dimension without retargeting on remount', async () => {
    await mount(); await enter('Baffle depth attachment', '30'); await settle();
    await act(async () => button('Fit all (Shift+F)').click());
    await act(async () => button('Dimension (D)').click());
    const svg = () => host.querySelector('[aria-label="Sketch drawing"]')!;
    const pole = host.querySelector('.sketch-point[data-id="pole"]')!;
    await pointer(pole, 'pointerdown', Number(pole.getAttribute('cx')), Number(pole.getAttribute('cy'))); await pointer(svg(), 'pointerup');
    await pointer(host.querySelector('[data-context-id="box.back"] .sketch-context-hit')!); await settle();
    await pointer(svg(), 'pointermove', 300, 110); await pointer(svg(), 'pointerdown', 300, 110); await pointer(svg(), 'pointerup'); await settle();
    await act(async () => svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    const before = readSourceAuthoring(readSourceDraft()), documentBefore = readSourceDraft();
    expect(before.sketch.constraints.some(c => c.kind === 'pointLineDistance')).toBe(true);
    expect(readSourceAttachment().dimensions.depth_mm).toBe(30);
    await act(async () => root.render(null)); await mount();
    expect(readSourceAttachment().dimensions.depth_mm).toBe(30);
    expect(Number(input('Baffle depth attachment').value)).toBe(30);
    expect(readSourceAuthoring(readSourceDraft()).sketch).toEqual(before.sketch);
    expect(readSourceDraft()).toEqual(documentBefore);
    expect(host.textContent).not.toContain('Reference context changed');
    await enter('Patch 1 weight', '.5'); await settle();
    expect(readSourceDraft().contour.points).toEqual(documentBefore.contour.points);
    await act(async () => root.render(null));
    const envelope = JSON.parse(storage.getItem('')!); envelope.attachment.dimensions.depth_mm = 40;
    storage.setItem('', JSON.stringify(envelope)); await mount();
    expect(host.textContent).toContain('Reference context changed; contour updated');
    expect(readSourceDraft().contour.points[0].z_mm).toBeCloseTo(-10, 8);

  });

  it('restores the standalone conical attachment kind and all dimensions on remount', async () => {
    await mount();
    const selector = host.querySelector<HTMLSelectElement>('[aria-label="Source attachment"]')!;
    await act(async () => { selector.value = 'horn'; selector.dispatchEvent(new Event('change', { bubbles: true })); }); await settle();
    await enter('Backing depth attachment', '25'); await settle();
    const before = readSourceAttachment();
    expect(before.kind).toBe('horn'); expect(before.dimensions.backing_depth_mm).toBe(25);
    await act(async () => root.render(null)); await mount();
    expect(readSourceAttachment()).toEqual(before);
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Source attachment"]')!.value).toBe('horn');
    expect(Number(input('Backing depth attachment').value)).toBe(25);
  });

  it.each([true, false])('keeps point freedom unanalysed after a failed solve with stale determination %s', async determined => {
    const solve = SketchSession.prototype.solveFull;
    vi.spyOn(SketchSession.prototype, 'solveFull').mockImplementation(async function (this: SketchSession) {
      return { ...await solve.call(this), status: 'failed', dof: 0, pointDetermined: { rim: determined } };
    });
    await mount();
    await act(async () => button('Select point 2').click());
    const properties = host.querySelector('[aria-label="Point properties"]')!;
    expect(properties.textContent).toContain('Remaining freedom not analysed');
    expect(input('Point 2 radius').disabled).toBe(false);
  });
  it('switches the properties panel by selection and edits through the existing point, metadata and dimension commands', async () => {
    const doc = expandedPreset('cone', presetDefaults.cone);
    const state = sourceAdapter.fitSourcePreset((await import('../sketch/adapters/sourcePreset')).seedPreset(doc,'cone',presetDefaults.cone), {kind:'aperture',radius:14});
    const envelope = (await import('../sketch/adapters/sourceAuthoring')).serializeSourceAuthoring(state,false);
    await act(async () => root.render(<Editor onClose={() => {}} initialDocument={doc} initialAuthoring={envelope}/>)); await settle();
    const panel = host.querySelector('[aria-label="Sketch properties"]')!;
    expect(panel.querySelector('.source-summary')).not.toBeNull();
    expect(panel.querySelector('[aria-label="Point properties"]')).toBeNull();
    for (const section of panel.querySelectorAll<HTMLDetailsElement>('details')) expect(section.open).toBe(false);
    await act(async () => button('Select point 2').click());
    expect(panel.querySelector('[aria-label="Point properties"]')).not.toBeNull();
    expect(panel.querySelector('[aria-label="Constraints on selection"]')?.textContent).toContain('Dust-cap radius');
    expect(panel.querySelector('[aria-label="Point properties"]')?.textContent).toMatch(/remaining freedom|still move/);
    await act(async () => button('Select patch 2').click());
    expect(panel.querySelector('[aria-label="Point properties"]')).toBeNull();
    expect(panel.querySelector('[aria-label="Segment properties"]')?.textContent).toContain('length');
    await enter('Patch 2 weight','.25'); await settle();
    const field = panel.querySelector<HTMLSelectElement>('[aria-label="Segment properties"] select[aria-label="Patch 2 role"]')!;
    expect(field.value).toBe('moving');
    const dimension = state.sketch.constraints.find(c => c.label === 'Surround width')!;
    await act(async () => host.querySelector(`[data-row-id="${dimension.id}"]`)!.dispatchEvent(new MouseEvent('click',{bubbles:true})));
    expect(panel.querySelector('[aria-label="Segment properties"]')).toBeNull();
    expect(panel.querySelector('[aria-label="Dimension properties"]')).not.toBeNull();
    await enter(`Dimension ${dimension.id} value`,'2.5'); await settle();
    expect(Number(input(`Dimension ${dimension.id} value`).value)).toBe(2.5);
    expect(panel.querySelector('[aria-label="Selection actions"]')?.textContent).toContain('Reset position');
    await act(async () => button('Make reference Surround width').click()); await settle();
    expect(input(`Dimension ${dimension.id} value`).disabled).toBe(true);
    await act(async () => button('Delete selection').click()); await settle();
    expect(panel.querySelector('[aria-label="Dimension properties"]')).toBeNull();
    expect(host.querySelector('[aria-label="Reference dimension readout"], [aria-label="Edit dimension"]')).toBeNull();
    await act(async () => button('Undo').click()); await settle();
    await act(async () => host.querySelector(`[data-row-id="${dimension.id}"]`)!.dispatchEvent(new MouseEvent('click',{bubbles:true})));
    expect(input(`Dimension ${dimension.id} value`).disabled).toBe(true);
    await act(async () => host.querySelector('[aria-label="Sketch drawing"]')!.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
    expect(panel.querySelector('[aria-label="Dimension properties"]')).toBeNull();
    expect((panel.querySelector('.source-summary')!.parentElement as HTMLElement).hidden).toBe(false);
  });
  it('uses displayName for a selected arc centre outside the contour point table', async () => {
    const { displayName } = await import('../sketch/displayName');
    await mount(); await act(async () => button('Apply shape').click()); await settle();
    const state = readSourceAuthoring(readSourceDraft());
    const arc = state.sketch.entities.find(e => e.kind === 'arc' && !e.construction)!;
    if (arc.kind !== 'arc') throw new Error('Expected preset arc');
    await pointer(host.querySelector(`.sketch-entity[data-id="${arc.id}"]`)!);
    await pointer(host.querySelector('[aria-label="Sketch drawing"]')!,'pointerup'); await settle();
    await pointer(host.querySelector(`.sketch-point[data-id="${arc.center}"]`)!);
    await pointer(host.querySelector('[aria-label="Sketch drawing"]')!,'pointerup'); await settle();
    const properties = host.querySelector('[aria-label="Point properties"]')!;
    expect(properties.textContent).toContain(`${displayName(state.sketch,arc.center)} radius`);
    expect(properties.textContent).not.toContain('Point 0');
  });

  it('renders each selected row and field once as All tables open and close', async () => {
    await mount(); await act(async () => button('Apply shape').click()); await settle();
    for (const [selector, table, panel] of [
      ['.sketch-point[data-id="rim"]','.source-points','Point properties'],
      ['.sketch-entity:not(.construction)','.source-patches','Segment properties'],
      ['.sketch-dimension text','.source-dimensions','Dimension properties'],
    ]) {
      const target = host.querySelector(selector)!;
      const id = target.getAttribute('data-id')!;
      await pointer(target); await pointer(host.querySelector('[aria-label="Sketch drawing"]')!,'pointerup'); await settle();
      expect(host.querySelectorAll(`[data-row-id="${id}"]`)).toHaveLength(1);
      expect(host.querySelector(`[aria-label="${panel}"]`)).not.toBeNull();
      const details = host.querySelector<HTMLDetailsElement>(table)!;
      await act(async () => { details.open = true; details.dispatchEvent(new Event('toggle')); });
      expect(host.querySelector(`[aria-label="${panel}"]`)).toBeNull();
      expect(host.querySelectorAll(`[data-row-id="${id}"]`)).toHaveLength(1);
      const labels = [...host.querySelectorAll('input[aria-label], select[aria-label]')].map(e => e.getAttribute('aria-label'));
      expect(new Set(labels).size).toBe(labels.length);
      await act(async () => { details.open = false; details.dispatchEvent(new Event('toggle')); });
      expect(host.querySelectorAll(`[data-row-id="${id}"]`)).toHaveLength(1);
      expect(host.querySelector(`[aria-label="${panel}"]`)).not.toBeNull();
    }
  });

  it('keeps a height-filling workspace with an independent panel and persistent toolbar/status at mocked 1440 × 900', async () => {
    let resized: ResizeObserverCallback | undefined;
    vi.stubGlobal('ResizeObserver', class { constructor(callback: ResizeObserverCallback) { resized = callback; } observe() {} disconnect() {} });
    const style = document.createElement('style'); style.textContent = editorCss + canvasCss; document.head.append(style);
    try {
      await mount();
      const editor = host.querySelector<HTMLElement>('.source-editor')!, canvas = host.querySelector<SVGSVGElement>('[aria-label="Sketch drawing"]')!, panel = host.querySelector<HTMLElement>('.source-properties')!;
      vi.spyOn(editor,'getBoundingClientRect').mockReturnValue({width:1400,height:846,top:27,bottom:873,left:20,right:1420} as DOMRect);
      vi.spyOn(canvas,'getBoundingClientRect').mockReturnValue({width:1026,height:600,top:100,bottom:700,left:38,right:1064} as DOMRect);
      await act(async () => resized!([{contentRect:canvas.getBoundingClientRect()} as ResizeObserverEntry],{} as ResizeObserver));
      expect(canvas.getBoundingClientRect().height / editor.getBoundingClientRect().height).toBeGreaterThanOrEqual(.6);
      expect(canvas.getAttribute('viewBox')).toBe('0 0 1026 600');
      expect(getComputedStyle(editor).overflow).toBe('hidden');
      expect(getComputedStyle(panel).overflow).toBe('auto');
      expect(getComputedStyle(host.querySelector('.source-editor fieldset')!).minHeight).toBe('0px');
      expect(getComputedStyle(host.querySelector('.sketch-surface')!).flexGrow).toBe('1');
      expect(host.querySelector('.source-toolbar-host .sketch-toolbar')).not.toBeNull();
      expect(host.querySelector('footer .sketch-status')).not.toBeNull();
      expect(host.querySelector('footer')!.contains(button('Done'))).toBe(true);
      expect(editorCss).toContain('@media (max-width: 899px)');
      expect(editorCss).toContain('grid-template-rows: minmax(360px, 1fr)');
    } finally { style.remove(); vi.unstubAllGlobals(); }
  });
  it.each([false,true])('blocks acceptance and offers explicit scaling after an infeasible host change (nested: %s)', async nested => {
    const primaryAction = nested ? 'Use contour' : 'Done';
    const doc = structuredClone(INITIAL_SOURCE); doc.contour.points[1].r_mm = 12.7; doc.contour.points[0].z_mm = Math.sqrt(13**2-12.7**2);
    let state = sourceAdapter.withSourceLimit(sourceAdapter.seed(doc),{kind:'throat',radius:12.7,hard:true});
    const result = sourceAdapter.applySourceCommand(state.sketch,state.meta,{kind:'addConstraint',constraint:{kind:'distance',a:'pole',b:'rim',value:13,driving:true,label:'Typed chord'}});
    if ('error' in result) throw new Error(result.error); state = result;
    const authoring = (await import('../sketch/adapters/sourceAuthoring')).serializeSourceAuthoring(state,false);
    const changed = vi.fn();
    const render = async (radius: number) => { await act(async () => root.render(<Editor onClose={() => {}} initialDocument={doc} initialAuthoring={authoring} limit={{kind:'throat',radius,hard:true}} onDraftChange={changed} onUseDocument={nested ? vi.fn() : undefined}/>)); await settle(); };
    await render(12.7); expect(button(primaryAction).disabled).toBe(false); await render(14);
    expect(button(primaryAction).disabled).toBe(true);
    expect(host.querySelector('.source-throat-mismatch')!.textContent).toContain('Rim does not meet the horn throat (12.70 vs 14.00)');
    const banner = host.querySelector('.sketch-conflict-banner')!;
    expect(banner.textContent).toContain('The new throat conflicts with:');
    expect(banner.textContent).toContain('Typed chord');
    expect(banner.querySelector('button')!.textContent).toBe('Scale to fit throat');
    expect(host.querySelector('.source-summary')!.textContent).toContain('Scale to fit throat');
    expect(Number(input('Point 2 radius').value)).toBe(12.7);
    await act(async () => banner.querySelector('button')!.click()); await settle();
    expect(changed.mock.calls.at(-1)![0].contour.points.at(-1).r_mm).toBe(14);
    expect(host.querySelector('.sketch-conflict-banner')).toBeNull();
    expect(button(primaryAction).disabled).toBe(false);
    await act(async () => button('Undo').click()); await settle();
    expect(Number(input('Point 2 radius').value)).toBe(12.7);
    expect(button(primaryAction).disabled).toBe(true);
    expect(host.querySelector('.source-throat-mismatch')!.textContent).toContain('(12.70 vs 14.00)');
    // Dismissing the solver banner cannot dismiss the host mismatch or enable acceptance.
    await act(async () => button('Dismiss').click()); await settle();
    expect(host.querySelector('.source-throat-mismatch')).not.toBeNull();
    expect(button(primaryAction).disabled).toBe(true);
    await act(async () => button('Redo').click()); await settle();
    expect(Number(input('Point 2 radius').value)).toBe(14);
    expect(button(primaryAction).disabled).toBe(false);
  });

  it('blocks acceptance when the current context cannot install even though the throat still matches', async () => {
    const { wooferAssemblyContext } = await import('../sketch/adapters/sourceContext');
    const { materialiseContext } = await import('../sketch/context');
    const { serializeSourceAuthoring } = await import('../sketch/adapters/sourceAuthoring');
    const { INITIAL_ASSEMBLY } = await import('./SourceAssemblyEditor');
    const context = (depth: number) => wooferAssemblyContext({ ...INITIAL_ASSEMBLY.dimensions, depth_mm: depth }, 12.7);
    const doc = structuredClone(INITIAL_SOURCE);
    doc.contour.points = [{ id: 'pole', r_mm: 0, z_mm: 0 }, { id: 'mid', r_mm: 6.35, z_mm: -Math.sqrt(7.5 ** 2 - 6.35 ** 2) }, { id: 'rim', r_mm: 12.7, z_mm: 0 }];
    doc.contour.segments = [{ ...doc.contour.segments[0], id: 'inner', end: 'mid' }, { ...doc.contour.segments[0], id: 'outer', start: 'mid' }];
    doc.drive.weights = { inner: 1, outer: 1 };
    const state = sourceAdapter.withSourceContext(sourceAdapter.withSourceLimit(sourceAdapter.seed(doc), { kind: 'throat', radius: 12.7, hard: true }), context(40));
    const back = materialiseContext(state.sketch, context(40), 'box.back'); state.sketch = back.sketch;
    state.sketch.constraints.push(
      { id: 'inner-length', kind: 'distance', a: 'pole', b: 'mid', value: 7.5, driving: true },
      { id: 'outer-length', kind: 'distance', a: 'mid', b: 'rim', value: 7.5, driving: true },
      { id: 'back-distance', kind: 'pointLineDistance', point: 'pole', line: back.id, value: 40, driving: true },
    );
    const authoring = serializeSourceAuthoring(state), changed = vi.fn();
    const render = async (depth: number) => {
      await act(async () => root.render(<Editor onClose={() => {}} initialDocument={doc} initialAuthoring={authoring} context={context(depth)} limit={{ kind: 'throat', radius: 12.7, hard: true }} onDraftChange={changed}/>)); await settle();
    };
    await render(40); expect(button('Done').disabled).toBe(false);
    await render(60);
    expect(host.querySelector('.sketch-conflict-banner')?.textContent).toContain('The new throat conflicts with:');
    expect(Number(input('Point 3 radius').value)).toBe(12.7);
    expect(host.querySelector('.source-throat-mismatch')).toBeNull();
    expect(button('Done').disabled).toBe(true);
    await render(40); expect(button('Done').disabled).toBe(false);
  });

  async function mountPresetInThroat(kind: 'mouth' | 'aperture' | 'throat', nested = true) {
    const { seedPreset } = await import('../sketch/adapters/sourcePreset');
    const { serializeSourceAuthoring } = await import('../sketch/adapters/sourceAuthoring');
    const dimensions = { ...presetDefaults.cone, radius_mm: kind === 'throat' ? 14 : 12.7 };
    const source = seedPreset(expandedPreset('cone', dimensions), 'cone', dimensions);
    const state = kind === 'throat' ? sourceAdapter.fitSourcePreset(source, {kind,radius:14,hard:true})
      : sourceAdapter.withSourceLimit(source, {kind,radius:20});
    const lowered = sourceAdapter.lower(state.sketch,state.meta);
    expect(lowered.ok).toBe(true); if (!lowered.ok) throw new Error('Invalid preset fixture');
    vi.mocked(sourceEditorApi.presets).mockResolvedValue([{id:'loaded',revision:'r1',name:'Loaded shape',document:lowered.document,authoring:serializeSourceAuthoring(state,false)}]);
    const changed = vi.fn(), use = vi.fn();
    const render = async (radius = 14) => { await act(async () => root.render(<Editor initialDocument={INITIAL_SOURCE} onClose={() => {}} onUseDocument={nested ? use : undefined} onDraftChange={changed} limit={{kind:'throat',radius,hard:true}}/>)); await settle(); };
    await render();
    const before = changed.mock.calls.at(-1)![0];
    return { changed, use, before, loadedDocument: lowered.document, render };
  }

  it.each(['Apply shape', 'Fit to limit'])('keeps shape fields aligned with scaled geometry after a rejected %s', async action => {
    const changed = vi.fn();
    const render = async (radius: number) => {
      await act(async () => root.render(<Editor onClose={() => {}} initialDocument={INITIAL_SOURCE} onDraftChange={changed} limit={{ kind: 'throat', radius, hard: true }}/>)); await settle();
    };
    await render(16);
    let finishExpand!: () => void;
    // Older expansion responses use generic import semantics rather than named constraints.
    vi.mocked(sourceEditorApi.expand).mockImplementation(async (shape, dimensions) => {
      const doc = expandedPreset(shape, dimensions);
      doc.contour.segments[0].id = 'imported-cap';
      doc.drive.weights['imported-cap'] = doc.drive.weights['dust-cap']; delete doc.drive.weights['dust-cap'];
      return new Promise(resolve => { finishExpand = () => resolve(validation(doc)); });
    });
    await act(async () => button(action).click());
    // The external host changes while expansion is in flight, including after Fit to limit.
    await render(18);
    const solve = SketchSession.prototype.solveFull;
    vi.spyOn(SketchSession.prototype, 'solveFull').mockImplementationOnce(async function (this: SketchSession) {
      const result = await solve.call(this);
      return { ...result, status: 'failed', conflicting: [this.sketch.constraints[0].id] };
    });
    await act(async () => finishExpand()); await settle();
    expect(host.querySelector('.source-load-offer')).not.toBeNull();
    const [, dimensions] = vi.mocked(sourceEditorApi.expand).mock.calls.at(-1)!;
    const radius = dimensions.radius_mm + dimensions.surround_width_mm + dimensions.land_width_mm;
    const factor = 18 / radius;
    await act(async () => button('Scale it to fit').click()); await settle();
    expect(host.querySelector('.source-load-offer')).toBeNull();
    expect(button('Done').disabled).toBe(false);
    const document = changed.mock.calls.at(-1)![0] as SourceDocument;
    expect(document.contour.points.at(-1)!.r_mm).toBeCloseTo(18, 8);
    expect(document.contour.points.find(p => p.id === 'cone.rim')!.r_mm).toBeCloseTo(dimensions.radius_mm * factor, 8);
    for (const [key, value] of Object.entries(dimensions)) expect(Number(input(`${presetLabels[key]} preset`).value)).toBe(Number((value * factor).toFixed(2)));
    expect(document.contour.points[0].z_mm).toBeCloseTo((dimensions.cap_height_mm - dimensions.depth_mm) * factor, 8);
  });

  it.each(['mouth', 'aperture', 'throat'] as const)('loads a preset saved with a %s limit into a throat host', async kind => {
    const { changed, use, before, loadedDocument } = await mountPresetInThroat(kind);
    await act(async () => button('Load Loaded shape').click()); await settle();
    if (kind !== 'throat') {
      expect(changed.mock.calls.at(-1)![0]).toEqual(before);
      expect(host.querySelector('.source-load-offer')?.textContent).toContain('Loaded preset conflicts with the throat:');
      expect(host.querySelector('.source-throat-mismatch')).toBeNull();
      expect(host.querySelector('.sketch-conflict-banner')).toBeNull();
      expect(button('Use contour').disabled).toBe(false);
      await act(async () => button('Scale it to fit').click()); await settle();
    }
    expect(host.querySelector('.source-load-offer')).toBeNull();
    expect(host.querySelector('.source-throat-mismatch')).toBeNull();
    expect(host.querySelector('.sketch-conflict-banner')).toBeNull();
    expect(button('Use contour').disabled).toBe(false);
    const loaded = changed.mock.calls.at(-1)![0] as SourceDocument;
    expect(loaded.contour.segments.map(p => p.id)).toEqual(loadedDocument.contour.segments.map(p => p.id));
    expect(loaded.contour.points.at(-1)!.r_mm).toBeCloseTo(14,9);
    const factor = kind === 'throat' ? 1 : 14 / loadedDocument.contour.points.at(-1)!.r_mm;
    expect(loaded.contour.points[0].z_mm).toBeCloseTo(loadedDocument.contour.points[0].z_mm * factor,8);
    await act(async () => button('Use contour').click());
    expect(use).toHaveBeenCalledWith(loaded);
    await act(async () => button('Undo').click()); await settle();
    expect(changed.mock.calls.at(-1)![0]).toEqual(before);
  });

  it.each([true, false])('keeps acceptance enabled after a conflicting Load (nested: %s)', async nested => {
    const { changed, before } = await mountPresetInThroat('mouth', nested);
    await act(async () => button('Load Loaded shape').click()); await settle();
    expect(changed.mock.calls.at(-1)![0]).toEqual(before);
    expect(button(nested ? 'Use contour' : 'Done').disabled).toBe(false);
    expect(button('Update loaded preset').disabled).toBe(true);
    expect(host.querySelector('.source-validation')?.textContent).toContain('Valid contour');
    expect(host.querySelector('.source-throat-mismatch')).toBeNull();
    expect(host.querySelector('.source-load-offer')).not.toBeNull();
    await act(async () => button('Discard').click()); await settle();
    expect(host.querySelector('.source-load-offer')).toBeNull();
    expect(changed.mock.calls.at(-1)![0]).toEqual(before);
  });

  it.each(['Scale it to fit', 'Discard'] as const)('keeps the loaded preset identity through %s', async action => {
    const { seedPreset } = await import('../sketch/adapters/sourcePreset');
    const { serializeSourceAuthoring } = await import('../sketch/adapters/sourceAuthoring');
    const makePreset = (id: string, radius: number) => {
      const dimensions = { ...presetDefaults.cone, radius_mm: radius };
      const source = seedPreset(expandedPreset('cone', dimensions), 'cone', dimensions);
      const state = id === 'A' ? sourceAdapter.fitSourcePreset(source, { kind: 'throat', radius: 14, hard: true }) : source;
      state.sketch.entities[0].data = { ...state.sketch.entities[0].data as object, name: `Contour ${id}` };
      const lowered = sourceAdapter.lower(state.sketch, state.meta);
      if (!lowered.ok) throw new Error('Invalid preset fixture');
      return { id, revision: `revision-${id}`, name: `Preset ${id}`, document: lowered.document, authoring: serializeSourceAuthoring(state) };
    };
    const a = makePreset('A', 14), b = makePreset('B', 12.7);
    vi.mocked(sourceEditorApi.presets).mockResolvedValue([a, b]);
    const { sourceEditorApi: realApi } = await vi.importActual<typeof import('../api/sourceEditor')>('../api/sourceEditor');
    vi.mocked(sourceEditorApi.save).mockImplementation(realApi.save);
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
      const body = JSON.parse(init!.body as string);
      return new Response(JSON.stringify({ ...(action === 'Discard' ? a : b), ...body }), { status: 200 });
    });
    const changed = vi.fn();
    await act(async () => root.render(<Editor onClose={() => {}} initialDocument={INITIAL_SOURCE} limit={{ kind: 'throat', radius: 14, hard: true }} onDraftChange={changed}/>)); await settle();
    await act(async () => button('Load Preset A').click()); await settle();
    expect(input('Preset name').value).toBe('Preset A');
    const before = changed.mock.calls.at(-1)![0];
    await act(async () => button('Load Preset B').click()); await settle();
    expect(input('Preset name').value).toBe('Preset A');
    expect(changed.mock.calls.at(-1)![0]).toEqual(before);
    await act(async () => button(action).click()); await settle();
    const expected = action === 'Discard' ? a : b;
    expect(input('Preset name').value).toBe(expected.name);
    expect(button('Update loaded preset').disabled).toBe(false);
    expect(host.querySelector('.source-load-offer')).toBeNull();
    await act(async () => button('Update loaded preset').click()); await settle();
    expect(fetch).toHaveBeenCalledWith(`/api/source-editor/presets/${expected.id}`, expect.objectContaining({ method: 'PUT' }));
    const body = JSON.parse(fetch.mock.calls.at(-1)![1]!.body as string);
    expect(body).toMatchObject({ name: expected.name, expected_revision: expected.revision });
    expect(body.document).toEqual(changed.mock.calls.at(-1)![0]);
    expect(body.authoring.sketch.entities[0].data.name).toBe(`Contour ${expected.id}`);
    expect(body.document.contour.points.at(-1).r_mm).toBeCloseTo(14, 8);
    if (action === 'Discard') expect(body.document).toEqual(before);
  });

  it('recovers acceptance when Undo removes a dimension that rejected the current throat', async () => {
    const doc = structuredClone(INITIAL_SOURCE);
    doc.contour.points[1].r_mm = 12.7; doc.contour.points[0].z_mm = Math.sqrt(13 ** 2 - 12.7 ** 2);
    const changed = vi.fn(), authoring = vi.fn();
    const render = async (radius: number) => {
      await act(async () => root.render(<Editor onClose={() => {}} initialDocument={doc} limit={{ kind: 'throat', radius, hard: true }} onDraftChange={changed} onAuthoringChange={authoring}/>)); await settle();
    };
    await render(12.7);
    await act(async () => button('Dimension (D)').click());
    await pointer(host.querySelector('.sketch-entity[data-id="piston"]')!); await settle();
    const canvas = host.querySelector('[aria-label="Sketch drawing"]')!;
    await pointer(canvas, 'pointermove', 400, 80); await pointer(canvas, 'pointerdown', 400, 80); await pointer(canvas, 'pointerup', 400, 80); await settle();
    await act(async () => canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))); await settle();
    const constraints = authoring.mock.calls.at(-1)![0].sketch.constraints as { kind: string; value?: number; driving?: boolean }[];
    expect(constraints.find(c => c.kind === 'distance')).toMatchObject({ value: expect.closeTo(13, 8), driving: true });
    await render(14);
    expect(button('Done').disabled).toBe(true);
    expect(host.querySelector('.sketch-conflict-banner')).not.toBeNull();
    await act(async () => button('Undo').click()); await settle();
    expect(changed.mock.calls.at(-1)![0].contour.points.at(-1).r_mm).toBeCloseTo(14, 8);
    expect(button('Done').disabled).toBe(false);
    expect(host.querySelector('.source-throat-mismatch')).toBeNull();
    expect(host.querySelector('.sketch-conflict-banner')).toBeNull();
    // Redo restores the incompatible chord; Undo recovers again against the same host.
    await act(async () => button('Redo').click()); await settle();
    expect(button('Done').disabled).toBe(true);
    await act(async () => button('Undo').click()); await settle();
    expect(button('Done').disabled).toBe(false);
    expect(host.querySelector('.source-throat-mismatch')).toBeNull();
  });

  it.each(['edit', 'undo', 'redo', 'context'] as const)('clears a rejected Load on %s and cannot adopt a stale offer', async action => {
    const { changed, render } = await mountPresetInThroat('aperture');
    await enter('Patch 1 weight', '.5'); await settle();
    if (action === 'redo') { await act(async () => button('Undo').click()); await settle(); }
    await act(async () => button('Load Loaded shape').click()); await settle();
    const staleScale = button('Scale it to fit');
    expect(staleScale).toBeDefined();
    if (action === 'edit') { await enter('Patch 1 weight', '.25'); await settle(); }
    else if (action === 'context') await render(15);
    else { await act(async () => button(action === 'undo' ? 'Undo' : 'Redo').click()); await settle(); }
    expect(host.querySelector('.source-load-offer')).toBeNull();
    const after = changed.mock.calls.at(-1)![0];
    const count = changed.mock.calls.length;
    await act(async () => staleScale.click()); await settle();
    expect(changed.mock.calls).toHaveLength(count);
    expect(changed.mock.calls.at(-1)![0]).toEqual(after);
    expect(after.contour.segments.map((p: { id: string }) => p.id)).toEqual(INITIAL_SOURCE.contour.segments.map(p => p.id));
  });

  it('clears the Load offer and surfaces a context error when Undo cannot finish', async () => {
    const { changed, render } = await mountPresetInThroat('mouth');
    await render(15);
    await enter('Patch 1 weight', '.25'); await settle();
    await act(async () => button('Load Loaded shape').click()); await settle();
    expect(host.querySelector('.source-load-offer')).not.toBeNull();
    const before = changed.mock.calls.at(-1)![0], count = changed.mock.calls.length;
    const solve = SketchSession.prototype.solveFull;
    const failure = vi.spyOn(SketchSession.prototype, 'solveFull').mockImplementationOnce(solve).mockRejectedValueOnce(new Error('Context solve failed'));
    try { await act(async () => button('Undo').click()); await settle(); }
    finally { failure.mockRestore(); }
    expect(host.querySelector('.source-load-offer')).toBeNull();
    expect(host.textContent).toContain('Context solve failed');
    expect(changed.mock.calls).toHaveLength(count);
    expect(changed.mock.calls.at(-1)![0]).toEqual(before);
    expect(Number(input('Patch 1 weight').value)).toBe(.25);
    await act(async () => button('Redo').click()); await settle();
    expect(changed.mock.calls).toHaveLength(count);
    await act(async () => button('Undo').click()); await settle();
    expect(Number(input('Patch 1 weight').value)).toBe(1);
  });

  it('does not scale a stale Load when a field commit is queued before the scale click', async () => {
    const { changed } = await mountPresetInThroat('mouth');
    await act(async () => button('Load Loaded shape').click()); await settle();
    const scale = button('Scale it to fit'), field = input('Patch 1 weight');
    await act(async () => {
      field.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, '.25');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => { field.blur(); scale.click(); }); await settle();
    expect(host.querySelector('.source-load-offer')).toBeNull();
    const after = changed.mock.calls.at(-1)![0] as SourceDocument;
    expect(after.contour.segments.map(p => p.id)).toEqual(INITIAL_SOURCE.contour.segments.map(p => p.id));
    expect(after.drive.weights.piston).toBe(.25);
  });

  it('rolls back an infeasible first throat attachment to existing typed authoring', async () => {
    const doc = structuredClone(INITIAL_SOURCE); doc.contour.points[1].r_mm = 12.7;
    doc.contour.points[0].z_mm = Math.sqrt(13**2-12.7**2);
    const state = sourceAdapter.seed(doc);
    state.sketch.constraints.push({id:'typed-chord',kind:'distance',a:'pole',b:'rim',value:13,driving:true,label:'Typed chord'});
    const authoring = (await import('../sketch/adapters/sourceAuthoring')).serializeSourceAuthoring(state,false);
    await act(async () => root.render(<Editor onClose={() => {}} initialDocument={doc} initialAuthoring={authoring} limit={{kind:'throat',radius:14,hard:true}}/>)); await settle();
    expect(Number(input('Point 2 radius').value)).toBe(12.7);
    expect(Number(input('Dimension typed-chord value').value)).toBe(13);
    const banner = host.querySelector('.sketch-conflict-banner')!;
    expect(banner.textContent).toContain('The new throat conflicts with:');
    expect(banner.querySelector('button')!.textContent).toBe('Scale to fit throat');
  });
  it('keeps unfinished diagnostics neutral in the panel and finished failures in error style', async () => {
    await mount(); await deletePoleLock();
    const problem = host.querySelector('.sketch-domain-problem')!;
    expect(problem.classList.contains('unfinished')).toBe(true);
    expect(host.querySelector('.source-properties')!.contains(problem)).toBe(true);
    expect(host.querySelector('footer .sketch-status')!.textContent).toContain('Start at the axis');
    expect(host.querySelectorAll('.sketch-problem:not(.unfinished)')).toHaveLength(0);
    await act(async () => button('Lock pole to axis').click()); await settle();
    const bad = structuredClone(INITIAL_SOURCE); bad.contour.points[1].r_mm = -1;
    await act(async () => root.render(null));
    await act(async () => root.render(<Editor onClose={() => {}} initialDocument={bad}/>)); await settle();
    expect(host.querySelector('.sketch-domain-problem.invalid')).not.toBeNull();
    expect(host.querySelector('footer .sketch-status')!.textContent).toContain('invalid');
  });

  it('shows active command steps in the panel and length/angle fields once at the cursor', async () => {
    await mount();
    await act(async () => button('Line (L)').click());
    expect(host.querySelector('.source-command-properties')?.textContent).toContain('Click to chain lines');
    const canvas = host.querySelector('[aria-label="Sketch drawing"]')!;
    await pointer(canvas,'pointerdown',100,100);
    await act(async () => canvas.dispatchEvent(new KeyboardEvent('keydown',{key:'2',bubbles:true})));
    expect(host.querySelectorAll('[aria-label="Line length"]')).toHaveLength(1);
    expect(host.querySelectorAll('[aria-label="Line angle"]')).toHaveLength(1);
  });

});
