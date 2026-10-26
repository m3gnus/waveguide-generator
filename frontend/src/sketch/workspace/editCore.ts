import { LocalHistoryAuthority, type HistoryAuthority, type HistoryCause } from './historyAuthority';

export interface EditCoreOptions<S> {
  initial: S;
  authority?: HistoryAuthority<S>;
  changed?: (state: S) => void;
  activity?: (busy: boolean) => void;
  preview?: (state: S) => void;
  restore?: { cancelGesture(): Promise<void>; restoreState(state: S): Promise<S> };
}

export class StaleWorkspaceWork extends Error { constructor() { super('The project changed before this edit ran.'); } }

/** Serial queue and uncommitted gesture buffer, independent of the domain and solver. */
export class EditCore<S, SketchSession> {
  readonly authority: HistoryAuthority<S>;
  session?: SketchSession;
  readonly options: EditCoreOptions<S>;
  ready: Promise<void> = Promise.resolve();
  private work: Promise<unknown> = Promise.resolve();
  destroyed = false;
  dragging = false;
  dragStart?: S;
  private pending = 0;
  busy = false;
  notice?: string;
  rejection?: unknown;
  private ghost?: S;
  private historyVersion = 0;
  private historyListeners = new Set<(cause: HistoryCause) => void>();
  private unsubscribe?: () => void;
  get historyKind() { return this.authority.kind; }
  subscribeHistory = (listener: (cause: HistoryCause) => void) => {
    this.historyListeners.add(listener); return () => { this.historyListeners.delete(listener); };
  };
  constructor(options: EditCoreOptions<S>) {
    this.options = options;
    this.authority = options.authority ?? new LocalHistoryAuthority(options.initial);
    if (this.authority.kind === 'project') this.unsubscribe = this.authority.subscribe((state, cause) => {
      this.historyVersion++;
      this.historyListeners.forEach(listener => listener(cause));
      void this.rehydrate(state, options.restore).catch(reason => { if (!this.destroyed && !(reason instanceof StaleWorkspaceWork)) this.rejection = reason; });
    });
  }
  get current() { return this.authority.current; }
  private setActivity(busy: boolean) { if (busy !== this.busy) { this.busy = busy; if (!this.destroyed) this.options.activity?.(busy); } }
  private queue<R>(fn: (session: SketchSession) => Promise<R>): Promise<R> {
    const version = this.historyVersion;
    this.pending++; this.setActivity(true);
    const result = this.work.then(async () => {
      await this.ready;
      if (version !== this.historyVersion) throw new StaleWorkspaceWork();
      if (this.destroyed || !this.session) throw new Error('Sketch session is closed.');
      return fn(this.session);
    }).finally(() => { this.pending--; this.setActivity(this.pending > 0 || this.dragging); });
    this.work = result.catch(() => {}); return result;
  }
  enqueue<R>(fn: (session: SketchSession) => Promise<R>) { return this.queue(fn); }
  dismissRejection() { this.rejection = undefined; }
  protected publish(state: S) {
    this.dismissRejection(); if (!this.destroyed) this.options.changed?.(state);
  }
  protected showPreview(state: S) { if (!this.destroyed) this.options.preview?.(state); }
  async transact(label: string, edit: () => Promise<S>, amend = false, reportRejection = true) {
    this.notice = undefined; this.ghost = undefined;
    this.authority.begin(label);
    try {
      const next = await edit(); this.authority.update(next); const committed = this.authority.commit({ amend }); this.publish(committed); return committed;
    } catch (reason) { this.rejection = reportRejection ? reason : undefined; this.authority.cancel(); throw reason; }
  }
  startDrag() { this.notice = undefined; this.ghost = undefined; this.dragStart = this.current; this.authority.begin('Drag', { drag: true }); }
  updateDrag(state: S) { this.authority.update(state); this.showPreview(this.current); }
  finishDrag(cancel: boolean, state = this.current, rejected = false) {
    try {
      if (cancel || rejected) {
        this.authority.cancel();
        if (rejected && !cancel) this.publish(this.current); else this.showPreview(this.current);
      } else { this.authority.update(state); this.authority.commit(); this.publish(this.current); }
    } finally { this.dragging = false; this.dragStart = undefined; }
  }
  protected previewEdit(edit: () => S) {
    return this.enqueue(async () => { this.ghost = edit(); this.showPreview(this.ghost); return this.ghost; });
  }
  confirm() {
    return this.enqueue(async () => {
      const ghost = this.ghost;
      if (ghost === undefined) return this.current;
      return this.transact('Confirm preview', async () => ghost);
    });
  }
  cancelPreview() { this.ghost = undefined; this.showPreview(this.current); }
  rehydrate(state: S, hooks?: { cancelGesture(): Promise<void>; restoreState(state: S): Promise<S> }) {
    const version = this.historyVersion;
    return this.enqueue(async () => {
      if (version !== this.historyVersion) return;
      if (hooks) { await hooks.cancelGesture(); state = await hooks.restoreState(state); }
      else if (this.dragging) this.finishDrag(true);
      if (version !== this.historyVersion) return;
      this.ghost = undefined; this.authority.replace(state); this.dismissRejection(); this.notice = undefined; this.showPreview(state);
    });
  }
  close(dispose: () => void) {
    this.unsubscribe?.(); this.authority.dispose?.(); this.historyListeners.clear();
    this.destroyed = true; void this.ready.catch(() => {}).then(() => this.work).finally(dispose);
  }
}
