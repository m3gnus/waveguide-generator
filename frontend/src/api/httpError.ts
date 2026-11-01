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

const OWN_VALIDATOR = /^Value error,\s*/;
const FRAMEWORK_SENTENCE = /^(Field required|Extra inputs|Input should|Input is|String should|List should|Tuple should|Dictionary should|Unable to|Invalid JSON|Assertion failed)/;

/** A validation message as words for a user: our own validators' sentence, or null for the framework's. */
export function validationSentence(message: string): string | null {
  if (OWN_VALIDATOR.test(message)) return message.replace(OWN_VALIDATOR, '');
  return FRAMEWORK_SENTENCE.test(message) ? null : message;
}

const FRAMEWORK_BOUND = /^Input should be ((?:less|greater) than(?: or equal to)? -?[\d.]+(?:e[+-]?\d+)?)/;
const WIRE_PATH = new Set(['body', 'design']);

/**
 * The most a framework validation message can honestly say: the field's name and
 * any bound the server gave. Null when it names no field.
 */
export function fieldSentence(location: readonly unknown[], message: string): string | null {
  const own = validationSentence(message);
  if (own) return own;
  const name = location.map(String).filter(part => !WIRE_PATH.has(part)).join('.');
  if (!name) return null;
  const bound = FRAMEWORK_BOUND.exec(message);
  return bound ? `${name} should be ${bound[1]}.` : message.startsWith('Field required') ? `${name} is missing.` : `${name} has a value WG cannot use.`;
}

export const PLAIN_REQUEST_FAILURE = 'WG could not finish this request and has recorded the problem. Try again.';
export const PLAIN_VALIDATION_FAILURE = 'WG could not use these settings. Check the values and try again.';

let reportOriginal: (entry: { message: string; operation: string }) => void = () => {};
/** Where originals go. This owner stays free of runtime dependencies, so the shell registers it. */
export function keepRefusalOriginals(report: typeof reportOriginal): void { reportOriginal = report; }

const isValidationItem = (item: unknown) => !!item && typeof item === 'object' && 'loc' in item && 'msg' in item;

/**
 * What a failed request tells the user. A sentence the server worded is shown as
 * it is; framework validation output and bare failures become one plain message,
 * and the original goes to the developer record behind problem reports.
 */
export function refusalText(detail: unknown, operation: string, fallback = PLAIN_REQUEST_FAILURE): string {
  const raw = formatApiDetail(detail);
  const words = Array.isArray(detail) && detail.some(isValidationItem)
    ? detail.flatMap(item => {
      if (!isValidationItem(item)) return [];
      const { loc, msg } = item as { loc: unknown; msg: unknown };
      const sentence = fieldSentence(Array.isArray(loc) ? loc : [], String(msg));
      return sentence ? [sentence] : [];
    }).join('; ') || PLAIN_VALIDATION_FAILURE
    : raw || fallback;
  if (words !== raw) reportOriginal({ message: raw || fallback, operation });
  return words;
}

export async function httpErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
  } catch { /* HTTP status remains useful. */ }
  return `${fallback} (${response.status})`;
}
