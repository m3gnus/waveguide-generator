import { StaleWorkspaceWork } from './editCore';
import type { HistoryCause, WorkspaceHistory } from './historyAuthority';
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type RefObject } from 'react';
import { pan, zoomAt, wheelGesture, type View } from '../view';
import { coalesceTargets, combineSelection, select, cycleHit, moveSelect, startSelect, type Cycle, type SelectState } from './selection';
import type { XY } from '../solver/types';

/** View and navigation state shared by every domain, including the Sketch kit. */
export function useWorkspaceView(initialView: (width: number, height: number) => View) {
  const svg = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ width: 640, height: 360 });
  const [view, setView] = useState(() => initialView(640, 360));
  const initial = useRef(initialView); initial.current = initialView;
  const hovered = useRef(false), space = useRef(false), panning = useRef<{ x: number; y: number } | undefined>(undefined);
  const [panActive, setPanActive] = useState(false);
  useEffect(() => {
    // Hover panning also works before the canvas has acquired keyboard focus.
    const down = (event: globalThis.KeyboardEvent) => {
      const active = document.activeElement;
      if (event.key === ' ' && hovered.current && (!active || active === document.body)
        && !(event.target instanceof Element && event.target.closest('button, input, select, textarea, [contenteditable]'))) {
        event.preventDefault(); space.current = true;
      }
    };
    const up = (event: globalThis.KeyboardEvent) => { if (event.key === ' ') space.current = false; };
    const blur = () => { space.current = false; };
    window.addEventListener('keydown', down); window.addEventListener('keyup', up); window.addEventListener('blur', blur);
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); window.removeEventListener('blur', blur); };
  }, []);
  useEffect(() => {
    const element = svg.current; if (!element) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault(); const rect = element.getBoundingClientRect();
      const cursor = { x: (event.clientX - rect.left) * size.width / (rect.width || size.width), y: (event.clientY - rect.top) * size.height / (rect.height || size.height) };
      const gesture = wheelGesture(event);
      setView(view => gesture.kind === 'pan' ? pan(view, gesture.dx, gesture.dy) : zoomAt(view, cursor, Math.max(.01 / view.scale, Math.min(100000 / view.scale, gesture.factor))));
    };
    element.addEventListener('wheel', wheel, { passive: false });
    return () => element.removeEventListener('wheel', wheel);
  }, [size.width, size.height]);
  useEffect(() => {
    if (!svg.current || typeof ResizeObserver === 'undefined') return;
    let previous = { width: 640, height: 360 }, measured = false;
    const observer = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      if (width > 64 && height > 64) { const dx = (width - previous.width) / 2, dy = (height - previous.height) / 2; previous = { width, height }; const first = !measured; measured = true; setSize(previous); setView(view => first ? initial.current(width, height) : pan(view, dx, dy)); }
    }); observer.observe(svg.current); return () => observer.disconnect();
  }, []);
  function position(event: { clientX: number; clientY: number }) {
    const rect = svg.current!.getBoundingClientRect();
    return { x: (event.clientX - rect.left) * size.width / (rect.width || size.width), y: (event.clientY - rect.top) * size.height / (rect.height || size.height) };
  }
  function zoom(factor: number) { setView(view => zoomAt(view, { x: size.width / 2, y: size.height / 2 }, factor)); }
  return { svg, size, view, setView, space, hovered, panning, panActive, setPanActive, position, zoom };
}

export function useWorkspaceSelection(controlled?: string[], onSelectionChange?: (ids: string[]) => void) {
  const [localSelection, setLocalSelection] = useState<string[]>([]);
  const selection = controlled ?? localSelection;
  const setSelection = (ids: string[]) => { setLocalSelection(ids); onSelectionChange?.(ids); };
  const selecting = useRef<SelectState | undefined>(undefined);
  const cycle = useRef<Cycle | undefined>(undefined);
  const [marquee, setMarquee] = useState<SelectState>();
  function pick(event: { clientX: number; clientY: number }, ids: string[], setMessage: (message: string) => void) {
    cycle.current = cycleHit(cycle.current, { x: event.clientX, y: event.clientY }, performance.now(), ids);
    const selectedHit = ids[cycle.current.index];
    setMessage(ids.length > 1 ? `${cycle.current.index + 1} of ${ids.length}` : '');
    return selectedHit;
  }
  function empty(screen: XY, event: { shiftKey: boolean; ctrlKey: boolean; metaKey: boolean }, select = true) {
    if (select) selecting.current = startSelect(screen, undefined, selection, { shift: event.shiftKey, toggle: event.ctrlKey || event.metaKey });
    if (!event.shiftKey && !event.ctrlKey && !event.metaKey) setSelection([]);
  }
  function begin(screen: XY, id: string, event: { shiftKey: boolean; ctrlKey: boolean; metaKey: boolean }, select = true) {
    const modifiers = { shift: event.shiftKey, toggle: event.ctrlKey || event.metaKey };
    const next = select && selection.includes(id) && !modifiers.shift && !modifiers.toggle ? selection : combineSelection(selection, [id], modifiers);
    setSelection(next);
    if (select && next.includes(id)) selecting.current = startSelect(screen, id, next, modifiers);
  }
  function advance(screen: XY, beginDrag: (state: SelectState) => void) {
    if (!selecting.current) return false;
    const next = moveSelect(selecting.current, screen); selecting.current = next;
    if (next.phase !== 'pending') cycle.current = undefined;
    if (next.phase === 'marquee') { setMarquee(next); return true; }
    if (next.phase === 'drag') beginDrag(next);
    return false;
  }
  function release(cancel: boolean, finish: (state: SelectState) => string[]) {
    const selectionGesture = selecting.current; selecting.current = undefined; setMarquee(undefined);
    if (!cancel && selectionGesture) setSelection(finish(selectionGesture));
  }
  return { selection, setSelection, selecting, cycle, marquee, setMarquee, pick, empty, begin, advance, release };
}

export function useWorkspaceFrames<T>(consume: (target: T) => void) {
  const frames = useRef<ReturnType<typeof coalesceTargets<T>> | undefined>(undefined);
  // Deliberately retain the initial consumer: it reads live data through refs.
  if (!frames.current) frames.current = coalesceTargets(requestAnimationFrame, cancelAnimationFrame, consume);
  useEffect(() => () => frames.current?.cancel(), []);
  return frames;
}

/** Domain panels and inline dialogs own all keys, including capture-phase Escape. */
export function ownsWorkspaceKeys(target: EventTarget | null) {
  return target instanceof Element && !!target.closest('[data-workspace-own-keys]');
}

type WorkspaceKeyEvent = KeyboardEvent | globalThis.KeyboardEvent;
interface KeyboardActions {
  surface: ReturnType<typeof useWorkspaceView>;
  tool: string; selection: string[]; setSelection(ids: string[]): void;
  dragging: boolean; navigateSelection: boolean;
  cancelOnUndo?: boolean;
  escape(event: WorkspaceKeyEvent): void;
  enter?(event: WorkspaceKeyEvent): boolean;
  activate?(id: string, event: WorkspaceKeyEvent): void;
  undo(redo: boolean): void; fit(all: boolean): void; remove(): void;
  shortcut(key: string, event: WorkspaceKeyEvent): void;
}
/** One key routing order for the generic surface and domain tool kits. */
export function workspaceKeyboard(event: WorkspaceKeyEvent, actions: KeyboardActions) {
  if (ownsWorkspaceKeys(event.target)) return;
  const key = event.key.toLowerCase(), target = event.target;
  const { surface: { svg, zoom, space, hovered }, tool, selection, setSelection } = actions;
  if (key === 'escape') { event.stopPropagation(); event.preventDefault(); actions.escape(event); return; }
  if ((event.ctrlKey || event.metaKey) && key === 'z' && actions.cancelOnUndo) { event.preventDefault(); actions.escape(event); return; }
  if (!(target instanceof Element) || target.closest('input, select, textarea, button, [contenteditable]')) return;
  if (actions.dragging) { event.preventDefault(); return; }
  if (svg.current?.contains(target) && (key === '=' || key === '+' || key === '-')) { event.preventDefault(); zoom(key === '-' ? 1 / 1.25 : 1.25); return; }
  if (tool === 'hand' && (key.startsWith('arrow') || key === 'enter' || key === ' ')) { if (key === ' ') { event.preventDefault(); space.current = true; } return; }
  if (key.startsWith('arrow') && svg.current?.contains(target) && actions.navigateSelection) {
    focusWorkspaceSelection(svg.current, target, key, event, setSelection); return;
  }
  if (key === 'enter' && actions.enter?.(event)) { event.preventDefault(); return; }
  if (key === 'enter' || key === ' ') {
    const id = target.getAttribute('data-id');
    if (id && svg.current?.contains(target)) {
      event.preventDefault();
      if (actions.activate) actions.activate(id, event); else setSelection(select(selection, id, event.shiftKey));
      return;
    }
    const active = document.activeElement;
    if (key === ' ' && (active === svg.current || hovered.current && (!active || active === document.body))) { event.preventDefault(); space.current = true; }
    return;
  }
  if ((event.ctrlKey || event.metaKey) && key === 'z') { event.preventDefault(); actions.undo(event.shiftKey); return; }
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (tool === 'hand' && (key === 'delete' || key === 'backspace' || key === 'x')) { event.preventDefault(); return; }
  if (key === 'f') actions.fit(event.shiftKey);
  else if (key === 'delete' || key === 'backspace') { event.preventDefault(); actions.remove(); }
  else actions.shortcut(key, event);
}

export function useWorkspaceEscape(active: boolean, keyboard: (event: KeyboardEvent | globalThis.KeyboardEvent) => void) {
  useEffect(() => {
    // Own Escape before the enclosing modal, even when browser focus is on BODY.
    // Idle Select may close the dialog; each active command consumes one cancellation.
    const escape = (event: globalThis.KeyboardEvent) => {
      if (ownsWorkspaceKeys(event.target)) return;
      if (event.key === 'Escape' && active) keyboard(event);
    };
    document.addEventListener('keydown', escape, true);
    return () => document.removeEventListener('keydown', escape, true);
  });
}

export function focusWorkspaceSelection(svg: SVGSVGElement, target: Element, key: string, event: { preventDefault(): void }, setSelection: (ids: string[]) => void) {
  const items = [...svg.querySelectorAll<SVGElement>('[role="button"][data-id]')];
  if (items.length) {
    event.preventDefault();
    const index = items.indexOf(target as SVGElement), direction = key === 'arrowleft' || key === 'arrowup' ? -1 : 1;
    const next = items[index < 0 ? direction > 0 ? 0 : items.length - 1 : (index + direction + items.length) % items.length];
    next.focus({ preventScroll: true }); setSelection([next.dataset.id!]);
  }
}

export function beginWorkspacePan(event: PointerEvent<SVGSVGElement>, surface: ReturnType<typeof useWorkspaceView>) {
  const { panning, position, setPanActive, svg } = surface;
  panning.current = position(event); setPanActive(true); event.currentTarget.setPointerCapture?.(event.pointerId); svg.current?.focus({ preventScroll: true });
}

export function moveWorkspacePan(screen: { x: number; y: number }, surface: ReturnType<typeof useWorkspaceView>) {
  const { panning, setView } = surface;
  if (!panning.current) return false;
  const dx = screen.x - panning.current.x, dy = screen.y - panning.current.y; setView(view => pan(view, dx, dy)); panning.current = screen; return true;
}

/** Flush the last coalesced target before clearing the active drag or queueing release. */
export function finishWorkspaceDrag<T extends { ready: Promise<void> }>(drag: RefObject<T | undefined>, frames: RefObject<{ flush(): void; cancel(): void } | undefined>, end: (cancel: boolean) => Promise<void>, error: (reason: unknown) => void, cancel: boolean) {
  const active = drag.current;
  if (!active) return;
  if (cancel) frames.current?.cancel(); else frames.current?.flush();
  drag.current = undefined;
  void active.ready.then(() => end(cancel)).catch(error);
}
export function abortWorkspaceDrag<T extends { ready: Promise<void> }>(drag: RefObject<T | undefined>, end: (cancel: boolean) => Promise<void>, error: (reason: unknown) => void) {
  if (drag.current) { const active = drag.current; drag.current = undefined; void active.ready.then(() => end(true)).catch(error); }
}

/** UI command ordering shared by tool kits; the controller's transaction queue stays in W3. */
export function useWorkspaceCommands(enabled: boolean, alive: RefObject<boolean>, callbacks: { before(): void; success(result: unknown): void; error(reason: unknown): void }, history?: WorkspaceHistory) {
  const generation = useRef(0);
  useWorkspaceHistory(history, () => { generation.current++; });
  const [pending, setPending] = useState(false);
  const pendingCount = useRef(0);
  const work = useRef<Promise<unknown>>(Promise.resolve());
  function run(action: () => Promise<unknown>) {
    if (!enabled) return Promise.resolve();
    const captured = generation.current;
    pendingCount.current++; setPending(true);
    const result = work.current.then(async () => {
      try {
        if (!alive.current || captured !== generation.current) return;
        callbacks.before();
        const result = await action();
        if (alive.current && captured === generation.current) callbacks.success(result);
      } catch (reason) { if (!(reason instanceof StaleWorkspaceWork)) callbacks.error(reason); }
      finally { pendingCount.current--; if (alive.current) setPending(pendingCount.current > 0); }
    });
    work.current = result.catch(() => {}); return result;
  }
  return { run, pending, pendingCount };
}

/** External project navigation resets gestures in either surface, before queued work resumes. */
export function useWorkspaceHistory(session: WorkspaceHistory | undefined, reset: (cause: HistoryCause) => void) {
  const latest = useRef(reset); latest.current = reset;
  useEffect(() => session?.subscribeHistory?.(cause => latest.current(cause)), [session]);
}
