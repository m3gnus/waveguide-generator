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
import { fetchAssemblyContext } from '../api/assembly';
import { useModalDialogFocus } from '../shell/dialogFocus';
import { useOptionalSolveControl } from '../shell/JobsCoordinator';
import { frontViewAdapter, frontViewBinding, type FrontCommand, type FrontViewContext } from '../sketch/adapters/frontView';
import { PlainController } from '../sketch/workspace/plainController';
import { ProjectHistoryAuthority } from '../sketch/workspace/projectHistoryAuthority';
import { Workspace } from '../sketch/workspace/Workspace';
import { commitAssembly, emptyAssembly, type DesignAssembly } from '../stores/assembly';
import { requestAssemblyBuild, useAssemblyBuildStore } from '../stores/assemblyBuild';
import { serializeDesign, subscribeRevision, useDesignStore } from '../stores/design';
import { ExperimentalBadge } from './ExperimentalBadge';
import { frontViewProperties } from './FrontProperties';
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
  return <div className="source-editor-launch" data-control-reveal-id="front-workspace-launch">
    <button className="btn" type="button" aria-haspopup="dialog" onClick={openFrontWorkspace}>Sources and baffle…</button>
    <ExperimentalBadge/>
    <small>Woofers on the enclosure's front baffle, solved with the horn.</small>
  </div>;
}

export function FrontWorkspaceDialog() {
  const open = useSyncExternalStore(visibility.subscribe, visibility.getSnapshot, visibility.getSnapshot);
  return open ? createPortal(<FrontWorkspace onClose={closeFrontWorkspace}/>, document.body) : null;
}

/** The latest front context for the design on screen, refreshed when the design rests. */
function useFrontContext(blank: DesignAssembly, fetcher: typeof fetch): { resolved: FrontViewContext | undefined; error: string } {
  const design = useDesignStore(state => state.design);
  const dragging = useDesignStore(state => !!state.dragSnapshot);
  const [resolved, setResolved] = useState<FrontViewContext>();
  const [error, setError] = useState('');
  const latest = useRef(0);
  useEffect(() => {
    if (dragging) return;
    const ask = ++latest.current;
    const assembly = structuredClone(design.assembly ?? blank);
    const timer = setTimeout(() => {
      fetchAssemblyContext(serializeDesign({ ...design, assembly }), fetcher).then(context => {
        if (ask === latest.current) { setResolved({ context, assembly }); setError(''); }
      }, (reason: unknown) => { if (ask === latest.current) setError(reason instanceof Error ? reason.message : String(reason)); });
    }, FRONT_CONTEXT_DELAY_MS);
    return () => clearTimeout(timer);
  }, [design, dragging, fetcher]);
  return { resolved, error };
}

export function FrontWorkspace({ onClose, fetcher = fetch }: { onClose: () => void; fetcher?: typeof fetch }) {
  const dialog = useModalDialogFocus<HTMLDivElement>({ open: true, onClose });
  const [blank] = useState(() => emptyAssembly());
  const { resolved, error } = useFrontContext(blank, fetcher);
  const context = useRef(resolved); context.current = resolved;
  const adapter = useMemo(() => frontViewAdapter(() => context.current, frontViewProperties(() => context.current)), []);
  const [state, setState] = useState<DesignAssembly>(() => useDesignStore.getState().design.assembly ?? blank);
  const controller = useMemo(() => {
    const authority = new ProjectHistoryAuthority(frontViewBinding(blank));
    return new PlainController<DesignAssembly, DesignAssembly, FrontCommand>({
      initial: authority.current, adapter, authority, changed: setState, preview: setState,
    });
  }, []);
  useEffect(() => () => controller.destroy(), [controller]);
  useEffect(() => subscribeRevision(event => {
    if (event.reason === 'load' && !useDesignStore.getState().design.assembly) onClose();
  }), [onClose]);
  const hasAssembly = useDesignStore(store => !!store.design.assembly);
  const build = useAssemblyBuildStore();
  const solveControl = useOptionalSolveControl();
  const [notice, setNotice] = useState('');
  const [selection, setSelection] = useState<string[]>([]);
  const solve = () => {
    setNotice('');
    // The jobs coordinator owns every submission; it reports a refusal itself.
    solveControl?.solveAssembly?.().then(result => setNotice(result.jobId ? result.notice ?? 'Solve submitted.' : ''),
      (reason: unknown) => setNotice(reason instanceof Error ? reason.message : String(reason)));
  };
  return <div className="source-editor-backdrop"><div ref={dialog} role="dialog" aria-modal="true" aria-label="Sources and baffle" className="source-editor front-workspace">
    <header className="source-editor-toolbar">
      <h2>Sources and baffle <ExperimentalBadge/></h2>
      <button className="btn" type="button" onClick={onClose}>Close</button>
    </header>
    <Workspace adapter={adapter} state={hasAssembly ? state : blank} session={controller} selection={selection} onSelectionChange={setSelection}/>
    <footer className="source-editor-toolbar">
      <button className="btn" type="button" disabled={!hasAssembly || build.status === 'building'} onClick={() => { void requestAssemblyBuild(fetcher).catch(() => {}); }}>Build</button>
      <button className="btn" type="button" disabled={!hasAssembly || !solveControl?.solveAssembly || solveControl.submitting} onClick={solve}>Solve</button>
      {hasAssembly && <button className="btn" type="button" onClick={() => commitAssembly(null)}>Remove assembly</button>}
      <span role="status">{error || build.refusal || notice || (build.status === 'building' ? 'Building the assembly…' : build.status === 'published' ? 'Assembly built.' : '')}</span>
    </footer>
  </div></div>;
}
