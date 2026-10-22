/** The engine name WG advertised while BEAT's backends were one entry. */
export const LEGACY_BEAT_ENGINE = 'beat';

/** Whether `name` is one of BEAT's per-backend engines. */
export function isBeatBackendEngine(name: string): boolean {
  return name.toLowerCase().startsWith('beat-');
}
