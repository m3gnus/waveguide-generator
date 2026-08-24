import { useId, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { useEffect } from 'react';
import type { CadReturnBundle, CadReturnFinding, CadReturnIngestRecord } from '../api/cadlink';
import { OnshapePublicConsentRequired, sendDesignToOnshape } from '../api/onshape';
import { usePreferences } from '../prefs/preferences';
import { useCadReturnStore } from '../stores/cadReturn';
import { parkedSolveCommandStore } from '../stores/solveCommand';
import { recordCommittedAthPolars, useDesignStore } from '../stores/design';
import { polarConfigFromUi, useSolveOptionsStore } from '../stores/solveOptions';
import { useDocumentStore } from '../stores/document';
import { designNameSlug, UNTITLED_SLUG } from '../stores/designName';
import { useCapabilities } from '../jobs/useCapabilities';
import {
  cadLinkCoordinatorBridge,
  returnBelongsToProject,
} from './CadLinkCoordinator';
import { fusionWorkflowView, onshapeWorkflowView, type CadWorkflowView } from './cadWorkflowView';
import { Icon } from './icons';
import { fullTime, pluralized, relativeTime } from './cadTime';
import { CadProjectHeader, CadProjectHistory } from './CadProjectPanel';
import { requestSettings } from './settingsNavigation';
import { workspaceNavigation } from './workspaceNavigation';
import './cadLinkPanel.css';

// Kept as public panel helpers for existing callers; implementation lives next
// to the two solve entry points so the global command can share it.
import { importedSubmissionNotices } from '../jobs/importedSubmission';
export { buildImportedSubmission, widenPolarToDerivation } from '../jobs/importedSubmission';
export { newestReturnArrival, showIngestedMeshInViewport } from './CadLinkCoordinator';

const FRESHNESS_COPY: Record<string, string> = {
  current: 'Current — unchanged fingerprint and the saved design still agree with this generator.',
  body_modified: 'CAD body changed after linking. This returned geometry remains the solve truth.',
  missing_design: 'The linked design is not in this workspace registry; this is normal for a return from another machine.',
  design_changed: 'The linked Waveguide Generator design has changed since this geometry was exported.',
  generator_changed: 'The same saved design would export differently with the current generator.',
  unknown: 'Freshness could not be established from the available evidence.',
  unlinked: 'Imported CAD model — not linked to a Waveguide Generator design. The assembly frame is solved as-is: radiation along +Z with the throat at the origin.',
  mixed: 'The linked instances disagree about freshness; each instance carries its own verdict.',
};

// The workflow views moved beside the coordinator's unified send path; the
// re-export keeps this module the panel-facing home for existing callers.
export { fusionWorkflowView, onshapeWorkflowView, type CadWorkflowView };

function compactValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

function findingDetail(finding: CadReturnFinding): string {
  const preferred = finding.reason ?? finding.verdict;
  if (preferred) return String(preferred).replaceAll('_', ' ');
  const details = Object.entries(finding)
    .filter(([key]) => !['id', 'kind', 'blocking', 'evidence_path'].includes(key))
    .map(([key, value]) => `${key.replaceAll('_', ' ')}: ${compactValue(value)}`);
  return details.join(' · ') || 'Recorded by CAD-return ingestion.';
}

function returnDisplayName(bundle: CadReturnBundle): string {
  if (bundle.documentName) return bundle.documentName;
  const date = new Date(bundle.modifiedAt);
  if (Number.isNaN(date.getTime())) return 'Return';
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `Return · ${hours}:${minutes}`;
}

function bundleInventory(bundle: CadReturnBundle): string {
  if (!bundle.readable) return bundle.reason ?? 'Manifest is unreadable';
  const sources = pluralized(bundle.sourceCount ?? bundle.sources.length, 'source');
  if (!bundle.instanceCount) return sources;
  return `${sources} · ${pluralized(bundle.instanceCount, 'linked instance')}`;
}

function formatCount(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return compactValue(value);
  if (value < 1_000) return String(Math.round(value));
  const thousands = value / 1_000;
  return `${thousands >= 100 ? Math.round(thousands) : thousands.toFixed(1)} k`;
}

function formatDuration(seconds: unknown): string | null {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return null;
  if (seconds < 60) return '<1 min';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `~${minutes} min`;
  return `~${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

interface CadDrawerProps {
  title: string;
  chip?: string;
  defaultOpen?: boolean;
  warning?: boolean;
  className?: string;
  children: ReactNode;
}

/** CAD sections use the same heading/button/chevron disclosure pattern as the
 * parameter rail, but keep their own compact card surface and state chip. */
function CadDrawer({ title, chip, defaultOpen = false, warning = false, className = '', children }: CadDrawerProps) {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = useId();
  return <section className={`cad-section cad-drawer${open ? '' : ' closed'}${warning ? ' degraded' : ''}${className ? ` ${className}` : ''}`}>
    <h4 className="section-heading">
      <button type="button" className="section-head" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen((value) => !value)}>
        <span className="chevron" aria-hidden="true">⌄</span>
        <span className="section-name">{title}</span>
        <span className="spacer"/>
        {chip && <span className={`cad-state-chip${warning ? ' warn' : ''}`}>{chip}</span>}
      </button>
    </h4>
    {open && <div id={bodyId} className="section-body">{children}</div>}
  </section>;
}

type CheckState = 'ok' | 'info' | 'warn';

interface CheckDescriptor {
  key: string;
  name: string;
  state: CheckState;
  verdict: string;
  /** Hover: what this check verifies, spelled out. */
  title: string;
  /** The full evidence, one chevron away. Absent when the verdict is whole. */
  detail?: ReactNode;
}

const CHECK_GLYPH: Record<CheckState, string> = { ok: '✓', info: 'i', warn: '!' };

function freshnessSummary(record: CadReturnIngestRecord): string {
  if (record.freshness.verdict === 'unlinked') return 'unlinked';
  const instances = record.freshness.instances;
  if (instances.length === 0) return 'unknown';
  if (instances.every((instance) => instance.verdict === 'current')) return 'current';
  if (instances.every((instance) => instance.verdict === instances[0]?.verdict)) {
    return instances[0].verdict.replaceAll('_', ' ');
  }
  return 'mixed';
}

/** One row per verification the ingest performed. The six drawers this list
 * replaced each carried a chip that read "clean" on virtually every return;
 * a check only asks for attention when it deviates, and holds its evidence
 * behind its own chevron. */
function recordChecks(record: CadReturnIngestRecord): CheckDescriptor[] {
  const checks: CheckDescriptor[] = [];

  // Scope
  const scopeClean = record.scope.status === 'clean';
  const included = record.scope.included ?? [];
  const skipped = record.scope.skipped ?? [];
  const scopeFindings = record.findings.filter((finding) => finding.kind === 'scope-degradation');
  checks.push({
    key: 'scope',
    name: 'Scope',
    state: scopeClean ? 'ok' : 'warn',
    verdict: scopeClean
      ? included.length
        ? `${pluralized(included.length, 'body', 'bodies')} exported, nothing skipped`
        : 'the exported exterior was complete'
      : `${pluralized(record.scope.degraded_skip_count, 'object')} skipped — solve is degraded`,
    title: 'Everything the CAD document exported for the acoustic exterior, and anything it had to skip.',
    detail: (included.length || skipped.length || scopeFindings.length) ? <>
      {included.map((item, index) => <p key={`${String(item.object_id)}-${index}`} className="cad-detail"><b>Included · {String(item.name ?? item.object_id ?? 'Unnamed object')}</b>{item.body_kind ? ` — ${item.body_kind}` : ''}</p>)}
      {skipped.map((item, index) => <p key={`${String(item.object_id)}-${index}`} className={`cad-detail scope-skip-${item.severity ?? 'info'}`}><b>Skipped · {String(item.name ?? item.object_id ?? item.kind ?? 'Unnamed object')}</b> — {String(item.reason ?? item.kind ?? 'not included')} · {String(item.severity ?? 'info')}</p>)}
      {skipped.length === 0 && scopeFindings.map((finding) => <p key={finding.id} className="cad-detail"><b>{String(finding.object_id ?? 'Unnamed object')}</b> — {findingDetail(finding)}</p>)}
    </> : undefined,
  });

  // Freshness
  const freshness = freshnessSummary(record);
  checks.push({
    key: 'freshness',
    name: 'Freshness',
    state: freshness === 'current' ? 'ok' : freshness === 'unlinked' ? 'info' : 'warn',
    verdict: freshness,
    title: FRESHNESS_COPY[freshness.replaceAll(' ', '_')] ?? 'Whether this returned geometry still matches the linked WG design and generator.',
    detail: record.freshness.verdict === 'unlinked'
      ? <p className="cad-verdict neutral">{FRESHNESS_COPY.unlinked}</p>
      : record.freshness.instances.length ? <>{record.freshness.instances.map((instance) => <div className={`cad-verdict ${instance.verdict === 'current' ? 'ok' : 'warn'}`} key={instance.instance_id}>
          <b>{instance.instance_id}</b><span>{FRESHNESS_COPY[instance.verdict] ?? instance.verdict}</span>
          {instance.error && <small>{instance.error}</small>}
        </div>)}</> : undefined,
  });

  // Symmetry
  const planes = Object.entries(record.symmetry.planes ?? {});
  const rejectedPlanes = planes.filter(([, verdict]) => !verdict.accepted);
  const appliedCuts = record.symmetry.cut_planes ?? [];
  const cadDomain = appliedCuts.length >= 2 ? 'quarter domain' : appliedCuts.length === 1 ? 'half domain' : 'full domain';
  checks.push({
    key: 'symmetry',
    name: 'Symmetry',
    state: planes.length === 0 ? 'info' : rejectedPlanes.length === 0 ? 'ok' : 'warn',
    verdict: planes.length === 0
      ? 'no plane verdicts recorded'
      : rejectedPlanes.length === 0
        ? `accepted · solving ${cadDomain}`
        : `${rejectedPlanes.map(([name]) => name).join(', ')} rejected · solving ${cadDomain}`,
    title: 'Mirror planes WG re-tested on the returned STEP after CAD edits, bodies and source tags were applied. A rejected plane keeps the larger safe domain instead of inheriting the parametric reduction.',
    detail: <>
      {planes.map(([name, verdict]) => {
        const residual = verdict.max_residual_step_units ?? verdict.residuals;
        const offModel = verdict.worst_off_model_distance_step_units;
        const details = [
          verdict.reason ? String(verdict.reason) : null,
          residual === undefined ? null : `max residual ${compactValue(residual)} STEP units`,
          offModel === undefined ? null : `worst off-model ${compactValue(offModel)} STEP units`,
        ].filter(Boolean).join(' · ');
        return <div className="cad-row" key={name}><b>{name}</b><span className={verdict.accepted ? 'ok-text' : 'warn-text'}>{verdict.accepted ? 'accepted' : 'rejected'}</span><small>{details}</small></div>;
      })}
      {planes.length === 0 && <p>No coordinate plane verdicts were recorded.</p>}
      {appliedCuts.length > 0 && <p className="cad-detail">Applied cuts: {appliedCuts.join(', ')}</p>}
      <p className="cad-detail">Resolved independently from Parametric mode: WG re-tests the returned geometry itself.</p>
    </>,
  });

  // Healing
  const healed = Boolean(record.healing.performed);
  checks.push({
    key: 'healing',
    name: 'Healing',
    state: healed ? 'warn' : 'ok',
    verdict: healed ? `OCC healing was needed${record.healing.mode ? ` · ${record.healing.mode}` : ''}` : 'not needed',
    title: 'Whether OCC healing had to repair the exported CAD before it could mesh. Healed geometry can differ subtly from the export.',
    detail: healed ? <>
      <p>The exported CAD did not mesh unchanged. OCC healing was used; re-export stitched or imprinted CAD when possible.</p>
      {'original_mesh_error' in record.healing && <p className="cad-detail">{compactValue(record.healing.original_mesh_error)}</p>}
    </> : undefined,
  });

  // Mesh sizing & cost
  const sizing = record.sizing_estimate;
  const feasibility = typeof sizing.feasibility === 'string' ? sizing.feasibility : null;
  const meshParts = [
    typeof sizing.n_triangles === 'number' ? `${formatCount(sizing.n_triangles)} triangles` : null,
    typeof sizing.ram_gb === 'number' ? `~${(sizing.ram_gb as number).toFixed(1)} GB` : null,
    formatDuration(sizing.solve_seconds_total),
  ].filter(Boolean);
  checks.push({
    key: 'mesh',
    name: 'Mesh',
    state: feasibility === null || feasibility === 'ok' ? 'ok' : feasibility === 'caution' ? 'info' : 'warn',
    verdict: meshParts.length
      ? meshParts.join(' · ') + (feasibility && feasibility !== 'ok' ? ` · ${feasibility}` : '')
      : 'no cost estimate recorded',
    title: 'Estimated solver cost of the prepared mesh at the chosen sizing: symmetry-reduced triangle count, dense-matrix memory, and solve time for the sweep.',
    detail: <>{Object.entries(sizing).map(([key, value]) => <div className="cad-row" key={key}><b>{key.replaceAll('_', ' ')}</b><span>{compactValue(value)}</span></div>)}</>,
  });

  // Polar grid
  const polarAxes = Object.entries((record.polar_grid_derivation.axes ?? {}) as Record<string, { symmetry_accepted?: boolean; minimum_deg?: number; maximum_deg?: number; plane?: string }>);
  const widened = polarAxes.filter(([, axis]) => axis.symmetry_accepted === false);
  checks.push({
    key: 'polar',
    name: 'Polar grid',
    state: widened.length ? 'info' : 'ok',
    verdict: polarAxes.length === 0
      ? 'no derivation recorded'
      : widened.length
        ? `${widened.map(([axis]) => axis).join(', ')} widened to a full sweep`
        : 'follows the accepted symmetry',
    title: 'The directivity sweep derived from the symmetry verdicts. An axis whose mirror plane was rejected is swept over the full circle; sweeps may widen but never narrow.',
    detail: <>
      {polarAxes.map(([axis, spec]) => <div className="cad-row" key={axis}><b>{axis}</b><span>{spec.minimum_deg ?? 0}° … {spec.maximum_deg ?? 180}°{spec.symmetry_accepted === false ? ' · widened' : ''}</span></div>)}
      {Object.entries(record.polar_grid_derivation).filter(([key]) => key !== 'axes').map(([key, value]) => <div className="cad-row" key={key}><b>{key.replaceAll('_', ' ')}</b><span>{compactValue(value)}</span></div>)}
    </>,
  });

  return checks;
}

function CheckRow({ check }: { check: CheckDescriptor }) {
  // A failing check arrives open; the user can still fold it away, so the
  // element is stateful rather than a hard-wired `open` attribute React would
  // keep re-asserting on every render.
  const [open, setOpen] = useState(check.state === 'warn');
  if (!check.detail) {
    return <div className={`cad-check cad-check-${check.state}`} title={check.title}>
      <span className="cad-check-glyph" aria-hidden="true">{CHECK_GLYPH[check.state]}</span>
      <b>{check.name}</b>
      <span className="cad-check-verdict">{check.verdict}</span>
    </div>;
  }
  return <details
    className={`cad-check cad-check-${check.state}`}
    open={open}
    onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}
  >
    <summary title={check.title}>
      <span className="cad-check-glyph" aria-hidden="true">{CHECK_GLYPH[check.state]}</span>
      <b>{check.name}</b>
      <span className="cad-check-verdict">{check.verdict}</span>
      <span className="cad-check-chevron" aria-hidden="true">›</span>
    </summary>
    <div className="cad-check-detail">{check.detail}</div>
  </details>;
}

/** Findings are part of the same checklist: a blocking finding is a failing
 * check, not a gate. It is recorded with the run when the user solves. */
function FindingRows({ record }: { record: CadReturnIngestRecord }) {
  if (record.findings.length === 0) return null;
  return <div className="cad-check-findings">
    <p className="cad-check-findings-head">{pluralized(record.findings.length, 'finding')}</p>
    {record.findings.map((finding) => <div key={finding.id} className={`cad-check ${finding.blocking ? 'cad-check-warn' : 'cad-check-info'}`}>
      <span className="cad-check-glyph" aria-hidden="true">{finding.blocking ? '!' : 'i'}</span>
      <b>{finding.kind.replaceAll('-', ' ')}</b>
      <span className="cad-check-verdict">{findingDetail(finding)}{finding.blocking && <small
        className="cad-blocking-suffix"
        title="Recorded in the run's provenance when you solve. Solving is not blocked; this marks evidence worth understanding first."
      > · blocking</small>}</span>
    </div>)}
  </div>;
}

function ChecksSection({ record }: { record: CadReturnIngestRecord }) {
  const checks = useMemo(() => recordChecks(record), [record]);
  const attention = checks.filter((check) => check.state === 'warn').length;
  const blocking = record.findings.filter((finding) => finding.blocking).length;
  const needsAttention = attention > 0 || blocking > 0;
  const chip = needsAttention
    ? `${attention + blocking} need attention`
    : record.findings.length
      ? `passed · ${pluralized(record.findings.length, 'finding')}`
      : 'all passed';
  return <CadDrawer
    key={record.ingest_id}
    title={`Checks (${checks.length})`}
    chip={chip}
    defaultOpen={needsAttention}
    warning={needsAttention}
    className="cad-checks"
  >
    {checks.map((check) => <CheckRow key={check.key} check={check}/>)}
    <FindingRows record={record}/>
  </CadDrawer>;
}

interface ModelVersionsProps {
  projectBundles: CadReturnBundle[];
  unlinkedReturns: CadReturnBundle[];
  otherProjectReturns: number;
  selectedPath: string | null;
  select: (bundle: CadReturnBundle) => void;
}

/** Previous returns are previous models, so they live under the model they
 * relate to. Unlinked returns are a filter, never merged into the project's
 * own history, and are never selected as this project's geometry automatically. */
function ModelVersions({ projectBundles, unlinkedReturns, otherProjectReturns, selectedPath, select }: ModelVersionsProps) {
  const [filter, setFilter] = useState<'project' | 'unlinked'>('project');
  const showChips = unlinkedReturns.length > 0 && projectBundles.length > 0;
  const shown = filter === 'unlinked' && unlinkedReturns.length ? unlinkedReturns : projectBundles.length ? projectBundles : unlinkedReturns;
  const total = projectBundles.length + unlinkedReturns.length;
  if (total === 0 && otherProjectReturns === 0) return null;
  return <CadDrawer title={`Model versions (${total})`} className="cad-history">
    {showChips && <div className="cad-version-filter" role="tablist" aria-label="Model version filter">
      <button role="tab" aria-selected={filter === 'project'} className={filter === 'project' ? 'on' : ''} onClick={() => setFilter('project')}>This project · {projectBundles.length}</button>
      <button
        role="tab"
        aria-selected={filter === 'unlinked'}
        className={filter === 'unlinked' ? 'on' : ''}
        title="Returns that name no CAD-linked project. Never selected as this project's geometry automatically."
        onClick={() => setFilter('unlinked')}
      >Unlinked · {unlinkedReturns.length}</button>
    </div>}
    <div className="cad-bundle-list" role="listbox" aria-label="CAD return history">
      {shown.map((bundle) => <button
        type="button"
        key={bundle.bundlePath}
        role="option"
        aria-selected={selectedPath === bundle.bundlePath}
        disabled={!bundle.readable}
        onClick={() => select(bundle)}
        title={!bundle.readable ? bundle.reason ?? 'Manifest is unreadable' : `${bundleInventory(bundle)} · ${fullTime(bundle.modifiedAt)}`}
      ><b>{returnDisplayName(bundle)}</b><span>{bundleInventory(bundle)}</span><time dateTime={bundle.modifiedAt} title={fullTime(bundle.modifiedAt)}>{relativeTime(bundle.modifiedAt)}</time></button>)}
    </div>
    {otherProjectReturns > 0 && <p className="cad-detail">{pluralized(otherProjectReturns, 'return')} from other CAD-linked projects {otherProjectReturns === 1 ? 'is' : 'are'} not listed. Open that project from File → CAD-linked designs to use {otherProjectReturns === 1 ? 'it' : 'them'}.</p>}
  </CadDrawer>;
}

export function CadLinkPanel() {
  const state = useCadReturnStore();
  const preferences = usePreferences();
  const design = useDesignStore((current) => current.design);
  const designRevision = useDesignStore((current) => current.designRevision);
  const documentName = useDocumentStore((current) => current.designName);
  const identity = useDocumentStore((current) => current.identity);
  const setCadLink = useDocumentStore((current) => current.setCadLink);
  const cadCoordinator = useSyncExternalStore(cadLinkCoordinatorBridge.subscribe, cadLinkCoordinatorBridge.getSnapshot, cadLinkCoordinatorBridge.getSnapshot);
  const parkedCommand = useSyncExternalStore(parkedSolveCommandStore.subscribe, parkedSolveCommandStore.getSnapshot, parkedSolveCommandStore.getSnapshot).command;
  const [confirmPublicDocument, setConfirmPublicDocument] = useState<string | null>(null);
  const [sendingToOnshape, setSendingToOnshape] = useState(false);
  const onshapeSendGeneration = useRef(0);
  const onshape = preferences.cadApplication === 'onshape';
  const {
    bundles,
    loading,
    ingesting,
    ingestError,
    sendingToFusion,
    error,
    status,
    viewportNotice,
    fusionStatus,
    onshapeStatus,
    onshapeConnection,
  } = cadCoordinator;
  const projectBundles = identity?.designId
    ? bundles.filter((bundle) => returnBelongsToProject(bundle, identity.designId))
    : bundles;
  const unlinkedReturns = identity?.designId
    ? bundles.filter((bundle) => (bundle.designIds ?? []).length === 0)
    : [];
  const otherProjectReturns = bundles.length - projectBundles.length - unlinkedReturns.length;

  useEffect(() => () => { onshapeSendGeneration.current += 1; }, []);

  // The outbound leg for Onshape. There is no local client to notify and no
  // workspace folder to write into: WG uploads the bundle over HTTPS itself.
  const sendToOnshape = async (allowPublic = false) => {
    const request = ++onshapeSendGeneration.current;
    const sourceRevision = designRevision;
    cadCoordinator.clearFeedback(); setSendingToOnshape(true);
    try {
      const wasLinked = onshapeStatus?.state === 'stale' || onshapeStatus?.state === 'current';
      const polarConfig = polarConfigFromUi(useSolveOptionsStore.getState().polar);
      const result = await sendDesignToOnshape(
        design, designRevision, designNameSlug(documentName), identity, {
          allowPublic,
          polarConfig,
          instanceId: onshapeStatus?.selectedInstanceId ?? null,
        },
      );
      if (request !== onshapeSendGeneration.current) return;
      setConfirmPublicDocument(null);
      if (useDesignStore.getState().designRevision !== sourceRevision) {
        cadCoordinator.reportStatus('Sent the previous design to Onshape, but the WG design changed while it was uploading. Send again to link the current design.');
        return;
      }
      recordCommittedAthPolars(polarConfig);
      const visibility = result.onshape.isPublic ? ' · public document' : '';
      cadCoordinator.reportStatus(result.onshape.createdDocument
        ? `Created ${result.onshape.documentName} in Onshape · ${result.onshape.variablesPushed} parameters${visibility}`
        : `Updated ${result.onshape.documentName} in Onshape · sequence ${result.sequence}${visibility}`);
      if (!wasLinked && result.identity) setCadLink(result.identity, 'current');
      // A first send can mint this identity. Passing it explicitly avoids the
      // pre-send closure briefly reporting the new document as not linked.
      await cadCoordinator.refreshOnshapeStatus(result.identity);
    } catch (reason) {
      if (request !== onshapeSendGeneration.current) return;
      // HTTP 428 is control flow, not a generic send error: this dialog is the
      // only path that can retry with allowPublic on an Onshape Free account.
      if (reason instanceof OnshapePublicConsentRequired) {
        setConfirmPublicDocument(reason.message);
      } else {
        cadCoordinator.reportError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (request === onshapeSendGeneration.current) setSendingToOnshape(false);
    }
  };

  // The coordinator owns one pending pull shared by every control, so both
  // entry points stay busy until arrival, failure, or the timeout.
  const bringFromFusion = () => {
    cadCoordinator.clearFeedback();
    void cadCoordinator.pullFromFusion().catch(() => undefined);
  };

  const sendToFusion = () => { void cadCoordinator.sendWgToFusion().catch(() => undefined); };

  const workflow = onshape ? onshapeWorkflowView(onshapeStatus) : fusionWorkflowView(fusionStatus);
  // Imported geometry is solved on Metal or not at all: runtime.py rewrites
  // AUTO to metal and refuses bempp outright. Without Metal the whole round
  // trip still works as a CAD workflow and only the solve is out of reach, so
  // this states the boundary up front instead of hiding the panel or letting
  // someone discover it after exporting and preparing the model.
  const { engines: solverEngines, isLoading: capabilitiesLoading } = useCapabilities();
  const metalUnavailable = !capabilitiesLoading
    && !solverEngines.some((engine) => engine.name.toLowerCase() === 'metal' && engine.available);
  const designName = designNameSlug(documentName);
  const shownName = designName === UNTITLED_SLUG ? 'this design' : designName;
  const canRequestFusionReturn = Boolean(
    fusionStatus?.running && fusionStatus.documentName && fusionStatus.documentId
    && fusionStatus.link && identity?.designId,
  );
  // A Fusion solve request that stopped at a gate is held, not discarded, so it
  // needs somewhere to be seen and acted on.
  const parked = parkedCommand && parkedCommand.blockers.length > 0 ? parkedCommand : null;
  // Onshape's Free plan makes every document world-readable. Say so before the
  // user sends, not after -- and say it from the plan WG actually read.
  const publicOnly = onshapeConnection?.plan?.publicOnly === true;
  const linkedDocument = onshapeStatus?.link ?? null;
  const matchingOnshapeLinks = onshapeStatus?.matchingLinks ?? [];
  const matchingFusionLinks = fusionStatus?.matchingLinks ?? [];
  const record = state.ingestRecord;
  const bundle = state.selectedBundle;
  const quietLink = workflow.state === 'current' || workflow.state === 'checking';
  const fusionBothChanged = Boolean(fusionStatus?.wgChangesAvailable && fusionStatus.fusionChangesAvailable);
  const staleModel = Boolean(record && state.needsIngest);
  const onshapeActionLabel = workflow.action === 'update' ? 'Send WG changes to Onshape' : `Create ${shownName} in Onshape`;

  const linkActions = onshape
    ? <>
      <button className="link-button" disabled={sendingToOnshape} onClick={() => void sendToOnshape()}>Send to Onshape</button>
      {linkedDocument && <button className="link-button" disabled={ingesting} title="Export the linked Part Studio to STEP, verify its source evidence, and prepare it for the viewport and solver." onClick={() => { void cadCoordinator.returnFromOnshape().catch(() => undefined); }}>Bring geometry into WG</button>}
      {linkedDocument?.documentUrl && <a className="link-button" href={linkedDocument.documentUrl} target="_blank" rel="noreferrer noopener">Open in Onshape</a>}
    </>
    : <>
      <button className="link-button" disabled={sendingToFusion} title="Rebuild the linked Fusion waveguide from the current WG design." onClick={sendToFusion}>Send to Fusion</button>
      <button className="link-button" disabled={!canRequestFusionReturn || cadCoordinator.pullingFromFusion} title="Ask Fusion for the active document's current geometry and source tags." onClick={() => void bringFromFusion()}>{cadCoordinator.pullingFromFusion ? 'Waiting for Fusion…' : 'Bring geometry into WG'}</button>
    </>;

  return <div className="cadlink-panel panel-scroll">
    <h2 className="sr-only">CAD Link</h2>
    {/* One notice channel. Transient events land here, at the top of the rail;
        persistent conditions render inside the card they belong to. */}
    {error && <div className="cad-alert cad-alert-error" role="alert">{error}</div>}
    {status && <div className="cad-status-strip" role="status">{status}</div>}

    {/* 1 · Project: what am I working on? */}
    <CadProjectHeader/>

    {/* 2 · CAD Link: is CAD in sync with WG? One card, both directions. */}
    <section className={`cad-workflow cad-link-card${quietLink ? '' : ' attention'}`}>
      {quietLink
        ? <details className="cad-link-quiet">
          <summary title={workflow.detail}>
            <span className="cad-link-chevron" aria-hidden="true">›</span>
            <span className={`cad-connection-dot cad-connection-dot-${workflow.state}`} aria-hidden="true"/>
            <b>{onshape ? 'Onshape' : 'Fusion 360'}{workflow.state === 'current' ? ' · in sync' : ' · checking…'}</b>
            <span className="cad-link-meta">{onshape ? linkedDocument?.documentName ?? '' : fusionStatus?.documentName ?? ''}</span>
          </summary>
          <div className="cad-link-quiet-body">
            <p>{workflow.detail}</p>
            <div className="cad-link-actions">
              {linkActions}
              <button className="link-button cad-link-settings" onClick={() => requestSettings('cad')}>Settings</button>
            </div>
          </div>
        </details>
        : <>
          <div className={`cad-connection cad-connection-${workflow.state}`}>
            <span className="cad-connection-dot" aria-hidden="true"/>
            <div><h4>{workflow.headline}</h4><p>{workflow.detail}</p></div>
            <button className="link-button cad-link-settings" onClick={() => requestSettings('cad')}>Settings</button>
          </div>
          {!onshape && matchingFusionLinks.length > 1 && workflow.state === 'instance-selection' && <label className="field-row linked-instance-selection">
            <span>Managed Fusion link</span>
            <select
              aria-label="Linked Fusion instance"
              value={fusionStatus?.link?.instanceId ?? ''}
              onChange={(event) => cadCoordinator.selectFusionInstance(event.target.value)}
            >
              <option value="" disabled>Choose an instance</option>
              {matchingFusionLinks.map((link) => <option value={link.instanceId} key={link.instanceId}>{link.instanceId}</option>)}
            </select>
            <small>Freshness, geometry requests and updates use this exact managed body.</small>
          </label>}
          {onshape && matchingOnshapeLinks.length > 1 && <label className="field-row linked-instance-selection">
            <span>Managed Onshape link</span>
            <select
              aria-label="Linked Onshape instance"
              value={onshapeStatus?.selectedInstanceId ?? ''}
              onChange={(event) => cadCoordinator.selectOnshapeInstance(event.target.value)}
            >
              <option value="" disabled>Choose a link</option>
              {matchingOnshapeLinks.map((link) => <option value={link.instanceId} key={link.instanceId}>
                {link.documentName} · {link.instanceId}
              </option>)}
            </select>
            <small>Updates and returns use this exact managed Part Studio link. WG does not guess from the newest document.</small>
          </label>}
          {/* Fusion: the actions that resolve the out-of-sync state, and only those. */}
          {!onshape && workflow.state === 'not-configured' && <button className="primary cad-primary-action" onClick={() => requestSettings('cad')}>Set up Fusion connection</button>}
          {!onshape && workflow.action === 'open' && <button className="primary cad-primary-action" disabled={sendingToFusion} onClick={sendToFusion}>{sendingToFusion ? 'Sending…' : 'Open in Fusion 360'}</button>}
          {!onshape && fusionStatus?.fusionChangesAvailable && !fusionStatus.wgChangesAvailable && <div className="cad-confirm-actions">
            <button disabled={!canRequestFusionReturn || cadCoordinator.pullingFromFusion} onClick={() => void bringFromFusion()}>{cadCoordinator.pullingFromFusion ? 'Waiting for Fusion…' : 'Bring in'}</button>
            <button className="primary" disabled={!canRequestFusionReturn || cadCoordinator.pullingFromFusion} title="Bring the current Fusion geometry into WG, prepare it, and start the solve." onClick={() => { void cadCoordinator.pullAndSolve(); }}>{cadCoordinator.pullingFromFusion ? 'Waiting for Fusion…' : 'Bring in & solve'}</button>
          </div>}
          {!onshape && workflow.action === 'update' && <div className={fusionBothChanged ? 'cad-confirm-actions' : undefined}>
            {fusionBothChanged && <button disabled={!canRequestFusionReturn || cadCoordinator.pullingFromFusion} title="Keep the Fusion edits: bring the Fusion geometry into WG instead of overwriting it." onClick={() => void bringFromFusion()}>{cadCoordinator.pullingFromFusion ? 'Waiting for Fusion…' : 'Bring Fusion changes in'}</button>}
            <button className="primary cad-primary-action" disabled={sendingToFusion} onClick={sendToFusion}>{sendingToFusion ? 'Sending…' : 'Send WG changes to Fusion'}</button>
          </div>}
          {/* Onshape: the same card carries its send/return pair. */}
          {onshape && workflow.state !== 'not-configured' && !confirmPublicDocument && workflow.action && <button className="primary cad-primary-action" disabled={sendingToOnshape} onClick={() => void sendToOnshape()}>{sendingToOnshape ? (workflow.action === 'update' ? 'Updating…' : 'Creating…') : onshapeActionLabel}</button>}
          {onshape && linkedDocument && workflow.state !== 'not-configured' && <button className="cad-secondary-action" disabled={ingesting} onClick={() => { void cadCoordinator.returnFromOnshape().catch(() => undefined); }}>{ingesting ? 'Returning & preparing…' : 'Bring Onshape geometry into WG'}</button>}
          {onshape && linkedDocument?.documentUrl && <a className="link-button cad-onshape-open" href={linkedDocument.documentUrl} target="_blank" rel="noreferrer noopener">Open {linkedDocument.documentName} in Onshape</a>}
        </>}
      {onshape && publicOnly && !confirmPublicDocument && <div className="cad-alert cad-alert-notice" role="status"><b>This Onshape plan creates public documents.</b> {onshapeConnection?.plan?.name ?? 'The Free plan'} makes every document world-readable — anyone with the link can view this waveguide. Confidential designs belong in Fusion 360 or on a paid Onshape plan.</div>}
      {onshape && onshapeConnection?.insecureKeyFile && <div className="cad-alert cad-alert-error" role="alert">The Onshape key file at {onshapeConnection.credentialsPath} is readable by other accounts on this machine. Restrict it with <code>chmod 600</code>.</div>}
      {onshape && confirmPublicDocument && <div className="cad-direction-alert" role="alert"><div><b>This document will be public</b><span>{confirmPublicDocument}</span></div><div className="cad-confirm-actions"><button onClick={() => setConfirmPublicDocument(null)}>Cancel</button><button className="primary" disabled={sendingToOnshape} onClick={() => void sendToOnshape(true)}>Continue: create a public document</button></div></div>}
    </section>

    {/* 3 · Model: is the geometry sound? */}
    <section className="cad-workflow cad-model-card">
      <header className="cad-model-head">
        <span className="cad-card-label">Model</span>
        <span className="spacer"/>
        <button
          className="cad-icon-button"
          disabled={loading || ingesting}
          title="Refresh the CAD return listing"
          aria-label="Refresh CAD returns"
          onClick={() => void cadCoordinator.refresh()}
        ><Icon name="reset"/></button>
      </header>
      {!bundle && !record && <div className="empty-state">
        <b>No CAD model yet.</b>
        <span>{onshape
          ? 'Send this design to Onshape, then bring its geometry back here.'
          : 'Send a design from Fusion 360 and it will appear here.'}</span>
      </div>}
      {bundle && <div className="cad-model-identity">
        <b className="cad-model-name" title={bundle.documentName ?? bundle.name}>{returnDisplayName(bundle)}</b>
        {record && <span
          className={`cad-state-chip${freshnessSummary(record) === 'current' ? '' : ' warn'}`}
          title={FRESHNESS_COPY[freshnessSummary(record).replaceAll(' ', '_')] ?? FRESHNESS_COPY.unknown}
        >{freshnessSummary(record)}</span>}
        <time dateTime={record?.created_at || bundle.modifiedAt} title={fullTime(record?.created_at || bundle.modifiedAt)}>{relativeTime(record?.created_at || bundle.modifiedAt)}</time>
      </div>}
      {metalUnavailable && <div className="cad-alert cad-alert-notice cad-solver-unavailable" role="status">
        <b>Imported CAD geometry cannot be solved on this machine.</b> Solving an
        ingested model needs the Metal backend, which is macOS-only; this host has
        no Metal engine available. The round trip still works for building and
        exporting geometry, and the parametric workspace still solves here.
      </div>}
      {state.ingestStaleReason && <div className="cad-alert cad-alert-notice" role="status">{state.ingestStaleReason} Prepare the model again before solving.</div>}
      {viewportNotice && <div className="cad-alert cad-alert-notice" role="status">{viewportNotice}</div>}
      {/* Beside the button that starts the solve, not only in the rail where
          the drivers are set: this is what the run will and will not produce,
          and it was previously only discoverable from the empty chart. */}
      {record && !staleModel && importedSubmissionNotices(state).map((notice) => (
        <div className="cad-alert cad-alert-notice" role="status" key={notice}>{notice}</div>
      ))}
      {ingestError && <div className="cad-alert cad-alert-error" role="alert">Preparation failed — {ingestError}</div>}
      {bundle?.readable && (ingesting || !record || staleModel || ingestError) && <button
        className="primary cad-primary-action"
        disabled={ingesting}
        title="Mesh the returned geometry, verify its evidence, and make it the solve truth."
        onClick={() => { void cadCoordinator.ingest(); }}
      >{ingesting ? 'Preparing…' : record ? 'Prepare again' : 'Prepare simulation'}</button>}
      {record && !staleModel && !ingesting && <div className="cad-prepared-line">
        <span className="cad-check-glyph ok" aria-hidden="true">✓</span>
        <span>Prepared for simulation</span>
        <button
          className="link-button"
          title="Drivers, crossover, sweep, directivity, solve options, and mesh detail live in the Simulation tab."
          onClick={() => workspaceNavigation.activate('simulation')}
        >Open Simulation</button>
      </div>}
      {record && <ChecksSection record={record}/>}
      {parked && <div className="cad-direction-alert cad-parked-command" role="status">
        <div><b>Fusion asked for a solve</b><span>Waiting on: {parked.blockers.join(' · ')}</span></div>
        <div className="cad-confirm-actions">
          <button title="Refuse the request for good; Fusion will not offer it again." onClick={() => void cadCoordinator.dismissSolveCommand().catch(() => undefined)}>Dismiss</button>
          <button className="primary" onClick={() => { void cadCoordinator.solveParkedCommand().catch(() => undefined); }}>Solve now</button>
        </div>
      </div>}
      <ModelVersions
        projectBundles={projectBundles}
        unlinkedReturns={unlinkedReturns}
        otherProjectReturns={otherProjectReturns}
        selectedPath={bundle?.bundlePath ?? null}
        select={cadCoordinator.selectBundle}
      />
    </section>

    {/* 4 · Runs: what have I solved? */}
    <CadProjectHistory/>
  </div>;
}
