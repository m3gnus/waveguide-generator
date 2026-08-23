import { create } from 'zustand';
import {
  getDriverLibrary,
  rescanDriverLibrary,
  type DriverKind,
  type DriverLibraryInfo,
} from '../api/drivers';
import { namespaceStorage } from './durableSettings';
import { DRIVER_FIELD_KEYS, type DriverFieldKey, type DriverPreset } from './cadReturn';

/**
 * A driver the user saved from the T/S sheet.
 *
 * `based_on` records where the numbers came from -- a library id, or `manual`
 * for one typed from a datasheet. Keeping `base` and `overrides` apart here
 * too means a saved driver reopens with the same edit marks it was saved with,
 * rather than collapsing into a flat set of numbers whose provenance is gone.
 */
export interface SavedDriver {
  id: string;
  label: string;
  based_on: string;
  base: Partial<Record<DriverFieldKey, number>>;
  overrides: Partial<Record<DriverFieldKey, number>>;
  kind: DriverKind;
  z_ohm: number | null;
}

interface SavedDriverEnvelope {
  version: 1;
  drivers: SavedDriver[];
}

const SAVED_DRIVER_STORAGE_VERSION = 1;
const MAX_SAVED_DRIVERS = 200;
const storage = namespaceStorage('driverLibrary');

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseFields(value: unknown): Partial<Record<DriverFieldKey, number>> | null {
  if (value === undefined) return {};
  if (!isObject(value)) return null;
  const fields: Partial<Record<DriverFieldKey, number>> = {};
  for (const [key, item] of Object.entries(value)) {
    if (!DRIVER_FIELD_KEYS.includes(key as DriverFieldKey)) continue;
    if (typeof item !== 'number' || !Number.isFinite(item)) return null;
    fields[key as DriverFieldKey] = item;
  }
  return fields;
}

function parseSavedDriver(value: unknown): SavedDriver | null {
  if (!isObject(value)) return null;
  const { id, label, based_on: basedOn, kind, z_ohm: z } = value;
  if (typeof id !== 'string' || !id || typeof label !== 'string' || !label) return null;
  if (typeof basedOn !== 'string' || !basedOn) return null;
  if (kind !== 'lf' && kind !== 'cd' && kind !== 'unknown') return null;
  if (z !== null && z !== undefined && (typeof z !== 'number' || !Number.isFinite(z))) return null;
  const base = parseFields(value.base);
  const overrides = parseFields(value.overrides);
  if (!base || !overrides) return null;
  return { id, label, based_on: basedOn, base, overrides, kind, z_ohm: typeof z === 'number' ? z : null };
}

function readSavedDrivers(): SavedDriver[] {
  let raw: string | null = null;
  try { raw = storage.getItem('driverLibrary'); } catch { return []; }
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as SavedDriverEnvelope;
    if (!isObject(parsed) || parsed.version !== SAVED_DRIVER_STORAGE_VERSION) return [];
    if (!Array.isArray(parsed.drivers)) return [];
    const drivers = parsed.drivers.map(parseSavedDriver);
    // One malformed entry invalidates the file rather than being skipped: a
    // partial list would look like the user deleted the missing drivers.
    return drivers.every((driver): driver is SavedDriver => driver !== null) ? drivers : [];
  } catch { return []; }
}

function writeSavedDrivers(drivers: SavedDriver[]): void {
  try {
    storage.setItem(
      'driverLibrary',
      JSON.stringify({ version: SAVED_DRIVER_STORAGE_VERSION, drivers } satisfies SavedDriverEnvelope),
    );
  } catch { /* persistence is best effort */ }
}

/** Whether a saved driver's brand/model text answers this query. Substring,
 * not the server's token-prefix rank: this list is the user's own and short. */
export function savedDriverMatches(driver: SavedDriver, query: string): boolean {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return true;
  return normalized.split(/\s+/).every((token) => driver.label.toLocaleLowerCase().includes(token));
}

export function savedDriverPreset(driver: SavedDriver): DriverPreset {
  return {
    id: driver.id,
    label: driver.label,
    source: 'mine',
    kind: driver.kind,
    z_ohm: driver.z_ohm,
    base: { ...driver.base, ...driver.overrides },
  };
}

export type DriverLibraryStatus = 'idle' | 'loading' | 'ready' | 'unavailable';

interface DriverLibraryState {
  status: DriverLibraryStatus;
  info: DriverLibraryInfo | null;
  error: string | null;
  saved: SavedDriver[];
  /** Load the folder report once per session; later calls are no-ops unless
   * `force` asks for a fresh answer (a rescan, or a retry after a failure). */
  load: (options?: { force?: boolean; fetcher?: typeof fetch }) => Promise<void>;
  rescan: (fetcher?: typeof fetch) => Promise<void>;
  save: (driver: SavedDriver) => void;
  remove: (id: string) => void;
}

let inFlight: Promise<void> | null = null;

export const useDriverLibraryStore = create<DriverLibraryState>((set, get) => ({
  status: 'idle',
  info: null,
  error: null,
  saved: readSavedDrivers(),
  load: async ({ force = false, fetcher } = {}) => {
    if (!force && (get().status === 'ready' || get().status === 'unavailable')) return;
    if (inFlight && !force) return inFlight;
    set({ status: 'loading', error: null });
    const request = (async () => {
      try {
        const info = await getDriverLibrary(fetcher ?? fetch);
        set({ status: 'ready', info, error: null });
      } catch (reason) {
        // A library that cannot be reached is not a failure the rail reports
        // as an error: it is the same situation as an empty folder, and the
        // card falls back to the manual fields.
        set({ status: 'unavailable', info: null, error: reason instanceof Error ? reason.message : String(reason) });
      }
    })().finally(() => { inFlight = null; });
    inFlight = request;
    return request;
  },
  rescan: async (fetcher) => {
    set({ status: 'loading', error: null });
    try {
      set({ status: 'ready', info: await rescanDriverLibrary(fetcher ?? fetch), error: null });
    } catch (reason) {
      set({ status: 'unavailable', info: null, error: reason instanceof Error ? reason.message : String(reason) });
    }
  },
  save: (driver) => {
    set((state) => {
      const drivers = [driver, ...state.saved.filter((item) => item.id !== driver.id)].slice(0, MAX_SAVED_DRIVERS);
      writeSavedDrivers(drivers);
      return { saved: drivers };
    });
  },
  remove: (id) => {
    set((state) => {
      const drivers = state.saved.filter((item) => item.id !== id);
      writeSavedDrivers(drivers);
      return { saved: drivers };
    });
  },
}));

/** Whether the picker can offer a search at all. */
export function driverLibraryHasFiles(state: Pick<DriverLibraryState, 'status' | 'info'>): boolean {
  return state.status === 'ready' && (state.info?.files.length ?? 0) > 0;
}

export function resetDriverLibraryStore(): void {
  inFlight = null;
  useDriverLibraryStore.setState({ status: 'idle', info: null, error: null, saved: readSavedDrivers() });
}
