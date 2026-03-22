export function generateId() {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

export function categorizeEvent(eventName) {
  if (eventName.startsWith('state:')) return 'state';
  if (eventName.startsWith('geometry:')) return 'geometry';
  if (eventName.startsWith('workflow:')) return 'workflow';
  if (eventName.startsWith('export:')) return 'export';
  if (eventName.startsWith('simulation:')) return 'simulation';
  if (eventName.startsWith('optimization:')) return 'optimization';
  if (eventName.startsWith('validation:')) return 'validation';
  return 'general';
}

export function sanitizeData(data, depth = 3) {
  if (depth <= 0) return '[max depth]';
  if (data === null || data === undefined) return data;
  if (typeof data !== 'object') return data;

  // Handle arrays
  if (Array.isArray(data)) {
    if (data.length > 100) {
      return `[Array(${data.length})]`;
    }
    return data.slice(0, 20).map((item) => sanitizeData(item, depth - 1));
  }

  // Handle typed arrays (e.g., Float32Array from Three.js)
  if (ArrayBuffer.isView(data)) {
    return `[${data.constructor.name}(${data.length})]`;
  }

  // Handle plain objects
  const result = {};
  const keys = Object.keys(data);

  for (const key of keys.slice(0, 50)) {
    result[key] = sanitizeData(data[key], depth - 1);
  }

  if (keys.length > 50) {
    result._truncated = `${keys.length - 50} more keys`;
  }

  return result;
}
