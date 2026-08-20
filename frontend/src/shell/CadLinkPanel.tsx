import { useEffect, useId, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import type { CadReturnBundle, CadReturnFinding, CadReturnIngestRecord } from '../api/cadlink';
import { OnshapePublicConsentRequired, sendDesignToOnshape } from '../api/onshape';
import { usePreferences } from '../prefs/preferences';
import {
  blockingFindings,
  unacknowledgedBlocking,
  useCadReturnStore,
} from '../stores/cadReturn';
import { parkedSolveCommandStore } from '../stores/solveCommand';
import { recordCommittedAthPolars, useDesignStore } from '../stores/design';
import { polarConfigFromUi, useSolveOptionsStore } from '../stores/solveOptions';
import { useDocumentStore } from '../stores/document';
import { designNameSlug, UNTITLED_SLUG } from '../stores/designName';
import { useCapabilities } from '../jobs/useCapabilities';
import { cadLinkCoordinatorBridge, returnBelongsToAnotherProject } from './CadLinkCoordinator';
import { fusionWorkflowView, onshapeWorkflowView, type CadWorkflowView } from './cadWorkflowView';
import { Icon } from './icons';
import { requestSettings } from './settingsNavigation';
import './cadLinkPanel.css';

// Kept as public panel helpers for existing callers; implementation lives next
// to the two solve entry points so the global command can share it.
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

function pluralized(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

function relativeTime(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return 'time unavailable';
  const elapsedSeconds = Math.max(0, Math.round((Date.now() - timestamp) / 1_000));
  if (elapsedSeconds < 60) return 'just now';
  const elapsedMinutes = Math.round(elapsedSeconds / 60);
  if (elapsedMinutes < 60) return `${elapsedMinutes} min ago`;
  const elapsedHours = Math.round(elapsedMinutes / 60);
  if (elapsedHours < 24) return `${elapsedHours} hr ago`;
  const elapsedDays = Math.round(elapsedHours / 24);
  return `${elapsedDays} ${elapsedDays === 1 ? 'day' : 'days'} ago`;
}

function fullTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Time unavailable' : date.toLocaleString();
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

interface CadDrawerProps {
  title: string;
  chip?: string;
  defaultOpen?: boolean;
  warning?: boolean;
  className?: string;
  children: ReactNode;
}

/** CAD diagnostics use the same heading/button/chevron disclosure pattern as
 * the parameter rail, but keep their own compact card surface and state chip. */
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

function RecordSummary({ record }: { record: CadReturnIngestRecord }) {
  const scopeFindings = record.findings.filter((finding) => finding.kind === 'scope-degradation');
  const planes = Object.entries(record.symmetry.planes ?? {});
  const freshnessState = record.freshness.verdict === 'unlinked'
    ? 'unlinked'
    : record.freshness.instances.length === 0
      ? 'unknown'
      : record.freshness.instances.every((instance) => instance.verdict === 'current')
        ? 'current'
        : record.freshness.instances.every((instance) => instance.verdict === record.freshness.instances[0]?.verdict)
          ? record.freshness.instances[0].verdict.replaceAll('_', ' ')
          : 'mixed';
  const rejectedPlanes = planes.filter(([, verdict]) => !verdict.accepted).length;
  const appliedCuts = record.symmetry.cut_planes ?? [];
  const cadDomain = appliedCuts.length >= 2
    ? 'quarter domain'
    : appliedCuts.length === 1 ? 'half domain' : 'full domain';
  const symmetryState = planes.length === 0
    ? 'not recorded'
    : rejectedPlanes === 0 ? 'accepted' : pluralized(rejectedPlanes, 'rejected plane');
  const polarAxes = Object.values((record.polar_grid_derivation.axes ?? {}) as Record<string, { symmetry_accepted?: boolean }>);
  const widenedAxes = polarAxes.filter((axis) => axis.symmetry_accepted === false).length;
  const polarState = widenedAxes ? pluralized(widenedAxes, 'widened axis', 'widened axes') : 'accepted';
  return <div className="cad-details">
    <CadDrawer key={`${record.ingest_id}-scope`} title="Scope" chip={record.scope.status} defaultOpen={record.scope.status !== 'clean'} warning={record.scope.status !== 'clean'} className="cad-scope">
      <p>{record.scope.status === 'clean'
        ? 'The exported exterior scope was complete.'
        : `${record.scope.degraded_skip_count} exported object${record.scope.degraded_skip_count === 1 ? ' was' : 's were'} skipped. The solve is degraded.`}</p>
      {(record.scope.included ?? []).map((item, index) => <p key={`${String(item.object_id)}-${index}`} className="cad-detail"><b>Included · {String(item.name ?? item.object_id ?? 'Unnamed object')}</b>{item.body_kind ? ` — ${item.body_kind}` : ''}</p>)}
      {(record.scope.skipped ?? []).map((item, index) => <p key={`${String(item.object_id)}-${index}`} className={`cad-detail scope-skip-${item.severity ?? 'info'}`}><b>Skipped · {String(item.name ?? item.object_id ?? item.kind ?? 'Unnamed object')}</b> — {String(item.reason ?? item.kind ?? 'not included')} · {String(item.severity ?? 'info')}</p>)}
      {(record.scope.skipped?.length ?? 0) === 0 && scopeFindings.map((finding) => <p key={finding.id} className="cad-detail"><b>{String(finding.object_id ?? 'Unnamed object')}</b> — {findingDetail(finding)}</p>)}
    </CadDrawer>
    <CadDrawer key={`${record.ingest_id}-freshness`} title="Freshness" chip={freshnessState} defaultOpen={freshnessState !== 'current'} warning={freshnessState !== 'current' && freshnessState !== 'unlinked'}>
      {record.freshness.verdict === 'unlinked'
        ? <p className="cad-verdict neutral">{FRESHNESS_COPY.unlinked}</p>
        : record.freshness.instances.map((instance) => <div className={`cad-verdict ${instance.verdict === 'current' ? 'ok' : 'warn'}`} key={instance.instance_id}>
            <b>{instance.instance_id}</b><span>{FRESHNESS_COPY[instance.verdict] ?? instance.verdict}</span>
            {instance.error && <small>{instance.error}</small>}
          </div>)}
    </CadDrawer>
    <CadDrawer key={`${record.ingest_id}-symmetry`} title="Symmetry" chip={symmetryState} defaultOpen={symmetryState !== 'accepted'} warning={symmetryState !== 'accepted'}>
      <p className="cad-detail cad-symmetry-context"><b>CAD prepared as {cadDomain}.</b> This is resolved independently from Parametric mode: WG re-tests the returned STEP after Fusion edits, bodies and source tags are applied. A rejected plane below means the CAD solve keeps the larger safe domain instead of inheriting the parametric reduction.</p>
      {planes.length ? planes.map(([name, verdict]) => {
        const residual = verdict.max_residual_step_units ?? verdict.residuals;
        const offModel = verdict.worst_off_model_distance_step_units;
        const details = [
          verdict.reason ? String(verdict.reason) : null,
          residual === undefined ? null : `max residual ${compactValue(residual)} STEP units`,
          offModel === undefined ? null : `worst off-model ${compactValue(offModel)} STEP units`,
        ].filter(Boolean).join(' · ');
        return <div className="cad-row" key={name}><b>{name}</b><span className={verdict.accepted ? 'ok-text' : 'warn-text'}>{verdict.accepted ? 'accepted' : 'rejected'}</span><small>{details}</small></div>;
      }) : <p>No coordinate plane verdicts were recorded.</p>}
      {appliedCuts.length > 0 && <p className="cad-detail">Applied cuts: {appliedCuts.join(', ')}</p>}
    </CadDrawer>
    <CadDrawer key={`${record.ingest_id}-healing`} title="Healing performed" chip={record.healing.performed ? record.healing.mode ?? 'healed' : 'clean'} defaultOpen={Boolean(record.healing.performed)} warning={Boolean(record.healing.performed)}>
      {record.healing.performed
        ? <><p>The exported CAD did not mesh unchanged. OCC healing was used; re-export stitched or imprinted CAD when possible.</p>
          {'original_mesh_error' in record.healing && <p className="cad-detail">{compactValue(record.healing.original_mesh_error)}</p>}</>
        : <p>No CAD healing was needed.</p>}
    </CadDrawer>
    <CadDrawer key={`${record.ingest_id}-sizing`} title="Sizing & cost" chip="current" className="cad-pairs">
      {Object.entries(record.sizing_estimate).map(([key, value]) => <div className="cad-row" key={key}><b>{key.replaceAll('_', ' ')}</b><span>{compactValue(value)}</span></div>)}
    </CadDrawer>
    <CadDrawer key={`${record.ingest_id}-polar`} title="Polar derivation" chip={polarState} defaultOpen={polarState !== 'accepted'} warning={polarState !== 'accepted'} className="cad-pairs">
      {Object.entries(record.polar_grid_derivation).map(([key, value]) => <div className="cad-row" key={key}><b>{key.replaceAll('_', ' ')}</b><span>{compactValue(value)}</span></div>)}
    </CadDrawer>
  </div>;
}

interface FindingsProps {
  record: CadReturnIngestRecord;
  acknowledgedFindingIds: string[];
  acknowledge: (findingId: string, value: boolean) => void;
  acknowledgeAllBlocking: () => void;
}

function FindingRows({ record, acknowledgedFindingIds, acknowledge }: Omit<FindingsProps, 'acknowledgeAllBlocking'>) {
  if (record.findings.length === 0) return <p>No findings. This ingestion needs no acknowledgements.</p>;
  return <>{record.findings.map((finding) => <label key={finding.id} className={finding.blocking ? 'blocking' : ''}>
    {finding.blocking && <input type="checkbox" checked={acknowledgedFindingIds.includes(finding.id)} onChange={(event) => acknowledge(finding.id, event.target.checked)}/>}
    <span><b>{finding.kind.replaceAll('-', ' ')}{finding.blocking && <small className="cad-blocking-suffix"> · blocking</small>}</b><small>{findingDetail(finding)}</small></span>
  </label>)}</>;
}

function Findings({ record, acknowledgedFindingIds, acknowledge, acknowledgeAllBlocking }: FindingsProps) {
  const blocking = blockingFindings(record);
  const unacknowledged = blocking.filter((finding) => !acknowledgedFindingIds.includes(finding.id));
  if (unacknowledged.length === 0) {
    const chip = blocking.length
      ? `${blocking.length} acknowledged`
      : record.findings.length ? `${record.findings.length} informational` : 'clean';
    return <CadDrawer key={`${record.ingest_id}-findings`} title="Findings" chip={chip} className="cad-findings">
      <FindingRows record={record} acknowledgedFindingIds={acknowledgedFindingIds} acknowledge={acknowledge}/>
    </CadDrawer>;
  }
  return <section className="cad-section cad-findings degraded">
    <header><h4>Findings</h4>{blocking.length > 1 && <button onClick={acknowledgeAllBlocking}>Acknowledge all {blocking.length}</button>}</header>
    <FindingRows record={record} acknowledgedFindingIds={acknowledgedFindingIds} acknowledge={acknowledge}/>
  </section>;
}

function returnStatus(record: CadReturnIngestRecord | null, acknowledgedFindingIds: string[], ingesting: boolean, ingestError: string | null): string {
  if (ingesting) return 'Preparing…';
  if (ingestError) return `Preparation failed — ${ingestError}`;
  if (!record) return 'Ready to prepare';
  const unacknowledged = blockingFindings(record).filter((finding) => !acknowledgedFindingIds.includes(finding.id)).length;
  const findings = unacknowledged
    ? `${pluralized(unacknowledged, 'finding')} ${unacknowledged === 1 ? 'needs' : 'need'} review`
    : null;
  const degraded = record.scope.status === 'clean'
    ? null
    : `Degraded: ${pluralized(record.scope.degraded_skip_count, 'object')} skipped`;
  return [degraded, findings].filter(Boolean).join(' · ') || 'Ready to solve';
}

interface CadReturnReviewProps extends Omit<FindingsProps, 'record'> {
  bundle: CadReturnBundle;
  record: CadReturnIngestRecord | null;
  ingesting: boolean;
  ingestError: string | null;
  prepare: () => void;
}

function CadReturnReview({ bundle, record, ingesting, ingestError, prepare, ...findingsProps }: CadReturnReviewProps) {
  const titleId = useId();
  const timestamp = record?.created_at || bundle.modifiedAt;
  return <>
    <article className={`cad-return-summary${record && record.scope.status !== 'clean' ? ' degraded' : ''}`} aria-labelledby={titleId}>
      <div className="cad-return-summary-copy">
        <h4 id={titleId}>{returnDisplayName(bundle)}</h4>
        <time dateTime={timestamp} title={fullTime(timestamp)}>{relativeTime(timestamp)}</time>
        <p>{returnStatus(record, findingsProps.acknowledgedFindingIds, ingesting, ingestError)}</p>
      </div>
      {(ingesting || ingestError || !record) && <button className="primary" disabled={ingesting} onClick={prepare}>{ingesting ? 'Preparing…' : 'Prepare simulation'}</button>}
    </article>
    {record && <><RecordSummary record={record}/><Findings record={record} {...findingsProps}/></>}
  </>;
}

interface CadHistoryProps {
  bundles: CadReturnBundle[];
  selectedPath: string | null;
  select: (bundle: CadReturnBundle) => void;
}

function CadHistory({ bundles, selectedPath, select }: CadHistoryProps) {
  return <CadDrawer title={`History (${bundles.length})`} className="cad-history">
    <div className="cad-bundle-list" role="listbox" aria-label="CAD return history">
      {bundles.map((bundle) => <button
        type="button"
        key={bundle.bundlePath}
        role="option"
        aria-selected={selectedPath === bundle.bundlePath}
        disabled={!bundle.readable}
        onClick={() => select(bundle)}
        title={!bundle.readable ? bundle.reason ?? 'Manifest is unreadable' : undefined}
      ><b>{returnDisplayName(bundle)}</b><span>{bundleInventory(bundle)}</span><time dateTime={bundle.modifiedAt} title={fullTime(bundle.modifiedAt)}>{relativeTime(bundle.modifiedAt)}</time></button>)}
    </div>
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
  const [requestingReturn, setRequestingReturn] = useState(false);
  const [confirmPublicDocument, setConfirmPublicDocument] = useState<string | null>(null);
  const [sendingToOnshape, setSendingToOnshape] = useState(false);
  const onshapeSendGeneration = useRef(0);
  const onshape = preferences.cadApplication === 'onshape';
  const {
    bundles,
    loading,
    ingesting,
    ingestError,
    error,
    status,
    viewportNotice,
    fusionStatus,
    onshapeStatus,
    onshapeConnection,
  } = cadCoordinator;
  const projectBundles = identity?.designId
    ? bundles.filter((bundle) => !returnBelongsToAnotherProject(bundle, identity.designId))
    : bundles;
  const otherProjectReturns = bundles.length - projectBundles.length;

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

  // The Onshape outbound leg. Fusion's outbound action left this panel: the
  // design menu and the Geometry rail call the coordinator's unified path.
  const send = async () => { await sendToOnshape(); };

  const bringFromFusion = async () => {
    setRequestingReturn(true); cadCoordinator.clearFeedback();
    try {
      // The arrival itself is awaited by the coordinator's correlated waiter,
      // which also owns the timeout message; this button only starts the pull.
      void cadCoordinator.pullFromFusion().catch(() => undefined);
    } catch (reason) {
      cadCoordinator.reportError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRequestingReturn(false);
    }
  };

  const workflow = onshape ? onshapeWorkflowView(onshapeStatus) : fusionWorkflowView(fusionStatus);
  const settingsLabel = onshape ? 'Onshape · Change' : 'Fusion 360 · Change';
  // Imported geometry is solved on Metal or not at all: runtime.py rewrites
  // AUTO to metal and refuses bempp outright. Without Metal the whole round
  // trip still works as a CAD workflow and only the solve is out of reach, so
  // this states the boundary up front instead of hiding the panel or letting
  // someone discover it after exporting, ingesting and acknowledging findings.
  const { engines: solverEngines, isLoading: capabilitiesLoading } = useCapabilities();
  const metalUnavailable = !capabilitiesLoading
    && !solverEngines.some((engine) => engine.name.toLowerCase() === 'metal' && engine.available);
  const designName = designNameSlug(documentName);
  const shownName = designName === UNTITLED_SLUG ? 'this design' : designName;
  const actionLabel = workflow.action === 'update' ? 'Send WG changes to Onshape' : `Create ${shownName} in Onshape`;
  const busy = sendingToOnshape;
  const canRequestFusionReturn = Boolean(
    fusionStatus?.running && fusionStatus.documentName && fusionStatus.documentId
    && fusionStatus.link && identity?.designId,
  );
  // A Fusion solve request that stopped at a gate is held, not discarded, so it
  // needs somewhere to be seen and acted on. Acknowledging is offered inline
  // when it is the whole of what the request is waiting for.
  const unacknowledged = unacknowledgedBlocking(state);
  const parked = parkedCommand && parkedCommand.blockers.length > 0 ? parkedCommand : null;
  const resumeParked = () => {
    if (unacknowledged.length) state.acknowledgeAllBlocking();
    void cadCoordinator.solveParkedCommand().catch(() => undefined);
  };
  // Onshape's Free plan makes every document world-readable. Say so before the
  // user sends, not after -- and say it from the plan WG actually read.
  const publicOnly = onshapeConnection?.plan?.publicOnly === true;
  const linkedDocument = onshapeStatus?.link ?? null;
  const matchingOnshapeLinks = onshapeStatus?.matchingLinks ?? [];

  return <div className="cadlink-panel panel-scroll">
    <h2 className="sr-only">CAD Link</h2>
    {metalUnavailable && <div className="cad-alert cad-alert-notice cad-solver-unavailable" role="status">
      <b>Imported CAD geometry cannot be solved on this machine.</b> Solving an
      ingested model needs the Metal backend, which is macOS-only; this host has
      no Metal engine available. The round trip below still works for building
      and exporting geometry, and the parametric workspace still solves here.
    </div>}
    {/* Fusion's outbound leg lives in the design menu and the Geometry rail;
        this panel only reports the connection and owns the inbound leg. The
        Onshape workflow keeps its two numbered steps: the panel is its home. */}
    {onshape && <section className="cad-workflow cad-send">
      <header className="cad-workflow-header"><span className="cad-step">1</span><div><h3>WG → CAD</h3><p>Create or update this WG design in Onshape.</p></div><button className="link-button" onClick={() => requestSettings('cad')}>{settingsLabel}</button></header>
      <div className={`cad-connection cad-connection-${workflow.state}`}>
        <span className="cad-connection-dot" aria-hidden="true"/>
        <div><h4>{workflow.headline}</h4><p>{workflow.detail}</p></div>
      </div>
      {matchingOnshapeLinks.length > 1 && <label className="field-row linked-instance-selection">
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
      {linkedDocument?.documentUrl && <a className="cad-secondary-action cad-onshape-open" href={linkedDocument.documentUrl} target="_blank" rel="noreferrer noopener">Open {linkedDocument.documentName} in Onshape</a>}
      {publicOnly && !confirmPublicDocument && <div className="cad-alert cad-alert-notice" role="status"><b>This Onshape plan creates public documents.</b> {onshapeConnection?.plan?.name ?? 'The Free plan'} makes every document world-readable — anyone with the link can view this waveguide. Confidential designs belong in Fusion 360 or on a paid Onshape plan.</div>}
      {onshapeConnection?.insecureKeyFile && <div className="cad-alert cad-alert-error" role="alert">The Onshape key file at {onshapeConnection.credentialsPath} is readable by other accounts on this machine. Restrict it with <code>chmod 600</code>.</div>}
      {workflow.action && !confirmPublicDocument && <button className="primary cad-primary-action" disabled={busy} onClick={() => void send()}>{busy ? (workflow.action === 'update' ? 'Updating…' : 'Creating…') : actionLabel}</button>}
      {confirmPublicDocument && <div className="cad-direction-alert" role="alert"><div><b>This document will be public</b><span>{confirmPublicDocument}</span></div><div className="cad-confirm-actions"><button onClick={() => setConfirmPublicDocument(null)}>Cancel</button><button className="primary" disabled={sendingToOnshape} onClick={() => void sendToOnshape(true)}>Continue: create a public document</button></div></div>}
      {error && <div className="cad-alert cad-alert-error" role="alert">{error}</div>}
      {status && <div className="cad-status-strip" role="status">{status}</div>}
    </section>}
    {!onshape && <section className="cad-workflow cad-send">
      <header className="cad-workflow-header no-step"><div><h3>Fusion connection</h3><p>Sends live in the design menu and the Geometry rail.</p></div><button className="link-button" onClick={() => requestSettings('cad')}>{settingsLabel}</button></header>
      <div className={`cad-connection cad-connection-${workflow.state}`}>
        <span className="cad-connection-dot" aria-hidden="true"/>
        <div><h4>{workflow.headline}</h4><p>{workflow.detail}</p></div>
      </div>
      {workflow.state === 'not-configured' && <button className="primary cad-primary-action" onClick={() => requestSettings('cad')}>Set up Fusion connection</button>}
    </section>}
    {onshape && <section className="cad-workflow cad-return-workflow">
      <header className="cad-workflow-header"><span className="cad-step">2</span><div><h3>CAD → SIMULATION</h3><p>Bring CAD geometry and source tags into WG.</p></div></header>
      {linkedDocument ? <div className="cad-return-quick-action">
        <div><b>{linkedDocument.documentName}</b><span>Export the current linked Part Studio to STEP, verify its source evidence, and prepare it for the viewport and solver.</span></div>
        <button className="primary" disabled={ingesting} onClick={() => { void cadCoordinator.returnFromOnshape().catch(() => undefined); }}>{ingesting ? 'Returning & preparing…' : 'Bring Onshape geometry into WG'}</button>
      </div> : <div className="empty-state"><b>No linked Onshape Part Studio</b><span>Send this WG design to Onshape first, then return its current geometry here.</span></div>}
      {state.ingestStaleReason && <div className="cad-alert cad-alert-notice" role="status">{state.ingestStaleReason} Return the current Onshape geometry again before solving.</div>}
      {viewportNotice && <div className="cad-alert cad-alert-notice" role="status">{viewportNotice}</div>}
      {state.selectedBundle?.readable && <CadReturnReview
        bundle={state.selectedBundle}
        record={state.ingestRecord}
        ingesting={ingesting}
        ingestError={ingestError}
        prepare={() => { void cadCoordinator.ingest(); }}
        acknowledgedFindingIds={state.acknowledgedFindingIds}
        acknowledge={state.acknowledge}
        acknowledgeAllBlocking={state.acknowledgeAllBlocking}
      />}
      {projectBundles.length > 0 && <CadHistory bundles={projectBundles} selectedPath={state.selectedBundle?.bundlePath ?? null} select={cadCoordinator.selectBundle}/>}
    </section>}
    {!onshape && <section className="cad-workflow cad-return-workflow">
    <header className="cad-workflow-header no-step"><div><h3>FUSION → SIMULATION</h3><p>Bring Fusion geometry and source tags into WG.</p></div><button disabled={loading || ingesting} onClick={() => void cadCoordinator.refresh()}><Icon name="reset"/>{loading ? 'Loading…' : 'Refresh'}</button></header>
    {fusionStatus?.fusionChangesAvailable && <div className="cad-direction-alert"><div><b>Fusion geometry has changed</b><span>The active Fusion body or source setup differs from the last design returned to WG.</span></div><div className="cad-confirm-actions"><button disabled={!canRequestFusionReturn || requestingReturn} onClick={() => void bringFromFusion()}>{requestingReturn ? 'Requesting…' : 'Bring changes into WG'}</button><button className="primary" disabled={!canRequestFusionReturn || requestingReturn} onClick={() => { void cadCoordinator.pullAndSolve(); }}>Bring changes in & solve</button></div></div>}
    {!fusionStatus?.fusionChangesAvailable && canRequestFusionReturn && <button className="cad-secondary-action" disabled={requestingReturn} onClick={() => void bringFromFusion()}>{requestingReturn ? 'Requesting…' : 'Refresh geometry from Fusion'}</button>}
    {parked && <div className="cad-direction-alert cad-parked-command" role="status">
      <div><b>Fusion asked for a solve</b><span>Waiting on: {parked.blockers.join(' · ')}</span></div>
      <div className="cad-confirm-actions">
        <button onClick={() => void cadCoordinator.dismissSolveCommand().catch(() => undefined)}>Dismiss</button>
        <button className="primary" onClick={resumeParked}>{unacknowledged.length
          ? `Acknowledge all ${unacknowledged.length} & solve`
          : 'Solve now'}</button>
      </div>
    </div>}
    {error && <div className="cad-alert cad-alert-error" role="alert">{error}</div>}
    {state.ingestStaleReason && <div className="cad-alert cad-alert-notice" role="status">{state.ingestStaleReason} Re-ingest before solving.</div>}
    {viewportNotice && <div className="cad-alert cad-alert-notice" role="status">{viewportNotice}</div>}
    {status && <div className="cad-status-strip" role="status">{status}</div>}
    {otherProjectReturns > 0 && <div className="cad-alert cad-alert-notice" role="status">{pluralized(otherProjectReturns, 'return')} hidden because {otherProjectReturns === 1 ? 'it belongs' : 'they belong'} to another CAD-linked project. Open that project from File → CAD-linked designs to use {otherProjectReturns === 1 ? 'it' : 'them'}.</div>}
    {!loading && workflow.state !== 'not-configured' && !error && projectBundles.length === 0 && otherProjectReturns === 0 && <div className="empty-state"><b>No CAD returns yet.</b><span>Send a design from Fusion 360 and it will appear here.</span></div>}
    {state.selectedBundle?.readable && <CadReturnReview
      bundle={state.selectedBundle}
      record={state.ingestRecord}
      ingesting={ingesting}
      ingestError={ingestError}
      prepare={() => { void cadCoordinator.ingest(); }}
      acknowledgedFindingIds={state.acknowledgedFindingIds}
      acknowledge={state.acknowledge}
      acknowledgeAllBlocking={state.acknowledgeAllBlocking}
    />}
    {projectBundles.length > 0 && <CadHistory bundles={projectBundles} selectedPath={state.selectedBundle?.bundlePath ?? null} select={cadCoordinator.selectBundle}/>}
    </section>}
  </div>;
}
