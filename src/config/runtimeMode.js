/**
 * Runtime mode helper used to gate debug-only globals and diagnostics.
 */
export function isDevRuntime() {
  if (typeof globalThis === 'undefined') {
    return false;
  }

  const forced = globalThis.__WAVEGUIDE_DEBUG__;
  if (typeof forced === 'boolean') {
    return forced;
  }

  if (typeof process !== 'undefined' && process?.env?.NODE_ENV) {
    return process.env.NODE_ENV !== 'production';
  }

  const loc = globalThis.location;
  if (!loc) {
    return false;
  }

  const host = String(loc.hostname || '').toLowerCase();
  return (
    loc.protocol === 'file:' ||
    host === 'localhost' ||
    host === '127.0.0.1' ||
    host.endsWith('.local')
  );
}
