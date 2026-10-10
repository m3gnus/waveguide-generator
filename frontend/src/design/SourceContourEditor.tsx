import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type PointerEvent } from 'react';
import { createPortal } from 'react-dom';
import { sourceEditorApi, type SourceAttachment, type SourceDocument, type SourcePoint, type SourcePreset, type SourceValidation } from '../api/sourceEditor';
import { durableSettings, namespaceStorage } from '../stores/durableSettings';
import { useModalDialogFocus } from '../shell/dialogFocus';
import { NumberField } from './NumberField';
import { drawnSource, editPatch, editPoint, INITIAL_SOURCE, removePoint } from './sourceContourEditing';
import { ExperimentalBadge, EXPERIMENTAL_NOTE } from './ExperimentalBadge';
import './sourceContourEditor.css';

const storage = namespaceStorage('sourceContourDraft');
const id = () => globalThis.crypto.randomUUID();
const defaults = {
  flat: { radius_mm: 8 },
  dome: { radius_mm: 8, height_mm: 3, surround_width_mm: 2, surround_depth_mm: .5, land_width_mm: 1 },
  cone: { radius_mm: 10, depth_mm: 4, cap_radius_mm: 3, cap_height_mm: 2, surround_width_mm: 2, surround_depth_mm: .6, land_width_mm: 1 },
};
const labels: Record<string, string> = { radius_mm: 'Radius', height_mm: 'Dome height', depth_mm: 'Cone depth', cap_radius_mm: 'Dust-cap radius', cap_height_mm: 'Dust-cap height', surround_width_mm: 'Surround width', surround_depth_mm: 'Surround depth', land_width_mm: 'Rigid land width', width_mm: 'Baffle width', length_mm: 'Horn length', mouth_radius_mm: 'Mouth radius', housing_radius_mm: 'Housing radius', backing_depth_mm: 'Backing depth', aperture_radius_mm: 'Aperture radius' };

export function readSourceDraft(): SourceDocument {
  try {
    const raw = storage.getItem('');
    if (!raw) return structuredClone(INITIAL_SOURCE);
    const value = JSON.parse(raw) as SourceDocument;
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
export function SourceContourEditor() {
  return <div className="source-editor-launch"><button type="button" aria-haspopup="dialog" onClick={() => visibility.set(true)}>Edit source contour…</button><ExperimentalBadge/><small>Draw, edit points and arcs, or save a reusable source preset. {EXPERIMENTAL_NOTE}</small></div>;
}

/** The App owns the modal, so responsive panel replacement cannot discard edits. */
export function SourceContourDialog() {
  const open = useSyncExternalStore(visibility.subscribe, visibility.getSnapshot, visibility.getSnapshot);
  const close = useCallback(() => visibility.set(false), []);
  return open ? createPortal(<Editor onClose={close}/>, document.body) : null;
}

export function Editor({ onClose, initialDocument, onUseDocument, onDraftChange }: { onClose: () => void; initialDocument?: SourceDocument; onUseDocument?: (document: SourceDocument) => void; onDraftChange?: (document: SourceDocument) => void }) {
  const [draft, setDraft] = useState(() => initialDocument ? structuredClone(initialDocument) : readSourceDraft());
  const [undo, setUndo] = useState<SourceDocument[]>([]);
  const [view, setView] = useState(() => fit(draft.contour.points));
  const [accepted, setAccepted] = useState<{ key: string; value: SourceValidation }>();
  const [validationError, setValidationError] = useState('');
  const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [presets, setPresets] = useState<SourcePreset[]>([]);
  const [selected, setSelected] = useState<SourcePreset>();
  const [name, setName] = useState('');
  const [shape, setShape] = useState<'flat' | 'dome' | 'cone'>('cone');
  const [dimensions, setDimensions] = useState<Record<string, number>>({ ...defaults.cone });
  const [drawPoints, setDrawPoints] = useState<SourcePoint[] | null>(null);
  const [selectedPatch, setSelectedPatch] = useState(0);
  const [attachment, setAttachment] = useState<SourceAttachment>({ kind: 'baffle', dimensions: { width_mm: 44, height_mm: 54, depth_mm: 12, aperture_radius_mm: 14, center_mm: [0, 0, 0] } });
  const [meshSize, setMeshSize] = useState(2);
  const drag = useRef<number | null>(null);
  const fitted = useRef(false);
  const svg = useRef<SVGSVGElement>(null);
  const close = useCallback(() => { if (!busy) onClose(); }, [busy, onClose]);
  const dialog = useModalDialogFocus<HTMLDivElement>({ open: true, onClose: close });
  const key = JSON.stringify(draft);
  const valid = accepted?.key === key && drawPoints === null;

  useEffect(() => initialDocument ? undefined : durableSettings.subscribe('sourceContourDraft', () => {
    // The settings owner only notifies adopted server state: it already protects
    // edits made while hydration was in flight. Follow that same authority here.
    setDraft(readSourceDraft()); setUndo([]); setSelected(undefined); setSelectedPatch(0);
    fitted.current = false; drag.current = null;
    setNotice('Restored source draft from saved settings.');
  }), [initialDocument]);

  useEffect(() => {
    let current = true;
    setValidationError('');
    const timer = setTimeout(() => {
      void sourceEditorApi.validate(draft).then((value) => { if (current) { setAccepted({ key, value }); if (!fitted.current && drag.current === null) { setView(fit(value.document.contour.points, value.meridian)); fitted.current = true; } } }, (reason: unknown) => { if (current) setValidationError(reason instanceof Error ? reason.message : String(reason)); });
    }, 180);
    return () => { current = false; clearTimeout(timer); };
  }, [draft, key]);
  useEffect(() => { let current = true; void sourceEditorApi.presets().then((items) => { if (current) setPresets(items); }, (reason: unknown) => { if (current) setError(String(reason)); }); return () => { current = false; }; }, []);

  function change(next: SourceDocument, recordUndo = true) {
    if (recordUndo) setUndo((history) => [...history.slice(-19), structuredClone(draft)]);
    setDraft(next); if (!initialDocument) storage.setItem('', JSON.stringify(next)); onDraftChange?.(next); setNotice(''); setError('');
  }
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  function adopt(value: SourceValidation) { change(value.document); setView(fit(value.document.contour.points, value.meridian)); setAccepted({ key: JSON.stringify(value.document), value }); setSelectedPatch(0); setDrawPoints(null); }
  function coordinates(event: PointerEvent): [number, number] {
    const rect = svg.current!.getBoundingClientRect();
    return [Math.max(0, ((event.clientX - rect.left) / rect.width * 640 - 30) / 580 * view.r), view.high - ((event.clientY - rect.top) / rect.height * 260 - 20) / 210 * (view.high - view.low)];
  }
  const xy = (p: SourcePoint) => [30 + p.r_mm / view.r * 580, 20 + (view.high - p.z_mm) / (view.high - view.low) * 210];
  const path = (points: [number, number][]) => points.map(([r_mm, z_mm], i) => `${i ? 'L' : 'M'}${xy({ id: '', r_mm, z_mm }).join(',')}`).join(' ');
  const fields = (values: Record<string, number>, write: (next: Record<string, number>) => void, suffix: string) => Object.entries(values).map(([field, value]) => <NumberField key={field} label={`${suffix === 'attachment' && attachment.kind === 'baffle' && field === 'height_mm' ? 'Baffle height' : suffix === 'attachment' && attachment.kind === 'baffle' && field === 'depth_mm' ? 'Baffle depth' : labels[field] ?? field.replace('_mm', '').replaceAll('_', ' ')} ${suffix}`} unit="mm" value={value} precision={6} step={.1} onCommit={(v) => write({ ...values, [field]: v })}/>);

  return <div className="source-editor-backdrop"><div ref={dialog} role="dialog" aria-modal="true" aria-label="Source contour editor" className="source-editor">
    <header><div><h2>Source contour<ExperimentalBadge/></h2><p>Full circular source · dimensions in mm · aligned +Z</p></div><button type="button" disabled={busy} onClick={close}>Close</button></header>
    <fieldset disabled={busy}>
      <div className="source-editor-columns"><div>
        <div className="source-editor-toolbar"><button type="button" onClick={() => setView(fit(draft.contour.points, valid ? accepted!.value.meridian : {}))}>Fit drawing</button><button type="button" disabled={!undo.length || drawPoints !== null} onClick={() => { const previous = undo.at(-1)!; setUndo(undo.slice(0, -1)); change(previous, false); }}>Undo edit</button>
          <button type="button" disabled={drawPoints !== null} onClick={() => setDrawPoints([])}>Draw lines</button>
          {drawPoints !== null && <><button type="button" disabled={drawPoints.length < 2} onClick={() => { change(drawnSource(draft, drawPoints, id)); setSelectedPatch(0); setDrawPoints(null); }}>Finish at rim</button><button type="button" onClick={() => setDrawPoints(null)}>Cancel drawing</button></>}
        </div>
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
        <div role="status" className={`source-validation ${valid ? 'valid' : ''}`}>{validationError || (drawPoints !== null ? 'Drawing draft' : valid ? 'Valid contour · moving and rigid roles preserved' : 'Validating contour…')}</div>
        <h3>Points</h3><div className="source-table"><table><thead><tr><th>Point ID</th><th>r mm</th><th>z mm</th><th/></tr></thead><tbody>{draft.contour.points.map((point, i) => <tr key={point.id}><td title={point.id}>{point.id}</td>
          <td><NumberField label={`Point ${i + 1} radius`} value={point.r_mm} precision={9} step={.1} disabled={i === 0 || drawPoints !== null} onCommit={(v) => change(editPoint(draft, i, v, point.z_mm))}/></td>
          <td><NumberField label={`Point ${i + 1} z`} value={point.z_mm} precision={9} step={.1} disabled={i === draft.contour.points.length - 1 || drawPoints !== null} onCommit={(v) => change(editPoint(draft, i, point.r_mm, v))}/></td>
          <td>{i > 0 && i < draft.contour.points.length - 1 && <button type="button" aria-label={`Remove point ${i + 1}`} disabled={drawPoints !== null} onClick={() => { try { change(removePoint(draft, i)); setSelectedPatch(0); } catch (reason) { setError(String(reason)); } }}>Remove</button>}</td></tr>)}</tbody></table></div>
        <h3>Patches</h3><p>Weight is literal. A zero-weight moving patch keeps its moving role.</p><div className="source-table"><table><thead><tr><th>Patch ID</th><th>Curve</th><th>Role</th><th>Weight</th></tr></thead><tbody>{draft.contour.segments.map((patch, i) => <tr key={patch.id} onClick={() => setSelectedPatch(i)}><td title={patch.id}>{patch.id}</td>
          <td><select aria-label={`Patch ${i + 1} curve`} value={patch.kind} onChange={(e) => change(editPatch(draft, i, { kind: e.target.value as 'line' | 'arc' }))}><option value="line">Line</option><option value="arc">Arc</option></select></td>
          <td><select aria-label={`Patch ${i + 1} role`} value={patch.role} onChange={(e) => change(editPatch(draft, i, { role: e.target.value as 'moving' | 'rigid' }))}><option value="moving">Moving</option><option value="rigid">Rigid</option></select></td>
          <td>{patch.role === 'moving' ? <NumberField label={`Patch ${i + 1} weight`} value={draft.drive.weights[patch.id]} precision={6} step={.1} onCommit={(v) => change({ ...draft, drive: { ...draft.drive, weights: { ...draft.drive.weights, [patch.id]: v } } })}/> : '—'}</td></tr>)}</tbody></table></div>
        {draft.contour.segments[selectedPatch]?.kind === 'arc' && <div className="source-fields">
          <NumberField label="Arc center radius" unit="mm" precision={9} step={.1} value={draft.contour.segments[selectedPatch].center_mm![0]} onCommit={(v) => change(editPatch(draft, selectedPatch, { center_mm: [v, draft.contour.segments[selectedPatch].center_mm![1]] }))}/>
          <NumberField label="Arc center z" unit="mm" precision={9} step={.1} value={draft.contour.segments[selectedPatch].center_mm![1]} onCommit={(v) => change(editPatch(draft, selectedPatch, { center_mm: [draft.contour.segments[selectedPatch].center_mm![0], v] }))}/>
          <label>Arc direction<select aria-label="Arc direction" value={draft.contour.segments[selectedPatch].direction} onChange={(e) => change(editPatch(draft, selectedPatch, { direction: e.target.value as 'cw' | 'ccw' }))}><option value="cw">Clockwise</option><option value="ccw">Counterclockwise</option></select></label>
        </div>}
        <button type="button" disabled={!valid || draft.contour.segments.length >= 256} onClick={() => void run(async () => adopt(await sourceEditorApi.split(draft, selectedPatch)))}>Split selected patch at midpoint</button>
      </div><aside>
        <h3>Identity and motion</h3>
        {(['physical_source_id', 'rim_id'] as const).map((field) => <label key={field}>{field === 'rim_id' ? 'Rim ID' : 'Physical source ID'}<input aria-label={field === 'rim_id' ? 'Rim ID' : 'Physical source ID'} value={draft.contour[field]} onChange={(e) => change({ ...draft, contour: { ...draft.contour, [field]: e.target.value } })}/></label>)}
        <label>Drive channel ID<input aria-label="Drive channel ID" value={draft.drive.channel_id} onChange={(e) => change({ ...draft, drive: { ...draft.drive, channel_id: e.target.value } })}/></label>
        <label>Prescribed motion<select aria-label="Prescribed motion" value={draft.drive.motion} onChange={(e) => change({ ...draft, drive: { ...draft.drive, motion: e.target.value as 'normal' | 'axial' } })}><option value="normal">Normal</option><option value="axial">Axial +Z</option></select></label>
        <h3>Start from a shape</h3><label>Shape<select aria-label="Source shape" value={shape} onChange={(e) => { const s = e.target.value as typeof shape; setShape(s); setDimensions({ ...defaults[s] }); }}><option value="flat">Flat</option><option value="dome">Dome and surround</option><option value="cone">Cone and dust cap</option></select></label>
        <div className="source-fields">{fields(dimensions, setDimensions, 'preset')}</div><button type="button" disabled={drawPoints !== null} onClick={() => void run(async () => { adopt(await sourceEditorApi.expand(shape, dimensions, draft)); setSelected(undefined); })}>Apply shape</button>
        <h3>Saved presets</h3><label>Preset name<input aria-label="Preset name" value={name} maxLength={80} onChange={(e) => setName(e.target.value)}/></label><div className="source-editor-toolbar">
          <button type="button" disabled={!valid || !name.trim()} onClick={() => void run(async () => { const item = await sourceEditorApi.save(name, draft); setPresets([...presets, item]); setSelected(item); setNotice('Saved new preset.'); })}>Save new preset</button>
          <button type="button" disabled={!valid || !selected || !name.trim()} onClick={() => void run(async () => { const item = await sourceEditorApi.save(name, draft, selected); setPresets(presets.map((p) => p.id === item.id ? item : p)); setSelected(item); setNotice('Updated preset.'); })}>Update loaded preset</button>
          <button type="button" onClick={() => void run(async () => { setPresets(await sourceEditorApi.presets()); setNotice('Preset library refreshed. Load a preset to use its latest revision.'); })}>Refresh presets</button>
        </div>
        {presets.map((preset) => <div key={preset.id} className="source-preset"><button type="button" disabled={drawPoints !== null} onClick={() => void run(async () => { adopt(await sourceEditorApi.validate(preset.document)); setName(preset.name); setSelected(preset); })}>Load {preset.name}</button><button type="button" aria-label={`Delete preset ${preset.name}`} onClick={() => void run(async () => { await sourceEditorApi.delete(preset); setPresets(presets.filter((p) => p.id !== preset.id)); if (selected?.id === preset.id) setSelected(undefined); setNotice('Deleted preset. Current contour retained.'); })}>Delete</button></div>)}
        <h3>Export geometry<ExperimentalBadge/></h3><label>Attachment<select aria-label="Source attachment" value={attachment.kind} onChange={(e) => setAttachment(e.target.value === 'baffle' ? { kind: 'baffle', dimensions: { width_mm: 44, height_mm: 54, depth_mm: 12, aperture_radius_mm: 14, center_mm: [0, 0, 0] } } : { kind: 'horn', dimensions: { mouth_radius_mm: 20, length_mm: 30, housing_radius_mm: 22, backing_depth_mm: 12 } })}><option value="baffle">Front baffle woofer</option><option value="horn">Circular conical horn</option></select></label>
        <div className="source-fields">{fields(Object.fromEntries(Object.entries(attachment.dimensions).filter((entry): entry is [string, number] => typeof entry[1] === 'number')), (next) => setAttachment({ ...attachment, dimensions: { ...attachment.dimensions, ...next } }), 'attachment')}
          {attachment.kind === 'baffle' && ([0, 1, 2] as const).map((axis) => <NumberField key={axis} label={`Source center ${['X', 'Y', 'Z'][axis]}`} value={(attachment.dimensions.center_mm as [number, number, number])[axis]} precision={6} unit="mm" step={.1} onCommit={(v) => { const center = [...attachment.dimensions.center_mm as [number, number, number]] as [number, number, number]; center[axis] = v; setAttachment({ ...attachment, dimensions: { ...attachment.dimensions, center_mm: center } }); }}/>) }
          <NumberField label="Mesh size" value={meshSize} min={.001} precision={6} unit="mm" step={.1} onCommit={setMeshSize}/>
        </div><button type="button" disabled={!valid} onClick={() => void run(async () => {
          const blob = await sourceEditorApi.export(draft, attachment, meshSize); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = 'native-source.zip'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 30_000); setNotice('Exported native source bundle: STEP, mesh and hash-bound source recipe.');
        })}>Export STEP + mesh bundle</button>
        <p>Source presets store the contour and drive. Attachment and mesh size apply to this export.</p>
      </aside></div>
    </fieldset>
    <footer>{onUseDocument && <button type="button" disabled={!valid || busy} onClick={() => onUseDocument(accepted!.value.document)}>Use contour in assembly</button>}{busy && <p role="status">Working…</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}<small>{onDraftChange ? 'Contour edits are remembered in the assembly draft.' : initialDocument ? 'Use contour to apply these edits to the assembly.' : 'Draft changes are remembered.'} Use Undo to recover replaced contours.</small></footer>
  </div></div>;
}
