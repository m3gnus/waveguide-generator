import { act, type ComponentProps } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { sourceEditorApi, type SourceDocument } from '../api/sourceEditor';
import { Editor } from './SourceContourEditor';
import { INITIAL_SOURCE } from './sourceContourEditing';
import { expandedPreset } from '../sketch/adapters/presetTestSupport';
import { seed } from '../sketch/adapters/sourceContour';
import { serializeSourceAuthoring } from '../sketch/adapters/sourceAuthoring';
import { wooferAssemblyContext } from '../sketch/adapters/sourceContext';
import { realSessionReady } from '../sketch/testSupport';
import { sketchViewKey } from '../sketch/viewPreferences';

vi.mock('../api/sourceEditor', () => ({ sourceEditorApi: { validate: vi.fn(), presets: vi.fn() } }));

let host: HTMLDivElement, root: Root;
type Publish = { callback: 'onDraftChange' | 'onAuthoringChange'; payload: unknown };
let publishes: Publish[];
beforeAll(realSessionReady);
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  localStorage.removeItem(sketchViewKey);
  vi.mocked(sourceEditorApi.presets).mockResolvedValue([]);
  vi.mocked(sourceEditorApi.validate).mockImplementation(async document => ({
    document, geometry_sha256: 'geometry', excitation_sha256: 'excitation', meridian: {},
  }));
  publishes = [];
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount()); host.remove(); vi.useRealTimers(); vi.restoreAllMocks();
});
const svg = () => host.querySelector<SVGSVGElement>('svg[aria-label="Sketch drawing"]')!;
const button = (label: string) => {
  const found = [...host.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') ?? b.textContent) === label);
  if (!found) throw new Error(`Missing button: ${label}`);
  return found;
};
const settle = async () => act(async () => { await vi.advanceTimersByTimeAsync(200); });
async function mount(document: SourceDocument = INITIAL_SOURCE, props: Partial<ComponentProps<typeof Editor>> = {}) {
  await act(async () => root.render(<Editor initialDocument={document} onClose={() => {}}
    onDraftChange={payload => publishes.push({ callback: 'onDraftChange', payload: structuredClone(payload) })}
    onAuthoringChange={payload => publishes.push({ callback: 'onAuthoringChange', payload: structuredClone(payload) })}
    {...props}/>));
  await settle();
  expect(button('Line (L)').disabled).toBe(false);
  vi.spyOn(svg(), 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 640, height: 360 } as DOMRect);
}
const click = async (label: string) => act(async () => button(label).click());
const key = async (key: string) => act(async () => svg().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true })));
const pointer = async (target: Element, type: string, x: number, y: number) => act(async () => {
  target.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, buttons: type === 'pointerup' ? 0 : 1 }));
});
function point(id: string) {
  const element = host.querySelector<SVGCircleElement>(`circle.sketch-point[data-id="${id}"]`)!;
  return { element, x: Number(element.getAttribute('cx')), y: Number(element.getAttribute('cy')) };
}
async function enter(label: string, value: string) {
  const field = host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value);
    field.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
// One tag per line with whitespace-only text dropped, so the snapshot has no trailing whitespace.
const markup = (element: Element | null) => element?.outerHTML.replace(/>\s+</g, '><').replace(/></g, '>\n<');
function snapshotWorkspace() {
  // Only drawing + toolbar DOM. The inline form and conflict banner are siblings:
  // assert their state explicitly below without snapshotting the whole editor.
  expect({ svg: markup(svg()), toolbar: markup(host.querySelector('.sketch-toolbar')) }).toMatchSnapshot();
}
function snapshotPublishes() {
  // W0 deliberately pins TODAY'S two separate host calls: document first, then
  // authoring. W3 will deliberately replace these expectations with one atomic
  // host call, retaining the document and authoring payloads recorded here.
  expect(publishes.map(p => p.callback)).toEqual(['onDraftChange', 'onAuthoringChange']);
  expect(publishes).toMatchSnapshot();
}

describe('W0 workspace DOM at 77c641c8', () => {
  it('HF contour', async () => {
    const document = expandedPreset('dome');
    await mount(document, { limit: { kind: 'throat', radius: 11, hard: true } });
    expect(host.querySelector('.sketch-limit > title')?.textContent).toContain('Throat');
    expect(svg().querySelector('[data-id="dome"]')).not.toBeNull();
    snapshotWorkspace();
  });
  it('woofer with aperture context', async () => {
    const document = expandedPreset('cone');
    const context = wooferAssemblyContext({ width_mm: 200, height_mm: 300, depth_mm: 80, front_z_mm: 0,
      woofer_xy_mm: [0, 0], horn_xy_mm: [0, 60], horn_length_mm: 40, mouth_radius_mm: 30, aperture_radius_mm: 14 }, 13);
    await mount(document, { context, limit: { kind: 'aperture', radius: 14 } });
    expect(svg().textContent).toContain('Aperture');
    expect(svg().textContent).toContain('Enclosure back');
    snapshotWorkspace();
  });
  it('conflict state', async () => {
    const state = seed(INITIAL_SOURCE);
    state.sketch.constraints.push({ id: 'radius', kind: 'distanceX', a: 'pole', b: 'rim', value: 8, driving: true, label: 'Typed radius' });
    const props = { initialAuthoring: serializeSourceAuthoring(state), limit: { kind: 'throat' as const, radius: 8, hard: true } };
    await mount(INITIAL_SOURCE, props);
    await act(async () => root.render(<Editor initialDocument={INITIAL_SOURCE} onClose={() => {}} {...props}
      limit={{ kind: 'throat', radius: 10, hard: true }}/>));
    await settle();
    expect(host.querySelector('.sketch-conflict-banner')?.textContent).toContain('The new throat conflicts with:');
    expect(host.querySelector('.sketch-conflict-banner')?.textContent).toContain('Typed radius');
    snapshotWorkspace();
  });
  it('dimension editing with the value box open', async () => {
    await mount();
    await placeDimension();
    expect(host.querySelector<HTMLInputElement>('[aria-label="Dimension value"]')?.value).toBe('8');
    expect(host.querySelector('[aria-label="Edit dimension"]')).not.toBeNull();
    snapshotWorkspace();
  });
});

async function placeDimension() {
  await key('d');
  const a = point('pole'), b = point('rim');
  const x = (a.x + b.x) / 2, y = a.y;
  await pointer(svg(), 'pointermove', x, y);
  await pointer(svg(), 'pointerdown', x, y);
  await pointer(svg(), 'pointerup', x, y);
  await pointer(svg(), 'pointermove', x, y - 50);
  expect(host.querySelector('.sketch-dimension-preview')).not.toBeNull();
  await pointer(svg(), 'pointerdown', x, y - 50);
  await pointer(svg(), 'pointerup', x, y - 50);
  await settle();
  expect(host.querySelector('[aria-label="Edit dimension"]')).not.toBeNull();
}

describe('W0 nested editor host publishes at 77c641c8', () => {
  it('command publishes document then authoring with the complete payloads', async () => {
    await mount(); expect(publishes).toEqual([]);
    await key('s');
    const a = point('pole'), b = point('rim');
    await pointer(host.querySelector('.sketch-entity[data-id="piston"]')!, 'pointerdown', (a.x + b.x) / 2, (a.y + b.y) / 2);
    await settle();
    snapshotPublishes();
    expect((publishes[0].payload as SourceDocument).contour.segments).toHaveLength(2);
  });
  it('multi-frame drag publishes only at release, document then authoring', async () => {
    await mount(); expect(publishes).toEqual([]);
    const rim = point('rim');
    await pointer(rim.element, 'pointerdown', rim.x, rim.y);
    for (const dx of [5, 10, 15]) {
      await pointer(svg(), 'pointermove', rim.x + dx, rim.y); await settle();
      expect(publishes).toEqual([]);
    }
    await pointer(svg(), 'pointerup', rim.x + 15, rim.y); await settle();
    snapshotPublishes();
    expect((publishes[0].payload as SourceDocument).contour.points[1].r_mm).toBeGreaterThan(8);
  });
  it('placed dimension and its first value each publish a pair but merge into one undo step', async () => {
    await mount(); expect(publishes).toEqual([]);
    await placeDimension();
    snapshotPublishes();
    const placement = publishes.splice(0);
    await enter('Dimension value', '10');
    expect(publishes).toEqual([]); // Typing has no host side effects.
    await click('Apply dimension value'); await settle();
    snapshotPublishes();
    expect((placement[0].payload as SourceDocument).contour.points[1].r_mm).toBe(8);
    expect((publishes[0].payload as SourceDocument).contour.points[1].r_mm).toBeCloseTo(10, 8);
    publishes = [];
    await click('Undo'); await settle();
    expect((publishes[0].payload as SourceDocument).contour.points[1].r_mm).toBe(8);
    const envelope = publishes[1].payload as ReturnType<typeof serializeSourceAuthoring>;
    expect(envelope.sketch.constraints.filter(c => 'value' in c)).toEqual([]);
  });
});
