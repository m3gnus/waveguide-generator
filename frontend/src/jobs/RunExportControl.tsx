import type { JobItem } from '../api/jobsSocket';
import { jobsSocket } from '../api/jobsSocket';
import { fetchJobResults } from '../api/results';
import { ActionMenu, type ActionMenuItem } from '../design/ActionMenu';
import { preferencesStore, usePreferences, type ExportFormat } from '../prefs/preferences';
import { resultExportSnapshot } from '../results/exportContext';
import { runExportBundle, runExportFormat, type ExportContext } from '../results/exporters';
import type { ResultPayload } from '../results/types';
import { EMPTY_RUN_EXPORT_STATE, useRunExportStore, type RunExportOutcome } from '../stores/runExports';
import { jobRerunState } from './jobDesign';

export interface RunExportControlProps {
  job: JobItem;
  compact?: boolean;
  onOpenExportSettings(): void;
}

interface CatalogItem {
  format: ExportFormat;
  label: string;
  trailing: string;
  group: string;
  needsResult: boolean;
  needsDesign: boolean;
}

// FRD and archives extend this catalog once their exporters exist. Keeping the
// catalog constrained to today's dispatcher prevents the menu promising files
// the export layer cannot create yet.
const FORMAT_CATALOG: CatalogItem[] = [
  { format: 'png', label: 'Charts', trailing: '.png', group: 'Results', needsResult: true, needsDesign: false },
  { format: 'csv', label: 'Frequency data', trailing: '.csv', group: 'Results', needsResult: true, needsDesign: false },
  { format: 'json', label: 'Full results', trailing: '.json', group: 'Results', needsResult: true, needsDesign: false },
  { format: 'step', label: 'STEP solid', trailing: '.step', group: 'Geometry & design', needsResult: false, needsDesign: true },
  { format: 'mwg_config', label: 'Parameter config', trailing: '.txt', group: 'Geometry & design', needsResult: false, needsDesign: true },
  { format: 'impedance_csv', label: 'Impedance', trailing: '.csv', group: 'Advanced', needsResult: true, needsDesign: false },
  { format: 'polar_csv', label: 'Polar directivity', trailing: '.csv', group: 'Advanced', needsResult: true, needsDesign: false },
  { format: 'txt', label: 'Summary report', trailing: '.txt', group: 'Advanced', needsResult: true, needsDesign: false },
  { format: 'stl', label: 'STL mesh', trailing: '.stl', group: 'Advanced', needsResult: false, needsDesign: true },
  { format: 'fusion_csv', label: 'Fusion curves', trailing: '.csv', group: 'Advanced', needsResult: false, needsDesign: true },
];

const CATALOG_BY_FORMAT = new Map(FORMAT_CATALOG.map((item) => [item.format, item]));

export function canExportRun(job: Pick<JobItem, 'status' | 'has_results'>): boolean {
  return job.status === 'complete' && job.has_results;
}

function formatLabel(format: ExportFormat): string {
  const item = CATALOG_BY_FORMAT.get(format);
  return item ? `${item.label} (${item.trailing})` : format;
}

function needsResults(formats: readonly ExportFormat[]): boolean {
  return formats.some((format) => CATALOG_BY_FORMAT.get(format)?.needsResult === true);
}

function jobName(job: JobItem): string {
  return job.label?.trim() || `${String(job.config_summary.formula_type ?? 'design').toLowerCase()}_${job.id.slice(0, 8)}`;
}

export function RunExportControl({ job, compact = false, onOpenExportSettings }: RunExportControlProps) {
  const preferences = usePreferences();
  const operation = useRunExportStore((state) => state.jobs[job.id] ?? EMPTY_RUN_EXPORT_STATE);
  const execute = useRunExportStore((state) => state.execute);
  const designState = jobRerunState(job);

  const buildContext = async (formats: readonly ExportFormat[]): Promise<ExportContext> => ({
    ...(needsResults(formats) ? { result: await fetchJobResults(job.id) as ResultPayload } : {}),
    ...resultExportSnapshot(job),
    preferences: {
      ...preferences,
      outputName: job.label?.trim() || `${preferences.outputName}_${job.id}`,
    },
  });

  const recordFiles = async (files: string[]) => {
    if (!files.length) return;
    await jobsSocket.patchMetadata(job.id, {
      exported_files: [...new Set([...(job.exported_files ?? []), ...files])],
    });
    preferencesStore.update({ counter: Math.min(999_999, preferencesStore.getSnapshot().counter + 1) });
  };

  const exportOne = (format: ExportFormat) => execute(job.id, [format], async (): Promise<RunExportOutcome> => {
    const files = await runExportFormat(format, await buildContext([format]));
    await recordFiles(files);
    return { notice: `${formatLabel(format)} exported · ${files.length} file${files.length === 1 ? '' : 's'}` };
  });

  const exportPreferred = () => {
    const formats = [...preferences.exportFormats];
    return execute(job.id, formats, async (): Promise<RunExportOutcome> => {
      const result = await runExportBundle(await buildContext(formats), formats);
      await recordFiles(result.files);
      const fileText = `${result.files.length} file${result.files.length === 1 ? '' : 's'} exported`;
      if (!result.failures.length) return { notice: fileText };
      const failed = result.failures.map(({ format, reason }) => `${formatLabel(format)}: ${reason}`).join('; ');
      return {
        notice: fileText,
        error: `${fileText} · Failed ${result.failures.length} format${result.failures.length === 1 ? '' : 's'}: ${failed}`,
        errorFormats: result.failures.map(({ format }) => format),
      };
    });
  };

  const items: ActionMenuItem[] = [
    ...FORMAT_CATALOG.map((item): ActionMenuItem => {
      const unavailable = item.needsDesign && !designState.enabled;
      return {
        id: item.format,
        label: item.label,
        trailing: item.trailing,
        group: item.group,
        disabled: operation.busy || unavailable,
        disabledReason: unavailable ? designState.reason ?? 'This run has no recoverable design.' : undefined,
        busy: operation.busyFormats.includes(item.format),
        busyLabel: `Preparing ${item.label}…`,
        error: operation.lastErrorFormats.includes(item.format) ? operation.lastError ?? undefined : undefined,
        onSelect: async () => { await exportOne(item.format); },
      };
    }),
    {
      id: 'export-settings',
      label: 'Export settings…',
      disabled: operation.busy,
      onSelect: onOpenExportSettings,
    },
  ];

  if (!canExportRun(job)) return null;

  return <div className={`run-export-control${compact ? ' compact' : ''}`}>
    <ActionMenu
      items={items}
      menuLabel={`Export ${jobName(job)}`}
      triggerLabel={compact ? 'Export' : `Export${preferences.exportFormats.length ? ` (${preferences.exportFormats.length})` : ''}`}
      chevronLabel={`More export options for ${jobName(job)}`}
      onPrimary={preferences.exportFormats.length ? async () => { await exportPreferred(); } : undefined}
    />
    {operation.lastError
      ? <div className="design-menu-status run-export-feedback error" role="alert">{operation.lastError}</div>
      : operation.lastNotice && <div className="design-menu-status run-export-feedback" role="status">{operation.lastNotice}</div>}
  </div>;
}
