import { describe, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { preferencesStore } from '../prefs/preferences';
import { JobAutomation } from './automation';

const job: JobItem = { id: 'abcdef123', status: 'complete', progress: 1, stage: null, stage_message: null, created_at: '2026-08-04T10:00:00Z', queued_at: '2026-08-04T10:00:00Z', started_at: null, completed_at: '2026-08-04T10:01:00Z', config_summary: {}, has_results: true, has_mesh_artifact: true, label: null, error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null, rating: null, exported_files: [], auto_export_completed_at: null, raw_results_file: null, mesh_artifact_file: null, log_tail: [] };

describe('job completion automation', () => {
  it('downloads and exports each eligible job once and records completion', async () => {
    const automation = new JobAutomation();
    const dependencies = { downloadMesh: vi.fn().mockResolvedValue('mesh.msh'), exportCompleted: vi.fn().mockResolvedValue({ files: ['result.csv'], failures: [] }), markExported: vi.fn().mockResolvedValue(undefined), incrementCounter: vi.fn(), reportError: vi.fn(), now: () => '2026-08-04T12:00:00Z' };
    const preferences = { ...preferencesStore.getSnapshot(), autoDownloadMesh: true, autoExportOnComplete: true, exportFormats: ['csv' as const] };
    await automation.process([job], preferences, dependencies);
    await automation.process([job], preferences, dependencies);
    expect(dependencies.downloadMesh).toHaveBeenCalledTimes(1);
    expect(dependencies.exportCompleted).toHaveBeenCalledTimes(1);
    expect(dependencies.markExported).toHaveBeenCalledWith(job, ['result.csv'], '2026-08-04T12:00:00Z');
    expect(dependencies.incrementCounter).toHaveBeenCalledOnce();
  });
  it('releases the mesh guard after failure so a later update can retry', async () => {
    const automation = new JobAutomation();
    const downloadMesh = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue('mesh.msh');
    const dependencies = { downloadMesh, exportCompleted: vi.fn(), markExported: vi.fn(), incrementCounter: vi.fn(), reportError: vi.fn() };
    const preferences = { ...preferencesStore.getSnapshot(), autoDownloadMesh: true };
    await automation.process([job], preferences, dependencies);
    await automation.process([job], preferences, dependencies);
    expect(downloadMesh).toHaveBeenCalledTimes(2);
    expect(dependencies.reportError).toHaveBeenCalledWith(expect.stringContaining('offline'));
  });
});
