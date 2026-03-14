/**
 * Input Validation & Hardening Utilities
 * Provides constraints, normalization, and error messages for user inputs
 */

// Constraint definitions
const CONSTRAINTS = {
  // Export file name prefix
  outputName: {
    maxLength: 128,
    minLength: 1,
    pattern: /^[a-zA-Z0-9_\-]+$/, // Alphanumeric, underscore, hyphen only
    description: 'Only letters, numbers, underscore, and hyphen allowed (max 128 chars)'
  },
  // Counter value
  counter: {
    min: 1,
    max: 999999,
    description: 'Must be between 1 and 999,999'
  },
  // Job label/name
  jobLabel: {
    maxLength: 200,
    minLength: 0,
    description: 'Maximum 200 characters'
  },
  // Formula input
  formula: {
    maxLength: 500,
    description: 'Maximum 500 characters for formula'
  }
};

/**
 * Validate output name (file prefix)
 * Returns { valid, normalized, error }
 */
export function validateOutputName(value) {
  const raw = String(value ?? '').trim();

  if (!raw) {
    return { valid: false, error: 'Output name is required' };
  }

  if (raw.length > CONSTRAINTS.outputName.maxLength) {
    return {
      valid: false,
      error: `Output name too long (${raw.length}/${CONSTRAINTS.outputName.maxLength} chars)`
    };
  }

  if (!CONSTRAINTS.outputName.pattern.test(raw)) {
    return {
      valid: false,
      error: 'Output name contains invalid characters. ' + CONSTRAINTS.outputName.description
    };
  }

  return { valid: true, normalized: raw };
}

/**
 * Validate counter value
 * Returns { valid, normalized, error }
 */
export function validateCounter(value) {
  const num = Number(value);

  if (!Number.isInteger(num)) {
    return { valid: false, error: 'Counter must be a whole number' };
  }

  if (num < CONSTRAINTS.counter.min) {
    return {
      valid: false,
      error: `Counter too small (minimum: ${CONSTRAINTS.counter.min})`
    };
  }

  if (num > CONSTRAINTS.counter.max) {
    return {
      valid: false,
      error: `Counter too large (maximum: ${CONSTRAINTS.counter.max})`
    };
  }

  return { valid: true, normalized: num };
}

/**
 * Validate job label
 * Returns { valid, normalized, error }
 */
export function validateJobLabel(value) {
  const raw = String(value ?? '').trim();

  if (raw.length > CONSTRAINTS.jobLabel.maxLength) {
    return {
      valid: false,
      error: `Label too long (${raw.length}/${CONSTRAINTS.jobLabel.maxLength} chars)`
    };
  }

  return { valid: true, normalized: raw };
}

/**
 * Validate formula expression
 * Returns { valid, normalized, error }
 */
export function validateFormula(value) {
  const raw = String(value ?? '').trim();

  if (raw.length > CONSTRAINTS.formula.maxLength) {
    return {
      valid: false,
      error: `Formula too long (${raw.length}/${CONSTRAINTS.formula.maxLength} chars)`
    };
  }

  return { valid: true, normalized: raw };
}

/**
 * Sanitize filename for safe output
 * Replaces invalid characters with underscores
 */
export function sanitizeFileName(name) {
  return String(name ?? '')
    .trim()
    .replace(/[^a-zA-Z0-9_\-\.]/g, '_')
    .replace(/_{2,}/g, '_')
    .replace(/^_+|_+$/g, '');
}

/**
 * Format numeric value with proper i18n support
 * Uses Intl.NumberFormat for locale-aware formatting
 */
export function formatNumber(value, options = {}) {
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value ?? '');

  const {
    minimumFractionDigits = 0,
    maximumFractionDigits = 2,
    locale = undefined
  } = options;

  try {
    return new Intl.NumberFormat(locale, {
      minimumFractionDigits,
      maximumFractionDigits
    }).format(num);
  } catch {
    // Fallback for unsupported locales
    return num.toFixed(minimumFractionDigits);
  }
}

/**
 * Format file size in human-readable format
 * @param {number} bytes - File size in bytes
 * @returns {string} Formatted size (e.g., "2.5 MB")
 */
export function formatFileSize(bytes) {
  const num = Number(bytes);
  if (!Number.isFinite(num) || num < 0) return 'Unknown';

  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = num;
  let unitIndex = 0;

  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex++;
  }

  return `${size.toFixed(size < 10 ? 1 : 0)} ${units[unitIndex]}`;
}

/**
 * Check if text would overflow and needs truncation
 * Returns truncated version if needed
 */
export function truncateTextIfNeeded(text, maxLength = 50) {
  const str = String(text ?? '').trim();
  if (str.length <= maxLength) return str;
  return str.substring(0, maxLength - 3) + '…';
}

/**
 * Get field constraints for UI help text
 */
export function getFieldConstraints(fieldName) {
  return CONSTRAINTS[fieldName] || null;
}

/**
 * Create detailed error message from validation result
 */
export function getValidationError(result) {
  if (!result || result.valid) return null;
  return result.error || 'Invalid input';
}
