import { act } from 'react';
import { readFileSync } from 'node:fs';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { sourceEditorApi, type SourceAssemblyDocument } from '../api/sourceEditor';
import { durableSettings, namespaceStorage, SETTINGS_NAMESPACES } from '../stores/durableSettings';
import { serializeDesign, useDesignStore } from '../stores/design';
import { prepareNativeAssembly } from './nativeAssemblyPreparation';
import { AssemblyEditor, INITIAL_ASSEMBLY, parseAssemblyDraft } from './SourceAssemblyEditor';

import { parseSourceAuthoring, serializeSourceAuthoring } from '../sketch/adapters/sourceAuthoring';
import { seed } from '../sketch/adapters/sourceContour';
import { expandedPreset } from '../sketch/adapters/presetTestSupport';
import { realSessionReady } from '../sketch/testSupport';
beforeAll(realSessionReady);

vi.mock('../api/sourceEditor', () => ({ sourceEditorApi:{ validateAssembly:vi.fn(), hornProfile:vi.fn(), hornWall:vi.fn(), ingestAssembly:vi.fn(), exportAssembly:vi.fn(), validate:vi.fn(), presets:vi.fn() } }));
vi.mock('./nativeAssemblyPreparation', () => ({ prepareNativeAssembly:vi.fn() }));
const storage = namespaceStorage('sourceAssemblyDraft');
describe('shared source and passage authoring', () => {
  let host:HTMLDivElement; let root:Root;
  beforeEach(() => {
    vi.useFakeTimers(); vi.clearAllMocks(); storage.removeItem(''); namespaceStorage('sourceAssemblyAuthoring').removeItem(''); namespaceStorage('sketchView').removeItem('');
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT:boolean }).IS_REACT_ACT_ENVIRONMENT=true;
    vi.mocked(sourceEditorApi.validateAssembly).mockResolvedValue({ geometry_sha256:'geometry', passage_contract:null, horn_section_mm:{} });
    vi.mocked(sourceEditorApi.hornWall).mockImplementation(async config => { const p = config.profile as { r0_mm?: number; L_mm?: number }; const radius = p?.r0_mm ?? 4, length = p?.L_mm ?? 24, mouth = length === 130 ? 80 : 10; return { wall_mm: [[radius, 0], [mouth, length]], throat_radius_mm: radius, horn_length_mm: length, mouth_radius_mm: mouth }; });
    vi.mocked(sourceEditorApi.hornProfile).mockRejectedValue(new Error('No resolved profile'));
    vi.mocked(sourceEditorApi.presets).mockResolvedValue([]);
    vi.mocked(sourceEditorApi.validate).mockImplementation(async (document) => ({ document, geometry_sha256:'g',excitation_sha256:'e',meridian:Object.fromEntries(document.contour.segments.map((s,i) => [s.id,document.contour.points.slice(i,i+2).map((p) => [p.r_mm,p.z_mm])])) }));
    host=document.createElement('div'); document.body.append(host); root=createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); vi.useRealTimers(); vi.restoreAllMocks(); });
  const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(200); }); };
  const mount = async () => { await act(async () => root.render(<AssemblyEditor onClose={() => {}}/>)); await settle(); };
  const button = (text:string) => [...host.querySelectorAll('button')].find((b) => (b.getAttribute('aria-label') ?? b.textContent)===text)!;
  const read = () => parseAssemblyDraft(storage.getItem('')!);
  const field = (label:string) => {
    const owner=[...host.querySelectorAll('label')].find((l) => l.querySelector('.field-name')?.textContent===label)!;
    return document.getElementById(owner.htmlFor) as HTMLInputElement;
  };
  async function enter(label:string, value:string) {
    const owner=[...host.querySelectorAll('label')].find((l) => l.querySelector('.field-name')?.textContent===label)!;
    const input=document.getElementById(owner.htmlFor) as HTMLInputElement;
    await act(async () => { input.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,value); input.dispatchEvent(new Event('input',{bubbles:true})); });
    await act(async () => input.blur()); await settle();
  }
  const profile = { horn_config: { formula: 'OSSE', mode: 'bare', profile: { r0_mm: 7, L_mm: 130 } }, throat_radius_mm: 7, horn_length_mm: 130, mouth_radius_mm: 80 };
  it.each([
    ['Woofer aperture radius', 'Edit woofer contour…', 'aperture_radius_mm'],
    ['Horn mouth radius', 'Edit horn contour…', 'mouth_radius_mm'],
  ] as const)('rejects zero in %s and still opens the contour editor', async (label, launch, dimension) => {
    storage.setItem('', JSON.stringify(INITIAL_ASSEMBLY)); await mount();
    await enter(label, '0');
    expect(Number(field(label).getAttribute('aria-valuemin'))).toBeGreaterThan(0);
    expect(read().dimensions[dimension]).toBe(INITIAL_ASSEMBLY.dimensions[dimension]);
    await act(async () => button(launch).click()); await settle();
    expect(host.querySelector('[aria-label="Source contour editor"]')).not.toBeNull();
  });
  it.each([
    ['woofer', 'aperture_radius_mm', 0], ['woofer', 'aperture_radius_mm', -1],
    ['horn', 'mouth_radius_mm', 0], ['horn', 'mouth_radius_mm', -1],
  ] as const)('opens a saved %s with invalid %s = %s without a limit line or crash', async (source, dimension, radius) => {
    const saved = structuredClone(INITIAL_ASSEMBLY); saved.dimensions[dimension] = radius;
    storage.setItem('', JSON.stringify(saved)); await mount();
    await act(async () => button(`Edit ${source} contour…`).click()); await settle();
    expect(host.querySelector('[aria-label="Source contour editor"]')).not.toBeNull();
    expect(host.querySelector('.sketch-limit')).toBeNull();
    expect(host.textContent).toContain('Limit radius must be positive');
    expect(host.querySelector('[aria-label="Sketch drawing"]')).not.toBeNull();
  });
  const authoringStorage = namespaceStorage('sourceAssemblyAuthoring');
  const svg = () => host.querySelector('[aria-label="Sketch drawing"]')!;
  const pointer = async (target: Element, type = 'pointerdown', x = 0, y = 0) => act(async () => target.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, buttons: type === 'pointerup' ? 0 : 1 })));
  const envelopeFor = (source: 'horn' | 'woofer') => JSON.parse(authoringStorage.getItem('')!)[source];
  it.each(['horn', 'woofer'] as const)('keeps %s constraints, names, placement and construction through closing and remounting', async source => {
    storage.setItem('', JSON.stringify(INITIAL_ASSEMBLY)); await mount();
    await act(async () => button(`Edit ${source} contour…`).click()); await settle();
    await act(async () => button('Dimension (D)').click());
    await pointer(host.querySelector('.sketch-entity[data-id="piston"]')!); await settle();
    await pointer(svg(), 'pointermove', 400, 80); await pointer(svg(), 'pointerdown', 400, 80); await pointer(svg(), 'pointerup', 400, 80); await settle();
    await act(async () => svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    const dimension = envelopeFor(source).sketch.constraints.find((c: { value?: number }) => c.value !== undefined);
    expect(dimension.placement).toBeDefined();
    const cell = [...host.querySelectorAll('button')].find(b => b.getAttribute('aria-label')?.startsWith(`Dimension ${dimension.id} name:`))!;
    await act(async () => cell.dispatchEvent(new KeyboardEvent('keydown', { key: 'F2', bubbles: true })));
    const name = host.querySelector<HTMLInputElement>(`[aria-label="Dimension ${dimension.id} name"]`)!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'Diaphragm width');
      name.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => name.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))); await settle();
    await act(async () => button('Line (L)').click());
    await pointer(svg(), 'pointerdown', 370, 220); await pointer(svg(), 'pointerup', 370, 220);
    await pointer(svg(), 'pointerdown', 480, 260); await pointer(svg(), 'pointerup', 480, 260); await settle();
    await act(async () => svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    const added = envelopeFor(source).sketch.entities.find((e: { id: string; reference?: boolean }) => !e.reference && e.id !== 'piston');
    expect(added).toBeDefined();
    await pointer(host.querySelector(`.sketch-entity[data-id="${added.id}"]`)!); await pointer(svg(), 'pointerup'); await settle();
    await act(async () => button('Construction (X)').click()); await settle();
    const before = envelopeFor(source);
    expect(before.pending).toBe(false);
    expect(before.sketch.entities.find((e: { id: string }) => e.id === added.id).construction).toBe(true);
    const beforeDocument = read()[source]!;
    expect(beforeDocument).toEqual(INITIAL_ASSEMBLY[source]);
    expect(parseSourceAuthoring(before, beforeDocument)).not.toBeNull();
    await act(async () => button('Close').click()); await settle();
    await act(async () => root.render(null)); await mount();
    await act(async () => button(`Edit ${source} contour…`).click()); await settle();
    expect(host.querySelector(`[data-row-id="${dimension.id}"]`)!.textContent).toContain('Diaphragm width');
    expect(host.querySelector(`.sketch-entity[data-id="${added.id}"]`)!.classList.contains('construction')).toBe(true);
    await enter('Patch 1 weight', '.5'); await settle(); // Inspect the reopened controller's published sketch.
    expect(envelopeFor(source).sketch).toEqual(before.sketch);
    expect(read()[source]!.contour).toEqual(beforeDocument.contour);
    expect(envelopeFor(source).meta.weights.piston).toBe(.5);
  });
  it.each(['horn', 'woofer'] as const)('keeps a pending invalid %s sketch beside the last canonical document', async source => {
    storage.setItem('', JSON.stringify(INITIAL_ASSEMBLY)); await mount();
    await act(async () => button(`Edit ${source} contour…`).click()); await settle();
    await pointer(host.querySelector('.sketch-entity[data-id="piston"]')!); await pointer(svg(), 'pointerup');
    await act(async () => button('Delete selection').click()); await settle();
    const before = envelopeFor(source);
    expect(before.pending).toBe(true);
    expect(before.sketch.entities.some((e: { id: string }) => e.id === 'piston')).toBe(false);
    expect(read()[source]).toEqual(INITIAL_ASSEMBLY[source]);
    await act(async () => button('Close').click()); await settle();
    await act(async () => button(`Edit ${source} contour…`).click()); await settle();
    expect(host.querySelector('.sketch-entity[data-id="piston"]')).toBeNull();
    expect(button('Use contour in assembly').disabled).toBe(true);
    expect(envelopeFor(source).sketch).toEqual(before.sketch);
  });
  it('seeds when a companion no longer lowers to the current nested document', async () => {
    const state = seed(INITIAL_ASSEMBLY.horn); state.sketch.entities[0].data = { name: 'Stale name' };
    authoringStorage.setItem('', JSON.stringify({ horn: serializeSourceAuthoring(state) }));
    const changed = structuredClone(INITIAL_ASSEMBLY); changed.horn.drive.motion = 'axial';
    storage.setItem('', JSON.stringify(changed)); await mount();
    await act(async () => button('Edit horn contour…').click()); await settle();
    expect(host.textContent).not.toContain('Stale name');
    await enter('Patch 1 weight', '.5'); await settle();
    expect(envelopeFor('horn').meta.motion).toBe('axial');
    expect(envelopeFor('horn').sketch.entities[0].data?.name).toBeUndefined();
  });
  it('restores a plug dimension after closing, then follows the changed assembly host with a notice', async () => {
    const saved = structuredClone(INITIAL_ASSEMBLY);
    saved.horn = expandedPreset('dome', { radius_mm: 4, height_mm: 1, surround_width_mm: 0, surround_depth_mm: 0, land_width_mm: 0 });
    saved.phase_plugs = [{ id: 'core', z0_mm: 2, z1_mm: 6, inner0_mm: 0, outer0_mm: 2, inner1_mm: 0, outer1_mm: 3 }];
    storage.setItem('', JSON.stringify(saved)); await mount();
    await act(async () => button('Edit horn contour…').click()); await settle();
    await act(async () => button('Dimension (D)').click());
    const pole = host.querySelector('.sketch-point[data-id="pole"]')!;
    await pointer(pole, 'pointerdown', Number(pole.getAttribute('cx')), Number(pole.getAttribute('cy'))); await pointer(svg(), 'pointerup');
    await pointer(host.querySelector('[data-context-id="plug/core/inlet"] .sketch-context-hit')!); await settle();
    await pointer(svg(), 'pointermove', 300, 110); await pointer(svg(), 'pointerdown', 300, 110); await pointer(svg(), 'pointerup', 300, 110); await settle();
    await act(async () => svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(envelopeFor('horn').sketch.constraints.some((c: { kind: string }) => c.kind === 'pointLineDistance')).toBe(true);
    await act(async () => button('Close').click()); await settle();
    await enter('Plug inlet Z', '3');
    await act(async () => button('Edit horn contour…').click()); await settle();
    expect(host.textContent).toContain('Reference context changed; contour updated');
    expect(read().horn.contour.points[0].z_mm).toBeCloseTo(2, 8);
    const envelope = envelopeFor('horn');
    const reference = envelope.sketch.entities.find((e: { data?: { context?: string } }) => e.data?.context === 'plug/core/inlet');
    expect(envelope.sketch.points.find((p: { id: string }) => p.id === reference.p1).y).toBe(3);
  });
  it('reconciles companions when the restored assembly subscription adopts a different document', async () => {
    let restore!: (raw: string | null) => void;
    vi.spyOn(durableSettings, 'subscribe').mockImplementation((namespace, listener) => { if (namespace === 'sourceAssemblyDraft') restore = listener; return () => {}; });
    const horn = seed(INITIAL_ASSEMBLY.horn), woofer = seed(INITIAL_ASSEMBLY.woofer!);
    authoringStorage.setItem('', JSON.stringify({ horn: serializeSourceAuthoring(horn), woofer: serializeSourceAuthoring(woofer) }));
    storage.setItem('', JSON.stringify(INITIAL_ASSEMBLY)); await mount();
    const changed = structuredClone(INITIAL_ASSEMBLY); changed.horn.drive.motion = 'axial';
    localStorage.setItem(SETTINGS_NAMESPACES.sourceAssemblyDraft, JSON.stringify(changed));
    await act(async () => restore(JSON.stringify(changed))); await settle();
    expect(host.textContent).toContain('Restored source assembly draft.');
    expect(JSON.parse(authoringStorage.getItem('')!)).toEqual({ woofer: serializeSourceAuthoring(woofer) });
  });
  it('passes the resolved current-horn throat into the nested editor as an exact read-only limit', async () => {
    vi.mocked(sourceEditorApi.hornProfile).mockResolvedValue(profile);
    await mount();
    await act(async () => button('Edit horn contour…').click()); await settle();
    const label = host.querySelector('.sketch-limit text')!;
    expect(label.textContent).toContain('Throat r 7.00');
    expect(label.querySelector('title')?.textContent).toBe('Set by the horn design');
    await enter('Patch 1 weight', '.5');
    expect(read().horn.contour.points.at(-1)!.r_mm).toBe(7);
  });
  it('writes nested aperture label edits to the assembly and reflects the assembly field on reopening', async () => {
    storage.setItem('', JSON.stringify(INITIAL_ASSEMBLY)); await mount();
    await act(async () => button('Edit woofer contour…').click()); await settle();
    await act(async () => host.querySelector('.sketch-limit text')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })));
    const input = host.querySelector<HTMLInputElement>('[aria-label="Limit radius"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '10'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => host.querySelector('form[aria-label="Edit limit"]')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))); await settle();
    expect(read().dimensions.aperture_radius_mm).toBe(10);
    await act(async () => button('Close').click()); await settle();
    expect(Number(field('Woofer aperture radius').value)).toBe(10);
    await enter('Woofer aperture radius', '12');
    await act(async () => button('Edit woofer contour…').click()); await settle();
    expect(host.querySelector('.sketch-limit text')?.textContent).toContain('Aperture r 12.00');
  });
  it('seeds a fresh assembly from the current design with source clearance and enclosure margins', async () => {
    vi.mocked(sourceEditorApi.hornProfile).mockResolvedValue(profile);
    const current = serializeDesign(useDesignStore.getState().design);
    await mount();
    expect(sourceEditorApi.hornProfile).toHaveBeenCalledExactlyOnceWith(current);
    expect(field('Enclosure width').value).toBe('180.00');
    expect(field('Enclosure height').value).toBe('216.00');
    expect(field('Enclosure depth').value).toBe('140.00');
    expect(host.textContent).toContain('Mouth radius: 80.00 mm');
    expect(storage.getItem('')).toBeNull(); // Opening alone must not replace hydrated settings.
    await enter('Front Z', '8');
    expect(read()).toMatchObject({ horn_config: profile.horn_config, dimensions: {
      horn_xy_mm: [0, 18], woofer_xy_mm: [0, -90], horn_length_mm: 130, mouth_radius_mm: 80,
    } });
    expect(read().horn.contour.points.at(-1)!.r_mm).toBe(7);
    await act(async () => useDesignStore.getState().updateValue('L', 75)); await settle();
    expect(sourceEditorApi.hornProfile).toHaveBeenCalledTimes(1);
    expect(read().dimensions.horn_length_mm).toBe(130);
  });
  it('seeds the enclosure from the widest wall radius when a rollback exceeds the mouth', async () => {
    vi.mocked(sourceEditorApi.hornProfile).mockResolvedValue({ ...profile, wall_max_radius_mm: 90 });
    await mount();
    expect(field('Enclosure width').value).toBe('200.00');
    expect(field('Enclosure height').value).toBe('236.00');
    await enter('Front Z', '8');
    expect(read().dimensions).toMatchObject({ horn_xy_mm: [0, 18], woofer_xy_mm: [0, -100], mouth_radius_mm: 80 });
  });
  it('does not seed once the user has focused an input in the dialog', async () => {
    let resolve!: (value: typeof profile) => void;
    vi.mocked(sourceEditorApi.hornProfile).mockImplementationOnce(() => new Promise((r) => { resolve = r; }));
    await mount();
    await act(async () => field('Enclosure width').focus());
    await act(async () => resolve(profile)); await settle();
    // The focused field edits its stored value; the unseeded draft is still 70 x 40 mm.
    expect(field('Enclosure width').value).toBe('70');
    expect(field('Enclosure depth').value).toBe('40.00');
    expect(storage.getItem('')).toBeNull();
  });
  it('saves a never-edited seeded draft after a successful prepare or export', async () => {
    vi.mocked(sourceEditorApi.hornProfile).mockResolvedValue(profile);
    vi.mocked(sourceEditorApi.exportAssembly).mockResolvedValue(new Blob(['x']));
    vi.mocked(prepareNativeAssembly).mockResolvedValue({ mesh: { stats: { triangle_count: 12 } } } as never);
    URL.createObjectURL = vi.fn(() => 'blob:x'); URL.revokeObjectURL = vi.fn();
    await mount();
    expect(storage.getItem('')).toBeNull();
    await act(async () => button('Prepare assembly in WG').click()); await settle();
    expect(read()).toMatchObject({ horn_config: profile.horn_config, dimensions: { width_mm: 180, mouth_radius_mm: 80 } });
    storage.removeItem('');
    await act(async () => button('Export assembly STEP + mesh').click()); await settle();
    expect(read().dimensions.width_mm).toBe(180);
  });
  it('opens Identifiers when a passive body ID is empty or duplicated', async () => {
    await mount(); await act(async () => button('Add central plug').click()); await settle();
    await act(async () => button('Add annular vane').click()); await settle();
    const disclosure = host.querySelector<HTMLDetailsElement>('details.source-identifiers')!;
    expect(disclosure.open).toBe(false);
    const second = host.querySelector<HTMLInputElement>('[aria-label="Passive body 2 ID"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(second, read().phase_plugs[0].id); second.dispatchEvent(new Event('input', { bubbles: true })); }); await settle();
    expect(disclosure.open).toBe(true);
  });
  it('keeps the standalone defaults when the current horn cannot resolve', async () => {
    await mount();
    expect(field('Enclosure width').value).toBe('70.00');
    expect(field('Horn mouth radius').value).toBe('10.00');
    expect(sourceEditorApi.validateAssembly).toHaveBeenLastCalledWith(INITIAL_ASSEMBLY);
  });
  it('never seeds a saved assembly, including one without a saved horn profile', async () => {
    const saved = structuredClone(INITIAL_ASSEMBLY); saved.dimensions.width_mm = 123.456789;
    storage.setItem('', JSON.stringify(saved));
    vi.mocked(sourceEditorApi.hornProfile).mockResolvedValue(profile);
    await mount();
    expect(sourceEditorApi.hornProfile).not.toHaveBeenCalled();
    expect(read()).toEqual(saved);
    expect(field('Enclosure width').value).toBe('123.46');
    await act(async () => field('Enclosure width').focus());
    await act(async () => field('Enclosure width').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
    await enter('Front Z', '8');
    expect(read().dimensions.width_mm).toBe(123.456789);
  });
  it.each(['edit', 'load'] as const)('does not replace a %s made while the initial horn resolves', async (action) => {
    let resolve!: (value: typeof profile) => void;
    vi.mocked(sourceEditorApi.hornProfile).mockImplementationOnce(() => new Promise((r) => { resolve = r; }));
    await mount();
    if (action === 'edit') await enter('Enclosure width', '99');
    else {
      const saved = structuredClone(INITIAL_ASSEMBLY); saved.dimensions.width_mm = 99;
      const input = host.querySelector('[aria-label="Load assembly draft"]') as HTMLInputElement;
      Object.defineProperty(input, 'files', { configurable: true, value: [{ size: 100, text: async () => JSON.stringify(saved) }] });
      await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })));
    }
    await act(async () => resolve(profile)); await settle();
    expect(read().dimensions.width_mm).toBe(99);
    expect(read().horn_config).toBeUndefined();
  });
  it('lets a hydrated saved draft win over an in-flight initial profile request', async () => {
    let resolve!: (value: typeof profile) => void;
    let adopt!: (raw: string | null) => void;
    vi.mocked(sourceEditorApi.hornProfile).mockImplementationOnce(() => new Promise((r) => { resolve = r; }));
    vi.spyOn(durableSettings, 'subscribe').mockImplementation((namespace, listener) => {
      expect(namespace).toBe('sourceAssemblyDraft'); adopt = listener; return () => {};
    });
    await mount();
    const remote = structuredClone(INITIAL_ASSEMBLY); remote.dimensions.width_mm = 125;
    await act(async () => { localStorage.setItem(SETTINGS_NAMESPACES.sourceAssemblyDraft, JSON.stringify(remote)); adopt(JSON.stringify(remote)); });
    await act(async () => resolve(profile)); await settle();
    expect(field('Enclosure width').value).toBe('125.00');
    expect(read()).toEqual(remote);
  });
  it('does not change the assembly underneath an open nested contour editor', async () => {
    let resolve!: (value: typeof profile) => void;
    vi.mocked(sourceEditorApi.hornProfile).mockImplementationOnce(() => new Promise((r) => { resolve = r; }));
    await mount();
    await act(async () => button('Edit horn contour…').click()); await settle();
    await act(async () => resolve(profile)); await settle();
    await enter('Patch 1 weight', '.5');
    await act(async () => button('Use contour in assembly').click()); await settle();
    expect(read().horn_config).toBeUndefined();
    expect(read().dimensions).toEqual(INITIAL_ASSEMBLY.dimensions);
    expect(read().horn.drive.weights.piston).toBe(.5);
    expect(document.activeElement).toBe(button('Close'));
  });
  it('places each toggle after its label on a shared row and still toggles by label', async () => {
    await mount();
    const style = document.createElement('style');
    style.textContent = new TextDecoder().decode(readFileSync('src/design/sourceContourEditor.css'));
    document.head.append(style);
    const rows = [...host.querySelectorAll<HTMLLabelElement>('.source-toggle-row')];
    expect(rows.map((row) => row.firstElementChild?.textContent)).toEqual(['Include woofer', 'Attach to a resolved horn profile']);
    expect(rows.every((row) => row.lastElementChild?.matches('input[type="checkbox"]'))).toBe(true);
    try {
      for (const row of rows) {
        expect(getComputedStyle(row).display).toBe('flex');
        expect(getComputedStyle(row).justifyContent).toBe('space-between');
        expect(getComputedStyle(row.querySelector('input')!).width).toBe('auto');
        expect(getComputedStyle(row.querySelector('input')!).minWidth).toBe('0px');
      }
    } finally { style.remove(); }
    await act(async () => rows[0].click()); await settle();
    expect(read().woofer).toBeNull();
    await act(async () => rows[1].click()); await settle();
    expect(read().horn_config).toBeDefined();
    await act(async () => rows[1].click()); await settle();
    expect(read().horn_config).toBeUndefined();
  });
  it('keeps passive body identifiers editable in a collapsed disclosure and validates changes', async () => {
    await mount(); await act(async () => button('Add central plug').click()); await settle();
    const input = host.querySelector<HTMLInputElement>('[aria-label="Passive body 1 ID"]')!;
    const disclosure = input.closest('details')!;
    expect(disclosure.open).toBe(false); expect(disclosure.querySelector('summary')?.textContent).toBe('Identifiers');
    await act(async () => { disclosure.open = true; input.focus(); });
    vi.mocked(sourceEditorApi.validateAssembly).mockRejectedValueOnce(new Error('Invalid body identifier'));
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, ''); input.dispatchEvent(new Event('input', { bubbles: true })); }); await settle();
    expect(read().phase_plugs[0].id).toBe('');
    expect(host.textContent).toContain('Invalid body identifier');
    expect(button('Prepare assembly in WG').disabled).toBe(true);
  });
  it('adds a central body and annular vane and edits independent radii with shared planes', async () => {
    await mount(); await act(async () => button('Add central plug').click()); await settle();
    await act(async () => button('Add annular vane').click()); await settle();
    await enter('Body 2 inner inlet radius','1.6'); await enter('Body 2 outer inlet radius','2.1');
    await enter('Plug inlet Z','2.2');
    expect(read().phase_plugs.map((p) => p.z0_mm)).toEqual([2.2,2.2]);
    expect(read().phase_plugs[1]).toMatchObject({ inner0_mm:1.6,outer0_mm:2.1 });
    expect(read().horn.drive.channel_id).toBe('hf'); expect(read().woofer!.drive.channel_id).toBe('lf');
  });
  it('uses the canonical point/arc editor for each physical source and preserves literal weights', async () => {
    await mount(); await act(async () => button('Edit horn contour…').click()); await settle();
    await enter('Patch 1 weight','-.5');
    await act(async () => button('Use contour in assembly').click()); await settle();
    expect(read().horn.drive.weights.piston).toBe(-.5);
    expect(read().woofer!.drive.weights.piston).toBe(1);
    expect(button('Prepare assembly in WG').disabled).toBe(false);
  });
  it('rejects unsafe draft shapes before rendering and round trips passive bodies', () => {
    const document:SourceAssemblyDocument=structuredClone(INITIAL_ASSEMBLY);
    document.phase_plugs=[{id:'vane',z0_mm:2,z1_mm:6,inner0_mm:2,outer0_mm:2.5,inner1_mm:2.4,outer1_mm:2.9}];
    expect(parseAssemblyDraft(JSON.stringify(document))).toEqual(document);
    const bad=structuredClone(document); bad.horn.contour.segments[0].kind='arc'; bad.horn.contour.segments[0].center_mm=null;
    expect(() => parseAssemblyDraft(JSON.stringify(bad))).toThrow('draft');
  });
  it('copies the current horn profile without rescaling the authored source or tracking later design edits', async () => {
    const profile = { horn_config: { formula: 'OSSE', mode: 'bare', profile: { r0_mm: 7, L_mm: 30 } }, throat_radius_mm: 7, horn_length_mm: 30, mouth_radius_mm: 16 };
    vi.mocked(sourceEditorApi.hornProfile).mockResolvedValue(profile);
    storage.setItem('', JSON.stringify(INITIAL_ASSEMBLY));
    const current = serializeDesign(useDesignStore.getState().design);
    await mount();
    await act(async () => button('Use current horn design').click()); await settle();
    expect(sourceEditorApi.hornProfile).toHaveBeenCalledWith(current);
    expect(read().horn).toEqual(INITIAL_ASSEMBLY.horn);
    expect(read().horn_config).toEqual(profile.horn_config);
    expect(host.textContent).toContain('throat radius is 7 mm');
    expect([...host.querySelectorAll('.field-name')].map((node) => node.textContent)).not.toContain('Horn length');
    const frozen = read();
    await act(async () => useDesignStore.getState().updateValue('L', 75)); await settle();
    expect(read()).toEqual(frozen);
  });
  it('retains a horn-only profile in a loaded draft and hides unrelated woofer controls', async () => {
    const value = structuredClone(INITIAL_ASSEMBLY); value.woofer = null;
    value.dimensions.woofer_xy_mm = [0, 0]; value.dimensions.aperture_radius_mm = 0;
    value.horn_config = { formula: 'OSSE', mode: 'bare', profile: { r0_mm: 4, L_mm: 24 } };
    vi.mocked(sourceEditorApi.validateAssembly).mockResolvedValue({ geometry_sha256: 'g', passage_contract: null, horn_section_mm: { 'horn-wall': [[4, 0], [10, 24]] } });
    storage.setItem('', JSON.stringify(value));
    await mount();
    expect(read()).toEqual(value);
    expect(host.textContent).toContain('Horn only');
    expect([...host.querySelectorAll('button')].some((b) => b.textContent === 'Edit woofer contour…')).toBe(false);
    await act(async () => button('Edit horn contour…').click()); await settle();
    await enter('Patch 1 weight', '-.5');
    expect(host.querySelector('.sketch-limit text')?.textContent).toContain('Throat r 4.00 🔒');
    expect(read().horn.contour.points.at(-1)!.r_mm).toBe(4);
    await act(async () => button('Use contour in assembly').click()); await settle();
    expect(read().woofer).toBeNull(); expect(read().horn.drive.weights.piston).toBe(-.5);
    expect(read().horn_config).toEqual(value.horn_config);
  });
  it('replaces advanced text when loading another draft so Apply cannot restore the previous horn', async () => {
    const first = structuredClone(INITIAL_ASSEMBLY); first.horn_config = { formula: 'OSSE', profile: { L_mm: 24 } };
    storage.setItem('', JSON.stringify(first)); await mount();
    const second = structuredClone(first); second.horn_config = { formula: 'R-OSSE', profile: { R_mm: 40 } };
    const input = host.querySelector('[aria-label="Load assembly draft"]') as HTMLInputElement;
    Object.defineProperty(input, 'files', { configurable: true, value: [{ size: 100, text: async () => JSON.stringify(second) }] });
    await act(async () => input.dispatchEvent(new Event('change', { bubbles: true }))); await settle();
    const text = (host.querySelector('[aria-label="Horn configuration"]') as HTMLTextAreaElement).value;
    expect(JSON.parse(text)).toEqual(second.horn_config);
    await act(async () => button('Apply horn configuration').click()); await settle();
    expect(read().horn_config).toEqual(second.horn_config);
  });
  it('keeps prepare and export disabled after a new draft invalidates an older preview', async () => {
    await mount(); let resolve!: (value:Awaited<ReturnType<typeof sourceEditorApi.validateAssembly>>) => void;
    vi.mocked(sourceEditorApi.validateAssembly).mockImplementationOnce(() => new Promise((r) => { resolve=r; }));
    await enter('Enclosure width','72');
    vi.mocked(sourceEditorApi.validateAssembly).mockRejectedValueOnce(new Error('New clearance invalid'));
    await enter('Enclosure width','-1');
    await act(async () => resolve({geometry_sha256:'old',passage_contract:null,horn_section_mm:{}}));
    expect(host.textContent).toContain('New clearance invalid');
    expect(button('Prepare assembly in WG').disabled).toBe(true);
    expect(button('Export assembly STEP + mesh').disabled).toBe(true);
  });
  async function applyConfig(config: Record<string, unknown>) {
    const input = host.querySelector<HTMLTextAreaElement>('[aria-label="Horn configuration"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, JSON.stringify(config)); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => button('Apply horn configuration').click()); await settle();
  }
  it('resolves horn context independently of invalid sources and never fetches per nested edit', async () => {
    const saved = structuredClone(INITIAL_ASSEMBLY); saved.horn_config = { formula: 'OSSE', profile: { r0_mm: 4, L_mm: 24 } };
    storage.setItem('', JSON.stringify(saved));
    vi.mocked(sourceEditorApi.validateAssembly).mockRejectedValue(new Error('Invalid source'));
    await mount();
    expect(sourceEditorApi.hornWall).toHaveBeenCalledExactlyOnceWith(saved.horn_config);
    await act(async () => button('Edit horn contour…').click()); await settle();
    expect(host.querySelector('.sketch-limit text')?.textContent).toContain('Throat r 4.00');
    expect(host.querySelector('[data-context-id="horn.wall"]')).not.toBeNull();
    await enter('Patch 1 weight', '.5');
    expect(sourceEditorApi.hornWall).toHaveBeenCalledTimes(1);
  });
  it('ignores stale wall responses and caches successful requests by configuration JSON', async () => {
    const saved = structuredClone(INITIAL_ASSEMBLY);
    const first = { formula: 'OSSE', profile: { r0_mm: 4, L_mm: 24 } };
    const second = { formula: 'OSSE', profile: { r0_mm: 9, L_mm: 30 } };
    saved.horn_config = first; storage.setItem('', JSON.stringify(saved));
    let resolveFirst!: (value: Awaited<ReturnType<typeof sourceEditorApi.hornWall>>) => void;
    vi.mocked(sourceEditorApi.hornWall).mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve; }));
    await mount(); await applyConfig(second);
    await act(async () => resolveFirst({ wall_mm: [[4, 0], [10, 24]], throat_radius_mm: 4, horn_length_mm: 24, mouth_radius_mm: 10 })); await settle();
    await act(async () => button('Edit horn contour…').click()); await settle();
    expect(host.querySelector('.sketch-limit text')?.textContent).toContain('Throat r 9.00');
    await act(async () => button('Close').click()); await settle();
    await applyConfig(first);
    expect(sourceEditorApi.hornWall).toHaveBeenCalledTimes(2);
    await act(async () => button('Edit horn contour…').click()); await settle();
    expect(host.querySelector('.sketch-limit text')?.textContent).toContain('Throat r 4.00');
  });
  it('retains the last physical context greyed while loading and after failure when the current design resolved its throat', async () => {
    const saved = structuredClone(INITIAL_ASSEMBLY); saved.horn_config = { formula: 'OSSE', profile: { r0_mm: 4, L_mm: 24 } };
    storage.setItem('', JSON.stringify(saved)); await mount();
    let reject!: (reason: Error) => void;
    vi.mocked(sourceEditorApi.hornWall).mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }));
    vi.mocked(sourceEditorApi.hornProfile).mockResolvedValue({ horn_config: { formula: 'OSSE', profile: { r0_mm: 9, L_mm: 30 } }, throat_radius_mm: 9, horn_length_mm: 30, mouth_radius_mm: 10 });
    await act(async () => button('Use current horn design').click()); await settle();
    await act(async () => button('Edit horn contour…').click()); await settle();
    const previous = host.querySelector('[data-context-id="horn.wall"] > path')!.getAttribute('d');
    expect(host.textContent).toContain('Context out of date');
    expect(host.querySelector('[data-context-id="horn.throat"] text')?.textContent).toContain('Throat r 9.00');
    await act(async () => reject(new Error('Wall unavailable'))); await settle();
    expect(host.querySelector('[data-context-id="horn.wall"] > path')!.getAttribute('d')).toBe(previous);
    expect(host.querySelector('.sketch-context-layer')?.classList.contains('stale')).toBe(true);
    expect(host.textContent).toContain('Context out of date');
  });
  it('guards unresolved throat edits after a configuration change and retries rejected cached requests', async () => {
    const saved = structuredClone(INITIAL_ASSEMBLY), first = { formula: 'OSSE', profile: { r0_mm: 4, L_mm: 24 } };
    saved.horn_config = first;
    storage.setItem('', JSON.stringify(saved));
    let reject!: (reason: Error) => void;
    vi.mocked(sourceEditorApi.hornWall).mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }));
    await mount();
    await act(async () => button('Edit horn contour…').click()); await settle();
    expect(host.querySelector('[aria-label="Source contour editor"]')).toBeNull();
    expect(host.textContent).toContain('Use current horn design to resolve the throat before editing its contour');
    await act(async () => reject(new Error('Transient failure'))); await settle();
    await act(async () => button('Edit horn contour…').click()); await settle();
    expect(host.querySelector('[aria-label="Source contour editor"]')).toBeNull();
    const second = { formula: 'OSSE', profile: { r0_mm: 9, L_mm: 30 } };
    await applyConfig(second); await applyConfig(first);
    expect(sourceEditorApi.hornWall).toHaveBeenCalledTimes(3);
    await act(async () => button('Edit horn contour…').click()); await settle();
    expect(host.querySelector('.sketch-limit text')?.textContent).toContain('Throat r 4.00');
  });

});
