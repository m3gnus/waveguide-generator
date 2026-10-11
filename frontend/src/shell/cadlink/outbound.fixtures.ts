import type { CadConnectionStatus } from '../../stores/cadConnection';

export const identity = { designId: 'wgd_open', lineageId: 'wgl_open', baseEditVersion: 1 };
export const link: NonNullable<CadConnectionStatus['link']> = {
  instanceId: 'instance-1', bundlePath: null, designId: identity.designId, lineageId: identity.lineageId,
  editVersion: '1', designHash: 'sha256:design', designName: 'Speaker', formula: 'OSSE',
  configPresent: true, parameterCount: 1, parameterDriftCount: 0, localBodyState: 'unmodified',
  bodyFingerprintHash: 'sha256:body', documentSignatureHash: 'sha256:state', documentBodyCount: 1,
  sourceStateHash: 'sha256:source', exportId: 'export-1', exportSequence: '1',
};
export function status(overrides: Partial<CadConnectionStatus> = {}): CadConnectionStatus {
  return {
    cadApplication: 'fusion360', cadFolderConfigured: true, cadFolderPath: 'exchange', state: 'stale',
    processRunning: true, running: true, updatedAt: new Date().toISOString(),
    documentName: 'Speaker', documentId: 'document-1', currentFormula: 'OSSE', fusionFormula: 'OSSE',
    link, selectedInstanceId: link.instanceId, wgChangesAvailable: true, fusionChangesAvailable: false,
    documentChanged: false, documentChangeDetectable: true, staleDetectionExplanation: null,
    observationFreshness: 'current', observationPolicy: 'command', addinInboxTransfer: true,
    realizedDimensions: { state: 'no_link', instanceId: null, exportId: null, parameters: [] },
    ...overrides,
  };
}
export const exported = { sequence: 1, bundlePath: 'wglink/speaker.wglink', identity: { ...identity, baseEditVersion: 2 } };
