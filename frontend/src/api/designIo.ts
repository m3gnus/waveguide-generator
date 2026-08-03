import {
  designForFamily,
  serializeDesign,
  type DesignDocument,
  type DesignFamily,
} from '../stores/design';

export interface MigrationApplication {
  name: string;
  note: string;
}

export interface ImportReport {
  dialect: 'mwg' | 'ath';
  migrationsApplied: MigrationApplication[];
  passthrough: {
    keysPreserved: string[];
    blocksPreserved: string[];
    keyCount: number;
    blockCount: number;
  };
}

export interface OpenDesignResponse extends ImportReport {
  design: Record<string, unknown>;
}

export interface SaveDesignResponse {
  text: string;
  suggestedFilename: string;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: string | { message?: string } };
    if (typeof body.detail === 'string') return body.detail;
    if (body.detail?.message) return body.detail.message;
  } catch { /* the status text remains useful */ }
  return `Request failed: ${response.status} ${response.statusText}`.trim();
}

async function postText<T>(path: string, text: string, fetcher: typeof fetch): Promise<T> {
  const response = await fetcher(path, {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    body: text,
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<T>;
}

export function openDesignText(text: string, fetcher: typeof fetch = fetch): Promise<OpenDesignResponse> {
  return postText('/api/design/open', text, fetcher);
}

export function inspectDesignText(text: string, fetcher: typeof fetch = fetch): Promise<ImportReport> {
  return postText('/api/design/import-report', text, fetcher);
}

export async function saveDesignDocument(
  design: DesignDocument,
  filename: string,
  fetcher: typeof fetch = fetch,
): Promise<SaveDesignResponse> {
  const response = await fetcher('/api/design/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ design: serializeDesign(design), filename }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<SaveDesignResponse>;
}

function unwrapExpressions(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(unwrapExpressions);
  if (value === null || typeof value !== 'object') return value;
  const record = value as Record<string, unknown>;
  if ('value' in record && Object.keys(record).every((key) => key === 'value' || key === 'raw')) {
    return record.value ?? record.raw ?? null;
  }
  return Object.fromEntries(Object.entries(record).map(([key, item]) => [key, unwrapExpressions(item)]));
}

function merge<T>(base: T, incoming: unknown): T {
  if (incoming === null || typeof incoming !== 'object' || Array.isArray(incoming)) return incoming as T;
  const output = { ...(base as Record<string, unknown>) };
  Object.entries(incoming as Record<string, unknown>).forEach(([key, value]) => {
    const current = output[key];
    output[key] = value !== null && typeof value === 'object' && !Array.isArray(value)
      ? merge(current && typeof current === 'object' ? current : {}, value)
      : value;
  });
  return output as T;
}

/** Convert schema-wire Expr objects into the store's evaluated document shape. */
export function hydrateDesignDocument(wire: Record<string, unknown>): DesignDocument {
  const unwrapped = unwrapExpressions(wire) as Record<string, unknown>;
  const formula = unwrapped.formula as DesignFamily;
  if (!['OSSE', 'R-OSSE', 'ICW', 'FREEFORM'].includes(formula)) {
    throw new Error(`Unsupported design formula: ${String(unwrapped.formula)}`);
  }
  const document = merge(designForFamily(formula), unwrapped);
  const mask = Number(document.mesh.quadrants);
  document.quadrants = [1, 2, 3, 4].filter((quadrant) => Boolean(mask & (1 << (quadrant - 1))));
  document.enclosure.baffle_margin = Number(document.enclosure.space_l);
  document.source.contours = document.source.contours ?? '';
  return document;
}

function responseFilename(response: Response, fallback: string): string {
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return match?.[1] ?? fallback;
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function downloadText(text: string, filename: string, type = 'text/plain;charset=utf-8'): void {
  downloadBlob(new Blob([text], { type }), filename);
}

export async function downloadGeometryExport(
  kind: 'step' | 'stl' | 'profiles',
  design: DesignDocument,
  designRevision: number,
  baseName: string,
  profileKind?: 'profiles' | 'slices',
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const query = kind === 'profiles' ? `?kind=${profileKind ?? 'profiles'}` : '';
  const response = await fetcher(`/api/export/${kind}${query}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      design: serializeDesign(design),
      designRevision,
      baseName,
    }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  const suffix = kind === 'profiles' ? `_${profileKind ?? 'profiles'}.csv` : `.${kind}`;
  downloadBlob(await response.blob(), responseFilename(response, `${baseName}${suffix}`));
}
