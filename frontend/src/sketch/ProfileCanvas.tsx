import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { inspectProfileSamples } from '../api/designIo';
import type { ProfileSamples } from '../api/profileSamples';
import { failureWords } from '../api/httpError';
import {
  angleField, commitProfileEdit, currentEditOrigin, isCurrentOrigin, isSharedProfile, profileRequestKey, useCurrentProfileCurves, useProfileEdit,
} from '../design/customProfileEdit';
import { ProfileEditPreviewPanel } from '../design/FreeformEditors';
import { NumberField } from '../design/NumberField';
import { parameterPanelValidity } from '../design/parameterPanelValidity';
import { useDesignStore, type DesignDocument } from '../stores/design';
import { createProfileAdapter, equalProfiles, parseAnchor, profileBinding, profilePoints, type ProfileCommand, type ProfilePlaneChoice, type ProfilePresentation } from './adapters/profile';
import { ProfileController } from './profileController';
import { getProfileDraft, revertProfileFieldDrafts, setProfileDraft, useProfileDraftBlocker } from './profileDraft';
import { ProjectHistoryAuthority } from './workspace/projectHistoryAuthority';
import { PlainController } from './workspace/plainController';
import { Workspace } from './workspace/Workspace';
import './profileCanvas.css';

/** Generated samples are physical mm, including Scale. Never replace authored
 * samples with this export: the mesher curve query is the authoring authority. */
export function useGeneratedProfile(design: DesignDocument, revision: number, identity: number) {
  const [result, setResult] = useState<{ key: string; identity: number; samples: ProfileSamples; scale: number }>();
  const [failure, setFailure] = useState<{ key: string; message: string }>();
  const key = `${identity}:${revision}`;
  useEffect(() => {
    const origin = currentEditOrigin(), abort = new AbortController();
    // The content key guards the export too, even though its wire format is CSV.
    const requestKey = profileRequestKey(origin, design);
    let activeKey: string | undefined = requestKey;
    const timer = setTimeout(() => {
      void inspectProfileSamples(design, revision, abort.signal).then(samples => {
        if (!abort.signal.aborted && activeKey === requestKey && isCurrentOrigin(origin)) { setResult({ key, identity, samples, scale: design.scale }); setFailure(undefined); }
      }, reason => {
        if (!abort.signal.aborted && activeKey === requestKey && isCurrentOrigin(origin)) setFailure({ key, message: failureWords(reason) });
      });
    }, 300);
    return () => { activeKey = undefined; clearTimeout(timer); abort.abort(); };
  }, [design, revision, identity, key]);
  const visible = result?.identity === identity ? result : undefined;
  const error = failure?.key === key ? failure.message : undefined;
  return { samples: visible?.samples, scale: visible?.scale, stale: visible?.key !== key || Boolean(error), error };
}

export function ProfileCanvas() {
  const design = useDesignStore(state => state.design), revision = useDesignStore(state => state.designRevision), identity = useDesignStore(state => state.loadGeneration);
  const legacy = design.formula === 'LOOKUP';
  const invalidInputs = useSyncExternalStore(parameterPanelValidity.subscribe, parameterPanelValidity.getSnapshot, parameterPanelValidity.getSnapshot);
  const currentCurves = useCurrentProfileCurves(), generated = useGeneratedProfile(design, revision, identity), edit = useProfileEdit();
  const [state, setState] = useState(design), [session, setSession] = useState<PlainController<DesignDocument, DesignDocument, ProfileCommand>>();
  const [selection, setSelection] = useState<string[]>([]), [plane, setPlane] = useState<ProfilePlaneChoice>('both');
  const [scaled, setScaled] = useState(false), [showGenerated, setShowGenerated] = useState(true), [dragging, setDragging] = useState(false);
  const [fitRequest, setFitRequest] = useState(0), [fieldVersion, setFieldVersion] = useState(0);
  const [actionError, setActionError] = useState<string>();
  const [propertiesHost, setPropertiesHost] = useState<HTMLDivElement | null>(null);
  const draftOwner = useRef(Symbol('profile-canvas'));
  const setDraft = useCallback((value: Parameters<typeof setProfileDraft>[0]) => setProfileDraft(value, draftOwner.current), []);
  const anyDraft = useProfileDraftBlocker();
  const curveError = legacy ? undefined : currentCurves.error;
  const host = useRef<HTMLDivElement>(null), latest = useRef({ edit, dragging, invalidInputs, curveError }); latest.current = { edit, dragging, invalidInputs, curveError };
  const lastValid = useRef<{ identity: number; design: DesignDocument } | undefined>(undefined);
  if (!currentCurves.stale && currentCurves.curves && !currentCurves.error && !edit.preview && !dragging) lastValid.current = { identity, design };
  const presentation = useRef<ProfilePresentation>({});
  const adapter = useMemo(() => createProfileAdapter(() => presentation.current, legacy ? 'lookup' : 'freeform'), [legacy]);
  const selected = selection.flatMap(id => parseAnchor(id) ?? []).filter(anchor => !isSharedProfile(design) || anchor.plane === 'H')[0];
  const preview = edit.preview;
  const previewCurves = preview?.validation.status === 'ready' ? preview.validation.value : undefined;
  const displayState = preview?.after ?? state;
  const open = useCallback(() => { host.current?.scrollIntoView?.({ block: 'nearest' }); host.current?.querySelector<SVGSVGElement>('svg[role="application"]')?.focus(); }, []);
  const revert = useCallback(() => {
    latest.current.edit.cancel();
    void session?.endDrag(true);
    // NumberField's own Escape path restores its local draft without a write.
    const panel = host.current?.closest('.param-panel') ?? host.current;
    if (panel) revertProfileFieldDrafts(panel);
    setFieldVersion(version => version + 1); setDraft(null);
    const valid = lastValid.current;
    if (latest.current.curveError && valid?.identity === useDesignStore.getState().loadGeneration) {
      try { commitProfileEdit(currentEditOrigin(), useDesignStore.getState().design, valid.design); }
      catch (reason) { setActionError(failureWords(reason)); }
    }
  }, [session, setDraft]);

  useLayoutEffect(() => {
    if (design.formula !== 'FREEFORM' && !legacy) return;
    const authority = new ProjectHistoryAuthority(profileBinding(() => { setSelection([]); setDraft(null); }, legacy ? 'lookup' : 'freeform'));
    const options = { adapter, initial: authority.current, authority, changed: setState, preview: setState };
    const controller = new ProfileController({ ...options, presentation: () => presentation.current,
      propose: (label, builder) => latest.current.edit.propose(label, builder), drafting: setDragging,
      blocked: () => Boolean(latest.current.edit.preview || latest.current.invalidInputs),
    });
    const unsubscribe = controller.subscribeHistory(() => { setDragging(false); });
    setState(authority.current); setSession(controller);
    return () => { unsubscribe(); controller.destroy(); setDraft(null); };
  }, [adapter, identity, design.formula, legacy, setDraft]);

  const unfinished = Boolean(preview || dragging || invalidInputs || curveError || adapter.status(design).kind === 'unfinished');
  useLayoutEffect(() => {
    setDraft(unfinished ? { message: `Custom profile ${preview?.status === 'refused' || invalidInputs || curveError ? 'has an invalid draft' : 'is unfinished'}. Open sketch or Revert to last valid before Solve.`, open, revert } : null);
  }, [unfinished, preview?.status, curveError, open, revert, setDraft]);
  useEffect(() => {
    if (!preview) return;
    const cancelGesture = (event: KeyboardEvent) => {
      if (event.key === 'Escape' || (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault(); event.stopPropagation(); edit.cancel();
      }
    };
    window.addEventListener('keydown', cancelGesture, true);
    return () => window.removeEventListener('keydown', cancelGesture, true);
  }, [Boolean(preview), edit.cancel]);
  useEffect(() => { setSelection([]); setPlane('both'); }, [identity]);
  useEffect(() => { setFitRequest(value => value + 1); }, [scaled, Boolean(currentCurves.curves), Boolean(generated.samples)]);

  presentation.current = {
    curves: legacy ? undefined : previewCurves ?? currentCurves.curves, stale: legacy ? false : preview?.after ? !previewCurves : currentCurves.stale,
    error: legacy ? undefined : preview?.validation.status === 'error' ? preview.validation.message : currentCurves.error,
    before: preview?.after ? currentCurves.curves : undefined,
    generated: generated.samples, generatedScale: generated.scale, generatedStale: generated.stale || Boolean(anyDraft), showGenerated, plane, scaled,
  };
  const command = (action: Parameters<ProfileController['command']>[0]) => { setActionError(undefined); void session?.command(action).catch(reason => setActionError(failureWords(reason))); };
  const anchor = selected && profilePoints(displayState, selected.plane)[selected.index];
  const count = selected ? profilePoints(displayState, selected.plane).length : 0;
  const validateCommand = (action: ProfileCommand) => { const result = adapter.apply(displayState, action); return 'error' in result ? result.error : undefined; };
  const busy = Boolean(preview || dragging || !legacy && currentCurves.stale);
  if (design.formula !== 'FREEFORM' && !legacy) return null;
  const properties = selected && anchor ? <div className="profile-anchor-properties" key={`${selected.plane}:${selected.index}:${fieldVersion}`}>
      <strong>{selected.plane} {selected.index === 0 ? 'throat' : selected.index === count - 1 ? 'mouth' : `point ${selected.index}`}</strong>
      <div className="profile-anchor-fields">
        <div><NumberField label="z (design mm)" value={anchor.t * (displayState.length ?? 120)} precision={15} displayPrecision={4} disabled={busy || selected.index === 0 || !legacy && selected.index === count - 1}
          validate={x => validateCommand({ kind: 'move', ...selected, at: { x, y: anchor.r } })}
          onCommit={x => command({ kind: 'move', ...selected, at: { x, y: anchor.r } })}/></div>
        <div><NumberField label="r (design mm)" value={anchor.r} precision={15} displayPrecision={4} disabled={busy}
          validate={y => validateCommand({ kind: 'move', ...selected, at: { x: anchor.t * (displayState.length ?? 120), y } })}
          onCommit={y => command({ kind: 'move', ...selected, at: { x: anchor.t * (displayState.length ?? 120), y } })}/></div>
        <label>Join <select aria-label="Selected point join" disabled={busy || legacy} value={anchor.join ?? 'smooth'} onChange={event => command({ kind: 'join', ...selected, join: event.target.value as 'smooth' | 'corner' })}><option value="smooth">Smooth</option><option value="corner">Corner</option></select></label>
        {!legacy && (['in', 'out'] as const).map(side => {
          const field = angleField(anchor, selected.index, count, side);
          if (!field || side === 'out' && selected.index > 0 && anchor.join !== 'corner') return null;
          return <div key={side}><NumberField label={`${side === 'in' ? 'Incoming' : 'Outgoing'} angle`} unit="°" value={anchor[field] ?? undefined} optional min={-90} max={90} precision={4} disabled={busy}
            validate={value => validateCommand({ kind: 'angle', ...selected, side, value })}
            onCommit={value => command({ kind: 'angle', ...selected, side, value })} onClear={() => command({ kind: 'angle', ...selected, side })}/></div>;
        })}
      </div>
      <button type="button" className="btn" disabled={busy || (legacy ? count <= 2 : selected.index === 0 || selected.index === count - 1)} onClick={() => command({ kind: 'delete', ...selected })}>Delete point</button>
      <p className="profile-canvas-caption">{legacy ? 'Legacy LOOKUP keeps straight spans. Join and angle tools are disabled.' : 'Throat z and mouth z are fixed. Change Length in the table to move the mouth. Tangent handles are display only.'}</p>
    </div> : null;
  presentation.current.properties = () => properties ? [{ id: 'profile-anchor', title: 'Selected profile point', render: () => properties }] : [];
  return <div ref={host} className="profile-canvas" role="group" aria-label="Custom profile canvas">
    <div className="profile-canvas-options">
      <label>Profile planes <select aria-label="Profile planes" value={plane} onChange={event => { setPlane(event.target.value as ProfilePlaneChoice); setSelection([]); }}>
        <option value="both">H and V</option><option value="H">Horizontal (H)</option><option value="V">Vertical (V)</option>
      </select></label>
      <label><input type="checkbox" checked={showGenerated} onChange={event => setShowGenerated(event.target.checked)} /> Generated profile</label>
      {design.scale !== 1 && <label>Coordinates <select aria-label="Profile coordinates" value={scaled ? 'scaled' : 'design'} onChange={event => setScaled(event.target.value === 'scaled')}>
        <option value="design">Design mm</option><option value="scaled">Scaled mm</option>
      </select></label>}
    </div>
    <p className="profile-canvas-caption">{legacy ? 'Authored: exact legacy LOOKUP polyline (corners only)' : 'Authored: mesher curve'} · Generated: thin line{generated.samples && equalProfiles(generated.samples) ? ' · Generated H = V' : ''}{isSharedProfile(design) ? ' · Authored H = V (stored V inactive)' : legacy ? ' · Authored H = V' : ' · H blue / V amber'}{design.scale !== 1 ? ` · ${scaled ? 'Plot in scaled mm' : 'Design mm before Scale'} (Scale = ${design.scale}).` : ''}</p>
    <div role="status" className="profile-canvas-freshness" data-stale={!legacy && currentCurves.stale || generated.stale || unfinished || undefined}>
      {(!legacy ? currentCurves.error : undefined) ?? generated.error ?? (invalidInputs ? 'Fix the marked values. Showing the last valid profile.' : !legacy && currentCurves.stale || generated.stale ? 'Updating… — last valid profile' : '')}
    </div>
    {anyDraft && <div className="sketch-conflict-banner" role="status"><span>Custom profile draft — Solve is blocked. {generated.samples ? 'Showing the last valid generated shape.' : 'No validated generated profile yet.'}</span>
      <button type="button" className="btn" onClick={() => getProfileDraft()?.open()}>Open sketch</button><button type="button" className="btn" onClick={() => getProfileDraft()?.revert()}
        disabled={Boolean(curveError && lastValid.current?.identity !== identity && !preview && !dragging && !invalidInputs)}
        title={curveError && lastValid.current?.identity !== identity ? 'This document has no previously validated profile. Fix its table values.' : undefined}>Revert to last valid</button></div>}
    <Workspace adapter={adapter} state={displayState} session={session} selection={selection} onSelectionChange={setSelection} fitRequest={fitRequest} slots={{ properties: propertiesHost }}/>
    {actionError && <p className="field-error" role="alert">{actionError}</p>}
    <div ref={setPropertiesHost}/>
    <ProfileEditPreviewPanel edit={edit}/>
  </div>;
}
