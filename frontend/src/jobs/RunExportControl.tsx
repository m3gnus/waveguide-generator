import type { JobItem } from '../api/jobsSocket';
import { jobsSocket } from '../api/jobsSocket';
import { fetchJobResults } from '../api/results';
import { ActionMenu, type ActionMenuItem } from '../design/ActionMenu';
import { usePreferences, type ExportFormat } from '../prefs/preferences';
import { buildCanonicalDirectivityRequest } from '../results/CanonicalPlot';
import { resultExportSnapshot } from '../results/exportContext';
import {
  runExportFormat,
  runWorkspaceExportBundle,
  writeWorkspaceFiles,
  type ExportBundleResult,
  type ExportContext,
  type WorkspaceFile,
} from '../results/exporters';
import { buildOnAxisFrd, buildPolarFrdSet } from '../results/frd';
import { resultChannelFileSuffix, resultChannels, scopeResultChannel, type ResultPayload } from '../results/types';
import { EMPTY_RUN_EXPORT_STATE, useRunExportStore, type RunExportOutcome } from '../stores/runExports';
import { canLoadJobDesign, jobDesignAvailability, jobRerunState } from './jobDesign';
import { exportStemForJob } from './exportNaming';
import './RunExportControl.css';

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
  { id: 'mwg_config', format: 'mwg_config', label: 'Parameter config', trailing: '.cfg', group: 'Geometry & design', needsResult: false, needsDesign: true },
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

export function canExportRun(job: JobItem): boolean {
  return job.status === 'complete' && (job.has_results || canLoadJobDesign(job));
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

function workspaceNotice(label: string, result: Pick<ExportBundleResult, 'directory' | 'files'>): string {
  const count = `${result.files.length} file${result.files.length === 1 ? '' : 's'}`;
  return result.directory
    ? `${label} · ${count} written to ${result.directory}`
    : `${label} · ${count} written to Workspace`;
}

export function RunExportControl({ job, compact = false, onOpenExportSettings }: RunExportControlProps) {
  const preferences = usePreferences();
  const operation = useRunExportStore((state) => state.jobs[job.id] ?? EMPTY_RUN_EXPORT_STATE);
  const execute = useRunExportStore((state) => state.execute);
  const designState = jobRerunState(job);
  const designExportable = canLoadJobDesign(job);
  const designAvailability = jobDesignAvailability(job);

  const buildContext = async (formats: readonly ExportFormat[]): Promise<ExportContext> => ({
    ...(needsResults(formats) ? { result: await fetchJobResults(job.id) as ResultPayload } : {}),
    ...resultExportSnapshot(job),
    jobStem: exportStemForJob(job),
    preferences,
  });

  const recordFiles = async (files: string[]) => {
    if (!files.length) return;
    await jobsSocket.patchMetadata(job.id, {
      exported_files: [...new Set([...(job.exported_files ?? []), ...files])],
    });
  };

  const exportOne = (format: ExportFormat) => execute(job.id, [format], async (): Promise<RunExportOutcome> => {
    const result = await runWorkspaceExportBundle(await buildContext([format]), [format]);
    await recordFiles(result.files);
    const notice = workspaceNotice(formatLabel(format), result);
    return result.failures.length ? {
      notice,
      error: `${notice} · ${result.failures.map(({ reason }) => reason).join('; ')}`,
      errorFormats: [format],
    } : { notice };
  });

  // Temporary seam: fold this FRD action into the shared export dispatcher once
  // results/exporters.ts and the preference format contract are free to change.
  const exportOnAxisFrd = () => execute(job.id, [], async (): Promise<RunExportOutcome> => {
    const context = await buildContext([]);
    const result = await fetchJobResults(job.id) as ResultPayload;
    const channels = resultChannels(result);
    const variants = channels.length > 1 ? channels.map(({ id }) => id) : [undefined];
    const prepared = variants.map((channelId): WorkspaceFile => {
      const filename = `${context.jobStem}${resultChannelFileSuffix(result, channelId)}.frd`;
      return {
        filename,
        blob: new Blob([buildOnAxisFrd(result, context.preferences, channelId)], { type: 'text/plain;charset=utf-8' }),
      };
    });
    const written = await writeWorkspaceFiles(context.jobStem, prepared);
    await recordFiles(written.files);
    return { notice: workspaceNotice('On-axis response (.frd)', written) };
  });

  // Temporary seam: fold this VituixCAD set action into the shared export
  // dispatcher once results/exporters.ts and the preference format contract are free.
  const exportPolarFrd = () => execute(job.id, [], async (): Promise<RunExportOutcome> => {
    const context = await buildContext([]);
    const result = await fetchJobResults(job.id) as ResultPayload;
    const channels = resultChannels(result);
    const variants = channels.length > 1 ? channels.map(({ id }) => id) : [undefined];
    const files = variants.flatMap((channelId) => buildPolarFrdSet(result, context.preferences, context.jobStem, channelId));
    if (!files.length) throw new Error('This run has no directivity data for a polar FRD set.');
    const written = await writeWorkspaceFiles(
      context.jobStem,
      files.map(({ filename, text }) => ({
        filename,
        blob: new Blob([text], { type: 'text/plain;charset=utf-8' }),
      })),
    );
    await recordFiles(written.files);
    return { notice: `${written.files.length} polar FRD files written to ${written.directory}` };
  });

  // Temporary seam: the shared PNG dispatcher currently renders only the line
  // charts. Fold this second canonical request into it once exporters.ts is free.
  const exportCharts = () => execute(job.id, ['png'], async (): Promise<RunExportOutcome> => {
    const context = await buildContext(['png']);
    const result = context.result!;
    const channels = resultChannels(result);
    const variants = channels.length > 1 ? channels.map(({ id }) => id) : [undefined];
    const prepared: WorkspaceFile[] = [];
    for (const channelId of variants) {
      const directivityRequest = buildCanonicalDirectivityRequest(scopeResultChannel(result, channelId), context.preferences);
      const directivityResponse = await fetch(directivityRequest.endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(directivityRequest.payload),
      });
      if (!directivityResponse.ok) throw await responseError(directivityResponse);
      const directivityBody = await directivityResponse.json() as { image?: string };
      if (!directivityBody.image) throw new Error('HornLab plots returned no directivity image.');

      await runExportFormat('png', {
        ...context,
        channelId,
        saveBlob: (blob, filename) => prepared.push({ blob, filename }),
      });
      const directivityFilename = `${context.jobStem}${resultChannelFileSuffix(result, channelId)}_directivity_map.png`;
      prepared.push({ blob: pngBlob(directivityBody.image), filename: directivityFilename });
    }
    const written = await writeWorkspaceFiles(context.jobStem, prepared);
    await recordFiles(written.files);
    return { notice: workspaceNotice('Charts (.png)', written) };
  });

  const exportPreferred = () => {
    const formats = [...preferences.exportFormats];
    return execute(job.id, formats, async (): Promise<RunExportOutcome> => {
      const result = await runWorkspaceExportBundle(await buildContext(formats), formats);
      await recordFiles(result.files);
      const fileText = workspaceNotice('Export', result);
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
      const noResults = item.needsResult && !job.has_results;
      const unavailable = (item.needsDesign && !designExportable) || noResults || noDirectivity;
      const disabledReason = noDirectivity
        ? 'This run has no directivity data for a polar FRD set.'
        : noResults
          ? 'This run\'s results were removed by retention.'
        : designAvailability.reason ?? designState.reason ?? 'This run has no recoverable design.';
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
      onPrimary={preferences.exportFormats.length && preferences.exportFormats.every((format) => {
        const item = CATALOG_BY_FORMAT.get(format);
        return (!item?.needsResult || job.has_results) && (!item?.needsDesign || designExportable);
      }) ? async () => { await exportPreferred(); } : undefined}
    />
    {operation.lastError
      ? <div className="design-menu-status run-export-feedback error" role="alert">{operation.lastError}</div>
      : operation.lastNotice && <div className="design-menu-status run-export-feedback" role="status">{operation.lastNotice}</div>}
  </div>;
}
