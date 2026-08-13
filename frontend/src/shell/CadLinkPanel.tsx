import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { CadLinkApiError, getFusionCadStatus, ingestReturn, listReturns, requestFusionReturn, type CadReturnBundle, type CadReturnFinding, type CadReturnIngestRecord, type FusionCadStatus } from '../api/cadlink';
import { NumberField } from '../design/NumberField';
import { FrequencySweepControls, ToggleRow } from '../design/SolveOptionsSections';
import { useSendToCad } from '../design/useSendToCad';
import { buildImportedSubmission, importedSubmissionBlocker } from '../jobs/importedSubmission';
import { usePreferences, type CadApplication } from '../prefs/preferences';
import {
  blockingFindings,
  combineChain,
  combineLevelMatchDefault,
  DRIVER_REQUIRED_KEYS,
  useCadReturnStore,
  type CadDriveChannel,
  type ChannelDriverForm,
  type DriverFieldKey,
} from '../stores/cadReturn';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { parseMSH } from '../viewport/mshParser';
import { useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { filenameStem } from '../viewport/presentation';
import { jobsCoordinatorBridge } from './JobsCoordinator';
import { Icon } from './icons';
import { requestSettings } from './settingsNavigation';
import { workspaceNavigation } from './workspaceNavigation';
import './cadLinkPanel.css';

// Kept as public panel helpers for existing callers; implementation lives next
// to the two solve entry points so the global command can share it.
export { buildImportedSubmission, widenPolarToDerivation } from '../jobs/importedSubmission';

const FRESHNESS_COPY: Record<string, string> = {
  current: 'Current — unchanged fingerprint and the saved design still agree with this generator.',
  body_modified: 'CAD body changed after linking. This returned geometry remains the solve truth.',
  missing_design: 'The linked design is not in this workspace registry; this is normal for a return from another machine.',
  design_changed: 'The linked Waveguide Generator design has changed since this geometry was exported.',
  generator_changed: 'The same saved design would export differently with the current generator.',
  unknown: 'Freshness could not be established from the available evidence.',
  unlinked: 'Unlinked return — no Waveguide Generator design identity was attached in CAD.',
};

export interface CadWorkflowView {
  state: 'checking' | 'closed' | 'addin-offline' | 'no-document' | 'not-linked' | 'current' | 'stale' | 'coming-soon';
  headline: string;
  detail: string;
  action: 'open' | 'update' | null;
}

export function newestReturnArrival(
  items: CadReturnBundle[],
  previous: Map<string, string> | null,
  nowMs = Date.now(),
): CadReturnBundle | null {
  const recentThreshold = nowMs - 60_000;
  return items.find((item) => item.readable && (
    previous
      ? previous.get(item.bundlePath) !== item.modifiedAt
      : Date.parse(item.modifiedAt) >= recentThreshold
  )) ?? null;
}

export function cadWorkflowView(
  application: CadApplication,
  status: FusionCadStatus | null,
): CadWorkflowView {
  if (application === 'onshape') return {
    state: 'coming-soon',
    headline: 'Onshape support is coming soon',
    detail: 'Choose Fusion 360 in CAD settings to open or update this waveguide today.',
    action: null,
  };
  if (status === null) return {
    state: 'checking',
    headline: 'Checking Fusion 360…',
    detail: 'WG is looking for the WGLink add-in and its active document.',
    action: 'open',
  };
  if (status.state === 'closed') return {
    state: 'closed',
    headline: 'Fusion 360 is closed',
    detail: 'Open this WG design in Fusion 360. WG will start Fusion and WGLink will create a Design document if needed.',
    action: 'open',
  };
  if (status.state === 'addin_offline') return {
    state: 'addin-offline',
    headline: 'Fusion 360 is open · WGLink add-in is offline',
    detail: 'Fusion is running, but its WGLink add-in has not reported in. In Fusion, open Utilities → Scripts and Add-Ins, then stop and run WGLink.',
    action: null,
  };
  if (status.state === 'no_document') return {
    state: 'no-document',
    headline: 'Fusion 360 is open · no Design document',
    detail: 'WGLink will create a Design document and insert this waveguide.',
    action: 'open',
  };
  if (status.state === 'not_linked') return {
    state: 'not-linked',
    headline: `Fusion 360 is open${status.documentName ? ` · ${status.documentName}` : ''}`,
    detail: 'This WG design is not linked in the active Fusion document yet.',
    action: 'open',
  };
  const parameterCopy = status.link?.parameterCount
    ? `${status.link.parameterCount} managed CAD parameters`
    : 'the managed CAD parameters';
  if (status.state === 'current') return {
    state: 'current',
    headline: `Fusion 360 is open · ${status.documentName ?? 'active document'}`,
    detail: `Up to date. The full WG config and ${parameterCopy} are synchronized.`,
    action: null,
  };
  if (!status.wgChangesAvailable && status.fusionChangesAvailable) return {
    state: 'stale',
    headline: `Fusion geometry has changed${status.documentName ? ` · ${status.documentName}` : ''}`,
    detail: 'The parametric WG design has not changed. Bring the Fusion geometry into WG before rebuilding the Fusion waveguide from WG.',
    action: null,
  };
  if (!status.wgChangesAvailable) return {
    state: 'stale',
    headline: `Fusion has local geometry changes${status.documentName ? ` · ${status.documentName}` : ''}`,
    detail: 'The parametric WG design matches Fusion. The latest Fusion geometry has already been returned to WG for simulation.',
    action: null,
  };
  const fusionFormula = status.fusionFormula?.toLocaleUpperCase();
  const currentFormula = status.currentFormula.toLocaleUpperCase();
  const mismatch = fusionFormula && fusionFormula !== currentFormula
    ? `Fusion has ${fusionFormula}; WG is now ${currentFormula}.`
    : 'WG parameters changed after this Fusion waveguide was built.';
  const configCopy = status.link?.configPresent
    ? ''
    : ' This link also predates full WG config synchronization.';
  const localEditCopy = status.link?.parameterDriftCount
    ? ` ${status.link.parameterDriftCount} managed Fusion parameter${status.link.parameterDriftCount === 1 ? ' has' : 's have'} local edits.`
    : status.link?.localBodyState && status.link.localBodyState !== 'unmodified'
      ? ` The managed Fusion body is ${status.link.localBodyState}.`
      : '';
  const conflictCopy = status.fusionChangesAvailable
    ? ' Fusion geometry also changed; choose which direction to synchronize.'
    : '';
  return {
    state: 'stale',
    headline: `${status.fusionChangesAvailable ? 'WG and Fusion both changed' : 'WG design changed'}${status.documentName ? ` · ${status.documentName}` : ''}`,
    detail: `${mismatch}${configCopy}${localEditCopy}${conflictCopy}`,
    action: 'update',
  };
}

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

/** Show the ingested solve mesh in the viewport: in CAD Link mode the model
 * being solved is the CAD return, not the parametric preview. Advisory — a
 * failure leaves the viewport as it was rather than blocking the ingest. */
async function showIngestedMeshInViewport(
  ingestId: string,
  name: string,
  symmetryCutPlanes: readonly string[] = [],
): Promise<void> {
  const available = importedMeshStore.getSnapshot().scene;
  if (available?.source === 'cad' && available.ingestId === ingestId) {
    importedMeshStore.showImported();
    return;
  }
  try {
    const response = await fetch(`/api/cadlink/ingest/${encodeURIComponent(ingestId)}/mesh`);
    if (!response.ok) return;
    importedMeshStore.set(createImportedMeshScene(
      name,
      parseMSH(await response.text()),
      'cad',
      ingestId,
      symmetryCutPlanes,
    ));
  } catch {
    // The viewport keeps whatever it was showing.
  }
}

const DRIVER_FIELD_LABELS: ReadonlyArray<{ key: DriverFieldKey; label: string; unit: string; step: number }> = [
  { key: 'sd_cm2', label: 'Sd', unit: 'cm²', step: 5 },
  { key: 'bl_t_m', label: 'Bl', unit: 'T·m', step: 0.5 },
  { key: 're_ohm', label: 'Re', unit: 'Ω', step: 0.1 },
  { key: 'le_mh', label: 'Le', unit: 'mH', step: 0.05 },
  { key: 'mmd_g', label: 'Mmd', unit: 'g', step: 1 },
  { key: 'cms_m_per_n', label: 'Cms', unit: 'm/N', step: 0.0001 },
  { key: 'rms_kg_per_s', label: 'Rms', unit: 'kg/s', step: 0.1 },
  { key: 'xmax_mm', label: 'Xmax', unit: 'mm', step: 0.5 },
  { key: 'count', label: 'Count', unit: '', step: 1 },
  { key: 'rear_volume_l', label: 'Rear vol', unit: 'L', step: 0.5 },
];

/** Hornresp-unit T/S entry for one drive channel. Plain inputs on purpose:
 * an empty field means "not provided", which NumberField cannot express. */
function DriverFields({ channel, form, onField }: {
  channel: CadDriveChannel;
  form: ChannelDriverForm | undefined;
  onField: (field: DriverFieldKey, value: number | null) => void;
}) {
  const missing = DRIVER_REQUIRED_KEYS.filter((key) => form?.fields[key] === undefined);
  return <div className="cad-driver-grid">
    {DRIVER_FIELD_LABELS.map(({ key, label, unit, step }) => <label key={key} className="cad-driver-field">
      <span>{label}{unit ? ` (${unit})` : ''}{DRIVER_REQUIRED_KEYS.includes(key) ? ' *' : ''}</span>
      <input
        type="number"
        min={0}
        step={step}
        value={form?.fields[key] ?? ''}
        aria-label={`${label} for ${channel.id}`}
        onChange={(event) => onField(key, event.target.value === '' ? null : Number(event.target.value))}
      />
    </label>)}
    {missing.length > 0 && <p className="cad-driver-hint">Required: {missing.map((key) => DRIVER_FIELD_LABELS.find((item) => item.key === key)?.label ?? key).join(', ')}. The channel solves as a unit-drive basis until they are set. Vas/Fs/Qms alternatives are accepted through the API.</p>}
  </div>;
}

export function CadLinkPanel() {
  const state = useCadReturnStore();
  const solveStore = useSolveOptionsStore();
  const preferences = usePreferences();
  const design = useDesignStore((current) => current.design);
  const designRevision = useDesignStore((current) => current.designRevision);
  const filename = useDocumentStore((current) => current.filename);
  const identity = useDocumentStore((current) => current.identity);
  const coordinator = useSyncExternalStore(jobsCoordinatorBridge.subscribe, jobsCoordinatorBridge.getSnapshot, jobsCoordinatorBridge.getSnapshot);
  const viewportMesh = useSyncExternalStore(
    importedMeshStore.subscribe,
    importedMeshStore.getSnapshot,
    importedMeshStore.getSnapshot,
  );
  const [bundles, setBundles] = useState<CadReturnBundle[]>([]);
  const [loading, setLoading] = useState(true);
  const [ingesting, setIngesting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [requestingReturn, setRequestingReturn] = useState(false);
  const [confirmWgOverwrite, setConfirmWgOverwrite] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [fusionStatus, setFusionStatus] = useState<FusionCadStatus | null>(null);
  const seenReturnRevisions = useRef<Map<string, string> | null>(null);
  const fusionStatusRequest = useRef(0);
  const pendingReturnRequestId = useRef<string | null>(null);
  const pendingReturnRequestedAt = useRef<number | null>(null);
  const { send: sendToCad, sending } = useSendToCad();

  const refreshFusionStatus = useCallback(async () => {
    if (preferences.cadApplication !== 'fusion360') return;
    const request = ++fusionStatusRequest.current;
    try {
      const next = await getFusionCadStatus(
        design, identity, state.selectedBundle?.bundlePath ?? null,
      );
      if (request === fusionStatusRequest.current
        && ['closed', 'addin_offline', 'no_document', 'not_linked', 'current', 'stale'].includes(next.state)) {
        setFusionStatus(next);
      }
    } catch {
      // Presence is advisory. Workspace and export errors are presented by the
      // actual action; a missed heartbeat must not hide CAD returns.
    }
  }, [design, identity, preferences.cadApplication, state.selectedBundle?.bundlePath]);

  useEffect(() => {
    setFusionStatus(null);
    if (preferences.cadApplication !== 'fusion360') return undefined;
    void refreshFusionStatus();
    const timer = window.setInterval(() => { void refreshFusionStatus(); }, 2_500);
    return () => {
      window.clearInterval(timer);
      fusionStatusRequest.current += 1;
    };
  }, [designRevision, preferences.cadApplication, refreshFusionStatus]);

  const refresh = useCallback(async (options: { background?: boolean; autoOpenNew?: boolean } = {}) => {
    const background = options.background === true;
    if (!background) { setLoading(true); setError(null); }
    try {
      const response = await listReturns();
      setBundles(response.items);
      const previous = seenReturnRevisions.current;
      const next = new Map(response.items.map((item) => [item.bundlePath, item.modifiedAt]));
      const requested = pendingReturnRequestId.current
        ? response.items.find((item) => (
            item.readable && item.requestId === pendingReturnRequestId.current
          )) ?? null
        : null;
      if (
        pendingReturnRequestId.current
        && pendingReturnRequestedAt.current !== null
        && Date.now() - pendingReturnRequestedAt.current > 60_000
      ) {
        pendingReturnRequestId.current = null;
        pendingReturnRequestedAt.current = null;
        setError('Fusion did not return the requested model within 60 seconds. Check Fusion for a WGLink message, then retry.');
      }
      const arrived = options.autoOpenNew
        ? requested ?? (
            pendingReturnRequestId.current
              ? null
              : newestReturnArrival(response.items, previous)
          )
        : null;
      seenReturnRevisions.current = next;
      const initial = previous === null
        ? response.items.find((item) => item.readable) ?? null
        : null;
      const opened = arrived ?? initial;
      if (opened) {
        useCadReturnStore.getState().selectBundle(opened);
        importedMeshStore.showParametric();
      }
      if (arrived) {
        if (arrived.requestId === pendingReturnRequestId.current) {
          pendingReturnRequestId.current = null;
          pendingReturnRequestedAt.current = null;
        }
        setStatus(`Received ${arrived.documentName ?? arrived.name} from Fusion 360.`);
        workspaceNavigation.activate('cadlink');
      } else if (!initial) {
        const selected = useCadReturnStore.getState().selectedBundle;
        if (!selected) return;
        const current = response.items.find((bundle) => bundle.bundlePath === selected.bundlePath);
        useCadReturnStore.getState().refreshSelectedBundle(current ?? null);
      }
    } catch (reason) {
      if (!background) setError(reason instanceof Error ? reason.message : String(reason));
    }
    finally { if (!background) setLoading(false); }
  }, []);
  useEffect(() => {
    void refresh({ autoOpenNew: true });
    const timer = window.setInterval(() => {
      void refresh({ background: true, autoOpenNew: true });
    }, 2_500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const ingest = async () => {
    const current = useCadReturnStore.getState();
    if (!current.selectedBundle) return;
    setIngesting(true); setError(null); setStatus(null);
    try {
      const skipped = new Set(current.skippedSourceIds);
      const record = await ingestReturn({
        bundlePath: current.selectedBundle.bundlePath,
        mesh: {
          rigidSizeMm: current.rigidSizeMm,
          transitionMm: current.transitionMm,
          sourceSizeMm: Object.fromEntries(Object.entries(current.sourceSizesMm).filter(([id]) => !skipped.has(id))),
        },
        skippedSourceIds: current.skippedSourceIds,
        areaDriftOverrides: current.areaDriftOverrides,
      });
      current.applyIngest(record);
      setStatus(`Ingested ${record.ingest_id}. Review the verdicts before solving.`);
      void showIngestedMeshInViewport(
        record.ingest_id,
        current.selectedBundle.documentName || current.selectedBundle.name,
        record.symmetry.cut_planes ?? [],
      );
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      const structured = reason instanceof CadLinkApiError ? reason.areaDriftSources : [];
      structured.forEach(current.flagAreaDrift);
      if (!structured.length) {
        const drift = /source ['"]([^'"]+)['"] area drift/i.exec(message);
        if (drift) current.flagAreaDrift(drift[1]);
      }
      setError(message);
    }
    finally { setIngesting(false); }
  };

  // The outbound leg. Sending refreshes the list because the bundle it writes
  // is what CAD picks up, and a return for it lands in the same workspace.
  const send = async () => {
    setError(null); setStatus(null);
    try {
      const action = cadWorkflowView(preferences.cadApplication, fusionStatus).action;
      if (action === 'update' && fusionStatus?.fusionChangesAvailable && !confirmWgOverwrite) {
        setConfirmWgOverwrite(true);
        return;
      }
      const result = await sendToCad(
        action === 'update' && fusionStatus?.documentId
          ? {
              documentId: fusionStatus.documentId,
              returnStateHash: fusionStatus.link?.documentSignatureHash ?? null,
            }
          : undefined,
      );
      setConfirmWgOverwrite(false);
      setStatus(action === 'update'
        ? `Update sent to Fusion 360 · sequence ${result.sequence}`
        : `Opening in Fusion 360 · sequence ${result.sequence}`);
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  };

  const bringFromFusion = async () => {
    setRequestingReturn(true); setError(null); setStatus(null);
    try {
      if (!identity?.designId || !fusionStatus?.documentId || !fusionStatus.link) {
        throw new Error('Fusion changed documents. Refresh CAD Link and try again.');
      }
      const result = await requestFusionReturn({
        designId: identity.designId,
        documentId: fusionStatus.documentId,
        instanceId: fusionStatus.link.instanceId,
        expectedReturnStateHash: fusionStatus.link.documentSignatureHash,
      });
      pendingReturnRequestId.current = result.requestId;
      pendingReturnRequestedAt.current = Date.now();
      setStatus(`Requested current geometry from ${result.documentName}. Waiting for Fusion…`);
      await refresh({ background: true, autoOpenNew: true });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRequestingReturn(false);
    }
  };

  const activeSources = (state.selectedBundle?.sources ?? []).filter((source) => !state.skippedSourceIds.includes(source.id));
  const channelIds = [...new Set((state.selectedBundle?.sources ?? []).map((source) => source.defaultDriveChannelId))];
  const femVolumes = state.ingestRecord?.evidence?.fem_air_volumes ?? [];
  const solveReason = importedSubmissionBlocker(state, solveStore);
  const solve = async () => {
    if (solveReason) return;
    setSubmitting(true); setError(null); setStatus(null);
    try {
      await coordinator.runImported(buildImportedSubmission(useCadReturnStore.getState()));
      setStatus('CAD import solve submitted to Jobs.');
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setSubmitting(false); }
  };
  const driftSources = new Set([...state.areaDriftSourceIds, ...(state.ingestRecord?.role_findings ?? [])
    .filter((finding) => String(finding.kind).includes('area-drift'))
    .map((finding) => String(finding.source_id))]);
  const workflow = cadWorkflowView(preferences.cadApplication, fusionStatus);
  const cadApplicationLabel = preferences.cadApplication === 'fusion360' ? 'Autodesk Fusion 360' : 'Onshape';
  const designName = filenameStem(filename);
  const actionLabel = workflow.action === 'update'
    ? 'Send WG changes to Fusion'
    : `Open ${designName === 'waveguide' ? 'waveguide' : designName} in Fusion 360`;
  const canRequestFusionReturn = Boolean(
    fusionStatus?.running && fusionStatus.documentName && fusionStatus.documentId
    && fusionStatus.link && identity?.designId,
  );

  return <div className="cadlink-panel panel-scroll">
    <section className="cad-workflow cad-send">
      <header className="cad-workflow-header"><span className="cad-step">1</span><div><h3>WG → CAD</h3><p>Open or update this parametric WG design.</p></div><button className="link-button" onClick={() => requestSettings('cad')}>{cadApplicationLabel} · Change</button></header>
      <div className={`cad-connection cad-connection-${workflow.state}`}>
        <span className="cad-connection-dot" aria-hidden="true"/>
        <div><h3>{workflow.headline}</h3><p>{workflow.detail}</p></div>
      </div>
      {workflow.action && !confirmWgOverwrite && <button className="primary cad-primary-action" disabled={sending} onClick={() => void send()}>{sending ? (workflow.action === 'update' ? 'Updating…' : 'Opening…') : actionLabel}</button>}
      {confirmWgOverwrite && <div className="cad-direction-alert" role="alert"><div><b>Both WG and Fusion changed</b><span>This rebuilds only the linked waveguide from WG. Separate cabinet and mid-woofer bodies stay in Fusion, but direct edits to the linked waveguide are replaced.</span></div><div className="cad-confirm-actions"><button onClick={() => setConfirmWgOverwrite(false)}>Cancel</button><button className="primary" disabled={sending} onClick={() => void send()}>Continue: send WG changes</button></div></div>}
    </section>
    <section className="cad-workflow cad-return-workflow">
    <header className="cad-workflow-header"><span className="cad-step">2</span><div><h3>CAD → SIMULATION</h3><p>Bring Fusion geometry and source tags into WG.</p></div><button disabled={loading || ingesting} onClick={() => void refresh()}><Icon name="reset"/>{loading ? 'Loading…' : 'Refresh'}</button></header>
    {fusionStatus?.fusionChangesAvailable && <div className="cad-direction-alert"><div><b>Fusion geometry has changed</b><span>The active Fusion body or source setup differs from the last design returned to WG.</span></div><button className="primary" disabled={!canRequestFusionReturn || requestingReturn} onClick={() => void bringFromFusion()}>{requestingReturn ? 'Requesting…' : 'Bring Fusion changes into WG'}</button></div>}
    {!fusionStatus?.fusionChangesAvailable && canRequestFusionReturn && <button className="cad-secondary-action" disabled={requestingReturn} onClick={() => void bringFromFusion()}>{requestingReturn ? 'Requesting…' : 'Refresh geometry from Fusion'}</button>}
    {error && <div className="cad-alert" role="alert">{error}</div>}
    {state.ingestStaleReason && <div className="cad-alert" role="status">{state.ingestStaleReason} Re-ingest before solving.</div>}
    {status && <div className="cad-status-strip" role="status">{status}</div>}
    {state.selectedBundle?.readable && <div className="cad-return-quick-action">
      <div><b>{state.selectedBundle.documentName ?? state.selectedBundle.name}</b><span>{state.ingestRecord ? 'Choose what the viewport displays.' : 'Prepare this Fusion return for the viewport and solver.'}</span></div>
      {state.ingestRecord ? <div className="cad-viewport-source-buttons" aria-label="CAD viewport source">
        <button className={!viewportMesh.active ? 'active' : ''} aria-pressed={!viewportMesh.active} onClick={() => importedMeshStore.showParametric()}>Parametric</button>
        <button className={viewportMesh.active ? 'active' : ''} aria-pressed={viewportMesh.active} onClick={() => void showIngestedMeshInViewport(state.ingestRecord!.ingest_id, state.selectedBundle!.documentName || state.selectedBundle!.name, state.ingestRecord!.symmetry.cut_planes ?? [])}>Fusion CAD</button>
      </div> : <button className="primary" disabled={ingesting} onClick={() => void ingest()}>{ingesting ? 'Preparing…' : 'Prepare simulation'}</button>}
    </div>}
    {!loading && error?.includes('No workspace folder') && <div className="empty-state"><b>No workspace selected</b><span>Choose a workspace folder in Settings, then refresh this panel.</span></div>}
    {!loading && !error && bundles.length === 0 && <div className="empty-state"><b>No CAD returns</b><span>Returned bundles appear under the selected workspace’s wgreturn folder.</span></div>}
    {bundles.length > 0 && <div className="cad-bundle-list" role="list" aria-label="Designs returned from CAD">{bundles.map((bundle) => <button
      key={bundle.bundlePath}
      role="listitem"
      className={state.selectedBundle?.bundlePath === bundle.bundlePath ? 'selected' : ''}
      disabled={!bundle.readable || ingesting}
      onClick={() => { state.selectBundle(bundle); importedMeshStore.showParametric(); }}
      title={!bundle.readable ? bundle.reason ?? 'Manifest is unreadable' : undefined}
    ><b>{bundle.documentName ?? bundle.name}</b><span>{bundle.readable ? `${bundle.sourceCount} sources · ${bundle.instanceCount} linked instances` : bundle.reason ?? 'Manifest is unreadable'}</span><time>{new Date(bundle.modifiedAt).toLocaleString()}</time></button>)}</div>}

    {state.selectedBundle?.readable && <>
      <section className="cad-section cad-sizing">
        <header><div><h3>Mesh detail</h3><p>Smaller values are finer and support higher frequencies. Curved CAD faces receive bounded extra refinement automatically.</p></div>{state.ingestRecord && <button className="primary" disabled={ingesting} onClick={() => void ingest()}>{ingesting ? 'Preparing…' : 'Rebuild mesh'}</button>}</header>
        <NumberField label="Cabinet & waveguide" unit="mm" value={state.rigidSizeMm} min={0.01} step={0.5} precision={2} description="Maximum target size for rigid CAD surfaces; tight curvature may be refined further." onCommit={state.setRigidSize}/>
        <NumberField label="Size transition" unit="mm" value={state.transitionMm} min={0.01} step={0.5} precision={2} description="Maximum size transition between adjacent mesh regions." onCommit={state.setTransition}/>
        {state.selectedBundle.sources.map((source) => <div className={`cad-source ${state.skippedSourceIds.includes(source.id) ? 'skipped' : ''}`} key={source.id}>
          <NumberField label={`${source.role} source`} unit="mm" value={state.sourceSizesMm[source.id] ?? source.suggestedResolutionMm} min={0.01} step={0.25} precision={2} description={`${source.id} · suggested ${source.suggestedResolutionMm} mm`} disabled={state.skippedSourceIds.includes(source.id)} onCommit={(value) => state.setSourceSize(source.id, value)}/>
          {!source.required && <ToggleRow id={`skip-${source.id}`} label="Skip optional source" help="Exclude this optional source from ingestion and the solve. This creates a blocking finding." checked={state.skippedSourceIds.includes(source.id)} onChange={(checked) => state.setSkipped(source.id, checked)}/>} 
          {driftSources.has(source.id) && <ToggleRow id={`drift-${source.id}`} label="Allow recorded area drift" help="Explicitly accept the source-area mismatch and re-ingest. The override remains a finding that must be acknowledged." checked={state.areaDriftOverrides.includes(source.id)} onChange={(checked) => state.setAreaDriftOverride(source.id, checked)}/>} 
        </div>)}
      </section>

      {state.ingestRecord && <>
        <RecordSummary record={state.ingestRecord}/>
        <section className="cad-section cad-findings">
          <header><h3>Findings</h3>{blockingFindings(state.ingestRecord).length > 1 && <button onClick={state.acknowledgeAllBlocking}>Acknowledge all {blockingFindings(state.ingestRecord).length}</button>}</header>
          {state.ingestRecord.findings.length === 0 ? <p>No findings. This ingestion needs no acknowledgements.</p> : state.ingestRecord.findings.map((finding) => <label key={finding.id} className={finding.blocking ? 'blocking' : ''}>
            {finding.blocking && <input type="checkbox" checked={state.acknowledgedFindingIds.includes(finding.id)} onChange={(event) => state.acknowledge(finding.id, event.target.checked)}/>}<span><b>{finding.kind.replaceAll('-', ' ')}</b><small>{findingDetail(finding)}</small></span>
          </label>)}
        </section>
        <section className="cad-section">
          <header><h3>Drive channels</h3></header>
          <p>Assign two sources to the same channel to drive them together.</p>
          {activeSources.map((source) => {
            const channel = state.driveChannels.find((item) => item.source_ids.includes(source.id));
            return <div className="cad-channel-row" key={source.id}><b>{source.id}</b><select aria-label={`Drive channel for ${source.id}`} value={channel?.id ?? ''} onChange={(event) => state.setSourceChannel(source.id, event.target.value)}>{channelIds.map((id) => <option value={id} key={id}>{id}</option>)}</select></div>;
          })}
          {state.driveChannels.map((channel) => {
            const driverForm = state.channelDrivers[channel.id];
            const driverEligible = channel.source_ids.length === 1 && channel.motion === 'normal';
            return <div key={channel.id}>
              <div className="cad-channel-summary"><span>{channel.id} · {channel.source_ids.join(' + ')}</span><select aria-label={`Motion for ${channel.id}`} value={channel.motion} onChange={(event) => state.setChannelMotion(channel.id, event.target.value as 'normal' | 'axial')}><option value="normal">Normal motion</option><option value="axial">Axial motion</option></select></div>
              {driverEligible
                ? <ToggleRow id={`cad-driver-${channel.id}`} label={`Driver T/S · ${channel.id}`} help="Voltage-driven Thiele-Small coupling. The channel's levels become absolute at the drive voltage and its impedance chart becomes the electrical input impedance in ohms." checked={driverForm?.enabled ?? false} onChange={(checked) => state.setChannelDriverEnabled(channel.id, checked)}/>
                : null}
              {driverEligible && driverForm?.enabled && <DriverFields channel={channel} form={driverForm} onField={(field, value) => state.setChannelDriverField(channel.id, field, value)}/>}
            </div>;
          })}
          {state.driveChannels.some((channel) => state.channelDrivers[channel.id]?.enabled)
            && <NumberField label="Drive voltage" unit="V" value={state.driveVoltageV} min={0.01} step={0.1} precision={2} description="RMS voltage applied to every driver channel (2.83 V ≈ 1 W into 8 Ω)" onCommit={state.setDriveVoltage}/>}
          {state.driveChannels.length >= 2 && <>
            <ToggleRow id="cad-combine" label="Combined output (LR4 sum)" help="Append a time-aligned LR4 crossover sum of the drive channels as one more result channel. The chain runs lowest band first, ordered by the sources' return roles (LF → MF → HF)." checked={state.combineEnabled} onChange={state.setCombineEnabled}/>
            {state.combineEnabled && combineChain(state).map((pair) => <NumberField key={pair.key} label={`${pair.lower} → ${pair.upper}`} unit="Hz" value={pair.hz} min={1} step={50} precision={0} description="Crossover between adjacent channels" onCommit={(value) => state.setCombineCrossover(pair.key, value)}/>)}
            {state.combineEnabled && <ToggleRow id="cad-combine-level" label="Level match members" help="Equalise member band levels before summing. Defaults off when every member carries a driver model — real voltage-driven levels should not be re-equalised." checked={state.combineLevelMatch ?? combineLevelMatchDefault(state)} onChange={state.setCombineLevelMatch}/>}
          </>}
        </section>
        <section className="cad-section">
          <header><h3>Explicit solve sweep</h3><span className="cad-status">Metal · full 3-D · free space</span></header>
          {femVolumes.length > 0 && <ToggleRow id="cad-exterior-only" label="Exterior-only Phase 2 solve" help="Explicitly exclude the returned FEM air volumes. Phase 2 solves only the exterior Metal free-space problem." checked={state.exteriorOnly} onChange={state.setExteriorOnly}/>} 
          <FrequencySweepControls idPrefix="cad-import" context="imported"/>
          {solveStore.frequencyMode === 'range' && <div className="cad-sweep-grid">
            <NumberField label="Start" unit="Hz" value={state.frequencyStartHz} min={1} step={10} precision={0} onCommit={(frequencyStartHz) => state.setSweep({ frequencyStartHz })}/>
            <NumberField label="End" unit="Hz" value={state.frequencyEndHz} min={1} step={100} precision={0} onCommit={(frequencyEndHz) => state.setSweep({ frequencyEndHz })}/>
            <NumberField label="Points" value={state.frequencyCount} min={1} max={401} step={1} precision={0} onCommit={(frequencyCount) => state.setSweep({ frequencyCount })}/>
          </div>}
        </section>
        <div className="cad-solve-bar"><div>{solveReason ?? 'All blocking findings acknowledged. Ready to submit.'}</div><button className="primary" disabled={Boolean(solveReason) || submitting || ingesting} onClick={() => void solve()}>{submitting ? 'Submitting…' : 'Solve CAD import'}</button></div>
      </>}
    </>}
    </section>
  </div>;
}
