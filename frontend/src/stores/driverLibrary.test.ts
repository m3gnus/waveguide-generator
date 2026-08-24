import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SETTINGS_NAMESPACES } from './durableSettings';
import { SETTINGS_NAMESPACE_EFFECTS } from './settingsClassification';
import {
  driverLibraryHasFiles,
  resetDriverLibraryStore,
  savedDriverMatches,
  savedDriverPreset,
  useDriverLibraryStore,
  type SavedDriver,
} from './driverLibrary';

const storageKey = SETTINGS_NAMESPACES.driverLibrary;

const MINE: SavedDriver = {
  id: 'mine:Acme::HD-1::8',
  label: 'Acme HD-1',
  based_on: 'Acme::HD-1::8',
  base: { sd_cm2: 26, bl_t_m: 12.4, re_ohm: 6.2 },
  overrides: { bl_t_m: 11.9 },
  kind: 'cd',
  z_ohm: 8,
  xo_min_hz: 1_600,
};

describe('the driverLibrary durable namespace', () => {
  beforeEach(() => {
    localStorage.clear();
    resetDriverLibraryStore();
    vi.unstubAllGlobals();
  });

  it('is registered and classified like every other namespace', () => {
    // Both records are exhaustive over `SettingsNamespace`, so a namespace
    // added to one and not the other does not compile. This asserts the pair
    // an installation actually reads: the storage key and its effect.
    expect(SETTINGS_NAMESPACES.driverLibrary).toBe('waveguide-v2-g3-driver-library');
    expect(SETTINGS_NAMESPACE_EFFECTS.driverLibrary).toBe('inert');
  });

  it('saves a driver under the namespace and reads it back', () => {
    useDriverLibraryStore.getState().save(MINE);
    expect(JSON.parse(localStorage.getItem(storageKey)!)).toEqual({ version: 1, drivers: [MINE] });

    resetDriverLibraryStore();
    expect(useDriverLibraryStore.getState().saved).toEqual([MINE]);

    // Saving the same driver again replaces it rather than duplicating it.
    useDriverLibraryStore.getState().save({ ...MINE, label: 'Acme HD-1 (measured)' });
    expect(useDriverLibraryStore.getState().saved).toHaveLength(1);
  });

  it('drops a malformed payload rather than restoring part of a list', () => {
    localStorage.setItem(storageKey, JSON.stringify({
      version: 1,
      drivers: [MINE, { id: 'broken', label: 'Broken', based_on: 'manual', kind: 'nonsense' }],
    }));
    resetDriverLibraryStore();
    expect(useDriverLibraryStore.getState().saved).toEqual([]);
  });

  it('offers a saved driver as a preset with its edits already folded in', () => {
    expect(savedDriverPreset(MINE)).toEqual({
      id: MINE.id,
      label: 'Acme HD-1',
      source: 'mine',
      kind: 'cd',
      z_ohm: 8,
      xo_min_hz: 1_600,
      base: { sd_cm2: 26, bl_t_m: 11.9, re_ohm: 6.2 },
    });
    expect(savedDriverMatches(MINE, 'acme hd')).toBe(true);
    expect(savedDriverMatches(MINE, 'hd-1 acme')).toBe(true);
    expect(savedDriverMatches(MINE, 'radian')).toBe(false);
  });

  it('round-trips xo_min_hz through storage, and tolerates a driver saved before the field existed', () => {
    useDriverLibraryStore.getState().save(MINE);
    resetDriverLibraryStore();
    expect(useDriverLibraryStore.getState().saved).toEqual([MINE]);

    // A driver saved before this field existed has no `xo_min_hz` key at all;
    // that migrates to null rather than dropping the whole record.
    const { xo_min_hz: _drop, ...withoutField } = MINE;
    localStorage.setItem(storageKey, JSON.stringify({ version: 1, drivers: [withoutField] }));
    resetDriverLibraryStore();
    expect(useDriverLibraryStore.getState().saved).toEqual([{ ...MINE, xo_min_hz: null }]);

    // A present but malformed value still fails the whole record, like every
    // other field here.
    localStorage.setItem(storageKey, JSON.stringify({
      version: 1,
      drivers: [{ ...MINE, xo_min_hz: 'not a number' }],
    }));
    resetDriverLibraryStore();
    expect(useDriverLibraryStore.getState().saved).toEqual([]);
  });

  it('reports an unreachable or empty library as nothing to search', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('offline'))));
    await useDriverLibraryStore.getState().load();
    expect(useDriverLibraryStore.getState().status).toBe('unavailable');
    expect(driverLibraryHasFiles(useDriverLibraryStore.getState())).toBe(false);

    resetDriverLibraryStore();
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(
      JSON.stringify({ folder: '/library', files: [], total_drivers: 0 }),
      { status: 200 },
    ))));
    await useDriverLibraryStore.getState().load();
    expect(useDriverLibraryStore.getState().status).toBe('ready');
    expect(driverLibraryHasFiles(useDriverLibraryStore.getState())).toBe(false);
  });
});
