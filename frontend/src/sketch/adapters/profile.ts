import type { CurvesResponse, InsertMode, PlaneCurve } from '../../api/freeformProfile';
import type { ProfileSamples } from '../../api/profileSamples';
import {
  activePlanes, deleteAnchor, editLookupAnchor, insertAnchorBuilder, isSharedProfile, setAnchorAngle, setAnchorJoin, setAnchorValue,
  type EditBuilder, type Plane, type ProfileEditResult,
} from '../../design/customProfileEdit';
import { useDesignStore, type DesignDocument, type FreeformPoint } from '../../stores/design';
import type { DomainAdapter, DisplayItem, DisplayUI, PropertiesSection } from '../workspace/types';
import type { ProjectBinding } from '../workspace/projectHistoryAuthority';
import { gridStep, worldToScreen } from '../view';
import { distance as pointDistance, projectToSegment } from '../geometry';
import type { XY } from '../solver/types';

export type ProfileCommand =
  | { kind: 'move'; plane: Plane; index: number; at: XY }
  | { kind: 'delete'; plane: Plane; index: number }
  | { kind: 'join'; plane: Plane; index: number; join: 'corner' | 'smooth' }
  | { kind: 'angle'; plane: Plane; index: number; side: 'in' | 'out'; value?: number }
  | { kind: 'insert'; plane: Plane; z: number; mode: InsertMode };
export type ProfilePlaneChoice = 'both' | Plane;
export interface ProfilePresentation {
  curves?: CurvesResponse;
  stale?: boolean;
  error?: string;
  generated?: ProfileSamples;
  /** Scale belonging to the export, including while it is last-valid data. */
  generatedScale?: number;
  generatedStale?: boolean;
  showGenerated?: boolean;
  plane?: ProfilePlaneChoice;
  scaled?: boolean;
  before?: CurvesResponse;
  properties?(state: DesignDocument, selection: string[]): PropertiesSection[];
}
export const anchorId = (plane: Plane, index: number) => `${plane}:anchor:${index}`;
export function parseAnchor(id: string) {
  const match = /^([HV]):anchor:(\d+)$/.exec(id);
  return match ? { plane: match[1] as Plane, index: Number(match[2]) } : undefined;
}
export const profilePoints = (design: DesignDocument, plane: Plane): FreeformPoint[] => design.formula === 'LOOKUP'
  ? (design.lookup_profile ?? []).map(([z, r]) => ({ t: z / (design.length ?? 120), r, join: 'corner' as const }))
  : (plane === 'H' ? design.profile_h : design.profile_v)!.points;
const sharedCanvas = (design: DesignDocument) => design.formula === 'LOOKUP' || isSharedProfile(design);
export function equalProfiles(samples: ProfileSamples): boolean {
  return samples.H.length === samples.V.length && samples.H.every((p, i) =>
    Math.abs(p.z - samples.V[i].z) < 1e-5 && Math.abs(p.r - samples.V[i].r) < 1e-5);
}

/** All edit semantics stay in the table's owner. No curve evaluator lives here. */
export function profileEdit(state: DesignDocument, command: Exclude<ProfileCommand, { kind: 'insert' }>, curves?: CurvesResponse): ProfileEditResult {
  const { plane, index } = command;
  if (state.formula === 'LOOKUP') {
    if (command.kind === 'join' || command.kind === 'angle') throw new Error('Legacy LOOKUP has corners only; join and angle tools are disabled.');
    return editLookupAnchor(state, command.kind === 'delete' ? { kind: 'delete', index }
      : { kind: 'move', index, z: index === 0 ? undefined : command.at.x, r: command.at.y }, true);
  }
  switch (command.kind) {
    case 'delete': return deleteAnchor(state, plane, index);
    case 'join': return setAnchorJoin(state, plane, index, command.join, curves);
    case 'angle': return setAnchorAngle(state, plane, index, command.side, command.value);
    case 'move': {
      // Endpoints keep their shared z. The owner alone checks monotonicity and r > 0.
      const points = profilePoints(state, plane);
      const moved = index > 0 && index < points.length - 1 && command.at.x !== points[index].t * (state.length ?? 120)
        ? setAnchorValue(state, plane, index, 'z', command.at.x) : { after: state, notes: [] };
      const radius = command.at.y !== points[index].r
        ? setAnchorValue(moved.after, plane, index, 'r', command.at.y) : { after: moved.after, notes: [] };
      return { after: radius.after, notes: [...moved.notes, ...radius.notes] };
    }
  }
}
export function profileCommandBuilder(commands: ProfileCommand[], curves?: CurvesResponse): EditBuilder {
  return async (before, context) => {
    let after = before;
    const notes: string[] = [];
    for (const command of commands) {
      const result = command.kind === 'insert'
        ? await insertAnchorBuilder(command.plane, command.z, command.mode)(after, context)
        : profileEdit(after, command, curves);
      after = result.after; notes.push(...result.notes);
    }
    return { after, notes: [...new Set(notes)] };
  };
}

const profileFields = ['formula', 'length', 'scale', 'profile_h', 'profile_v', 'shared_profile', '_expressions', '_absent'] as const;
const lookupFields = ['formula', 'length', 'scale', 'lookup_profile', '_expressions', '_absent'] as const;
/** Keep the editor's bound fields, rebasing everything else onto the live document. */
export function rebaseProfileDocument(state: DesignDocument, document: DesignDocument): DesignDocument {
  const fields = state.formula === 'LOOKUP' ? lookupFields : profileFields;
  return { ...document, ...Object.fromEntries(fields.map(field => [field, state[field]])) };
}

/** Native, sidecar-free profile binding. Drag writes only changed coordinate leaves,
 * preserving all untouched expressions and omissions exactly as setAnchorValue does. */
export function profileBinding(onMissing: () => void, mode: 'freeform' | 'lookup' = 'freeform'): ProjectBinding<DesignDocument> {
  const coordinates = (['h', 'v'] as const).flatMap(plane => Array.from({ length: 64 }, (_, i) =>
    ['t', 'r'].map(field => `profile_${plane}.points.${i}.${field}`)).flat());
  return {
    get paths() { return mode === 'lookup'
      ? [...lookupFields, ...(useDesignStore.getState().design.lookup_profile ?? []).flatMap((_point, index) => [0, 1].map(axis => `lookup_profile.${index}.${axis}`))]
      : [...profileFields, ...coordinates]; },
    read: () => {
      const design = useDesignStore.getState().design;
      return mode === 'lookup' ? design.formula === 'LOOKUP' && design.lookup_profile ? design : undefined
        : design.formula === 'FREEFORM' && design.profile_h && design.profile_v ? design : undefined;
    },
    write: (after, values) => {
      const updates: Record<string, unknown> = {};
      if (mode === 'lookup') {
        after.lookup_profile!.forEach((pair, index) => pair.forEach((value, axis) => {
          const path = `lookup_profile.${index}.${axis}`;
          if (values[path] !== value) updates[path] = value;
        }));
        return updates;
      }
      for (const plane of ['H', 'V'] as const) profilePoints(after, plane).forEach((point, index) => {
        for (const field of ['t', 'r'] as const) {
          const path = `profile_${plane.toLowerCase()}.points.${index}.${field}`;
          if (values[path] !== point[field]) updates[path] = point[field];
        }
      });
      return updates;
    }, onMissing,
  };
}

function distance(at: XY, points: XY[]) {
  let gap = Infinity;
  for (let i = 1; i < points.length; i++) {
    gap = Math.min(gap, pointDistance(at, projectToSegment(at, points[i - 1], points[i])));
  }
  return gap;
}
const textItem = (id: string, text: string, at: XY, role = 'profile-label'): DisplayItem => ({
  id, kind: 'dimension', role, editable: false, text, driving: false,
  layout: { path: '', arrows: '', at, rotation: 0, placement: { offset: 0, along: .5 } },
});

export function createProfileAdapter(presentation: () => ProfilePresentation = () => ({}), mode: 'freeform' | 'lookup' = 'freeform'): DomainAdapter<DesignDocument, DesignDocument, ProfileCommand> {
  const factor = (state: DesignDocument) => presentation().scaled ? state.scale : 1;
  const planes = (state: DesignDocument): Plane[] => state.formula === 'LOOKUP' ? ['H'] : activePlanes(state).filter(plane => isSharedProfile(state) || !presentation().plane || presentation().plane === 'both' || presentation().plane === plane);
  const adapter: DomainAdapter<DesignDocument, DesignDocument, ProfileCommand> = {
    id: 'custom-profile', axes: { x: { label: 'z', unit: 'mm' }, y: { label: 'r', unit: 'mm', clamp: { min: 0 } } },
    labels: { drawing: 'Custom profile drawing', tools: 'Profile tools' }, viewControls: { fitAll: false, legend: false },
    seed: document => document, lower: state => ({ ok: true, document: state }),
    status: state => (state.formula === 'LOOKUP' ? (state.lookup_profile?.length ?? 0) < 2 : (state.profile_h?.points.length ?? 0) < 2 || (state.profile_v?.points.length ?? 0) < 2)
      ? { kind: 'unfinished', hint: 'Add a throat and mouth in the profile table.' }
      : presentation().error ? { kind: 'invalid', problems: [{ code: 'profile-curve', ids: ['H:curve', 'V:curve'], message: presentation().error! }] } : { kind: 'valid' },
    serializeAuthoring: () => undefined,
    apply: (state, command) => {
      try {
        if (command.kind === 'insert') return state.formula === 'LOOKUP' ? editLookupAnchor(state, { kind: 'insert', z: command.z }).after : { error: 'Insertion needs a fresh mesher curve and the server edit preview.' };
        return profileEdit(state, command, presentation().curves).after;
      } catch (reason) { return { error: reason instanceof Error ? reason.message : String(reason) }; }
    },
    remove: (_state, selection) => selection.flatMap(id => { const anchor = parseAnchor(id); return anchor ? [{ kind: 'delete' as const, ...anchor }] : []; }).sort((a, b) => b.index - a.index),
    display: (state, ui) => {
      const p = presentation(), scale = factor(state), items: DisplayItem[] = [];
      const point = (z: number, r: number) => ({ x: z * scale, y: r * scale });
      const generated = p.generated;
      if (generated && p.showGenerated !== false) {
        const choices: Plane[] = !p.plane || p.plane === 'both' ? equalProfiles(generated) ? ['H'] : ['H', 'V'] : [p.plane];
        for (const plane of choices) items.push({ id: `${plane}:generated`, kind: 'polyline', role: `profile-generated profile-${plane}`, editable: false,
          stale: p.generatedStale, label: `Generated ${equalProfiles(generated) && (!p.plane || p.plane === 'both') ? 'H = V' : plane}`,
          points: generated[plane].map(({ z, r }) => ({ x: z / (p.scaled ? 1 : p.generatedScale ?? state.scale), y: r / (p.scaled ? 1 : p.generatedScale ?? state.scale) })) });
      }
      for (const plane of planes(state)) {
        const curve = state.formula === 'LOOKUP' ? lookupCurve(state) : p.curves?.[plane], before = p.before?.[plane];
        if (before) items.push({ id: `${plane}:before`, kind: 'polyline', role: `profile-${plane}`, editable: false, state: ['ghost'], label: `Previous ${plane} curve`, points: before.samples.map(([z, r]) => point(z, r)) });
        if (curve) {
          items.push({ id: `${plane}:curve`, kind: 'polyline', role: `profile-authored profile-${plane}`, editable: true, stale: p.stale,
            label: `Authored ${sharedCanvas(state) ? 'H = V' : plane}`, points: curve.samples.map(([z, r]) => point(z, r)) });
          curve.spans.forEach((span, index) => {
            const left = profilePoints(state, plane)[index], right = profilePoints(state, plane)[index + 1];
            if (!left || !right) return;
            const angleSet = (left.angle_out_deg ?? left.angle_deg) != null || right.angle_deg != null;
            const label = span.straight ? 'straight' : angleSet ? 'curved, angle set' : undefined;
            if (label && ui.view && ui.dimensions !== false) {
              const samples = curve.samples.filter(([z]) => z >= curve.anchors[index].z && z <= curve.anchors[index + 1].z);
              const sample = samples[Math.floor(samples.length / 2)];
              if (sample) { const at = worldToScreen(ui.view, point(...sample)); items.push(textItem(`${plane}:span:${index}`, `${plane} · ${label}`, { x: at.x, y: at.y - 10 })); }
            }
            // A server refusal names a span. Keep its actual sampled geometry highlighted.
            const refusedPlane = /Vertical spline points|profileV/i.test(p.error ?? '') ? 'V' : /Horizontal spline points|profileH/i.test(p.error ?? '') ? 'H' : undefined;
            if (p.error && (!refusedPlane || refusedPlane === plane) && new RegExp(`span\\s+${index + 1}(?:\\D|$)`, 'i').test(p.error)) items.push({
              id: `${plane}:span-problem:${index}`, kind: 'polyline', role: 'profile-refusal', editable: false, state: ['problem'], label: p.error,
              points: curve.samples.filter(([z]) => z >= curve.anchors[index].z && z <= curve.anchors[index + 1].z).map(([z, r]) => point(z, r)),
            });
          });
        }
        profilePoints(state, plane).forEach((anchor, index, anchors) => {
          const id = anchorId(plane, index), selected = ui.selection.includes(id), at = point(anchor.t * (state.length ?? 120), anchor.r);
          const resolved = curve?.anchors[index];
          if (selected && resolved && !p.stale) for (const side of ['In', 'Out'] as const) {
            const handle = resolved[`handle${side}`];
            if (handle) items.push({ id: `${id}:handle${side}`, kind: 'handle', role: `profile-${plane}`, editable: false, owner: id,
              anchor: at, at: { x: at.x + handle[0] * scale, y: at.y + handle[1] * scale } });
          }
          items.push({ id, kind: 'point', role: `profile-${plane}`, editable: true, at,
            glyph: anchor.join === 'corner' ? 'corner' : 'smooth', state: [selected ? 'selected' : '', ui.hover === id ? 'hovered' : ''],
            label: `${plane} ${index === 0 ? 'throat' : index === anchors.length - 1 ? 'mouth' : `point ${index}`} (${anchor.join ?? 'smooth'})` });
        });
      }
      const geometry = items.flatMap(item => item.kind === 'polyline' ? item.points : item.kind === 'point' ? [item.at] : []);
      const bounds = { minX: Math.min(0, ...geometry.map(p => p.x)), maxX: Math.max(1, ...geometry.map(p => p.x)), minY: 0, maxY: Math.max(1, ...geometry.map(p => p.y)) };
      const context = [{ id: 'profile-axis-z', kind: 'segment' as const, role: 'axis', pickable: false, label: `z (${p.scaled ? 'scaled ' : ''}mm)`, geometry: [{ x: bounds.minX, y: 0 }, { x: bounds.maxX, y: 0 }] },
        { id: 'profile-axis-r', kind: 'segment' as const, role: 'axis', pickable: false, label: `r (${p.scaled ? 'scaled ' : ''}mm)`, geometry: [{ x: 0, y: 0 }, { x: 0, y: bounds.maxY }] }];
      const h = profilePoints(state, 'H');
      const references = [{ z: (h[0]?.t ?? 0) * (state.length ?? 120), label: 'Throat', role: 'throat' },
        { z: (h.at(-1)?.t ?? 1) * (state.length ?? 120), label: 'Mouth', role: 'mouth' },
        ...(state.cross_sections ?? []).slice(1, -1).map((station, index) => ({ z: station.t * (state.length ?? 120), label: `Station ${index + 2}`, role: 'station' }))];
      references.forEach((reference, index) => context.push({ id: `profile-reference:${index}`, kind: 'segment', role: reference.role, pickable: false,
        label: `${reference.label} · z = ${Number((reference.z * scale).toPrecision(6))} ${p.scaled ? 'scaled ' : ''}mm`,
        geometry: [{ x: reference.z * scale, y: 0 }, { x: reference.z * scale, y: bounds.maxY }] }));
      addAxisLabels(items, ui, p.scaled ?? false);
      return { items, context, bounds: () => bounds, fitPadding: { bottom: 20, top: 10, right: 0, offsetX: 0, margin: 32 } };
    },
    hits: (state, at, tolerance, ui) => adapter.display(state, { ...ui, scale: 1 }).items.flatMap(item => {
      if (!item.editable) return [];
      const gap = item.kind === 'point' ? Math.hypot(item.at.x - at.x, item.at.y - at.y) : item.kind === 'polyline' ? distance(at, item.points) : Infinity;
      return gap <= tolerance ? [{ id: item.id, kind: item.kind === 'point' ? 'point' as const : 'curve' as const, role: item.role, gap }] : [];
    }),
    marquee: (state, box) => adapter.display(state, { selection: [], scale: 1 }).items.flatMap(item => item.kind === 'point' && item.at.x >= box.minX && item.at.x <= box.maxX && item.at.y >= box.minY && item.at.y <= box.maxY ? [item.id] : []),
    dragSet: (state, _selection, hit) => {
      const anchor = parseAnchor(hit);
      return anchor && planes(state).includes(anchor.plane) ? { ids: [hit], move: (_state, _delta, at) => [{ kind: 'move', ...anchor, at: { x: at.x / factor(state), y: at.y / factor(state) } }] } : undefined;
    },
    tools: (mode === 'lookup' ? ['exact' as const] : ['exact', 'refit'] as const).map(insertMode => ({
      id: `insert-${insertMode}`, group: 'Profile', label: insertMode === 'exact' ? 'Insert exact' : 'Insert refit', icon: 'M2 8H14M8 2V14',
      hint: () => `Click the authored curve to insert a point (${insertMode}).`,
      pointer: (event, state) => {
        if (event.phase !== 'down' || !event.hit?.endsWith(':curve')) return {};
        if (mode !== 'lookup' && (presentation().stale || !presentation().curves)) return { commands: [{ kind: 'insert', plane: event.hit[0] as Plane, z: NaN, mode: insertMode }], done: true };
        return { commands: [{ kind: 'insert', plane: event.hit[0] as Plane, z: event.at.x / factor(state), mode: insertMode }], done: true };
      },
    })),
    properties: (state, selection) => presentation().properties?.(state, selection) ?? [],
    statusLine: (state, cursor, message) => `z ${cursor.x.toFixed(2)}, r ${cursor.y.toFixed(2)} ${presentation().scaled ? 'scaled ' : 'design '}mm · ${message || (presentation().stale ? 'Updating authored curve…' : `Drag a point · ${sharedCanvas(state) ? 'H = V' : 'H and V'} · Delete removes · Esc cancels`)}`,
  };
  return adapter;
}

function lookupCurve(state: DesignDocument): PlaneCurve {
  const points = state.lookup_profile ?? [];
  return { samples: points, inflections: [], spans: points.slice(1).map(() => ({ straight: true, bezier: [] })),
    anchors: points.map(([z, r]) => ({ z, r, t: z / (state.length ?? 120), join: 'corner', handleIn: null, handleOut: null, explicitIn: false, explicitOut: false, angleInDeg: null, angleOutDeg: null })) };
}

function addAxisLabels(items: DisplayItem[], ui: DisplayUI, scaled: boolean) {
  if (!ui.view || !ui.viewport) return;
  const { view, viewport } = ui, step = gridStep(view.scale) * 5;
  const minX = -view.offsetX / view.scale, maxX = (viewport.width - view.offsetX) / view.scale;
  const minY = (view.offsetY - viewport.height) / view.scale, maxY = view.offsetY / view.scale;
  const xY = Math.max(16, Math.min(viewport.height - 22, view.offsetY));
  const yX = Math.max(32, Math.min(viewport.width - 28, view.offsetX));
  for (let x = Math.ceil(minX / step) * step; x <= maxX; x += step) items.push(textItem(`tick-z:${x}`, String(Number(x.toPrecision(6))), { x: worldToScreen(view, { x, y: 0 }).x, y: xY + 14 }, 'profile-axis-label'));
  for (let y = Math.max(0, Math.ceil(minY / step) * step); y <= maxY; y += step) items.push(textItem(`tick-r:${y}`, String(Number(y.toPrecision(6))), { x: yX - 18, y: worldToScreen(view, { x: 0, y }).y + 3 }, 'profile-axis-label'));
  items.push(textItem('axis-z-label', `z (${scaled ? 'scaled ' : ''}mm)`, { x: viewport.width - 46, y: viewport.height - 8 }, 'profile-axis-label'));
  items.push(textItem('axis-r-label', `r (${scaled ? 'scaled ' : ''}mm)`, { x: 48, y: 16 }, 'profile-axis-label'));
}
