/**
 * The design's integrated Sources and baffle assembly: identity-preserving edits.
 *
 * The wire shape is server/design/schema.py's AssemblyConfig (generated types).
 * Every operation is pure and returns a new assembly; the design store applies
 * one as one project history step, so Undo/Redo, New and Open need no local
 * stack. Radiators, patches and signal groups are named by stable UUIDs, never
 * labels: a recreated label never inherits a deleted woofer's driver.
 */
import type { components } from '../api/generated/openapi';
import { useDesignStore, type DesignDocument } from './design';

type Schemas = components['schemas'];
export type DesignAssembly = Schemas['AssemblyConfig'];
export type AssemblyWoofer = Schemas['AssemblyWoofer'];
export type AssemblyPlacement = Schemas['AssemblyPlacement'];
export type AssemblyWooferShape = Schemas['AssemblyWooferShape'];
export type AssemblyDriver = Schemas['AssemblyDriver'];
export type AssemblyMember = Schemas['AssemblyMember'];
export type AssemblySignalGroup = Schemas['AssemblySignalGroup'];
export type AssemblyAnchor = AssemblyPlacement['anchor'];

/** The design mesher's fixed identity of the horn's classic prescribed source. */
export const HF_RADIATOR_ID = 'horn-throat';
export const DEFAULT_WOOFER_RADIUS_MM = 80;
export const DEFAULT_WOOFER_GAP_MM = 10;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export function newRadiatorId(random: () => string = () => globalThis.crypto.randomUUID()): string {
  const id = random().toLowerCase();
  if (!UUID.test(id)) throw new Error('A radiator identity must be a canonical UUID.');
  return id;
}

/** A horn-only assembly: the classic HF source in its own signal group. */
export function emptyAssembly(newId: () => string = newRadiatorId): DesignAssembly {
  return {
    hf_source: { radiator_id: HF_RADIATOR_ID, kind: 'classic' },
    woofers: [],
    signal_groups: [{ id: newId(), label: 'HF', members: [{ radiator_id: HF_RADIATOR_ID }] }],
  };
}

const woofers = (assembly: DesignAssembly) => assembly.woofers ?? [];
const groups = (assembly: DesignAssembly) => assembly.signal_groups ?? [];

export function wooferById(assembly: DesignAssembly | undefined, id: string): AssemblyWoofer | undefined {
  return assembly ? woofers(assembly).find(woofer => woofer.id === id) : undefined;
}

function uniqueLabel(assembly: DesignAssembly, base: string): string {
  const used = new Set([...woofers(assembly).map(woofer => woofer.label), ...groups(assembly).map(group => group.label)]);
  for (let n = 1; ; n += 1) if (!used.has(`${base} ${n}`)) return `${base} ${n}`;
}

export interface NewWooferOptions {
  anchor: AssemblyAnchor;
  radiusMm?: number;
  gapMm?: number;
  shape?: AssemblyWooferShape;
  /** Free placement centre in the horn-throat frame. */
  xMm?: number;
  yMm?: number;
}

/** Add a woofer in its own new signal group. Returns the assembly and the new id. */
export function addWoofer(assembly: DesignAssembly, options: NewWooferOptions,
  newId: () => string = newRadiatorId): { assembly: DesignAssembly; id: string } {
  const next = structuredClone(assembly);
  const id = newId();
  const label = uniqueLabel(next, 'Woofer');
  const placement: AssemblyPlacement = options.anchor === 'free'
    ? { anchor: 'free', x_mm: options.xMm ?? 0, y_mm: options.yMm ?? 0 }
    : { anchor: options.anchor, gap_mm: options.gapMm ?? DEFAULT_WOOFER_GAP_MM };
  next.woofers = [...woofers(next), {
    id, label, enabled: true, radius_mm: options.radiusMm ?? DEFAULT_WOOFER_RADIUS_MM,
    shape: structuredClone(options.shape ?? { kind: 'piston' }), placement, drive: 'prescribed',
  }];
  next.signal_groups = [...groups(next), { id: newId(), label, members: [{ radiator_id: id }] }];
  return { assembly: next, id };
}

/** Disabled woofers keep every setting and leave geometry and solve. */
export function setWooferEnabled(assembly: DesignAssembly, id: string, enabled: boolean): DesignAssembly {
  return updateWoofer(assembly, id, { enabled });
}

export type WooferPatch = Partial<Pick<AssemblyWoofer, 'label' | 'enabled' | 'radius_mm' | 'shape' | 'resolution_mm' | 'drive'>>;

export function updateWoofer(assembly: DesignAssembly, id: string, patch: WooferPatch): DesignAssembly {
  if (!wooferById(assembly, id)) throw new Error('Unknown woofer.');
  const next = structuredClone(assembly);
  next.woofers = woofers(next).map(woofer => {
    if (woofer.id !== id) return woofer;
    const updated = { ...woofer, ...structuredClone(patch) };
    if ('resolution_mm' in patch && patch.resolution_mm == null) delete updated.resolution_mm;
    if (updated.shape.kind === 'ath_cone') delete updated.shape.recess_mm;
    return updated;
  });
  return next;
}

/** Replace a woofer's placement. A free placement drops any centring relation. */
export function setPlacement(assembly: DesignAssembly, id: string, placement: AssemblyPlacement): DesignAssembly {
  if (!wooferById(assembly, id)) throw new Error('Unknown woofer.');
  if (placement.spacing_to && (placement.spacing_to.radiator_id === id || !wooferById(assembly, placement.spacing_to.radiator_id))) {
    throw new Error('A spacing relation names another woofer of this assembly.');
  }
  const next = structuredClone(assembly);
  next.woofers = woofers(next).map(woofer => woofer.id === id ? { ...woofer, placement: structuredClone(placement) } : woofer);
  if (spacingCycle(next)) throw new Error('Spacing relations must not form a cycle.');
  return next;
}

function spacingCycle(assembly: DesignAssembly): boolean {
  const target = new Map(woofers(assembly).flatMap(woofer =>
    woofer.placement.spacing_to ? [[woofer.id, woofer.placement.spacing_to.radiator_id] as const] : []));
  for (const start of target.keys()) {
    const seen = new Set([start]);
    for (let cursor = target.get(start); cursor !== undefined; cursor = target.get(cursor)) {
      if (seen.has(cursor)) return true;
      seen.add(cursor);
    }
  }
  return false;
}

/**
 * Delete a woofer and everything bound to its identity: its driver, its group
 * membership (an emptied group goes too, and leaves the crossover), and other
 * woofers' spacing relations to it. Undo restores all of it in one step.
 */
export function deleteWoofer(assembly: DesignAssembly, id: string): DesignAssembly {
  if (!wooferById(assembly, id)) throw new Error('Unknown woofer.');
  const next = structuredClone(assembly);
  next.woofers = woofers(next).filter(woofer => woofer.id !== id).map(woofer => {
    if (woofer.placement.spacing_to?.radiator_id !== id) return woofer;
    const { spacing_to: _removed, ...placement } = woofer.placement;
    return { ...woofer, placement };
  });
  if (next.drivers) {
    delete next.drivers[id];
    if (!Object.keys(next.drivers).length) delete next.drivers;
  }
  const removed = new Set<string>();
  next.signal_groups = groups(next).flatMap(group => {
    const members = group.members.filter(member => member.radiator_id !== id);
    if (!members.length) removed.add(group.id);
    return members.length ? [{ ...group, members }] : [];
  });
  return removed.size ? withoutCrossoverMembers(next, removed) : next;
}

function withoutCrossoverMembers(assembly: DesignAssembly, removed: ReadonlySet<string>): DesignAssembly {
  const spec = assembly.crossover?.spec as { members?: string[]; reference?: string | null; channels?: Record<string, unknown> } | null | undefined;
  if (!assembly.crossover || !spec) return assembly;
  const members = (spec.members ?? []).filter(member => !removed.has(member));
  const channels = spec.channels ? Object.fromEntries(Object.entries(spec.channels).filter(([key]) => !removed.has(key))) : undefined;
  const reference = spec.reference && removed.has(spec.reference) ? undefined : spec.reference;
  const nextSpec = { ...spec, members, ...(channels ? { channels } : {}) };
  if (reference === undefined) delete nextSpec.reference; else nextSpec.reference = reference;
  // The combine wire needs two branches; fewer leaves a crossover nobody can solve.
  assembly.crossover = members.length >= 2 ? { ...assembly.crossover, spec: nextSpec } : { enabled: false };
  return assembly;
}

/** Bind (or clear) a T/S driver to a radiator identity. */
export function setDriver(assembly: DesignAssembly, id: string, driver: AssemblyDriver | null): DesignAssembly {
  if (!wooferById(assembly, id)) throw new Error('Drivers bind to a woofer of this assembly.');
  const next = structuredClone(assembly);
  const drivers = { ...(next.drivers ?? {}) };
  if (driver) drivers[id] = structuredClone(driver); else delete drivers[id];
  if (Object.keys(drivers).length) next.drivers = drivers; else delete next.drivers;
  return next;
}

export type MemberProcessing = Partial<Pick<AssemblyMember, 'weight' | 'delay_ms' | 'invert'>>;

/** Member weight, delay and polarity apply before the group's sum. */
export function setMemberProcessing(assembly: DesignAssembly, radiatorId: string, processing: MemberProcessing): DesignAssembly {
  const next = structuredClone(assembly);
  let found = false;
  next.signal_groups = groups(next).map(group => ({ ...group, members: group.members.map(member => {
    if (member.radiator_id !== radiatorId) return member;
    found = true;
    const updated: AssemblyMember = { ...member, ...processing };
    if (updated.weight === 1) delete updated.weight;
    if (!updated.delay_ms) delete updated.delay_ms;
    if (!updated.invert) delete updated.invert;
    return updated;
  }) }));
  if (!found) throw new Error('Unknown radiator.');
  return next;
}

/** Move a radiator into another existing group, or into a new group of its own. */
export function moveToGroup(assembly: DesignAssembly, radiatorId: string, groupId: string | null,
  newId: () => string = newRadiatorId): DesignAssembly {
  const source = groups(assembly).find(group => group.members.some(member => member.radiator_id === radiatorId));
  if (!source) throw new Error('Unknown radiator.');
  if (groupId === source.id) return structuredClone(assembly);
  if (groupId !== null && !groups(assembly).some(group => group.id === groupId)) throw new Error('Unknown signal group.');
  const next = structuredClone(assembly);
  const member = source.members.find(item => item.radiator_id === radiatorId)!;
  const removed = new Set<string>();
  let list = groups(next).flatMap(group => {
    if (group.id !== source.id) return [group];
    const members = group.members.filter(item => item.radiator_id !== radiatorId);
    if (!members.length) removed.add(group.id);
    return members.length ? [{ ...group, members }] : [];
  });
  if (groupId === null) {
    const label = radiatorId === HF_RADIATOR_ID ? 'HF' : wooferById(assembly, radiatorId)!.label;
    list = [...list, { id: newId(), label, members: [structuredClone(member)] }];
  } else {
    list = list.map(group => group.id === groupId ? { ...group, members: [...group.members, structuredClone(member)] } : group);
  }
  next.signal_groups = list;
  return removed.size ? withoutCrossoverMembers(next, removed) : next;
}

/** Apply one assembly edit as one project history step (or one frame of a drag). */
export function commitAssembly(next: DesignAssembly | null, mode: 'step' | 'drag' = 'step'): void {
  useDesignStore.getState().commitEmbedded({ assembly: next ?? undefined }, mode);
}

export function designAssembly(design: DesignDocument): DesignAssembly | undefined {
  return design.assembly ?? undefined;
}
