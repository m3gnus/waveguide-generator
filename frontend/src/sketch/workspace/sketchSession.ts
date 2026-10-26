import type { WorkspaceHistory } from './historyAuthority';
import type { SketchContext } from '../context';
import type { Command } from '../commands';
import type { NewConstraint, Sketch } from '../model';
import type { DragResult } from '../session';
import type { Capabilities, XY } from '../solver/types';

/** Compatibility surface for the Sketch controller until its W3 composition split. */
export interface SketchWorkspaceSession extends WorkspaceHistory {
  readonly current?: { sketch: Sketch };
  readonly capabilities?: Capabilities;
  readonly rejection?: unknown;
  readonly notice?: string;
  readonly lastSolveMs?: number;
  dismissRejection?(): void;
  materialiseContext?(context: SketchContext, itemId: string): Promise<{ sketch: Sketch; id: string }>;
  command(command: Command | Command[], dimensions?: { length?: number; angle?: number }): Promise<Sketch>;
  constraint(constraint: NewConstraint, reference?: boolean, placement?: boolean): Promise<Sketch>;
  dimension(id: string, value: number, mergePlacement?: boolean): Promise<Sketch>;
  beginDrag(id: string | string[]): Promise<void>;
  dragTo(id: string, target: XY, targets?: { pointId: string; x: number; y: number }[]): Promise<DragResult>;
  endDrag(cancel?: boolean): Promise<void>;
  undo(redo?: boolean): Promise<void>;
}
