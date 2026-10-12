import { act } from 'react';
import { readFileSync } from 'node:fs';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sourceEditorApi, type SourceAssemblyDocument } from '../api/sourceEditor';
import { durableSettings, namespaceStorage, SETTINGS_NAMESPACES } from '../stores/durableSettings';
import { serializeDesign, useDesignStore } from '../stores/design';
import { prepareNativeAssembly } from './nativeAssemblyPreparation';
import { AssemblyEditor, INITIAL_ASSEMBLY, parseAssemblyDraft } from './SourceAssemblyEditor';

vi.mock('../api/sourceEditor', () => ({ sourceEditorApi:{ validateAssembly:vi.fn(), hornProfile:vi.fn(), ingestAssembly:vi.fn(), exportAssembly:vi.fn(), validate:vi.fn(), presets:vi.fn() } }));
vi.mock('./nativeAssemblyPreparation', () => ({ prepareNativeAssembly:vi.fn() }));
const storage = namespaceStorage('sourceAssemblyDraft');
describe('shared source and passage authoring', () => {
  let host:HTMLDivElement; let root:Root;
  beforeEach(() => {
    vi.useFakeTimers(); vi.clearAllMocks(); storage.removeItem('');
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT:boolean }).IS_REACT_ACT_ENVIRONMENT=true;
    vi.mocked(sourceEditorApi.validateAssembly).mockResolvedValue({ geometry_sha256:'geometry', passage_contract:null, horn_section_mm:{} });
    vi.mocked(sourceEditorApi.hornProfile).mockRejectedValue(new Error('No resolved profile'));
    vi.mocked(sourceEditorApi.presets).mockResolvedValue([]);
    vi.mocked(sourceEditorApi.validate).mockImplementation(async (document) => ({ document, geometry_sha256:'g',excitation_sha256:'e',meridian:Object.fromEntries(document.contour.segments.map((s,i) => [s.id,document.contour.points.slice(i,i+2).map((p) => [p.r_mm,p.z_mm])])) }));
    host=document.createElement('div'); document.body.append(host); root=createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); vi.useRealTimers(); vi.restoreAllMocks(); });
  const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(200); }); };
  const mount = async () => { await act(async () => root.render(<AssemblyEditor onClose={() => {}}/>)); await settle(); };
  const button = (text:string) => [...host.querySelectorAll('button')].find((b) => b.textContent===text)!;
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
    storage.setItem('', JSON.stringify(value));
    await mount();
    expect(read()).toEqual(value);
    expect(host.textContent).toContain('Horn only');
    expect([...host.querySelectorAll('button')].some((b) => b.textContent === 'Edit woofer contour…')).toBe(false);
    await act(async () => button('Edit horn contour…').click()); await settle();
    await enter('Patch 1 weight', '-.5');
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
});
