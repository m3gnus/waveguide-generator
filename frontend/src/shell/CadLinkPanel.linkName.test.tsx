import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cadWorkspace } from '../stores/cadWorkspace';
import { preferencesStore } from '../prefs/preferences';
import { useCadConnectionStore, type CadConnectionStatus } from '../stores/cadConnection';
import { resetCadReturnStore } from '../stores/cadReturn';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { nativeCadLimitation, NATIVE_FAMILIES } from '../design/nativeFamilies';
import { workspaceModeStore } from '../stores/workspaceMode';
import { CadLinkPanel } from './CadLinkPanel';

const link = (instanceId: string, linkName?: string | null) => ({
  instanceId, designName: 'WG design', linkName,
} as NonNullable<CadConnectionStatus['link']>);
const fusionStatus = (links = [link('instance-1', 'Upper horn')]): CadConnectionStatus => ({
  cadApplication: 'fusion360', cadFolderConfigured: true, state: 'current', running: true,
  documentName: 'Assembly document', currentFormula: 'OSSE', link: links[0], matchingLinks: links,
  selectedInstanceId: links[0].instanceId, observationFreshness: 'current',
} as CadConnectionStatus);

describe('CadLinkPanel Fusion connection', () => {
  let host: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  const originalPreferences = preferencesStore.getSnapshot();
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore(); resetCadReturnStore(); useCadConnectionStore.invalidate();
    preferencesStore.update({ cadApplication: 'fusion360' });
    workspaceModeStore.setMode('cad');
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 404 })));
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.spyOn(cadWorkspace, 'getSnapshot').mockReturnValue(cadWorkspace.getSnapshot());
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });
  afterEach(() => {
    act(() => root.unmount()); host.remove(); client.clear();
    useCadConnectionStore.invalidate(); workspaceModeStore.setMode('parametric');
    preferencesStore.update(originalPreferences); vi.restoreAllMocks(); vi.unstubAllGlobals();
  });
  function render(status: CadConnectionStatus | null, overrides: Partial<ReturnType<typeof cadWorkspace.getSnapshot>> = {}) {
    const snapshot = { ...cadWorkspace.getSnapshot(), fusionStatus: status, ...overrides };
    vi.spyOn(cadWorkspace, 'getSnapshot').mockReturnValue(snapshot);
    act(() => root.render(<QueryClientProvider client={client}><CadLinkPanel/></QueryClientProvider>));
  }

  it.each(['Upper horn', undefined, null, ''])('keeps linkName %s out of the connection card', (name) => {
    render(fusionStatus([link('instance-1', name)]));
    const card = host.querySelector('.cad-link-card')!;
    expect(card.textContent).toContain('Assembly document');
    expect(card.textContent).not.toContain('Fusion link:');
    expect(card.textContent).not.toContain('Upper horn');
  });

  it.each([undefined, null, '', '   '])('preserves the exact WG design chooser label when linkName is %s', (name) => {
    const designName = '  WG design \u00a0 ';
    render(fusionStatus([{ ...link('instance-1', name), designName }, link('instance-2')]));
    const select = host.querySelector<HTMLSelectElement>('[aria-label="Linked Fusion instance"]')!;
    expect(select.options[1].textContent).toBe(`${designName} · instance-1`);
    expect(select.options[1].value).toBe('instance-1');
  });

  it('uses labels in the instance chooser while preserving exact instance IDs', () => {
    render(fusionStatus([link('instance-1', 'Upper horn'), link('instance-2', 'Lower horn')]));
    const select = host.querySelector<HTMLSelectElement>('[aria-label="Linked Fusion instance"]')!;
    expect([...select.options].map((option) => [option.value, option.textContent])).toEqual([
      ['', 'Choose an instance'], ['instance-1', 'Upper horn · instance-1'], ['instance-2', 'Lower horn · instance-2'],
    ]);
  });

  it('uses connection store labels in the chooser while preserving the document card', async () => {
    await useCadConnectionStore.read(async () => fusionStatus([link('instance-1', 'Store label'), link('instance-2', 'Second label')]));
    render(fusionStatus([link('instance-1'), link('instance-2')]));
    const select = host.querySelector<HTMLSelectElement>('[aria-label="Linked Fusion instance"]')!;
    expect([...select.options].map((option) => [option.value, option.textContent])).toEqual([
      ['', 'Choose an instance'], ['instance-1', 'Store label · instance-1'], ['instance-2', 'Second label · instance-2'],
    ]);
    expect(host.querySelector('.cad-link-card')?.textContent).not.toContain('Fusion link:');
    expect(host.querySelector('.cad-link-card')?.textContent).toContain('Assembly document');
  });

  it('keeps a connection store label out of the card without a coordinator status', async () => {
    await useCadConnectionStore.read(async () => fusionStatus([link('instance-1', 'Store label')]));
    render(null);
    expect(host.querySelector('.cad-link-card')?.textContent).not.toContain('Fusion link:');
    expect(host.querySelector('.cad-link-card')?.textContent).not.toContain('Store label');
  });

  it('keeps one chooser available after selecting a placement and reaching in sync', () => {
    const links = [link('instance-1', 'Upper horn'), link('instance-2', 'Lower horn')];
    const selectInstance = vi.fn();
    render({ ...fusionStatus(links), state: 'instance_selection_required', link: null, selectedInstanceId: null }, { selectFusionInstance: selectInstance });
    const choose = () => host.querySelector<HTMLSelectElement>('[aria-label="Linked Fusion instance"]')!;
    expect(choose().value).toBe('');
    act(() => { choose().value = 'instance-2'; choose().dispatchEvent(new Event('change', { bubbles: true })); });
    expect(selectInstance).toHaveBeenCalledExactlyOnceWith('instance-2');

    render({ ...fusionStatus(links), link: links[1], selectedInstanceId: 'instance-2' }, { selectFusionInstance: selectInstance });
    expect(host.querySelector('details')?.open).toBe(false);
    expect(choose().closest('details')).toBeNull();
    expect(choose().value).toBe('instance-2');
    expect(host.querySelectorAll('[aria-label="Linked Fusion instance"]')).toHaveLength(1);
    act(() => { choose().value = 'instance-1'; choose().dispatchEvent(new Event('change', { bubbles: true })); });
    expect(selectInstance).toHaveBeenLastCalledWith('instance-1');
  });

  it('does not offer a chooser for a single placement', () => {
    render(fusionStatus());
    expect(host.querySelector('[aria-label="Linked Fusion instance"]')).toBeNull();
  });

  it.each([...NATIVE_FAMILIES, 'LOOKUP'] as const)('disables Fusion sends for %s with its limitation note', (formula) => {
    useDesignStore.getState().setFamily(formula);
    const send = vi.fn().mockResolvedValue(undefined);
    const note = nativeCadLimitation(formula)!;
    for (const state of ['closed', 'stale', 'current'] as const) {
      render({ ...fusionStatus(), state, wgChangesAvailable: state === 'stale' }, { sendWgToFusion: send });
      const action = host.querySelector<HTMLButtonElement>(state === 'current' ? '.cad-link-card .cad-link-actions button' : '.cad-primary-action')!;
      expect(action.disabled).toBe(true);
      expect(action.title).toBe(note);
      expect(host.querySelector('.cad-link-card .section-note')?.textContent).toBe(note);
      act(() => action.click());
    }
    expect(send).not.toHaveBeenCalled();
  });

  it('sends supported designs through the existing coordinator action', () => {
    const send = vi.fn().mockResolvedValue(undefined);
    render({ ...fusionStatus(), state: 'closed' }, { sendWgToFusion: send });
    const action = host.querySelector<HTMLButtonElement>('.cad-primary-action')!;
    expect(action.disabled).toBe(false);
    act(() => action.click());
    expect(send).toHaveBeenCalledOnce();
  });

  it.each([{ names: ['wg_depth', 'wg_mouth_overshoot'] }, { names: undefined }])('shows drift names when supplied and retains the count for older add-ins: $names', ({ names }) => {
    const status = fusionStatus();
    render({ ...status, state: 'stale', wgChangesAvailable: true, link: {
      ...status.link!, parameterDriftCount: 2, driftedParameters: names,
    } });
    const card = host.querySelector('.cad-link-card')!;
    expect(card.textContent?.match(/2 managed Fusion parameters have local edits/g)).toHaveLength(1);
    const lists = card.querySelectorAll('[aria-label="Locally edited Fusion parameters"]');
    expect(lists).toHaveLength(names ? 1 : 0);
    if (names) expect([...lists[0].querySelectorAll('li code')].map((item) => item.textContent)).toEqual(names);
  });

});
