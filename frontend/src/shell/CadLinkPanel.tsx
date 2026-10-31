import { useCadModelSelection, useCadModelState } from '../stores/cadModelState';
import { ENGINE_HOLDS } from '../stores/cadModelStatusLine';
import { useCurrentModelStatus } from '../model/currentModel';
import { EmptyState } from './EmptyState';
import { Notice } from './Notice';
import { useCadFeedbackError } from './cadSetupPublisher';
import { nativeCadLimitation } from '../design/nativeFamilies';
import { useCadConnectionStore } from '../stores/cadConnection';
import { ExperimentalBadge } from '../design/ExperimentalBadge';
import { useRef, useState, useSyncExternalStore } from 'react';
import { useEffect } from 'react';
import { installCadLinkAddin } from '../api/cadlink';
import type { CadReturnBundle } from '../api/cadlink';
import { joinPlanes } from '../api/domainDecision';
import { OnshapePublicConsentRequired, sendDesignToOnshape, unlinkOnshape } from '../api/onshape';
import { useDeveloperDetails, usePreferences } from '../prefs/preferences';
import { DeveloperDetails } from './DeveloperDetails';
import { useCadReturnStore } from '../stores/cadReturn';
import { currentDocumentLoad, isCurrentDocumentLoad, recordCommittedAthPolars, useDesignStore } from '../stores/design';
import { keptContentKeyOf, rememberSentCopy } from '../design/replacementCheck';
import { polarConfigFromUi, useSolveOptionsStore } from '../stores/solveOptions';
import { useDocumentStore } from '../stores/document';
import { designNameSlug, UNTITLED_SLUG } from '../stores/designName';
import { cadWorkspace } from '../stores/cadWorkspace';
import { returnBelongsToProject } from './cadlink/arrivals';
import { fusionAddinNeedsInstall, fusionAddinTooOldForUpdate, fusionLinkDisplayName, fusionParameterDriftDetail, fusionWorkflowView, onshapeWorkflowView, type CadWorkflowView } from './cadWorkflowView';
import { Icon } from './icons';
import { fullTime, pluralized, relativeTime } from './cadTime';
import { CadProjectHeader, CadProjectHistory } from './CadProjectPanel';
import { CadOperationsSection, CurrentModelRequests } from './CadOperationsSection';
import { CadDeliveryHealth } from './CadDeliveryHealth';
import { CadModelDetails, FRESHNESS_COPY, freshnessSummary } from './CadModelDetails';
import { CadDrawer } from './CadDrawer';
import { RootFolderControls } from './RootFolderControls';
import { requestSettings } from './settingsNavigation';
import { workspaceNavigation } from './workspaceNavigation';
import { usePanelVisible } from './panelVisibility';
import { cadCoordinationStore } from '../api/cadCoordination';
import './cadLinkPanel.css';

// Kept as public panel helpers for existing callers; implementation lives next
// to the two solve entry points so the global command can share it.
import { importedSubmissionNotices } from '../jobs/importedSubmission';
export { buildImportedSubmission } from '../jobs/importedSubmission';
export { newestReturnArrival } from './cadlink/arrivals';
export { showIngestedMeshInViewport } from './cadlink/artifactLoader';

/** The Fusion link states that describe the active document against the
 * parametric WG design: meaningless while a model authored in Fusion is on
 * screen. The connection problems (offline, outdated, recovery) are not here. */
const DOCUMENT_LINK_STATES: ReadonlySet<string> = new Set([
  'not-linked', 'no-document', 'current', 'stale', 'unmeasured', 'refresh-needed', 'instance-selection',
]);

// The workflow views moved beside the coordinator's unified send path; the
// re-export keeps this module the panel-facing home for existing callers.
export { fusionWorkflowView, onshapeWorkflowView, type CadWorkflowView };

function returnDisplayName(bundle: CadReturnBundle): string {
  if (bundle.documentName) return bundle.documentName;
  const date = new Date(bundle.modifiedAt);
  if (Number.isNaN(date.getTime())) return 'Model';
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `Model · ${hours}:${minutes}`;
}

/** How a declared pre-cut domain reads in one phrase, or '' for a full model. */
export function declaredDomainPhrase(planes: string[] | undefined): string {
  const cut = (planes ?? []).filter((plane) => plane === 'x0' || plane === 'y0');
  if (cut.length === 0) return '';
  return `${cut.length === 1 ? 'half' : 'quarter'} model, cut on ${joinPlanes(cut)}`;
}

function bundleInventory(bundle: CadReturnBundle): string {
  if (!bundle.readable) return bundle.reason ?? 'WG cannot read this model. Send it again from CAD.';
  const sources = pluralized(bundle.sourceCount ?? bundle.sources.length, 'source');
  const domain = declaredDomainPhrase(bundle.declaredCutPlanes);
  const parts = [sources];
  if (bundle.instanceCount) parts.push(pluralized(bundle.instanceCount, 'linked instance'));
  // Which model is about to be solved is not a detail: a half solves in a
  // quarter of the time and answers a different question than the same file
  // returned whole.
  if (domain) parts.push(domain);
  return parts.join(' · ');
}

/** The developer detail behind the model's status line: the raw text of WG's
 * own failed preparation, and why the mesh on screen no longer matches. */
function CadModelDeveloperDetails({ staleReason }: { staleReason: string | null | undefined }) {
  const model = useCadModelSelection();
  const lines = [model.failureDiagnostic, staleReason].filter((line): line is string => !!line);
  if (!lines.length) return null;
  return <DeveloperDetails className="cad-model-developer-details">
    {lines.map((line) => <p key={line} className="cad-detail">{line}</p>)}
  </DeveloperDetails>;
}

/** Explain the file transport's idle observation policy. */
export function CadCoordinationNote() {
  const state = useSyncExternalStore(cadCoordinationStore.subscribe, cadCoordinationStore.getSnapshot, cadCoordinationStore.getSnapshot);
  const coordinator = useSyncExternalStore(cadWorkspace.subscribe, cadWorkspace.getSnapshot, cadWorkspace.getSnapshot);
  // How the transport polls is developer detail.
  const developerDetails = useDeveloperDetails();
  // Wait until the returns listing has reported its compatibility policy.
  if (!developerDetails || state === 'unknown' || state === 'on') return null;
  const inbox = coordinator.fusionStatus?.addinInboxTransfer === true;
  return <p className="cad-detail cad-coordination-state" data-coordination={state}>
    {inbox
      ? 'Background checks are off. WG checks models from CAD and Fusion status while CAD work is in progress, and when you act.'
      : 'Background checks are off. This older Fusion add-in needs WG to keep checking the WGLink folder for models you send.'}
  </p>;
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
        title="Models with no CAD-linked project. Never selected as this project's geometry automatically."
        onClick={() => setFilter('unlinked')}
      >Unlinked · {unlinkedReturns.length}</button>
    </div>}
    <div className="cad-bundle-list" role="listbox" aria-label="CAD model history">
      {shown.map((bundle) => <button
        type="button"
        key={bundle.bundlePath}
        role="option"
        aria-selected={selectedPath === bundle.bundlePath}
        disabled={!bundle.readable}
        onClick={() => select(bundle)}
        title={!bundle.readable ? bundle.reason ?? 'WG cannot read this model. Send it again from CAD.' : `${bundleInventory(bundle)} · ${fullTime(bundle.modifiedAt)}`}
      ><b>{returnDisplayName(bundle)}</b><span>{bundleInventory(bundle)}</span><time dateTime={bundle.modifiedAt} title={fullTime(bundle.modifiedAt)}>{relativeTime(bundle.modifiedAt)}</time></button>)}
    </div>
    {otherProjectReturns > 0 && <p className="cad-detail">{pluralized(otherProjectReturns, 'model')} from other CAD-linked projects {otherProjectReturns === 1 ? 'is' : 'are'} not listed. Open that project from File → CAD-linked designs to use {otherProjectReturns === 1 ? 'it' : 'them'}.</p>}
  </CadDrawer>;
}

/** Keep the count before any names, including when the workflow detail does
 * not mention parameter edits (for example, Fusion-only geometry changes). */
function FusionParameterDrift({ parameterDriftCount, driftedParameters, workflowDetail }: {
  parameterDriftCount: number;
  driftedParameters?: readonly string[];
  workflowDetail: string;
}) {
  const detail = fusionParameterDriftDetail(parameterDriftCount);
  if (!detail) return null;
  return <>
    {!workflowDetail.includes(detail) && <p>{detail}</p>}
    {!!driftedParameters?.length && <ul aria-label="Locally edited Fusion parameters">
      {driftedParameters.map((name) => <li key={name}><code>{name}</code></li>)}
    </ul>}
  </>;
}

export function CadLinkPanel() {
  const connection = useRef<HTMLElement>(null);
  const visible = usePanelVisible();
  const navigation = useSyncExternalStore(workspaceNavigation.subscribe, workspaceNavigation.getSnapshot, workspaceNavigation.getSnapshot);
  useEffect(() => {
    if (!visible || workspaceNavigation.getRevealTarget() !== 'cad-link' || !connection.current) return;
    const disclosure = connection.current.querySelector('details');
    if (disclosure) disclosure.open = true;
    connection.current.scrollIntoView?.({ block: 'nearest' });
    connection.current.focus();
    workspaceNavigation.claimReveal('cad-link');
  }, [navigation, visible]);
  const state = useCadReturnStore();
  const preferences = usePreferences();
  const design = useDesignStore((current) => current.design);
  const cadLimitation = nativeCadLimitation(design.formula);
  const observedFusionStatus = useCadConnectionStore((current) => current.fusionStatus);
  const designRevision = useDesignStore((current) => current.designRevision);
  const documentName = useDocumentStore((current) => current.designName);
  const identity = useDocumentStore((current) => current.identity);
  const setCadLink = useDocumentStore((current) => current.setCadLink);
  const cadWorkspaceView = useSyncExternalStore(cadWorkspace.subscribe, cadWorkspace.getSnapshot, cadWorkspace.getSnapshot);
  const [confirmPublicDocument, setConfirmPublicDocument] = useState<string | null>(null);
  const [sendingToOnshape, setSendingToOnshape] = useState(false);
  const [unlinkingOnshape, setUnlinkingOnshape] = useState(false);
  const [confirmUnlink, setConfirmUnlink] = useState(false);
  const [installingAddin, setInstallingAddin] = useState(false);
  // The action's own answer, and the Fusion status it was given under. A later
  // status read replaces it: a pending install can finish on its own.
  const [addinReply, setAddinReply] = useState<{ detail: string; under: unknown } | null>(null);
  async function installAddin() {
    setInstallingAddin(true);
    let detail: string;
    try {
      detail = (await installCadLinkAddin()).detail;
    } catch (error) {
      detail = error instanceof Error ? error.message : 'Could not install the Waveguide Generator add-in in Fusion. Try again.';
    }
    setAddinReply({ detail, under: cadWorkspace.getSnapshot().fusionStatus });
    setInstallingAddin(false);
  }
  const onshapeSendGeneration = useRef(0);
  const onshape = preferences.cadApplication === 'onshape';
  const {
    bundles,
    loading,
    ingesting,
    sendingToFusion,
    error: actionError,
    errorDiagnostics,
    status,
    viewportNotice,
    fusionStatus,
    onshapeStatus,
    onshapeConnection,
  } = cadWorkspaceView;
  const error = useCadFeedbackError(actionError);
  const addinNotice = addinReply === null ? null
    : fusionStatus !== addinReply.under && fusionStatus?.addinRefresh
      ? fusionStatus.addinRefresh.detail
      : addinReply.detail;
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
    if (cadLimitation) return;
    const request = ++onshapeSendGeneration.current;
    const sourceRevision = designRevision;
    const documentLoad = currentDocumentLoad();
    cadWorkspaceView.clearFeedback(); setSendingToOnshape(true);
    try {
      const wasLinked = onshapeStatus?.state === 'stale' || onshapeStatus?.state === 'current';
      const polarConfig = polarConfigFromUi(useSolveOptionsStore.getState().polar);
      // What the registry holds once this commits, taken before the awaits.
      const sentKey = keptContentKeyOf(design);
      const result = await sendDesignToOnshape(
        design, designRevision, designNameSlug(documentName), identity, {
          allowPublic,
          polarConfig,
          instanceId: onshapeStatus?.selectedInstanceId ?? null,
        },
      );
      rememberSentCopy(
        sentKey,
        result.identity?.designId,
        request === onshapeSendGeneration.current && isCurrentDocumentLoad(documentLoad),
      );
      if (request !== onshapeSendGeneration.current) return;
      setConfirmPublicDocument(null);
      if (useDesignStore.getState().designRevision !== sourceRevision) {
        cadWorkspaceView.reportStatus('Sent the previous design to Onshape, but the WG design changed while it was uploading. Send again to link the current design.');
        return;
      }
      recordCommittedAthPolars(polarConfig);
      const visibility = result.onshape.isPublic ? ' · public document' : '';
      cadWorkspaceView.reportStatus(result.onshape.createdDocument
        ? `Created ${result.onshape.documentName} in Onshape · ${result.onshape.variablesPushed} parameters${visibility}`
        : `Updated ${result.onshape.documentName} in Onshape · sequence ${result.sequence}${visibility}`);
      if (!wasLinked && result.identity) setCadLink(result.identity, 'current');
      // A first send can mint this identity. Passing it explicitly avoids the
      // pre-send closure briefly reporting the new document as not linked.
      await cadWorkspaceView.refreshOnshapeStatus(result.identity);
    } catch (reason) {
      // The registry is committed before the upload that can still fail (the
      // public-document refusal among them), so a failed send may have
      // overwritten the copy this design was opened from: forget it.
      rememberSentCopy(null, identity?.designId, isCurrentDocumentLoad(documentLoad));
      if (request !== onshapeSendGeneration.current) return;
      // HTTP 428 is control flow, not a generic send error: this dialog is the
      // only path that can retry with allowPublic on an Onshape Free account.
      if (reason instanceof OnshapePublicConsentRequired) {
        setConfirmPublicDocument(reason.message);
      } else {
        cadWorkspaceView.reportError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (request === onshapeSendGeneration.current) setSendingToOnshape(false);
    }
  };

  // The coordinator owns one pending pull shared by every control, so both
  // entry points stay busy until arrival, failure, or the timeout.
  const bringFromFusion = () => {
    cadWorkspaceView.clearFeedback();
    void cadWorkspaceView.pullFromFusion().catch(() => undefined);
  };

  const sendToFusion = () => {
    if (cadLimitation) return;
    void cadWorkspaceView.sendWgToFusion().catch(() => undefined);
  };

  // Forget this design's link to its Onshape document. The document itself
  // is left exactly as it is; the next send creates a new one.
  const unlinkFromOnshape = async () => {
    const link = onshapeStatus?.link;
    const designId = identity?.designId ?? link?.designId;
    if (!link || !designId) return;
    cadWorkspaceView.clearFeedback(); setUnlinkingOnshape(true);
    try {
      const result = await unlinkOnshape(designId, link.instanceId);
      cadWorkspaceView.reportStatus(result.unlinked
        ? `Unlinked ${link.documentName}. The Onshape document is unchanged; the next send creates a new one.`
        : `${link.documentName} was already unlinked.`);
      await cadWorkspaceView.refreshOnshapeStatus();
    } catch (reason) {
      cadWorkspaceView.reportError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setUnlinkingOnshape(false);
      setConfirmUnlink(false);
    }
  };

  const workflow = onshape ? onshapeWorkflowView(onshapeStatus) : fusionWorkflowView(fusionStatus);
  const designName = designNameSlug(documentName);
  const shownName = designName === UNTITLED_SLUG ? 'this design' : designName;
  const canRequestFusionReturn = Boolean(
    fusionStatus?.running && fusionStatus.documentName && fusionStatus.documentId
    && fusionStatus.link && identity?.designId,
  );
  // Onshape's Free plan makes every document world-readable. Say so before the
  // user sends, not after -- and say it from the plan WG actually read.
  const publicOnly = onshapeConnection?.plan?.publicOnly === true;
  const linkedDocument = onshapeStatus?.link ?? null;
  const matchingOnshapeLinks = onshapeStatus?.matchingLinks ?? [];
  const matchingFusionLinks = fusionStatus?.matchingLinks ?? [];
  const model = useCadModelState();
  const { record, bundle } = model;
  // An engine gate on this model's latest request is described only in
  // Results, with its remedies: its requests get no card here meanwhile.
  const { status: requestStatus } = useCurrentModelStatus(record ?? undefined);
  const engineHold = requestStatus.kind === 'waiting' && ENGINE_HOLDS.has(requestStatus.job?.cad_state?.reason ?? '');
  // A model authored in Fusion is on screen. The link states compare the
  // Fusion document with the parametric WG design, which is not this model,
  // and their Open and Send actions would insert that design into the user's
  // document: while Fusion is simply connected, it is one quiet line.
  const unlinkedOnScreen = !onshape && model.authoredInCad;
  const fusionConnected = unlinkedOnScreen && Boolean(fusionStatus?.running)
    && DOCUMENT_LINK_STATES.has(workflow.state);
  const quietLink = unlinkedOnScreen || workflow.state === 'current' || workflow.state === 'checking';
  const addinVersion = !onshape && fusionStatus?.running && fusionStatus.adapterVersion
    ? `The Waveguide Generator add-in in Fusion ${fusionStatus.adapterVersion}`
    : undefined;
  const fusionBothChanged = Boolean(fusionStatus?.wgChangesAvailable && fusionStatus.fusionChangesAvailable);
  const staleModel = model.preparation === 'stale';
  const solveCard = model.preparation === 'prepared';
  const onshapeActionLabel = workflow.action === 'update' ? 'Send WG changes to Onshape' : `Create ${shownName} in Onshape`;
  const unlinkButton = <button
    className="btn-link link-button cad-onshape-unlink"
    disabled={unlinkingOnshape || sendingToOnshape}
    title="Forget this link. The Onshape document is left as it is; the next send creates a new document."
    onClick={() => setConfirmUnlink(true)}
  >{unlinkingOnshape ? 'Unlinking…' : 'Unlink'}</button>;

  const workflowDetail = <>
    <p>{workflow.detail}</p>
    {!onshape && <FusionParameterDrift
      parameterDriftCount={fusionStatus?.link?.parameterDriftCount ?? 0}
      driftedParameters={fusionStatus?.link?.driftedParameters}
      workflowDetail={workflow.detail}
    />}
  </>;

  const linkActions = onshape
    ? <>
      <button className="btn-link link-button" disabled={sendingToOnshape || Boolean(cadLimitation)} title={cadLimitation} onClick={() => void sendToOnshape()}>Send to Onshape</button>
      {linkedDocument && <button className="btn-link link-button" disabled={ingesting} title="Export the linked Part Studio to STEP, verify its source evidence, and prepare it for the viewport and solver." onClick={() => { void cadWorkspaceView.returnFromOnshape().catch(() => undefined); }}>Bring geometry into WG</button>}
      {linkedDocument?.documentUrl && <a className="btn-link link-button" href={linkedDocument.documentUrl} target="_blank" rel="noreferrer noopener">Open in Onshape</a>}
      {linkedDocument && unlinkButton}
    </>
    : <>
      <button className="btn-link link-button" disabled={sendingToFusion || Boolean(cadLimitation)} title={cadLimitation ?? "Rebuild the linked Fusion waveguide from the current WG design."} onClick={sendToFusion}>Send to Fusion</button>
      <button className="btn-link link-button" disabled={!canRequestFusionReturn || cadWorkspaceView.pullingFromFusion} title="Ask Fusion for the active document's current geometry and source tags." onClick={() => void bringFromFusion()}>{cadWorkspaceView.pullingFromFusion ? 'Waiting for Fusion…' : 'Bring geometry into WG'}</button>
    </>;

  // 2 · CAD Link: is CAD in sync with WG? One card, both directions. Install
  // add-in is its action only while the add-in is missing or out of date.
  const connectionCard = <section ref={connection} tabIndex={-1} aria-label="CAD Link connection" className={`cad-workflow cad-link-card${quietLink ? '' : ' attention'}`}>
    {quietLink
      ? <details className="cad-link-quiet">
        <summary title={unlinkedOnScreen ? addinVersion ?? '' : [workflow.detail, addinVersion].filter(Boolean).join(' · ')}>
          <span className="cad-link-chevron" aria-hidden="true">›</span>
          <span className={`cad-connection-dot cad-connection-dot-${fusionConnected ? 'current' : workflow.state}`} aria-hidden="true"/>
          <b>{onshape ? 'Onshape' : 'Fusion 360'}{unlinkedOnScreen ? fusionStatus?.statusObserved === false ? ' · last reported' : fusionConnected ? ' · connected' : fusionStatus === null ? ' · checking…' : fusionStatus.running ? ' · connection needs attention' : ' · not connected' : workflow.state === 'current' ? ' · in sync' : ' · checking…'}</b>
          <span className="cad-link-meta">{onshape ? linkedDocument?.documentName ?? '' : fusionStatus?.documentName ?? ''}</span>
        </summary>
        <div className="cad-link-quiet-body">
          {!unlinkedOnScreen && workflowDetail}
          {unlinkedOnScreen && !fusionConnected && workflow.action === null && workflowDetail}
          <CadCoordinationNote/>
          <div className="cad-link-actions">
            {!unlinkedOnScreen && linkActions}
            <button className="btn-link link-button cad-link-settings" onClick={() => requestSettings('cad')}>Settings</button>
          </div>
        </div>
      </details>
      : <>
        <div className={`cad-connection cad-connection-${workflow.state}`}>
          <span className="cad-connection-dot" aria-hidden="true"/>
          <div><h4 title={addinVersion}>{workflow.headline}</h4>{workflowDetail}<CadCoordinationNote/></div>
          <button className="btn-link link-button cad-link-settings" onClick={() => requestSettings('cad')}>Settings</button>
        </div>
        {onshape && matchingOnshapeLinks.length > 1 && <label className="field-row linked-instance-selection">
          <span>Managed Onshape link</span>
          <select
            aria-label="Linked Onshape instance"
            value={onshapeStatus?.selectedInstanceId ?? ''}
            onChange={(event) => cadWorkspaceView.selectOnshapeInstance(event.target.value)}
          >
            <option value="" disabled>Choose a link</option>
            {matchingOnshapeLinks.map((link) => <option value={link.instanceId} key={link.instanceId}>
              {link.documentName} · {link.instanceId}
            </option>)}
          </select>
          <small>Updates and received models use this exact linked Part Studio. WG does not guess from the newest document.</small>
        </label>}
        {/* Fusion: the actions that resolve the out-of-sync state, and only those. */}
        {!onshape && workflow.state === 'not-configured' && fusionStatus?.cadFolderMissingPath && <RootFolderControls cadlinkOnly showPath={false}/>}
        {!onshape && workflow.state === 'not-configured' && !fusionStatus?.cadFolderMissingPath && <button className="btn-primary primary cad-primary-action" onClick={() => requestSettings('cad')}>Set up Fusion connection</button>}
        {!onshape && !unlinkedOnScreen && workflow.action === 'open' && <button className="btn-primary primary cad-primary-action" disabled={sendingToFusion || Boolean(cadLimitation)} title={cadLimitation} onClick={sendToFusion}>{sendingToFusion ? 'Sending…' : 'Open in Fusion 360'}</button>}
        {!onshape && fusionStatus?.fusionChangesAvailable && !fusionStatus.wgChangesAvailable && <div className="cad-confirm-actions">
          <button className="btn" disabled={!canRequestFusionReturn || cadWorkspaceView.pullingFromFusion} onClick={() => void bringFromFusion()}>{cadWorkspaceView.pullingFromFusion ? 'Waiting for Fusion…' : 'Bring in'}</button>
          <button className="btn-primary primary" disabled={!canRequestFusionReturn || cadWorkspaceView.pullingFromFusion} title="Bring the current Fusion geometry into WG, prepare it, and start the solve." onClick={() => { void cadWorkspaceView.pullAndSolve(); }}>{cadWorkspaceView.pullingFromFusion ? 'Waiting for Fusion…' : 'Bring in & solve'}</button>
        </div>}
        {!onshape && !unlinkedOnScreen && workflow.action === 'update' && <div className={fusionBothChanged ? 'cad-confirm-actions' : undefined}>
          {fusionBothChanged && <button className="btn" disabled={!canRequestFusionReturn || cadWorkspaceView.pullingFromFusion} title="Keep the Fusion edits: bring the Fusion geometry into WG instead of overwriting it." onClick={() => void bringFromFusion()}>{cadWorkspaceView.pullingFromFusion ? 'Waiting for Fusion…' : 'Bring Fusion changes in'}</button>}
          <button className="btn-primary primary cad-primary-action" disabled={sendingToFusion || Boolean(cadLimitation)} title={cadLimitation} onClick={sendToFusion}>{sendingToFusion ? 'Sending…' : 'Send WG changes to Fusion'}</button>
        </div>}
        {/* Onshape: the same card carries its send/return pair. */}
        {onshape && workflow.state !== 'not-configured' && !confirmPublicDocument && workflow.action && <button className="btn-primary primary cad-primary-action" disabled={sendingToOnshape || Boolean(cadLimitation)} title={cadLimitation} onClick={() => void sendToOnshape()}>{sendingToOnshape ? (workflow.action === 'update' ? 'Updating…' : 'Creating…') : onshapeActionLabel}</button>}
        {onshape && linkedDocument && workflow.state !== 'not-configured' && <button className="btn cad-secondary-action" disabled={ingesting} onClick={() => { void cadWorkspaceView.returnFromOnshape().catch(() => undefined); }}>{ingesting ? 'Receiving & preparing…' : 'Bring Onshape geometry into WG'}</button>}
        {onshape && linkedDocument?.documentUrl && <a className="btn-link link-button cad-onshape-open" href={linkedDocument.documentUrl} target="_blank" rel="noreferrer noopener">Open {linkedDocument.documentName} in Onshape</a>}
        {onshape && linkedDocument && workflow.state !== 'not-configured' && unlinkButton}
      </>}
    {!onshape && matchingFusionLinks.length > 1 && <label className="field-row linked-instance-selection">
      <span>Managed Fusion link</span>
      <select
        aria-label="Linked Fusion instance"
        value={fusionStatus?.selectedInstanceId ?? fusionStatus?.link?.instanceId ?? ''}
        onChange={(event) => cadWorkspaceView.selectFusionInstance(event.target.value)}
      >
        <option value="" disabled>Choose an instance</option>
        {matchingFusionLinks.map((link) => {
          const name = fusionLinkDisplayName(observedFusionStatus?.matchingLinks?.find((item) => item.instanceId === link.instanceId) ?? link);
          return <option value={link.instanceId} key={link.instanceId}>
            {name ? `${name} · ` : ''}{link.instanceId}
          </option>;
        })}
      </select>
      <small>Freshness, geometry requests and updates use this exact managed body. Placements of the same design are never merged.</small>
    </label>}
    {cadLimitation && <p className="section-note">{cadLimitation}</p>}
    {onshape && publicOnly && !confirmPublicDocument && <Notice tone="warning" title="This Onshape plan creates public documents.">{onshapeConnection?.plan?.name ?? 'The Free plan'} makes every document world-readable — anyone with the link can view this waveguide. Confidential designs belong in Fusion 360 or on a paid Onshape plan.</Notice>}
    {onshape && onshapeConnection?.insecureKeyFile && <Notice tone="error">The Onshape key file at {onshapeConnection.credentialsPath} is readable by other accounts on this machine. Restrict it with <code>chmod 600</code>.</Notice>}
    {onshape && confirmPublicDocument && <Notice tone="warning" role="alert" title={<>This document will be public</>} actions={<div className="cad-confirm-actions"><button className="btn" onClick={() => setConfirmPublicDocument(null)}>Cancel</button><button className="btn-primary primary" disabled={sendingToOnshape || Boolean(cadLimitation)} title={cadLimitation} onClick={() => void sendToOnshape(true)}>Continue: create a public document</button></div>}><span>{confirmPublicDocument}</span></Notice>}
    {onshape && confirmUnlink && linkedDocument && <Notice tone="warning" role="alert" title={<>Unlink {linkedDocument.documentName}?</>} actions={<div className="cad-confirm-actions"><button className="btn" onClick={() => setConfirmUnlink(false)}>Cancel</button><button className="btn-primary primary" disabled={unlinkingOnshape} onClick={() => void unlinkFromOnshape()}>Unlink {linkedDocument.documentName}</button></div>}><span>WG forgets this link. The Onshape document is left exactly as it is, and the next send creates a new document.</span></Notice>}
    {!onshape && (fusionAddinNeedsInstall(fusionStatus) || installingAddin || addinNotice) && <div className="cad-link-actions cad-addin-install">
      <button className="btn-link link-button" disabled={installingAddin} onClick={() => void installAddin()}
        title="Install or repair the Waveguide Generator add-in in Fusion. WG keeps it up to date after you choose this.">
        {installingAddin ? 'Installing…' : 'Install add-in'}
      </button>
      {addinNotice && <span role="status">{addinNotice}</span>}
      {fusionAddinTooOldForUpdate(fusionStatus) && <span role="status">
        Update the Fusion add-in to let manual Update follow newer exports. Choose Install add-in here, then restart Fusion. Automatic Send still works.
      </span>}
    </div>}
  </section>;

  return <div className="cadlink-panel">
    <h2 className="sr-only">CAD Link</h2>
    {/* One notice channel. Transient events land here, at the top of the rail;
        persistent conditions render inside the card they belong to. */}
    {error && <Notice tone="error">
      {error}
      {/* The report behind the sentence: the adapter's own words, the entity
          ids and the internal invariants. It belongs to this exact message,
          so a later unrelated error leaves it behind rather than carrying a
          stale explanation under a new headline. */}
      {errorDiagnostics?.message === error && <DeveloperDetails summary="Diagnostics" className="cad-alert-diagnostics">
        <p className="cad-detail">{errorDiagnostics.detail}</p>
      </DeveloperDetails>}
    </Notice>}
    {status && <Notice tone="info" className="cad-status-strip" role="status">{status}</Notice>}

    {/* 1 · Project: what am I working on? */}
    <CadProjectHeader/>

    {/* The connection leads only while it needs the user; otherwise it is one
        quiet line below the runs. */}
    {!quietLink && connectionCard}

    {/* 3 · Model: is the geometry sound? */}
    <section className="cad-workflow cad-model-card">
      <header className="cad-model-head">
        <span className="cad-card-label">Model</span>
        <span className="spacer"/>
        <button
          className="icon-button cad-icon-button"
          disabled={loading || ingesting}
          title="Refresh the CAD model list"
          aria-label="Refresh CAD models"
          onClick={() => void cadWorkspaceView.refresh()}
        ><Icon name="reset"/></button>
      </header>
      {!bundle && !record && <EmptyState title="No CAD model yet." detail={onshape
          ? 'Send this design to Onshape, then bring its geometry back here.'
          : 'Send a design from Fusion 360 and it will appear here.'}/>}
      {bundle && <div className="cad-model-identity">
        <b className="cad-model-name" title={returnDisplayName(bundle)}>{returnDisplayName(bundle)}</b>
        {bundle.bundleOrigin === 'native' && <ExperimentalBadge/>}
        {record && <span
          className={`cad-state-chip${['current', 'unlinked'].includes(freshnessSummary(record)) ? '' : ' warn'}`}
          title={FRESHNESS_COPY[freshnessSummary(record).replaceAll(' ', '_')] ?? FRESHNESS_COPY.unknown}
        >{freshnessSummary(record) === 'unlinked' ? `from ${onshape ? 'Onshape' : 'Fusion'}` : freshnessSummary(record)}</span>}
        <time dateTime={record?.created_at || bundle.modifiedAt} title={fullTime(record?.created_at || bundle.modifiedAt)}>{relativeTime(record?.created_at || bundle.modifiedAt)}</time>
      </div>}
      {state.setupLoading && <Notice>Loading model settings…</Notice>}
      {/* The model's status line and its one action live in Results, once. */}
      {/* What lies behind the line, for developers: the raw text of WG's own
          failure, and why the mesh on screen no longer matches. */}
      {(bundle || record) && <CadModelDeveloperDetails staleReason={state.ingestStaleReason}/>}
      {viewportNotice && <Notice>{viewportNotice}</Notice>}
      {/* Beside the button that starts the solve, not only in the rail where
          the drivers are set: this is what the run will and will not produce,
          and it was previously only discoverable from the empty chart. */}
      {record && !staleModel && importedSubmissionNotices(state).map((notice) => (
        <Notice key={notice}>{notice}</Notice>
      ))}
      {/* The requests for this model that wait, with only the actions Solve
          never takes; the rest are one quiet line each below. */}
      {solveCard && !engineHold && <CurrentModelRequests record={record}/>}
      {/* What the model is, how it is solved and which way it radiates, its
          mesh, settings and checks: one disclosure, collapsed. */}
      {record && <CadModelDetails record={record} prepared={solveCard} label={record.project?.document_name ?? bundle?.documentName ?? 'this model'}/>}
      <CadDeliveryHealth/>
      <CadOperationsSection record={record} solves={!solveCard} lineQuestion={model.heldRequest}/>
      <ModelVersions
        projectBundles={projectBundles}
        unlinkedReturns={unlinkedReturns}
        otherProjectReturns={otherProjectReturns}
        selectedPath={bundle?.bundlePath ?? null}
        select={cadWorkspaceView.selectBundle}
      />
    </section>

    {/* 4 · Runs: what have I solved? */}
    <CadProjectHistory/>
    {quietLink && connectionCard}
  </div>;
}
