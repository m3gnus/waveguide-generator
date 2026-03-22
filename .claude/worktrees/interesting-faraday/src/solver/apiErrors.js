function formatValidationDetail(detail) {
  if (typeof detail === 'string') {
    return detail.trim();
  }

  if (Array.isArray(detail)) {
    const lines = detail
      .map((item) => {
        if (typeof item === 'string') return item.trim();
        if (!item || typeof item !== 'object') return '';
        const location = Array.isArray(item.loc) ? item.loc.join('.') : '';
        const message = typeof item.msg === 'string' ? item.msg.trim() : '';
        if (location && message) return `${location}: ${message}`;
        return message || location;
      })
      .filter(Boolean);
    return lines.join('; ');
  }

  if (detail && typeof detail === 'object') {
    try {
      return JSON.stringify(detail);
    } catch {
      return '';
    }
  }

  return '';
}

export function classifyApiErrorStatus(status) {
  if (status === 422) return 'validation';
  if (status === 503) return 'dependency';
  if (status === 404) return 'not_found';
  return 'unexpected';
}

export function parseApiErrorPayload(payload) {
  if (!payload || typeof payload !== 'object') return '';
  if (payload.detail !== undefined) {
    return formatValidationDetail(payload.detail);
  }
  if (typeof payload.message === 'string') {
    return payload.message.trim();
  }
  return '';
}

function formatApiErrorMessage({ operation, status, category, detail }) {
  const prefixByCategory = {
    validation: `${operation} failed validation (${status})`,
    dependency: `${operation} unavailable (${status})`,
    not_found: `${operation} resource not found (${status})`,
    unexpected: `${operation} failed (${status})`
  };

  const prefix = prefixByCategory[category] || `${operation} failed (${status})`;
  return detail ? `${prefix}: ${detail}` : prefix;
}

export function createApiError({ operation, status, category, detail, cause = null }) {
  const error = new Error(formatApiErrorMessage({ operation, status, category, detail }));
  error.name = 'ApiError';
  error.isApiError = true;
  error.operation = operation;
  error.status = status;
  error.category = category;
  error.detail = detail || '';
  if (cause) {
    error.cause = cause;
  }
  return error;
}

export async function parseApiErrorResponse(response, { operation = 'Request' } = {}) {
  let detail = '';
  try {
    const payload = await response.json();
    detail = parseApiErrorPayload(payload);
  } catch {
    detail = '';
  }

  const category = classifyApiErrorStatus(response.status);
  return createApiError({
    operation,
    status: response.status,
    category,
    detail
  });
}

export function createNetworkApiError(operation, cause) {
  const message = cause?.name === 'AbortError'
    ? `${operation} timed out while contacting backend`
    : `${operation} could not reach backend service`;

  const error = new Error(message);
  error.name = 'ApiError';
  error.isApiError = true;
  error.operation = operation;
  error.status = 0;
  error.category = 'network';
  error.detail = '';
  if (cause) {
    error.cause = cause;
  }
  return error;
}
