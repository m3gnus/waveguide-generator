/**
 * Empty State Messages & Helpers
 * Provides user-friendly messaging for empty/no-data scenarios
 */

const EMPTY_STATE_MESSAGES = {
  noResults: {
    title: "Simulation results will appear here",
    description:
      "After running a BEM solve, you'll see frequency response curves, polar directivity plots, and SPL measurements—essential data for validating your horn's acoustic performance. Click \"Start BEM Simulation\" to generate your first results.",
  },
  noJobs: {
    title: "Your simulation history is empty",
    description:
      "Each BEM solve creates a job record with frequency response and directivity data for comparison across design iterations. Run your first simulation to begin building a history of acoustic analyses.",
  },
  noData: {
    title: "Unable to load data",
    description:
      "The requested data couldn't be retrieved. Refresh the page to restore state, or run a new simulation to generate fresh results.",
  },
  noSimulationRunning: {
    title: "Ready for acoustic analysis",
    description:
      "Run a BEM simulation to compute how your waveguide performs across the frequency range. Results include on-axis response, beamwidth, and polar directivity—critical metrics for loudspeaker design.",
  },
  connectionError: {
    title: "Solver backend not connected",
    description:
      "The BEM solver runs locally and must be started before simulations. Open a terminal and run: python server/app.py. This enables mesh processing and acoustic field computation.",
  },
  noExportFormats: {
    title: "No export formats configured",
    description:
      "Enable CSV for SPL data, JSON for complete results, or PNG for publication-ready plots. Configure formats in Settings to save your simulation data for analysis and documentation.",
  },
  exportPending: {
    title: "Preparing export",
    description:
      "Generating your simulation data files. Complex frequency sweeps with high resolution may take several seconds to process.",
  },
  fileTooLarge: {
    title: "Export exceeds size limit",
    description:
      "High-frequency resolution creates large datasets. Export one format at a time, or reduce frequency point count in simulation settings to create smaller, faster exports.",
  },
  networkTimeout: {
    title: "Request timed out",
    description:
      "The solver didn't respond in time. Large meshes and high frequency resolution increase solve time—try again or simplify the geometry for faster iteration.",
  },
  networkOffline: {
    title: "Network connection lost",
    description:
      "Simulation jobs require connection to the local solver. Restore your network connection and try again.",
  },
  serverError: {
    title: "Solver error",
    description:
      "The BEM solver encountered an unexpected condition. Check the server console for details—common causes include mesh topology issues or invalid boundary conditions.",
  },
  validationError: {
    title: "Invalid parameters detected",
    description:
      "Some geometry or simulation values are outside valid ranges. Review the highlighted fields—correct parameters ensure accurate acoustic predictions and prevent solver failures.",
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
