import type { JobItem } from '../api/jobsSocket';
import { jobsSocket } from '../api/jobsSocket';
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
import { EMPTY_RUN_EXPORT_STATE, useRunExportStore, type RunExportOutcome } from '../stores/runExports';
import { canLoadJobDesign, hydrateJobDesign, jobDesignAvailability, jobRerunState } from './jobDesign';
import { exportStemForJob, exportSubdirectoryForJob } from './exportNaming';
import './RunExportControl.css';

export interface RunExportControlProps {
  job: JobItem;
  compact?: boolean;
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
  { id: 'step', format: 'step', label: 'STEP solid', trailing: '.step', group: 'Geometry & design', needsResult: false, needsDesign: true },
  { id: 'mwg_config', format: 'mwg_config', label: 'Parameter config', trailing: '.cfg', group: 'Geometry & design', needsResult: false, needsDesign: true },
  { id: 'impedance_csv', format: 'impedance_csv', label: 'Impedance', trailing: '.csv', group: 'Advanced', needsResult: true, needsDesign: false },
  { id: 'radiation_impedance_csv', format: 'radiation_impedance_csv', label: 'Radiation matrix curves', trailing: '.csv', group: 'Advanced', needsResult: false, needsDesign: false, needsRadiationArtifact: true },
  { id: 'radiation_impedance_npz', format: 'radiation_impedance_npz', label: 'Lossless radiation matrix', trailing: '.npz', group: 'Advanced', needsResult: false, needsDesign: false, needsRadiationArtifact: true },
  { id: 'polar_csv', format: 'polar_csv', label: 'Polar directivity', trailing: '.csv', group: 'Advanced', needsResult: true, needsDesign: false },
  { id: 'txt', format: 'txt', label: 'Summary report', trailing: '.txt', group: 'Advanced', needsResult: true, needsDesign: false },
  { id: 'stl', format: 'stl', label: 'STL mesh', trailing: '.stl', group: 'Advanced', needsResult: false, needsDesign: true },
  { id: 'fusion_csv', format: 'fusion_csv', label: 'Fusion curves', trailing: '.csv', group: 'Advanced', needsResult: false, needsDesign: true },
];

const CATALOG_BY_FORMAT = new Map(
  FORMAT_CATALOG.map((item) => [item.format, item]),
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

function unavailableFormatReason(
  item: CatalogItem,
  job: JobItem,
  designExportable: boolean,
  designUnavailableReason: string,
): string | undefined {
  if (item.id === 'polar_frd' && Object.keys(job.polar_grid ?? {}).length === 0) {
    return 'This run has no directivity data for a polar FRD set.';
  }
  if (item.needsRadiationArtifact && !job.has_radiation_impedance_artifact) {
    return 'This run has no retained passive-cardioid radiation matrix.';
  }
  if (item.needsResult && !job.has_results) return 'This run\'s results were removed by retention.';
  if (item.needsPressureBasis && !job.has_pressure_basis_artifact) {
    return 'This run has no retained complex pressure basis. Re-solve an imported Metal run to create one.';
  }
  if (item.needsDesign && !designExportable) return designUnavailableReason;
  return undefined;
}

function jobName(job: JobItem): string {
  return job.label?.trim() || `${String(job.config_summary.formula_type ?? 'design').toLowerCase()}_${job.id.slice(0, 8)}`;
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
  const designUnavailableReason = designAvailability.reason ?? designState.reason ?? 'This run has no recoverable design.';

  const buildContext = async (formats: readonly ExportFormat[]): Promise<ExportContext> => ({
    ...(needsResults(formats) ? { result: await fetchJobResults(job.id) as ResultPayload } : {}),
    ...resultExportSnapshot(job),
    jobId: job.id,
    jobStem: exportStemForJob(job),
    hasRadiationImpedanceArtifact: job.has_radiation_impedance_artifact,
    workspaceSubdirectory: exportSubdirectoryForJob(job),
    designName: job.label ?? undefined,
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
    const result = await runWorkspaceExportBundle(await buildContext([format]), [format], 'overwrite');
    await recordFiles(result.files);
    const notice = workspaceNotice(formatLabel(format), result);
    return result.failures.length ? {
      notice,
      error: `${notice} · ${result.failures.map(({ reason }) => reason).join('; ')}`,
      errorFormats: [format],
    } : { notice };
  });

  const exportPreferred = () => {
    const formats = [...preferences.exportFormats];
    return execute(job.id, formats, async (): Promise<RunExportOutcome> => {
      const result = await runWorkspaceExportBundle(await buildContext(formats), formats, 'overwrite');
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
    {
      id: 'send-to-cad',
      label: 'Send to CAD',
      trailing: '.wglink',
      group: 'CAD',
      disabled: operation.busy || !designExportable,
      disabledReason: designExportable ? undefined : designAvailability.reason ?? designState.reason ?? 'This run has no recoverable design.',
      busy: operation.busy && operation.busyFormats.length === 0,
      busyLabel: 'Sending to CAD…',
      error: operation.lastErrorFormats.length === 0 ? operation.lastError ?? undefined : undefined,
      onSelect: async () => { await sendRunToCad(); },
    },
    ...FORMAT_CATALOG.map((item): ActionMenuItem => {
      const disabledReason = unavailableFormatReason(item, job, designExportable, designUnavailableReason);
      return {
        id: item.id,
        label: item.label,
        trailing: item.trailing,
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

  if (!canExportRun(job)) return null;

  return <div className={`run-export-control${compact ? ' compact' : ''}`}>
    <ActionMenu
      items={items}
      menuLabel={`Export ${jobName(job)}`}
      triggerLabel={compact ? 'Export' : `Export${preferences.exportFormats.length ? ` (${preferences.exportFormats.length})` : ''}`}
      chevronLabel={`More export options for ${jobName(job)}`}
      onPrimary={preferences.exportFormats.length && preferences.exportFormats.every((format) => {
        const item = CATALOG_BY_FORMAT.get(format);
        if (!item) return false;
        return !unavailableFormatReason(item, job, designExportable, designUnavailableReason);
      }) ? async () => { await exportPreferred(); } : undefined}
    />
    {operation.lastError
      ? <div className="design-menu-status run-export-feedback error" role="alert">{operation.lastError}</div>
      : operation.lastNotice && <div className="design-menu-status run-export-feedback" role="status">{operation.lastNotice}</div>}
  </div>;
}
