export type SettingsSection = 'cad';

type Listener = (section: SettingsSection) => void;
const listeners = new Set<Listener>();

export function requestSettings(section: SettingsSection): void {
  listeners.forEach((listener) => listener(section));
}

export function subscribeSettingsRequests(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
