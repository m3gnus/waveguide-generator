import { currentModel, modelKindForRun, modelForRun, shownResult, type CurrentModel, type ResultSelection } from '../model/currentModel';
import type { ExportFormat } from '../prefs/preferences';
import type { CadIdentityProvenance, JobItem } from '../api/jobsSocket';
import { hydrateJobDesign } from '../jobs/jobDesign';
import type { DesignDocument } from '../stores/design';
import { wgSolveSettingsFromSolveOptions } from '../stores/designWire';
import type { WgSolveSettings } from '../stores/wgSolveBlock';

/** Geometry/config exports for a result must use the design that produced it. */
export function resultExportSnapshot(
  job: (Pick<JobItem, 'script_snapshot' | 'design_revision'>
    & Partial<Pick<JobItem, 'solve_options' | 'cad_source' | 'config_summary' | 'cad_state' | 'cad_intent' | 'design_availability'>>) | undefined,
): {
  design: DesignDocument | undefined;
  designRevision: number;
  polarConfig?: unknown;
  solveSettings?: WgSolveSettings | null;
  cadIdentity?: CadIdentityProvenance;
} {
  const polarConfig = job?.solve_options?.polar_config;
  return {
    design: job && modelKindForRun(job) === 'parametric' ? hydrateJobDesign(job) ?? undefined : undefined,
    designRevision: job?.design_revision ?? 0,
    ...(polarConfig && typeof polarConfig === 'object' ? { polarConfig } : {}),
    // The config exported for a run must describe that run, so its recorded
    // solve options are the source here -- never the draft on screen.
    solveSettings: wgSolveSettingsFromSolveOptions(job?.solve_options),
    ...(job?.cad_source?.identity ? { cadIdentity: job.cad_source.identity } : {}),
  };
}

/** A Results export uses the live selection; Jobs/automatic exports name an immutable run model. */
export function resultExportSelection(job: JobItem, model: CurrentModel = modelForRun(job),
  selection: ResultSelection = { primary: job.id }, jobs: readonly JobItem[] = [job]): JobItem {
  const shown = shownResult(model, selection, jobs).job;
  if (!shown || shown.id !== job.id) throw new Error('This result is not shown for the selected model.');
  return shown;
}

/** One capability rule for document, manual run and automatic run exports. */
export function parameterExportReason(model: Pick<CurrentModel, 'kind'>, format: ExportFormat): string | undefined {
  if (model.kind === 'parametric') return undefined;
  const reasons: Partial<Record<ExportFormat, string>> = {
    step: 'STEP export is not available for imported models.',
    stl: 'STL export is not available for imported models.',
    mwg_config: 'Imported models have no WG parameter config to export.',
    fusion_csv: 'Imported models have no WG profiles to export.',
  };
  return reasons[format];
}

/** Refuse stale File commands, including a model arrival during a destination dialog. */
export function assertCurrentModelExport(target: CurrentModel, format: ExportFormat): void {
  const current = currentModel();
  const reason = parameterExportReason(current, format);
  if (reason) throw new Error(reason);
  if (current.kind !== target.kind || current.key !== target.key || current.lineage !== target.lineage) {
    throw new Error('The model changed. Start the export again.');
  }
}
