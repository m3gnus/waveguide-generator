/**
 * A person changed a solve setting through a control.
 *
 * The stores also change for reasons nobody chose -- a selection restores a
 * profile, an ingestion files the settings under the project it states, a
 * recalled run puts its options back -- so a store subscription cannot tell a
 * choice from a transition. The setters a person drives say so here instead,
 * and only that is recorded as a project's setup (shell/cadSetupPublisher).
 */
export interface SolveSettingsEdit { source?: string; control?: string }
type Listener = (edit?: SolveSettingsEdit) => void;

type EditGate = (source: string | undefined, apply: () => unknown) => boolean;
const gates = new Set<EditGate>();
/** A model read may hold controls until its baseline is known. */
export function gateSolveSettingsEdits(gate: EditGate): () => void {
  gates.add(gate);
  return () => { gates.delete(gate); };
}
const listeners = new Set<Listener>();
const beforeListeners = new Set<Listener>();

/** Capture the controls immediately before a person's edit, after any edit gate. */
export function beforeSolveSettingsEdit(edit: SolveSettingsEdit): void {
  beforeListeners.forEach(listener => listener(edit));
}

export function subscribeBeforeSolveSettingsEdits(listener: Listener): () => void {
  beforeListeners.add(listener);
  return () => { beforeListeners.delete(listener); };
}

export function noteSolveSettingsEdit(edit?: SolveSettingsEdit): void {
  listeners.forEach((listener) => listener(edit));
}

export function subscribeSolveSettingsEdits(listener: Listener): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/**
 * A store's initial state with the named setters announcing themselves: each
 * runs as written, then signals when it changed the state. A setter that
 * changed nothing -- the last directivity plane kept on -- chose nothing.
 */
export function withEditSignals<S extends object>(
  get: () => S,
  state: S,
  setters: ReadonlyArray<keyof S>,
  source?: string,
): S {
  const announced = { ...state };
  for (const name of setters) {
    const setter = state[name] as unknown as (...args: unknown[]) => unknown;
    announced[name] = ((...args: unknown[]) => {
      const apply = () => {
        const before = get();
        beforeSolveSettingsEdit({ source, control: String(name) });
        const result = setter(...args);
        if (get() !== before) noteSolveSettingsEdit({ source, control: String(name) });
        return result;
      };
      if ([...gates].some(gate => gate(source, apply))) return;
      return apply();
    }) as unknown as S[keyof S];
  }
  return announced;
}
