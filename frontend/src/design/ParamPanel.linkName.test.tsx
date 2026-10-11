import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cadLinkCoordinatorBridge } from '../shell/CadLinkCoordinator';
import { preferencesStore } from '../prefs/preferences';
import { useCadConnectionStore, type CadConnectionStatus } from '../stores/cadConnection';
import { resetCadReturnStore } from '../stores/cadReturn';
import { resetDesignStore } from '../stores/design';
import { workspaceModeStore } from '../stores/workspaceMode';
import { ParamPanel } from './ParamPanel';

const link = (instanceId: string, linkName?: string | null) => ({
  instanceId, designName: 'WG design', linkName,
} as NonNullable<CadConnectionStatus['link']>);
const fusionStatus = (links = [link('instance-1', 'Upper horn')]): CadConnectionStatus => ({
  cadApplication: 'fusion360', cadFolderConfigured: true, state: 'current', running: true,
  documentName: 'Assembly document', currentFormula: 'OSSE', link: links[0], matchingLinks: links,
  selectedInstanceId: links[0].instanceId, observationFreshness: 'current',
} as CadConnectionStatus);

describe('ParamPanel Fusion link label', () => {
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
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });
  afterEach(() => {
    act(() => root.unmount()); host.remove(); client.clear();
    useCadConnectionStore.invalidate(); workspaceModeStore.setMode('parametric');
    preferencesStore.update(originalPreferences); vi.restoreAllMocks(); vi.unstubAllGlobals();
  });
  function render(status: CadConnectionStatus | null) {
    const snapshot = { ...cadLinkCoordinatorBridge.getSnapshot(), fusionStatus: status };
    vi.spyOn(cadLinkCoordinatorBridge, 'getSnapshot').mockReturnValue(snapshot);
    act(() => root.render(<QueryClientProvider client={client}><ParamPanel tab="geometry"/></QueryClientProvider>));
  }

  it('shows the coordinator linkName today beside the Fusion document status', () => {
    render(fusionStatus());
    const card = host.querySelector('.linked-design-card')!;
    expect(card.textContent).toContain('Assembly document');
    expect(card.textContent).toContain('Fusion link: Upper horn');
    expect(card.textContent).not.toContain('Fusion link: WG design');
  });

  it.each([undefined, null, ''])('falls back to the WG design label when linkName is %s', (name) => {
    render(fusionStatus([link('instance-1', name)]));
    expect(host.querySelector('.linked-design-card')?.textContent).toContain('Fusion link: WG design');
  });

  it('uses labels in the instance chooser while preserving exact instance IDs', () => {
    render(fusionStatus([link('instance-1', 'Upper horn'), link('instance-2', 'Lower horn')]));
    const select = host.querySelector<HTMLSelectElement>('[aria-label="Linked Fusion instance"]')!;
    expect([...select.options].map((option) => [option.value, option.textContent])).toEqual([
      ['', 'Choose an instance'], ['instance-1', 'Upper horn · instance-1'], ['instance-2', 'Lower horn · instance-2'],
    ]);
  });

  it('also displays the new connection store label without a coordinator status', async () => {
    await useCadConnectionStore.read(async () => fusionStatus([link('instance-1', 'Store label')]));
    render(null);
    expect(host.querySelector('.linked-design-card')?.textContent).toContain('Fusion link: Store label');
  });
});
