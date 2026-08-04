import { describe, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { preferencesStore } from '../prefs/preferences';
import type { ExportFormat, Preferences } from '../prefs/preferences';
import { JobAutomation } from './automation';

const job: JobItem = { id: 'abcdef123', status: 'complete', progress: 1, stage: null, stage_message: null, created_at: '2026-08-04T10:00:00Z', queued_at: '2026-08-04T10:00:00Z', started_at: null, completed_at: '2026-08-04T10:01:00Z', config_summary: {}, has_results: true, has_mesh_artifact: true, label: null, error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null, design_revision: 0, polar_grid: {}, rating: null, exported_files: [], auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null, mesh_artifact_file: null, log_tail: [] };

describe('job completion automation', () => {
  it('downloads and exports each eligible job once and records completion', async () => {
    const automation = new JobAutomation();
    const dependencies = { downloadMesh: vi.fn().mockResolvedValue('mesh.msh'), exportCompleted: vi.fn().mockResolvedValue({ files: ['result.csv'], failures: [] }), markExported: vi.fn().mockResolvedValue(undefined), incrementCounter: vi.fn(), reportError: vi.fn(), now: () => '2026-08-04T12:00:00Z' };
    const preferences = { ...preferencesStore.getSnapshot(), autoDownloadMesh: true, autoExportOnComplete: true, exportFormats: ['csv' as const] };
    await automation.process([job], preferences, dependencies);
    await automation.process([job], preferences, dependencies);
    expect(dependencies.downloadMesh).toHaveBeenCalledTimes(1);
    expect(dependencies.exportCompleted).toHaveBeenCalledTimes(1);
    expect(dependencies.exportCompleted).toHaveBeenCalledWith(job, ['csv']);
    expect(dependencies.markExported).toHaveBeenCalledWith(job, ['result.csv'], {
      csv: { status: 'complete', attempted_at: '2026-08-04T12:00:00Z' },
    }, '2026-08-04T12:00:00Z');
    expect(dependencies.incrementCounter).toHaveBeenCalledOnce();
  });

  it('keeps all-failed exports retryable and retries only failed formats after partial success', async () => {
    const automation = new JobAutomation();
    const exportCompleted = vi.fn()
      .mockResolvedValueOnce({ files: ['result.csv'], failures: [{ format: 'json', reason: 'disk full' }] })
      .mockResolvedValueOnce({ files: ['result.json'], failures: [] });
    const markExported = vi.fn().mockResolvedValue(undefined);
    const dependencies = { downloadMesh: vi.fn(), exportCompleted, markExported, incrementCounter: vi.fn(), reportError: vi.fn(), now: () => '2026-08-04T12:00:00Z' };
    const preferences: Preferences = { ...preferencesStore.getSnapshot(), autoExportOnComplete: true, exportFormats: ['csv', 'json'] as ExportFormat[] };
    await automation.process([job], preferences, dependencies);
    expect(markExported.mock.calls[0][3]).toBeNull();

    const partial = {
      ...job,
      auto_export_formats: markExported.mock.calls[0][2],
      exported_files: ['result.csv'],
    };
    await automation.process([partial], preferences, dependencies);
    expect(exportCompleted.mock.calls[1][1]).toEqual(['json']);
    expect(markExported.mock.calls[1][3]).toBe('2026-08-04T12:00:00Z');

    const allFailed = new JobAutomation();
    const failedMark = vi.fn().mockResolvedValue(undefined);
    await allFailed.process([job], { ...preferences, exportFormats: ['json'] }, {
      ...dependencies,
      exportCompleted: vi.fn().mockResolvedValue({ files: [], failures: [{ format: 'json', reason: 'offline' }] }),
      markExported: failedMark,
    });
    expect(failedMark.mock.calls[0][3]).toBeNull();
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
