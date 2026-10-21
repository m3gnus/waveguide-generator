import type { Command } from '../commands';
import type { Authoring } from '../controller';
import type { Reference } from '../adapter';
import type { Sketch } from '../model';
import { drawingIcons, type Tool } from '../SketchToolbar';
import type { ToolDescriptor } from '../workspace/types';
import { continueLine, lineState, placeLine, type LineState } from './line';
import { candidateDimension, dimensionSecondPick, dimensionPicks, type CandidateDimension } from './dimension';
import { projectToEntity } from '../geometry';
import { geometryCandidates, geometryHit } from '../geometryHit';
import { constraintToolEntries } from './constraints';
import { constraintTool, pickConstraint, escapeConstraint, type ConstraintToolState } from './constraintTool';

export const sketchToolHints: Record<Tool, string> = {
  select: 'Select or drag · Shift adds · Delete removes · Esc cancels',
  hand: 'Drag to pan · Esc: Select', constraint: '',
  line: 'Click to chain lines · type length, Tab angle · double-click ends · Ctrl/Cmd suppresses snaps',
  tangent: 'Choose a highlighted chain end, or click empty space to start a 3-point arc · A: 3-point arc',
  arc3: 'Click start, end, then a point on the arc · A: tangent arc',
  dimension: 'Click a line, arc or point; click a second item if needed; move to choose; click to place; type the value', split: 'Click inside a curve to split',
};

/** Thin descriptors over the existing pure Sketch tool reducers. */
export function sketchTools<M extends { references: Reference[] }>(): ToolDescriptor<Authoring<M>, Command>[] {
  type State = Authoring<M>;
  const drawing = (['line', 'tangent', 'arc3'] as const).map((mode): ToolDescriptor<State, Command> => ({
    id: mode, group: 'Sketch', label: mode === 'line' ? 'Line' : mode === 'tangent' ? 'Tangent arc' : '3-point arc',
    shortcut: mode === 'line' ? 'l' : 'a', icon: drawingIcons[mode === 'line' ? 'line' : 'arc'],
    hint: () => sketchToolHints[mode],
    pointer(event, state, local) {
      let current = local as { line: LineState; before?: Sketch } | undefined;
      if (current?.before && current.before !== state.sketch) current = { line: continueLine(current.line, current.before, state.sketch) };
      const line = current?.line ?? { ...lineState(), mode };
      if (event.phase !== 'down') return { local: current ?? { line } };
      const placed = placeLine(line, event.at, event.snap ?? null, state.sketch);
      return { local: { line: placed.state, ...(placed.command ? { before: state.sketch } : {}) }, commands: placed.command ? [placed.command] : [] };
    },
    key(event) { return { consumed: event.key === 'Escape', ...(event.key === 'Escape' ? { local: { line: { ...lineState(), mode } } } : {}) }; },
  }));
  const dimension: ToolDescriptor<State, Command> = {
    id: 'dimension', group: 'Sketch', label: 'Dimension', shortcut: 'd', icon: drawingIcons.dimension, hint: () => sketchToolHints.dimension,
    pointer(event, state, local) {
      const current = (local as { ids: string[]; placement?: CandidateDimension } | undefined) ?? { ids: [] };
      if (event.phase === 'up') return current.placement
        ? { local: { ids: [] }, commands: [{ kind: 'addConstraint', constraint: { ...current.placement, driving: !event.reference } }] }
        : { local: current };
      if (event.phase !== 'down') return { local: current };
      if (!current.ids.length) return { local: { ids: event.hit ? [event.hit] : [] } };
      const second = dimensionSecondPick(state.sketch, state.meta.references, current.ids, event.hit, event.at);
      return { local: second ? { ids: second } : { ...current, placement: candidateDimension(dimensionPicks(state.sketch, current.ids, state.meta.references), event.at) } };
    },
    key: event => ({ consumed: event.key === 'Escape', ...(event.key === 'Escape' ? { local: { ids: [] } } : {}) }),
  };

  const split: ToolDescriptor<State, Command> = {
    id: 'split', group: 'Sketch', label: 'Split', shortcut: 's', icon: drawingIcons.split, hint: () => sketchToolHints.split,
    pointer(event, state) {
      if (event.phase !== 'down') return {};
      const hit = geometryHit(geometryCandidates(state.sketch, event.at, [], state.meta.references).filter(h => h.kind !== 'point'), event.tolerance);
      return { commands: hit ? [{ kind: 'splitAt', entity: hit.id, ...projectToEntity(state.sketch, hit.id, event.at.x, event.at.y) }] : [] };
    },
  };
  const constraints = constraintToolEntries().map((entry): ToolDescriptor<State, Command> => ({
    id: `constraint:${entry.kind}`, group: 'Constraints', label: entry.label, shortcut: entry.key, icon: entry.symbol,
    hint: () => entry.label,
    pointer(event, state, local) {
      const current = (local as ConstraintToolState | undefined) ?? constraintTool(entry.kinds);
      if (event.phase !== 'down' || !event.hit) return { local: current };
      const picked = pickConstraint(state.sketch, current, event.hit);
      return { local: picked.state, commands: picked.constraint ? [{ kind: 'addConstraint', constraint: picked.constraint }] : [] };
    },
    key(event, _state, local) {
      return { consumed: event.key === 'Escape', ...(event.key === 'Escape' ? { local: escapeConstraint((local as ConstraintToolState | undefined) ?? constraintTool(entry.kinds)) } : {}) };
    },
  }));
  return [...drawing, dimension, split, ...constraints];
}
