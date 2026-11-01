import { defaultWallThickness, useDesignStore, type DesignDocument } from '../stores/design';

/** The depth an enclosure comes back with: the latest positive one, whichever control set it. */
let lastEnclosureDepth = 280;
useDesignStore.subscribe(({ design }) => { if (design.enclosure.depth > 0) lastEnclosureDepth = design.enclosure.depth; });

/** The design writes that select a horn's outer body, shared by every control that offers it. */
export function outerBodyUpdates(design: DesignDocument, mode: 'bare' | 'freestanding' | 'enclosure',
  lastWallThickness: number | null, mounting: boolean): Record<string, number | string> {
  const updates = mode === 'bare'
    ? { 'mesh.wall_thickness': 0, 'enclosure.depth': 0 }
    : mode === 'freestanding'
      ? { 'mesh.wall_thickness': lastWallThickness ?? defaultWallThickness(), 'enclosure.depth': 0 }
      : { 'mesh.wall_thickness': 0, 'enclosure.depth': lastEnclosureDepth };
  // Write only what changes. An import without Mesh.Enclosure keeps `enclosure`
  // in _absent, and any write under it (even depth 0 over 0) would drop that
  // marker and send a full object, which the mesher refuses with a lip or Opus.
  // An unset wall is ATH's 5 mm (0 only while infinite baffle hides it), so a
  // bare or enclosed body always needs an explicit 0, and a free-standing
  // one keeps the unset wall. An unset enclosure stays unset at depth 0.
  const wallUnset = Boolean(design._absent?.includes('mesh.wall_thickness'));
  const enclosureUnset = Boolean(design._absent?.includes('enclosure'));
  const changed: Record<string, number | string> = {};
  const wall = updates['mesh.wall_thickness'];
  if (wallUnset ? wall === 0 : design.mesh.wall_thickness !== wall) changed['mesh.wall_thickness'] = wall;
  const depth = updates['enclosure.depth'];
  if (enclosureUnset ? depth !== 0 : design.enclosure.depth !== depth) changed['enclosure.depth'] = depth;
  if (mounting && design.simulation.sim_type === 'infinite-baffle') changed['simulation.sim_type'] = 'freestanding';
  return changed;
}
