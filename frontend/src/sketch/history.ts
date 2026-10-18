import type { Sketch } from './model';

export class History<T = Sketch> {
  private past: { label: string; snapshot: T }[] = [];
  private future: { label: string; snapshot: T }[] = [];
  private transaction?: { label: string; snapshot: T };
  constructor(public current: T) {}
  get canUndo() { return this.past.length > 0; }
  get canRedo() { return this.future.length > 0; }
  begin(label: string) {
    if (this.transaction) throw new Error('A transaction is already active.');
    this.transaction = { label, snapshot: this.current };
  }
  update(snapshot: T) {
    if (!this.transaction) throw new Error('begin() must precede an update.');
    this.current = snapshot;
  }
  commit() {
    if (!this.transaction) throw new Error('No active transaction.');
    if (this.current !== this.transaction.snapshot) {
      this.past = [...this.past.slice(-99), this.transaction]; this.future = [];
    }
    this.transaction = undefined; return this.current;
  }
  cancel() {
    if (!this.transaction) throw new Error('No active transaction.');
    this.current = this.transaction.snapshot; this.transaction = undefined; return this.current;
  }
  undo() {
    if (this.transaction) throw new Error('Finish the active transaction first.');
    const previous = this.past.pop(); if (!previous) return this.current;
    this.future.push({ label: previous.label, snapshot: this.current }); this.current = previous.snapshot; return this.current;
  }
  redo() {
    if (this.transaction) throw new Error('Finish the active transaction first.');
    const next = this.future.pop(); if (!next) return this.current;
    this.past.push({ label: next.label, snapshot: this.current }); this.current = next.snapshot; return this.current;
  }
}
