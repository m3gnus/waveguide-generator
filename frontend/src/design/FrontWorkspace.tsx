/**
 * The front workspace of a design's Sources and baffle assembly.
 *
 * The workspace edits the open design's `assembly` through project history:
 * one control action or one drag is one Undo step, and Undo, Redo, New and
 * Open rehydrate it. Looking changes nothing: a design without an assembly
 * shows a horn-only one held here, and only the first real edit writes it.
 * Loading a design without an assembly closes the workspace. The server
 * supplies the live context (outlines, mouth edges, resolved woofer centres)
 * for each assembly revision on screen.
 *
 * The launcher lives with the design's wall and enclosure controls.
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { assemblyContextRefused, assemblyProblemText, fetchAssemblyContext } from '../api/assembly';
import { DeveloperDetails } from '../shell/DeveloperDetails';
import { useModalDialogFocus } from '../shell/dialogFocus';
import { EmptyState } from '../shell/EmptyState';
import { useOptionalSolveControl } from '../shell/JobsCoordinator';
import { Notice } from '../shell/Notice';
import { frontViewAdapter, frontViewBinding, placementProblems, type FrontCommand, type FrontViewContext } from '../sketch/adapters/frontView';
import { PlainController } from '../sketch/workspace/plainController';
import { ProjectHistoryAuthority } from '../sketch/workspace/projectHistoryAuthority';
import { Workspace } from '../sketch/workspace/Workspace';
import { emptyAssembly, type DesignAssembly } from '../stores/assembly';
import { designModelCurrent, prepareDesignModel, useAssemblyBuildStore, type AssemblyBuildState } from '../stores/assemblyBuild';
import { useDesignModelStore } from '../stores/importedModel';
import { currentDocumentLoad, serializeDesign, subscribeRevision, useDesignStore } from '../stores/design';
import { DiaphragmWorkspace } from './DiaphragmWorkspace';
import { DiaphragmSelect } from './DiaphragmControls';
import { diaphragmFor, revertDiaphragm, unfinishedDiaphragms } from '../sketch/adapters/assemblyDiaphragm';
import { commitAssembly, HF_RADIATOR_ID, setHfSource, setWooferShape } from '../stores/assembly';
import { ExperimentalBadge } from './ExperimentalBadge';
import { frontViewProperties } from './FrontProperties';
import './frontWorkspace.css';
import './sourceContourEditor.css';

/** How long the design must rest before the front context is refreshed. */
export const FRONT_CONTEXT_DELAY_MS = 150;

const visibility = {
  open: false, listeners: new Set<() => void>(),
  getSnapshot: () => visibility.open,
  subscribe: (listener: () => void) => { visibility.listeners.add(listener); return () => { visibility.listeners.delete(listener); }; },
  set(open: boolean) { visibility.open = open; visibility.listeners.forEach(listener => listener()); },
};

/** Open the front workspace. The design itself is left exactly as it is. */
export function openFrontWorkspace(): void { visibility.set(true); }

export function closeFrontWorkspace(): void { visibility.set(false); }

export function FrontWorkspaceLauncher() {
  const woofers = useDesignStore(state => state.design.assembly?.woofers?.length ?? 0);
  return <div className="front-launch" data-control-reveal-id="front-workspace-launch">
    <div className="front-launch-action">
      <button className="btn-primary" type="button" aria-haspopup="dialog" onClick={openFrontWorkspace}>Sources and baffle…</button>
      <ExperimentalBadge/>
    </div>
    <small>{woofers > 0 ? `${woofers} ${woofers === 1 ? 'woofer' : 'woofers'} on the front baffle, solved with the horn from Sources and baffle.`
      : "Woofers on the enclosure's front baffle, solved with the horn."}</small>
  </div>;
}

export function FrontWorkspaceDialog() {
  const open = useSyncExternalStore(visibility.subscribe, visibility.getSnapshot, visibility.getSnapshot);
  return open ? createPortal(<FrontWorkspace onClose={closeFrontWorkspace}/>, document.body) : null;
}

/** Why the front context did not arrive for one revision; `refused` means the server's words are about the design. */
interface FrontContextFailure { revision: number; text: string; detail: string; refused: boolean }
interface FrontContextState { resolved: FrontViewContext | undefined; checkedRevision: number | null; checkedLoad: number | null; failure: FrontContextFailure | null; retry(): void }

/** The latest front context for the design on screen, refreshed when the design rests. */
function useFrontContext(blank: DesignAssembly, fetcher: typeof fetch): FrontContextState {
  const design = useDesignStore(state => state.design);
  const revision = useDesignStore(state => state.designRevision);
  const dragging = useDesignStore(state => !!state.dragSnapshot);
  const [resolved, setResolved] = useState<FrontViewContext>();
  const [checkedRevision, setCheckedRevision] = useState<number | null>(null);
  const [checkedLoad, setCheckedLoad] = useState<number | null>(null);
  const [failure, setFailure] = useState<FrontContextState['failure']>(null);
  const [attempt, setAttempt] = useState(0);
  const latest = useRef(0);
  useEffect(() => {
    const ask = ++latest.current;
    const load = currentDocumentLoad();
    if (dragging) return;
    const assembly = structuredClone(design.assembly ?? blank);
    const request = new AbortController();
    const timer = setTimeout(() => {
      fetchAssemblyContext(serializeDesign({ ...design, assembly }), fetcher, request.signal).then(context => {
        if (ask === latest.current && load === currentDocumentLoad()) { setResolved({ context, assembly }); setCheckedRevision(revision); setCheckedLoad(load); setFailure(null); }
      }, (reason: unknown) => {
        if (ask !== latest.current) return;
        setFailure({ revision, text: assemblyProblemText(reason, 'WG could not update the front view and has recorded the problem.'),
          detail: reason instanceof Error ? reason.message : String(reason), refused: assemblyContextRefused(reason) });
      });
    }, FRONT_CONTEXT_DELAY_MS);
    return () => { clearTimeout(timer); request.abort(); };
  }, [design, revision, dragging, fetcher, attempt]);
  return { resolved, checkedRevision, checkedLoad, failure, retry: () => { setFailure(null); setAttempt(value => value + 1); } };
}

/**
 * What the fit of the design on screen allows, and the line that says so. Only an
 * answer about this revision can refuse. While one is on its way Build and Solve
 * wait; if none can come they go ahead, because the build certifies the fit itself
 * and refuses a woofer that does not fit.
 */
function fitHold(answered: boolean, failure: FrontContextFailure | null, refusal: string | undefined, problems: string[]): { held: boolean; line: string | null } {
  if (answered && problems.length) {
    return { held: true, line: problems.length > 1 ? `${problems[0]} ${problems.length - 1} more under Does not fit.` : problems[0] };
  }
  if (answered) return { held: !!refusal, line: refusal ?? null };
  if (failure?.refused) return { held: true, line: failure.text };
  if (failure) return { held: false, line: 'WG could not check the fit. Build and Solve check it themselves.' };
  return { held: true, line: 'Checking that the woofers fit on the baffle…' };
}

function buildLine(hasAssembly: boolean, status: AssemblyBuildState['status'], published: boolean, current: boolean): string {
  if (!hasAssembly) return 'Add a woofer to build or solve.';
  if (status === 'building') return 'Building the mesh…';
  if (status === 'refused') return current ? 'The last build still matches this design.' : 'Not built.';
  if (current) return 'Built. Ready to solve.';
  return published ? 'Changed since the last build. Solve builds it again.' : 'Not built yet. Solve builds it first.';
}

export function FrontWorkspace({ onClose, fetcher = fetch }: { onClose: () => void; fetcher?: typeof fetch }) {
  const dialog = useModalDialogFocus<HTMLDivElement>({ open: true, onClose });
  const [blank] = useState(() => emptyAssembly());
  const [tab, setTab] = useState('front');
  const { resolved, checkedRevision, checkedLoad, failure, retry } = useFrontContext(blank, fetcher);
  const context = useRef(resolved); context.current = resolved;
  const adapter = useMemo(() => frontViewAdapter(() => context.current,
    frontViewProperties(() => context.current, () => !!useDesignStore.getState().design.assembly, setTab)), []);
  const [state, setState] = useState<DesignAssembly>(() => useDesignStore.getState().design.assembly ?? blank);
  const controller = useMemo(() => {
    const binding = frontViewBinding(blank);
    const authority = new ProjectHistoryAuthority({ ...binding, write: (current, values) => current.hf_source?.kind === 'sketched'
      ? { assembly: current } : binding.write(current, values) });
    return new PlainController<DesignAssembly, DesignAssembly, FrontCommand>({
      initial: authority.current, adapter, authority, changed: setState, preview: setState,
    });
  }, []);
  // Strict Mode remounts this effect at once; only a real unmount ends the session.
  const retiring = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => {
    clearTimeout(retiring.current);
    return () => { retiring.current = setTimeout(() => controller.destroy()); };
  }, [controller]);
  useEffect(() => subscribeRevision(event => {
    if (event.reason === 'load' && !useDesignStore.getState().design.assembly) onClose();
  }), [onClose]);
  const design = useDesignStore(store => store.design);
  const hasAssembly = !!design.assembly;
  const tabs = [{ id: 'front', label: 'Front' }, { id: HF_RADIATOR_ID, label: 'HF diaphragm' },
    ...(design.assembly?.woofers ?? []).filter(w => w.shape.kind === 'sketched').map(w => ({ id: w.id, label: w.label }))];
  const activeTab = tabs.some(t => t.id === tab) ? tab : 'front';
  useEffect(() => { if (activeTab !== tab) setTab(activeTab); }, [activeTab, tab]);
  useEffect(() => { if (activeTab !== 'front') void controller.endDrag(true); }, [activeTab, controller]);
  const unfinished = unfinishedDiaphragms(design.assembly ?? blank);
  const unfinishedReason = unfinished.length ? `Finish or revert the ${unfinished[0].label} sketch before solving.` : null;
  const revert = (id: string) => {
    const current = useDesignStore.getState().design.assembly;
    const shape = current && diaphragmFor(current, id);
    if (!current || !shape) return;
    const next = revertDiaphragm(shape);
    commitAssembly(id === HF_RADIATOR_ID ? setHfSource(current, id, { ...next, kind: next.kind === 'sketched' ? 'sketched' : 'classic' }) : setWooferShape(current, id, { ...next, kind: next.kind === 'sketched' ? 'sketched' : 'piston' }));
  };
  const shown = hasAssembly ? state : blank;
  const build = useAssemblyBuildStore();
  const slot = useDesignModelStore();
  const solveControl = useOptionalSolveControl();
  const [notice, setNotice] = useState('');
  const [selection, setSelection] = useState<string[]>([]);
  const [panel, setPanel] = useState<HTMLElement | null>(null);
  // Refit when the drawing's extent changes, never while a woofer is being placed.
  const extent = JSON.stringify([resolved?.context.baffle ?? null, resolved?.context.mouth_top_mm, resolved?.context.mouth_bottom_mm, resolved?.context.woofers?.length]);
  const fits = useRef({ extent: '', count: 0 });
  if (resolved && fits.current.extent !== extent) fits.current = { extent, count: fits.current.count + 1 };
  const solve = () => {
    setNotice('');
    // The jobs coordinator owns every submission; it reports a refusal itself.
    void solveControl?.solve().then(result => setNotice(result === 'submitted' ? 'Solve submitted. Close this window to follow it in Results.' : ''));
  };
  const building = build.status === 'building';
  const refusal = resolved?.context.refusal;
  const problems = [...placementProblems(shown, resolved).values()].flat();
  const revision = useDesignStore(store => store.designRevision);
  const contextCurrent = checkedRevision === revision && checkedLoad === currentDocumentLoad();
  const fit = fitHold(contextCurrent, failure?.revision === revision ? failure : null, refusal, problems);
  const solveDisabledReason = unfinishedReason || (!hasAssembly ? 'Add a woofer to build or solve.'
    : building ? 'Building the mesh… Wait for the build to finish.'
      : fit.held ? fit.line
        : !solveControl ? 'Solve is unavailable. Close this window and try again from the main workspace.'
          : solveControl.disabled ? solveControl.title : null);
  return <div className="source-editor-backdrop"><div ref={dialog} role="dialog" aria-modal="true" aria-label="Sources and baffle" className="front-workspace">
    <header className="front-header">
      <h2>Sources and baffle</h2><ExperimentalBadge/>
      <button className="btn" type="button" onClick={onClose}>Close</button>
    </header>
    <div className="front-tabs" role="tablist" aria-label="Assembly views">{tabs.map(item => <button className="btn" type="button" role="tab" key={item.id}
      aria-selected={activeTab === item.id} onClick={() => setTab(item.id)}>{item.label}</button>)}</div>
    {activeTab !== 'front' && diaphragmFor(design.assembly ?? blank, activeTab)?.kind === 'sketched'
      ? <DiaphragmWorkspace key={`${activeTab}:${currentDocumentLoad()}`} id={activeTab} assembly={design.assembly ?? blank} resolved={contextCurrent ? resolved : undefined} onMissing={() => setTab('front')}/>
      : activeTab === HF_RADIATOR_ID ? <div className="front-body" role="tabpanel" aria-label="HF diaphragm"><div className="front-canvas"><p>Choose Sketched to edit an HF diaphragm profile.</p></div>
        <aside className="front-panel"><DiaphragmSelect id={HF_RADIATOR_ID} openProfile={setTab}/></aside></div>
      : <div className="front-body" role="tabpanel" aria-label="Front">
      <div className="front-canvas">
        {panel && <Workspace adapter={adapter} state={shown} session={controller} selection={selection} onSelectionChange={setSelection}
          slots={{ properties: panel }} fitRequest={fits.current.count}/>}
        {!resolved && failure && <EmptyState className="front-canvas-empty" title="WG could not show the front view" detail={failure.text}
          action={<button className="btn-primary" type="button" onClick={retry}>Try again</button>}/>}
      </div>
      <aside className="front-panel" aria-label="Sources and baffle properties">
        <div className="front-messages">
          {resolved && failure && <Notice tone="warning" title="This view may be out of date" actions={<button className="btn" type="button" onClick={retry}>Try again</button>}>{failure.text}</Notice>}
          {refusal && <Notice tone="error">{refusal}</Notice>}
          {!refusal && problems.length > 0 && <Notice tone="error" title="Does not fit">{problems.map(problem => <div key={problem}>{problem}</div>)}</Notice>}
          {build.refusal && <Notice tone="error" title="Could not build">{build.refusal}
            {build.failureDetail && <DeveloperDetails><pre>{build.failureDetail}</pre></DeveloperDetails>}</Notice>}
          {failure && <DeveloperDetails><pre>{failure.detail}</pre></DeveloperDetails>}
          {solveControl?.actionError && <Notice tone="error">{solveControl.actionError}</Notice>}
          {solveControl?.solveNotice && <Notice tone="info">{solveControl.solveNotice.message}</Notice>}
          {notice && <Notice tone="info">{notice}</Notice>}
        </div>
        <div ref={setPanel}/>
      </aside>
    </div>}
    <footer className="front-footer">
      <div><p role="status">{solveDisabledReason || hasAssembly && !building && fit.line || buildLine(hasAssembly, build.status, !!slot.prepared, designModelCurrent(design, slot))}</p>
        {unfinished.map(item => <div key={item.id}><span>{item.label} · </span><button className="btn-link" type="button" onClick={() => setTab(item.id)}>Open sketch</button>{' · '}
          <button className="btn-link" type="button" onClick={() => revert(item.id)}>Revert</button></div>)}</div>
      <div className="front-actions">
        <button className="btn" type="button" disabled={!hasAssembly || building || fit.held || !!unfinishedReason} title={fit.held && fit.line || undefined} onClick={() => { void prepareDesignModel(fetcher).catch(() => {}); onClose(); }}>Build</button>
        <button className="btn-primary" type="button" disabled={!!solveDisabledReason} title={solveDisabledReason || solveControl?.title} onClick={solve}>Solve</button>
      </div>
    </footer>
  </div></div>;
}
