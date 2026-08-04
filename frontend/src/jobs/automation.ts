import type { JobItem } from '../api/jobsSocket';
import type { Preferences } from '../prefs/preferences';
import type { ExportFormat } from '../prefs/preferences';

export interface AutomationDependencies {
  downloadMesh(job: JobItem): Promise<string>;
  exportCompleted(job: JobItem, formats: ExportFormat[]): Promise<{ files: string[]; failures: Array<{ format: ExportFormat; reason: string }> }>;
  markExported(job: JobItem, files: string[], formats: JobItem['auto_export_formats'], completedAt: string | null): Promise<void>;
  incrementCounter(): void;
  reportError(message: string): void;
  now?(): string;
}

export class JobAutomation {
  private readonly meshStarted = new Set<string>();
  private readonly exportStarted = new Set<string>();

  async process(jobs: JobItem[], preferences: Preferences, dependencies: AutomationDependencies): Promise<void> {
    const tasks: Promise<void>[] = [];
    if (preferences.autoDownloadMesh) jobs.filter((job) => job.has_mesh_artifact).forEach((job) => {
      if (this.meshStarted.has(job.id)) return;
      this.meshStarted.add(job.id);
      tasks.push(dependencies.downloadMesh(job).then(() => undefined).catch((error) => {
        this.meshStarted.delete(job.id);
        dependencies.reportError(`Mesh auto-download failed for ${job.id.slice(0, 6)}: ${error instanceof Error ? error.message : String(error)}`);
      }));
    });
    if (preferences.autoExportOnComplete && preferences.exportFormats.length) jobs.filter((job) => job.status === 'complete' && job.has_results && !job.auto_export_completed_at).forEach((job) => {
      if (this.exportStarted.has(job.id)) return;
      const pendingFormats = preferences.exportFormats.filter((format) => job.auto_export_formats[format]?.status !== 'complete');
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
        const allSelectedComplete = preferences.exportFormats.every((format) => formatStatus[format]?.status === 'complete');
        await dependencies.markExported(job, result.files, formatStatus, allSelectedComplete ? attemptedAt : null);
        if (result.files.length) dependencies.incrementCounter();
        if (result.failures.length) dependencies.reportError(`Auto-export for ${job.id.slice(0, 6)} completed with ${result.failures.length} failure${result.failures.length === 1 ? '' : 's'}: ${result.failures.map(({ format, reason }) => `${format} (${reason})`).join(', ')}`);
        if (!allSelectedComplete) this.exportStarted.delete(job.id);
      }).catch((error) => {
        this.exportStarted.delete(job.id);
        dependencies.reportError(`Auto-export failed for ${job.id.slice(0, 6)}: ${error instanceof Error ? error.message : String(error)}`);
      }));
    });
    await Promise.all(tasks);
  }
}
