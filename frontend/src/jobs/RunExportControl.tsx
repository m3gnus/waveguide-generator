import { nativeCadLimitation } from '../design/nativeFamilies';
import type { JobItem } from '../api/jobsSocket';
import { jobsSocket } from '../api/jobsSocket';
import { fetchMeshReplay } from '../api/meshReplay';
import { writeToOutputFolder } from '../api/workspace';
import { fetchJobResults } from '../api/results';
import { sendDesignToCad } from '../api/designIo';
import { ActionMenu, type ActionMenuItem } from '../design/ActionMenu';
import { sentToCadMessage } from '../design/useSendToCad';
import { usePreferences, type ExportFormat } from '../prefs/preferences';
import { resultExportSnapshot } from '../results/exportContext';
import {
  runWorkspaceExportBundle,
  type ExportBundleResult,
  type ExportContext,
} from '../results/exporters';
import type { ResultPayload } from '../results/types';
import { ExportCancelledError } from '../api/exportDestination';
import {
  askForExportDestination,
  askToReplaceExports,
  replacedNotice,
  EXPORT_CANCELLED_MESSAGE,
} from '../shell/exportDestinationPrompt';
import { EMPTY_RUN_EXPORT_STATE, useRunExportStore, type RunExportOutcome } from '../stores/runExports';
import { solveOptionsStoreForJob } from '../stores/solveOptionsContext';
import { canLoadJobDesign, hydrateJobDesign, jobDesignAvailability, jobRerunState } from './jobDesign';
import { exportStemForJob, exportSubdirectoryForJob } from './exportNaming';
import { designFileExtension } from '../stores/designName';
import './RunExportControl.css';

export interface RunExportControlProps {
  job: JobItem;
  compact?: boolean;
  disabledReason?: string;
  result?: ResultPayload;
  channelId?: string;
  onOpenExportSettings(): void;
}

interface CatalogItem {
  id: ExportFormat;
  format: ExportFormat;
  label: string;
  trailing: string;
  group: string;
  needsResult: boolean;
  needsDesign: boolean;
  cadRunReason?: string;
  needsPressureBasis?: boolean;
  needsRadiationArtifact?: boolean;
}

const FORMAT_CATALOG: CatalogItem[] = [
  { id: 'png', format: 'png', label: 'Charts', trailing: '.png', group: 'Results', needsResult: true, needsDesign: false },
  { id: 'on_axis_frd', format: 'on_axis_frd', label: 'On-axis response', trailing: '.frd', group: 'Results', needsResult: true, needsDesign: false },
  { id: 'polar_frd', format: 'polar_frd', label: 'Polar set (VituixCAD)', trailing: '.frd', group: 'Results', needsResult: true, needsDesign: false },
  { id: 'zma', format: 'zma', label: 'Electrical impedance (VituixCAD)', trailing: '.zma', group: 'Results', needsResult: true, needsDesign: false },
  { id: 'vxp', format: 'vxp', label: 'VituixCAD project', trailing: '.vxp', group: 'Results', needsResult: true, needsDesign: false },
  { id: 'csv', format: 'csv', label: 'Frequency data', trailing: '.csv', group: 'Results', needsResult: true, needsDesign: false },
  { id: 'json', format: 'json', label: 'Full results', trailing: '.json', group: 'Results', needsResult: true, needsDesign: false },
  { id: 'pressure_basis', format: 'pressure_basis', label: 'Complex pressure basis', trailing: '.npz', group: 'Results', needsResult: true, needsDesign: false, needsPressureBasis: true },
  { id: 'derived_acoustics', format: 'derived_acoustics', label: 'Derived acoustics', trailing: '.csv + .json', group: 'Results', needsResult: true, needsDesign: false },
  { id: 'html_report', format: 'html_report', label: 'Static run report', trailing: '.html', group: 'Results', needsResult: true, needsDesign: false },
  { id: 'step', format: 'step', label: 'STEP solid', trailing: '.step', group: 'Geometry & design', needsResult: false, needsDesign: true, cadRunReason: 'STEP export is not available for CAD runs yet.' },
  { id: 'mwg_config', format: 'mwg_config', label: 'Parameter config', trailing: '.cfg', group: 'Geometry & design', needsResult: false, needsDesign: true, cadRunReason: 'CAD runs have no WG parameter config to export.' },
  { id: 'impedance_csv', format: 'impedance_csv', label: 'Impedance', trailing: '.csv', group: 'Advanced', needsResult: true, needsDesign: false },
  { id: 'radiation_impedance_csv', format: 'radiation_impedance_csv', label: 'Radiation matrix curves', trailing: '.csv', group: 'Advanced', needsResult: false, needsDesign: false, needsRadiationArtifact: true },
  { id: 'radiation_impedance_npz', format: 'radiation_impedance_npz', label: 'Lossless radiation matrix', trailing: '.npz', group: 'Advanced', needsResult: false, needsDesign: false, needsRadiationArtifact: true },
  { id: 'polar_csv', format: 'polar_csv', label: 'Polar directivity', trailing: '.csv', group: 'Advanced', needsResult: true, needsDesign: false },
  { id: 'txt', format: 'txt', label: 'Summary report', trailing: '.txt', group: 'Advanced', needsResult: true, needsDesign: false },
  { id: 'stl', format: 'stl', label: 'STL mesh', trailing: '.stl', group: 'Advanced', needsResult: false, needsDesign: true, cadRunReason: 'STL export is not available for CAD runs.' },
  { id: 'fusion_csv', format: 'fusion_csv', label: 'Fusion curves', trailing: '.csv', group: 'Advanced', needsResult: false, needsDesign: true, cadRunReason: 'CAD runs have no WG curves to export.' },
];

const CATALOG_BY_FORMAT = new Map(
  FORMAT_CATALOG.map((item) => [item.format, item]),
);

export function canExportRun(job: JobItem): boolean {
  return job.status === 'complete' && (job.config_summary.import_origin === 'mesh-file' || job.has_results || canLoadJobDesign(job));
}

/** The parameter config is a native .mwg for LOOKUP, which has no ATH form. */
function trailingFor(item: CatalogItem, family?: string): string {
  return item.format === 'mwg_config' ? `.${designFileExtension(family)}` : item.trailing;
}

function formatLabel(format: ExportFormat, family?: string): string {
  const item = CATALOG_BY_FORMAT.get(format);
  return item ? `${item.label} (${trailingFor(item, family)})` : format;
}

function needsResults(formats: readonly ExportFormat[]): boolean {
  return formats.some((format) => CATALOG_BY_FORMAT.get(format)?.needsResult === true);
}

function unavailableFormatReason(
  item: CatalogItem,
  job: JobItem,
  designExportable: boolean,
  designUnavailableReason: string,
  cadRun: boolean,
): string | undefined {
  if (cadRun && item.cadRunReason) return item.cadRunReason;
  if (item.id === 'polar_frd' && Object.keys(job.polar_grid ?? {}).length === 0) {
    return 'This run has no directivity data for a polar FRD set.';
  }
  if (item.needsRadiationArtifact && !job.has_radiation_impedance_artifact) {
    return 'This run has no retained passive-cardioid radiation matrix.';
  }
  if (item.needsResult && !job.has_results) return 'This run\'s results were removed by retention.';
  if (item.needsPressureBasis && !job.has_pressure_basis_artifact) {
    const cause = job.persistence_warnings?.filter((warning) => /pressure[- ]basis/i.test(warning)).join(' ');
    return cause ? `This run has no retained complex pressure basis. ${cause}`
      : 'This run has no retained complex pressure basis. Re-solve the imported CAD model to create one.';
  }
  if (item.needsDesign && !designExportable) return designUnavailableReason;
  return undefined;
}

/**
 * Run an export, reporting a declined replacement as a cancellation.
 *
 * Declining "replace these files?" is an answer, not a failure, and this
 * control shows a thrown error in red beside the run.
 */
async function declinable<T>(run: () => Promise<T>): Promise<T | null> {
  try {
    return await run();
  } catch (error) {
    if (error instanceof ExportCancelledError) return null;
    throw error;
  }
}

function jobName(job: JobItem): string {
  return job.label?.trim() || `${String(job.config_summary.formula_type ?? 'design').toLowerCase()}_${job.id.slice(0, 8)}`;
}

function workspaceNotice(
  label: string,
  result: Pick<ExportBundleResult, 'directory' | 'files' | 'replaced'>,
): string {
  const count = `${result.files.length} file${result.files.length === 1 ? '' : 's'}`;
  const written = result.directory
    ? `${label} · ${count} written to ${result.directory}`
    : `${label} · ${count} written to Workspace`;
  return `${written}${replacedNotice(result.replaced)}`;
}

export function RunExportControl({ job, compact = false, disabledReason, result, channelId, onOpenExportSettings }: RunExportControlProps) {
  const preferences = usePreferences();
  const operation = useRunExportStore((state) => state.jobs[job.id] ?? EMPTY_RUN_EXPORT_STATE);
  const execute = useRunExportStore((state) => state.execute);
  const designState = jobRerunState(job);
  const designExportable = canLoadJobDesign(job);
  const family = hydrateJobDesign(job)?.formula ?? '';
  const cadLimitation = nativeCadLimitation(family);
  const designAvailability = jobDesignAvailability(job);
  const cadRun = designAvailability.source === 'cad-import' || job.config_summary.formula_type === 'cad-import';
  const designUnavailableReason = designAvailability.reason ?? designState.reason ?? 'This run has no recoverable design.';
  const formatReason = (item: CatalogItem) => unavailableFormatReason(item, job, designExportable, designUnavailableReason, cadRun);

  const buildContext = async (formats: readonly ExportFormat[]): Promise<ExportContext> => ({
    ...(needsResults(formats) ? { result: result ?? await fetchJobResults(job.id) as ResultPayload } : {}),
    ...resultExportSnapshot(job),
    ...(channelId ? { channelId } : {}),
    jobId: job.id,
    jobStem: exportStemForJob(job),
    hasRadiationImpedanceArtifact: job.has_radiation_impedance_artifact,
    workspaceSubdirectory: exportSubdirectoryForJob(job),
    designName: job.label ?? undefined,
    normalizationAngle: solveOptionsStoreForJob(job).getState().polar.normAngle,
    preferences,
  });

  const recordFiles = async (files: string[]) => {
    if (!files.length) return;
    await jobsSocket.patchMetadata(job.id, {
      exported_files: [...new Set([...(job.exported_files ?? []), ...files])],
    });
  };

  // 'overwrite': these two are the user asking for an export, and asking again
  // must produce the file again. See `ExistingFilePolicy`.
  const exportOne = (format: ExportFormat) => execute(job.id, [format], async (): Promise<RunExportOutcome> => {
    const destination = await askForExportDestination({
      title: `Export ${formatLabel(format, family)}`,
      detail: `From run ${jobName(job)}.`,
    });
    if (!destination) return { notice: EXPORT_CANCELLED_MESSAGE };
    const result = await declinable(async () => runWorkspaceExportBundle({
      ...await buildContext([format]),
      destinationToken: destination.token,
      confirmReplacements: askToReplaceExports,
    }, [format], 'confirm'));
    if (!result) return { notice: EXPORT_CANCELLED_MESSAGE };
    await recordFiles(result.files);
    const notice = workspaceNotice(formatLabel(format, family), result);
    return result.failures.length ? {
      notice,
      error: `${notice} · ${result.failures.map(({ reason }) => reason).join('; ')}`,
      errorFormats: [format],
    } : { notice };
  });

  const exportPreferred = () => {
    const preferred = [...preferences.exportFormats];
    return execute(job.id, preferred, async (): Promise<RunExportOutcome> => {
      const unavailable = preferred.flatMap((format) => {
        const item = CATALOG_BY_FORMAT.get(format);
        const reason = item ? formatReason(item) : 'This format is not available.';
        return reason ? [{ format, reason: `Skipped ${formatLabel(format, family)}: ${reason}` }] : [];
      });
      const skipped = unavailable.map(({ reason }) => reason).join(' ');
      const formats = preferred.filter((format) => !unavailable.some((item) => item.format === format));
      if (!formats.length) return {
        notice: '',
        error: skipped,
        errorFormats: unavailable.map(({ format }) => format),
      };
      const destination = await askForExportDestination({
        title: 'Export this run',
        detail: `${formats.length} format${formats.length === 1 ? '' : 's'} from run ${jobName(job)}.`,
      });
      if (!destination) return { notice: EXPORT_CANCELLED_MESSAGE };
      const result = await declinable(async () => runWorkspaceExportBundle({
        ...await buildContext(formats),
        destinationToken: destination.token,
        confirmReplacements: askToReplaceExports,
      }, formats, 'confirm'));
      if (!result) return { notice: EXPORT_CANCELLED_MESSAGE };
      await recordFiles(result.files);
      const fileText = workspaceNotice('Export', result);
      if (!result.failures.length && !unavailable.length) return { notice: fileText };
      const failed = result.failures.map(({ format, reason }) => `${formatLabel(format, family)}: ${reason}`).join('; ');
      const failureText = result.failures.length
        ? `Failed ${result.failures.length} format${result.failures.length === 1 ? '' : 's'}: ${failed}` : '';
      return {
        notice: fileText,
        error: [fileText, skipped, failureText].filter(Boolean).join(' · '),
        errorFormats: [...unavailable, ...result.failures].map(({ format }) => format),
      };
    });
  };

  const exportReplay = () => execute(job.id, [], async (): Promise<RunExportOutcome> => {
    const destination = await askForExportDestination({ title: 'Export mesh replay', detail: `Exact mesh and solve settings from run ${jobName(job)}.` });
    if (!destination) return { notice: EXPORT_CANCELLED_MESSAGE };
    const result = await declinable(async () => writeToOutputFolder(exportSubdirectoryForJob(job), [{
      filename: `${exportStemForJob(job)}.wgreplay`, blob: await fetchMeshReplay(job.id),
    }], fetch, 'confirm', destination.token, askToReplaceExports));
    if (!result) return { notice: EXPORT_CANCELLED_MESSAGE };
    await recordFiles(result.files);
    return { notice: workspaceNotice('Mesh replay', result) };
  });

  const sendRunToCad = () => execute(job.id, [], async (): Promise<RunExportOutcome> => {
    const design = hydrateJobDesign(job);
    if (!design) throw new Error(designAvailability.reason ?? 'This run has no recoverable design.');
    const result = await sendDesignToCad(
      design,
      job.design_revision,
      exportStemForJob(job),
      // A historical run is its own immutable handoff. It must not advance the
      // CAD identity of whichever editable document happens to be on screen.
      null,
      fetch,
      undefined,
      null,
      job.solve_options.polar_config,
    );
    return { notice: sentToCadMessage(result) };
  });

  const items: ActionMenuItem[] = [
    ...(job.status === 'complete' && job.config_summary.import_origin === 'mesh-file' ? [{
      id: 'mesh-replay', label: 'Mesh replay', trailing: '.wgreplay', group: 'Geometry & design',
      disabled: operation.busy, onSelect: async () => { await exportReplay(); },
    }] : []),
    ...(!cadRun ? [{
      id: 'send-to-cad',
      label: 'Send to CAD',
      trailing: '.wglink',
      group: 'CAD',
      disabled: operation.busy || !designExportable || Boolean(cadLimitation),
      disabledReason: cadLimitation ?? (designExportable ? undefined : designAvailability.reason ?? designState.reason ?? 'This run has no recoverable design.'),
      busy: operation.busy && operation.busyFormats.length === 0,
      busyLabel: 'Sending to CAD…',
      error: operation.lastErrorFormats.length === 0 ? operation.lastError ?? undefined : undefined,
      onSelect: async () => { await sendRunToCad(); },
    }] : []),
    ...FORMAT_CATALOG.filter((item) => !cadRun || !item.needsDesign || item.id === 'step').map((item): ActionMenuItem => {
      const disabledReason = formatReason(item);
      return {
        id: item.id,
        label: item.label,
        trailing: trailingFor(item, family),
        group: item.group,
        disabled: operation.busy || Boolean(disabledReason),
        disabledReason,
        busy: operation.busyFormats.includes(item.format),
        busyLabel: `Preparing ${item.label}…`,
        error: operation.lastErrorFormats.includes(item.format) ? operation.lastError ?? undefined : undefined,
        onSelect: async () => {
          await exportOne(item.format);
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

  if (!canExportRun(job) && !disabledReason) return null;

  const preferredNames = preferences.exportFormats.map((format) =>
    ['csv', 'png', 'json', 'step', 'stl', 'txt', 'zma', 'vxp'].includes(format)
      ? format.toUpperCase() : formatLabel(format, family));
  const names = preferredNames.join(' + ');
  const triggerLabel = !preferredNames.length ? 'Export'
    : preferredNames.length <= 2 && names.length <= 20 ? `Export ${names}`
      : `Export (${preferredNames.length})`;
  const triggerTitle = disabledReason ?? (preferredNames.length
    ? `Export ${preferences.exportFormats.map((format) => formatLabel(format, family)).join(' + ')}`
    : 'Choose an export format');

  return <div className={`run-export-control${compact ? ' compact' : ''}`}>
    <ActionMenu
      items={items}
      menuLabel={`Export ${jobName(job)}`}
      triggerLabel={triggerLabel}
      triggerTitle={triggerTitle}
      disabled={operation.busy || Boolean(disabledReason)}
      chevronLabel={`More export options for ${jobName(job)}`}
      onPrimary={preferences.exportFormats.length ? async () => { await exportPreferred(); } : undefined}
    />
    {operation.lastError
      ? <div className="design-menu-status run-export-feedback error" role="alert">{operation.lastError}</div>
      : operation.lastNotice && <div className="design-menu-status run-export-feedback" role="status">{operation.lastNotice}</div>}
  </div>;
}
