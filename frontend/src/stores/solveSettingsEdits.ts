/**
 * A person changed a solve setting through a control.
 *
 * The stores also change for reasons nobody chose -- a selection restores a
 * profile, an ingestion files the settings under the project it states, a
 * recalled run puts its options back -- so a store subscription cannot tell a
 * choice from a transition. The setters a person drives say so here instead,
 * and only that is recorded as a project's setup (shell/cadSetupPublisher).
 */
type Listener = () => void;

const listeners = new Set<Listener>();

export function noteSolveSettingsEdit(): void {
  listeners.forEach((listener) => listener());
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
): S {
  const announced = { ...state };
  for (const name of setters) {
    const setter = state[name] as unknown as (...args: unknown[]) => unknown;
    announced[name] = ((...args: unknown[]) => {
      const before = get();
      const result = setter(...args);
      if (get() !== before) noteSolveSettingsEdit();
      return result;
    }) as unknown as S[keyof S];
  }
  return announced;
}
