import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { useStore } from 'zustand';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { hydrateDesignDocument, sendDesignToCad } from '../api/designIo';
import { createCadRecoveryNoticesStore, type CadRecoveryNotice, type CadRecoveryNoticesStore } from '../stores/cadRecoveryNotices';
import { workspaceModeStore } from '../stores/workspaceMode';
import { resetSolveAttentionForTests, solveAttention, useRecoveryNoticeAttention } from './solveAttention';
import { bindWorkspaceNavigation, publishVisiblePanels, resetWorkspaceNavigationForTests, workspaceNavigation, type WorkspacePanel } from './workspaceNavigation';

const notice = (overrides: Partial<CadRecoveryNotice> = {}): CadRecoveryNotice => ({
  id: 'mutation', kind: 'update_link', document_id: 'fusion:active', document_name: 'Document',
  created_at: '2026-10-01T10:00:00Z', sequence: 1, active_document: true, ...overrides,
});
function Attention({ store }: { store: CadRecoveryNoticesStore }) {
  const notices = useStore(store, (state) => state.notices);
  const count = useRecoveryNoticeAttention(notices);
  return count ? <button onClick={() => { workspaceModeStore.setMode('cad'); workspaceNavigation.navigate('geometry'); }}>Recovery notices ({count})</button> : null;
}
describe('recovery attention without the coordinator', () => {
  let host: HTMLDivElement;
  let root: Root;
  let store: CadRecoveryNoticesStore;
  let activations: WorkspacePanel[];
  beforeEach(async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetSolveAttentionForTests(); resetWorkspaceNavigationForTests();
    workspaceModeStore.setMode('cad');
    activations = [];
    bindWorkspaceNavigation((panel) => { activations.push(panel); publishVisiblePanels([panel]); return true; });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    store = createCadRecoveryNoticesStore();
    await act(async () => root.render(<Attention store={store}/>));
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); workspaceModeStore.setMode('parametric'); });
  const arrive = (item: CadRecoveryNotice) => act(() => store.setState({ notices: { [item.id]: item } }));

  it('fronts a newly actionable active-document recovery only once', () => {
    solveAttention.armOperation('mutation');
    arrive(notice()); arrive(notice());
    expect(activations).toEqual(['geometry']);
  });
  it('keeps an unarmed recovery discoverable after navigation without arming at discovery', () => {
    act(() => workspaceNavigation.navigate('geometry')); activations.length = 0;
    arrive(notice());
    expect(activations).toEqual([]);
    expect(host.textContent).toContain('Recovery notices (1)');
  });
  it.each([false, true])('captures submission navigation before awaiting the mutation id (navigate=%s)', async (navigate) => {
    let complete!: (response: Response) => void;
    const response = new Promise<Response>((resolve) => { complete = resolve; });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({ selected: true })))
      .mockImplementationOnce(() => response);
    // Exercise the submission boundary; no test pre-arming.
    const sending = sendDesignToCad(hydrateDesignDocument({ formula: 'OSSE' }), 1, 'horn', null, fetcher, 'press');
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    if (navigate) act(() => workspaceNavigation.navigate('geometry'));
    activations.length = 0;
    complete(new Response(JSON.stringify({ cadHandoffRequestId: 'mutation', cadHandoff: 'published' })));
    await sending;
    arrive(notice());
    expect(activations).toEqual(navigate ? [] : ['geometry']);
    expect(host.textContent).toContain('Recovery notices (1)');
  });
  it('keeps recovery changes discoverable after navigation without yanking the user', () => {
    solveAttention.armOperation('mutation');
    act(() => workspaceNavigation.navigate('geometry')); activations.length = 0;
    arrive(notice());
    expect(activations).toEqual([]);
    expect(host.textContent).toContain('Recovery notices (1)');
    arrive(notice({ active_document: false })); arrive(notice());
    expect(activations).toEqual([]);
    act(() => host.querySelector('button')!.click());
    expect(activations).toEqual(['geometry']);
    act(() => store.setState({ notices: {} }));
    expect(host.querySelector('button')).toBeNull();
  });
  it('keeps earlier-document recovery quiet but discoverable, then fronts when active', () => {
    solveAttention.armOperation('mutation');
    arrive(notice({ active_document: false }));
    expect(activations).toEqual([]);
    expect(host.textContent).toContain('Recovery notices (1)');
    arrive(notice());
    expect(activations).toEqual(['geometry']);
  });
  it('never changes Parametric mode automatically and leaves a route to the notice', () => {
    act(() => workspaceModeStore.setMode('parametric'));
    arrive(notice());
    expect(activations).toEqual([]);
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
    expect(host.querySelector('button')).not.toBeNull();
  });
});
