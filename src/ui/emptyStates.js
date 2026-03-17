/**
 * Empty State Messages & Helpers
 * Provides user-friendly messaging for empty/no-data scenarios
 */

const EMPTY_STATE_MESSAGES = {
  noResults: {
    title: "No simulation results yet",
    description:
      "Run a BEM simulation to see frequency response, directivity, and impedance data here.",
  },
  noJobs: {
    title: "No simulation history",
    description: "Your completed and in-progress simulations will appear here.",
  },
  noData: {
    title: "Unable to load data",
    description:
      "Something went wrong. Try refreshing the page or running the simulation again.",
  },
  noSimulationRunning: {
    title: "Ready to simulate",
    description:
      'Click "Run BEM Simulation" to start analyzing your waveguide design.',
  },
  connectionError: {
    title: "Solver not connected",
    description:
      'Start the Python backend server (localhost:8000) to run simulations. Run "python server/app.py" from the project directory.',
  },
  noExportFormats: {
    title: "No export formats selected",
    description:
      "Go to Settings and enable at least one export format (CSV, JSON, PNG, etc.) to export results.",
  },
  exportPending: {
    title: "Exporting results",
    description:
      "Preparing your files. Large exports may take a few seconds...",
  },
  fileTooLarge: {
    title: "Export too large",
    description:
      "The data exceeds size limits. Try exporting one format at a time, or reduce the frequency resolution.",
  },
  networkTimeout: {
    title: "Request timed out",
    description:
      "The server took too long to respond. Check your network connection and try again.",
  },
  networkOffline: {
    title: "No network connection",
    description:
      "You appear to be offline. Check your internet connection and try again.",
  },
  serverError: {
    title: "Server error",
    description:
      "The backend encountered an unexpected error. Check the server logs for details.",
  },
  validationError: {
    title: "Invalid input",
    description:
      "Some values are out of range or invalid. Please check the highlighted fields.",
  },
};

/**
 * Create an empty state HTML element
 * @param {string} type - The empty state type (key from EMPTY_STATE_MESSAGES)
 * @param {Object} options - Additional options
 * @returns {HTMLElement} Empty state container
 */
export function createEmptyStateElement(type = "noData", options = {}) {
  const config = EMPTY_STATE_MESSAGES[type] || EMPTY_STATE_MESSAGES.noData;
  const {
    title = config.title,
    description = config.description,
    icon = null,
  } = options;

  const container = document.createElement("div");
  container.className = "empty-state";
  container.setAttribute("role", "status");
  container.setAttribute("aria-live", "polite");

  if (icon) {
    const iconEl = document.createElement("div");
    iconEl.className = "empty-state-icon";
    iconEl.textContent = icon;
    container.appendChild(iconEl);
  }

  const titleEl = document.createElement("h3");
  titleEl.className = "empty-state-title";
  titleEl.textContent = title;
  container.appendChild(titleEl);

  const descEl = document.createElement("p");
  descEl.className = "empty-state-description";
  descEl.textContent = description;
  container.appendChild(descEl);

  return container;
}

/**
 * Check if a value is considered "empty" for state purposes
 */
export function isEmpty(value) {
  if (value === null || value === undefined) return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value).length === 0;
  if (typeof value === "string") return value.trim().length === 0;
  return false;
}

/**
 * Get appropriate empty state message for a condition
 */
export function getEmptyStateMessage(type = "noData") {
  return EMPTY_STATE_MESSAGES[type] || EMPTY_STATE_MESSAGES.noData;
}

/**
 * Render error state in a container
 * @param {HTMLElement} container - Target container
 * @param {string} title - Error title
 * @param {string} message - Error message
 * @param {Function} onRetry - Optional retry callback
 */
export function renderErrorState(
  container,
  title = "Error",
  message = "An error occurred",
  onRetry = null,
) {
  if (!container) return;

  container.innerHTML = "";
  container.className = "error-state";

  const titleEl = document.createElement("h3");
  titleEl.className = "error-state-title";
  titleEl.textContent = title;
  container.appendChild(titleEl);

  const msgEl = document.createElement("p");
  msgEl.className = "error-state-message";
  msgEl.textContent = message;
  container.appendChild(msgEl);

  if (typeof onRetry === "function") {
    const retryBtn = document.createElement("button");
    retryBtn.className = "error-state-retry-btn";
    retryBtn.type = "button";
    retryBtn.textContent = "Try Again";
    retryBtn.addEventListener("click", onRetry);
    container.appendChild(retryBtn);
  }
}

/**
 * Render loading state in a container
 * @param {HTMLElement} container - Target container
 * @param {string} message - Loading message
 */
export function renderLoadingState(container, message = "Loading...") {
  if (!container) return;

  container.innerHTML = "";
  container.className = "loading-state";

  const spinner = document.createElement("div");
  spinner.className = "loading-spinner";
  container.appendChild(spinner);

  const msgEl = document.createElement("p");
  msgEl.className = "loading-message";
  msgEl.textContent = message;
  container.appendChild(msgEl);
}

export function categorizeError(error) {
  if (!error) return "noData";

  if (error.isApiError) {
    if (error.category === "network") {
      if (error.cause?.name === "AbortError") {
        return "networkTimeout";
      }
      if (!navigator.onLine) {
        return "networkOffline";
      }
      return "connectionError";
    }
    if (error.category === "validation") {
      return "validationError";
    }
    if (error.status >= 500) {
      return "serverError";
    }
    if (error.status === 404) {
      return "noData";
    }
  }

  if (error.name === "TypeError" && error.message?.includes("fetch")) {
    return "connectionError";
  }

  return "noData";
}

export function renderApiErrorState(container, error, onRetry = null) {
  const errorType = categorizeError(error);
  const config = EMPTY_STATE_MESSAGES[errorType] || EMPTY_STATE_MESSAGES.noData;
  const title = error?.operation ? `${error.operation} failed` : config.title;
  const message = error?.message || config.description;
  renderErrorState(container, title, message, onRetry);
}

export function withRetry(fn, options = {}) {
  const { maxRetries = 3, delayMs = 1000, onRetry = null } = options;

  return async (...args) => {
    let lastError = null;
    for (let attempt = 0; attempt < maxRetries; attempt++) {
      try {
        return await fn(...args);
      } catch (error) {
        lastError = error;
        if (attempt < maxRetries - 1) {
          if (typeof onRetry === "function") {
            onRetry(attempt + 1, error);
          }
          await new Promise((resolve) => setTimeout(resolve, delayMs));
        }
      }
    }
    throw lastError;
  };
}

export function renderJobListSkeleton(container, count = 3) {
  if (!container) return;

  const skeletons = Array.from(
    { length: count },
    () => `
    <div class="skeleton-job-item">
      <div class="skeleton-job-header">
        <div class="skeleton-job-info">
          <div class="skeleton skeleton-job-title"></div>
          <div class="skeleton skeleton-job-meta"></div>
        </div>
        <div class="skeleton-job-actions">
          <div class="skeleton skeleton-job-btn"></div>
          <div class="skeleton skeleton-job-btn"></div>
        </div>
      </div>
      <div class="skeleton-job-footer">
        <div class="skeleton skeleton-job-footer-label"></div>
        <div class="skeleton-job-stars">
          ${Array.from({ length: 5 }, () => '<div class="skeleton skeleton-job-star"></div>').join("")}
        </div>
      </div>
    </div>
  `,
  ).join("");

  container.innerHTML = skeletons;
}

export function renderResultsSkeleton(container) {
  if (!container) return;

  container.innerHTML = `
    <div class="skeleton-results-panel">
      <div class="skeleton skeleton-results-title"></div>
      <div class="skeleton-results-grid">
        <div class="skeleton skeleton-results-item"></div>
        <div class="skeleton skeleton-results-item"></div>
        <div class="skeleton skeleton-results-item"></div>
        <div class="skeleton skeleton-results-item"></div>
      </div>
      <div class="skeleton skeleton-results-chart"></div>
      <div class="skeleton skeleton-results-chart"></div>
    </div>
  `;
}
