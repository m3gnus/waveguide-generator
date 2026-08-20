import { beforeEach, describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { workspaceModeStore } from '../stores/workspaceMode';
import { runContext, runMatchesContext, type RunContext } from './runCoherence';

function parametric(revision: number): JobItem {
  return { config_summary: {}, design_revision: revision } as JobItem;
}

function cad(ingestId: string | null): JobItem {
  return {
    config_summary: { geometry_type: 'imported' },
    design_revision: 0,
    cad_source: { ingest_id: ingestId },
  } as unknown as JobItem;
}

const parametricContext: RunContext = {
  mode: 'parametric', designRevision: 7, ingestId: null, designId: 'wgd_live',
};
const cadContext: RunContext = {
  mode: 'cad', designRevision: 7, ingestId: 'wgi_live', designId: 'wgd_live',
};

describe('run coherence', () => {
  it.each([
    ['parametric at the live revision', parametric(7), parametricContext, 'current'],
    ['parametric at an older revision', parametric(6), parametricContext, 'older-revision'],
    ['CAD while looking at parametric', cad('wgi_live'), parametricContext, 'other-model'],
    ['the active CAD ingestion', cad('wgi_live'), cadContext, 'current'],
    ['a different CAD ingestion', cad('wgi_other'), cadContext, 'other-model'],
    ['CAD with no active ingestion', cad(null), { ...cadContext, ingestId: null }, 'other-model'],
    ['parametric while looking at CAD', parametric(7), cadContext, 'other-model'],
  ] as const)('classifies %s', (_label, job, context, verdict) => {
    expect(runMatchesContext(job, context)).toBe(verdict);
  });

  beforeEach(() => {
    resetDesignStore();
    resetCadReturnStore();
    resetDocumentStore();
    workspaceModeStore.setMode('parametric');
  });

  it('derives the live context directly from the workspace stores', () => {
    useDesignStore.setState({ designRevision: 14 });
    useCadReturnStore.setState({ ingestRecord: { ingest_id: 'wgi_store' } as never });
    useDocumentStore.setState({ identity: { designId: 'wgd_store', lineageId: 'wgl_store', baseEditVersion: 2 } });
    workspaceModeStore.setMode('cad');

    expect(runContext()).toEqual({
      mode: 'cad', designRevision: 14, ingestId: 'wgi_store', designId: 'wgd_store',
    });
  });
});
