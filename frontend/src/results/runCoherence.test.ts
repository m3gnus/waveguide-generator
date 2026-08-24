import { beforeEach, describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { designForFamily, resetDesignStore, serializeDesign, useDesignStore, type DesignDocument } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { workspaceModeStore } from '../stores/workspaceMode';
import {
  designFingerprint, runContext, runContextMarker, runMatchesContext, runProvenanceMarker, type RunContext,
} from './runCoherence';

let nextJob = 0;

/** A run carrying the design it was solved from, the way the server stores it. */
function parametric(design: DesignDocument | null): JobItem {
  nextJob += 1;
  return {
    id: `job-${nextJob}`,
    config_summary: {},
    design_revision: 1,
    script_snapshot: design ? { version: 1, design: serializeDesign(design) } : null,
  } as unknown as JobItem;
}

function cad(ingestId: string | null): JobItem {
  nextJob += 1;
  return {
    id: `job-${nextJob}`,
    config_summary: { geometry_type: 'imported' },
    design_revision: 0,
    script_snapshot: null,
    cad_source: { ingest_id: ingestId },
  } as unknown as JobItem;
}

function contextFor(design: DesignDocument, overrides: Partial<RunContext> = {}): RunContext {
  return {
    mode: 'parametric',
    designRevision: 7,
    designFingerprint: designFingerprint(design),
    ingestId: null,
    designId: 'wgd_live',
    ...overrides,
  };
}

describe('run coherence', () => {
  beforeEach(() => {
    resetDesignStore();
    resetCadReturnStore();
    resetDocumentStore();
    workspaceModeStore.setMode('parametric');
  });

  it('classifies a parametric run by the design it was solved from', () => {
    const design = designForFamily('OSSE');
    const run = parametric(design);
    expect(runMatchesContext(run, contextFor(design))).toBe('current');
    // The revision counter moves on every edit, undo and file open; the
    // verdict must not, or only the newest solve could ever read current.
    expect(runMatchesContext(run, contextFor(design, { designRevision: 4_000 }))).toBe('current');
  });

  it('reads current again once an edit is undone back to what was solved', () => {
    const run = parametric(useDesignStore.getState().design);
    expect(runMatchesContext(run, contextFor(useDesignStore.getState().design))).toBe('current');

    useDesignStore.getState().updateField('R', 180);
    expect(runMatchesContext(run, contextFor(useDesignStore.getState().design))).toBe('older-revision');

    useDesignStore.getState().undo();
    expect(runMatchesContext(run, contextFor(useDesignStore.getState().design))).toBe('current');
  });

  it('ignores expression spelling and the machine-local solver path', () => {
    const design = designForFamily('OSSE');
    const respelled = structuredClone(design);
    respelled._expressions = { R: { value: respelled.R ?? 0, text: '2*R0' } as never };
    respelled.simulation.solver_mode = 'bempp';
    expect(designFingerprint(respelled)).toBe(designFingerprint(design));
  });

  it('separates a real geometry edit', () => {
    const design = designForFamily('OSSE');
    const edited = structuredClone(design);
    edited.L = (edited.L ?? 0) + 1;
    expect(designFingerprint(edited)).not.toBe(designFingerprint(design));
    expect(runMatchesContext(parametric(design), contextFor(edited))).toBe('older-revision');
  });

  it('refuses a run whose stored design cannot be read back', () => {
    expect(runMatchesContext(parametric(null), contextFor(designForFamily('OSSE')))).toBe('older-revision');
  });

  it('classifies imported runs by the ingestion in the viewport', () => {
    const design = designForFamily('OSSE');
    const cadContext = contextFor(design, { mode: 'cad', ingestId: 'wgi_live' });
    expect(runMatchesContext(cad('wgi_live'), cadContext)).toBe('current');
    expect(runMatchesContext(cad('wgi_other'), cadContext)).toBe('other-model');
    expect(runMatchesContext(cad(null), { ...cadContext, ingestId: null })).toBe('other-model');
    expect(runMatchesContext(cad('wgi_live'), contextFor(design))).toBe('other-model');
    expect(runMatchesContext(parametric(design), cadContext)).toBe('other-model');
  });

  it('marks only the provenance that differs from the workspace mode', () => {
    const design = designForFamily('OSSE');
    const parametricContext = contextFor(design);
    const cadContext = contextFor(design, { mode: 'cad', ingestId: 'wgi_live' });
    expect(runContextMarker(cad('wgi_live'), parametricContext)).toBe('CAD');
    expect(runContextMarker(cad('wgi_live'), cadContext)).toBeNull();
    expect(runContextMarker(cad('wgi_other'), cadContext)).toBe('other model');
    expect(runContextMarker(parametric(design), cadContext)).toBe('Parametric');
    expect(runProvenanceMarker(cad('wgi_live'), 'cad')).toBeNull();
    expect(runProvenanceMarker(cad('wgi_live'), 'parametric')).toBe('CAD');
  });

  it('marks a parametric run the design has moved on from', () => {
    const design = designForFamily('OSSE');
    const edited = structuredClone(design);
    edited.L = (edited.L ?? 0) + 1;
    expect(runContextMarker(parametric(design), contextFor(design))).toBeNull();
    expect(runContextMarker(parametric(design), contextFor(edited))).toBe('edited since');
  });

  it('derives the live context directly from the workspace stores', () => {
    useDesignStore.setState({ designRevision: 14 });
    useCadReturnStore.setState({ ingestRecord: { ingest_id: 'wgi_store' } as never });
    useDocumentStore.setState({ identity: { designId: 'wgd_store', lineageId: 'wgl_store', baseEditVersion: 2 } });
    workspaceModeStore.setMode('cad');

    expect(runContext()).toEqual({
      mode: 'cad',
      designRevision: 14,
      designFingerprint: designFingerprint(useDesignStore.getState().design),
      ingestId: 'wgi_store',
      designId: 'wgd_store',
    });
  });
});
