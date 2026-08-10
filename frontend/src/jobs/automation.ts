import type { JobItem } from '../api/jobsSocket';
import type { Preferences } from '../prefs/preferences';
import type { ExportFormat } from '../prefs/preferences';

export interface AutomationDependencies {
  downloadMesh(job: JobItem): Promise<string>;
  markMeshDownloaded(job: JobItem, filename: string): Promise<void>;
  exportCompleted(job: JobItem, formats: ExportFormat[]): Promise<{ files: string[]; failures: Array<{ format: ExportFormat; reason: string }> }>;
  markExported(job: JobItem, files: string[], formats: JobItem['auto_export_formats'], completedAt: string | null): Promise<void>;
  incrementCounter(): void;
  reportError(message: string): void;
  now?(): string;
}

export class JobAutomation {
  private readonly meshStarted = new Set<string>();
  private readonly exportStarted = new Set<string>();
  private emptyAutoExportWarningShown = false;

  async process(jobs: JobItem[], preferences: Preferences, dependencies: AutomationDependencies): Promise<void> {
    const tasks: Promise<void>[] = [];
    if (preferences.autoDownloadMesh) jobs.filter((job) => job.has_mesh_artifact && !job.mesh_artifact_file).forEach((job) => {
      if (this.meshStarted.has(job.id)) return;
      this.meshStarted.add(job.id);
      tasks.push((async () => {
        let filename: string;
        try {
          filename = await dependencies.downloadMesh(job);
        } catch (error) {
          // Nothing reached the browser, so a later job update may safely retry.
          this.meshStarted.delete(job.id);
          dependencies.reportError(`Mesh auto-download failed for ${job.id.slice(0, 6)}: ${error instanceof Error ? error.message : String(error)}`);
          return;
        }
        try {
          await dependencies.markMeshDownloaded(job, filename);
        } catch (error) {
          // The download already happened. Keep the session guard so a failed
          // metadata write cannot duplicate it; an unmarked job retries after
          // an app restart, when this in-memory guard intentionally resets.
          dependencies.reportError(`Could not record mesh auto-download for ${job.id.slice(0, 6)}: ${error instanceof Error ? error.message : String(error)}`);
        }
      })());
    });
    if (preferences.autoExportOnComplete && !preferences.autoExportFormats.length) {
      if (!this.emptyAutoExportWarningShown) {
        dependencies.reportError('Auto-export is enabled, but no automatic export formats are selected. Choose at least one format in Results & export preferences.');
        this.emptyAutoExportWarningShown = true;
      }
    } else {
      this.emptyAutoExportWarningShown = false;
    }
    if (preferences.autoExportOnComplete && preferences.autoExportFormats.length) jobs.filter((job) => job.status === 'complete' && job.has_results && !job.auto_export_completed_at).forEach((job) => {
      if (this.exportStarted.has(job.id)) return;
      const pendingFormats = preferences.autoExportFormats.filter((format) => job.auto_export_formats[format]?.status !== 'complete');
      if (!pendingFormats.length) return;
      this.exportStarted.add(job.id);
      tasks.push(dependencies.exportCompleted(job, pendingFormats).then(async (result) => {
        const attemptedAt = dependencies.now?.() ?? new Date().toISOString();
        const failures = new Map(result.failures.map((failure) => [failure.format, failure.reason]));
        const formatStatus = { ...job.auto_export_formats };
        pendingFormats.forEach((format) => {
          const reason = failures.get(format);
          formatStatus[format] = reason
            ? { status: 'failed', attempted_at: attemptedAt, reason }
            : { status: 'complete', attempted_at: attemptedAt };
        });
        const allSelectedComplete = preferences.autoExportFormats.every((format) => formatStatus[format]?.status === 'complete');
        await dependencies.markExported(job, result.files, formatStatus, allSelectedComplete ? attemptedAt : null);
        if (result.files.length) dependencies.incrementCounter();
        if (result.failures.length) dependencies.reportError(`Auto-export for ${job.id.slice(0, 6)} completed with ${result.failures.length} failure${result.failures.length === 1 ? '' : 's'}: ${result.failures.map(({ format, reason }) => `${format} (${reason})`).join(', ')}`);
      }).catch((error) => {
        // Whether export preparation or its metadata patch failed, retrying on
        // the next jobs event can duplicate browser downloads. Keep one attempt
        // per job per app session; persisted null/failed state remains eligible
        // when a fresh JobAutomation is created after restart.
        dependencies.reportError(`Auto-export failed for ${job.id.slice(0, 6)}: ${error instanceof Error ? error.message : String(error)}`);
      }));
    });
    await Promise.all(tasks);
  }
}
