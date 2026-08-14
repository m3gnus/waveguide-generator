import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import type { CadReturnFinding, CadReturnIngestRecord } from '../api/cadlink';
import { OnshapePublicConsentRequired, sendDesignToOnshape } from '../api/onshape';
import { usePreferences } from '../prefs/preferences';
import {
  blockingFindings,
  useCadReturnStore,
} from '../stores/cadReturn';
import { useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { filenameStem } from '../viewport/presentation';
import { cadLinkCoordinatorBridge } from './CadLinkCoordinator';
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
  unlinked: 'Unlinked return — no Waveguide Generator design identity was attached in CAD.',
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

function RecordSummary({ record }: { record: CadReturnIngestRecord }) {
  const scopeFindings = record.findings.filter((finding) => finding.kind === 'scope-degradation');
  const planes = Object.entries(record.symmetry.planes ?? {});
  return <>
    <section className={`cad-section cad-scope ${record.scope.status === 'clean' ? '' : 'degraded'}`}>
      <header><h3>Scope</h3><span className="cad-status">{record.scope.status}</span></header>
      <p>{record.scope.status === 'clean'
        ? 'The exported exterior scope was complete.'
        : `${record.scope.degraded_skip_count} exported object${record.scope.degraded_skip_count === 1 ? ' was' : 's were'} skipped. The solve is degraded.`}</p>
      {(record.scope.included ?? []).map((item, index) => <p key={`${String(item.object_id)}-${index}`} className="cad-detail"><b>Included · {String(item.name ?? item.object_id ?? 'Unnamed object')}</b>{item.body_kind ? ` — ${item.body_kind}` : ''}</p>)}
      {(record.scope.skipped ?? []).map((item, index) => <p key={`${String(item.object_id)}-${index}`} className={`cad-detail scope-skip-${item.severity ?? 'info'}`}><b>Skipped · {String(item.name ?? item.object_id ?? item.kind ?? 'Unnamed object')}</b> — {String(item.reason ?? item.kind ?? 'not included')} · {String(item.severity ?? 'info')}</p>)}
      {(record.scope.skipped?.length ?? 0) === 0 && scopeFindings.map((finding) => <p key={finding.id} className="cad-detail"><b>{String(finding.object_id ?? 'Unnamed object')}</b> — {findingDetail(finding)}</p>)}
    </section>
    <section className="cad-section">
      <header><h3>Freshness</h3></header>
      {record.freshness.verdict === 'unlinked'
        ? <p className="cad-verdict warn">{FRESHNESS_COPY.unlinked}</p>
        : record.freshness.instances.map((instance) => <div className={`cad-verdict ${instance.verdict === 'current' ? 'ok' : 'warn'}`} key={instance.instance_id}>
            <b>{instance.instance_id}</b><span>{FRESHNESS_COPY[instance.verdict] ?? instance.verdict}</span>
            {instance.error && <small>{instance.error}</small>}
          </div>)}
    </section>
    <section className="cad-section">
      <header><h3>Symmetry</h3></header>
      {planes.length ? planes.map(([name, verdict]) => {
        const residual = verdict.max_residual_step_units ?? verdict.residuals;
        const offModel = verdict.worst_off_model_distance_step_units;
        const details = [
          verdict.reason ? String(verdict.reason) : null,
          residual === undefined ? null : `max residual ${compactValue(residual)} mm`,
          offModel === undefined ? null : `worst off-model ${compactValue(offModel)} mm`,
        ].filter(Boolean).join(' · ');
        return <div className="cad-row" key={name}><b>{name}</b><span className={verdict.accepted ? 'ok-text' : 'warn-text'}>{verdict.accepted ? 'accepted' : 'rejected'}</span><small>{details}</small></div>;
      }) : <p>No coordinate plane verdicts were recorded.</p>}
      {(record.symmetry.cut_planes?.length ?? 0) > 0 && <p className="cad-detail">Applied cuts: {record.symmetry.cut_planes?.join(', ')}</p>}
    </section>
    {record.healing.performed && <section className="cad-section degraded">
      <header><h3>Healing performed</h3><span className="cad-status">{record.healing.mode ?? 'healed'}</span></header>
      <p>The exported CAD did not mesh unchanged. OCC healing was used; re-export stitched or imprinted CAD when possible.</p>
      {'original_mesh_error' in record.healing && <p className="cad-detail">{compactValue(record.healing.original_mesh_error)}</p>}
    </section>}
    <section className="cad-section cad-pairs">
      <header><h3>Sizing & cost</h3></header>
      {Object.entries(record.sizing_estimate).map(([key, value]) => <div className="cad-row" key={key}><b>{key.replaceAll('_', ' ')}</b><span>{compactValue(value)}</span></div>)}
    </section>
    <section className="cad-section cad-pairs">
      <header><h3>Polar derivation</h3></header>
      {Object.entries(record.polar_grid_derivation).map(([key, value]) => <div className="cad-row" key={key}><b>{key.replaceAll('_', ' ')}</b><span>{compactValue(value)}</span></div>)}
    </section>
  </>;
}

export function CadLinkPanel() {
  const state = useCadReturnStore();
  const preferences = usePreferences();
  const design = useDesignStore((current) => current.design);
  const designRevision = useDesignStore((current) => current.designRevision);
  const filename = useDocumentStore((current) => current.filename);
  const identity = useDocumentStore((current) => current.identity);
  const setCadLink = useDocumentStore((current) => current.setCadLink);
  const cadCoordinator = useSyncExternalStore(cadLinkCoordinatorBridge.subscribe, cadLinkCoordinatorBridge.getSnapshot, cadLinkCoordinatorBridge.getSnapshot);
  const [requestingReturn, setRequestingReturn] = useState(false);
  const [confirmPublicDocument, setConfirmPublicDocument] = useState<string | null>(null);
  const [sendingToOnshape, setSendingToOnshape] = useState(false);
  const onshapeSendGeneration = useRef(0);
  const onshape = preferences.cadApplication === 'onshape';
  const {
    bundles,
    loading,
    ingesting,
    error,
    status,
    viewportNotice,
    fusionStatus,
    onshapeStatus,
    onshapeConnection,
  } = cadCoordinator;

  useEffect(() => () => { onshapeSendGeneration.current += 1; }, []);

  // The outbound leg for Onshape. There is no local client to notify and no
  // workspace folder to write into: WG uploads the bundle over HTTPS itself.
  const sendToOnshape = async (allowPublic = false) => {
    const request = ++onshapeSendGeneration.current;
    const sourceRevision = designRevision;
    cadCoordinator.clearFeedback(); setSendingToOnshape(true);
    try {
      const wasLinked = onshapeStatus?.state === 'stale' || onshapeStatus?.state === 'current';
      const result = await sendDesignToOnshape(
        design, designRevision, filenameStem(filename), identity, { allowPublic },
      );
      if (request !== onshapeSendGeneration.current) return;
      setConfirmPublicDocument(null);
      if (useDesignStore.getState().designRevision !== sourceRevision) {
        cadCoordinator.reportStatus('Sent the previous design to Onshape, but the WG design changed while it was uploading. Send again to link the current design.');
        return;
      }
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
  const cadApplicationLabel = onshape ? 'Onshape' : 'Autodesk Fusion 360';
  const designName = filenameStem(filename);
  const shownName = designName === 'waveguide' ? 'waveguide' : designName;
  const actionLabel = workflow.action === 'update' ? 'Send WG changes to Onshape' : `Create ${shownName} in Onshape`;
  const busy = sendingToOnshape;
  const canRequestFusionReturn = Boolean(
    fusionStatus?.running && fusionStatus.documentName && fusionStatus.documentId
    && fusionStatus.link && identity?.designId,
  );
  // Onshape's Free plan makes every document world-readable. Say so before the
  // user sends, not after -- and say it from the plan WG actually read.
  const publicOnly = onshapeConnection?.plan?.publicOnly === true;
  const linkedDocument = onshapeStatus?.link ?? null;

  return <div className="cadlink-panel panel-scroll">
    {/* Fusion's outbound leg lives in the design menu and the Geometry rail;
        this panel only reports the connection and owns the inbound leg. The
        Onshape workflow keeps its two numbered steps: the panel is its home. */}
    {onshape && <section className="cad-workflow cad-send">
      <header className="cad-workflow-header"><span className="cad-step">1</span><div><h3>WG → CAD</h3><p>Create or update this WG design in Onshape.</p></div><button className="link-button" onClick={() => requestSettings('cad')}>{cadApplicationLabel} · Change</button></header>
      <div className={`cad-connection cad-connection-${workflow.state}`}>
        <span className="cad-connection-dot" aria-hidden="true"/>
        <div><h3>{workflow.headline}</h3><p>{workflow.detail}</p></div>
      </div>
      {linkedDocument?.documentUrl && <a className="cad-secondary-action cad-onshape-open" href={linkedDocument.documentUrl} target="_blank" rel="noreferrer noopener">Open {linkedDocument.documentName} in Onshape</a>}
      {publicOnly && !confirmPublicDocument && <div className="cad-alert" role="status"><b>This Onshape plan creates public documents.</b> {onshapeConnection?.plan?.name ?? 'The Free plan'} makes every document world-readable — anyone with the link can view this waveguide. Confidential designs belong in Fusion 360 or on a paid Onshape plan.</div>}
      {onshapeConnection?.insecureKeyFile && <div className="cad-alert" role="alert">The Onshape key file at {onshapeConnection.credentialsPath} is readable by other accounts on this machine. Restrict it with <code>chmod 600</code>.</div>}
      {workflow.action && !confirmPublicDocument && <button className="primary cad-primary-action" disabled={busy} onClick={() => void send()}>{busy ? (workflow.action === 'update' ? 'Updating…' : 'Creating…') : actionLabel}</button>}
      {confirmPublicDocument && <div className="cad-direction-alert" role="alert"><div><b>This document will be public</b><span>{confirmPublicDocument}</span></div><div className="cad-confirm-actions"><button onClick={() => setConfirmPublicDocument(null)}>Cancel</button><button className="primary" disabled={sendingToOnshape} onClick={() => void sendToOnshape(true)}>Continue: create a public document</button></div></div>}
      {error && <div className="cad-alert" role="alert">{error}</div>}
      {status && <div className="cad-status-strip" role="status">{status}</div>}
    </section>}
    {!onshape && <section className="cad-workflow cad-send">
      <header className="cad-workflow-header no-step"><div><h3>Fusion connection</h3><p>Sends live in the design menu and the Geometry rail.</p></div><button className="link-button" onClick={() => requestSettings('cad')}>{cadApplicationLabel} · Change</button></header>
      <div className={`cad-connection cad-connection-${workflow.state}`}>
        <span className="cad-connection-dot" aria-hidden="true"/>
        <div><h3>{workflow.headline}</h3><p>{workflow.detail}</p></div>
      </div>
      {workflow.state === 'not-configured' && <button className="primary cad-primary-action" onClick={() => requestSettings('cad')}>Set up Fusion connection</button>}
    </section>}
    {onshape && <section className="cad-workflow cad-return-workflow">
      <header className="cad-workflow-header"><span className="cad-step">2</span><div><h3>CAD → SIMULATION</h3><p>Bring CAD geometry and source tags into WG.</p></div></header>
      {linkedDocument ? <div className="cad-return-quick-action">
        <div><b>{linkedDocument.documentName}</b><span>Export the current linked Part Studio to STEP, verify its source evidence, and prepare it for the viewport and solver.</span></div>
        <button className="primary" disabled={ingesting} onClick={() => { void cadCoordinator.returnFromOnshape().catch(() => undefined); }}>{ingesting ? 'Returning & preparing…' : 'Bring Onshape geometry into WG'}</button>
      </div> : <div className="empty-state"><b>No linked Onshape Part Studio</b><span>Send this WG design to Onshape first, then return its current geometry here.</span></div>}
      {state.ingestStaleReason && <div className="cad-alert" role="status">{state.ingestStaleReason} Return the current Onshape geometry again before solving.</div>}
      {viewportNotice && <div className="cad-alert" role="status">{viewportNotice}</div>}
      {state.selectedBundle?.readable && state.ingestRecord && <>
        <RecordSummary record={state.ingestRecord}/>
        <section className="cad-section cad-findings">
          <header><h3>Findings</h3>{blockingFindings(state.ingestRecord).length > 1 && <button onClick={state.acknowledgeAllBlocking}>Acknowledge all {blockingFindings(state.ingestRecord).length}</button>}</header>
          {state.ingestRecord.findings.length === 0 ? <p>No findings. This ingestion needs no acknowledgements.</p> : state.ingestRecord.findings.map((finding) => <label key={finding.id} className={finding.blocking ? 'blocking' : ''}>
            {finding.blocking && <input type="checkbox" checked={state.acknowledgedFindingIds.includes(finding.id)} onChange={(event) => state.acknowledge(finding.id, event.target.checked)}/>}<span><b>{finding.kind.replaceAll('-', ' ')}</b><small>{findingDetail(finding)}</small></span>
          </label>)}
        </section>
      </>}
    </section>}
    {!onshape && <section className="cad-workflow cad-return-workflow">
    <header className="cad-workflow-header no-step"><div><h3>FUSION → SIMULATION</h3><p>Bring Fusion geometry and source tags into WG.</p></div><button disabled={loading || ingesting} onClick={() => void cadCoordinator.refresh()}><Icon name="reset"/>{loading ? 'Loading…' : 'Refresh'}</button></header>
    {fusionStatus?.fusionChangesAvailable && <div className="cad-direction-alert"><div><b>Fusion geometry has changed</b><span>The active Fusion body or source setup differs from the last design returned to WG.</span></div><div className="cad-confirm-actions"><button disabled={!canRequestFusionReturn || requestingReturn} onClick={() => void bringFromFusion()}>{requestingReturn ? 'Requesting…' : 'Bring changes into WG'}</button><button className="primary" disabled={!canRequestFusionReturn || requestingReturn} onClick={() => { void cadCoordinator.pullAndSolve(); }}>Bring changes in & solve</button></div></div>}
    {!fusionStatus?.fusionChangesAvailable && canRequestFusionReturn && <button className="cad-secondary-action" disabled={requestingReturn} onClick={() => void bringFromFusion()}>{requestingReturn ? 'Requesting…' : 'Refresh geometry from Fusion'}</button>}
    {error && <div className="cad-alert" role="alert">{error}</div>}
    {state.ingestStaleReason && <div className="cad-alert" role="status">{state.ingestStaleReason} Re-ingest before solving.</div>}
    {viewportNotice && <div className="cad-alert" role="status">{viewportNotice}</div>}
    {status && <div className="cad-status-strip" role="status">{status}</div>}
    {state.selectedBundle?.readable && <div className="cad-return-quick-action">
      <div><b>{state.selectedBundle.documentName ?? state.selectedBundle.name}</b><span>{state.ingestRecord ? 'Prepared for the CAD workspace and solver.' : 'Prepare this Fusion return for the viewport and solver.'}</span></div>
      {!state.ingestRecord && <button className="primary" disabled={ingesting} onClick={() => void cadCoordinator.ingest()}>{ingesting ? 'Preparing…' : 'Prepare simulation'}</button>}
    </div>}
    {!loading && workflow.state !== 'not-configured' && !error && bundles.length === 0 && <div className="empty-state"><b>No CAD returns</b><span>Returned bundles appear under the selected WGLink folder’s wgreturn folder.</span></div>}
    {bundles.length > 0 && <div className="cad-bundle-list" role="list" aria-label="Designs returned from CAD">{bundles.map((bundle) => <button
      key={bundle.bundlePath}
      role="listitem"
      className={state.selectedBundle?.bundlePath === bundle.bundlePath ? 'selected' : ''}
      disabled={!bundle.readable || ingesting}
      onClick={() => { state.selectBundle(bundle); }}
      title={!bundle.readable ? bundle.reason ?? 'Manifest is unreadable' : undefined}
    ><b>{bundle.documentName ?? bundle.name}</b><span>{bundle.readable ? `${bundle.sourceCount} sources · ${bundle.instanceCount} linked instances` : bundle.reason ?? 'Manifest is unreadable'}</span><time>{new Date(bundle.modifiedAt).toLocaleString()}</time></button>)}</div>}

    {state.selectedBundle?.readable && state.ingestRecord && <>
        <RecordSummary record={state.ingestRecord}/>
        <section className="cad-section cad-findings">
          <header><h3>Findings</h3>{blockingFindings(state.ingestRecord).length > 1 && <button onClick={state.acknowledgeAllBlocking}>Acknowledge all {blockingFindings(state.ingestRecord).length}</button>}</header>
          {state.ingestRecord.findings.length === 0 ? <p>No findings. This ingestion needs no acknowledgements.</p> : state.ingestRecord.findings.map((finding) => <label key={finding.id} className={finding.blocking ? 'blocking' : ''}>
            {finding.blocking && <input type="checkbox" checked={state.acknowledgedFindingIds.includes(finding.id)} onChange={(event) => state.acknowledge(finding.id, event.target.checked)}/>}<span><b>{finding.kind.replaceAll('-', ' ')}</b><small>{findingDetail(finding)}</small></span>
          </label>)}
        </section>
      </>}
    </section>}
  </div>;
}
