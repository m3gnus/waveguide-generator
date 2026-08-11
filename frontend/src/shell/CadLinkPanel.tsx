import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { CadLinkApiError, ingestReturn, listReturns, type CadReturnBundle, type CadReturnFinding, type CadReturnIngestRecord } from '../api/cadlink';
import { NumberField } from '../design/NumberField';
import { FrequencySweepControls, ToggleRow } from '../design/SolveOptionsSections';
import type { ImportedSolveSubmission } from '../jobs/actions';
import {
  acknowledgedFindingWire,
  blockingFindings,
  unacknowledgedBlocking,
  useCadReturnStore,
} from '../stores/cadReturn';
import { parseFrequencyList, useSolveOptionsStore } from '../stores/solveOptions';
import { jobsCoordinatorBridge } from './JobsCoordinator';
import { Icon } from './icons';
import './cadLinkPanel.css';

const FRESHNESS_COPY: Record<string, string> = {
  current: 'Current — unchanged fingerprint and the saved design still agree with this generator.',
  body_modified: 'CAD body changed after linking. This returned geometry remains the solve truth.',
  missing_design: 'The linked design is not in this workspace registry; this is normal for a return from another machine.',
  design_changed: 'The linked Waveguide Generator design has changed since this geometry was exported.',
  generator_changed: 'The same saved design would export differently with the current generator.',
  unknown: 'Freshness could not be established from the available evidence.',
  unlinked: 'Unlinked return — no Waveguide Generator design identity was attached in CAD.',
};

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

export function buildImportedSubmission(
  state: ReturnType<typeof useCadReturnStore.getState>,
): ImportedSolveSubmission {
  const record = state.ingestRecord;
  if (!record) throw new Error('Ingest a CAD return before solving.');
  const solveStore = useSolveOptionsStore.getState();
  const options = solveStore.options() as ImportedSolveSubmission['options'];
  if (solveStore.frequencyMode === 'range') {
    options.frequency_range = [state.frequencyStartHz, state.frequencyEndHz];
    options.num_frequencies = state.frequencyCount;
  }
  options.engine = 'metal';
  options.symmetry = 'auto';
  return {
    geometry: {
      type: 'imported',
      ingest_id: record.ingest_id,
      manifest_sha256: record.manifest_sha256,
      artifact_sha256: record.artifact_sha256,
      drive_channels: state.driveChannels.map((channel) => ({ ...channel, source_ids: [...channel.source_ids] })),
      mesh: {
        rigid_size_mm: state.rigidSizeMm,
        transition_mm: state.transitionMm,
        source_size_mm: Object.fromEntries(Object.entries(state.sourceSizesMm).filter(([id]) => !state.skippedSourceIds.includes(id))),
      },
      acknowledged_findings: acknowledgedFindingWire(record, state.acknowledgedFindingIds),
      skipped_source_ids: [...state.skippedSourceIds],
      exterior_only: state.exteriorOnly,
    },
    options,
  };
}

export function CadLinkPanel() {
  const state = useCadReturnStore();
  const solveStore = useSolveOptionsStore();
  const coordinator = useSyncExternalStore(jobsCoordinatorBridge.subscribe, jobsCoordinatorBridge.getSnapshot, jobsCoordinatorBridge.getSnapshot);
  const [bundles, setBundles] = useState<CadReturnBundle[]>([]);
  const [loading, setLoading] = useState(true);
  const [ingesting, setIngesting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const response = await listReturns();
      setBundles(response.items);
      const selected = useCadReturnStore.getState().selectedBundle;
      if (selected) {
        const current = response.items.find((bundle) => bundle.bundlePath === selected.bundlePath);
        useCadReturnStore.getState().refreshSelectedBundle(current ?? null);
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

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

  const unacknowledged = unacknowledgedBlocking(state);
  const activeSources = (state.selectedBundle?.sources ?? []).filter((source) => !state.skippedSourceIds.includes(source.id));
  const channelIds = [...new Set((state.selectedBundle?.sources ?? []).map((source) => source.defaultDriveChannelId))];
  const rangeInvalid = solveStore.frequencyMode === 'range' && (
    !(state.frequencyStartHz > 0) || state.frequencyEndHz <= state.frequencyStartHz || state.frequencyCount < 1 || state.frequencyCount > 401
  );
  const listInvalid = solveStore.frequencyMode === 'list' && parseFrequencyList(solveStore.frequencyListText).frequencies === null;
  const femVolumes = state.ingestRecord?.evidence?.fem_air_volumes ?? [];
  const requiredFem = femVolumes.some((volume) => !volume || typeof volume !== 'object' || (volume as Record<string, unknown>).required !== false);
  const solveReason = !state.ingestRecord ? 'Ingest the selected bundle first.'
    : state.needsIngest ? state.ingestStaleReason ?? 'Sizing or source selection changed. Re-ingest before solving.'
      : unacknowledged.length ? `Acknowledge ${unacknowledged.length} blocking finding${unacknowledged.length === 1 ? '' : 's'} before solving.`
        : requiredFem && !state.exteriorOnly ? 'This return includes FEM air volumes. Explicitly choose an exterior-only Phase 2 solve.'
        : rangeInvalid || listInvalid ? 'Enter a valid explicit frequency sweep.'
          : !state.driveChannels.length ? 'At least one drive channel is required.' : null;
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

  return <div className="cadlink-panel panel-scroll">
    <div className="cadlink-toolbar"><b>Returned bundles</b><span className="spacer"/><button disabled={loading || ingesting} onClick={() => void refresh()}><Icon name="reset"/>{loading ? 'Loading…' : 'Refresh'}</button></div>
    {error && <div className="cad-alert" role="alert">{error}</div>}
    {state.ingestStaleReason && <div className="cad-alert" role="status">{state.ingestStaleReason} Re-ingest before solving.</div>}
    {status && <div className="cad-status-strip" role="status">{status}</div>}
    {!loading && error?.includes('No workspace folder') && <div className="empty-state"><b>No workspace selected</b><span>Choose a workspace folder in Settings, then refresh this panel.</span></div>}
    {!loading && !error && bundles.length === 0 && <div className="empty-state"><b>No CAD returns</b><span>Returned bundles appear under the selected workspace’s wgreturn folder.</span></div>}
    {bundles.length > 0 && <div className="cad-bundle-list" role="list" aria-label="CAD return bundles">{bundles.map((bundle) => <button
      key={bundle.bundlePath}
      role="listitem"
      className={state.selectedBundle?.bundlePath === bundle.bundlePath ? 'selected' : ''}
      disabled={!bundle.readable || ingesting}
      onClick={() => state.selectBundle(bundle)}
      title={!bundle.readable ? bundle.reason ?? 'Manifest is unreadable' : undefined}
    ><b>{bundle.documentName ?? bundle.name}</b><span>{bundle.readable ? `${bundle.sourceCount} sources · ${bundle.instanceCount} linked instances` : bundle.reason ?? 'Manifest is unreadable'}</span><time>{new Date(bundle.modifiedAt).toLocaleString()}</time></button>)}</div>}

    {state.selectedBundle?.readable && <>
      <section className="cad-section cad-sizing">
        <header><h3>Ingestion sizing</h3><button className="primary" disabled={ingesting} onClick={() => void ingest()}>{ingesting ? 'Ingesting…' : state.ingestRecord ? 'Re-ingest' : 'Ingest bundle'}</button></header>
        <p>Every mesh size is explicit. Suggested source values come from the return manifest.</p>
        <NumberField label="Rigid surface" unit="mm" value={state.rigidSizeMm} min={0.01} step={0.5} precision={2} description="Coarsest source resolution, used only as an editable suggestion." onCommit={state.setRigidSize}/>
        <NumberField label="Transition" unit="mm" value={state.transitionMm} min={0.01} step={0.5} precision={2} onCommit={state.setTransition}/>
        {state.selectedBundle.sources.map((source) => <div className={`cad-source ${state.skippedSourceIds.includes(source.id) ? 'skipped' : ''}`} key={source.id}>
          <NumberField label={`${source.role} · ${source.id}`} unit="mm" value={state.sourceSizesMm[source.id] ?? source.suggestedResolutionMm} min={0.01} step={0.25} precision={2} description={`Suggested ${source.suggestedResolutionMm} mm`} disabled={state.skippedSourceIds.includes(source.id)} onCommit={(value) => state.setSourceSize(source.id, value)}/>
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
          {state.driveChannels.map((channel) => <div className="cad-channel-summary" key={channel.id}><span>{channel.id} · {channel.source_ids.join(' + ')}</span><select aria-label={`Motion for ${channel.id}`} value={channel.motion} onChange={(event) => state.setChannelMotion(channel.id, event.target.value as 'normal' | 'axial')}><option value="normal">Normal motion</option><option value="axial">Axial motion</option></select></div>)}
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
  </div>;
}
