import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { parseDecimalInput } from '../../design/decimalInput';
import type { Reference } from '../adapter';
import type { Command } from '../commands';
import type { SketchContext } from '../context';
import { curveIntersections, distance } from '../geometry';
import { geometryCandidates, geometryHit } from '../geometryHit';
import type { Entity, Sketch } from '../model';
import { applyCommand } from '../ops';
import type { XY } from '../solver/types';
import { filletPreview, trimPreview, extendPreview } from '../topology';
import { modifyContext } from '../tools/modifyContext';
import { worldToScreen, type View } from '../view';
import { displayPath } from '../workspace/WorkspaceItems';
import type { DisplayItem } from '../workspace/types';
import type { Tool } from './SketchTools';

export type ModifyTool = 'fillet' | 'trim' | 'extend';
export const isModifyTool = (tool: Tool): tool is ModifyTool => tool === 'fillet' || tool === 'trim' || tool === 'extend';
export const modifyHints = {
  fillet: 'Click a corner to round it', trim: 'Click the piece to remove · drag across pieces', extend: 'Click near an end to extend it',
};
export const modifyEntries = [
  { tool: 'fillet', label: 'Fillet (G)' }, { tool: 'trim', label: 'Trim (T)' }, { tool: 'extend', label: 'Extend (E)' },
] as const;
const item = (shape: Omit<Extract<DisplayItem, { kind: 'arc' }>, 'id' | 'role' | 'editable'> | { kind: 'polyline'; points: XY[] }): DisplayItem => ({ id: 'modify-preview', role: 'preview', editable: false, ...shape });
function trimmed(s: Sketch, id: string, at: XY, unbounded: string[]) {
  const preview = trimPreview(s, id, at, unbounded);
  return 'error' in preview ? preview : item(preview.kind === 'line' ? { kind: 'polyline', points: [preview.a, preview.b] } : preview);
}
interface Props {
  tool: Tool; sketch: Sketch; references: Reference[]; hidden: string[]; context?: SketchContext;
  view: View; size: { width: number; height: number }; pending: boolean;
  execute(commands: Command[]): Promise<unknown>; select(): void; focus(): void;
}
interface TrimGesture { before: Sketch; sketch: Sketch; last: XY; commands: Command[]; pieces: DisplayItem[]; origins: Map<string, string> }

/** Local prompts and gesture plans only; the session remains the sole edit/history owner. */
export function useModifyTools({ tool, sketch, references, hidden, context, view, size, pending, execute, select, focus }: Props) {
  const unbounded = references.filter(r => r.kind === 'axis' || r.kind === 'baffle' || r.kind === 'limit').map(r => r.id);
  const generation = useRef(0);
  const [hover, setHover] = useState<{ id?: string; at: XY }>();
  const [prompt, setPrompt] = useState<{ point: string; value: string; reason?: string }>();
  const lastRadius = useRef('2'), input = useRef<HTMLInputElement>(null), applying = useRef(false);
  const gesture = useRef<TrimGesture | undefined>(undefined);
  const [pieces, setPieces] = useState<DisplayItem[]>([]);
  const prepared = useMemo(() => tool === 'trim' || tool === 'extend' ? modifyContext(sketch, context) : sketch, [tool, sketch, context]);
  useLayoutEffect(() => { if (prompt) { input.current?.focus({ preventScroll: true }); input.current?.select(); } }, [prompt?.point]);
  function reset() { generation.current++; setHover(undefined); setPrompt(undefined); gesture.current = undefined; setPieces([]); }
  function choose(next: Tool, selection: string[]) {
    reset();
    if (next === 'fillet' && selection.length === 1 && sketch.points.some(p => p.id === selection[0]) && geometryCandidates(sketch, sketch.points.find(p => p.id === selection[0])!, hidden, references).some(h => h.kind === 'point' && h.id === selection[0])) {
      setPrompt({ point: selection[0], value: lastRadius.current });
    }
  }
  const editable = (e: Entity) => !e.reference && !references.some(r => r.id === e.id);
  function hit(at: XY, mode: ModifyTool, s = sketch) {
    return geometryHit(geometryCandidates(s, at, hidden, references, editable).filter(h => mode === 'fillet' ? h.kind === 'point' : h.kind !== 'point'), 10 / view.scale)?.id;
  }
  function addTrim(active: TrimGesture, id: string, at: XY) {
    const piece = trimmed(active.sketch, id, at, unbounded);
    if ('error' in piece) return;
    const command: Command = { kind: 'trim', entity: id, ...at, unbounded, ...(context ? { context } : {}) };
    const result = applyCommand(active.sketch, command);
    if ('error' in result) return;
    const origin = active.origins.get(id) ?? id;
    for (const entity of result.sketch.entities) if (!entity.reference && !active.origins.has(entity.id)) active.origins.set(entity.id, origin);
    active.sketch = result.sketch; active.commands.push(command); active.pieces.push(piece);
    setPieces([...active.pieces]);
  }
  function sweep(at: XY) {
    const active = gesture.current;
    if (!active || distance(active.last, at) < 1e-9) return;
    // Reuse exact line/arc intersections, including crossings between sparse pointer events.
    const a = { ...active.last, id: '__modify_from' }, b = { ...at, id: '__modify_to' };
    const probe = { kind: 'line' as const, id: '__modify_path', p1: a.id, p2: b.id };
    const crossingSketch = { ...active.sketch, points: [...active.sketch.points, a, b] };
    const hits = active.sketch.entities.filter(e => editable(e) && (e.kind === 'line' || e.kind === 'arc')).flatMap(e =>
      e.kind === 'line' || e.kind === 'arc' ? curveIntersections(crossingSketch, e, probe).map(at => ({ origin: active.origins.get(e.id), at })) : []
    ).sort((a, b) => distance(active.last, a.at) - distance(active.last, b.at));
    for (const crossing of hits) {
      // A previous crossing can split/reidentify this curve: pick against the evolving plan.
      const id = geometryHit(geometryCandidates(active.sketch, crossing.at, hidden, references, e => editable(e) && active.origins.get(e.id) === crossing.origin)
        .filter(candidate => candidate.kind !== 'point'), 1e-7)?.id;
      if (id) addTrim(active, id, crossing.at);
    }
    active.last = at;
  }
  function move(at: XY, buttons: number) {
    if (!isModifyTool(tool)) return false;
    if (gesture.current && (buttons & 1)) sweep(at);
    setHover({ id: hit(at, tool), at }); return true;
  }
  function down(at: XY) {
    if (!isModifyTool(tool)) return false;
    if (pending || applying.current || prompt) return true;
    const id = hit(at, tool); setHover({ id, at });
    if (tool === 'fillet') { if (id) setPrompt({ point: id, value: lastRadius.current }); }
    else if (tool === 'trim') {
      const active: TrimGesture = { before: sketch, sketch: prepared, last: at, commands: [], pieces: [], origins: new Map(prepared.entities.filter(editable).map(e => [e.id, e.id])) };
      gesture.current = active; if (id) addTrim(active, id, at);
    } else if (id) {
      const preview = extendPreview(prepared, id, at, unbounded);
      if (!('error' in preview)) { setHover(undefined); void execute([{ kind: 'extend', entity: id, ...at, unbounded, ...(context ? { context } : {}) }]).catch(() => {}); }
    }
    return true;
  }
  function up(cancel: boolean, at?: XY) {
    const active = gesture.current;
    if (!active) return;
    if (!cancel && at) sweep(at);
    gesture.current = undefined; setPieces([]); setHover(undefined);
    if (!cancel && active.before === sketch && active.commands.length) void execute(active.commands).catch(() => {});
  }
  const parsed = prompt && parseDecimalInput(prompt.value);
  const radius = parsed?.kind === 'number' ? parsed.value : NaN;
  const fillet = prompt ? filletPreview(sketch, prompt.point, radius) : undefined;
  let preview: DisplayItem | undefined, reason: string | undefined;
  if (tool === 'fillet' && fillet) {
    if ('error' in fillet) reason = fillet.error;
    else preview = item({ kind: 'arc', ...fillet });
  } else if ((tool === 'trim' || tool === 'extend') && hover?.id) {
    const s = gesture.current?.sketch ?? prepared;
    const result = tool === 'trim' ? trimmed(s, hover.id, hover.at, unbounded) : extendPreview(s, hover.id, hover.at, unbounded);
    if ('error' in result) reason = result.error;
    else if ('from' in result) preview = item(result.kind === 'line' ? { kind: 'polyline', points: [result.from, result.to] } : { kind: 'arc', center: result.center, start: result.from, end: result.to, ccw: result.ccw });
    else preview = result;
  } else if (tool === 'extend' && hover) reason = 'No editable curve nearby. Move near an end of an authored line or arc.';
  const corner = sketch.points.find(p => p.id === (prompt?.point ?? (tool === 'fillet' ? hover?.id : undefined)));
  const at = corner && worldToScreen(view, corner);
  const cancel = () => { reset(); select(); focus(); };
  const overlays = prompt && <form aria-label="Fillet radius" className="sketch-inline sketch-fillet-prompt" data-workspace-own-keys
    style={{ left: Math.max(4, Math.min((at?.x ?? 0) + 12, size.width - 220)), top: Math.max(4, Math.min((at?.y ?? 0) + 12, size.height - 150)) }}
    onKeyDown={event => { event.stopPropagation(); if (event.key === 'Enter' && event.target === input.current) { event.preventDefault(); event.currentTarget.requestSubmit(); } if (event.key === 'Escape') { event.preventDefault(); cancel(); } }}
    onSubmit={event => {
      event.preventDefault(); if (reason || pending || applying.current) return;
      applying.current = true; const captured = generation.current;
      void execute([{ kind: 'fillet', point: prompt.point, radius }]).then(() => {
        if (captured !== generation.current) return;
        lastRadius.current = prompt.value; setPrompt(undefined); setHover(undefined); focus();
      }).catch(error => { if (captured === generation.current) setPrompt({ ...prompt, reason: error instanceof Error ? error.message : String(error) }); }).finally(() => { applying.current = false; });
    }}>
    <label>Radius (mm)<input ref={input} aria-label="Fillet radius" value={prompt.value} onChange={event => setPrompt({ ...prompt, value: event.target.value })}/></label>
    {(reason || prompt.reason) && <span role="alert">{reason || prompt.reason}</span>}
    <div><button className="btn" type="submit" disabled={!!reason || pending}>Apply</button><button className="btn" type="button" onClick={cancel}>Cancel</button></div>
  </form>;
  const graphics = <g className="sketch-modify-graphics" pointerEvents="none">
    {at && <circle aria-label="Fillet corner" className="sketch-modify-corner" cx={at.x} cy={at.y} r={8}/>}
    {preview && <path aria-label={`${tool} preview`} className={`sketch-preview${tool === 'trim' ? ' sketch-trim-preview' : ''}`} d={displayPath(preview, view)}/>}
    {pieces.map((piece, i) => <path key={i} className="sketch-preview sketch-trim-preview" d={displayPath(piece, view)}/>)}
  </g>;
  return { choose, reset, down, move, up, leave: () => setHover(undefined), overlays, graphics, reason, activeGesture: () => !!gesture.current };
}
