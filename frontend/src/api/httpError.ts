export function formatApiDetail(value: unknown): string | null {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    const items = value.map((item) => formatApiDetail(item)).filter((item): item is string => Boolean(item));
    return items.length ? items.join('; ') : null;
  }
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const message = typeof record.msg === 'string' ? record.msg : typeof record.message === 'string' ? record.message : null;
    const location = Array.isArray(record.loc) ? record.loc.map(String).join('.') : null;
    if (message) return location ? `${location}: ${message}` : message;
    const entries = Object.entries(record).flatMap(([key, item]) => {
      const formatted = formatApiDetail(item);
      return formatted ? [`${key}: ${formatted}`] : [];
    });
    return entries.length ? entries.join('; ') : null;
  }
  return value === null || value === undefined ? null : String(value);
}

export async function httpErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
  } catch { /* HTTP status remains useful. */ }
  return `${fallback} (${response.status})`;
}
