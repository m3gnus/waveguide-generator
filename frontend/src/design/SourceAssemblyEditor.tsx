import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { sourceEditorApi, type AssemblyValidation, type SourceAssemblyDocument, type SourceDocument } from '../api/sourceEditor';
import { durableSettings, namespaceStorage } from '../stores/durableSettings';
import { useModalDialogFocus } from '../shell/dialogFocus';
import { NumberField } from './NumberField';
import { serializeDesign, useDesignStore } from '../stores/design';
import { Editor } from './SourceContourEditor';
import { INITIAL_SOURCE } from './sourceContourEditing';
import { prepareNativeAssembly } from './nativeAssemblyPreparation';
import { ExperimentalBadge, EXPERIMENTAL_NOTE } from './ExperimentalBadge';
import './sourceContourEditor.css';

const storage = namespaceStorage('sourceAssemblyDraft');
function flatSource(id: string, radius: number): SourceDocument {
  const document = structuredClone(INITIAL_SOURCE);
  document.contour.physical_source_id = id; document.contour.rim_id = `${id}.rim`;
  document.contour.points[1].r_mm = radius; document.drive.channel_id = id === 'horn' ? 'hf' : 'lf';
  return document;
}
export const INITIAL_ASSEMBLY: SourceAssemblyDocument = {
  horn: flatSource('horn', 4), woofer: flatSource('woofer', 8),
  dimensions: { width_mm: 70, height_mm: 90, depth_mm: 40, front_z_mm: 7,
    horn_xy_mm: [-13, 17], horn_length_mm: 24, mouth_radius_mm: 10,
    woofer_xy_mm: [9, -15], aperture_radius_mm: 8 },
  phase_plugs: [], mesh_size_mm: 4, passage_refinement: 1,
};
function sourceShape(value: SourceDocument): boolean {
  const c = value?.contour, d = value?.drive;
  return !!c && !!d && c.version === 1 && typeof c.physical_source_id === 'string' && typeof c.rim_id === 'string'
    && Array.isArray(c.points) && c.points.length >= 2 && c.points.length <= 257
    && c.points.every((p) => typeof p.id === 'string' && Number.isFinite(p.r_mm) && Number.isFinite(p.z_mm))
    && Array.isArray(c.segments) && c.segments.length === c.points.length - 1
    && c.segments.every((s) => [s.id, s.start, s.end].every((id) => typeof id === 'string')
      && ['moving', 'rigid'].includes(s.role) && ['line', 'arc'].includes(s.kind)
      && ['cw', 'ccw'].includes(s.direction)
      && (s.kind === 'line' || Array.isArray(s.center_mm) && s.center_mm.length === 2 && s.center_mm.every(Number.isFinite)))
    && typeof d.channel_id === 'string' && ['normal', 'axial'].includes(d.motion)
    && !!d.weights && typeof d.weights === 'object' && !Array.isArray(d.weights) && Object.values(d.weights).every(Number.isFinite);
}
export function parseAssemblyDraft(raw: string): SourceAssemblyDocument {
  const value = JSON.parse(raw) as SourceAssemblyDocument;
  if (!sourceShape(value.horn) || (value.woofer !== null && !sourceShape(value.woofer)) || !value.dimensions
      || !Object.entries(INITIAL_ASSEMBLY.dimensions).every(([key, initial]) => {
        const entry = value.dimensions[key as keyof typeof value.dimensions];
        return Array.isArray(initial) ? Array.isArray(entry) && entry.length === 2 && entry.every(Number.isFinite) : Number.isFinite(entry);
      }) || !Array.isArray(value.phase_plugs) || value.phase_plugs.length > 8
      || !value.phase_plugs.every((p) => typeof p.id === 'string' && ['z0_mm', 'z1_mm', 'inner0_mm', 'outer0_mm', 'inner1_mm', 'outer1_mm'].every((key) => Number.isFinite(p[key as keyof typeof p])))
      || (value.horn_config !== undefined && (!value.horn_config || typeof value.horn_config !== 'object' || Array.isArray(value.horn_config)))
      || !Number.isFinite(value.mesh_size_mm) || ![1, 2, 4].includes(value.passage_refinement)) throw new Error('This file is not a source assembly draft.');
  return value;
}
function readDraft(): SourceAssemblyDocument {
  try { const raw = storage.getItem(''); return raw ? parseAssemblyDraft(raw) : structuredClone(INITIAL_ASSEMBLY); }
  catch { return structuredClone(INITIAL_ASSEMBLY); }
}
const visibility = { open: false, listeners: new Set<() => void>(), getSnapshot: () => visibility.open,
  subscribe: (listener: () => void) => { visibility.listeners.add(listener); return () => { visibility.listeners.delete(listener); }; },
  set(open: boolean) { visibility.open = open; visibility.listeners.forEach((listener) => listener()); } };
export function SourceAssemblyLauncher() {
  return <div className="source-editor-launch"><button type="button" aria-haspopup="dialog" onClick={() => visibility.set(true)}>Edit horn + woofer assembly…</button><ExperimentalBadge/><small>Shared enclosure, source contours and phase-plug passages. {EXPERIMENTAL_NOTE}</small></div>;
}
export function SourceAssemblyDialog() {
  const open = useSyncExternalStore(visibility.subscribe, visibility.getSnapshot, visibility.getSnapshot);
  return open ? createPortal(<AssemblyEditor onClose={() => visibility.set(false)}/>, document.body) : null;
}
export function AssemblyEditor({ onClose }: { onClose: () => void }) {
  const [draft, setDraft] = useState(readDraft);
  const [hornText, setHornText] = useState(() => JSON.stringify(readDraft().horn_config ?? {}, null, 2));
  const [editing, setEditing] = useState<'horn' | 'woofer' | null>(null);
  const [accepted, setAccepted] = useState<{ key: string; value: AssemblyValidation }>();
  const [error, setError] = useState(''); const [validationError, setValidationError] = useState('');
  const [notice, setNotice] = useState(''); const [busy, setBusy] = useState(false);
  const key = JSON.stringify(draft); const valid = accepted?.key === key;
  const close = useCallback(() => { if (!busy) onClose(); }, [busy, onClose]);
  const dialog = useModalDialogFocus<HTMLDivElement>({ open: !editing, onClose: close });
  function change(next: SourceAssemblyDocument) { setDraft(next); storage.setItem('', JSON.stringify(next)); setError(''); setNotice(''); }
  useEffect(() => durableSettings.subscribe('sourceAssemblyDraft', () => { const restored = readDraft(); setDraft(restored); setHornText(JSON.stringify(restored.horn_config ?? {}, null, 2)); setEditing(null); setNotice('Restored source assembly draft.'); }), []);
  useEffect(() => {
    let current = true; setValidationError('');
    const timer = setTimeout(() => { void sourceEditorApi.validateAssembly(draft).then((value) => {
      if (current) setAccepted({ key, value });
    }, (reason: unknown) => { if (current) setValidationError(reason instanceof Error ? reason.message : String(reason)); }); }, 180);
    return () => { current = false; clearTimeout(timer); };
  }, [draft, key]);
  async function run(action: () => Promise<void>) { setBusy(true); setError(''); setNotice(''); try { await action(); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } finally { setBusy(false); } }
  function download(blob: Blob, name: string) { const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 30_000); }
  // Publish nested edits to the assembly namespace immediately: the settings
  // owner protects genuine local changes from an in-flight hydration reply.
  if (editing) return <Editor key={editing} initialDocument={draft[editing]!} onDraftChange={(document) => change({ ...draft, [editing]: document })} onClose={() => setEditing(null)} onUseDocument={(document) => { change({ ...draft, [editing]: document }); setEditing(null); }}/>;
  const dimensions = valid && draft.horn_config && accepted?.value.recipe
    ? { ...draft.dimensions, horn_length_mm: accepted.value.recipe.horn_length_mm, mouth_radius_mm: accepted.value.recipe.mouth_radius_mm }
    : draft.dimensions;
  const scalarLabels = { width_mm: 'Enclosure width', height_mm: 'Enclosure height', depth_mm: 'Enclosure depth', front_z_mm: 'Front Z', horn_length_mm: 'Horn length', mouth_radius_mm: 'Horn mouth radius', aperture_radius_mm: 'Woofer aperture radius' };
  const maxR = Math.max(dimensions.mouth_radius_mm, ...draft.phase_plugs.map((p) => Math.max(p.outer0_mm, p.outer1_mm)), 1);
  const minZ = Math.min(0, ...draft.horn.contour.points.map((p) => p.z_mm));
  const span = Math.max(1, dimensions.horn_length_mm - minZ);
  const sectionPath = (points: [number, number][]) => points.map(([r,z], i) => `${i ? 'L' : 'M'}${25 + (z - minZ) / span * 550},${210 - r / maxR * 175}`).join(' ');
  return <div className="source-editor-backdrop"><div ref={dialog} role="dialog" aria-modal="true" aria-label="Horn and woofer assembly editor" className="source-editor">
    <header><div><h2>Horn + woofer assembly<ExperimentalBadge/></h2><p>Shared enclosure · dimensions in mm · parallel +Z axes</p></div><button type="button" disabled={busy} onClick={close}>Close</button></header>
    <fieldset disabled={busy}><div className="source-editor-columns"><div>
      <h3>Sources</h3><p>Each diaphragm keeps one physical identity and drive channel, including its patch weights.</p>
      <div className="source-editor-toolbar"><button type="button" onClick={() => setEditing('horn')}>Edit horn contour…</button>{draft.woofer && <button type="button" onClick={() => setEditing('woofer')}>Edit woofer contour…</button>}</div>
      <p>Horn: {draft.horn.contour.physical_source_id} · {draft.horn.drive.channel_id}. {draft.woofer ? <>Woofer: {draft.woofer.contour.physical_source_id} · {draft.woofer.drive.channel_id}.</> : "Horn only."}</p>
      <label><input type="checkbox" checked={!!draft.woofer} onChange={(e) => change({ ...draft, woofer: e.target.checked ? flatSource('woofer', 8) : null,
        dimensions: { ...draft.dimensions, woofer_xy_mm: e.target.checked ? [9, -15] : [0, 0], aperture_radius_mm: e.target.checked ? 8 : 0 } })}/> Include woofer</label>
      <h3>Horn profile</h3>
      <button type="button" onClick={() => void run(async () => {
        const profile = await sourceEditorApi.hornProfile(serializeDesign(useDesignStore.getState().design));
        setHornText(JSON.stringify(profile.horn_config, null, 2));
        change({ ...draft, horn_config: profile.horn_config, dimensions: { ...draft.dimensions,
          horn_length_mm: profile.horn_length_mm, mouth_radius_mm: profile.mouth_radius_mm } });
        setNotice(`Current horn profile attached. Its throat radius is ${profile.throat_radius_mm} mm; the horn contour rim must match.`);
      })}>Use current horn design</button>
      <p>Uses the full horn profile on this assembly enclosure. Match the horn contour rim to the throat and size the enclosure around its mouth.</p>
      <label><input type="checkbox" checked={!!draft.horn_config} onChange={(e) => {
        if (!e.target.checked) { const { horn_config: _config, ...plain } = draft; change(plain); }
        else { const example = { formula: 'OSSE', mode: 'bare', profile: { L_mm: 24, r0_mm: 4, a_deg: 32, a0_deg: 6 }, mesh: { angular_segments: 64, length_segments: 32, throat_res_mm: 2, mouth_res_mm: 3 } };
          setHornText(JSON.stringify(example, null, 2)); change({ ...draft, horn_config: example }); }
      }}/> Attach to a resolved horn profile</label>
      {draft.horn_config && <details><summary>Advanced horn configuration</summary><p>Paste a native bare horn configuration. Circular OSSE, R-OSSE, ICW and FREEFORM profiles retain their nonlinear wall. The source rim must match its throat. Length and mouth radius come from the profile.</p>
        <label>Horn configuration<textarea aria-label="Horn configuration" rows={8} value={hornText} onChange={(e) => setHornText(e.target.value)}/></label>
        <button type="button" onClick={() => { try { const config: unknown = JSON.parse(hornText); if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('Horn configuration must be an object.'); change({ ...draft, horn_config: config as Record<string, unknown> }); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } }}>Apply horn configuration</button>
      </details>}
      <h3>Front layout</h3><svg aria-label="Assembly front layout" viewBox={`${-dimensions.width_mm/2-8} ${-dimensions.height_mm/2-8} ${Math.max(1, dimensions.width_mm)+16} ${Math.max(1,dimensions.height_mm)+16}`} className="assembly-preview">
        <rect x={-dimensions.width_mm/2} y={-dimensions.height_mm/2} width={Math.max(0,dimensions.width_mm)} height={Math.max(0,dimensions.height_mm)} fill="none" stroke="currentColor" strokeWidth=".4"/>
        <circle cx={dimensions.horn_xy_mm[0]} cy={-dimensions.horn_xy_mm[1]} r={Math.max(0,dimensions.mouth_radius_mm)} fill="none" stroke="var(--accent, #77aaff)" strokeWidth=".5"/>
        {draft.woofer && <circle cx={dimensions.woofer_xy_mm[0]} cy={-dimensions.woofer_xy_mm[1]} r={Math.max(0,dimensions.aperture_radius_mm)} fill="none" stroke="currentColor" strokeWidth=".5"/>}
      </svg>
      <h3>Horn section</h3><p>Axial Z → · radial R ↑. Plug Z is measured from the horn source rim.</p><svg viewBox="0 0 620 240" className="source-meridian" aria-label="Phase plug passage section">
        <line x1="25" x2="575" y1="210" y2="210" className="source-axis"/>
        {valid && Object.entries(accepted!.value.horn_section_mm).map(([id, points]) => <path key={id} d={sectionPath(points)} className={id.startsWith('plug/') || id.startsWith('horn-wall') ? 'rigid' : 'moving'}/>)}
      </svg>
      <div role="status" className={`source-validation ${valid ? 'valid' : ''}`}>{validationError || (valid ? 'Valid assembly · passive bodies and source roles checked' : 'Validating assembly…')}</div>
      {valid && accepted!.value.passage_contract && <p>{accepted!.value.passage_contract.open_passage_count} open passages · minimum analytical clearance {Math.min(...Object.values(accepted!.value.passage_contract.clearances_mm)).toFixed(3)} mm.</p>}
      <h3>Phase plug and annular vanes<ExperimentalBadge/></h3><p>Rigid, full circular bodies with independently tapered radii. All bodies share inlet and outlet planes.</p>
      <div className="source-editor-toolbar"><button type="button" disabled={draft.phase_plugs.length >= 8 || draft.phase_plugs.some((p) => p.inner0_mm === 0)} onClick={() => {
        const first = draft.phase_plugs[0]; change({ ...draft, phase_plugs: [{ id: `core-${crypto.randomUUID()}`, z0_mm: first?.z0_mm ?? 2, z1_mm: first?.z1_mm ?? 6, inner0_mm: 0, inner1_mm: 0, outer0_mm: first ? first.inner0_mm / 2 : 1, outer1_mm: first ? first.inner1_mm / 2 : 1.4 }, ...draft.phase_plugs] });
      }}>Add central plug</button><button type="button" disabled={draft.phase_plugs.length >= 8} onClick={() => {
        const last = draft.phase_plugs.at(-1); const inner0 = last ? last.outer0_mm + 1 : 2; const inner1 = last ? last.outer1_mm + 1 : 2.4;
        change({ ...draft, phase_plugs: [...draft.phase_plugs, { id: `vane-${crypto.randomUUID()}`, z0_mm: last?.z0_mm ?? 2, z1_mm: last?.z1_mm ?? 6, inner0_mm: inner0, outer0_mm: inner0 + .5, inner1_mm: inner1, outer1_mm: inner1 + .5 }] });
      }}>Add annular vane</button></div>
      {!!draft.phase_plugs.length && <div className="source-fields">{(['z0_mm','z1_mm'] as const).map((field) => <NumberField key={field} label={field === 'z0_mm' ? 'Plug inlet Z' : 'Plug outlet Z'} value={draft.phase_plugs[0][field]} unit="mm" precision={6} step={.1} onCommit={(v) => change({ ...draft, phase_plugs: draft.phase_plugs.map((p) => ({ ...p, [field]: v })) })}/>)}</div>}
      {draft.phase_plugs.map((plug, i) => <section key={i}><label>Passive body ID<input aria-label={`Passive body ${i+1} ID`} value={plug.id} onChange={(e) => change({ ...draft, phase_plugs: draft.phase_plugs.map((p,j) => j === i ? { ...p, id:e.target.value } : p) })}/></label>
        <div className="source-fields">{(['inner0_mm','outer0_mm','inner1_mm','outer1_mm'] as const).map((field) => <NumberField key={field} label={`Body ${i+1} ${field.startsWith('inner') ? 'inner' : 'outer'} ${field.includes('0') ? 'inlet' : 'outlet'} radius`} value={plug[field]} unit="mm" precision={6} step={.1} onCommit={(v) => change({ ...draft, phase_plugs: draft.phase_plugs.map((p,j) => j === i ? { ...p, [field]: v } : p) })}/>)}</div><button type="button" onClick={() => change({ ...draft, phase_plugs: draft.phase_plugs.filter((_,j) => j !== i) })}>Remove body {i+1}</button></section>)}
    </div><aside><h3>Enclosure and placement</h3><div className="source-fields">
      {Object.entries(scalarLabels).filter(([field]) => (!draft.horn_config || !['horn_length_mm', 'mouth_radius_mm'].includes(field)) && (draft.woofer || field !== 'aperture_radius_mm')).map(([field,label]) => <NumberField key={field} label={label} value={dimensions[field as keyof typeof scalarLabels]} unit="mm" precision={6} step={.1} onCommit={(v) => change({ ...draft, dimensions: { ...dimensions, [field]: v } })}/>)}
      {(['horn_xy_mm','woofer_xy_mm'] as const).filter((field) => draft.woofer || field === 'horn_xy_mm').flatMap((field) => ([0,1] as const).map((axis) => <NumberField key={`${field}-${axis}`} label={`${field === 'horn_xy_mm' ? 'Horn' : 'Woofer'} ${axis ? 'Y' : 'X'}`} value={dimensions[field][axis]} unit="mm" precision={6} step={.1} onCommit={(v) => { const xy = [...dimensions[field]] as [number,number]; xy[axis] = v; change({ ...draft, dimensions: { ...dimensions, [field]: xy } }); }}/>))}
      <NumberField label="Enclosure mesh size" value={draft.mesh_size_mm} unit="mm" min={.001} precision={6} step={.5} onCommit={(v) => change({ ...draft, mesh_size_mm: v })}/>
      <label>Passage refinement<select aria-label="Passage refinement" value={draft.passage_refinement} onChange={(e) => change({ ...draft, passage_refinement:Number(e.target.value) as 1|2|4 })}><option value="1">Standard</option><option value="2">2× finer</option><option value="4">4× finer</option></select></label>
    </div>{draft.horn_config && <p>Resolved horn length: {dimensions.horn_length_mm.toFixed(3)} mm. Mouth radius: {dimensions.mouth_radius_mm.toFixed(3)} mm.</p>}
    <p>Passages refine locally to preserve clearance. Smaller enclosure mesh sizes and finer passages use more triangles.</p>
      <button type="button" disabled={!valid} onClick={() => void run(async () => { const record = await prepareNativeAssembly(() => sourceEditorApi.ingestAssembly(draft)); setNotice(`Prepared ${record.mesh?.stats.triangle_count ?? ''} triangles. The imported workspace now uses this assembly and its physical source channels.`); })}>Prepare assembly in WG</button>
      <button type="button" disabled={!valid} onClick={() => void run(async () => { download(await sourceEditorApi.exportAssembly(draft), 'native-assembly.zip'); setNotice('Exported shared STEP shell, preview mesh and source recipe.'); })}>Export assembly STEP + mesh</button>
      <button type="button" disabled={!valid} onClick={() => download(new Blob([JSON.stringify(draft,null,2)], { type:'application/json' }), 'source-assembly.json')}>Save assembly draft</button>
      <label>Load assembly draft<input aria-label="Load assembly draft" type="file" accept=".json,application/json" onChange={(e) => { const file = e.target.files?.[0]; if (!file) return; void run(async () => { if (file.size > 2*1024*1024) throw new Error('Assembly draft exceeds 2 MiB.'); const loaded = parseAssemblyDraft(await file.text()); setHornText(JSON.stringify(loaded.horn_config ?? {}, null, 2)); change(loaded); }); e.target.value=''; }}/></label>
      <p>Source editing includes drawing, arcs and the saved source-preset library. Assembly drafts also retain placement, passive bodies and density.</p>
    </aside></div></fieldset><footer>{busy && <p role="status">Preparing geometry…</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}<small>Draft changes are remembered. Preparing selects the immutable mesh and Metal, the supported native contour consumer.</small></footer>
  </div></div>;
}
