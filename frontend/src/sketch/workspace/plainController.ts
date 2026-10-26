import { EditCore, type EditCoreOptions } from './editCore';
import type { DomainAdapter, WorkspaceSession } from './types';
import type { XY } from '../solver/types';

type Commands<S, C> = C | C[] | ((state: S) => C | C[]);
export interface PlainControllerOptions<D, S, C> extends EditCoreOptions<S> { adapter: DomainAdapter<D, S, C> }

/** Solver-free domains use the same transactions, queue, preview and history lifecycle. */
export class PlainController<D, S, C> extends EditCore<S, object> implements WorkspaceSession<S, C> {
  private drag?: { id: string; move: (state: S, delta: XY, at: XY) => C[]; at: XY };
  constructor(private config: PlainControllerOptions<D, S, C>) { super(config); this.session = {}; }
  get status() { return this.config.adapter.status(this.current); }
  private apply(state: S, commands: Commands<S, C>) {
    const resolved = typeof commands === 'function' ? (commands as (state: S) => C | C[])(state) : commands;
    for (const command of Array.isArray(resolved) ? resolved : [resolved]) {
      const next = this.config.adapter.apply(state, command);
      if (next && typeof next === 'object' && 'error' in next) throw new Error(String(next.error));
      state = next as S;
    }
    return state;
  }
  command(commands: Commands<S, C>) {
    return this.enqueue(() => this.transact('Edit', async () => this.apply(this.current, commands)));
  }
  beginDrag(ids: string[]) {
    return this.enqueue(async () => {
      const set = this.config.adapter.dragSet(this.current, ids, ids[0]);
      const at = this.config.adapter.display(this.current, { selection: ids, scale: 1 }).items.find(item => item.id === ids[0]);
      if (!set || !at || !('at' in at)) throw new Error('Selection cannot be dragged.');
      this.startDrag(); this.drag = { id: ids[0], move: set.move, at: at.at }; this.dragging = true;
    });
  }
  dragTo(targets: { id: string; at: XY }[]) {
    return this.enqueue(async () => {
      if (!this.dragging) return { stopped: true };
      if (!this.drag || !this.dragStart) throw new Error('No active drag.');
      const at = targets.find(target => target.id === this.drag!.id)?.at;
      if (!at) return { stopped: false };
      try {
        const delta = { x: at.x - this.drag.at.x, y: at.y - this.drag.at.y };
        this.updateDrag(this.apply(this.dragStart, this.drag.move(this.dragStart, delta, at)));
        return { stopped: false };
      } catch (reason) { return { stopped: true, reason: reason instanceof Error ? reason.message : String(reason) }; }
    });
  }
  endDrag(cancel = false) {
    return this.enqueue(async () => {
      if (!this.dragging) return;
      this.finishDrag(cancel || JSON.stringify(this.current) === JSON.stringify(this.dragStart)); this.drag = undefined;
    });
  }
  undo(redo = false) {
    if (this.authority.kind === 'project') return this.authority.undo(redo);
    return this.enqueue(async () => {
      const before = this.current; this.cancelPreview();
      await this.authority.undo(redo); if (this.current !== before) this.publish(this.current);
    });
  }
  preview(command: C) { return this.previewEdit(() => this.apply(this.current, command)); }
  destroy() { this.close(() => {}); }
}
