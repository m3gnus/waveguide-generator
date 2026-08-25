/**
 * The driver library's REST surface (CADLINK-CROSSOVER-DRIVERS.md section 4).
 *
 * Waveguide Generator ships no driver data: these routes read whatever CSV
 * files the user dropped into their own library folder. An installation with
 * no such folder is the normal case, not an error, so every caller here has to
 * treat "no files" as an answer rather than a failure.
 */

export type DriverKind = 'lf' | 'cd' | 'unknown';
export type DriverCompleteness = 'full' | 'partial' | 'catalogue';

export interface DriverVariantSummary {
  id: string;
  z_ohm?: number | null;
}

export interface DriverSource {
  file: string;
  source_url?: string | null;
  price_eur?: number | null;
}

export interface DriverDisplay {
  fs_hz?: number | null;
  sd_cm2?: number | null;
  bl_t_m?: number | null;
  xmax_mm?: number | null;
  sensitivity_db?: number | null;
  price_eur?: number | null;
}

export interface DriverHit {
  id: string;
  brand: string;
  model: string;
  z_ohm?: number | null;
  variants: DriverVariantSummary[];
  kind: DriverKind;
  size?: string | null;
  completeness: DriverCompleteness;
  /** Only the fields the row actually carried, in `DriverSpec`'s wire units. */
  spec: Record<string, number>;
  display: DriverDisplay;
  xo_min_hz?: number | null;
  source: DriverSource;
}

export interface DriverDetail extends DriverHit {
  fields: Record<string, number | string | null>;
  extras: Record<string, string>;
}

export interface DriverLibraryFile {
  name: string;
  rows: number;
}

export interface DriverLibraryInfo {
  folder: string;
  files: DriverLibraryFile[];
  total_drivers: number;
  /** How many of them can actually drive a channel -- what a search offers. */
  complete_drivers?: number;
  last_scan?: string | null;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
  } catch { /* HTTP status remains useful. */ }
  return `Driver library request failed (${response.status})`;
}

export interface DriverSearchQuery {
  q?: string;
  kind?: DriverKind | 'all';
  z?: number | null;
  limit?: number;
  /**
   * Withhold rows the solve cannot use. Defaults on: a row without Sd, Bl, Re,
   * a mass and a compliance is dropped on the way to the wire, so offering it
   * is offering a driver that comes back with no power, current or excursion.
   * Most compression-driver rows in a catalogue CSV are exactly that.
   */
  complete?: boolean;
}

export interface DriverSearchResult {
  items: DriverHit[];
  /** Matches the library holds but cannot drive a channel with. */
  hiddenIncomplete: number;
}

export async function searchDrivers(
  { q = '', kind = 'all', z = null, limit = 20, complete = true }: DriverSearchQuery = {},
  fetcher: typeof fetch = fetch,
): Promise<DriverSearchResult> {
  const params = new URLSearchParams({ q, kind, limit: String(limit) });
  if (z !== null && Number.isFinite(z)) params.set('z', String(z));
  if (complete) params.set('complete', 'true');
  const response = await fetcher(`/api/drivers?${params.toString()}`);
  if (!response.ok) throw new Error(await errorMessage(response));
  const body = await response.json() as { items?: DriverHit[]; hidden_incomplete?: number };
  return {
    items: Array.isArray(body.items) ? body.items : [],
    hiddenIncomplete: typeof body.hidden_incomplete === 'number' ? body.hidden_incomplete : 0,
  };
}

/**
 * One driver, with its impedance list narrowed to the windings that can drive
 * a channel -- the same rule the search applies, so the sheet's winding
 * buttons never offer a row the search would have withheld. The winding asked
 * for by id is always listed, even if it is the incomplete one.
 */
export async function getDriver(
  driverId: string,
  fetcher: typeof fetch = fetch,
): Promise<DriverDetail> {
  const response = await fetcher(`/api/drivers/${encodeURIComponent(driverId)}?complete=true`);
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<DriverDetail>;
}

export async function getDriverLibrary(
  fetcher: typeof fetch = fetch,
): Promise<DriverLibraryInfo> {
  const response = await fetcher('/api/drivers/library');
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<DriverLibraryInfo>;
}

export async function rescanDriverLibrary(
  fetcher: typeof fetch = fetch,
): Promise<DriverLibraryInfo> {
  const response = await fetcher('/api/drivers/library/rescan', { method: 'POST' });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<DriverLibraryInfo>;
}
