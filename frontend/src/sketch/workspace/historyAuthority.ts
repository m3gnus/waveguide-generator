import { History } from '../history';

export type HistoryCause = 'undo' | 'redo' | 'load' | 'external';
export interface WorkspaceHistory {
  readonly historyKind?: 'local' | 'project';
  subscribeHistory?(listener: (cause: HistoryCause) => void): () => void;
}
export interface HistoryAuthority<S> {
  readonly kind: 'local' | 'project';
  readonly current: S;
  begin(label: string, options?: { drag?: boolean }): void;
  update(state: S): void;
  commit(opts?: { amend?: boolean }): S;
  cancel(): S;
  replace(state: S): void;
  readonly canUndo: boolean;
  readonly canRedo: boolean;
  undo(redo?: boolean): Promise<void>;
  dispose?(): void;
  subscribe(listener: (state: S, cause: HistoryCause) => void): () => void;
}

/** Temporary authority for settings-hosted editors, retained until their retirement. */
export class LocalHistoryAuthority<S> implements HistoryAuthority<S> {
  readonly kind = 'local';
  readonly history: History<S>;
  private listeners = new Set<(state: S, cause: HistoryCause) => void>();
  constructor(initial: S, private restore?: (state: S, accept: () => void) => Promise<void>) {
    this.history = new History(initial);
  }
  get current() { return this.history.current; }
  get canUndo() { return this.history.canUndo; }
  get canRedo() { return this.history.canRedo; }
  begin(label: string) { this.history.begin(label); }
  update(state: S) { this.history.update(state); }
  commit(opts?: { amend?: boolean }) { return this.history.commit(opts?.amend); }
  cancel() { return this.history.cancel(); }
  replace(state: S) { this.history.current = state; }
  async undo(redo = false) {
    // Peek and validate before moving either stack. A failed solver/context restore
    // rolls both history and the controller's live session back.
    const state = this.history.peek(redo);
    if (state === this.current) return;
    let moved = false;
    const accept = () => { if (redo) this.history.redo(); else this.history.undo(); moved = true; };
    try {
      if (this.restore) await this.restore(state, accept);
      else accept();
    } catch (reason) {
      if (moved) { if (redo) this.history.undo(); else this.history.redo(); }
      throw reason;
    }
    this.listeners.forEach(listener => listener(this.current, redo ? 'redo' : 'undo'));
  }
  subscribe(listener: (state: S, cause: HistoryCause) => void) {
    this.listeners.add(listener); return () => { this.listeners.delete(listener); };
  }
}
