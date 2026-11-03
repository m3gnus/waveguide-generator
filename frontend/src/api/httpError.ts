export const SOLVE_CONTROLS: Record<string, NamedControl> = {
  engine: { label: 'Solver', place: 'Solve options' },
  accuracy: { label: 'Accuracy', place: 'Solve options' },
  symmetry: { label: 'Symmetry', place: 'Solve options' },
  solver_mode: { label: 'Solver mode', place: 'Solve options' },
  frequency_range: { label: 'Frequency range', place: 'Solve options' },
  num_frequencies: { label: 'Frequency samples', place: 'Solve options' },
  frequencies_hz: { label: 'Solver frequencies in Hz', place: 'Solve options' },
  frequency_spacing: { label: 'Sweep spacing', place: 'Solve options' },
  adaptive_frequency_sampling: { label: 'Adaptive frequency sampling (experimental)', place: 'Solve options' },
  mesh_ladder: { label: 'Per-band mesh ladder', place: 'Solve options' },
  mesh_validation_mode: { label: 'Mesh validation', place: 'Solve options' },
  'ground_plane.enabled': { label: 'Ground plane', place: 'Solve options' },
  'ground_plane.axis': { label: 'Reflecting surface', place: 'Ground plane under Solve options' },
  'ground_plane.height_m': { label: 'Height above surface', place: 'Ground plane under Solve options' },
  'polar_config.angle_range': { label: 'Angle range', place: 'Directivity map under Solve options' },
  'polar_config.angle_step': { label: 'Angular step', place: 'Directivity map under Solve options' },
  'polar_config.distance': { label: 'Measurement distance', place: 'Directivity map under Solve options' },
  'polar_config.norm_angle': { label: 'Normalization angle', place: 'Directivity map under Solve options' },
  'polar_config.inclination': { label: 'Diagonal plane angle', place: 'Directivity map under Solve options' },
  'polar_config.enabled_axes': { label: 'Directivity planes', place: 'Directivity map under Solve options' },
  'polar_config.observation_origin': { label: 'Measurement origin', place: 'Directivity map under Solve options' },
  'polar_config.spherical_sampling': { label: 'Keep 3D balloon result', place: 'Directivity map under Solve options' },
  'polar_config.field_plane': { label: 'Keep field plane data', place: 'Directivity map under Solve options' },
};


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
const WIRE_PATH = new Set(['body', 'design', 'root', 'options']);

export interface NamedControl {
  /** The word on the control. */
  label: string;
  /** Where the control lives, as the panel names it. */
  place?: string;
}


let controlFor: (path: string) => NamedControl | null = () => null;
/** Who can name a field. This owner stays free of runtime dependencies, so the parameter registry registers it. */
export function nameFieldsWith(lookup: typeof controlFor): void { controlFor = lookup; }

const fieldPath = (location: readonly unknown[]) => location.map(String).filter(part => !WIRE_PATH.has(part)).join('.');

/**
 * A framework validation message beside the control it belongs to, where the
 * label is already on screen: what to do to this value, and nothing else.
 */
export function rowSentence(message: string): string {
  const own = validationSentence(message);
  if (own) return own;
  const bound = FRAMEWORK_BOUND.exec(message);
  if (bound) return `Enter a value ${bound[1]}.`;
  return message.startsWith('Field required') ? 'Enter a value.' : 'Enter a value WG can use here.';
}

/**
 * The most a framework validation message can honestly say on its own: the
 * control's name and any bound the server gave. Null when no control has that
 * name, because a raw key means nothing to a user.
 */
export function fieldSentence(location: readonly unknown[], message: string): string | null {
  const own = validationSentence(message);
  if (own) return own;
  const path = fieldPath(location);
  const control = location.includes('options') ? SOLVE_CONTROLS[path.replace(/\.\d+/g, '')] ?? null : controlFor(path);
  if (!control) return null;
  const where = control.place ? ` in ${control.place}` : '';
  const bound = FRAMEWORK_BOUND.exec(message);
  if (bound) return `${control.label} must be ${bound[1]}. Change it${where}.`;
  return message.startsWith('Field required')
    ? `${control.label} is empty. Enter a value${where}.`
    : `WG cannot use the value of ${control.label}. Enter a new value${where}.`;
}

export const PLAIN_REQUEST_FAILURE = 'WG could not finish this request and has recorded the problem.';
export const PLAIN_VALIDATION_FAILURE = 'WG could not read a value in this design and has recorded the problem. Undo your last change, or open the design again.';
export const PLAIN_CAD_LINK_FAILURE = 'WG could not reach CAD Link and has recorded the problem. Refresh CAD Link to try again.';
export const LOST_CONTACT = 'WG is not responding. Close WG, open it again, then try again.';

let reportOriginal: (entry: { message: string; operation: string }) => void = () => {};
/** Where originals go. This owner stays free of runtime dependencies, so the shell registers it. */
export function keepRefusalOriginals(report: typeof reportOriginal): void { reportOriginal = report; }

/**
 * A failure the program caused. Its message is for developers: it goes to the
 * problem record and never to the screen, so a parser or an invariant can say
 * exactly what it found.
 */
export class ProgramFault extends Error {
  constructor(message: string, options?: ErrorOptions) { super(message, options); this.name = 'ProgramFault'; }
}

/** Fetch cancellation is a user action, including errors from another realm. */
export function isAbortError(reason: unknown): boolean {
  return !!reason && typeof reason === 'object' && 'name' in reason && reason.name === 'AbortError';
}

/** Text no user should read: engine, framework or transport words, wire keys and status codes. */
const NOT_FOR_USERS = [
  /\b(?:is not a function|is not iterable|is not defined|Cannot read propert|Cannot set propert|undefined is not|null is not|Unexpected token|Unexpected end of|Maximum call stack|Traceback|\[object Object\])/i,
  /^(?:(?:Type|Reference|Syntax|Range|Assertion|Value|Key|Attribute|Runtime|OS|Timeout|Index)Error\b|Error:)/,
  /\b(?:Request failed|request failed|HTTP \d{3}|status \d{3})\b|\(\d{3}\)\.?$|^\d{3}\b/,
  /^(?:role resolution|symmetry|normalisation|normalization|meshing|viewport meshing|mesh sizing|tag allocation|healing gate|scope gate|STEP import \+ normalisation|stage \d+[^:]*):/i,
  /\b[a-z][a-z0-9]*_[a-z0-9_]+\b/,
  /\b[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\[/,
  /\b(?:design|mesh|options|simulation|source)\.[a-z_]+\b/,
  /\b[A-Z][a-z]+[A-Za-z]*\.[A-Z][a-z]+[A-Za-z]*\b/,
  /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i,
  /\bfinding-[a-z]+(?:-[a-z0-9]+)+\b/,
];

/** Whether a sentence can be shown as it is. */
export function readsPlain(text: string): boolean {
  const words = text.trim();
  // A path, a file name or a quoted name is the user's own; only the sentence around it is judged.
  const sentence = words.replace(/(?:[A-Za-z]:)?[\\/][^\s,;)]+|[^\s/\\]+\.(?:step|stp|stl|msh|mwg|cfg|f3d|json|csv|txt|frd|zma|png|svg|zip|wgreturn)\b|[“"'‘][^”"'’]*[”"'’]/g, ' ');
  return Boolean(words) && !FRAMEWORK_SENTENCE.test(words) && !NOT_FOR_USERS.some(pattern => pattern.test(sentence));
}

/** A wire code at the front of a sentence: classification reads it, the user does not. */
const WIRE_CODE_PREFIX = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)+: (?=[A-Z])/;
const withoutCode = (text: string) => text.replace(WIRE_CODE_PREFIX, '');

const LOST_TRANSPORT = /^(?:Failed to fetch|Load failed|NetworkError when attempting to fetch resource\.?|The network connection was lost\.?)$/i;

function shown(original: string, operation: string, fallback: string): string {
  reportOriginal({ message: original || fallback, operation });
  return fallback;
}

/**
 * What a caught failure tells the user. A sentence that reads plainly is shown
 * as it is. A lost connection says so. Anything the program caused becomes the
 * fallback, and the original goes to the developer record. Every catch that
 * reaches the screen goes through here.
 */
export function failureWords(reason: unknown, fallback = PLAIN_REQUEST_FAILURE, operation = 'caught failure'): string {
  if (isAbortError(reason)) return 'Stopped';
  const text = withoutCode(reason instanceof Error ? reason.message : typeof reason === 'string' ? reason : String(reason ?? ''));
  if (LOST_TRANSPORT.test(text.trim())) return LOST_CONTACT;
  if (reason instanceof ProgramFault || !readsPlain(text)) return shown(text, operation, fallback);
  return text;
}

const isValidationItem = (item: unknown) => !!item && typeof item === 'object' && 'loc' in item && 'msg' in item;

/**
 * What a failed request tells the user. A sentence the server worded is shown
 * as it is; framework validation output, internal text and bare failures become
 * one plain message, and the original goes to the developer record behind
 * problem reports.
 */
export function refusalText(detail: unknown, operation: string, fallback = PLAIN_REQUEST_FAILURE): string {
  const formatted = formatApiDetail(detail);
  const raw = formatted && withoutCode(formatted);
  if (Array.isArray(detail) && detail.some(isValidationItem)) {
    const words = detail.flatMap(item => {
      if (!isValidationItem(item)) return [];
      const { loc, msg, ctx } = item as { loc: unknown; msg: unknown; ctx?: { error?: unknown } };
      const typed = ctx?.error as { user_message?: unknown; next_step?: unknown } | undefined;
      if (typeof typed?.user_message === 'string' && typeof typed.next_step === 'string') {
        return [refusalText(ctx?.error, operation, fallback)];
      }
      const sentence = fieldSentence(Array.isArray(loc) ? loc : [], String(msg));
      return sentence ? [sentence] : [];
    }).join(' ') || PLAIN_VALIDATION_FAILURE;
    if (words !== raw) reportOriginal({ message: raw || fallback, operation });
    return words;
  }
  if (detail && typeof detail === 'object') {
    const typed = detail as { field?: unknown; user_message?: unknown; next_step?: unknown };
    if (typeof typed.field === 'string' && typeof typed.user_message === 'string' && typeof typed.next_step === 'string') {
      const said = `${typed.user_message} ${typed.next_step}`.trim();
      return readsPlain(said) ? said : shown(said, operation, fallback);
    }
    const { message, next_step: next } = detail as { message?: unknown; next_step?: unknown };
    if (typeof message === 'string' && message.trim()) {
      const said = [message, typeof next === 'string' ? next : ''].filter(Boolean).join(' ');
      return readsPlain(said) ? said : shown(said, operation, fallback);
    }
  }
  return raw && readsPlain(raw) ? raw : shown(raw ?? '', operation, fallback);
}

/** The words of a failed response. `fallback` is a whole sentence with its next step; the status never reaches the user. */
export async function httpErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (body.detail !== undefined && body.detail !== null) return refusalText(body.detail, `request ${response.status}`, fallback);
  } catch (error) {
    if (isAbortError(error)) throw error;
    // The response has no readable body; the original below records its status.
  }
  return shown(`request failed with status ${response.status}`, 'request', fallback);
}
