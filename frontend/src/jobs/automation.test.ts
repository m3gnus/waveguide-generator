import { describe, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { preferencesStore } from '../prefs/preferences';
import type { ExportFormat, Preferences } from '../prefs/preferences';
import { JobAutomation } from './automation';

const job: JobItem = { id: 'abcdef123', run_number: 1, parent_job_id: null, status: 'complete', progress: 1, stage: null, stage_message: null, created_at: '2026-08-04T10:00:00Z', queued_at: '2026-08-04T10:00:00Z', started_at: null, completed_at: '2026-08-04T10:01:00Z', config_summary: {}, solve_options: {} as JobItem['solve_options'], has_results: true, has_mesh_artifact: true, label: null, error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null, design_revision: 0, polar_grid: {}, rating: null, exported_files: [], auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null, mesh_artifact_file: null, log_tail: [] };

describe('job completion automation', () => {
  it('downloads and exports each eligible job once and records completion', async () => {
    const automation = new JobAutomation();
    const dependencies = { downloadMesh: vi.fn().mockResolvedValue('mesh.msh'), markMeshDownloaded: vi.fn().mockResolvedValue(undefined), exportCompleted: vi.fn().mockResolvedValue({ files: ['result.csv'], failures: [] }), markExported: vi.fn().mockResolvedValue(undefined), incrementCounter: vi.fn(), reportError: vi.fn(), now: () => '2026-08-04T12:00:00Z' };
    const preferences = { ...preferencesStore.getSnapshot(), autoDownloadMesh: true, autoExportOnComplete: true, exportFormats: ['csv' as const] };
    await automation.process([job], preferences, dependencies);
    await automation.process([job], preferences, dependencies);
    expect(dependencies.downloadMesh).toHaveBeenCalledTimes(1);
    expect(dependencies.markMeshDownloaded).toHaveBeenCalledWith(job, 'mesh.msh');
    expect(dependencies.exportCompleted).toHaveBeenCalledTimes(1);
    expect(dependencies.exportCompleted).toHaveBeenCalledWith(job, ['csv']);
    expect(dependencies.markExported).toHaveBeenCalledWith(job, ['result.csv'], {
      csv: { status: 'complete', attempted_at: '2026-08-04T12:00:00Z' },
    }, '2026-08-04T12:00:00Z');
    expect(dependencies.incrementCounter).toHaveBeenCalledOnce();
  });

  it('holds a partial export failure for the session and retries only failed formats after restart', async () => {
    const automation = new JobAutomation();
    const exportCompleted = vi.fn()
      .mockResolvedValueOnce({ files: ['result.csv'], failures: [{ format: 'json', reason: 'disk full' }] })
      .mockResolvedValueOnce({ files: ['result.json'], failures: [] });
    const markExported = vi.fn().mockResolvedValue(undefined);
    const dependencies = { downloadMesh: vi.fn(), markMeshDownloaded: vi.fn(), exportCompleted, markExported, incrementCounter: vi.fn(), reportError: vi.fn(), now: () => '2026-08-04T12:00:00Z' };
    const preferences: Preferences = { ...preferencesStore.getSnapshot(), autoExportOnComplete: true, exportFormats: ['csv', 'json'] as ExportFormat[] };
    await automation.process([job], preferences, dependencies);
    expect(markExported.mock.calls[0][3]).toBeNull();

    const partial = {
      ...job,
      auto_export_formats: markExported.mock.calls[0][2],
      exported_files: ['result.csv'],
    };
    await automation.process([partial], preferences, dependencies);
    expect(exportCompleted).toHaveBeenCalledTimes(1);

    await new JobAutomation().process([partial], preferences, dependencies);
    expect(exportCompleted.mock.calls[1][1]).toEqual(['json']);
    expect(markExported.mock.calls[1][3]).toBe('2026-08-04T12:00:00Z');
  });

  it('holds an all-failed resolved bundle for the session but leaves it retryable after restart', async () => {
    const automation = new JobAutomation();
    const exportCompleted = vi.fn().mockResolvedValue({ files: [], failures: [{ format: 'json' as const, reason: 'offline' }] });
    const failedMark = vi.fn().mockResolvedValue(undefined);
    const dependencies = {
      downloadMesh: vi.fn(), markMeshDownloaded: vi.fn(), exportCompleted,
      markExported: failedMark,
      incrementCounter: vi.fn(), reportError: vi.fn(), now: () => '2026-08-04T12:00:00Z',
    };
    const preferences: Preferences = { ...preferencesStore.getSnapshot(), autoExportOnComplete: true, exportFormats: ['json'] };
    await automation.process([job], preferences, dependencies);
    expect(failedMark.mock.calls[0][3]).toBeNull();
    const persistedFailure = { ...job, auto_export_formats: failedMark.mock.calls[0][2] };

    await automation.process([persistedFailure], preferences, dependencies);
    expect(exportCompleted).toHaveBeenCalledTimes(1);

    await new JobAutomation().process([persistedFailure], preferences, dependencies);
    expect(exportCompleted).toHaveBeenCalledTimes(2);
  });

  it('keeps rejected export and metadata attempts guarded until a fresh session', async () => {
    const preferences: Preferences = { ...preferencesStore.getSnapshot(), autoExportOnComplete: true, exportFormats: ['json'] };
    const rejectedExport = vi.fn().mockRejectedValueOnce(new Error('results offline')).mockResolvedValue({ files: ['result.json'], failures: [] });
    const exportDependencies = { downloadMesh: vi.fn(), markMeshDownloaded: vi.fn(), exportCompleted: rejectedExport, markExported: vi.fn().mockResolvedValue(undefined), incrementCounter: vi.fn(), reportError: vi.fn() };
    const firstSession = new JobAutomation();
    await firstSession.process([job], preferences, exportDependencies);
    await firstSession.process([job], preferences, exportDependencies);
    expect(rejectedExport).toHaveBeenCalledTimes(1);
    await new JobAutomation().process([job], preferences, exportDependencies);
    expect(rejectedExport).toHaveBeenCalledTimes(2);

    const metadataDependencies = { ...exportDependencies, exportCompleted: vi.fn().mockResolvedValue({ files: ['result.json'], failures: [] }), markExported: vi.fn().mockRejectedValue(new Error('metadata offline')) };
    const metadataSession = new JobAutomation();
    await metadataSession.process([job], preferences, metadataDependencies);
    await metadataSession.process([job], preferences, metadataDependencies);
    expect(metadataDependencies.exportCompleted).toHaveBeenCalledTimes(1);
    await new JobAutomation().process([job], preferences, metadataDependencies);
    expect(metadataDependencies.exportCompleted).toHaveBeenCalledTimes(2);
  });

  it('downloads a legacy unmarked mesh once and a fresh session skips its persisted marker', async () => {
    const downloadMesh = vi.fn().mockResolvedValue('mesh.msh');
    const markMeshDownloaded = vi.fn().mockResolvedValue(undefined);
    const dependencies = { downloadMesh, markMeshDownloaded, exportCompleted: vi.fn(), markExported: vi.fn(), incrementCounter: vi.fn(), reportError: vi.fn() };
    const preferences = { ...preferencesStore.getSnapshot(), autoDownloadMesh: true };
    await new JobAutomation().process([job], preferences, dependencies);
    expect(markMeshDownloaded).toHaveBeenCalledWith(job, 'mesh.msh');

    await new JobAutomation().process([{ ...job, mesh_artifact_file: 'mesh.msh' }], preferences, dependencies);
    expect(downloadMesh).toHaveBeenCalledTimes(1);
  });

  it('does not duplicate a completed browser download when persisting its marker fails', async () => {
    const downloadMesh = vi.fn().mockResolvedValue('mesh.msh');
    const dependencies = { downloadMesh, markMeshDownloaded: vi.fn().mockRejectedValue(new Error('metadata offline')), exportCompleted: vi.fn(), markExported: vi.fn(), incrementCounter: vi.fn(), reportError: vi.fn() };
    const preferences = { ...preferencesStore.getSnapshot(), autoDownloadMesh: true };
    const automation = new JobAutomation();
    await automation.process([job], preferences, dependencies);
    await automation.process([job], preferences, dependencies);
    expect(downloadMesh).toHaveBeenCalledTimes(1);
    expect(dependencies.reportError).toHaveBeenCalledWith(expect.stringContaining('metadata offline'));
    await new JobAutomation().process([job], preferences, dependencies);
    expect(downloadMesh).toHaveBeenCalledTimes(2);
  });

  it('releases the mesh guard after failure so a later update can retry', async () => {
    const automation = new JobAutomation();
    const downloadMesh = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue('mesh.msh');
    const dependencies = { downloadMesh, markMeshDownloaded: vi.fn().mockResolvedValue(undefined), exportCompleted: vi.fn(), markExported: vi.fn(), incrementCounter: vi.fn(), reportError: vi.fn() };
    const preferences = { ...preferencesStore.getSnapshot(), autoDownloadMesh: true };
    await automation.process([job], preferences, dependencies);
    await automation.process([job], preferences, dependencies);
    expect(downloadMesh).toHaveBeenCalledTimes(2);
    expect(dependencies.reportError).toHaveBeenCalledWith(expect.stringContaining('offline'));
  });
});
