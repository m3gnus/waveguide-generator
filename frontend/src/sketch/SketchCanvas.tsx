import { Fragment, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from 'react';
import type { DomainProblem, Reference } from './adapter';
import type { Command, PointRef } from './commands';
import { constraintRefs, entityPoints, type Constraint, type DimensionPlacement, type Entity, type NewConstraint, type Sketch } from './model';
import type { Diagnosis, DragResult } from './session';
import type { XY } from './solver/types';
import { arcGeometry, arcThrough, onArc, positiveAngle, projectToEntity, tangentArc, tangentIncoming, tau } from './geometry';
import { findSnap, snapLimitTarget, type SnapResult } from './snap';
import { fit, gridStep, pan, screenToWorld, worldToScreen, zoomAt, type View } from './view';
import { continueLine, lineState, placeLine, typedTarget, type LineState } from './tools/line';
import { coalesceTargets, select } from './tools/select';
import { dimensionFor } from './tools/dimension';
import { constraintFor, selectionConstraints } from './tools/constraints';
import { dimensionHelper, dimensionLabel, dimensionLayouts, dragDimensionPlacement } from './dimensionGraphics';
import { displayName } from './displayName';
import { readSketchView, saveSketchView } from './viewPreferences';
import './sketchCanvas.css';

export interface CanvasSession {
  readonly notice?: string;
  readonly lastSolveMs?: number;
  command(command: Command | Command[], dimensions?: { length: number; angle?: number }): Promise<Sketch>;
  constraint(constraint: NewConstraint, reference?: boolean): Promise<Sketch>;
  dimension(id: string, value: number): Promise<Sketch>;
  beginDrag(id: string): Promise<void>;
  dragTo(id: string, target: XY): Promise<DragResult>;
  endDrag(cancel?: boolean): Promise<void>;
  undo(redo?: boolean): Promise<void>;
}
export interface SketchCanvasProps {
  sketch: Sketch; diagnosis?: Diagnosis; references: Reference[]; problems: DomainProblem[];
  session?: CanvasSession; entityStyle?: (entity: Entity) => CSSProperties;
  problemAction?: (problem: DomainProblem) => { label: string; constraint: NewConstraint } | undefined;
  entityClassName?: (entity: Entity) => string;
  hoverId?: string; onHoverChange?: (id?: string) => void; nameFor?: (id: string) => string;
  onLimitChange?: (radius: number) => void;
  fitRequest?: number; selection?: string[]; onSelectionChange?: (selection: string[]) => void;
}
declare global { interface Window { __sketchPerf?: { frames: number[]; solveMs: number[]; reset(): void } } }
type Tool = 'select' | 'line' | 'tangent' | 'arc3' | 'dimension' | 'split';
const hints: Record<Tool, string> = {
  select: 'Select or drag · Shift adds · Delete removes · Esc cancels',
  line: 'Click to chain lines · type length, Tab angle · double-click ends · Ctrl/Cmd suppresses snaps',
  tangent: 'Choose a highlighted chain end, or click empty space to start a 3-point arc · A: 3-point arc',
  arc3: 'Click start, end, then a point on the arc · A: tangent arc',
  dimension: 'Click a curve or two points · Alt-click two lines for angle · Enter applies · Esc keeps the measurement', split: 'Click inside a curve to split',
};
const symbols: Record<string, string> = { horizontal: 'H', vertical: 'V', coincident: '●', tangent: '⌒', fix: '▣', equal: '=', pointOnLine: '⊙', pointOnArc: '⊙' };
function initialView(sketch: Sketch, width = 640, height = 360): View {
  const ids = new Set(sketch.entities.filter(e => !e.reference && !dimensionHelper(e)).flatMap(e => e.kind === 'arc' ? [e.start, e.end] : entityPoints(e)));
  const pts = sketch.points.filter(p => ids.has(p.id));
  // Include only extrema on the finite arc, including major arcs.
  for (const e of sketch.entities) if (e.kind === 'arc' && !dimensionHelper(e)) {
    const g = arcGeometry(sketch, e);
    for (const angle of [0, Math.PI / 2, Math.PI, 3 * Math.PI / 2]) {
      const p = { id: '', x: g.center.x + g.radius * Math.cos(angle), y: g.center.y + g.radius * Math.sin(angle) };
      if (onArc(sketch, e, p)) pts.push(p);
    }
  }
  for (const e of sketch.entities) if (e.reference && e.kind === 'line') {
    const a = sketch.points.find(p => p.id === e.p1)!, b = sketch.points.find(p => p.id === e.p2)!;
    if (a.x === b.x && a.x > 0) pts.push(a);
  }
  const named = sketch.constraints.filter(c => c.label && 'value' in c);
  const bottom = named.filter(c => c.kind === 'distanceX').length * 24;
  const top = named.filter(c => c.kind === 'distanceY').length * 24;
  const right = named.length ? 170 : 0;
  const fitted = fit({ minX: Math.min(0, ...pts.map(p => p.x)), maxX: Math.max(1, ...pts.map(p => p.x)), minY: Math.min(0, ...pts.map(p => p.y)), maxY: Math.max(1, ...pts.map(p => p.y)) }, { width: Math.max(100, width - right), height: Math.max(100, height - bottom - top) }, 32);
  return { ...fitted, offsetX: fitted.offsetX + (named.length ? 48 : 0), offsetY: fitted.offsetY + top };
}
export function SketchCanvas({ sketch, diagnosis, references, problems, session, entityStyle, problemAction, selection: controlled, onSelectionChange, fitRequest, entityClassName, hoverId, onHoverChange, nameFor, onLimitChange }: SketchCanvasProps) {
  const svg = useRef<SVGSVGElement>(null);
  const dimensionInput = useRef<HTMLInputElement>(null);
  const labelDrag = useRef<{ id: string; screen: XY; client: XY; placement: DimensionPlacement; next?: DimensionPlacement } | undefined>(undefined);
  const [dimensionPreview, setDimensionPreview] = useState<{ id: string; placement: DimensionPlacement }>();
  const labelDown = useRef<{ id: string; time: number } | undefined>(undefined);
  const [size, setSize] = useState({ width: 640, height: 360 });
  const [view, setView] = useState(() => initialView(sketch));
  const [dimensionAxis, setDimensionAxis] = useState<'x' | 'y'>();
  const [renderFrame, setRenderFrame] = useState(0);
  const [arcMenu, setArcMenu] = useState(false);
  const [viewOptions, setViewOptions] = useState(readSketchView);
  const [hoveredConstraint, setHoveredConstraint] = useState<string>();
  const [arcTool, setArcTool] = useState<'tangent' | 'arc3'>('arc3');
  const [tool, setTool] = useState<Tool>('select');
  const [chain, writeChain] = useState<LineState>(lineState);
  const chainRef = useRef(chain);
  const setChain = (next: LineState) => { chainRef.current = next; writeChain(next); };
  const [localSelection, setLocalSelection] = useState<string[]>([]);
  const selection = controlled ?? localSelection;
  const [cursor, setCursor] = useState<XY>({ x: 0, y: 0 });
  const [snap, setSnap] = useState<SnapResult | null>(null);
  const [message, setMessage] = useState('');
  const rejectionOrigin = useRef<XY | undefined>(undefined);
  const [limitEditing, setLimitEditing] = useState<string>();
  const [pickReason, setPickReason] = useState('');
  const [hoveredArc, setHoveredArc] = useState<string>();
  const [hoveredPoint, setHoveredPoint] = useState<string>();
  const [conflicts, setConflicts] = useState<string[]>([]);
  const [typed, setTyped] = useState<{ length: string; angle: string }>();
  const [editing, setEditing] = useState<{ id: string; value: string; reason?: string }>();
  const cursorRef = useRef({ at: cursor, snap });
  const [pending, setPending] = useState(false);
  const pendingCount = useRef(0);
  const work = useRef<Promise<unknown>>(Promise.resolve());
  const hovered = useRef(false);
  const anglePick = useRef<string | undefined>(undefined);
  const [refusals, setRefusals] = useState(0);
  const space = useRef(false);
  const drag = useRef<{ id: string; ready: Promise<void> } | undefined>(undefined);
  const panning = useRef<XY | undefined>(undefined);
  const gesture = useRef<{ id: string; screen: XY } | undefined>(undefined);
  const perf = useRef<{ start: number; solveMs: number } | undefined>(undefined);
  const [perfEnabled] = useState(() => typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('sketchPerf') === '1');
  const alive = useRef(true);
  const latest = useRef({ sketch, session });
  const placementSketch = useRef(sketch);
  if (latest.current.sketch !== sketch) placementSketch.current = sketch;
  latest.current = { sketch, session };
  const setSelection = (ids: string[]) => { setLocalSelection(ids); onSelectionChange?.(ids); };
  const point = (id: string) => sketch.points.find(p => p.id === id)!;
  const xy = (id: string) => worldToScreen(view, point(id));
  const from = (ref?: PointRef): XY | undefined => typeof ref === 'string' ? sketch.points.find(p => p.id === ref) : ref?.snap ?? ref;
  const drawing = tool === 'line' || tool === 'tangent' || tool === 'arc3';
  const fixedPoints = new Set(sketch.entities.filter(e => e.reference || references.some(r => r.id === e.id)).flatMap(entityPoints));
  const fixed = new Set([...fixedPoints, ...sketch.constraints.flatMap(c => c.kind === 'fix' ? [c.point] : [])]);
  const determined = (id: string) => diagnosis?.pointDetermined?.[id] ?? (diagnosis?.dof === 0 || fixed.has(id));
  const selectedConstraintRefs = sketch.constraints.filter(c => selection.includes(c.id) || conflicts.includes(c.id)).flatMap(constraintRefs);
  const highlighted = new Set([...selection, ...selectedConstraintRefs]);
  const conflictRefs = new Set(sketch.constraints.filter(c => diagnosis?.constraintStates[c.id] === 'conflicting' || conflicts.includes(c.id)).flatMap(constraintRefs));
  const color = (ids: string[]) => ids.some(id => conflictRefs.has(id)) ? 'conflicting' : ids.every(determined) ? 'determined' : 'under';
  const arcs = sketch.entities.filter(e => e.kind === 'arc');
  const endpoints = new Set(sketch.entities.flatMap(e => e.kind === 'line' ? [e.p1, e.p2] : e.kind === 'arc' ? [e.start, e.end] : []));
  const hiddenCentres = arcs.map(e => e.center).filter(id => !endpoints.has(id) && !selection.includes(id) && hoveredPoint !== id
    && !arcs.some(e => e.center === id && (selection.includes(e.id) || (hoveredArc === e.id || hoverId === e.id))));
  const helperPoints = new Set(sketch.entities.filter(dimensionHelper).flatMap(entityPoints).filter(id => !sketch.entities.some(e => !dimensionHelper(e) && entityPoints(e).includes(id))));
  const visiblePoints = sketch.points.filter(p => !fixedPoints.has(p.id) && !hiddenCentres.includes(p.id) && !helperPoints.has(p.id));
  const validStarts = new Set(drawing && tool !== 'line' ? visiblePoints.filter(p => {
    try { tangentIncoming(sketch, p.id); return true; } catch { return false; }
  }).map(p => p.id) : []);
  const convertible = selection.length === 1 ? sketch.entities.find(e => e.id === selection[0] && !e.reference
    && !references.some(r => r.id === e.id) && (e.kind === 'line' || e.kind === 'arc')) : undefined;
  function error(reason: unknown) {
    if (!alive.current) return;
    setMessage(reason instanceof Error ? reason.message : String(reason));
    if (reason && typeof reason === 'object' && 'conflicting' in reason && Array.isArray(reason.conflicting)) setConflicts(reason.conflicting);
  }
  function run(action: () => Promise<unknown>) {
    if (!session) return Promise.resolve();
    pendingCount.current++; setPending(true);
    const result = work.current.then(async () => {
      if (!alive.current) return;
      setMessage('');
      try { await action(); if (session.notice && alive.current) setMessage(session.notice); }
      catch (reason) { error(reason); }
      finally { pendingCount.current--; if (alive.current) setPending(pendingCount.current > 0); }
    });
    work.current = result.catch(() => {}); return result;
  }
  const frames = useRef<ReturnType<typeof coalesceTargets<{ id: string; target: XY; start?: number }>> | undefined>(undefined);
  if (!frames.current) frames.current = coalesceTargets(requestAnimationFrame, cancelAnimationFrame, target => {
    const active = drag.current;
    if (!active) return;
    void active.ready.then(() => latest.current.session!.dragTo(target.id, target.target)).then(result => {
      if (!alive.current) return;
      if (perfEnabled && target.start !== undefined) { perf.current = { start: target.start, solveMs: latest.current.session?.lastSolveMs ?? 0 }; setRenderFrame(v => v + 1); }
      setMessage(result.stopped ? `Limited by constraints${result.reason ? `: ${result.reason}` : ''}` : '');
    }, error);
  });
  useEffect(() => {
    alive.current = true;
    if (perfEnabled) window.__sketchPerf = { frames: [], solveMs: [], reset() { this.frames.length = 0; this.solveMs.length = 0; } };
    return () => { alive.current = false; frames.current?.cancel(); if (perfEnabled) delete window.__sketchPerf; };
  }, [perfEnabled]);
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
  useLayoutEffect(() => {
    if (!perfEnabled || perf.current === undefined) return;
    const { start, solveMs } = perf.current; perf.current = undefined;
    const frame = requestAnimationFrame(() => { window.__sketchPerf?.frames.push(performance.now() - start); window.__sketchPerf?.solveMs.push(solveMs); });
    return () => cancelAnimationFrame(frame);
  }, [sketch, perfEnabled, message, renderFrame]);
  useEffect(() => {
    const element = svg.current; if (!element) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault(); const rect = element.getBoundingClientRect();
      const cursor = { x: (event.clientX - rect.left) * size.width / (rect.width || size.width), y: (event.clientY - rect.top) * size.height / (rect.height || size.height) };
      setView(view => zoomAt(view, cursor, Math.max(.01 / view.scale, Math.min(100000 / view.scale, Math.exp(-event.deltaY * .001)))));
    };
    element.addEventListener('wheel', wheel, { passive: false });
    return () => element.removeEventListener('wheel', wheel);
  }, [size.width, size.height]);
  useEffect(() => {
    if (!svg.current || typeof ResizeObserver === 'undefined') return;
    let previous = { width: 640, height: 360 }, measured = false;
    const observer = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      if (width > 64 && height > 64) { const dx = (width - previous.width) / 2, dy = (height - previous.height) / 2; previous = { width, height }; const first = !measured; measured = true; setSize(previous); setView(view => first ? initialView(latest.current.sketch, width, height) : pan(view, dx, dy)); }
    }); observer.observe(svg.current); return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => {
    if (fitRequest !== undefined) { setView(initialView(latest.current.sketch, size.width, size.height)); setEditing(undefined); }
  }, [fitRequest]);
  useLayoutEffect(() => {
    if (editing) { dimensionInput.current?.focus({ preventScroll: true }); dimensionInput.current?.select(); }
  }, [editing?.id, refusals]);
  useEffect(() => {
    if (!pickReason) return;
    const timer = setTimeout(() => setPickReason(''), 1500); return () => clearTimeout(timer);
  }, [pickReason]);
  useEffect(() => {
    if (!conflicts.length) return;
    const timer = setTimeout(() => setConflicts([]), 1800); return () => clearTimeout(timer);
  }, [conflicts]);
  function position(event: { clientX: number; clientY: number }) {
    const rect = svg.current!.getBoundingClientRect();
    return { x: (event.clientX - rect.left) * size.width / (rect.width || size.width), y: (event.clientY - rect.top) * size.height / (rect.height || size.height) };
  }
  function choose(next: Tool) {
    if (next === 'tangent' || next === 'arc3') setArcTool(next);
    if (next === 'dimension') setSelection([]);
    labelDown.current = undefined; anglePick.current = undefined;
    setArcMenu(false); setTool(next); setTyped(undefined); setEditing(undefined); setMessage(''); setPickReason(''); setSnap(null);
    const selectedEnd = selection.find(id => sketch.points.some(p => p.id === id) && !fixedPoints.has(id));
    const candidate = chain.from ?? selectedEnd;
    let tangentEnd: string | undefined;
    if (typeof candidate === 'string') { try { tangentIncoming(sketch, candidate); tangentEnd = candidate; } catch { /* Wait for a valid start. */ } }
    setChain({ mode: next === 'arc3' ? 'arc3' : next === 'tangent' ? 'tangent' : 'line', from: next === 'tangent' ? tangentEnd : next === 'arc3' ? candidate : undefined });
    svg.current?.focus({ preventScroll: true });
  }
  function place(at: XY, snapping: SnapResult | null, dimensions?: { length: number; angle?: number }) {
    return run(async () => {
      const before = placementSketch.current, previous = chainRef.current, placed = placeLine(previous, at, snapping, before);
      setPickReason(''); rejectionOrigin.current = worldToScreen(view, at);
      if (!placed.command) {
        setChain(placed.state);
        if (placed.state.mode !== previous.mode) { setTool(placed.state.mode); setArcTool(placed.state.mode === 'arc3' ? 'arc3' : 'tangent'); }
        if (placed.reason) { setMessage(placed.reason); if (placed.state === previous) setPickReason(placed.reason); }
        return;
      }
      let after: Sketch;
      try { after = await session!.command(placed.command, dimensions); }
      catch (reason) { setPickReason(reason instanceof Error ? reason.message : String(reason)); throw reason; }
      placementSketch.current = after;
      setChain(continueLine(placed.state, before, after)); setTyped(undefined); svg.current?.focus({ preventScroll: true });
    });
  }
  function removeSelection() {
    const commands: Command[] = [];
    for (const id of selection) {
      if (sketch.constraints.some(c => c.id === id)) commands.push({ kind: 'removeConstraint', id });
      else {
        const entity = sketch.entities.find(e => e.id === id);
        if (entity && !entity.reference) commands.push({ kind: 'deleteEntity', id });
        else if (sketch.points.some(p => p.id === id) && !fixedPoints.has(id)) {
          if (!sketch.entities.some(e => selection.includes(e.id) && entityPoints(e).includes(id))) commands.push({ kind: 'deletePoint', id });
        }
      }
    }
    const unique = [...new Map(commands.map(c => ['id' in c ? c.id : '', c])).values()];
    // Geometry deletion also removes dangling constraints.
    const deleting = unique.filter(c => c.kind === 'deleteEntity');
    const removedRefs = new Set([...deleting.flatMap(c => c.kind === 'deleteEntity' ? [c.id, ...entityPoints(sketch.entities.find(e => e.id === c.id)!)] : []), ...unique.flatMap(c => c.kind === 'deletePoint' ? [c.id] : [])]);
    const filtered = unique.filter(c => c.kind !== 'removeConstraint' || !constraintRefs(sketch.constraints.find(v => v.id === c.id)!).some(id => removedRefs.has(id)));
    if (filtered.length) void run(async () => { await session!.command(filtered.sort((a, b) => Number(b.kind === 'removeConstraint') - Number(a.kind === 'removeConstraint'))); setSelection([]); svg.current?.focus({ preventScroll: true }); });
  }
  function editDimension(c: Constraint) {
    if ('value' in c) setEditing({ id: c.id, value: String(c.kind === 'angle' ? c.value * 180 / Math.PI : c.value) });
  }
  function applyDimension(ids = selection) {
    const c = dimensionFor(sketch, ids, dimensionAxis);
    if (c) void run(async () => {
      const after = await session!.constraint(c, true);
      const added = after.constraints.find(c => !sketch.constraints.some(old => old.id === c.id));
      if (added) editDimension(added);
    });
  }
  function keyboard(event: KeyboardEvent) {
    if ((event.target as Element).matches('input, select, textarea')) return;
    const key = event.key.toLowerCase();
    if (key === 'escape') {
      event.stopPropagation(); event.preventDefault(); setArcMenu(false); frames.current?.cancel();
      if (drag.current) { const active = drag.current; drag.current = undefined; void active.ready.then(() => session!.endDrag(true)).catch(error); }
      labelDrag.current = undefined; setDimensionPreview(undefined); labelDown.current = undefined;
      panning.current = undefined; gesture.current = undefined;
      if (typed) setTyped(undefined); else { choose('select'); setSelection([]); }
      return;
    }
    if (drag.current || labelDrag.current?.next) { event.preventDefault(); return; }
    const target = event.target as Element;
    if (key === ' ' && target.closest('button')) return;
    if (key.startsWith('arrow') && svg.current?.contains(target) && !drawing) {
      const items = [...svg.current.querySelectorAll<SVGElement>('[role="button"][data-id]')];
      if (items.length) {
        event.preventDefault();
        const index = items.indexOf(target as SVGElement), direction = key === 'arrowleft' || key === 'arrowup' ? -1 : 1;
        const next = items[index < 0 ? direction > 0 ? 0 : items.length - 1 : (index + direction + items.length) % items.length];
        next.focus({ preventScroll: true }); setSelection([next.dataset.id!]);
      }
      return;
    }
    if (key === 'enter' || key === ' ') {
      const id = target.getAttribute('data-id');
      if (id && svg.current?.contains(target)) {
        event.preventDefault();
        const constraint = sketch.constraints.find(c => c.id === id);
        if (constraint && 'value' in constraint) editDimension(constraint); else setSelection(select(selection, id, event.shiftKey));
        return;
      }
      const active = document.activeElement;
      if (key === ' ' && (active === svg.current || hovered.current && (!active || active === document.body))) {
        event.preventDefault(); space.current = true;
      }
      return;
    }
    if ((event.ctrlKey || event.metaKey) && key === 'z') { event.preventDefault(); void run(() => session!.undo(event.shiftKey)); return; }
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (key === 'l') choose('line');
    else if (key === 'a') choose(tool === 'tangent' ? 'arc3' : tool === 'arc3' ? 'tangent' : arcTool);
    else if (key === 'd') { if (dimensionFor(sketch, selection, dimensionAxis)) applyDimension(); else choose('dimension'); }
    else if (key === 's') choose('split');
    else if (key === 'f') setView(initialView(sketch, size.width, size.height));
    else if (key === 'delete' || key === 'backspace') { event.preventDefault(); removeSelection(); }
    else if (key === 'x') {
      const commands = sketch.entities.filter(e => selection.includes(e.id) && !e.reference).map(e => ({ kind: 'setConstruction' as const, id: e.id, construction: !e.construction }));
      if (commands.length) void run(() => session!.command(commands));
    } else if (/^[0-9.]$/.test(key) && tool === 'line' && chain.from) { event.preventDefault(); setTyped({ length: key, angle: '' }); }
  }
  function pointerDown(event: PointerEvent<SVGSVGElement>) {
    event.preventDefault();
    const label = (event.target as Element).closest('[data-constraint-id]');
    const constraint = sketch.constraints.find(c => c.id === label?.getAttribute('data-constraint-id'));
    if (event.button === 0 && constraint) {
      const now = performance.now(), previous = labelDown.current; labelDrag.current = undefined;
      setSelection(select(selection, constraint.id, event.shiftKey));
      if (previous?.id === constraint.id && now - previous.time <= 400) { editDimension(constraint); labelDown.current = undefined; }
      else {
        labelDown.current = { id: constraint.id, time: now }; svg.current?.focus({ preventScroll: true });
        const layout = layouts.get(constraint.id);
        if (session && viewOptions.dimensions && layout) labelDrag.current = { id: constraint.id, screen: position(event), client: { x: event.clientX, y: event.clientY }, placement: layout.placement };
      }
      return;
    }
    labelDown.current = undefined; labelDrag.current = undefined; setDimensionPreview(undefined);
    event.currentTarget.setPointerCapture?.(event.pointerId);
    svg.current?.focus({ preventScroll: true });
    const screen = position(event), at = screenToWorld(view, screen);
    setCursor(at); cursorRef.current.at = at;
    if (event.button === 1 || space.current) {
      event.preventDefault(); panning.current = screen; event.currentTarget.setPointerCapture?.(event.pointerId); return;
    }
    if (event.button !== 0 || !session || drag.current) return;
    const target = (event.target as Element).closest('[data-id]'); const id = target?.getAttribute('data-id');
    if (drawing) {
      if (event.detail > 1) return;
      if (id && sketch.points.some(p => p.id === id) && !fixedPoints.has(id)) {
        if (tool === 'tangent' && !chainRef.current.from) {
          void place(at, { ...point(id), glyph: 'endpoint', constraint: { kind: 'coincident', point: id } }); return;
        }
        if (tool === 'line' && pendingCount.current === 0 && (!chainRef.current.from || chainRef.current.from === id)) {
          setChain({ from: id, mode: 'line' }); gesture.current = { id, screen }; event.currentTarget.setPointerCapture?.(event.pointerId); return;
        }
      }
      const snapping = findSnap(sketch, references, at, 1 / view.scale, snap, { suppress: event.ctrlKey || event.metaKey, exclude: hiddenCentres, lastPoint: typeof chain.from === 'string' ? chain.from : undefined });
      void place(at, snapping); return;
    }
    if (id && fixedPoints.has(id)) return;
    if (!id) { if (!event.shiftKey) setSelection([]); return; }
    if (tool === 'split' && sketch.entities.some(e => e.id === id && !e.reference)) {
      const p = projectToEntity(sketch, id, at.x, at.y); void run(() => session.command({ kind: 'splitAt', entity: id, ...p })); return;
    }
    if (tool === 'dimension') {
      if (event.altKey && sketch.entities.some(e => e.id === id && e.kind === 'line')) {
        const first = anglePick.current; anglePick.current = first && first !== id ? undefined : id;
        const ids = first && first !== id ? [first, id] : [id];
        setSelection(ids); if (ids.length === 2) applyDimension(ids); return;
      }
      anglePick.current = undefined;
      const pair = selection.length === 1 && selection[0] !== id && sketch.points.some(p => p.id === id)
        && sketch.points.some(p => p.id === selection[0]);
      const ids = pair ? [selection[0], id] : [id];
      setSelection(ids); applyDimension(ids); return;
    }
    setSelection(select(selection, id, event.shiftKey));
    if (tool === 'select' && sketch.points.some(p => p.id === id) && !fixedPoints.has(id)) {
      const ready = session.beginDrag(id); drag.current = { id, ready }; void ready.catch(error);
      event.currentTarget.setPointerCapture?.(event.pointerId);
    }
  }
  function pointerMove(event: PointerEvent<SVGSVGElement>) {
    const start = perfEnabled && drag.current ? performance.now() : undefined;
    const screen = position(event), at = screenToWorld(view, screen); setCursor(at); cursorRef.current.at = at;
    const label = labelDrag.current;
    if (label) {
      // A release outside the canvas before the drag threshold leaves no pointerup here: never drag without the button held.
      if (!viewOptions.dimensions || (event.buttons & 1) === 0) { labelDrag.current = undefined; setDimensionPreview(undefined); return; }
      if (label.next || Math.hypot(event.clientX - label.client.x, event.clientY - label.client.y) >= 4) {
        const c = sketch.constraints.find(c => c.id === label.id)!;
        label.next = dragDimensionPlacement(sketch, c, label.placement, { x: (screen.x - label.screen.x) / view.scale, y: (label.screen.y - screen.y) / view.scale });
        labelDown.current = undefined; event.currentTarget.setPointerCapture?.(event.pointerId);
        setDimensionPreview({ id: label.id, placement: label.next });
      }
      return;
    }
    const targetId = (event.target as Element).closest('[data-id]')?.getAttribute('data-id');
    setHoveredConstraint(sketch.constraints.some(c => c.id === targetId) ? targetId! : undefined);
    setHoveredArc(arcs.some(e => e.id === targetId) ? targetId! : undefined);
    setHoveredPoint(visiblePoints.some(p => p.id === targetId) ? targetId! : undefined);
    onHoverChange?.(targetId && (sketch.entities.some(e => e.id === targetId && !e.reference) || visiblePoints.some(p => p.id === targetId)) ? targetId : undefined);
    if (rejectionOrigin.current && Math.hypot(screen.x - rejectionOrigin.current.x, screen.y - rejectionOrigin.current.y) > 8) setPickReason('');
    if (panning.current) { setView(pan(view, screen.x - panning.current.x, screen.y - panning.current.y)); panning.current = screen; return; }
    if (drag.current) {
      frames.current!.push({ id: drag.current.id, target: event.ctrlKey || event.metaKey ? at : snapLimitTarget(references, drag.current.id, at, 8 / view.scale), ...(perfEnabled ? { start } : {}) }); return;
    }
    if (gesture.current && Math.hypot(screen.x - gesture.current.screen.x, screen.y - gesture.current.screen.y) > 6) {
      setChain({ from: gesture.current.id, mode: 'tangent' }); setTool('tangent');
    }
    if (drawing) { const next = findSnap(sketch, references, at, 1 / view.scale, snap, { suppress: event.ctrlKey || event.metaKey, exclude: hiddenCentres, lastPoint: typeof chain.from === 'string' ? chain.from : undefined }); setSnap(next); cursorRef.current.snap = next; }
  }
  function pointerUp(cancel = false) {
    const label = labelDrag.current; labelDrag.current = undefined;
    if (label?.next && !cancel && viewOptions.dimensions) {
      void run(() => session!.command({ kind: 'setDimensionPlacement', id: label.id, placement: label.next })).finally(() => setDimensionPreview(undefined));
    } else if (label) setDimensionPreview(undefined);
    panning.current = undefined;
    if (gesture.current) {
      if (!cancel && chainRef.current.mode === 'tangent') void place(cursorRef.current.at, cursorRef.current.snap);
      gesture.current = undefined;
    }
    const active = drag.current;
    if (!active) return;
    if (cancel) frames.current?.cancel(); else frames.current?.flush();
    drag.current = undefined;
    void active.ready.then(() => session!.endDrag(cancel)).catch(error);
  }
  const step = gridStep(view.scale), topLeft = screenToWorld(view, { x: 0, y: 0 }), bottomRight = screenToWorld(view, { x: size.width, y: size.height });
  const gridX = Array.from({ length: Math.min(200, Math.ceil((bottomRight.x - topLeft.x) / step) + 1) }, (_, i) => (Math.ceil(topLeft.x / step) + i) * step);
  const gridY = Array.from({ length: Math.min(200, Math.ceil((topLeft.y - bottomRight.y) / step) + 1) }, (_, i) => (Math.ceil(bottomRight.y / step) + i) * step);
  const constraintPosition = (c: Constraint, i: number) => {
    const ids = constraintRefs(c).flatMap(id => sketch.points.some(p => p.id === id) ? [id] : entityPoints(sketch.entities.find(e => e.id === id) ?? { kind: 'circle', id: '' }));
    const points = ids.filter(id => !fixedPoints.has(id)).map(xy);
    return { x: (points.reduce((v, p) => v + p.x, 0) / points.length || 24) + 8, y: (points.reduce((v, p) => v + p.y, 0) / points.length || 24) - 12 - i % 3 * 14 };
  };
  const contourPoints = sketch.entities.filter(e => !e.construction).flatMap(e => e.kind === 'arc' ? [e.start, e.end] : entityPoints(e)).map(xy);
  for (const e of sketch.entities) if (e.kind === 'arc' && !e.construction) {
    const g = arcGeometry(sketch, e);
    for (const angle of [0, Math.PI / 2, Math.PI, 3 * Math.PI / 2]) {
      const p = { x: g.center.x + g.radius * Math.cos(angle), y: g.center.y + g.radius * Math.sin(angle) };
      if (onArc(sketch, e, p)) contourPoints.push(worldToScreen(view, p));
    }
  }
  const dimensionBounds = { height: size.height, left: Math.min(size.width, ...contourPoints.map(p => p.x)), right: Math.max(0, ...contourPoints.map(p => p.x)), bottom: Math.max(0, ...contourPoints.map(p => p.y)), top: Math.min(size.height, ...contourPoints.map(p => p.y)) };
  const dimensionSketch = dimensionPreview ? { ...sketch, constraints: sketch.constraints.map(c => c.id === dimensionPreview.id ? { ...c, placement: dimensionPreview.placement } : c) } : sketch;
  // Reserve full names so hovering/selecting a label never changes its automatic lane.
  const layouts = dimensionLayouts(dimensionSketch, xy, dimensionBounds, view.scale, c => dimensionLabel(c, 'name'));
  const start = from(chain.from), preview = worldToScreen(view, snap ?? cursor);
  let previewPath: string | undefined;
  if (drawing && start && (chain.mode === 'arc3' && chain.end || chain.mode === 'tangent' && typeof chain.from === 'string')) {
    try {
      const end = chain.mode === 'arc3' ? from(chain.end)! : snap ?? cursor;
      const g = chain.mode === 'arc3' ? arcThrough(start, end, cursor) : tangentArc(sketch, chain.from as string, end);
      const a = worldToScreen(view, start), b = worldToScreen(view, end);
      const radius = Math.hypot(start.x - g.center.x, start.y - g.center.y) * view.scale;
      const angle = Math.atan2(end.y - g.center.y, end.x - g.center.x) - Math.atan2(start.y - g.center.y, start.x - g.center.x);
      const sweep = positiveAngle(angle) - (g.ccw ? 0 : tau);
      previewPath = `M${a.x},${a.y} A${radius},${radius} 0 ${Math.abs(sweep) > Math.PI ? 1 : 0} ${g.ccw ? 0 : 1} ${b.x},${b.y}`;
    } catch { /* A collinear cursor shows the chord until an arc is defined. */ }
  }
  const arcHint = tool === 'arc3' ? `3-point arc: ${!chain.from ? 'start 1' : !chain.end ? 'end 2' : 'point on arc 3'} of 3 · A: tangent arc` : hints[tool];
  return <div className="sketch-canvas" aria-busy={pending} onKeyDown={keyboard} onKeyUp={e => { if (e.key === ' ') space.current = false; }} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) space.current = false; }}>
    <div role="toolbar" aria-label="Sketch tools" className="sketch-toolbar">
      <div className="sketch-tool-segment" role="group" aria-label="Drawing tools">
        {(['select', 'line', 'arc', 'dimension', 'split'] as const).map(t => {
          const next = t === 'arc' ? arcTool : t;
          const label = { select: 'Select (Esc)', line: 'Line (L)', arc: arcTool === 'tangent' ? 'Tangent arc (A)' : '3-point arc (A)', dimension: 'Dimension (D)', split: 'Split (S)' }[t];
          const icons = { select: 'M4 2 13 10 8 10 6 14Z', line: 'M3 13 13 3', arc: 'M2 13Q2 2 14 3', dimension: 'M2 5V13M14 5V13M2 9H14M5 6 2 9 5 12M11 6 14 9 11 12', split: 'M2 12 7 7M10 4 14 0M6 2 14 10' };
          return <Fragment key={t}><button type="button" aria-label={label} title={label} aria-pressed={tool === next} disabled={!session} onClick={() => choose(next)}><svg aria-hidden="true" viewBox="0 0 16 16"><path d={icons[t]}/></svg><span className="sketch-tool-label">{t === 'arc' ? 'Arc' : t[0].toUpperCase() + t.slice(1)}</span></button>{t === 'arc' && <>
            <div className="sketch-menu-anchor"><button type="button" aria-label="Arc options" aria-expanded={arcMenu} onClick={() => setArcMenu(!arcMenu)}>⌄</button>
              {arcMenu && <div className="sketch-menu" role="group" aria-label="Arc modes">{(['tangent', 'arc3'] as const).map(t => <button type="button" key={t} aria-pressed={arcTool === t} onClick={() => choose(t)}>{t === 'tangent' ? 'Tangent arc (A)' : '3-point arc (A)'}</button>)}</div>}
            </div>
          </>}</Fragment>;
        })}
      </div>
      <div className="sketch-view-tools">
        {(['dimensions', 'names', 'constraints'] as const).map(key => <button type="button" key={key} aria-pressed={viewOptions[key]} onClick={() => {
          setViewOptions(previous => { const next = { ...previous, [key]: !previous[key] }; saveSketchView(next); return next; });
        }}>{key[0].toUpperCase() + key.slice(1)}</button>)}
        <button type="button" disabled={!session} onClick={() => void run(() => session!.undo())}>Undo</button><button type="button" disabled={!session} onClick={() => void run(() => session!.undo(true))}>Redo</button>
        <button type="button" title="Fit (F)" onClick={() => setView(initialView(sketch, size.width, size.height))}>Fit (F)</button>

      </div>
    </div>
    <div className="sketch-surface">
    {tool === 'dimension' && <div className="sketch-context sketch-dimension-context"><label className="sketch-dimension-kind">Measurement<select aria-label="Dimension measurement" value={dimensionAxis ?? ''} onChange={e => setDimensionAxis(e.target.value === 'x' ? 'x' : e.target.value === 'y' ? 'y' : undefined)}><option value="">Automatic</option><option value="x">Horizontal distance</option><option value="y">Vertical distance</option></select></label></div>}
    {selection.length > 0 && tool !== 'dimension' && <div role="toolbar" aria-label="Selection constraints" className="sketch-context sketch-selection-context">
      {selectionConstraints.filter(kind => constraintFor(sketch, selection, kind) && !selection.some(id => fixedPoints.has(id))).map(kind => <button type="button" key={kind} disabled={!session} onClick={() => { const c = constraintFor(sketch, selection, kind); if (c) void run(() => session!.constraint(c)); }}>{kind === 'point-on' ? 'Point on' : kind[0].toUpperCase() + kind.slice(1)}</button>)}
      {sketch.entities.some(e => selection.includes(e.id) && !e.reference && !references.some(r => r.id === e.id)) && <button type="button" disabled={!session} onClick={() => void run(() => session!.command(sketch.entities.filter(e => selection.includes(e.id) && !e.reference).map(e => ({ kind: 'setConstruction', id: e.id, construction: !e.construction }))))}>Construction (X)</button>}
      {convertible && <button type="button" disabled={!session} onClick={() => void run(() => session!.command({ kind: 'convertEntity', id: convertible.id, to: convertible.kind === 'line' ? 'arc' : 'line' }))}>{convertible.kind === 'line' ? 'Make arc' : 'Make line'}</button>}
      {sketch.constraints.some(c => selection.includes(c.id) && 'value' in c) && <button type="button" disabled={!session} onClick={() => void run(() => session!.command(sketch.constraints.filter(c => selection.includes(c.id) && 'value' in c).map(c => ({ kind: 'setDimensionPlacement', id: c.id }))))}>Reset position</button>}
      <button type="button" disabled={!session} onClick={removeSelection}>Delete selection</button>
    </div>}
      <svg ref={svg} viewBox={`0 0 ${size.width} ${size.height}`} preserveAspectRatio="xMidYMid meet" tabIndex={0} role="application" aria-label="Sketch drawing" onPointerEnter={() => { hovered.current = true; }} onPointerLeave={() => { hovered.current = false; setHoveredConstraint(undefined); setHoveredArc(undefined); setHoveredPoint(undefined); onHoverChange?.(undefined); setPickReason(''); }} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={() => pointerUp()} onPointerCancel={() => pointerUp(true)} onLostPointerCapture={() => pointerUp(true)} onDoubleClick={() => { if (drawing) choose('select'); }}>
        <g className="sketch-grid" aria-hidden="true">{gridX.map(x => <line key={`x${x}`} x1={worldToScreen(view, { x, y: 0 }).x} x2={worldToScreen(view, { x, y: 0 }).x} y1={0} y2={size.height}/>)}{gridY.map(y => <line key={`y${y}`} x1={0} x2={size.width} y1={worldToScreen(view, { x: 0, y }).y} y2={worldToScreen(view, { x: 0, y }).y}/>)}</g>
        <g className="sketch-references" aria-label="Fixed references">{references.map(r => {
          const x = r.kind === 'limit' ? worldToScreen(view, { x: r.radius, y: 0 }).x : view.offsetX;
          return <g key={r.id} className={r.kind === 'limit' ? 'sketch-limit' : undefined}>
            <line x1={r.kind === 'baffle' ? 0 : x} x2={r.kind === 'baffle' ? size.width : x} y1={r.kind === 'baffle' ? view.offsetY : 0} y2={r.kind === 'baffle' ? view.offsetY : size.height}/>
            <text x={r.kind === 'baffle' ? 8 : Math.max(4, Math.min(x + 4, size.width - Math.max(150, r.kind === 'limit' ? r.label.length * 6.4 + 16 : 150)))} y={r.kind === 'baffle' ? view.offsetY - 4 : 14}
              role={r.kind === 'limit' && !r.locked ? 'button' : undefined} tabIndex={r.kind === 'limit' && !r.locked ? 0 : undefined}
              onPointerDown={e => { if (r.kind === 'limit') e.stopPropagation(); }}
              onDoubleClick={e => { e.stopPropagation(); if (r.kind === 'limit' && !r.locked && onLimitChange) setLimitEditing(String(r.radius)); }}
              onKeyDown={e => { if (r.kind === 'limit' && !r.locked && onLimitChange && e.key === 'Enter') { e.stopPropagation(); setLimitEditing(String(r.radius)); } }}>
              <title>{r.kind === 'limit' ? r.locked ? 'Set by the horn design' : 'Double-click to edit limit' : r.kind}</title>{r.kind === 'limit' ? `${r.locked ? '🔒 ' : ''}${r.label}` : r.kind}</text>
          </g>;
        })}</g>
        {sketch.entities.filter(e => !e.reference && !dimensionHelper(e) && !references.some(r => r.id === e.id)).map(e => {
          const ids = entityPoints(e), status = conflictRefs.has(e.id) ? 'conflicting' : color(ids), issues = problems.filter(p => p.ids.includes(e.id) || ids.some(id => p.ids.includes(id)));
          let d = '';
          if (e.kind === 'line') { const a = xy(e.p1), b = xy(e.p2); d = `M${a.x},${a.y} L${b.x},${b.y}`; }
          else if (e.kind === 'arc') { const a = xy(e.start), b = xy(e.end), g = arcGeometry(sketch, e); d = `M${a.x},${a.y} A${g.radius * view.scale},${g.radius * view.scale} 0 ${Math.abs(g.sweep) > Math.PI ? 1 : 0} ${e.ccw ? 0 : 1} ${b.x},${b.y}`; }
          let labelAt;
          if (viewOptions.names) {
            if (e.kind === 'line') { const a = xy(e.p1), b = xy(e.p2); labelAt = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; }
            else if (e.kind === 'arc') { const g = arcGeometry(sketch, e), angle = g.start + g.sweep / 2; labelAt = worldToScreen(view, { x: g.center.x + g.radius * Math.cos(angle), y: g.center.y + g.radius * Math.sin(angle) }); }
          }
          const entityName = nameFor?.(e.id) ?? displayName(sketch, e.id);
          return <g key={e.id} className="sketch-curve"><path data-id={e.id} d={d} className="sketch-curve-hit"/>{issues.length > 0 && <path d={d} className="sketch-problem"/>}<path data-id={e.id} d={d} tabIndex={-1} role="button" aria-label={`Select ${e.kind} ${nameFor?.(e.id) ?? e.id}`} style={entityStyle?.(e)} className={`sketch-entity ${entityClassName?.(e) ?? ''} ${status} ${hoverId === e.id ? 'hovered' : ''} ${e.construction ? 'construction' : ''} ${highlighted.has(e.id) || ids.some(id => highlighted.has(id)) ? 'selected' : ''}`}><title>{entityName} ({e.id}){issues.length > 0 ? ` · ${issues.map(p => p.message).join('\n')}` : ''}</title></path>{labelAt && <text data-id={e.id} x={labelAt.x} y={labelAt.y - 10} textAnchor="middle" className="sketch-curve-name sketch-glyph" aria-hidden="true">{entityName}</text>}</g>;
        })}
        {visiblePoints.map(p => { const at = xy(p.id), centre = !endpoints.has(p.id) && arcs.some(e => e.center === p.id);
          const className = `sketch-point ${color([p.id])} ${hoverId === p.id ? 'hovered' : ''} ${highlighted.has(p.id) ? 'selected' : ''} ${validStarts.has(p.id) ? 'valid-start' : ''}`;
          return <g key={p.id} className="sketch-handle"><circle data-id={p.id} cx={at.x} cy={at.y} r={10} className="sketch-point-hit"/>
            {validStarts.has(p.id) && <circle cx={at.x} cy={at.y} r={8} className="sketch-arc-start"/>}
            {centre ? <path data-id={p.id} d={`M${at.x - 5},${at.y} h10 M${at.x},${at.y - 5} v10`} tabIndex={-1} role="button" aria-label={`Select point ${nameFor?.(p.id) ?? p.id}`} className={`${className} sketch-centre`}><title>{p.id}: {p.x}, {p.y} mm</title></path>
              : <circle data-id={p.id} cx={at.x} cy={at.y} r={4} tabIndex={-1} role="button" aria-label={`Select point ${nameFor?.(p.id) ?? p.id}`} className={className}><title>{p.id}: {p.x}, {p.y} mm{problems.filter(v => v.ids.includes(p.id)).map(v => ` · ${v.message}`)}</title></circle>}
          </g>; })}
        {sketch.constraints.map((c, i) => { const dimension = 'value' in c;
          const related = new Set([c.id, ...constraintRefs(c).flatMap(id => [id, ...entityPoints(sketch.entities.find(e => e.id === id) ?? { kind: 'circle', id: '' })])]);
          const problem = diagnosis?.constraintStates[c.id] === 'conflicting' || conflicts.includes(c.id) || problems.some(p => p.ids.some(id => related.has(id)));
          const active = selection.includes(c.id) || hoveredConstraint === c.id || hoverId === c.id;
          if (!(dimension ? viewOptions.dimensions : viewOptions.constraints) && !problem && !active) return null;
          const layout = layouts.get(c.id);
          const at = layout?.at ?? constraintPosition(c, i);
          const label = dimension ? dimensionLabel(c, viewOptions.names || active ? 'name' : 'value') : symbols[c.kind] ?? c.kind;
          return <g key={c.id} onPointerEnter={() => setHoveredConstraint(c.id)} onPointerLeave={() => setHoveredConstraint(undefined)} data-constraint-id={c.id} className={dimension ? 'sketch-dimension' : undefined}>
            {layout && <g className="sketch-dimension-lines" aria-hidden="true"><path className="sketch-extension-lines" d={layout.path}/><path className="sketch-dimension-arrows" d={layout.arrows}/></g>}
            <g transform={layout?.rotation ? `rotate(${layout.rotation} ${at.x} ${at.y})` : undefined}><rect data-id={c.id} x={at.x - (layout ? label.length * 3.2 : 0) - 6} y={at.y - 17} width={dimension ? label.length * 6.4 + 12 : 24} height={28} className="sketch-label-hit"/><text textAnchor={layout ? 'middle' : undefined} data-id={c.id} x={at.x} y={at.y} tabIndex={-1} role="button" aria-label={`${dimension ? 'Dimension' : 'Constraint'} ${c.kind} ${c.id}${dimension ? ` ${dimensionLabel(c, 'name')}` : c.label ? ` ${c.label}` : ''}${diagnosis?.constraintStates[c.id] === 'redundant' ? ' implied' : ''}`} className={`sketch-glyph ${dimension && !c.driving ? 'reference' : ''} ${diagnosis?.constraintStates[c.id] ?? ''} ${conflicts.includes(c.id) ? 'conflicting' : ''} ${problem ? 'problem' : ''} ${active ? 'selected' : ''}`} onDoubleClick={event => { event.stopPropagation(); if (dimension) editDimension(c); }}><title>{dimension ? dimensionLabel(c, 'name') : c.kind} {c.id}{diagnosis?.constraintStates[c.id] === 'redundant' ? ' (implied)' : ''}</title>{label}</text></g></g>;
        })}
        {previewPath && <path className="sketch-preview" d={previewPath}/>}
        {drawing && start && !previewPath && <line className="sketch-preview" x1={worldToScreen(view, start).x} y1={worldToScreen(view, start).y} x2={preview.x} y2={preview.y}/>}
        {drawing && snap && <g className="sketch-pending" aria-label={`Pending ${snap.glyph} snap`}><circle cx={preview.x} cy={preview.y} r={7}/><text x={preview.x + 10} y={preview.y - 8}>{snap.glyph}</text></g>}
      </svg>
      {pickReason && <div role="tooltip" className="sketch-pick-tooltip" style={{ left: Math.max(4, Math.min(preview.x + 12, size.width - 252)), top: Math.max(4, Math.min(preview.y + 12, size.height - 60)) }}>{pickReason}</div>}
      {limitEditing !== undefined && <form className="sketch-inline" aria-label="Edit limit" onSubmit={event => {
        event.preventDefault(); const value = Number(limitEditing);
        if (Number.isFinite(value) && value > 0) { onLimitChange?.(value); setLimitEditing(undefined); }
      }}><label>Limit radius<input aria-label="Limit radius" autoFocus type="number" min="0.001" step="any" value={limitEditing} onChange={e => setLimitEditing(e.target.value)} onKeyDown={e => { e.stopPropagation(); if (e.key === 'Escape') setLimitEditing(undefined); }}/></label><button type="submit">Apply limit</button></form>}
      {typed && <div className="sketch-inline" style={{ left: Math.min(preview.x + 12, size.width - 170), top: Math.min(preview.y + 12, size.height - 80) }}>
        {(['length', 'angle'] as const).map(field => <label key={field}>{field === 'length' ? 'Length (mm)' : 'Angle (°)'}<input aria-label={`Line ${field}`} autoFocus={field === 'length'} value={typed[field]} onChange={event => setTyped({ ...typed, [field]: event.target.value })} onKeyDown={event => {
          event.stopPropagation();
          if (event.key === 'Escape') { event.preventDefault(); setTyped(undefined); svg.current?.focus({ preventScroll: true }); }
          if (event.key === 'Enter' && start) {
            event.preventDefault(); const length = Number(typed.length), angle = typed.angle.trim() ? Number(typed.angle) : undefined;
            if (!(length > 0) || !Number.isFinite(length) || angle !== undefined && !Number.isFinite(angle)) { setMessage('Enter a positive length and a finite angle.'); return; }
            void place(typedTarget(start, cursor, length, angle), null, { length, angle });
          }
        }}/></label>)}
      </div>}
      {editing && <form className="sketch-inline" aria-label="Edit dimension" onSubmit={event => {
        event.preventDefault(); const c = sketch.constraints.find(c => c.id === editing.id), value = Number(editing.value) * (c?.kind === 'angle' ? Math.PI / 180 : 1);
        if (!session || !Number.isFinite(value)) { setEditing({ ...editing, reason: 'Enter a finite number.' }); setRefusals(v => v + 1); return; }
        void session.dimension(editing.id, value).then(() => { setEditing(undefined); svg.current?.focus({ preventScroll: true }); }, reason => { setEditing({ ...editing, reason: reason instanceof Error ? reason.message : String(reason) }); setRefusals(v => v + 1); });
      }}><label>Dimension value<input ref={dimensionInput} aria-label="Dimension value" value={editing.value} onChange={event => setEditing({ ...editing, value: event.target.value })} onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setEditing(undefined); svg.current?.focus({ preventScroll: true }); } }}/></label><button type="submit">Apply dimension value</button>{editing.reason && <span role="alert">{editing.reason}</span>}</form>}
    </div>
    <div role="status" className="sketch-status"><span className={`sketch-dof ${diagnosis?.dof === 0 ? 'complete' : ''}`}>{diagnosis?.dof === 0 ? 'Fully constrained' : `${diagnosis?.dof ?? '…'} DOF`}</span>{problems.length > 0 && ` · ${problems.length} problem${problems.length === 1 ? '' : 's'}`} · x {cursor.x.toFixed(2)}, y {cursor.y.toFixed(2)} mm · Snap: {snap?.glyph ?? 'none'} · {message || arcHint}</div>
    {diagnosis && sketch.constraints.some(c => diagnosis.constraintStates[c.id] === 'redundant') && <p>Implied: {sketch.constraints.filter(c => diagnosis.constraintStates[c.id] === 'redundant').map(c => c.id).join(', ')}</p>}
    {problems.map((problem, i) => { const action = problemAction?.(problem); return <div className="sketch-domain-problem" key={`${problem.code}${i}`}>{problem.message}{problem.ids.filter(id => { const data = sketch.entities.find(e => e.id === id)?.data; return data && typeof data === 'object' && !Array.isArray(data) && typeof data.name === 'string' && data.name.trim(); }).map(id => ` · ${nameFor?.(id) ?? displayName(sketch, id)}`)}{action && <button type="button" disabled={!session} onClick={() => void run(() => session!.constraint(action.constraint))}>{action.label}</button>}</div>; })}
  </div>;
}
