import { createPortal } from 'react-dom';
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from 'react';
import { contextData, materialiseContext, referenceContext, referencePointIds, type ContextDrawingItem, type SketchContext } from './context';
import type { DomainProblem, Reference } from './adapter';
import type { Command, PointRef } from './commands';
import { constraintRefs, entityPoints, type Constraint, type DimensionPlacement, type Entity, type NewConstraint, type Sketch } from './model';
import type { Diagnosis, DragResult } from './session';
import { DETERMINATION_ENTITY_LIMIT, type Capabilities, type XY } from './solver/types';
import { arcGeometry, arcThrough, onArc, positiveAngle, projectToEntity, tangentArc, tangentIncoming, tau } from './geometry';
import { findSnap, snapLimitTarget, type SnapResult } from './snap';
import { fit, gridStep, pan, screenToWorld, worldToScreen, zoomAt, wheelGesture, type View } from './view';
import { continueLine, lineState, placeLine, prospectiveLine, type LineState } from './tools/line';
import { combineSelection, cycleHit, dragPointIds, finishSelect, moveSelect, selectHits, startSelect, type Cycle, type SelectState, coalesceTargets, select } from './tools/select';
import { candidateDimension, dimensionPicks, hitDimension, type CandidateDimension } from './tools/dimension';
import { constraintPalette, selectionConstraints, selectionCanConstrain, pickPatterns, rules } from './tools/constraints';
import { acceptsConstraintPick, constraintTool, pickConstraint, escapeConstraint, constraintHint, hitConstraint, type ConstraintToolState } from './tools/constraintTool';
import { SketchToolbar, type Tool } from './SketchToolbar';
import { ContextLayer } from './SketchContextLayer';
import { dimensionLabelBox, internalHelper, dimensionLabel, dimensionLayout, dimensionLayouts, dragDimensionPlacement } from './dimensionGraphics';
import { displayName } from './displayName';
import { isConstraintConflict } from './controller';
import { readSketchView, saveSketchView } from './viewPreferences';
import './sketchCanvas.css';

export interface CanvasSession {
  readonly capabilities?: Capabilities;
  readonly rejection?: unknown;
  readonly notice?: string;
  readonly lastSolveMs?: number;
  dismissRejection?(): void;
  materialiseContext?(context: SketchContext, itemId: string): Promise<{ sketch: Sketch; id: string }>;
  command(command: Command | Command[], dimensions?: { length?: number; angle?: number }): Promise<Sketch>;
  constraint(constraint: NewConstraint, reference?: boolean, placement?: boolean): Promise<Sketch>;
  dimension(id: string, value: number, mergePlacement?: boolean): Promise<Sketch>;
  beginDrag(id: string | string[]): Promise<void>;
  dragTo(id: string, target: XY, targets?: { pointId: string; x: number; y: number }[]): Promise<DragResult>;
  endDrag(cancel?: boolean): Promise<void>;
  undo(redo?: boolean): Promise<void>;
}
export interface SketchCanvasProps {
  context?: SketchContext; contextStale?: boolean;
  slots?: { toolbar?: HTMLElement | null; status?: HTMLElement | null; properties?: HTMLElement | null };
  workflow?: { state: 'unfinished' | 'invalid' | 'valid'; hint: string };
  scaleToFit?: () => void;
  sketch: Sketch; diagnosis?: Diagnosis; references: Reference[]; problems: DomainProblem[];
  session?: CanvasSession; entityStyle?: (entity: Entity) => CSSProperties;
  problemAction?: (problem: DomainProblem) => { label: string; constraint: NewConstraint } | undefined;
  entityClassName?: (entity: Entity) => string;
  hoverId?: string; onHoverChange?: (id?: string) => void; nameFor?: (id: string) => string;
  onLimitChange?: (radius: number) => void;
  fitRequest?: number; selection?: string[]; onSelectionChange?: (selection: string[]) => void;
}
declare global { interface Window { __sketchPerf?: { frames: number[]; solveMs: number[]; reset(): void } } }
const hints: Record<Tool, string> = {
  select: 'Select or drag · Shift adds · Delete removes · Esc cancels',
  hand: 'Drag to pan · Esc: Select', constraint: '',
  line: 'Click to chain lines · type length, Tab angle · double-click ends · Ctrl/Cmd suppresses snaps',
  tangent: 'Choose a highlighted chain end, or click empty space to start a 3-point arc · A: 3-point arc',
  arc3: 'Click start, end, then a point on the arc · A: tangent arc',
  dimension: 'Click a line, arc or point; click a second item if needed; move to choose; click to place; type the value', split: 'Click inside a curve to split',
};
function initialView(sketch: Sketch, width = 640, height = 360, context?: SketchContext, all = false): View {
  const ids = new Set(sketch.entities.filter(e => !e.reference && !internalHelper(e)).flatMap(e => e.kind === 'arc' ? [e.start, e.end] : entityPoints(e)));
  const pts = sketch.points.filter(p => ids.has(p.id));
  // Include only extrema on the finite arc, including major arcs.
  for (const e of sketch.entities) if (e.kind === 'arc' && !internalHelper(e)) {
    const g = arcGeometry(sketch, e);
    for (const angle of [0, Math.PI / 2, Math.PI, 3 * Math.PI / 2]) {
      const p = { id: '', x: g.center.x + g.radius * Math.cos(angle), y: g.center.y + g.radius * Math.sin(angle) };
      if (onArc(sketch, e, p)) pts.push(p);
    }
  }
  for (const e of sketch.entities) if (e.reference && !contextData(e) && e.kind === 'line') {
    const a = sketch.points.find(p => p.id === e.p1)!;
    if (a.x > 0) pts.push(a);
  }
  const extent = Math.max(1, ...pts.map(p => Math.abs(p.x)), ...pts.map(p => Math.abs(p.y)));
  const boxPoints = context?.items.filter(i => i.role === 'enclosure' || i.role === 'baffle').flatMap(i => i.geometry) ?? [];
  const nearbyBox = boxPoints.every(p => Math.max(Math.abs(p.x), Math.abs(p.y)) <= 3 * extent);
  for (const item of context?.items ?? []) if (all || ['throat', 'plug', 'rim-plane'].includes(item.role)
    || nearbyBox && ['enclosure', 'baffle', 'wall', 'mouth', 'aperture', 'collar'].includes(item.role)) pts.push(...item.geometry.map(p => ({ ...p, id: '' })));
  const named = sketch.constraints.filter(c => c.label && 'value' in c);
  const bottom = named.filter(c => c.kind === 'distanceX').length * 24;
  const top = named.filter(c => c.kind === 'distanceY').length * 24;
  const right = named.length ? 170 : 0;
  const fitted = fit({ minX: Math.min(0, ...pts.map(p => p.x)), maxX: Math.max(1, ...pts.map(p => p.x)), minY: Math.min(0, ...pts.map(p => p.y)), maxY: Math.max(1, ...pts.map(p => p.y)) }, { width: Math.max(100, width - right), height: Math.max(100, height - bottom - top) }, 32);
  return { ...fitted, offsetX: fitted.offsetX + (named.length ? 48 : 0), offsetY: fitted.offsetY + top };
}
export function SketchCanvas({ slots, workflow, scaleToFit, context, contextStale = false, sketch, diagnosis, references, problems, session, entityStyle, problemAction, selection: controlled, onSelectionChange, fitRequest, entityClassName, hoverId, onHoverChange, nameFor, onLimitChange }: SketchCanvasProps) {
  const svg = useRef<SVGSVGElement>(null);
  const dimensionInput = useRef<HTMLInputElement>(null);
  const dimensionApplying = useRef(false);
  const dimensionGeneration = useRef(0);
  const dimensionPlacement = useRef<CandidateDimension | undefined>(undefined);
  const toolRef = useRef<Tool>('select');
  const labelDrag = useRef<{ id: string; screen: XY; client: XY; placement: DimensionPlacement; next?: DimensionPlacement } | undefined>(undefined);
  const [dimensionPreview, setDimensionPreview] = useState<{ id: string; placement: DimensionPlacement }>();
  const labelDown = useRef<{ id: string; time: number } | undefined>(undefined);
  const [size, setSize] = useState({ width: 640, height: 360 });
  const [view, setView] = useState(() => initialView(sketch));
  const [dimensionIds, setDimensionIds] = useState<string[]>([]);
  const [dimensionHover, setDimensionHover] = useState<string>();
  const [dimensionPrompt, setDimensionPrompt] = useState<CandidateDimension>();
  const [renderFrame, setRenderFrame] = useState(0);
  const [constraintState, writeConstraintState] = useState<ConstraintToolState>();
  const constraintStateRef = useRef<ConstraintToolState | undefined>(undefined);
  const setConstraintState = (state: ConstraintToolState | undefined) => { constraintStateRef.current = state; writeConstraintState(state); };
  const [constraintHover, setConstraintHover] = useState<string>();
  const [panActive, setPanActive] = useState(false);
  const [viewOptions, setViewOptions] = useState(readSketchView);
  const [contextPointer, setContextPointer] = useState<XY>();
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
  const [rejected, setRejected] = useState<{ constraints: Constraint[]; reason: string; sketch: Sketch }>();
  const [showConflict, setShowConflict] = useState(false);
  const [dismissedDiagnosis, setDismissedDiagnosis] = useState<Diagnosis>();
  const conflictConstraints = rejected?.constraints ?? (diagnosis !== dismissedDiagnosis
    ? sketch.constraints.filter(c => diagnosis?.constraintStates[c.id] === 'conflicting') : []);
  const conflicts = conflictConstraints.map(c => c.id);
  const conflictSketch = { ...sketch, constraints: [...sketch.constraints.filter(c => !conflicts.includes(c.id)), ...conflictConstraints] };
  const conflictNames = conflictConstraints.map(c => displayName(conflictSketch, c.id)).join('; ');
  const [typed, setTyped] = useState<{ length: string; angle: string }>();
  const [editing, setEditing] = useState<{ id: string; value: string; reason?: string; placed?: boolean }>();
  const editingConstraint = sketch.constraints.find(c => c.id === editing?.id);
  const editingDimension = editingConstraint && 'value' in editingConstraint ? editingConstraint : undefined;
  const cursorRef = useRef({ at: cursor, snap });
  const [pending, setPending] = useState(false);
  const pendingCount = useRef(0);
  const work = useRef<Promise<unknown>>(Promise.resolve());
  const hovered = useRef(false);
  const [refusals, setRefusals] = useState(0);
  const space = useRef(false);
  const drag = useRef<{ id: string; ready: Promise<void>; origin?: XY; points?: { id: string; x: number; y: number }[] } | undefined>(undefined);
  const selecting = useRef<SelectState | undefined>(undefined);
  const cycle = useRef<Cycle | undefined>(undefined);
  const [marquee, setMarquee] = useState<SelectState>();
  const panning = useRef<XY | undefined>(undefined);
  const gesture = useRef<{ id: string; screen: XY } | undefined>(undefined);
  const perf = useRef<{ start: number; solveMs: number } | undefined>(undefined);
  const [perfEnabled] = useState(() => typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('sketchPerf') === '1');
  const alive = useRef(true);
  const latest = useRef({ sketch, session, context });
  const contextPicking = useRef(false);
  const contextPickSketch = useRef<Sketch | undefined>(undefined);
  const placementSketch = useRef(sketch);
  if (latest.current.sketch !== sketch) placementSketch.current = sketch;
  latest.current = { sketch, session, context };
  const setSelection = (ids: string[]) => { setLocalSelection(ids); onSelectionChange?.(ids); };
  const point = (id: string) => sketch.points.find(p => p.id === id)!;
  const xy = (id: string) => worldToScreen(view, point(id));
  const from = (ref?: PointRef): XY | undefined => typeof ref === 'string' ? sketch.points.find(p => p.id === ref) : ref?.snap ?? ref;
  toolRef.current = tool;
  const drawing = tool === 'line' || tool === 'tangent' || tool === 'arc3';
  const fixedPoints = referencePointIds(sketch, references);
  const fixed = new Set([...fixedPoints, ...sketch.constraints.flatMap(c => c.kind === 'fix' ? [c.point] : [])]);
  const fullyConstrained = diagnosis?.status === 'ok' && diagnosis.dof === 0;
  const determined = (id: string) => fixed.has(id) ? true : diagnosis?.status !== 'ok' ? undefined : diagnosis.pointDetermined?.[id]
    ?? (sketch.points.length + sketch.entities.length > DETERMINATION_ENTITY_LIMIT ? undefined : fullyConstrained);
  const selectedConstraintRefs = sketch.constraints.filter(c => selection.includes(c.id) || conflicts.includes(c.id)).flatMap(constraintRefs);
  const highlighted = new Set([...(constraintState?.ids ?? []), ...selection, ...selectedConstraintRefs, ...(showConflict ? conflictConstraints.flatMap(constraintRefs) : [])]);
  const conflictRefs = new Set([...sketch.constraints.filter(c => diagnosis?.constraintStates[c.id] === 'conflicting'), ...conflictConstraints].flatMap(c => constraintRefs(c).flatMap(id => [id, ...entityPoints(sketch.entities.find(e => e.id === id) ?? { kind: 'circle', id: '' })])));
  const color = (ids: string[]) => ids.some(id => conflictRefs.has(id)) ? 'conflicting' : ids.every(id => determined(id) === true) ? 'determined' : ids.some(id => determined(id) === undefined) ? 'not-analysed' : 'under';
  const arcs = sketch.entities.filter(e => e.kind === 'arc');
  const endpoints = new Set(sketch.entities.flatMap(e => e.kind === 'line' ? [e.p1, e.p2] : e.kind === 'arc' ? [e.start, e.end] : []));
  const hiddenCentres = arcs.map(e => e.center).filter(id => !endpoints.has(id) && !selection.includes(id) && hoveredPoint !== id
    && !(tool === 'constraint' && constraintState && acceptsConstraintPick(sketch, constraintState, id))
    && !arcs.some(e => e.center === id && (selection.includes(e.id) || (hoveredArc === e.id || hoverId === e.id))));
  const helperPoints = new Set(sketch.entities.filter(internalHelper).flatMap(entityPoints).filter(id => !sketch.entities.some(e => !internalHelper(e) && entityPoints(e).includes(id))));
  const drawingSnapHidden = [...hiddenCentres.filter(id => !arcs.some(e => e.construction && e.center === id)), ...helperPoints];
  const visiblePoints = sketch.points.filter(p => !fixedPoints.has(p.id) && !hiddenCentres.includes(p.id) && !helperPoints.has(p.id));
  const validStarts = new Set(drawing && tool !== 'line' ? visiblePoints.filter(p => {
    try { tangentIncoming(sketch, p.id); return true; } catch { return false; }
  }).map(p => p.id) : []);
  const convertible = selection.length === 1 ? sketch.entities.find(e => e.id === selection[0] && !e.reference
    && !references.some(r => r.id === e.id) && (e.kind === 'line' || e.kind === 'arc')) : undefined;
  const offeredConstraints = selectionConstraints(sketch, selection, session?.capabilities);
  const selectedDimension = selection.length === 1 ? sketch.constraints.find(c => c.id === selection[0] && 'value' in c) : undefined;
  function reasonText(reason: unknown) {
    return reason instanceof Error ? reason.message : String(reason);
  }
  function error(reason: unknown) {
    if (!alive.current) return;
    if (isConstraintConflict(reason)) {
      setMessage(''); setPickReason(''); setShowConflict(false);
      setRejected({ constraints: reason.constraints, reason: reasonText(reason), sketch });
    } else setMessage(reasonText(reason));
  }
  // Table and canvas commands share the same rejection display.
  useEffect(() => { if (session?.rejection) error(session.rejection); else if (session) setRejected(undefined); }, [session, session?.rejection]);
  function run(action: () => Promise<unknown>) {
    if (!session) return Promise.resolve();
    pendingCount.current++; setPending(true);
    const result = work.current.then(async () => {
      if (!alive.current) return;
      setMessage('');
      try { const result = await action(); if (result !== false && alive.current) { setRejected(undefined); setDismissedDiagnosis(diagnosis); setPickReason(''); setMessage(session.notice ?? ''); } }
      catch (reason) { error(reason); }
      finally { pendingCount.current--; if (alive.current) setPending(pendingCount.current > 0); }
    });
    work.current = result.catch(() => {}); return result;
  }
  const frames = useRef<ReturnType<typeof coalesceTargets<{ id: string; target: XY; start?: number }>> | undefined>(undefined);
  if (!frames.current) frames.current = coalesceTargets(requestAnimationFrame, cancelAnimationFrame, target => {
    const active = drag.current;
    if (!active) return;
    void active.ready.then(() => latest.current.session!.dragTo(target.id, target.target, active.points?.map(p => ({ pointId: p.id, x: p.x + target.target.x - active.origin!.x, y: p.y + target.target.y - active.origin!.y })))).then(result => {
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
      if (width > 64 && height > 64) { const dx = (width - previous.width) / 2, dy = (height - previous.height) / 2; previous = { width, height }; const first = !measured; measured = true; setSize(previous); setView(view => first ? initialView(latest.current.sketch, width, height) : pan(view, dx, dy)); }
    }); observer.observe(svg.current); return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => {
    if (fitRequest !== undefined) { setView(initialView(latest.current.sketch, size.width, size.height)); setEditing(undefined); setDimensionIds([]); setDimensionPrompt(undefined); }
  }, [fitRequest]);
  useLayoutEffect(() => {
    // Opening or changing mode starts from the live value, without overwriting typing.
    if (editing && editingDimension) editDimension(editingDimension, editing.placed);
  }, [editing?.id, editingDimension?.driving]);
  useLayoutEffect(() => {
    // Run after the releasing event and React commit, once capture and SVG focus are done.
    let cancelled = false;
    if (editing && editingDimension?.driving) queueMicrotask(() => {
      if (!cancelled) { dimensionInput.current?.focus({ preventScroll: true }); dimensionInput.current?.select(); }
    });
    return () => { cancelled = true; };
  }, [editing?.id, editingDimension?.driving, refusals]);
  useLayoutEffect(() => {
    if (editing && !editingDimension) setEditing(undefined);
  }, [editing, editingDimension]);
  useLayoutEffect(() => {
    if (!contextPicking.current && contextPickSketch.current !== sketch) { setDimensionIds([]); setDimensionHover(undefined); dimensionPlacement.current = undefined; }
    if (contextPickSketch.current === sketch) contextPickSketch.current = undefined;
  }, [sketch]);
  useEffect(() => {
    // Own Escape before the enclosing modal, even when browser focus is on BODY.
    // Idle Select may close the dialog; each active command consumes one cancellation.
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.target instanceof Element && event.target.closest('[data-dimension-dialog]')) return;
      if (event.key === 'Escape' && (tool !== 'select' || dimensionIds.length || editing || dimensionPrompt || typed || limitEditing !== undefined || selection.length)) keyboard(event);
    };
    document.addEventListener('keydown', escape, true);
    return () => document.removeEventListener('keydown', escape, true);
  });
  useEffect(() => {
    if (!editing) return;
    const dismiss = (event: globalThis.PointerEvent) => {
      if (!(event.target instanceof Element) || !event.target.closest('[data-dimension-dialog]')) setEditing(undefined);
    };
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, [editing?.id]);
  useEffect(() => {
    if (!pickReason) return;
    const timer = setTimeout(() => setPickReason(''), 1500); return () => clearTimeout(timer);
  }, [pickReason]);
  useEffect(() => {
    if (rejected && rejected.sketch !== sketch) { setRejected(undefined); setMessage(''); setPickReason(''); }
  }, [sketch, rejected]);
  function position(event: { clientX: number; clientY: number }) {
    const rect = svg.current!.getBoundingClientRect();
    return { x: (event.clientX - rect.left) * size.width / (rect.width || size.width), y: (event.clientY - rect.top) * size.height / (rect.height || size.height) };
  }
  function choose(next: Tool) {
    if (next === 'tangent' || next === 'arc3') setArcTool(next);
    const picks = next === 'dimension' ? dimensionPicks(sketch, selection, references) : [];
    if (next === 'dimension' && selection.length && (picks.length !== selection.length || picks.length > 2
      || picks.length === 2 && !candidateDimension(picks, cursor))) {
      setMessage('Dimension: select a line, arc, two points, a point and a line, or two lines.'); return;
    }
    dimensionGeneration.current++; dimensionPlacement.current = undefined; toolRef.current = next;
    labelDown.current = undefined; setDimensionIds(next === 'dimension' ? [...selection] : []); setDimensionPrompt(undefined); setDimensionHover(undefined);
    setConstraintState(undefined); setConstraintHover(undefined); setTool(next); setTyped(undefined); setEditing(undefined); setMessage(''); setPickReason(''); setSnap(null);
    const selectedEnd = selection.find(id => sketch.points.some(p => p.id === id) && !fixedPoints.has(id));
    const candidate = chain.from ?? selectedEnd;
    let tangentEnd: string | undefined;
    if (typeof candidate === 'string') { try { tangentIncoming(sketch, candidate); tangentEnd = candidate; } catch { /* Wait for a valid start. */ } }
    setChain({ mode: next === 'arc3' ? 'arc3' : next === 'tangent' ? 'tangent' : 'line', from: next === 'tangent' ? tangentEnd : next === 'arc3' ? candidate : undefined });
    svg.current?.focus({ preventScroll: true });
  }
  const palette = constraintPalette(session?.capabilities).map(entry => ({ ...entry,
    disabled: !session || pending || !selectionCanConstrain(sketch, selection) || selection.length > 0 && !entry.kinds.some(kind => pickPatterns(sketch, selection, kind).length),
    title: `${entry.label}: select ${entry.kinds.flatMap(kind => rules[kind].patterns.map(p => p.join(' and '))).join(' or ')}`,
  }));
  function activateConstraint(entry: ReturnType<typeof constraintPalette>[number] & { disabled: boolean; title?: string }) {
    if (entry.disabled) { setMessage(entry.title ?? entry.label); return; }
    const selected = offeredConstraints.find(c => entry.kinds.includes(c.kind));
    if (selected) { void run(() => session!.constraint(selected.constraint)); return; }
    let state = constraintTool(entry.kinds);
    for (const id of selection) state = pickConstraint(sketch, state, id).state;
    choose('constraint'); setConstraintState(state);
  }
  function constraintPick(before: Sketch, id: string) {
    const state = constraintStateRef.current;
    if (!state) return;
    const result = pickConstraint(before, state, id);
    setConstraintState(result.state); setConstraintHover(undefined);
    return result.constraint;
  }
  function zoom(factor: number) { setView(view => zoomAt(view, { x: size.width / 2, y: size.height / 2 }, factor)); }
  function place(at: XY, snapping: SnapResult | null) {
    const prospective = chainRef.current.mode === 'line' ? prospectiveLine(from(chainRef.current.from), at, snapping, typed) : { endpoint: at, snap: snapping };
    if ('reason' in prospective && prospective.reason) { setMessage(prospective.reason); return Promise.resolve(); }
    return run(async () => {
      const before = placementSketch.current, previous = chainRef.current, placed = placeLine(previous, prospective.endpoint, prospective.snap, before);
      setPickReason(''); rejectionOrigin.current = worldToScreen(view, at);
      if (!placed.command) {
        setChain(placed.state);
        if (placed.state.mode !== previous.mode) { setTool(placed.state.mode); setArcTool(placed.state.mode === 'arc3' ? 'arc3' : 'tangent'); }
        if (placed.reason) { setMessage(placed.reason); if (placed.state === previous) setPickReason(placed.reason); }
        return false;
      }
      let after: Sketch;
      try { after = await session!.command(placed.command, 'dimensions' in prospective ? prospective.dimensions : undefined); }
      catch (reason) { setPickReason(reasonText(reason)); throw reason; }
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
  function editDimension(c: Constraint, placed?: boolean) {
    if ('value' in c) setEditing({ id: c.id, value: String(c.kind === 'angle' ? c.value * 180 / Math.PI : c.value), placed });
  }
  function placeDimension(candidate: CandidateDimension, reference = false) {
    if (pendingCount.current || toolRef.current !== 'dimension') return;
    const generation = dimensionGeneration.current;
    setDimensionIds([]); setSelection([]);
    void run(async () => {
      try {
        if (generation !== dimensionGeneration.current || toolRef.current !== 'dimension') return;
        const before = latest.current.sketch;
        const after = await session!.constraint({ ...candidate, driving: !reference }, false, true);
        if (!alive.current || generation !== dimensionGeneration.current || toolRef.current !== 'dimension') return;
        setDimensionPrompt(undefined);
        const added = after.constraints.find(c => !before.constraints.some(old => old.id === c.id));
        if (added && 'value' in added && !reference) {
          editDimension(added, true);
        }
      } catch (reason) {
        if (!alive.current || generation !== dimensionGeneration.current || toolRef.current !== 'dimension') return;
        if (!reference && isConstraintConflict(reason)) setDimensionPrompt(candidate);
        else throw reason;
      }
    });
  }
  function undo(redo = false) {
    if (constraintStateRef.current) setConstraintState(constraintTool(constraintStateRef.current.kinds));
    dimensionGeneration.current++; dimensionPlacement.current = undefined;
    setDimensionIds([]); setDimensionHover(undefined); setDimensionPrompt(undefined); setEditing(undefined);
    return run(() => session!.undo(redo));
  }
  function keyboard(event: KeyboardEvent | globalThis.KeyboardEvent) {
    const key = event.key.toLowerCase();
    if (key === 'escape') {
      dimensionGeneration.current++; dimensionPlacement.current = undefined;
      event.stopPropagation(); event.preventDefault(); frames.current?.cancel();
      selecting.current = undefined; setMarquee(undefined);
      if (drag.current) { const active = drag.current; drag.current = undefined; void active.ready.then(() => session!.endDrag(true)).catch(error); }
      labelDrag.current = undefined; setDimensionPreview(undefined); labelDown.current = undefined;
      panning.current = undefined; setPanActive(false); gesture.current = undefined;
      if (editing) setEditing(undefined);
      else if (dimensionPrompt) { setDimensionPrompt(undefined); setDimensionIds([]); setSelection([]); }
      else if (limitEditing !== undefined) setLimitEditing(undefined);
      else if (typed) setTyped(undefined);
      else if (tool === 'constraint' && constraintState?.ids.length) { setConstraintState(escapeConstraint(constraintState)); setConstraintHover(undefined); setSelection([]); }
      else if (tool === 'dimension' && dimensionIds.length) { setDimensionIds([]); setDimensionPrompt(undefined); setSelection([]); }
      else { choose('select'); setSelection([]); }
      return;
    }
    if ((event.target as Element).closest('input, select, textarea, button')) return;
    if (drag.current || labelDrag.current?.next) { event.preventDefault(); return; }
    const target = event.target as Element;
    if (svg.current?.contains(target) && (key === '=' || key === '+' || key === '-')) { event.preventDefault(); zoom(key === '-' ? 1 / 1.25 : 1.25); return; }
    if (tool === 'hand' && (key.startsWith('arrow') || key === 'enter' || key === ' ')) { if (key === ' ') { event.preventDefault(); space.current = true; } return; }
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
    if (key === 'enter' && selection.length === 1) {
      const c = sketch.constraints.find(c => c.id === selection[0]);
      if (c && 'value' in c) { event.preventDefault(); editDimension(c); return; }
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
    if ((event.ctrlKey || event.metaKey) && key === 'z') { event.preventDefault(); void undo(event.shiftKey); return; }
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (tool === 'hand' && (key === 'delete' || key === 'backspace' || key === 'x')) { event.preventDefault(); return; }
    if (key === 'l') choose('line');
    else if (key === 'a') choose(tool === 'tangent' ? 'arc3' : tool === 'arc3' ? 'tangent' : arcTool);
    else if (key === 'd') choose('dimension');
    else if (key === 's') choose('split');
    else if (key === 'f') setView(initialView(sketch, size.width, size.height, context, event.shiftKey));
    else if (key === 'delete' || key === 'backspace') { event.preventDefault(); removeSelection(); }
    else if (key === 'x') {
      const commands = sketch.entities.filter(e => selection.includes(e.id) && !e.reference).map(e => ({ kind: 'setConstruction' as const, id: e.id, construction: !e.construction }));
      if (commands.length) void run(() => session!.command(commands));
    } else if (palette.some(c => c.key === key)) {
      event.preventDefault(); activateConstraint(palette.find(c => c.key === key)!);
    } else if (/^[0-9.]$/.test(key) && tool === 'line' && chain.from) { event.preventDefault(); setTyped({ length: key, angle: '' }); }
  }
  function pointerDown(event: PointerEvent<SVGSVGElement>) {
    event.preventDefault();
    if (event.button === 1 || event.button === 0 && (space.current || tool === 'hand')) {
      panning.current = position(event); setPanActive(true); event.currentTarget.setPointerCapture?.(event.pointerId); svg.current?.focus({ preventScroll: true }); return;
    }
    if (tool === 'hand') return;
    if (editing) setEditing(undefined);
    if (dimensionPrompt) return;
    const label = (event.target as Element).closest('[data-constraint-id]');
    let constraint = sketch.constraints.find(c => c.id === label?.getAttribute('data-constraint-id'));
    const screen = position(event), at = screenToWorld(view, screen);
    let selectedHit: string | undefined;
    if (tool === 'select' && event.button === 0) {
      const dimensions = [...layouts].filter(([id, layout]) => {
        const c = sketch.constraints.find(c => c.id === id);
        if (!c || !('value' in c) || !constraintVisibility(c).visible) return false;
        const box = dimensionLabelBox(layout, renderedDimensionLabel(c));
        return screen.x >= box.x && screen.x <= box.x + box.width && screen.y >= box.y && screen.y <= box.y + box.height;
      }).map(([id]) => id);
      const targetId = (event.target as Element).closest('[data-id]')?.getAttribute('data-id');
      const ids = selectHits(sketch, at, 8 / view.scale, [...hiddenCentres, ...helperPoints], dimensions);
      // Explicit SVG targets also support keyboard/test-dispatched pointer events.
      if (targetId && !ids.includes(targetId) && !fixedPoints.has(targetId)) ids.push(targetId);
      if (constraint && !ids.includes(constraint.id)) ids.push(constraint.id);
      cycle.current = cycleHit(cycle.current, { x: event.clientX, y: event.clientY }, performance.now(), ids);
      selectedHit = ids[cycle.current.index];
      setMessage(ids.length > 1 ? `${cycle.current.index + 1} of ${ids.length}` : '');
      constraint = sketch.constraints.find(c => c.id === selectedHit);
    }
    if (event.button === 0 && constraint && tool !== 'dimension' && tool !== 'constraint') {
      const now = performance.now(), previous = labelDown.current; labelDrag.current = undefined;
      setSelection(combineSelection(selection, [constraint.id], { shift: event.shiftKey, toggle: event.ctrlKey || event.metaKey }));
      if (previous?.id === constraint.id && now >= previous.time && now - previous.time <= 400) { editDimension(constraint); labelDown.current = undefined; }
      else {
        labelDown.current = { id: constraint.id, time: now }; svg.current?.focus({ preventScroll: true });
        const layout = layouts.get(constraint.id);
        if (session && viewOptions.dimensions && layout) labelDrag.current = { id: constraint.id, screen: position(event), client: { x: event.clientX, y: event.clientY }, placement: layout.placement };
      }
      return;
    }
    labelDown.current = undefined; labelDrag.current = undefined; setDimensionPreview(undefined);
    if (tool !== 'dimension') event.currentTarget.setPointerCapture?.(event.pointerId);
    svg.current?.focus({ preventScroll: true });
    setCursor(at); cursorRef.current.at = at;
    if (event.button !== 0 || !session || drag.current) return;
    const target = (event.target as Element).closest('[data-id]'); const id = tool === 'select' ? selectedHit : target?.getAttribute('data-id');
    if (tool === 'constraint' && constraintStateRef.current) {
      if (pendingCount.current) return;
      const hit = hitConstraint(sketch, references, constraintStateRef.current, at, 12 / view.scale, [...hiddenCentres, ...helperPoints]);
      if (hit) { const c = constraintPick(sketch, hit); if (c) void run(() => session.constraint(c)); }
      return;
    }
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
      const snapping = findSnap(sketch, references, at, 1 / view.scale, snap, { suppress: event.ctrlKey || event.metaKey, exclude: drawingSnapHidden, lastPoint: typeof chain.from === 'string' ? chain.from : undefined });
      void place(at, snapping); return;
    }
    if (tool === 'dimension') {
      if (pendingCount.current) return;
      const hit = hitDimension(sketch, references, at, 12 / view.scale, hiddenCentres) ?? (id && dimensionPicks(sketch, [id], references).length ? id : undefined);
      if (!dimensionIds.length) {
        if (hit) { setDimensionIds([hit]); setSelection([hit]); }
        return;
      }
      const second = dimensionSecondPick(hit, at);
      if (second) { setDimensionIds(second); setSelection(second); return; }
      dimensionPlacement.current = candidateDimension(dimensionPicks(sketch, dimensionIds, references), at);
      if (dimensionPlacement.current) event.currentTarget.setPointerCapture?.(event.pointerId);
      return;
    }
    if (id && fixedPoints.has(id)) return;
    if (!id) {
      if (tool === 'select') selecting.current = startSelect(screen, undefined, selection, { shift: event.shiftKey, toggle: event.ctrlKey || event.metaKey });
      if (!event.shiftKey && !event.ctrlKey && !event.metaKey) setSelection([]); return;
    }
    if (tool === 'split' && sketch.entities.some(e => e.id === id && !e.reference)) {
      const p = projectToEntity(sketch, id, at.x, at.y); void run(() => session.command({ kind: 'splitAt', entity: id, ...p })); return;
    }
    const modifiers = { shift: event.shiftKey, toggle: event.ctrlKey || event.metaKey };
    const next = tool === 'select' && selection.includes(id) && !modifiers.shift && !modifiers.toggle ? selection : combineSelection(selection, [id], modifiers);
    setSelection(next);
    if (tool === 'select' && next.includes(id)) selecting.current = startSelect(screen, id, next, modifiers);

  }
  function dimensionSecondPick(hit: string | undefined, at: XY): string[] | undefined {
    if (dimensionIds.length !== 1 || !hit || hit === dimensionIds[0]) return;
    const pair = [dimensionIds[0], hit];
    if (candidateDimension(dimensionPicks(sketch, pair, references), at)) return pair;
  }
  function pointerMove(event: PointerEvent<SVGSVGElement>) {
    const start = perfEnabled && (drag.current || selecting.current) ? performance.now() : undefined;
    const screen = position(event), at = screenToWorld(view, screen); setCursor(at); cursorRef.current.at = at; setContextPointer(screen);
    if (panning.current) { const dx = screen.x - panning.current.x, dy = screen.y - panning.current.y; setView(view => pan(view, dx, dy)); panning.current = screen; return; }
    if (tool === 'hand') return;
    if (tool === 'constraint' && constraintState) {
      const hit = hitConstraint(sketch, references, constraintState, at, 12 / view.scale, [...hiddenCentres, ...helperPoints]);
      setConstraintHover(hit); onHoverChange?.(hit); return;
    }
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
    if (tool === 'dimension' && !editing && !dimensionPrompt) {
      const hit = hitDimension(sketch, references, at, 12 / view.scale, hiddenCentres);
      const pick = !dimensionIds.length || dimensionSecondPick(hit, at) ? hit : undefined;
      setDimensionHover(pick); onHoverChange?.(pick);
      return;
    }
    if (selecting.current && !event.buttons) { pointerUp(); return; }
    if (selecting.current) {
      const next = moveSelect(selecting.current, screen); selecting.current = next;
      if (next.phase !== 'pending') cycle.current = undefined;
      if (next.phase === 'marquee') { setMarquee(next); return; }
      if (next.phase === 'drag' && !drag.current && session) {
        const ids = dragPointIds(sketch, next.selection);
        if (ids.length) {
          const singlePoint = next.selection.length === 1 && sketch.points.some(p => p.id === next.hit);
          const ready = session.beginDrag(singlePoint ? ids[0] : ids);
          drag.current = { id: ids[0], ready, ...(!singlePoint ? { origin: screenToWorld(view, next.start), points: ids.map(point).map(p => ({ ...p })) } : {}) };
          void ready.catch(error);
        }
      }
    }
    const targetId = (event.target as Element).closest('[data-id]')?.getAttribute('data-id');
    setHoveredConstraint(sketch.constraints.some(c => c.id === targetId) ? targetId! : undefined);
    setHoveredArc(arcs.some(e => e.id === targetId) ? targetId! : undefined);
    setHoveredPoint(visiblePoints.some(p => p.id === targetId) ? targetId! : undefined);
    onHoverChange?.(targetId && (sketch.entities.some(e => e.id === targetId && !e.reference) || visiblePoints.some(p => p.id === targetId)) ? targetId : undefined);
    if (rejectionOrigin.current && Math.hypot(screen.x - rejectionOrigin.current.x, screen.y - rejectionOrigin.current.y) > 8) setPickReason('');
    if (drag.current) {
      frames.current!.push({ id: drag.current.id, target: drag.current.points || event.ctrlKey || event.metaKey ? at : snapLimitTarget(references, drag.current.id, at, 8 / view.scale), ...(perfEnabled ? { start } : {}) }); return;
    }
    if (gesture.current && Math.hypot(screen.x - gesture.current.screen.x, screen.y - gesture.current.screen.y) > 6) {
      setChain({ from: gesture.current.id, mode: 'tangent' }); setTool('tangent');
    }
    if (drawing) { const next = findSnap(sketch, references, at, 1 / view.scale, snap, { suppress: event.ctrlKey || event.metaKey, exclude: drawingSnapHidden, lastPoint: typeof chain.from === 'string' ? chain.from : undefined }); setSnap(next); cursorRef.current.snap = next; }
  }
  function pointerUp(cancel = false, event?: PointerEvent<SVGSVGElement>) {
    const selectionGesture = selecting.current; selecting.current = undefined; setMarquee(undefined);
    if (!cancel && selectionGesture) setSelection(finishSelect(selectionGesture, sketch, screenToWorld(view, selectionGesture.start), screenToWorld(view, selectionGesture.current), [...hiddenCentres, ...helperPoints]));
    const candidate = dimensionPlacement.current; dimensionPlacement.current = undefined;
    if (candidate && event && event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (candidate && !cancel) placeDimension(candidate);
    const label = labelDrag.current; labelDrag.current = undefined;
    if (label?.next && !cancel && viewOptions.dimensions) {
      void run(() => session!.command({ kind: 'setDimensionPlacement', id: label.id, placement: label.next })).finally(() => setDimensionPreview(undefined));
    } else if (label) setDimensionPreview(undefined);
    panning.current = undefined; setPanActive(false);
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
  const constraintVisibility = (c: Constraint) => {
    const related = new Set([c.id, ...constraintRefs(c).flatMap(id => [id, ...entityPoints(sketch.entities.find(e => e.id === id) ?? { kind: 'circle', id: '' })])]);
    const problem = diagnosis?.constraintStates[c.id] === 'conflicting' || conflicts.includes(c.id) || problems.some(p => p.ids.some(id => related.has(id)));
    const active = selection.includes(c.id) || hoveredConstraint === c.id || hoverId === c.id;
    return { problem, active, visible: ('value' in c ? viewOptions.dimensions : viewOptions.constraints) || problem || active };
  };
  const renderedDimensionLabel = (c: Constraint & { value: number; driving: boolean }) => dimensionLabel(c, viewOptions.names || constraintVisibility(c).active ? 'name' : 'value');
  // Reserve full names so hovering/selecting a label never changes its automatic lane.
  const layouts = dimensionLayouts(dimensionSketch, xy, dimensionBounds, view.scale, c => dimensionLabel(c, 'name'));
  const draft = dimensionPrompt ?? (tool === 'dimension' && !editing && !dimensionSecondPick(dimensionHover, cursor) ? candidateDimension(dimensionPicks(sketch, dimensionIds, references), cursor) : undefined);
  const draftConstraint = draft && { ...draft, id: '__dimension_preview' };
  const draftLayout = draftConstraint && dimensionLayout(sketch, draftConstraint, xy, dimensionBounds, 0, view.scale);
  const dimensionBoxes = [...layouts].flatMap(([id, layout]) => {
    const c = dimensionSketch.constraints.find(c => c.id === id)!;
    return 'value' in c && constraintVisibility(c).visible ? [dimensionLabelBox(layout, renderedDimensionLabel(c))] : [];
  });
  if (draftConstraint && draftLayout) dimensionBoxes.push(dimensionLabelBox(draftLayout, dimensionLabel(draftConstraint, 'name')));
  const editingAt = editing && layouts.get(editing.id)?.at;
  const boxAt = (at?: XY, width = 180, height = 100) => ({ left: Math.max(4, Math.min(at?.x ?? 12, size.width - width)), top: Math.max(4, Math.min(at?.y ?? 12, size.height - height)) });
  const start = from(chain.from);
  const prospective = prospectiveLine(start, cursor, snap, tool === 'line' ? typed : undefined);
  const preview = worldToScreen(view, prospective.endpoint);
  const previewSnap = tool === 'line' ? prospective.snap : snap;
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
  const low = screenToWorld(view, { x: 0, y: size.height }), high = screenToWorld(view, { x: size.width, y: 0 });
  const contextItems = referenceContext(references, context, { low: low.y, high: high.y, left: low.x, right: high.x });
  const constraintContext = constraintState && tool === 'constraint' ? new Set(contextItems.filter(item => {
    if (!item.pickable) return false;
    try {
      const preview = item.referenceId && (item.role === 'axis' || item.role === 'rim-plane')
        ? { sketch, id: item.referenceId } : materialiseContext(sketch, { items: contextItems, rimPlaneLabel: '', fingerprint: context?.fingerprint ?? '' }, item.id);
      return acceptsConstraintPick(preview.sketch, constraintState, preview.id);
    } catch { return false; }
  }).map(item => item.id)) : undefined;
  const canPickContext = !!session && (tool === 'select' || tool === 'dimension' || tool === 'constraint') && !chain.from && !contextStale && !pending;
  function pickContext(item: ContextDrawingItem, additive: boolean) {
    if (!canPickContext || pendingCount.current) return;
    const datum = item.role === 'axis' || item.role === 'rim-plane' ? item.referenceId : undefined;
    const previous = [...dimensionIds], generation = dimensionGeneration.current;
    contextPicking.current = true;
    void run(async () => {
      try {
        const result = datum ? { id: datum, sketch: latest.current.sketch }
          : await session.materialiseContext?.({ items: contextItems, rimPlaneLabel: contextItems.find(i => i.role === 'rim-plane')?.label ?? '', fingerprint: context?.fingerprint ?? '' }, item.id);
        if (!result || generation !== dimensionGeneration.current) return;
        contextPickSketch.current = result.sketch;
        if (toolRef.current === 'constraint') {
          const c = constraintPick(result.sketch, result.id);
          if (c) await session.constraint(c);
        } else if (toolRef.current === 'dimension') {
          const ids = previous.includes(result.id) ? previous : [...previous, result.id].slice(-2);
          setDimensionIds(ids); setSelection(ids);
        } else setSelection(select(selection, result.id, additive));
      } finally { queueMicrotask(() => { contextPicking.current = false; }); }
    });
  }
  const arcHint = tool === 'constraint' && constraintState ? constraintHint(sketch, constraintState) : tool === 'arc3' ? `3-point arc: ${!chain.from ? 'start 1' : !chain.end ? 'end 2' : 'point on arc 3'} of 3 · A: tangent arc` : hints[tool];
  const slot = (node: React.ReactNode, host?: HTMLElement | null) => host ? createPortal(node, host) : node;
  return <div className="sketch-canvas" aria-busy={pending} onKeyDown={keyboard} onKeyUp={e => { if (e.key === ' ') space.current = false; }} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) space.current = false; }}>
    {slot(<SketchToolbar tool={tool} arcTool={arcTool} enabled={!!session} palette={palette} activeConstraint={constraintState?.kinds[0]}
      viewOptions={viewOptions} choose={choose} constrain={activateConstraint} undo={redo => void undo(redo)} zoom={zoom}
      fit={all => setView(initialView(sketch, size.width, size.height, context, all))}
      toggle={key => setViewOptions(previous => { const next = { ...previous, [key]: !previous[key] }; saveSketchView(next); return next; })}/>, slots?.toolbar)}
    {contextStale && <p role="status" className="sketch-context-stale">Context out of date</p>}
    {(rejected || conflictConstraints.length > 0) && <div role="alert" className="sketch-conflict-banner">
      <span>{rejected?.reason || `Conflicting constraints: ${conflictNames}${diagnosis?.reason ? `. ${diagnosis.reason}` : ''}`}</span>
      {scaleToFit && rejected?.reason.startsWith('The new throat conflicts with:') && <button type="button" onClick={scaleToFit}>Scale to fit throat</button>}
      <button type="button" onClick={() => { setShowConflict(true); setSelection(conflicts.filter(id => sketch.constraints.some(c => c.id === id))); svg.current?.focus({ preventScroll: true }); }}>Show</button>
      <button type="button" onClick={() => { session?.dismissRejection?.(); setRejected(undefined); setDismissedDiagnosis(diagnosis); setMessage(''); setPickReason(''); }}>Dismiss</button>
    </div>}
    <div className="sketch-surface">
    {slot(<>{tool !== 'select' && <p className="sketch-command-step" role="status">{arcHint}</p>}{selection.length > 0 && tool !== 'dimension' && tool !== 'constraint' && tool !== 'hand' && <div role="toolbar" aria-label="Selection actions" className="sketch-context sketch-selection-context">
      {!slots?.properties && selectedDimension && 'driving' in selectedDimension && <button type="button" disabled={!session || pending} onClick={() => void run(() => session!.command({ kind: 'setDriving', id: selectedDimension.id, driving: !selectedDimension.driving }))}>{selectedDimension.driving ? 'Make reference' : 'Make driving'}</button>}
      {sketch.entities.some(e => selection.includes(e.id) && !e.reference && !references.some(r => r.id === e.id)) && <button type="button" disabled={!session} onClick={() => void run(() => session!.command(sketch.entities.filter(e => selection.includes(e.id) && !e.reference).map(e => ({ kind: 'setConstruction', id: e.id, construction: !e.construction }))))}>Construction (X)</button>}
      {convertible && <button type="button" disabled={!session} onClick={() => void run(() => session!.command({ kind: 'convertEntity', id: convertible.id, to: convertible.kind === 'line' ? 'arc' : 'line' }))}>{convertible.kind === 'line' ? 'Make arc' : 'Make line'}</button>}
      {sketch.constraints.some(c => selection.includes(c.id) && 'value' in c) && <button type="button" disabled={!session} onClick={() => void run(() => session!.command(sketch.constraints.filter(c => selection.includes(c.id) && 'value' in c).map(c => ({ kind: 'setDimensionPlacement', id: c.id }))))}>Reset position</button>}
      <button type="button" disabled={!session} onClick={removeSelection}>Delete selection</button>
    </div>}</>, slots?.properties)}
      <svg ref={svg} viewBox={`0 0 ${size.width} ${size.height}`} preserveAspectRatio="xMidYMid meet" className={tool === 'hand' || panActive ? `sketch-pan${panActive ? ' grabbing' : ''}` : tool === 'constraint' ? 'sketch-constraint-tool' : undefined} tabIndex={0} role="application" aria-label="Sketch drawing" onPointerEnter={() => { hovered.current = true; }} onPointerLeave={() => { hovered.current = false; setContextPointer(undefined); setHoveredConstraint(undefined); setHoveredArc(undefined); setHoveredPoint(undefined); setDimensionHover(undefined); setConstraintHover(undefined); onHoverChange?.(undefined); setPickReason(''); }} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={event => pointerUp(false, event)} onPointerCancel={() => pointerUp(true)} onLostPointerCapture={() => pointerUp(true)} onDoubleClick={() => { if (drawing) choose('select'); }}>
        <g className="sketch-grid" aria-hidden="true">{gridX.map(x => <line key={`x${x}`} x1={worldToScreen(view, { x, y: 0 }).x} x2={worldToScreen(view, { x, y: 0 }).x} y1={0} y2={size.height}/>)}{gridY.map(y => <line key={`y${y}`} x1={0} x2={size.width} y1={worldToScreen(view, { x: 0, y }).y} y2={worldToScreen(view, { x: 0, y }).y}/>)}</g>
        <ContextLayer names={viewOptions.names} cursor={contextPointer} contour={contourPoints} viewport={size} canPick={canPickContext} validTargets={constraintContext} isPanning={() => space.current || tool === 'hand'} sketch={sketch} reserved={dimensionBoxes} items={contextItems} hoveredId={tool === 'constraint' ? constraintHover : tool === 'dimension' ? dimensionHover : hoverId} highlighted={highlighted} stale={contextStale} view={view} onPick={pickContext}
          onEditLimit={radius => setLimitEditing(String(radius))} canEditLimit={!!onLimitChange}/>
        {sketch.entities.filter(e => !e.reference && !internalHelper(e) && !references.some(r => r.id === e.id)).map(e => {
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
          return <g key={e.id} className="sketch-curve"><path data-id={e.id} d={d} className="sketch-curve-hit"/>{issues.length > 0 && <path d={d} className={`sketch-problem ${workflow?.state === 'unfinished' ? 'unfinished' : ''}`} />}<path data-id={e.id} d={d} tabIndex={-1} role="button" aria-label={`Select ${e.kind} ${nameFor?.(e.id) ?? e.id}`} style={entityStyle?.(e)} className={`sketch-entity ${entityClassName?.(e) ?? ''} ${status} ${(tool === 'constraint' ? constraintHover : tool === 'dimension' ? dimensionHover : hoverId) === e.id ? 'hovered' : ''} ${e.construction ? 'construction' : ''} ${highlighted.has(e.id) || ids.some(id => highlighted.has(id)) ? 'selected' : ''}`}><title>{entityName} ({e.id}){issues.length > 0 ? ` · ${issues.map(p => p.message).join('\n')}` : ''}</title></path>{labelAt && <text data-id={e.id} x={labelAt.x} y={labelAt.y - 10} textAnchor="middle" className="sketch-curve-name sketch-glyph" aria-hidden="true">{entityName}</text>}</g>;
        })}
        {visiblePoints.map(p => { const at = xy(p.id), centre = !endpoints.has(p.id) && arcs.some(e => e.center === p.id);
          const className = `sketch-point ${color([p.id])} ${(tool === 'constraint' ? constraintHover : tool === 'dimension' ? dimensionHover : hoverId) === p.id ? 'hovered' : ''} ${highlighted.has(p.id) ? 'selected' : ''} ${validStarts.has(p.id) ? 'valid-start' : ''}`;
          return <g key={p.id} className="sketch-handle"><circle data-id={p.id} cx={at.x} cy={at.y} r={10} className="sketch-point-hit"/>
            {validStarts.has(p.id) && <circle cx={at.x} cy={at.y} r={8} className="sketch-arc-start"/>}
            {centre ? <path data-id={p.id} d={`M${at.x - 5},${at.y} h10 M${at.x},${at.y - 5} v10`} tabIndex={-1} role="button" aria-label={`Select point ${nameFor?.(p.id) ?? p.id}`} className={`${className} sketch-centre`}><title>{p.id}: {p.x}, {p.y} mm</title></path>
              : <circle data-id={p.id} cx={at.x} cy={at.y} r={4} tabIndex={-1} role="button" aria-label={`Select point ${nameFor?.(p.id) ?? p.id}`} className={className}><title>{p.id}: {p.x}, {p.y} mm{problems.filter(v => v.ids.includes(p.id)).map(v => ` · ${v.message}`)}</title></circle>}
          </g>; })}
        {sketch.constraints.map((c, i) => { const dimension = 'value' in c;
          const { problem, active, visible } = constraintVisibility(c);
          if (!visible) return null;
          const layout = layouts.get(c.id);
          const at = layout?.at ?? constraintPosition(c, i);
          const label = dimension ? renderedDimensionLabel(c) : (c.kind in rules ? rules[c.kind as keyof typeof rules].symbol : undefined) ?? c.kind;
          const labelBox = layout ? dimensionLabelBox({ ...layout, rotation: 0 }, label) : { x: at.x - 6, y: at.y - 17, width: 24, height: 28 };
          return <g key={c.id} onPointerEnter={() => setHoveredConstraint(c.id)} onPointerLeave={() => setHoveredConstraint(undefined)} data-constraint-id={c.id} className={dimension ? 'sketch-dimension' : undefined}>
            {layout && <g className="sketch-dimension-lines" aria-hidden="true"><path className="sketch-extension-lines" d={layout.path}/><path className="sketch-dimension-arrows" d={layout.arrows}/></g>}
            <g transform={layout?.rotation ? `rotate(${layout.rotation} ${at.x} ${at.y})` : undefined}><rect data-id={c.id} {...labelBox} className="sketch-label-hit"/><text textAnchor={layout ? 'middle' : undefined} data-id={c.id} x={at.x} y={at.y} tabIndex={-1} role="button" aria-label={`${dimension ? 'Dimension' : 'Constraint'} ${c.kind} ${c.id}${dimension ? ` ${dimensionLabel(c, 'name')}` : c.label ? ` ${c.label}` : ''}${diagnosis?.constraintStates[c.id] === 'redundant' ? ' implied' : ''}`} className={`sketch-glyph ${dimension && !c.driving ? 'reference' : ''} ${diagnosis?.constraintStates[c.id] ?? ''} ${conflicts.includes(c.id) ? 'conflicting' : ''} ${problem ? 'problem' : ''} ${active ? 'selected' : ''}`} onDoubleClick={event => { event.stopPropagation(); if (dimension && tool !== 'constraint' && tool !== 'hand') editDimension(c); }}><title>{dimension ? dimensionLabel(c, 'name') : c.kind} {c.id}{diagnosis?.constraintStates[c.id] === 'redundant' ? ' (implied)' : ''}</title>{label}</text></g></g>;
        })}
        {draftConstraint && draftLayout && <g className="sketch-dimension-preview" aria-label={`Preview ${draftConstraint.kind}`}>
          <path d={draftLayout.path}/><path d={draftLayout.arrows}/>
          <text x={draftLayout.at.x} y={draftLayout.at.y} textAnchor="middle" transform={draftLayout.rotation ? `rotate(${draftLayout.rotation} ${draftLayout.at.x} ${draftLayout.at.y})` : undefined}>{dimensionLabel(draftConstraint)}</text>
        </g>}
        {previewPath && <path className="sketch-preview" d={previewPath}/>}
        {drawing && start && !previewPath && <line className="sketch-preview" x1={worldToScreen(view, start).x} y1={worldToScreen(view, start).y} x2={preview.x} y2={preview.y}/>}
        {drawing && previewSnap && <g className="sketch-pending" aria-label={`Pending ${previewSnap.glyph} snap`}><circle cx={preview.x} cy={preview.y} r={7}/><text x={preview.x + 10} y={preview.y - 8}>{previewSnap.glyph}</text></g>}
        {marquee && <rect className={`sketch-marquee ${marquee.current.x < marquee.start.x ? 'crossing' : 'window'}`} x={Math.min(marquee.start.x,marquee.current.x)} y={Math.min(marquee.start.y,marquee.current.y)} width={Math.abs(marquee.current.x-marquee.start.x)} height={Math.abs(marquee.current.y-marquee.start.y)}/>}
      </svg>
      {pickReason && <div role="tooltip" className="sketch-pick-tooltip" style={{ left: Math.max(4, Math.min(preview.x + 12, size.width - 252)), top: Math.max(4, Math.min(preview.y + 12, size.height - 60)) }}>{pickReason}</div>}
      {limitEditing !== undefined && <form className="sketch-inline" aria-label="Edit limit" onSubmit={event => {
        event.preventDefault(); const value = Number(limitEditing);
        if (Number.isFinite(value) && value > 0) { onLimitChange?.(value); setLimitEditing(undefined); }
      }}><label>Limit radius<input aria-label="Limit radius" autoFocus type="number" min="0.001" step="any" value={limitEditing} onChange={e => setLimitEditing(e.target.value)} onKeyDown={e => { e.stopPropagation(); if (e.key === 'Escape') setLimitEditing(undefined); }}/></label><button type="submit">Apply limit</button></form>}
      {typed && <div className="sketch-inline" style={{ left: Math.min(preview.x + 12, size.width - 170), top: Math.min(preview.y + 12, size.height - 80) }}>
        {(['length', 'angle'] as const).map(field => <label key={field} className={typed[field].trim() ? 'locked' : undefined}>{field === 'length' ? 'Length (mm)' : 'Angle (°)'}<input aria-label={`Line ${field}`} autoFocus={field === 'length'} data-locked={!!typed[field].trim()} title={typed[field].trim() ? 'Locked; clear to unlock' : 'Unlocked'} value={typed[field]} onChange={event => setTyped({ ...typed, [field]: event.target.value })} onKeyDown={event => {
          event.stopPropagation();
          if (event.key === 'Escape') { event.preventDefault(); setTyped(undefined); svg.current?.focus({ preventScroll: true }); }
          if (event.key === 'Enter' && start) {
            event.preventDefault(); void place(cursor, snap);
          }
        }}/></label>)}
      </div>}
      {dimensionPrompt && <div role="dialog" aria-label="Over-constrained dimension" className="sketch-inline sketch-dimension-prompt" style={boxAt(draftLayout?.at, 260, 150)}>
        <p>This dimension would over-constrain the sketch.</p>
        <button type="button" disabled={pending} onClick={() => placeDimension(dimensionPrompt, true)}>Make it a reference (driven) dimension</button>
        <button type="button" disabled={pending} onClick={() => { setDimensionPrompt(undefined); setSelection([]); svg.current?.focus({ preventScroll: true }); }}>Cancel</button>
      </div>}
      {editing && editingDimension && !editingDimension.driving && <div role="dialog" aria-label="Reference dimension readout" data-dimension-dialog className="sketch-inline" style={boxAt(editingAt)} onKeyDown={event => {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setEditing(undefined); svg.current?.focus({ preventScroll: true }); }
      }}>
        <p>Reference dimension: {dimensionLabel(editingDimension)}</p>
        <button type="button" disabled={!session || pending} onClick={() => void run(async () => {
          const after = await session!.command({ kind: 'setDriving', id: editing.id, driving: true });
          const c = after.constraints.find(c => c.id === editing.id);
          if (c && 'value' in c && c.driving) editDimension(c);
        })}>Make driving</button>
      </div>}
      {editing && editingDimension?.driving && <form className="sketch-inline" style={boxAt(editingAt)} aria-label="Edit dimension" data-dimension-dialog onKeyDown={event => {
        event.stopPropagation();
        if (event.key === 'Tab') {
          event.preventDefault();
          const controls = [...event.currentTarget.querySelectorAll<HTMLElement>('input, button:not(:disabled)')];
          const index = controls.indexOf(document.activeElement as HTMLElement);
          controls[(index + (event.shiftKey ? controls.length - 1 : 1)) % controls.length]?.focus({ preventScroll: true });
        }
        if (event.key === 'Escape') { event.preventDefault(); setEditing(undefined); svg.current?.focus({ preventScroll: true }); }
      }} onSubmit={event => {
        event.preventDefault(); if (dimensionApplying.current) return;
        const value = Number(editing.value) * (editingDimension.kind === 'angle' ? Math.PI / 180 : 1);
        if (!session || !editing.value.trim() || !Number.isFinite(value)) { setEditing({ ...editing, reason: 'Enter a finite number.' }); setRefusals(v => v + 1); return; }
        dimensionApplying.current = true;
        void run(async () => {
          try {
            await session.dimension(editing.id, value, editing.placed);
            if (alive.current) { setEditing(undefined); svg.current?.focus({ preventScroll: true }); }
          } catch (reason) {
            if (alive.current) { setEditing({ ...editing, reason: reason instanceof Error ? reason.message : String(reason) }); setRefusals(v => v + 1); }
          } finally { dimensionApplying.current = false; }
        });
      }}><label>Dimension value<input ref={dimensionInput} aria-label="Dimension value" value={editing.value} onChange={event => setEditing({ ...editing, value: event.target.value })} onKeyDown={event => { if (event.key !== 'Tab') event.stopPropagation(); if (event.key === 'Enter') { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setEditing(undefined); svg.current?.focus({ preventScroll: true }); } }}/></label><button type="submit" disabled={pending}>Apply dimension value</button><button type="button" onClick={() => { setEditing(undefined); svg.current?.focus({ preventScroll: true }); }}>Cancel</button>{editing.reason && <span role="alert">{editing.reason}</span>}</form>}
    </div>
    {slot(<div role="status" className="sketch-status"><span className={`sketch-dof ${fullyConstrained ? 'complete' : ''}`}>{fullyConstrained ? 'Fully constrained' : `${diagnosis?.dof ?? '…'} DOF`}</span>{` · Solve: ${diagnosis?.status ?? 'pending'}`}{workflow && ` · ${workflow.state}`} {problems.length > 0 && workflow?.state !== 'unfinished' && ` · ${problems.length} problem${problems.length === 1 ? '' : 's'}`}  · x {cursor.x.toFixed(2)}, y {cursor.y.toFixed(2)} mm · Snap: {previewSnap?.glyph ?? 'none'} · {message || (tool === 'select' ? workflow?.hint : undefined) || arcHint}<span> · Constraint state shown for sketches up to {DETERMINATION_ENTITY_LIMIT} entities</span></div>, slots?.status)}
    {slot(<>{diagnosis && sketch.constraints.some(c => diagnosis.constraintStates[c.id] === 'redundant') && <p>Implied: {sketch.constraints.filter(c => diagnosis.constraintStates[c.id] === 'redundant').map(c => c.id).join(', ')}</p>}
    {problems.map((problem, i) => { const action = problemAction?.(problem); return <div className={`sketch-domain-problem ${workflow?.state === 'unfinished' ? 'unfinished' : problem.severity === 'warning' ? 'warning' : 'invalid'}`} key={`${problem.code}${i}`}>{problem.message}{problem.ids.filter(id => { const data = sketch.entities.find(e => e.id === id)?.data; return data && typeof data === 'object' && !Array.isArray(data) && typeof data.name === 'string' && data.name.trim(); }).map(id => ` · ${nameFor?.(id) ?? displayName(sketch, id)}`)}{action && <button type="button" disabled={!session} onClick={() => void run(() => session!.constraint(action.constraint))}>{action.label}</button>}</div>; })}</>, slots?.properties)}
  </div>;
}
