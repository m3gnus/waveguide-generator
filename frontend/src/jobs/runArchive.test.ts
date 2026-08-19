import { describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { archiveFolderForJob, exportSubdirectoryForJob, runSourceForJob } from './exportNaming';
import { buildDesignRecord, buildRunRecord, needsArchiving } from './runArchive';

function jobItem(overrides: Partial<JobItem> = {}): JobItem {
  return {
    id: 'abcdef123456', run_number: 3, parent_job_id: null, status: 'complete', progress: 1,
    stage: null, stage_message: null, created_at: '2026-08-19T10:00:00Z',
    queued_at: '2026-08-19T10:00:00Z', started_at: '2026-08-19T10:00:01Z',
    completed_at: '2026-08-19T10:02:00Z', config_summary: {}, solve_options: {} as JobItem['solve_options'],
    has_results: true, has_mesh_artifact: false, label: 'Big Horn', error_message: null,
    cancellation_requested: false, mesh_stats: null, script_snapshot: null, design_revision: 4,
    polar_grid: {}, rating: null, exported_files: [], auto_export_completed_at: null,
    auto_export_formats: {}, archived_at: null, raw_results_file: null, mesh_artifact_file: null,
    log_tail: [], ...overrides,
  };
}

describe('run archive layout', () => {
  it('groups a run under its design rather than under its pipeline', () => {
    expect(exportSubdirectoryForJob(jobItem())).toBe('Big_Horn/3_Big_Horn');
  });

  it('keeps a renamed CAD design writing to the folder its bundle already owns', () => {
    const renamed = jobItem({
      label: 'Big Horn Mk2',
      config_summary: { geometry_type: 'imported' },
      cad_source: {
        ingest_id: 'i1', design_id: 'd1', lineage_id: 'l1', archive_stem: 'Big Horn',
        manifest_sha256: 'sha256:aa', document_name: 'Big Horn.f3d', return_state_hash: 'sha256:bb',
      },
    });

    expect(archiveFolderForJob(renamed)).toBe('Big_Horn');
    expect(runSourceForJob(renamed)).toBe('cadlink');
  });

  it('records provenance in the run rather than in the folder', () => {
    const parametric = JSON.parse(buildRunRecord(jobItem()).text);
    expect(parametric.source).toBe('parametric');
    expect(parametric.cad).toBeNull();
    expect(parametric.run).toMatchObject({ number: 3, jobId: 'abcdef123456' });
    expect(parametric.timing.completedAt).toBe('2026-08-19T10:02:00Z');
  });

  it('archives the same run to identical bytes so a repeat write is a no-op', () => {
    const job = jobItem();
    expect(buildRunRecord(job).text).toBe(buildRunRecord(job).text);
    expect(buildDesignRecord(job).text).toBe(buildDesignRecord(job).text);
  });

  it('names the CAD document and return state a run was solved from', () => {
    const record = JSON.parse(buildRunRecord(jobItem({
      config_summary: { geometry_type: 'imported' },
      cad_source: {
        ingest_id: 'i1', design_id: 'd1', lineage_id: 'l1', archive_stem: 'Big Horn',
        manifest_sha256: 'sha256:aa', document_name: 'Big Horn v7', return_state_hash: 'sha256:bb',
      },
    })).text);

    expect(record.cad).toEqual({
      ingestId: 'i1',
      manifestSha256: 'sha256:aa',
      documentName: 'Big Horn v7',
      returnStateHash: 'sha256:bb',
    });
  });

  it('archives a completed run once', () => {
    expect(needsArchiving(jobItem())).toBe(true);
    expect(needsArchiving(jobItem({ archived_at: '2026-08-19T10:03:00Z' }))).toBe(false);
    expect(needsArchiving(jobItem({ status: 'error', has_results: false }))).toBe(false);
  });
});
