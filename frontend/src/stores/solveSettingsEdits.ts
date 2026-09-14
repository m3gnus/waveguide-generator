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
