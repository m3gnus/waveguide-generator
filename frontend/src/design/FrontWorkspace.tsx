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
import { assemblyBuildCurrent, requestAssemblyBuild, useAssemblyBuildStore, type AssemblyBuildState } from '../stores/assemblyBuild';
import { serializeDesign, subscribeRevision, useDesignStore } from '../stores/design';
import { ExperimentalBadge } from './ExperimentalBadge';
import { frontViewProperties } from './FrontProperties';
import './frontWorkspace.css';
import './sourceContourEditor.css';
import { failureWords } from '../api/httpError';

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
interface FrontContextState { resolved: FrontViewContext | undefined; checkedRevision: number | null; failure: FrontContextFailure | null; retry(): void }

/** The latest front context for the design on screen, refreshed when the design rests. */
function useFrontContext(blank: DesignAssembly, fetcher: typeof fetch): FrontContextState {
  const design = useDesignStore(state => state.design);
  const revision = useDesignStore(state => state.designRevision);
  const dragging = useDesignStore(state => !!state.dragSnapshot);
  const [resolved, setResolved] = useState<FrontViewContext>();
  const [checkedRevision, setCheckedRevision] = useState<number | null>(null);
  const [failure, setFailure] = useState<FrontContextState['failure']>(null);
  const [attempt, setAttempt] = useState(0);
  const latest = useRef(0);
  useEffect(() => {
    const ask = ++latest.current;
    if (dragging) return;
    const assembly = structuredClone(design.assembly ?? blank);
    const request = new AbortController();
    const timer = setTimeout(() => {
      fetchAssemblyContext(serializeDesign({ ...design, assembly }), fetcher, request.signal).then(context => {
        if (ask === latest.current) { setResolved({ context, assembly }); setCheckedRevision(revision); setFailure(null); }
      }, (reason: unknown) => {
        if (ask !== latest.current) return;
        setFailure({ revision, text: assemblyProblemText(reason, 'WG could not update the front view and has recorded the problem.'),
          detail: reason instanceof Error ? reason.message : String(reason), refused: assemblyContextRefused(reason) });
      });
    }, FRONT_CONTEXT_DELAY_MS);
    return () => { clearTimeout(timer); request.abort(); };
  }, [design, revision, dragging, fetcher, attempt]);
  return { resolved, checkedRevision, failure, retry: () => { setFailure(null); setAttempt(value => value + 1); } };
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
  const { resolved, checkedRevision, failure, retry } = useFrontContext(blank, fetcher);
  const context = useRef(resolved); context.current = resolved;
  const adapter = useMemo(() => frontViewAdapter(() => context.current,
    frontViewProperties(() => context.current, () => !!useDesignStore.getState().design.assembly)), []);
  const [state, setState] = useState<DesignAssembly>(() => useDesignStore.getState().design.assembly ?? blank);
  const controller = useMemo(() => {
    const authority = new ProjectHistoryAuthority(frontViewBinding(blank));
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
  const shown = hasAssembly ? state : blank;
  const build = useAssemblyBuildStore();
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
    solveControl?.solveAssembly?.().then(result => setNotice(result.jobId ? result.notice ?? 'Solve submitted. Close this window to follow it in Results.' : ''),
      (reason: unknown) => { if (useAssemblyBuildStore.getState().status !== 'refused') setNotice(failureWords(reason)); });
  };
  const building = build.status === 'building';
  const refusal = resolved?.context.refusal;
  const problems = [...placementProblems(shown, resolved).values()].flat();
  const revision = useDesignStore(store => store.designRevision);
  const fit = fitHold(checkedRevision === revision, failure?.revision === revision ? failure : null, refusal, problems);
  return <div className="source-editor-backdrop"><div ref={dialog} role="dialog" aria-modal="true" aria-label="Sources and baffle" className="front-workspace">
    <header className="front-header">
      <h2>Sources and baffle</h2><ExperimentalBadge/>
      <button className="btn" type="button" onClick={onClose}>Close</button>
    </header>
    <div className="front-body">
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
          {notice && <Notice tone="info">{notice}</Notice>}
        </div>
        <div ref={setPanel}/>
      </aside>
    </div>
    <footer className="front-footer">
      <p role="status">{hasAssembly && !building && fit.line || buildLine(hasAssembly, build.status, !!build.published, assemblyBuildCurrent(build, design))}</p>
      <div className="front-actions">
        <button className="btn" type="button" disabled={!hasAssembly || building || fit.held} title={fit.held && fit.line || undefined} onClick={() => { void requestAssemblyBuild(fetcher).catch(() => {}); onClose(); }}>Build</button>
        <button className="btn-primary" type="button" disabled={!hasAssembly || building || fit.held || !solveControl?.solveAssembly || solveControl.submitting} title={fit.held && fit.line || undefined} onClick={solve}>Solve</button>
      </div>
    </footer>
  </div></div>;
}
