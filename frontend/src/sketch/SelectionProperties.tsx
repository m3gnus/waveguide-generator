import type { Command } from './commands';
import type { Reference } from './adapter';
import type { Sketch } from './model';

interface Props {
  sketch: Sketch; references: Reference[]; selection: string[];
  enabled: boolean; pending: boolean; showDriving: boolean;
  command(commands: Command | Command[]): Promise<unknown>;
  removeSelection(): void;
}
/** Selection actions shared by the legacy canvas and the adapter properties slot. */
export function SelectionProperties({ sketch, references, selection, enabled, pending, showDriving, command, removeSelection }: Props) {
  const convertible = selection.length === 1 ? sketch.entities.find(e => e.id === selection[0] && !e.reference
    && !references.some(r => r.id === e.id) && (e.kind === 'line' || e.kind === 'arc')) : undefined;
  const selectedDimension = selection.length === 1 ? sketch.constraints.find(c => c.id === selection[0] && 'value' in c) : undefined;
  return <>
      {showDriving && selectedDimension && 'driving' in selectedDimension && <button type="button" disabled={!enabled || pending} onClick={() => void command({ kind: 'setDriving', id: selectedDimension.id, driving: !selectedDimension.driving })}>{selectedDimension.driving ? 'Make reference' : 'Make driving'}</button>}
      {sketch.entities.some(e => selection.includes(e.id) && !e.reference && !references.some(r => r.id === e.id)) && <button type="button" disabled={!enabled} onClick={() => void command(sketch.entities.filter(e => selection.includes(e.id) && !e.reference).map(e => ({ kind: 'setConstruction', id: e.id, construction: !e.construction })))}>Construction (X)</button>}
      {convertible && <button type="button" disabled={!enabled} onClick={() => void command({ kind: 'convertEntity', id: convertible.id, to: convertible.kind === 'line' ? 'arc' : 'line' })}>{convertible.kind === 'line' ? 'Make arc' : 'Make line'}</button>}
      {sketch.constraints.some(c => selection.includes(c.id) && 'value' in c) && <button type="button" disabled={!enabled} onClick={() => void command(sketch.constraints.filter(c => selection.includes(c.id) && 'value' in c).map(c => ({ kind: 'setDimensionPlacement', id: c.id })))}>Reset position</button>}
      <button type="button" disabled={!enabled} onClick={removeSelection}>Delete selection</button>
  </>;
}
