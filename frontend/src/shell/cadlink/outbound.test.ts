import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createCadConnectionStore } from '../../stores/cadConnection';
import { designForFamily, resetDesignStore, useDesignStore } from '../../stores/design';
import { resetDocumentStore, useDocumentStore } from '../../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../../stores/solveOptions';
import { createCadOutboundController, OutboundSupersededError } from './outbound';
import { deferred, json } from './selection.fixtures';
import { exported, identity, link, status } from './outbound.fixtures';
import menuSource from '../../design/DesignFileMenu.tsx?raw';
import paramSource from '../../design/ParamPanel.tsx?raw';
import panelSource from '../CadLinkPanel.tsx?raw';
import coordinatorSource from '../CadLinkCoordinator.tsx?raw';
import projectSource from '../CadProjectPanel.tsx?raw';
import runSource from '../../jobs/RunExportControl.tsx?raw';

const controllers: ReturnType<typeof createCadOutboundController>[] = [];
const connections: ReturnType<typeof createCadConnectionStore>[] = [];
beforeEach(() => {
  resetDesignStore(); resetDocumentStore(); resetSolveOptionsStore();
  useDocumentStore.getState().setCadLink(identity, 'current');
});
afterEach(() => { controllers.forEach((item) => item.dispose()); connections.forEach((item) => item.invalidate());
  controllers.length = connections.length = 0; vi.restoreAllMocks(); });
function setup(initial = status(), custom?: typeof fetch) {
  let current = initial;
  const connection = createCadConnectionStore(); connections.push(connection);
  const fetcher = custom ?? vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/wglink')
    ? json(exported) : json({ selected: true }));
  const reader = vi.fn(async () => current);
  const controller = createCadOutboundController({ connection, fetcher, readFusionStatus: reader,
    // The selected instance is editor intent, independent of status publication.
    selectedInstanceId: () => link.instanceId });
  controllers.push(controller);
  return { controller, connection, fetcher, reader, setStatus: (value: ReturnType<typeof status>) => { current = value; } };
}
function exportsFrom(fetcher: typeof fetch) {
  return vi.mocked(fetcher).mock.calls.filter(([input]) => String(input).endsWith('/wglink'));
}

describe('outbound commands without a mounted coordinator (row 17)', () => {
  it.each(['sendWgToFusion', 'confirmFusionConflict'] as const)('%s shares every guard and refuses unavailable evidence', async (entry) => {
    const { controller, reader, fetcher } = setup(); reader.mockRejectedValueOnce(new Error('offline'));
    await expect(controller[entry]()).rejects.toThrow('could not check');
    expect(exportsFrom(fetcher)).toHaveLength(0);
  });
  it.each(['current', 'addin_offline', 'addin_outdated', 'instance_selection_required'] as const)(
    'refuses %s at both public entry points', async (state) => {
      const { controller, fetcher } = setup(status({ state }));
      await expect(controller.sendWgToFusion()).rejects.toThrow();
      await expect(controller.confirmFusionConflict()).rejects.toThrow();
      expect(exportsFrom(fetcher)).toHaveLength(0);
    });
  it.each([
    { recoveryRequired: { kind: 'update', operationId: 'old' } },
    { cadConnectionIssue: 'folder_mismatch' },
    { wgChangesAvailable: false, fusionChangesAvailable: true },
    { wgChangesAvailable: false, observationFreshness: 'none' },
    { documentId: null }, { link: null },
  ] as const)('refuses unsafe status %j', async (changes) => {
    const { controller, fetcher } = setup(status(changes as Parameters<typeof status>[0]));
    await expect(controller.sendWgToFusion()).rejects.toThrow();
    expect(exportsFrom(fetcher)).toHaveLength(0);
  });
  it.each(['closed', 'no_document', 'not_linked'] as const)('chooses Insert from fresh %s evidence', async (state) => {
    const { controller, fetcher, reader, connection } = setup(status({ state, link: null, selectedInstanceId: null }));
    await connection.read(async () => status({ state: 'addin_offline' }));
    await controller.sendWgToFusion();
    const body = JSON.parse(String(exportsFrom(fetcher)[0][1]?.body));
    expect(body.expectedFusionDocumentId).toBeNull(); expect(body.expectedFusionInstanceId).toBeNull();
    expect(reader).toHaveBeenCalledOnce();
    expect(useDocumentStore.getState().identity).toEqual(exported.identity);
  });
  it('chooses an exact guarded Update from command-time evidence instead of a held Insert status', async () => {
    const { controller, fetcher, connection } = setup();
    await connection.read(async () => status({ state: 'closed', link: null }));
    await controller.sendWgToFusion();
    expect(JSON.parse(String(exportsFrom(fetcher)[0][1]?.body))).toMatchObject({
      identity, expectedFusionDocumentId: 'document-1', expectedFusionInstanceId: link.instanceId,
      expectedFusionReturnStateHash: link.documentSignatureHash,
    });
  });
  it('parks a two-way conflict, confirms it through the same guards, and can cancel it', async () => {
    const { controller, fetcher } = setup(status({ fusionChangesAvailable: true }));
    expect(await controller.sendWgToFusion()).toBeNull(); expect(exportsFrom(fetcher)).toHaveLength(0);
    expect(controller.getState().pendingFusionConflict).toBe(true);
    controller.cancelFusionConflict(); expect(controller.getState().pendingFusionConflict).toBe(false);
    // Direct confirmation without a displayed conflict cannot overwrite Fusion.
    expect(await controller.confirmFusionConflict()).toBeNull();
    expect(exportsFrom(fetcher)).toHaveLength(0);
    await controller.confirmFusionConflict(); expect(exportsFrom(fetcher)).toHaveLength(1);
  });
  it.each(['target', 'WG revision', 'WG settings', 'refusal'] as const)('rechecks a confirmation after changed %s', async (change) => {
    const { controller, fetcher, setStatus } = setup(status({ fusionChangesAvailable: true }));
    await controller.sendWgToFusion();
    if (change === 'WG revision') useDesignStore.getState().updateField('R', 123);
    else if (change === 'WG settings') useSolveOptionsStore.getState().updatePolar({ distance: 4 });
    else setStatus(status({ fusionChangesAvailable: true, ...(change === 'target'
      ? { documentId: 'another' } : { state: 'addin_outdated' }) }));
    if (change === 'refusal') await expect(controller.confirmFusionConflict()).rejects.toThrow();
    else expect(await controller.confirmFusionConflict()).toBeNull();
    expect(exportsFrom(fetcher)).toHaveLength(0);
  });
  it('runs all guards against the folder picker result and exports the latest edit and name', async () => {
    const pick = deferred<Response>();
    const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/select') ? pick.promise
      : String(input).endsWith('/wglink') ? json(exported) : json({ selected: true }));
    const { controller, reader, setStatus } = setup(status({ cadFolderConfigured: false }), fetcher);
    const sending = controller.sendWgToFusion();
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalled());
    useDesignStore.getState().updateField('R', 432); useDocumentStore.getState().setDesignName('New horn');
    setStatus(status({ fusionChangesAvailable: true })); pick.resolve(json({ selected: true }));
    expect(await sending).toBeNull(); expect(exportsFrom(fetcher)).toHaveLength(0);
    await controller.confirmFusionConflict();
    const body = JSON.parse(String(exportsFrom(fetcher)[0][1]?.body));
    expect(body.baseName).toBe('New_horn'); expect(body.design.R).toBe(432);
    expect(body.designRevision).toBe(useDesignStore.getState().designRevision); expect(reader).toHaveBeenCalledTimes(3);
  });
  it.each(['dismissed', 'unreadable', 'still missing'] as const)('refuses a %s folder choice', async (choice) => {
    const fetcher = vi.fn(async () => json({ selected: choice !== 'dismissed' }));
    const { controller, reader } = setup(status({ cadFolderConfigured: false }), fetcher);
    if (choice === 'unreadable') reader.mockResolvedValueOnce(status({ cadFolderConfigured: false })).mockRejectedValueOnce(new Error('offline'));
    await expect(controller.sendWgToFusion()).rejects.toThrow(); expect(exportsFrom(fetcher)).toHaveLength(0);
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it('joins one folder dialog, then lets only the newest command dispatch', async () => {
    const pick = deferred<Response>();
    const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/select') ? pick.promise
      : String(input).endsWith('/wglink') ? json(exported) : json({ selected: true }));
    const { controller, setStatus } = setup(status({ cadFolderConfigured: false }), fetcher);
    const first = controller.sendWgToFusion().catch((error) => error);
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    const second = controller.sendWgToFusion();
    await Promise.resolve(); await Promise.resolve();
    setStatus(status()); pick.resolve(json({ selected: true }));
    expect(await first).toBeInstanceOf(OutboundSupersededError); await second;
    expect(fetcher.mock.calls.filter(([path]) => String(path).endsWith('/select'))).toHaveLength(1);
    expect(exportsFrom(fetcher)).toHaveLength(1);
  });
  it.each(['document', 'identity', 'newer send', 'dispose'] as const)('fences late identity adoption after %s', async (change) => {
    const old = deferred<Response>(); let writes = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/wglink')
      ? ++writes === 1 ? old.promise : json({ ...exported, sequence: 2, identity: { ...identity, baseEditVersion: 3 } })
      : json({ selected: true }));
    const { controller } = setup(status(), fetcher);
    const first = controller.sendWgToFusion(); await vi.waitFor(() => expect(writes).toBe(1));
    if (change === 'document') useDesignStore.getState().replaceDesign(designForFamily('ICW'));
    if (change === 'identity') useDocumentStore.getState().setCadLink({ ...identity, designId: 'wgd_new' }, 'current');
    if (change === 'newer send') await controller.sendWgToFusion();
    if (change === 'dispose') controller.dispose();
    const before = useDocumentStore.getState().identity;
    old.resolve(json(exported)); await first;
    expect(useDocumentStore.getState().identity).toBe(before);
  });
  it('fences status evidence superseded by a newer connection read', async () => {
    const pending = deferred<ReturnType<typeof status>>();
    const { controller, connection, reader, fetcher } = setup(); reader.mockImplementationOnce(() => pending.promise);
    const first = controller.sendWgToFusion();
    await connection.read(async () => status({ state: 'addin_offline' })); pending.resolve(status());
    await expect(first).rejects.toThrow('could not check'); expect(exportsFrom(fetcher)).toHaveLength(0);
  });
  it('leaves polar edits made during export uncommitted', async () => {
    const old = deferred<Response>();
    const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/wglink') ? old.promise : json({ selected: true }));
    const { controller } = setup(status(), fetcher); const sending = controller.sendWgToFusion();
    await vi.waitFor(() => expect(exportsFrom(fetcher)).toHaveLength(1));
    useSolveOptionsStore.getState().updatePolar({ distance: 4 });
    const polar = useSolveOptionsStore.getState().polar; old.resolve(json(exported)); await sending;
    expect(useSolveOptionsStore.getState().polar).toBe(polar);
  });
});

it('inventories every editable-design send entry point behind the shared guard boundary', () => {
  for (const source of [menuSource, paramSource, panelSource]) {
    expect(source).toContain('.sendWgToFusion('); expect(source).not.toContain('sendDesignToCad');
  }
  expect(coordinatorSource).toContain('useCadSend(');
  expect(coordinatorSource).toContain('sendWgToFusion({ confirmed: true })');
  expect(projectSource).not.toContain('sendWgToFusion'); expect(projectSource).not.toContain('sendDesignToCad');
  // Historical immutable run export is separate and deliberately adopts no identity.
  expect(runSource).toContain('sendDesignToCad('); expect(runSource).toContain('job.design_revision');
});

it('uses the first connection observation as command evidence without requiring a prior coordinator read', async () => {
  const connection = createCadConnectionStore(); connections.push(connection);
  const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/fusion-status')
    ? json(status()) : String(input).endsWith('/wglink') ? json(exported) : json({ selected: true }));
  const controller = createCadOutboundController({ connection, fetcher }); controllers.push(controller);
  await controller.sendWgToFusion(); expect(exportsFrom(fetcher)).toHaveLength(1);
  expect(connection.getState().fusionStatus?.link?.instanceId).toBe(link.instanceId);
});

it.each(['document', 'parameters', 'settings', 'identity'] as const)('never sends status read for an editor superseded by %s', async (change) => {
  const pending = deferred<ReturnType<typeof status>>();
  const { controller, reader, fetcher } = setup(); reader.mockImplementationOnce(() => pending.promise);
  const first = controller.sendWgToFusion();
  if (change === 'document') useDesignStore.getState().replaceDesign(designForFamily('ICW'));
  if (change === 'parameters') useDesignStore.getState().updateField('R', 111);
  if (change === 'settings') useSolveOptionsStore.getState().updatePolar({ distance: 4 });
  if (change === 'identity') useDocumentStore.getState().setCadLink({ ...identity, designId: 'new' }, 'current');
  pending.resolve(status()); await expect(first).rejects.toThrow(); expect(exportsFrom(fetcher)).toHaveLength(0);
});
