import type { PhasePlug, SourceAssemblyDocument, SourceAttachment } from '../../api/sourceEditor';
import type { ContextItem, SketchContext } from '../context';
import type { XY } from '../solver/types';

// phase_plug.py validate_passages, source_assembly.py __post_init__, front_baffle.py validate:
// every source/back/front/plug clearance must be strictly greater than this distance.
export const SOURCE_CLEARANCE_MM = 0.1;
// contour_artifact.py mouth/rim rule: choose a target strictly inside its 0.1 mm boundary.
export const SOURCE_LIMIT_INSET_MM = 1e-6;
export type SourceClearances = { plugZ0?: number; backZ?: number; frontZ?: number };
type Dimensions = SourceAssemblyDocument['dimensions'];
const segment = (id: string, role: ContextItem['role'], label: string, a: XY, b: XY, pickable = true): ContextItem => ({ id, role, label, kind: 'segment', geometry: [a, b], pickable });
const point = (id: string, role: ContextItem['role'], label: string, at: XY, owner: string): ContextItem => ({ id, role, label, kind: 'point', geometry: [at], owner, pickable: true });
function finish(items: ContextItem[], rimPlaneLabel: string): SketchContext {
  // A revision token, not an encoded copy of the live context in the authoring envelope.
  let hash = 0xcbf29ce484222325n;
  for (const c of JSON.stringify([rimPlaneLabel, items])) hash = BigInt.asUintN(64, (hash ^ BigInt(c.codePointAt(0)!)) * 0x100000001b3n);
  return { items, rimPlaneLabel, fingerprint: hash.toString(16).padStart(16, '0') };
}
function band(id: string, z: number, radius: number, above: boolean): ContextItem {
  const edge = z + (above ? SOURCE_CLEARANCE_MM : -SOURCE_CLEARANCE_MM);
  return { id, kind: 'polyline', role: 'keepout', label: 'Minimum clearance 0.1 mm', pickable: false,
    geometry: [{ x: 0, y: z }, { x: radius, y: z }, { x: radius, y: edge }, { x: 0, y: edge }, { x: 0, y: z }] };
}
function box(d: Dimensions, xy: [number, number], front: number, aperture: number): ContextItem[] {
  const [x, y] = xy;
  const distances = [{ d: d.width_mm / 2 - x, dir: '+x' }, { d: d.width_mm / 2 + x, dir: '−x' },
    { d: d.height_mm / 2 - y, dir: '+y' }, { d: d.height_mm / 2 + y, dir: '−y' }].sort((a, b) => a.d - b.d);
  const near = distances[0], far = distances.at(-1)!, back = front - d.depth_mm;
  return [segment('baffle.front', 'baffle', `Front baffle (z = ${front})`, { x: aperture, y: front }, { x: near.d, y: front }),
    segment('box.back', 'enclosure', 'Enclosure back', { x: 0, y: back }, { x: far.d, y: back }),
    ...([['near', near], ['far', far]] as const).map(([side, edge]) => ({ ...segment(`box.side.${side}`, 'enclosure', `Enclosure side (${side === 'near' ? 'nearest' : 'farthest'}, ${edge.dir}) ${edge.d.toFixed(1)} mm`, { x: edge.d, y: back }, { x: edge.d, y: front }, side === 'near'), emphasis: side })),
    band('keepout.back', back, far.d, true)];
}
export function hornAssemblyContext(dimensions: Dimensions, plugs: PhasePlug[], wallPoints: [number, number][] | undefined, throatRadius: number): SketchContext {
  const d = dimensions, L = d.horn_length_mm, mouth = { x: d.mouth_radius_mm, y: L };
  const wall = wallPoints?.map(([x, y]) => ({ x, y })) ?? [0, ...(plugs.length ? [plugs[0].z0_mm, plugs[0].z1_mm] : []), L].map(y => ({ x: throatRadius + (d.mouth_radius_mm - throatRadius) * y / L, y }));
  const rimPlaneLabel = 'Throat plane (z = 0)';
  const items: ContextItem[] = [segment('rim-plane', 'rim-plane', rimPlaneLabel, { x: 0, y: 0 }, { x: throatRadius, y: 0 }),
    point('horn.throat', 'throat', `Throat r ${throatRadius.toFixed(2)} 🔒`, { x: throatRadius, y: 0 }, 'rim-plane'),
    { id: 'horn.wall', role: 'wall', label: 'Horn wall', kind: 'polyline', geometry: wall, pickable: false },
    point('horn.mouth', 'mouth', `Mouth r ${mouth.x.toFixed(2)}`, mouth, 'horn.wall'), ...box(d, d.horn_xy_mm, L, mouth.x), band('keepout.front', L, mouth.x, false)];
  for (const p of plugs) {
    const prefix = `plug/${p.id}`, a = { x: p.inner0_mm, y: p.z0_mm }, b = { x: p.outer0_mm, y: p.z0_mm }, c = { x: p.outer1_mm, y: p.z1_mm }, e = { x: p.inner1_mm, y: p.z1_mm };
    items.push(segment(`${prefix}/inlet`, 'plug', `Plug ${p.id} inlet`, a, b), point(`${prefix}/inlet.inner`, 'plug', `Plug ${p.id} inner inlet`, a, `${prefix}/inlet`), point(`${prefix}/inlet.outer`, 'plug', `Plug ${p.id} outer inlet`, b, `${prefix}/inlet`),
      { id: `${prefix}/body`, kind: 'polyline', geometry: [b, c, e, a], role: 'plug', label: `Plug ${p.id}`, pickable: false }, band(`${prefix}/keepout`, p.z0_mm, p.outer0_mm, false));
  }
  return finish(items, rimPlaneLabel);
}
export function wooferAssemblyContext(dimensions: Dimensions, rimRadius = dimensions.aperture_radius_mm): SketchContext {
  const a = dimensions.aperture_radius_mm, rimPlaneLabel = 'Front baffle (z = 0)';
  return finish([segment('rim-plane', 'rim-plane', rimPlaneLabel, { x: 0, y: 0 }, { x: rimRadius, y: 0 }),
    ...box(dimensions, dimensions.woofer_xy_mm, 0, a), point('woofer.aperture', 'aperture', `Aperture r ${a.toFixed(2)}`, { x: a, y: 0 }, 'baffle.front'),
    segment('woofer.collar', 'collar', 'Mounting collar', { x: rimRadius, y: 0 }, { x: a, y: 0 }, false)], rimPlaneLabel);
}
export function standaloneContext(attachment: SourceAttachment | undefined, rimRadius: number): SketchContext {
  if (!attachment) return finish([], 'Rim plane (z = 0)');
  const d = attachment.dimensions;
  if (attachment.kind === 'baffle') {
    const center = d.center_mm as [number, number, number];
    return wooferAssemblyContext({ width_mm: Number(d.width_mm), height_mm: Number(d.height_mm), depth_mm: Number(d.depth_mm), front_z_mm: center[2], woofer_xy_mm: [center[0], center[1]], horn_xy_mm: [0, 0], horn_length_mm: 0, mouth_radius_mm: 0, aperture_radius_mm: Number(d.aperture_radius_mm) }, rimRadius);
  }
  const L = Number(d.length_mm), mouth = Number(d.mouth_radius_mm), housing = Number(d.housing_radius_mm), back = -Number(d.backing_depth_mm);
  return finish([segment('rim-plane', 'rim-plane', 'Throat plane (z = 0)', { x: 0, y: 0 }, { x: rimRadius, y: 0 }),
    segment('horn.wall', 'wall', 'Horn wall', { x: rimRadius, y: 0 }, { x: mouth, y: L }, false),
    point('horn.mouth', 'mouth', `Mouth r ${mouth.toFixed(2)}`, { x: mouth, y: L }, 'horn.wall'),
    segment('housing.front', 'enclosure', 'Housing front', { x: mouth, y: L }, { x: housing, y: L }),
    segment('box.side.near', 'enclosure', 'Housing side', { x: housing, y: L }, { x: housing, y: back }),
    segment('box.back', 'enclosure', 'Housing back', { x: housing, y: back }, { x: 0, y: back }), band('keepout.back', back, housing, true), band('keepout.front', L, mouth, false)], 'Throat plane (z = 0)');
}
/** Host values are derived only here; none enter the canonical source document. */
export function contextClearances(context: SketchContext): SourceClearances {
  return { backZ: context.items.find(i => i.id === 'box.back')?.geometry[0].y,
    frontZ: context.items.find(i => i.id === 'keepout.front')?.geometry[0].y,
    plugZ0: context.items.find(i => i.role === 'plug' && i.id.endsWith('/inlet'))?.geometry[0].y };
}
