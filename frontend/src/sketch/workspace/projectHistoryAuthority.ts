import type { EmbeddedDesignUpdates } from '../../stores/designEdit';
import { currentDocumentLoad, getAtPath, selectCanRedo, selectCanUndo, subscribeRevision, useDesignStore, type RevisionEvent } from '../../stores/design';
import type { HistoryAuthority, HistoryCause } from './historyAuthority';

export interface ProjectBinding<S> {
  /** All paths read or written by this editor, including coupled membership paths. */
  paths: readonly string[];
  /** Undefined means the bound object is absent: close the tab or show its empty state. */
  read(values: Readonly<Record<string, unknown>>): S | undefined;
  /** Encode either EditRecords or the domain's native, sidecar-free records. */
  write(state: S, values: Readonly<Record<string, unknown>>): EmbeddedDesignUpdates;
  onMissing(): void;
}

interface BoundValues {
  values: Record<string, unknown>;
  json: Record<string, string | undefined>;
}

/** Project history owns the document; this authority only buffers an active edit. */
export class ProjectHistoryAuthority<S> implements HistoryAuthority<S> {
  readonly kind = 'project';
  current: S;
  private bound: BoundValues;
  private generation = currentDocumentLoad();
  private transaction?: { bound: BoundValues; generation: number; drag: boolean };
  private writing = false;
  private missing = false;
  private listeners = new Set<(state: S, cause: HistoryCause) => void>();
  private disposed = false;
  private unsubscribe: () => void;
  constructor(private binding: ProjectBinding<S>) {
    if (!binding.paths.length) throw new Error('A project binding needs at least one path.');
    this.bound = this.readValues();
    const initial = binding.read(this.bound.values);
    if (initial === undefined) throw new Error('The bound project object is absent.');
    this.current = initial;
    this.unsubscribe = subscribeRevision(event => this.revision(event));
  }
  private readValues(): BoundValues {
    const { design } = useDesignStore.getState();
    const values = Object.fromEntries(this.binding.paths.map(path => [path, getAtPath(design, path)]));
    const json = Object.fromEntries(this.binding.paths.map(path => [path, JSON.stringify(values[path])]));
    return { values, json };
  }
  private matches(bound: BoundValues, current = this.readValues()) {
    // setAtPath clones every design branch, including paths an edit did not touch.
    return this.binding.paths.every(path => current.json[path] === bound.json[path]);
  }
  private valid() {
    return !this.missing && !!this.transaction && this.transaction.generation === currentDocumentLoad()
      && this.matches(this.transaction.bound);
  }
  private ownWrite(action: () => void) {
    this.writing = true;
    try { action(); }
    finally {
      this.writing = false;
      // setAtPath clones the design, so retain the values read back after the write.
      this.bound = this.readValues();
      if (this.transaction) this.transaction.bound = this.bound;
    }
  }
  private revision(event: RevisionEvent) {
    if (this.writing) return;
    const next = this.readValues();
    const dragging = this.transaction?.drag;
    // Undo must cancel even a drag whose queued first frame has not changed the path yet.
    if (event.reason !== 'load' && !(event.reason === 'undo' && dragging) && this.matches(this.bound, next)) return;
    this.bound = next; this.generation = currentDocumentLoad();
    // Invalidate before notifying: an in-flight solve must never publish over Undo/Open.
    this.transaction = undefined;
    // Another writer owns the new value. End our live drag without overwriting it.
    if (dragging && useDesignStore.getState().dragSnapshot) this.ownWrite(() => useDesignStore.getState().endDrag());
    const state = this.binding.read(this.bound.values);
    this.missing = state === undefined;
    if (state !== undefined) this.current = state;
    const cause = event.reason === 'load' ? 'load' : event.reason === 'undo' || event.reason === 'redo' ? event.reason : 'external';
    this.listeners.forEach(listener => listener(this.current, cause));
    if (this.missing) this.binding.onMissing();
  }
  get canUndo() { return selectCanUndo(useDesignStore.getState(), useDesignStore.temporal.getState()); }
  get canRedo() { return selectCanRedo(useDesignStore.temporal.getState()); }
  begin(_label: string, options?: { drag?: boolean }) {
    this.transaction = { bound: this.bound, generation: this.generation, drag: !!options?.drag };
    if (options?.drag && this.valid()) this.ownWrite(() => useDesignStore.getState().beginDrag());
  }
  update(state: S) {
    if (!this.valid()) return;
    this.current = state;
    if (this.transaction!.drag) this.write('drag');
  }
  private write(mode: 'step' | 'amend' | 'drag') {
    const updates = this.binding.write(this.current, this.bound.values);
    if (Object.keys(updates).some(path => !this.binding.paths.includes(path))) throw new Error('Project write outside bound paths.');
    this.ownWrite(() => useDesignStore.getState().commitEmbedded(updates, mode));
  }
  commit(options?: { amend?: boolean }) {
    // Also guard unannounced writes (e.g. a store.setState by another host).
    if (!this.matches(this.bound) || this.generation !== currentDocumentLoad()) {
      this.revision({ revision: useDesignStore.getState().designRevision, reason: 'edit', immediate: true });
    }
    if (this.valid()) {
      if (this.transaction!.drag) this.ownWrite(() => useDesignStore.getState().endDrag());
      else this.write(options?.amend ? 'amend' : 'step');
    }
    this.transaction = undefined;
    return this.current;
  }
  cancel() {
    if (this.valid() && this.transaction!.drag) this.ownWrite(() => useDesignStore.getState().cancelDrag());
    this.transaction = undefined;
    const state = this.binding.read(this.bound.values);
    if (state !== undefined) this.current = state;
    return this.current;
  }
  replace(state: S) { this.current = state; }
  async undo(redo = false) { useDesignStore.getState()[redo ? 'redo' : 'undo'](); }
  subscribe(listener: (state: S, cause: HistoryCause) => void) {
    this.listeners.add(listener); return () => { this.listeners.delete(listener); };
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.cancel(); this.unsubscribe(); this.listeners.clear();
  }
}
