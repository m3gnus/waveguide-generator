import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type PointerEvent } from 'react';
import { createPortal } from 'react-dom';
import { sourceEditorApi, type SourceAttachment, type SourceDocument, type SourcePatch, type SourcePoint, type SourcePreset, type SourceValidation } from '../api/sourceEditor';
import { durableSettings, namespaceStorage } from '../stores/durableSettings';
import { useModalDialogFocus } from '../shell/dialogFocus';
import { NumberField } from './NumberField';
import { SketchNameCell } from './SketchNameCell';
import { dimensionName } from '../sketch/dimensionGraphics';
import { drawnSource, editPatch, editPoint, identityProblem, INITIAL_SOURCE, removePoint } from './sourceContourEditing';
import { ExperimentalBadge, EXPERIMENTAL_NOTE } from './ExperimentalBadge';
import { SketchCanvas } from '../sketch/SketchCanvas';
import { isConstraintConflict, SketchController, type Authoring } from '../sketch/controller';
import { applySourceCommand, contourPointOrder, lower, movingWeights, rules, seed, withSourceLimit, withSourceContext, sourceLimitTarget, type SourceLimit, type SourceMeta } from '../sketch/adapters/sourceContour';
import { presetDefaults as defaults, presetLabels, seedPreset, fitPresetDimensions } from '../sketch/adapters/sourcePreset';
import { presetDimensionActive, presetDimensionProblem, presetTopologyMessage, reapplyPresetMessage } from '../sketch/adapters/sourcePresetMapping';
import { refreshContext, type SketchContext } from '../sketch/context';
import { contextClearances, standaloneContext } from '../sketch/adapters/sourceContext';
import { displayName } from '../sketch/displayName';
import { uniqueProblems } from '../sketch/adapter';
import { newerEnvelope, parseSourceAuthoring, serializeSourceAuthoring, sameSourceDocument } from '../sketch/adapters/sourceAuthoring';
import type { Command } from '../sketch/commands';
import type { Diagnosis } from '../sketch/session';
import { arcGeometry } from '../sketch/geometry';
import { installSketchTest, registerSketchLoader, sketchPerfEnabled } from './sketchTestHarness';
import './sourceContourEditor.css';

const storage = namespaceStorage('sourceContourDraft');
const id = () => globalThis.crypto.randomUUID();
const labels: Record<string, string> = { ...presetLabels, width_mm: 'Baffle width', length_mm: 'Horn length', mouth_radius_mm: 'Mouth radius', housing_radius_mm: 'Housing radius', backing_depth_mm: 'Backing depth', aperture_radius_mm: 'Aperture radius' };

const defaultAttachment: SourceAttachment = { kind: 'baffle', dimensions: { width_mm: 44, height_mm: 54, depth_mm: 12, aperture_radius_mm: 14, center_mm: [0, 0, 0] } };
export function readSourceAttachment(): SourceAttachment {
  try {
    const envelope = JSON.parse(storage.getItem('') ?? 'null'), attachment = envelope?.attachment as SourceAttachment;
    if (envelope?.version !== 'wg-source-authoring-v2' || !attachment || !['baffle', 'horn'].includes(attachment.kind)) return structuredClone(defaultAttachment);
    const fields = attachment.kind === 'baffle' ? ['width_mm', 'height_mm', 'depth_mm', 'aperture_radius_mm'] : ['length_mm', 'mouth_radius_mm', 'housing_radius_mm', 'backing_depth_mm'];
    if (!fields.every(key => Number.isFinite(attachment.dimensions[key])) || attachment.kind === 'baffle' && (!Array.isArray(attachment.dimensions.center_mm) || attachment.dimensions.center_mm.length !== 3 || !attachment.dimensions.center_mm.every(Number.isFinite))) return structuredClone(defaultAttachment);
    return attachment;
  } catch { return structuredClone(defaultAttachment); }
}
const attachmentLimit = (attachment: SourceAttachment): SourceLimit => ({ kind: attachment.kind === 'baffle' ? 'aperture' : 'mouth', radius: Number(attachment.dimensions[attachment.kind === 'baffle' ? 'aperture_radius_mm' : 'mouth_radius_mm']) });

export function readSourceDraft(): SourceDocument {
  try {
    const raw = storage.getItem('');
    if (!raw) return structuredClone(INITIAL_SOURCE);
    const parsed = JSON.parse(raw);
    const value = (parsed.document ?? parsed) as SourceDocument;
    const c = value.contour; const d = value.drive;
    if (c.version !== 1 || typeof c.physical_source_id !== 'string' || typeof c.rim_id !== 'string'
      || !Array.isArray(c.points) || c.points.length < 2 || c.points.length > 257
      || !Array.isArray(c.segments) || c.segments.length !== c.points.length - 1
      || !c.points.every((p) => typeof p.id === 'string' && Number.isFinite(p.r_mm) && Number.isFinite(p.z_mm))
      || !c.segments.every((s) => [s.id, s.start, s.end].every((v) => typeof v === 'string') && ['line', 'arc'].includes(s.kind) && ['moving', 'rigid'].includes(s.role) && ['cw', 'ccw'].includes(s.direction) && (s.kind === 'line' && s.center_mm === null || Array.isArray(s.center_mm) && s.center_mm.length === 2 && s.center_mm.every(Number.isFinite)))
      || typeof d.channel_id !== 'string' || !['normal', 'axial'].includes(d.motion)
      || !d.weights || typeof d.weights !== 'object' || Array.isArray(d.weights) || !Object.values(d.weights).every(Number.isFinite)) throw new Error('Invalid draft');
    return value;
  } catch { return structuredClone(INITIAL_SOURCE); }
}

export function readSourceAuthoring(document: SourceDocument): Authoring<SourceMeta> {
  try {
    const restored = parseSourceAuthoring(JSON.parse(storage.getItem('') ?? 'null'), document);
    if (restored) return restored;
  } catch { /* fall back to the document */ }
  const fallback = seed(document);
  fallback.meta.weights = movingWeights(fallback.sketch, fallback.meta.weights);
  return fallback;
}

/** Install diagnostics on open; moved references go through replaceContext's solve and notice. */
function openingSourceContext(state: Authoring<SourceMeta>, context: SketchContext): Authoring<SourceMeta> {
  return { ...withSourceContext(state, context), sketch: state.sketch };
}

function readNewerEnvelope(): Record<string, unknown> | undefined {
  try {
    return newerEnvelope(JSON.parse(storage.getItem('') ?? 'null'));
  } catch { /* use the document fallback */ }
}

function restorationNotice(author: Authoring<SourceMeta>): string {
  try {
    const raw = JSON.parse(storage.getItem('') ?? 'null');
    return raw?.sketch && JSON.stringify(raw.sketch) !== JSON.stringify(author.sketch) ? 'Constraints were not restored; using the document contour.' : '';
  } catch { return ''; }
}

type View = { r: number; low: number; high: number };
function fit(points: SourcePoint[], meridian: SourceValidation['meridian'] = {}): View {
  const all = [...points, ...Object.values(meridian).flat().map(([r_mm, z_mm]) => ({ id: '', r_mm, z_mm }))];
  const r = Math.max(1, ...all.map((p) => p.r_mm)) * 1.15;
  const low = Math.min(0, ...all.map((p) => p.z_mm)); const high = Math.max(0, ...all.map((p) => p.z_mm));
  const pad = Math.max(r * .15, (high - low) * .15);
  return { r, low: low - pad, high: high + pad };
}

const visibility = {
  open: false,
  listeners: new Set<() => void>(),
  getSnapshot: () => visibility.open,
  subscribe: (listener: () => void) => { visibility.listeners.add(listener); return () => { visibility.listeners.delete(listener); }; },
  set(open: boolean) { visibility.open = open; visibility.listeners.forEach((listener) => listener()); },
};
export function SourceContourEditor({ row = false }: { row?: boolean } = {}) {
  return <div className="source-editor-launch" data-control-reveal-id="source-contour-launch">{row && <span className="experimental-launch-label"><span>Source contour</span><ExperimentalBadge /></span>}<button type="button" aria-haspopup="dialog" onClick={() => visibility.set(true)}>Edit source contour…</button>{!row && <ExperimentalBadge/>}<small>Draw, edit points and arcs, or save a reusable source preset.</small></div>;
}

/** The App owns the modal, so responsive panel replacement cannot discard edits. */
export function SourceContourDialog() {
  const open = useSyncExternalStore(visibility.subscribe, visibility.getSnapshot, visibility.getSnapshot);
  const close = useCallback(() => visibility.set(false), []);
  useEffect(() => installSketchTest(() => visibility.set(true)), []);
  return open ? createPortal(<Editor onClose={close}/>, document.body) : null;
}

export function Editor({ onClose, initialDocument, onUseDocument, onDraftChange, limit: hostLimit, onLimitChange, context: hostContext, contextStale = false, initialAuthoring, onAuthoringChange }: { onClose: () => void; initialDocument?: SourceDocument; onUseDocument?: (document: SourceDocument) => void; onDraftChange?: (document: SourceDocument) => void; limit?: SourceLimit; onLimitChange?: (radius: number) => void; context?: SketchContext; contextStale?: boolean; initialAuthoring?: unknown; onAuthoringChange?: (envelope: unknown) => void }) {
  const [attachment, setAttachment] = useState<SourceAttachment>(() => initialDocument ? structuredClone(defaultAttachment) : readSourceAttachment());
  const limit: SourceLimit = hostLimit ?? attachmentLimit(attachment);
  const attachmentRef = useRef(attachment); attachmentRef.current = attachment;
  const limitRef = useRef(limit); limitRef.current = limit;
  const [draft, setDraft] = useState(() => initialDocument ? structuredClone(initialDocument) : readSourceDraft());
  const context = useMemo(() => hostContext ?? standaloneContext(hostLimit ? undefined : attachment, draft.contour.points.at(-1)?.r_mm ?? 0), [hostContext, attachment, !!hostLimit, draft.contour.points.at(-1)?.r_mm]);
  const contextRef = useRef(context); contextRef.current = context;
  const [author, setAuthor] = useState(() => {
    const next = initialDocument ? parseSourceAuthoring(initialAuthoring, draft) ?? seed(draft) : readSourceAuthoring(draft);
    return openingSourceContext(withSourceLimit({ ...next, meta: { ...next.meta, weights: movingWeights(next.sketch, next.meta.weights) } }, limit), context);
  });
  const authorRef = useRef(author); authorRef.current = author;
  const [generation, setGeneration] = useState(0);
  const [newer, setNewer] = useState(() => initialDocument ? newerEnvelope(initialAuthoring) : readNewerEnvelope());
  const [classic, setClassic] = useState(() => !!newer);
  const [preview, setPreview] = useState<Authoring<SourceMeta>>();
  const displayAuthor = preview ?? author;
  const [diagnosis, setDiagnosis] = useState<Diagnosis>();
  const [fitRequest, setFitRequest] = useState(0);
  const [canvasSelection, setCanvasSelection] = useState<string[]>([]);
  const [hoverId, setHoverId] = useState<string>();
  const [pointsOpen, setPointsOpen] = useState(false);
  const [dimensionsOpen, setDimensionsOpen] = useState(true);
  const rows = useRef(new Map<string, HTMLTableRowElement>());
  useEffect(() => {
    const id = canvasSelection.at(-1);
    if (!id) return;
    if (author.sketch.points.some(p => p.id === id) && canvasSelection.includes(id)) setPointsOpen(true);
    if (author.sketch.constraints.some(c => c.id === id && 'value' in c)) setDimensionsOpen(true);
    rows.current.get(id)?.scrollIntoView?.({ block: 'nearest' });
  }, [canvasSelection, pointsOpen]);
  const authoringCallback = useRef(onAuthoringChange); authoringCallback.current = onAuthoringChange;
  const draftCallback = useRef(onDraftChange); draftCallback.current = onDraftChange;
  const controller = useRef<SketchController<SourceMeta> | undefined>(undefined);
  const [sketchReady, setSketchReady] = useState(false);
  const [sketchPending, setSketchPending] = useState(false);

  const lowering = useRef<{ state: Authoring<SourceMeta>; result: ReturnType<typeof lower> } | undefined>(undefined);
  const lowered = useMemo(() => lowering.current?.state === author ? lowering.current.result : lower(author.sketch, author.meta), [author]);
  const sketchRules = useMemo(() => rules(displayAuthor.sketch, displayAuthor.meta), [displayAuthor.sketch, displayAuthor.meta]);
  const sketchProblems = useMemo(() => uniqueProblems([...sketchRules, ...(!preview && !lowered.ok ? lowered.problems : [])]), [sketchRules, lowered, preview]);
  const committedProblemCount = useRef(sketchProblems.length);
  if (!preview) committedProblemCount.current = sketchProblems.filter(p => p.severity !== 'warning').length;
  const hasSketchProblems = !classic && committedProblemCount.current > 0;
  const tablePoints = useMemo(() => classic ? draft.contour.points : contourPointOrder(author.sketch, author.meta).map(id => author.sketch.points.find(p => p.id === id)!).map(p => ({ id: p.id, r_mm: p.x, z_mm: p.y })), [classic, draft.contour.points, author]);
  const tablePatches: SourcePatch[] = useMemo(() => classic ? draft.contour.segments : author.sketch.entities.flatMap(e => e.construction || e.kind !== 'line' && e.kind !== 'arc' ? [] : [{
    id: e.id, kind: e.kind, start: e.kind === 'line' ? e.p1 : e.start, end: e.kind === 'line' ? e.p2 : e.end,
    center_mm: e.kind === 'arc' ? [author.sketch.points.find(p => p.id === e.center)!.x, author.sketch.points.find(p => p.id === e.center)!.y] : null,
    direction: e.kind === 'arc' && !e.ccw ? 'cw' : 'ccw',
    role: e.data && typeof e.data === 'object' && !Array.isArray(e.data) && e.data.role === 'rigid' ? 'rigid' : 'moving',
  }]), [classic, draft.contour.segments, author]);
  const [undo, setUndo] = useState<SourceDocument[]>([]);
  const [view, setView] = useState(() => fit(draft.contour.points));
  const [accepted, setAccepted] = useState<{ key: string; value: SourceValidation }>();
  const [validationError, setValidationError] = useState('');
  const [error, setError] = useState(''); const [notice, setNotice] = useState(() => newer ? 'Newer sketch data is read-only. Classic drawing edits preserve it unchanged.' : initialDocument ? '' : restorationNotice(readSourceAuthoring(draft)));
  const [busy, setBusy] = useState(false);
  const [presets, setPresets] = useState<SourcePreset[]>([]);
  const [selected, setSelected] = useState<SourcePreset>();
  const [name, setName] = useState('');
  const [shape, setShape] = useState<'flat' | 'dome' | 'cone'>(() => author.meta.preset?.shape ?? 'cone');
  const [dimensions, setDimensions] = useState<Record<string, number>>(() => ({ ...defaults[author.meta.preset?.shape ?? 'cone'], ...author.meta.preset?.values }));
  const [topologyDimensions, setTopologyDimensions] = useState<Record<string, number>>({});
  const [drawPoints, setDrawPoints] = useState<SourcePoint[] | null>(null);
  const [selectedPatch, setSelectedPatch] = useState(0);
  const [meshSize, setMeshSize] = useState(2);
  const drag = useRef<number | null>(null);
  const fitted = useRef(false);
  const svg = useRef<SVGSVGElement>(null);
  const close = useCallback(() => { if (!busy) onClose(); }, [busy, onClose]);
  const dialog = useModalDialogFocus<HTMLDivElement>({ open: true, onClose: close });
  const key = useMemo(() => JSON.stringify(draft), [draft]);
  const valid = accepted?.key === key && drawPoints === null && (classic || sketchReady && !sketchPending && sketchProblems.every(p => p.severity === 'warning'));
  const [identifiersOpen, setIdentifiersOpen] = useState(false);
  const identityIssue = identityProblem(validationError, [draft.contour.physical_source_id, draft.contour.rim_id]) || draft.drive.channel_id.trim() === '';
  useEffect(() => { if (identityIssue) setIdentifiersOpen(true); }, [identityIssue]);

  const hydrateRef = useRef<() => void>(() => {});
  const deferredHydration = useRef(false);
  hydrateRef.current = () => {
    const envelope = readNewerEnvelope(); setNewer(envelope); if (envelope) setClassic(true);
    const restored = readSourceDraft(), next = readSourceAuthoring(restored), restoredAttachment = readSourceAttachment();
    setAttachment(restoredAttachment);
    const restoredLimit = hostLimit ?? attachmentLimit(restoredAttachment), restoredContext = hostContext ?? standaloneContext(restoredAttachment, restored.contour.points.at(-1)?.r_mm ?? 0);
    const moved = refreshContext(next.sketch, restoredContext).moved;
    setDraft(restored); replaceAuthor(openingSourceContext(withSourceLimit(next, restoredLimit), restoredContext)); setUndo([]); setSelected(undefined); setSelectedPatch(0);
    fitted.current = false; drag.current = null;
    setNotice(envelope ? 'Newer sketch data is read-only. Classic drawing edits preserve it unchanged.' : moved ? 'Reference context changed; contour updated' : restorationNotice(next) || 'Restored source draft from saved settings.');
  };
  useEffect(() => initialDocument ? undefined : durableSettings.subscribe('sourceContourDraft', () => {
    // The settings owner only notifies adopted server state: it already protects
    // edits made while hydration was in flight. Follow that same authority here.
    // During an edit, defer: re-reading storage when it ends is right either way, because a
    // published edit has already replaced the stored copy and a cancelled one has not.
    if (editingIntent.current) { deferredHydration.current = true; return; }
    hydrateRef.current();
  }), [initialDocument]);

  useEffect(() => {
    if (classic) return;
    let current = true;
    const initial = authorRef.current;
    let opening = true;
    setSketchReady(false);
    const next = new SketchController(initial, state => state.meta.references, (state, command) => {
      const result = applySourceCommand(state.sketch, state.meta, command);
      return 'error' in result ? result : { sketch: result.sketch, meta: result.meta };
    }, (state, result) => {
      if (!current) return;
      setDiagnosis(result); setSketchReady(true);
      if (opening) { opening = false; const opened = lower(state.sketch, state.meta); if (opened.ok && sameSourceDocument(draftRef.current, opened.document)) return; }
      authorRef.current = state; setAuthor(state); setPreview(undefined);
      const lowered = lower(state.sketch, state.meta); lowering.current = { state, result: lowered };
      if (lowered.ok) {
        draftRef.current = lowered.document; setDraft(lowered.document); draftCallback.current?.(lowered.document);
      }
      persistAuthoring(state, lowered.ok ? lowered.document : draftRef.current, !lowered.ok);
      setNotice(next.notice ?? ''); setError('');
    }, reason => { if (current) setError(reason instanceof Error ? reason.message : String(reason)); }, active => { if (current) { editingIntent.current = active; setSketchPending(active); if (!active && deferredHydration.current) { deferredHydration.current = false; hydrateRef.current(); } } }, (state, result) => { if (current) { setPreview(state === authorRef.current ? undefined : state); setDiagnosis(result); } }, (state, id, value) => presetDimensionProblem(state.sketch, state.meta, id, value));
    controller.current = next;
    // Initialization errors are reported by queued operations as well.
    return () => { current = false; next.destroy(); controller.current = undefined; };
  }, [classic, generation]);
  const savedAttachment = useRef(JSON.stringify(attachment));
  useEffect(() => {
    const key = JSON.stringify(attachment);
    if (key === savedAttachment.current) return;
    savedAttachment.current = key;
    if (!initialDocument && !newer) persistAuthoring(authorRef.current, draftRef.current, !lower(authorRef.current.sketch, authorRef.current.meta).ok);
  }, [attachment]);
  const installedContext = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (classic || !controller.current) return;
    const current = controller.current.current;
    if (JSON.stringify(current.meta.limit) === JSON.stringify(limit) && installedContext.current === context.fingerprint) return;
    const sameLimit = JSON.stringify(current.meta.limit) === JSON.stringify(limit);
    const refreshed = refreshContext(current.sketch, context);
    const usedContext = refreshed.moved;
    const sameReferences = refreshed.sketch === current.sketch && JSON.stringify(refreshed.missing) === JSON.stringify(current.meta.missingContext ?? []);
    if (sameLimit && sameReferences && JSON.stringify(current.meta.clearances) === JSON.stringify(contextClearances(context))) { installedContext.current = context.fingerprint; return; }
    const oldThroat = current.meta.limit?.kind === 'throat' ? current.meta.limit.radius : undefined;
    const notice = limit.kind === 'throat' && oldThroat !== undefined && oldThroat !== limit.radius
      ? `Throat changed ${oldThroat.toFixed(2)} → ${limit.radius.toFixed(2)} mm; contour updated` : usedContext ? 'Reference context changed; contour updated' : undefined;
    installedContext.current = context.fingerprint;
    try { void controller.current.replaceContext(state => withSourceContext(withSourceLimit(state, limit), context), notice).catch(reason => setError(String(reason))); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }, [limit.kind, limit.radius, limit.hard, context.fingerprint, classic, generation]);
  function editLimit(radius: number) {
    if (limit.hard) return;
    if (hostLimit) onLimitChange?.(radius);
    else setAttachment(a => ({ ...a, dimensions: { ...a.dimensions, [a.kind === 'baffle' ? 'aperture_radius_mm' : 'mouth_radius_mm']: radius } }));
  }
  const draftRef = useRef(draft); draftRef.current = draft;
  const editingIntent = useRef(false);
  function persistAuthoring(state: Authoring<SourceMeta>, document: SourceDocument, pending: boolean) {
    const envelope = newer ? { ...newer, document } : { ...serializeSourceAuthoring(state, pending), document };
    if (initialDocument) authoringCallback.current?.(envelope);
    else storage.setItem('', JSON.stringify(newer ? envelope : { ...envelope, attachment: attachmentRef.current }));
  }
  function replaceAuthor(next: Authoring<SourceMeta>) {
    installedContext.current = undefined;
    setFitRequest(v => v + 1);
    authorRef.current = next; setAuthor(next); setPreview(undefined); setGeneration(v => v + 1); setDiagnosis(undefined); setCanvasSelection([]);
  }
  function sketchCommand(command: Command | ((state: Authoring<SourceMeta>) => Command)) {
    void controller.current?.command(command).catch(reason => { if (!isConstraintConflict(reason)) setError(reason instanceof Error ? reason.message : String(reason)); });
  }
  useEffect(() => {
    let current = true;
    setValidationError('');
    if (hasSketchProblems) return;
    const timer = setTimeout(() => {
      void sourceEditorApi.validate(draft).then((value) => { if (current) { setAccepted({ key, value }); if (!fitted.current && drag.current === null) { setView(fit(value.document.contour.points, value.meridian)); fitted.current = true; } } }, (reason: unknown) => { if (current) setValidationError(reason instanceof Error ? reason.message : String(reason)); });
    }, 180);
    return () => { current = false; clearTimeout(timer); };
  }, [draft, key, hasSketchProblems]);
  useEffect(() => { let current = true; void sourceEditorApi.presets().then((items) => { if (current) setPresets(items); }, (reason: unknown) => { if (current) setError(String(reason)); }); return () => { current = false; }; }, []);

  function change(next: SourceDocument, recordUndo = true) {
    if (!classic && recordUndo && controller.current) {
      void controller.current.metadata(meta => ({ ...meta, physical_source_id: next.contour.physical_source_id, rim_id: next.contour.rim_id,
        channel_id: next.drive.channel_id, motion: next.drive.motion })).catch(reason => setError(String(reason))); return;
    }
    if (recordUndo) setUndo((history) => [...history.slice(-19), structuredClone(draft)]);
    setDraft(next); persistAuthoring(classic ? seed(next) : authorRef.current, next, false); onDraftChange?.(next); setNotice(''); setError('');
  }
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  async function adopt(value: SourceValidation, preset?: { shape: typeof shape; dimensions: Record<string, number> }, savedAuthoring?: unknown) {
    const restored = parseSourceAuthoring(savedAuthoring, value.document);
    const next = withSourceContext(withSourceLimit(preset ? seedPreset(value.document, preset.shape, preset.dimensions) : restored ?? seed(value.document), limitRef.current), contextRef.current);
    if (!classic && controller.current) { await controller.current.adopt(next); }
    else { replaceAuthor(next); const lowered = lower(next.sketch, next.meta); change(lowered.ok ? lowered.document : value.document); }
    if (preset) setDimensions({ ...preset.dimensions });
    else if (restored?.meta.preset) { setShape(restored.meta.preset.shape); setDimensions({ ...defaults[restored.meta.preset.shape], ...restored.meta.preset.values }); }
    setTopologyDimensions({});
    setFitRequest(v => v + 1); setNotice(restored ? 'Restored source preset authoring.' : next.meta.preset ? 'Named dimensions drive this shape.' : 'Constraints were reset for the loaded contour.');
    setView(fit(value.document.contour.points, value.meridian)); setAccepted({ key: JSON.stringify(value.document), value }); setSelectedPatch(0); setDrawPoints(null); setCanvasSelection([]);
  }

  const loadForTest = useRef(adopt); loadForTest.current = adopt;
  useEffect(() => {
    if (!sketchReady || classic || !sketchPerfEnabled()) return;
    return registerSketchLoader(async document => { await loadForTest.current(await sourceEditorApi.validate(document)); });
  }, [sketchReady, classic]);

  function coordinates(event: PointerEvent): [number, number] {
    const rect = svg.current!.getBoundingClientRect();
    return [Math.max(0, ((event.clientX - rect.left) / rect.width * 640 - 30) / 580 * view.r), view.high - ((event.clientY - rect.top) / rect.height * 260 - 20) / 210 * (view.high - view.low)];
  }
  const xy = (p: SourcePoint) => [30 + p.r_mm / view.r * 580, 20 + (view.high - p.z_mm) / (view.high - view.low) * 210];
  const path = (points: [number, number][]) => points.map(([r_mm, z_mm], i) => `${i ? 'L' : 'M'}${xy({ id: '', r_mm, z_mm }).join(',')}`).join(' ');
  const activePreset = !classic && author.meta.preset?.shape === shape ? author.meta.preset : undefined;
  const presetDimensions = { ...dimensions };
  if (activePreset) for (const [parameter, id] of Object.entries(activePreset.dimensions)) {
    const c = author.sketch.constraints.find(c => c.id === id);
    if (c && 'value' in c) presetDimensions[parameter] = c.value;
  }
  if (activePreset) Object.assign(presetDimensions, topologyDimensions);
  function editPresetDimensions(next: Record<string, number>) {
    const parameter = Object.keys(next).find(key => next[key] !== presetDimensions[key]);
    const id = parameter && activePreset?.dimensions[parameter];
    if (id && parameter) {
      const problem = presetDimensionProblem(author.sketch, author.meta, id, next[parameter]);
      if (problem) { setTopologyDimensions({ ...topologyDimensions, [parameter]: next[parameter] }); return; }
      setTopologyDimensions(values => { const updated = { ...values }; delete updated[parameter]; return updated; });
      void run(async () => { await controller.current?.dimension(id, next[parameter]); });
    }
    else setDimensions(next);
  }
  const nameSketch = classic ? seed(draft).sketch : author.sketch;
  const fields = (values: Record<string, number>, write: (next: Record<string, number>) => void, suffix: string) => Object.entries(values).map(([field, value]) => {
    const id = suffix === 'preset' && activePreset?.dimensions[field];
    const inactive = suffix === 'preset' && !!activePreset && (id
      ? !presetDimensionActive(author.sketch, author.meta, field) : activePreset.values?.[field] !== 0);
    const problem = suffix === 'preset' && activePreset && !inactive ? id
      ? presetDimensionProblem(author.sketch, author.meta, id, value)
      : value !== activePreset.values?.[field] ? presetTopologyMessage : undefined : undefined;
    return <NumberField key={field} label={`${suffix === 'attachment' && attachment.kind === 'baffle' && field === 'height_mm' ? 'Baffle height' : suffix === 'attachment' && attachment.kind === 'baffle' && field === 'depth_mm' ? 'Baffle depth' : labels[field] ?? field.replace('_mm', '').replaceAll('_', ' ')} ${suffix}`} unit="mm" value={value}
      disabled={inactive || suffix === 'preset' && field === 'radius_mm' && !!limit.hard} disabledReason={inactive ? reapplyPresetMessage : undefined}
      invalidMessage={problem}
      precision={6} displayPrecision={2} step={.1} onCommit={(v) => write({ ...values, [field]: v })}/>;
  });

  return <div className="source-editor-backdrop"><div ref={dialog} role="dialog" aria-modal="true" aria-label="Source contour editor" className="source-editor">
    <header><div><h2>Source contour</h2><ExperimentalBadge/><p>{EXPERIMENTAL_NOTE}</p><p>Full circular source · dimensions in mm · aligned +Z</p></div><button type="button" disabled={busy} onClick={close}>Close</button></header>
    <fieldset disabled={busy}>
      <div className="source-editor-columns"><div>
        {classic && <div className="source-editor-toolbar">
          <button type="button" onClick={() => setView(fit(draft.contour.points, valid ? accepted!.value.meridian : {}))}>Fit drawing</button>
          <button type="button" disabled={!undo.length || drawPoints !== null} onClick={() => { const previous = undo.at(-1)!; setUndo(undo.slice(0, -1)); change(previous, false); }}>Undo edit</button>
          <button type="button" disabled={drawPoints !== null} onClick={() => setDrawPoints([])}>Draw lines</button>
          {drawPoints !== null && <><button type="button" disabled={drawPoints.length < 2} onClick={() => { change(drawnSource(draft, drawPoints, id)); setSelectedPatch(0); setDrawPoints(null); }}>Finish at rim</button><button type="button" onClick={() => setDrawPoints(null)}>Cancel drawing</button></>}
        </div>}
        {!classic && <SketchCanvas context={context} contextStale={contextStale} fitRequest={fitRequest} sketch={displayAuthor.sketch} diagnosis={diagnosis} references={displayAuthor.meta.references} problems={validationError && !hasSketchProblems ? [...sketchProblems, { code: 'server', ids: [], message: validationError }] : sketchProblems} session={sketchReady && !busy ? controller.current : undefined}
          entityClassName={entity => entity.data && typeof entity.data === 'object' && !Array.isArray(entity.data) && entity.data.role === 'rigid' ? 'rigid' : 'moving'}
          hoverId={hoverId} onHoverChange={setHoverId} nameFor={id => displayName(displayAuthor.sketch, id)} onLimitChange={editLimit}
          selection={canvasSelection} onSelectionChange={ids => { setCanvasSelection(ids); const index = tablePatches.findIndex(p => ids.includes(p.id)); if (index >= 0) setSelectedPatch(index); }}
          problemAction={problem => {
            if (problem.code !== 'pole' && problem.code !== 'rim') return;
            const reference = author.meta.references.find(r => r.kind === (problem.code === 'pole' ? 'axis' : 'baffle')), point = problem.ids[0];
            if (!reference || !point || author.sketch.constraints.some(c => c.kind === 'pointOnLine' && c.point === point && c.line === reference.id)) return;
            return { label: problem.code === 'pole' ? 'Lock pole to axis' : 'Lock rim to rim plane', constraint: { kind: 'pointOnLine', point, line: reference.id } };
          }}/>}
        {classic && <>
        <p>{drawPoints !== null ? 'Click the pole, then points in increasing radius. Finish places the last point on z=0.' : 'Drag a point or select a patch. The table edits the same contour; corners remain explicit.'}</p>
        <svg ref={svg} viewBox="0 0 640 260" preserveAspectRatio="none" aria-label="Source meridian drawing" className="source-meridian"
          onPointerDown={(event) => { if (busy || drawPoints === null || drawPoints.length >= 257) return; const [r, z] = coordinates(event); if (drawPoints.length && r <= drawPoints.at(-1)!.r_mm) { setError('Draw in strictly increasing radius.'); return; } setError(''); setDrawPoints([...drawPoints, { id: id(), r_mm: drawPoints.length ? r : 0, z_mm: z }]); }}
          onPointerMove={(event) => { if (busy || drag.current === null) return; const [r, z] = coordinates(event); change(editPoint(draft, drag.current, r, z), false); }}
          onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}>
          <line x1={30} x2={610} y1={xy({ id: '', r_mm: 0, z_mm: 0 })[1]} y2={xy({ id: '', r_mm: 0, z_mm: 0 })[1]} className="source-axis"/>
          <line x1={30} x2={30} y1={20} y2={230} className="source-axis"/>
          <text x={32} y={15}>z {view.high.toFixed(2)}</text><text x={32} y={251}>r 0 → {view.r.toFixed(2)} mm · z {view.low.toFixed(2)}</text>
          {drawPoints === null ? draft.contour.segments.map((patch, i) => <path key={patch.id} d={path(valid ? accepted!.value.meridian[patch.id] : draft.contour.points.slice(i, i + 2).map((p) => [p.r_mm, p.z_mm]))} className={`${patch.role} ${valid ? '' : 'draft'} ${i === selectedPatch ? 'selected' : ''}`} onPointerDown={(event) => { event.stopPropagation(); setSelectedPatch(i); }}/>) : <path d={path(drawPoints.map((p) => [p.r_mm, p.z_mm]))} className="moving draft"/>}
          {(drawPoints ?? draft.contour.points).map((point, i) => <circle key={point.id} cx={xy(point)[0]} cy={xy(point)[1]} r={5} className="source-handle" onPointerDown={(event) => { if (busy || drawPoints !== null) return; event.stopPropagation(); setUndo((history) => [...history.slice(-19), structuredClone(draft)]); drag.current = i; event.currentTarget.setPointerCapture(event.pointerId); }}><title>{point.id}: r {point.r_mm}, z {point.z_mm}</title></circle>)}
        </svg>
        </>}
        {(classic || sketchProblems.every(p => p.severity === 'warning') && !validationError) && <div role="status" className={`source-validation ${valid ? 'valid' : ''}`}>{validationError || (drawPoints !== null ? 'Drawing draft' : valid ? 'Valid contour · moving and rigid roles preserved' : 'Validating contour…')}</div>}
        <details className="source-points" open={classic || pointsOpen} onToggle={e => setPointsOpen(e.currentTarget.open)}><summary>Points</summary><div className="source-table"><table><thead><tr><th>Point</th><th>r mm</th><th>z mm</th><th/></tr></thead><tbody>{tablePoints.map((point, i) => <tr key={point.id} ref={row => { if (row) rows.current.set(point.id, row); else rows.current.delete(point.id); }} data-row-id={point.id} aria-selected={canvasSelection.includes(point.id)} className={hoverId === point.id ? 'hovered' : ''}
          onPointerEnter={() => setHoverId(point.id)} onPointerLeave={() => setHoverId(undefined)} onClick={() => { if (!classic) setCanvasSelection([point.id]); }}><td title={point.id}><button type="button" aria-label={`Select point ${i + 1}`} onClick={() => setCanvasSelection([point.id])}>{displayName(nameSketch, point.id)}</button></td>
          <td><NumberField label={`Point ${i + 1} radius`} value={point.r_mm} precision={9} displayPrecision={2} step={.1} disabled={classic && i === 0 || drawPoints !== null} onCommit={(v) => classic ? change(editPoint(draft, i, v, point.z_mm)) : sketchCommand(state => ({ kind: 'movePoint', id: point.id, x: v, y: state.sketch.points.find(p => p.id === point.id)!.y }))}/></td>
          <td><NumberField label={`Point ${i + 1} z`} value={point.z_mm} precision={9} displayPrecision={2} step={.1} disabled={classic && i === draft.contour.points.length - 1 || drawPoints !== null} onCommit={(v) => classic ? change(editPoint(draft, i, point.r_mm, v)) : sketchCommand(state => ({ kind: 'movePoint', id: point.id, x: state.sketch.points.find(p => p.id === point.id)!.x, y: v }))}/></td>
          <td>{classic && i > 0 && i < draft.contour.points.length - 1 && <button type="button" aria-label={`Remove point ${i + 1}`} disabled={drawPoints !== null} onClick={() => { try { change(removePoint(draft, i)); setSelectedPatch(0); } catch (reason) { setError(String(reason)); } }}>Remove</button>}</td></tr>)}</tbody></table></div></details>
        {!classic && <details className="source-dimensions" open={dimensionsOpen} onToggle={event => setDimensionsOpen(event.currentTarget.open)}><summary>Dimensions</summary>
          <div className="source-table"><table aria-label="Dimensions"><thead><tr><th>Name</th><th>Value</th><th>Driving</th></tr></thead><tbody>
            {author.sketch.constraints.filter(c => 'value' in c).map(c => 'value' in c && <tr key={c.id} data-row-id={c.id}
              ref={row => { if (row) rows.current.set(c.id, row); else rows.current.delete(c.id); }} aria-selected={canvasSelection.includes(c.id)} className={hoverId === c.id ? 'hovered' : ''}
              onClick={() => setCanvasSelection([c.id])} onPointerEnter={() => setHoverId(c.id)} onPointerLeave={() => setHoverId(undefined)}>
              <td title={c.id}><SketchNameCell value={dimensionName(c)} label={`Dimension ${c.id} name`} id={c.id} maxLength={40} disabled={!sketchReady}
                onCommit={label => sketchCommand(state => {
                  // An empty name reverts to the default: the preset's name for a preset dimension.
                  const parameter = Object.entries(state.meta.preset?.dimensions ?? {}).find(([, id]) => id === c.id)?.[0];
                  return { kind: 'setConstraintLabel', id: c.id, label: label.trim() || (parameter ? presetLabels[parameter] ?? '' : '') };
                })}/></td>
              <td><NumberField label={`Dimension ${c.id} value`} value={c.kind === 'angle' ? c.value * 180 / Math.PI : c.value} unit={c.kind === 'angle' ? '°' : 'mm'}
                precision={9} displayPrecision={2} step={.1} disabled={!sketchReady || !c.driving} disabledReason={!c.driving ? 'Reference dimension' : undefined}
                onCommit={value => void controller.current?.dimension(c.id, value * (c.kind === 'angle' ? Math.PI / 180 : 1)).catch(reason => setError(reason instanceof Error ? reason.message : String(reason)))}/></td>
              <td><button type="button" aria-label={`${c.driving ? 'Make reference' : 'Make driving'} ${dimensionName(c)}`} disabled={!sketchReady || sketchPending} onClick={() => sketchCommand({ kind: 'setDriving', id: c.id, driving: !c.driving })}>{c.driving ? 'Make reference' : 'Make driving'}</button></td>
            </tr>)}
          </tbody></table></div>
        </details>}
        <h3>Patches</h3><p>Weight is literal. A zero-weight moving patch keeps its moving role.</p><div className="source-table"><table><thead><tr><th>Patch</th><th>Name</th><th>Curve</th><th>Role</th><th>Weight</th></tr></thead><tbody>{tablePatches.map((patch, i) => <tr key={patch.id} ref={row => { if (row) rows.current.set(patch.id, row); else rows.current.delete(patch.id); }} data-row-id={patch.id} aria-selected={canvasSelection.includes(patch.id)} className={hoverId === patch.id ? 'hovered' : ''}
          onPointerEnter={() => setHoverId(patch.id)} onPointerLeave={() => setHoverId(undefined)} onClick={() => { setSelectedPatch(i); if (!classic) setCanvasSelection([patch.id]); }}><td title={patch.id}><button type="button" aria-label={`Select patch ${i + 1}`} onClick={() => { setSelectedPatch(i); if (!classic) setCanvasSelection([patch.id]); }}>{i + 1}</button></td>
          <td title={patch.id}>{classic ? displayName(nameSketch, patch.id) : <SketchNameCell value={displayName(nameSketch, patch.id)} label={`Patch ${i + 1} name`} id={patch.id} maxLength={60} disabled={!sketchReady} onCommit={name => sketchCommand({ kind: 'setEntityName', id: patch.id, name })}/>}</td>
          <td>{classic && <select aria-label={`Patch ${i + 1} curve`} value={patch.kind} onChange={(e) => change(editPatch(draft, i, { kind: e.target.value as 'line' | 'arc' }))}><option value="line">Line</option><option value="arc">Arc</option></select>}{!classic && patch.kind}</td>
          <td><select aria-label={`Patch ${i + 1} role`} value={patch.role} onChange={(e) => classic ? change(editPatch(draft, i, { role: e.target.value as 'moving' | 'rigid' })) : sketchCommand({ kind: 'setEntityData', id: patch.id, data: { role: e.target.value } })}><option value="moving">Moving</option><option value="rigid">Rigid</option></select></td>
          <td>{patch.role === 'moving' ? <NumberField label={`Patch ${i + 1} weight`} value={classic ? draft.drive.weights[patch.id] : author.meta.weights[patch.id] ?? 1} precision={6} displayPrecision={3} step={.1} onCommit={(v) => { if (classic) change({ ...draft, drive: { ...draft.drive, weights: { ...draft.drive.weights, [patch.id]: v } } }); else { void controller.current?.metadata(meta => ({ ...meta, weights: { ...meta.weights, [patch.id]: v } })).catch(reason => setError(String(reason))); } }}/> : '—'}</td></tr>)}</tbody></table></div>
        {classic && draft.contour.segments[selectedPatch]?.kind === 'arc' && <div className="source-fields">
          <NumberField label="Arc center radius" unit="mm" precision={9} displayPrecision={2} step={.1} value={draft.contour.segments[selectedPatch].center_mm![0]} onCommit={(v) => change(editPatch(draft, selectedPatch, { center_mm: [v, draft.contour.segments[selectedPatch].center_mm![1]] }))}/>
          <NumberField label="Arc center z" unit="mm" precision={9} displayPrecision={2} step={.1} value={draft.contour.segments[selectedPatch].center_mm![1]} onCommit={(v) => change(editPatch(draft, selectedPatch, { center_mm: [draft.contour.segments[selectedPatch].center_mm![0], v] }))}/>
          <label>Arc direction<select aria-label="Arc direction" value={draft.contour.segments[selectedPatch].direction} onChange={(e) => change(editPatch(draft, selectedPatch, { direction: e.target.value as 'cw' | 'ccw' }))}><option value="cw">Clockwise</option><option value="ccw">Counterclockwise</option></select></label>
        </div>}
        <button type="button" disabled={!valid || tablePatches.length >= 256} onClick={() => {
          if (classic) { void run(async () => await adopt(await sourceEditorApi.split(draft, selectedPatch))); return; }
          const e = author.sketch.entities.find(e => e.id === tablePatches[selectedPatch]?.id);
          if (e?.kind === 'line') { const a = author.sketch.points.find(p => p.id === e.p1)!, b = author.sketch.points.find(p => p.id === e.p2)!; sketchCommand({ kind: 'splitAt', entity: e.id, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }); }
          if (e?.kind === 'arc') { const g = arcGeometry(author.sketch, e), angle = g.start + g.sweep / 2; sketchCommand({ kind: 'splitAt', entity: e.id, x: g.center.x + g.radius * Math.cos(angle), y: g.center.y + g.radius * Math.sin(angle) }); }
        }}>Split selected patch at midpoint</button>
      </div><aside>
        <h3>Motion</h3>
        <details className="source-identifiers" open={identifiersOpen} onToggle={(e) => setIdentifiersOpen(e.currentTarget.open)}><summary>Identifiers</summary>
        {(['physical_source_id', 'rim_id'] as const).map((field) => <label key={field}>{field === 'rim_id' ? 'Rim ID' : 'Physical source ID'}<input aria-label={field === 'rim_id' ? 'Rim ID' : 'Physical source ID'} value={draft.contour[field]} onChange={(e) => change({ ...draft, contour: { ...draft.contour, [field]: e.target.value } })}/></label>)}
        <label>Drive channel ID<input aria-label="Drive channel ID" value={draft.drive.channel_id} onChange={(e) => change({ ...draft, drive: { ...draft.drive, channel_id: e.target.value } })}/></label>
        </details>
        <label>Prescribed motion<select aria-label="Prescribed motion" value={draft.drive.motion} onChange={(e) => change({ ...draft, drive: { ...draft.drive, motion: e.target.value as 'normal' | 'axial' } })}><option value="normal">Normal</option><option value="axial">Axial +Z</option></select></label>
        <h3>Start from a shape</h3>{hostLimit ? <p>{limit.kind === 'throat' ? 'Fits into the horn design' : limit.kind === 'aperture' ? 'Fits into the woofer baffle' : 'Fits into the conical horn'}</p> : <label>Fits into<select aria-label="Source attachment" value={attachment.kind} onChange={(e) => setAttachment(e.target.value === 'baffle' ? structuredClone(defaultAttachment) : { kind: 'horn', dimensions: { mouth_radius_mm: 20, length_mm: 30, housing_radius_mm: 22, backing_depth_mm: 12 } })}><option value="baffle">Front baffle woofer</option><option value="horn">Circular conical horn</option></select></label>}<label>Shape<select aria-label="Source shape" value={shape} onChange={(e) => { const s = e.target.value as typeof shape; setShape(s); setDimensions({ ...defaults[s] }); setTopologyDimensions({}); }}><option value="flat">Flat</option><option value="dome">Dome and surround</option><option value="cone">Cone and dust cap</option></select></label>
        <div className="source-fields">{fields(presetDimensions, editPresetDimensions, 'preset')}</div><button type="button" disabled={drawPoints !== null} onClick={() => void run(async () => { await adopt(await sourceEditorApi.expand(shape, presetDimensions, draft), { shape, dimensions: presetDimensions }); setSelected(undefined); })}>Apply shape</button>
        <p>Rim radius {((presetDimensions.radius_mm ?? 0) + (presetDimensions.surround_width_mm ?? 0) + (presetDimensions.land_width_mm ?? 0)).toFixed(2)} mm</p>
        <button type="button" disabled={drawPoints !== null} onClick={() => void run(async () => {
          const fitted = fitPresetDimensions(presetDimensions, sourceLimitTarget(limit));
          await adopt(await sourceEditorApi.expand(shape, fitted, draft), { shape, dimensions: fitted }); setDimensions(fitted); setSelected(undefined);
        })}>Fit to limit</button>
        <h3>Saved presets</h3><label>Preset name<input aria-label="Preset name" value={name} maxLength={80} onChange={(e) => setName(e.target.value)}/></label><div className="source-editor-toolbar">
          <button type="button" disabled={!valid || !name.trim()} onClick={() => void run(async () => { const item = await sourceEditorApi.save(name, draft, undefined, serializeSourceAuthoring(classic ? seed(draft) : authorRef.current, false)); setPresets([...presets, item]); setSelected(item); setNotice('Saved new preset.'); })}>Save new preset</button>
          <button type="button" disabled={!valid || !selected || !name.trim()} onClick={() => void run(async () => { const item = await sourceEditorApi.save(name, draft, selected, serializeSourceAuthoring(classic ? seed(draft) : authorRef.current, false)); setPresets(presets.map((p) => p.id === item.id ? item : p)); setSelected(item); setNotice('Updated preset.'); })}>Update loaded preset</button>
          <button type="button" onClick={() => void run(async () => { setPresets(await sourceEditorApi.presets()); setNotice('Preset library refreshed. Load a preset to use its latest revision.'); })}>Refresh presets</button>
        </div>
        {presets.map((preset) => <div key={preset.id} className="source-preset"><button type="button" disabled={drawPoints !== null} onClick={() => void run(async () => { await adopt(await sourceEditorApi.validate(preset.document), undefined, preset.authoring); setName(preset.name); setSelected(preset); })}>Load {preset.name}</button><button type="button" aria-label={`Delete preset ${preset.name}`} onClick={() => void run(async () => { await sourceEditorApi.delete(preset); setPresets(presets.filter((p) => p.id !== preset.id)); if (selected?.id === preset.id) setSelected(undefined); setNotice('Deleted preset. Current contour retained.'); })}>Delete</button></div>)}
        {!hostLimit && <><h3>Export geometry</h3>
        <div className="source-fields">{fields(Object.fromEntries(Object.entries(attachment.dimensions).filter((entry): entry is [string, number] => typeof entry[1] === 'number')), (next) => setAttachment({ ...attachment, dimensions: { ...attachment.dimensions, ...next } }), 'attachment')}
          {attachment.kind === 'baffle' && ([0, 1, 2] as const).map((axis) => <NumberField key={axis} label={`Source center ${['X', 'Y', 'Z'][axis]}`} value={(attachment.dimensions.center_mm as [number, number, number])[axis]} precision={6} displayPrecision={2} unit="mm" step={.1} onCommit={(v) => { const center = [...attachment.dimensions.center_mm as [number, number, number]] as [number, number, number]; center[axis] = v; setAttachment({ ...attachment, dimensions: { ...attachment.dimensions, center_mm: center } }); }}/>) }
          <NumberField label="Mesh size" value={meshSize} min={.001} precision={6} displayPrecision={3} unit="mm" step={.1} onCommit={setMeshSize}/>
        </div><button type="button" disabled={!valid} onClick={() => void run(async () => {
          const blob = await sourceEditorApi.export(draft, attachment, meshSize); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = 'native-source.zip'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 30_000); setNotice('Exported native source bundle: STEP, mesh and hash-bound source recipe.');
        })}>Export STEP + mesh bundle</button>
        <p>Source presets store the contour and drive. Attachment and mesh size apply to this export.</p></>}
      </aside></div>
    </fieldset>
    <footer>{onUseDocument && <button type="button" disabled={!valid || busy} onClick={() => onUseDocument(accepted!.value.document)}>Use contour in assembly</button>}{busy && <p role="status">Working…</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}<small>{onDraftChange ? 'Contour edits are remembered in the assembly draft.' : initialDocument ? 'Use contour to apply these edits to the assembly.' : 'Draft changes are remembered.'} Use Undo to recover replaced contours. Names are stored in the sketch only.</small></footer>
  </div></div>;
}
