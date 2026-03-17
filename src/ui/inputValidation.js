/**
 * Input Validation & Hardening Utilities
 * Provides constraints, normalization, and error messages for user inputs
 */

const CONSTRAINTS = {
  outputName: {
    maxLength: 128,
    minLength: 1,
    pattern: /^[a-zA-Z0-9_\-]+$/,
    description:
      "Use only letters, numbers, underscores (_), and hyphens (-). Up to 128 characters.",
    example: "Example: my_waveguide_v2",
  },
  counter: {
    min: 1,
    max: 999999,
    description: "Enter a number from 1 to 999,999",
  },
  jobLabel: {
    maxLength: 200,
    minLength: 0,
    description: "Optional label, up to 200 characters",
  },
  formula: {
    maxLength: 500,
    description: "Mathematical formula, up to 500 characters",
  },
  frequencyStart: {
    min: 10,
    max: 50000,
    description: "Frequency must be between 10 Hz and 50 kHz",
  },
  frequencyEnd: {
    min: 10,
    max: 50000,
    description: "Frequency must be between 10 Hz and 50 kHz",
  },
  frequencySteps: {
    min: 1,
    max: 1000,
    description: "Steps must be between 1 and 1000",
  },
};

/**
 * Validate output name (file prefix)
 * Returns { valid, normalized, error }
 */
export function validateOutputName(value) {
  const raw = String(value ?? "").trim();

  if (!raw) {
    return { valid: false, error: "Enter a name for your output files" };
  }

  if (raw.length > CONSTRAINTS.outputName.maxLength) {
    return {
      valid: false,
      error: `Name is too long (${raw.length} characters). Keep it under ${CONSTRAINTS.outputName.maxLength} characters.`,
    };
  }

  if (!CONSTRAINTS.outputName.pattern.test(raw)) {
    return {
      valid: false,
      error:
        "Use only letters, numbers, underscores (_), and hyphens (-). Spaces and special characters are not allowed.",
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
    return { valid: false, error: "Enter a whole number (e.g., 1, 2, 100)" };
  }

  if (num < CONSTRAINTS.counter.min) {
    return {
      valid: false,
      error: `Number must be at least ${CONSTRAINTS.counter.min}`,
    };
  }

  if (num > CONSTRAINTS.counter.max) {
    return {
      valid: false,
      error: `Number must be ${CONSTRAINTS.counter.max.toLocaleString()} or less`,
    };
  }

  return { valid: true, normalized: num };
}

/**
 * Validate job label
 * Returns { valid, normalized, error }
 */
export function validateJobLabel(value) {
  const raw = String(value ?? "").trim();

  if (raw.length > CONSTRAINTS.jobLabel.maxLength) {
    return {
      valid: false,
      error: `Label is too long. Keep it under ${CONSTRAINTS.jobLabel.maxLength} characters.`,
    };
  }

  return { valid: true, normalized: raw };
}

/**
 * Validate formula expression
 * Returns { valid, normalized, error }
 */
export function validateFormula(value) {
  const raw = String(value ?? "").trim();

  if (raw.length > CONSTRAINTS.formula.maxLength) {
    return {
      valid: false,
      error: `Formula too long (${raw.length}/${CONSTRAINTS.formula.maxLength} chars)`,
    };
  }

  return { valid: true, normalized: raw };
}

export function validateFrequencyRange(startValue, endValue) {
  const start = Number(startValue);
  const end = Number(endValue);

  if (!Number.isFinite(start)) {
    return { valid: false, error: "Start frequency must be a valid number" };
  }
  if (!Number.isFinite(end)) {
    return { valid: false, error: "End frequency must be a valid number" };
  }
  if (start < CONSTRAINTS.frequencyStart.min) {
    return {
      valid: false,
      error: `Start frequency must be at least ${CONSTRAINTS.frequencyStart.min} Hz`,
    };
  }
  if (end > CONSTRAINTS.frequencyEnd.max) {
    return {
      valid: false,
      error: `End frequency cannot exceed ${CONSTRAINTS.frequencyEnd.max.toLocaleString()} Hz`,
    };
  }
  if (start >= end) {
    return {
      valid: false,
      error: "Start frequency must be less than end frequency",
    };
  }
  if (start > CONSTRAINTS.frequencyStart.max) {
    return {
      valid: false,
      error: `Start frequency cannot exceed ${CONSTRAINTS.frequencyStart.max.toLocaleString()} Hz`,
    };
  }

  return { valid: true, normalized: { start, end } };
}

export function validateFrequencySteps(value) {
  const num = Number(value);

  if (!Number.isFinite(num) || !Number.isInteger(num)) {
    return { valid: false, error: "Frequency steps must be a whole number" };
  }
  if (num < CONSTRAINTS.frequencySteps.min) {
    return {
      valid: false,
      error: `At least ${CONSTRAINTS.frequencySteps.min} frequency step required`,
    };
  }
  if (num > CONSTRAINTS.frequencySteps.max) {
    return {
      valid: false,
      error: `Frequency steps cannot exceed ${CONSTRAINTS.frequencySteps.max.toLocaleString()}`,
    };
  }

  return { valid: true, normalized: num };
}

/**
 * Sanitize filename for safe output
 * Replaces invalid characters with underscores
 */
export function sanitizeFileName(name) {
  return String(name ?? "")
    .trim()
    .replace(/[^a-zA-Z0-9_\-\.]/g, "_")
    .replace(/_{2,}/g, "_")
    .replace(/^_+|_+$/g, "");
}

/**
 * Format numeric value with proper i18n support
 * Uses Intl.NumberFormat for locale-aware formatting
 */
export function formatNumber(value, options = {}) {
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value ?? "");

  const {
    minimumFractionDigits = 0,
    maximumFractionDigits = 2,
    locale = undefined,
  } = options;

  try {
    return new Intl.NumberFormat(locale, {
      minimumFractionDigits,
      maximumFractionDigits,
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
  if (!Number.isFinite(num) || num < 0) return "Unknown";

  const units = ["B", "KB", "MB", "GB", "TB"];
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
  const str = String(text ?? "").trim();
  if (str.length <= maxLength) return str;
  return str.substring(0, maxLength - 3) + "…";
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
  return result.error || "Invalid input";
}

export function showInputError(inputElement, message) {
  if (!inputElement) return;

  inputElement.classList.add("input-error");
  inputElement.classList.remove("input-success");
  inputElement.setAttribute("aria-invalid", "true");

  let errorEl = inputElement.parentElement?.querySelector(
    ".input-error-message",
  );
  if (!errorEl) {
    errorEl = document.createElement("span");
    errorEl.className = "input-error-message";
    errorEl.setAttribute("role", "alert");
    if (inputElement.parentElement) {
      inputElement.parentElement.appendChild(errorEl);
    }
  }
  errorEl.textContent = message;

  const inputId = inputElement.id;
  if (inputId) {
    const errorId = `${inputId}-error`;
    errorEl.id = errorId;
    inputElement.setAttribute("aria-describedby", errorId);
  }
}

export function hideInputError(inputElement, showSuccess = false) {
  if (!inputElement) return;

  inputElement.classList.remove("input-error");
  inputElement.removeAttribute("aria-invalid");
  inputElement.removeAttribute("aria-describedby");

  if (showSuccess) {
    inputElement.classList.add("input-success");
  } else {
    inputElement.classList.remove("input-success");
  }

  const errorEl = inputElement.parentElement?.querySelector(
    ".input-error-message",
  );
  if (errorEl) {
    errorEl.remove();
  }
}

export function debounce(fn, delayMs = 300) {
  let timeoutId = null;
  return (...args) => {
    if (timeoutId !== null) {
      clearTimeout(timeoutId);
    }
    timeoutId = setTimeout(() => {
      timeoutId = null;
      fn(...args);
    }, delayMs);
  };
}

export function throttle(fn, limitMs = 100) {
  let inThrottle = false;
  return (...args) => {
    if (!inThrottle) {
      fn(...args);
      inThrottle = true;
      setTimeout(() => {
        inThrottle = false;
      }, limitMs);
    }
  };
}

export function clamp(value, min, max) {
  const num = Number(value);
  if (!Number.isFinite(num)) return min;
  return Math.max(min, Math.min(max, num));
}

export function safeParseInt(value, fallback = 0) {
  const num = parseInt(String(value ?? ""), 10);
  return Number.isFinite(num) ? num : fallback;
}

export function safeParseFloat(value, fallback = 0) {
  const num = parseFloat(String(value ?? ""));
  return Number.isFinite(num) ? num : fallback;
}
