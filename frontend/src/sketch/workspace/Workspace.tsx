import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import type { DomainAdapter, DisplayItem, WorkspaceSession } from './types';
import { StaleWorkspaceWork } from './editCore';
import { displayView } from './view';
import { WorkspaceFrame, type WorkspaceSlots } from './WorkspaceFrame';
import { NavigateTools, WorkspaceToolbar } from './WorkspaceToolbar';
import { ContextLayer } from '../SketchContextLayer';
import { WorkspaceItems } from './WorkspaceItems';
import { useWorkspaceHistory, useWorkspaceEscape, useWorkspaceFrames, useWorkspaceSelection, useWorkspaceView, workspaceKeyboard, beginWorkspacePan, moveWorkspacePan, abortWorkspaceDrag, finishWorkspaceDrag, useWorkspaceCommands } from './surface';
import { combineSelection, finishWorkspaceSelection } from './selection';
import { rankedGeometryHits } from '../geometryHit';
import { screenToWorld, worldToScreen } from '../view';
import { readSketchView, saveSketchView } from '../viewPreferences';
import type { XY } from '../solver/types';
import '../sketchCanvas.css';

export interface WorkspaceProps<TDoc, S, C> {
  adapter: DomainAdapter<TDoc, S, C>; state: S; session?: WorkspaceSession<S, C>;
  slots?: WorkspaceSlots; selection?: string[]; onSelectionChange?(ids: string[]): void;
  hoverId?: string; onHoverChange?(id?: string): void; fitRequest?: number;
}
function itemPosition(item: DisplayItem): XY | undefined {
  return item.kind === 'point' || item.kind === 'handle' ? item.at : item.kind === 'circle' ? item.center : undefined;
}

/** Adapter-driven workspace. The Sketch kit composes the same surface, navigation and gesture primitives. */
export function Workspace<TDoc, S, C>({ adapter, state, session, slots, selection: controlled, onSelectionChange, hoverId, onHoverChange, fitRequest }: WorkspaceProps<TDoc, S, C>) {
  const surface = useWorkspaceView((width, height) => displayView(adapter.display(state, { selection: [], scale: 1 }), { width, height }));
  const { svg, size, view, setView, position, zoom, space, hovered, panning, panActive, setPanActive } = surface;
  const selectionState = useWorkspaceSelection(controlled, onSelectionChange);
  const { selection, setSelection, selecting, marquee, setMarquee } = selectionState;
  const [tool, setTool] = useState('select'), [hover, setHover] = useState<string>(), [cursor, setCursor] = useState<XY>({ x: 0, y: 0 });
  const [message, setMessage] = useState(''), [viewOptions, setViewOptions] = useState(readSketchView);
  const [preview, setPreview] = useState<DisplayItem[]>([]);
  const [contextPointer, setContextPointer] = useState<XY>();
  const local = useRef<unknown>(undefined), alive = useRef(true);
  const latest = useRef({ state, session }); latest.current = { state, session };
  const drag = useRef<{ ready: Promise<void>; origin: XY; points: { id: string; at: XY }[] } | undefined>(undefined);
  const viewState = { selection, hover: hoverId ?? hover };
  const display = adapter.display(state, { ...viewState, scale: view.scale, view, viewport: size, names: viewOptions.names });
  const status = session?.status ?? adapter.status(state), solver = session?.solver;
  const problems = status.kind === 'invalid' ? status.problems : [];
  const activeTool = adapter.tools.find(t => t.id === tool && (solver || t.group !== 'Constraints'));
  const hint = activeTool?.hint(state) ?? (tool === 'hand' ? 'Drag to pan · Esc: Select' : 'Select or drag · Shift adds · Delete removes · Esc cancels');
  function error(reason: unknown) { if (alive.current && !(reason instanceof StaleWorkspaceWork)) setMessage(reason instanceof Error ? reason.message : String(reason)); }
  const { run, pending } = useWorkspaceCommands(!!session, alive, {
    before: () => setMessage(''), success: () => { setMessage(latest.current.session?.notice ?? ''); }, error,
  }, session);
  const frames = useWorkspaceFrames<{ targets: { id: string; at: XY }[] }>(target => {
    const active = drag.current;
    if (!active) return;
    void active.ready.then(() => latest.current.session!.dragTo(target.targets)).then(result => {
      if (alive.current) setMessage(result.stopped ? result.reason ?? 'Movement limited' : '');
    }, error);
  });
  useEffect(() => { alive.current = true; return () => { alive.current = false; frames.current?.cancel(); }; }, []);
  useLayoutEffect(() => { if (fitRequest !== undefined) setView(displayView(display, size)); }, [fitRequest]);
  function clearGestureFrames() {
    frames.current?.cancel(); selecting.current = undefined; setMarquee(undefined);
    panning.current = undefined; setPanActive(false);
  }
  useWorkspaceHistory(session, cause => {
    clearGestureFrames(); drag.current = undefined;
    local.current = undefined; setPreview([]); setTool('select'); setMessage('');
    const current = session!.current;
    const display = adapter.display(current, { selection: [], scale: view.scale });
    const ids = new Set(display.items.filter(item => item.editable).map(item => item.id));
    setSelection(cause === 'load' ? [] : selection.filter(id => ids.has(id)));
    if (cause === 'load') setView(displayView(display, size));
  });
  function choose(next: string) { setTool(next); local.current = undefined; setPreview([]); setMessage(''); svg.current?.focus({ preventScroll: true }); }
  function undo(redo = false) { return session?.historyKind === 'project' ? session.undo(redo).catch(error) : run(() => session!.undo(redo)); }
  function keyboard(event: KeyboardEvent | globalThis.KeyboardEvent) {
    workspaceKeyboard(event, {
      cancelOnUndo: session?.historyKind === 'project' && (!!drag.current || !!selecting.current || tool !== 'select'),
      surface, tool, selection, setSelection, dragging: !!drag.current, navigateSelection: tool === 'select',
      escape: event => {
        clearGestureFrames();
        abortWorkspaceDrag(drag, cancel => session!.endDrag(cancel), error);
        const result = activeTool?.key?.(event as globalThis.KeyboardEvent, state, local.current);
        if (result?.consumed) {
          if ('local' in result) local.current = result.local;
          setPreview([]);
          if (result.commands?.length) void run(() => session!.command(result.commands!));
          return;
        }
        choose('select'); setSelection([]);
      },
      undo: redo => { void undo(redo); }, fit: all => setView(displayView(display, size, all)),
      remove: () => {
        const commands = adapter.remove(state, selection);
        if (commands.length) void run(async () => { await session!.command(commands); setSelection([]); svg.current?.focus({ preventScroll: true }); });
      },
      shortcut: (key, event) => {
        const result = activeTool?.key?.(event as globalThis.KeyboardEvent, state, local.current);
        if (result?.consumed) { event.preventDefault(); if ('local' in result) local.current = result.local; if (result.commands?.length) void run(() => session!.command(result.commands!)); return; }
        const next = adapter.tools.find(t => t.shortcut === key && (solver || t.group !== 'Constraints'));
        if (next) choose(next.id);
      },
    });
  }
  useWorkspaceEscape(tool !== 'select' || !!selection.length || !!drag.current || !!selecting.current, keyboard);
  function toolPointer(phase: 'down' | 'move' | 'up', at: XY, hit?: string) {
    if (!activeTool || !session) return;
    const result = activeTool.pointer({ phase, at, hit, tolerance: 12 / view.scale }, state, local.current);
    if ('local' in result) local.current = result.local;
    if (result.preview) setPreview(result.preview);
    if (result.commands?.length) void run(() => session.command(result.commands!));
    if (result.done) choose('select');
  }
  function hitAt(at: XY) { return rankedGeometryHits(adapter.hits(state, at, 8 / view.scale, viewState), 8 / view.scale).map(h => h.id); }
  function pointerDown(event: PointerEvent<SVGSVGElement>) {
    event.preventDefault();
    if (event.button === 1 || event.button === 0 && (space.current || tool === 'hand')) {
      beginWorkspacePan(event, surface); return;
    }
    if (tool === 'hand') return;
    const screen = position(event), at = screenToWorld(view, screen);
    let selectedHit: string | undefined;
    if (tool === 'select' && event.button === 0) {
      const ids = hitAt(at), targetId = (event.target as Element).closest('[data-id]')?.getAttribute('data-id');
      if (targetId && !ids.includes(targetId) && display.items.some(i => i.id === targetId && i.editable)) ids.push(targetId);
      selectedHit = selectionState.pick(event, ids, setMessage);
    }
    event.currentTarget.setPointerCapture?.(event.pointerId); svg.current?.focus({ preventScroll: true }); setCursor(at);
    if (event.button !== 0 || !session || drag.current) return;
    if (tool !== 'select') { toolPointer('down', at, hitAt(at)[0]); return; }
    const id = selectedHit;
    if (!id) { selectionState.empty(screen, event); return; }
    selectionState.begin(screen, id, event);
  }
  function pointerMove(event: PointerEvent<SVGSVGElement>) {
    const screen = position(event), at = screenToWorld(view, screen); setCursor(at); setContextPointer(screen);
    if (moveWorkspacePan(screen, surface)) return;
    if (tool === 'hand') return;
    if (selecting.current && !event.buttons) { pointerUp(); return; }
    if (selectionState.advance(screen, next => {
      if (!drag.current && session && next.hit) {
        const set = adapter.dragSet(state, next.selection, next.hit);
        if (set?.ids.length) {
          const points = set.ids.flatMap(id => { const item = display.items.find(i => i.id === id), at = item && itemPosition(item); return at ? [{ id, at }] : []; });
          if (points.length) {
            const ready = session.beginDrag(points.map(p => p.id)); drag.current = { ready, origin: screenToWorld(view, next.start), points }; void ready.catch(error);
          }
        }
      }
    })) return;
    const id = hitAt(at)[0]; setHover(id); onHoverChange?.(id);
    const active = drag.current;
    if (active) { frames.current!.push({ targets: active.points.map(p => ({ id: p.id, at: { x: p.at.x + at.x - active.origin.x, y: p.at.y + at.y - active.origin.y } })) }); return; }
    toolPointer('move', at, id);
  }
  function pointerUp(cancel = false, event?: PointerEvent<SVGSVGElement>) {
    selectionState.release(cancel, gesture => finishWorkspaceSelection(gesture, gesture => {
      const start = screenToWorld(view, gesture.start), end = screenToWorld(view, gesture.current);
      return combineSelection(gesture.selection, adapter.marquee(state, { minX: Math.min(start.x, end.x), maxX: Math.max(start.x, end.x), minY: Math.min(start.y, end.y), maxY: Math.max(start.y, end.y) }, gesture.current.x < gesture.start.x, viewState), gesture.modifiers);
    }));
    if (!cancel && event && !panning.current) toolPointer('up', screenToWorld(view, position(event)));
    panning.current = undefined; setPanActive(false);
    finishWorkspaceDrag(drag, frames, cancel => session!.endDrag(cancel), error, cancel);
  }
  const groups = [...new Set(adapter.tools.filter(t => solver || t.group !== 'Constraints').map(t => t.group))];
  const properties = session ? adapter.properties(state, selection, session, { setSelection }) : [];
  return <WorkspaceFrame slots={slots} labels={adapter.labels} pending={pending || !!session?.busy} view={view} size={size} svg={svg} marquee={marquee}
    rootProps={{ onKeyDown: keyboard, onKeyUp: e => { if (e.key === ' ') space.current = false; }, onBlur: e => { if (!e.currentTarget.contains(e.relatedTarget)) space.current = false; } }}
    svgProps={{ className: tool === 'hand' || panActive ? `sketch-pan${panActive ? ' grabbing' : ''}` : undefined,
      onPointerEnter: () => { hovered.current = true; }, onPointerLeave: () => { hovered.current = false; setContextPointer(undefined); setHover(undefined); onHoverChange?.(undefined); },
      onPointerDown: pointerDown, onPointerMove: pointerMove, onPointerUp: event => pointerUp(false, event), onPointerCancel: () => pointerUp(true), onLostPointerCapture: () => pointerUp(true) }}
    toolbar={<WorkspaceToolbar label={adapter.labels?.tools} tool={tool} enabled={!!session} historyKind={session?.historyKind} solver={!!solver} navigation={<NavigateTools tool={tool} choose={choose}/>} viewOptions={viewOptions}
      toggle={key => setViewOptions(previous => { const next = { ...previous, [key]: !previous[key] }; saveSketchView(next); return next; })} undo={redo => void undo(redo)} zoom={zoom} fit={all => setView(displayView(display, size, all))}>
      {groups.map(group => <div key={group} className="sketch-toolbar-group" role="group" aria-label={group}><span className="sketch-group-caption">{group}</span><div className="sketch-tool-segment">{adapter.tools.filter(t => t.group === group).map(t => <button className="btn" type="button" key={t.id} aria-label={t.label} title={t.shortcut ? `${t.label} (${t.shortcut.toUpperCase()})` : t.label} aria-pressed={tool === t.id} disabled={!session} onClick={() => choose(t.id)}><svg aria-hidden="true" viewBox="0 0 16 16"><path d={t.icon}/></svg><span className="sketch-tool-label">{t.label}</span></button>)}</div></div>)}
    </WorkspaceToolbar>}
    banners={<>{solver && session?.rejection !== undefined && <div role="alert" className="sketch-conflict-banner">{String(session.rejection)}</div>}</>}
    properties={<>{tool !== 'select' && <p className="sketch-command-step" role="status">{hint}</p>}{properties.map(section => <section data-workspace-own-keys key={section.id} aria-label={section.title}>{section.render()}</section>)}</>}
    status={<div role="status" className="sketch-status">{solver && <><span className="sketch-dof">{solver.diagnosis?.dof ?? '…'} DOF</span>{` · Solve: ${solver.diagnosis?.status ?? 'pending'} · `}</>}{status.kind}{problems.length > 0 && ` · ${problems.length} problems`} · {adapter.axes.x.label} {cursor.x.toFixed(2)}, {adapter.axes.y.label} {cursor.y.toFixed(2)} {adapter.axes.x.unit} · {message || (status.kind === 'unfinished' ? status.hint : hint)}</div>}
    footer={<>{problems.map((problem, i) => <div key={`${problem.code}${i}`} className="sketch-domain-problem">{problem.message}</div>)}</>}>
    <ContextLayer items={display.context} names={viewOptions.names} cursor={contextPointer} contour={display.items.flatMap(item => item.kind === 'point' ? [worldToScreen(view, item.at)] : [])} viewport={size} reserved={[]} canPick={false} isPanning={() => space.current || tool === 'hand'} highlighted={new Set(selection)} stale={false} view={view} onPick={() => {}} onEditLimit={() => {}} canEditLimit={false}/>
    <WorkspaceItems items={[...display.items, ...preview]} view={view}/>
  </WorkspaceFrame>;
}
