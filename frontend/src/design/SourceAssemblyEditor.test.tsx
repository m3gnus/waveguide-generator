import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sourceEditorApi, type SourceAssemblyDocument } from '../api/sourceEditor';
import { namespaceStorage } from '../stores/durableSettings';
import { serializeDesign, useDesignStore } from '../stores/design';
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
    vi.mocked(sourceEditorApi.presets).mockResolvedValue([]);
    vi.mocked(sourceEditorApi.validate).mockImplementation(async (document) => ({ document, geometry_sha256:'g',excitation_sha256:'e',meridian:Object.fromEntries(document.contour.segments.map((s,i) => [s.id,document.contour.points.slice(i,i+2).map((p) => [p.r_mm,p.z_mm])])) }));
    host=document.createElement('div'); document.body.append(host); root=createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); vi.useRealTimers(); vi.restoreAllMocks(); });
  const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(200); }); };
  const mount = async () => { await act(async () => root.render(<AssemblyEditor onClose={() => {}}/>)); await settle(); };
  const button = (text:string) => [...host.querySelectorAll('button')].find((b) => b.textContent===text)!;
  const read = () => parseAssemblyDraft(storage.getItem('')!);
  async function enter(label:string, value:string) {
    const owner=[...host.querySelectorAll('label')].find((l) => l.querySelector('.field-name')?.textContent===label)!;
    const input=document.getElementById(owner.htmlFor) as HTMLInputElement;
    await act(async () => { input.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,value); input.dispatchEvent(new Event('input',{bubbles:true})); });
    await act(async () => input.blur()); await settle();
  }
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
