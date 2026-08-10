import type { JobItem } from '../api/jobsSocket';
import { jobsSocket } from '../api/jobsSocket';
import { downloadBlob, downloadText } from '../api/designIo';
import { fetchJobResults } from '../api/results';
import { ActionMenu, type ActionMenuItem } from '../design/ActionMenu';
import { exportBaseName, preferencesStore, usePreferences, type ExportFormat } from '../prefs/preferences';
import { buildCanonicalDirectivityRequest } from '../results/CanonicalPlot';
import { resultExportSnapshot } from '../results/exportContext';
import { runExportBundle, runExportFormat, type ExportContext } from '../results/exporters';
import { buildOnAxisFrd, buildPolarFrdSet } from '../results/frd';
import type { ResultPayload } from '../results/types';
import { EMPTY_RUN_EXPORT_STATE, useRunExportStore, type RunExportOutcome } from '../stores/runExports';
import { jobRerunState } from './jobDesign';

export interface RunExportControlProps {
  job: JobItem;
  compact?: boolean;
  onOpenExportSettings(): void;
}

interface CatalogItem {
  id: ExportFormat | 'on_axis_frd' | 'polar_frd';
  format?: ExportFormat;
  label: string;
  trailing: string;
  group: string;
  needsResult: boolean;
  needsDesign: boolean;
}

const FORMAT_CATALOG: CatalogItem[] = [
  { id: 'png', format: 'png', label: 'Charts', trailing: '.png', group: 'Results', needsResult: true, needsDesign: false },
  { id: 'on_axis_frd', label: 'On-axis response', trailing: '.frd', group: 'Results', needsResult: true, needsDesign: false },
  { id: 'polar_frd', label: 'Polar set (VituixCAD)', trailing: '.frd', group: 'Results', needsResult: true, needsDesign: false },
  { id: 'csv', format: 'csv', label: 'Frequency data', trailing: '.csv', group: 'Results', needsResult: true, needsDesign: false },
  { id: 'json', format: 'json', label: 'Full results', trailing: '.json', group: 'Results', needsResult: true, needsDesign: false },
  { id: 'step', format: 'step', label: 'STEP solid', trailing: '.step', group: 'Geometry & design', needsResult: false, needsDesign: true },
  { id: 'mwg_config', format: 'mwg_config', label: 'Parameter config', trailing: '.txt', group: 'Geometry & design', needsResult: false, needsDesign: true },
  { id: 'impedance_csv', format: 'impedance_csv', label: 'Impedance', trailing: '.csv', group: 'Advanced', needsResult: true, needsDesign: false },
  { id: 'polar_csv', format: 'polar_csv', label: 'Polar directivity', trailing: '.csv', group: 'Advanced', needsResult: true, needsDesign: false },
  { id: 'txt', format: 'txt', label: 'Summary report', trailing: '.txt', group: 'Advanced', needsResult: true, needsDesign: false },
  { id: 'stl', format: 'stl', label: 'STL mesh', trailing: '.stl', group: 'Advanced', needsResult: false, needsDesign: true },
  { id: 'fusion_csv', format: 'fusion_csv', label: 'Fusion curves', trailing: '.csv', group: 'Advanced', needsResult: false, needsDesign: true },
];

const CATALOG_BY_FORMAT = new Map(
  FORMAT_CATALOG.filter((item): item is CatalogItem & { format: ExportFormat } => item.format !== undefined)
    .map((item) => [item.format, item]),
);

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

async function responseError(response: Response): Promise<Error> {
  let detail = `${response.status} ${response.statusText}`.trim();
  try {
    const body = await response.json() as { detail?: string };
    if (body.detail) detail = body.detail;
  } catch { /* retain the HTTP status */ }
  return new Error(detail || 'Export request failed.');
}

function pngBlob(dataUri: string): Blob {
  const encoded = dataUri.includes(',') ? dataUri.slice(dataUri.indexOf(',') + 1) : dataUri;
  const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
  return new Blob([bytes], { type: 'image/png' });
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

  // Temporary seam: fold this FRD action into the shared export dispatcher once
  // results/exporters.ts and the preference format contract are free to change.
  const exportOnAxisFrd = () => execute(job.id, [], async (): Promise<RunExportOutcome> => {
    const context = await buildContext([]);
    const result = await fetchJobResults(job.id) as ResultPayload;
    const filename = `${exportBaseName(context.preferences)}.frd`;
    downloadText(buildOnAxisFrd(result, context.preferences), filename);
    await recordFiles([filename]);
    return { notice: `On-axis response (.frd) exported · 1 file` };
  });

  // Temporary seam: fold this VituixCAD set action into the shared export
  // dispatcher once results/exporters.ts and the preference format contract are free.
  const exportPolarFrd = () => execute(job.id, [], async (): Promise<RunExportOutcome> => {
    const context = await buildContext([]);
    const result = await fetchJobResults(job.id) as ResultPayload;
    const files = buildPolarFrdSet(result, context.preferences);
    if (!files.length) throw new Error('This run has no directivity data for a polar FRD set.');

    const pathResponse = await fetch('/api/workspace/path');
    if (!pathResponse.ok) throw await responseError(pathResponse);
    let workspace = await pathResponse.json() as { selected?: boolean; path?: string };
    if (!workspace.selected) {
      const selectResponse = await fetch('/api/workspace/select', { method: 'POST' });
      if (!selectResponse.ok) throw await responseError(selectResponse);
      workspace = await selectResponse.json() as { selected?: boolean; path?: string };
      if (!workspace.selected) {
        throw new Error('Workspace selection was cancelled. No files were written.');
      }
    }

    const writeResponse = await fetch('/api/workspace/write-export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        subdirectory: exportBaseName(context.preferences),
        members: files.map(({ filename, text }) => ({ relative_path: filename, text })),
      }),
    });
    if (!writeResponse.ok) throw await responseError(writeResponse);
    const written = await writeResponse.json() as { directory: string; files: string[] };
    await recordFiles(written.files);
    return { notice: `${written.files.length} polar FRD files written to ${written.directory}` };
  });

  // Temporary seam: the shared PNG dispatcher currently renders only the line
  // charts. Fold this second canonical request into it once exporters.ts is free.
  const exportCharts = () => execute(job.id, ['png'], async (): Promise<RunExportOutcome> => {
    const context = await buildContext(['png']);
    const result = context.result!;
    const directivityRequest = buildCanonicalDirectivityRequest(result, context.preferences);
    const directivityResponse = await fetch(directivityRequest.endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(directivityRequest.payload),
    });
    if (!directivityResponse.ok) throw await responseError(directivityResponse);
    const directivityBody = await directivityResponse.json() as { image?: string };
    if (!directivityBody.image) throw new Error('HornLab plots returned no directivity image.');

    const chartFiles = await runExportFormat('png', context);
    const directivityFilename = `${exportBaseName(context.preferences)}_directivity_map.png`;
    downloadBlob(pngBlob(directivityBody.image), directivityFilename);
    const files = [...chartFiles, directivityFilename];
    await recordFiles(files);
    return { notice: `Charts (.png) exported · ${files.length} files` };
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
      const noDirectivity = item.id === 'polar_frd' && Object.keys(job.polar_grid ?? {}).length === 0;
      const unavailable = (item.needsDesign && !designState.enabled) || noDirectivity;
      const disabledReason = noDirectivity
        ? 'This run has no directivity data for a polar FRD set.'
        : designState.reason ?? 'This run has no recoverable design.';
      return {
        id: item.id,
        label: item.label,
        trailing: item.trailing,
        group: item.group,
        disabled: operation.busy || unavailable,
        disabledReason: unavailable ? disabledReason : undefined,
        busy: item.format ? operation.busyFormats.includes(item.format) : operation.busy,
        busyLabel: `Preparing ${item.label}…`,
        error: item.format && operation.lastErrorFormats.includes(item.format) ? operation.lastError ?? undefined : undefined,
        onSelect: async () => {
          if (item.id === 'on_axis_frd') await exportOnAxisFrd();
          else if (item.id === 'polar_frd') await exportPolarFrd();
          else if (item.id === 'png') await exportCharts();
          else if (item.format) await exportOne(item.format);
        },
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
