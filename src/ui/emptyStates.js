/**
 * Empty State Messages & Helpers
 * Provides user-friendly messaging for empty/no-data scenarios
 */

const EMPTY_STATE_MESSAGES = {
  noResults: {
    title: 'No Results',
    description: 'Run a simulation to generate results.'
  },
  noJobs: {
    title: 'No Simulations',
    description: 'Start a new simulation to see results here.'
  },
  noData: {
    title: 'No Data',
    description: 'Unable to retrieve data. Please try again.'
  },
  noSimulationRunning: {
    title: 'Ready to Simulate',
    description: 'Press "Start BEM Simulation" to begin.'
  },
  connectionError: {
    title: 'Solver Offline',
    description: 'Cannot connect to the solver. Make sure the Python backend is running on localhost:8000.'
  },
  noExportFormats: {
    title: 'No Export Formats',
    description: 'Select at least one export format in settings.'
  },
  exportPending: {
    title: 'Export In Progress',
    description: 'Please wait while your results are being exported...'
  },
  fileTooLarge: {
    title: 'File Too Large',
    description: 'The export is too large. Try exporting a single format or reducing data resolution.'
  }
};

/**
 * Create an empty state HTML element
 * @param {string} type - The empty state type (key from EMPTY_STATE_MESSAGES)
 * @param {Object} options - Additional options
 * @returns {HTMLElement} Empty state container
 */
export function createEmptyStateElement(type = 'noData', options = {}) {
  const config = EMPTY_STATE_MESSAGES[type] || EMPTY_STATE_MESSAGES.noData;
  const { title = config.title, description = config.description, icon = null } = options;

  const container = document.createElement('div');
  container.className = 'empty-state';
  container.setAttribute('role', 'status');
  container.setAttribute('aria-live', 'polite');

  if (icon) {
    const iconEl = document.createElement('div');
    iconEl.className = 'empty-state-icon';
    iconEl.textContent = icon;
    container.appendChild(iconEl);
  }

  const titleEl = document.createElement('h3');
  titleEl.className = 'empty-state-title';
  titleEl.textContent = title;
  container.appendChild(titleEl);

  const descEl = document.createElement('p');
  descEl.className = 'empty-state-description';
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
  if (typeof value === 'object') return Object.keys(value).length === 0;
  if (typeof value === 'string') return value.trim().length === 0;
  return false;
}

/**
 * Get appropriate empty state message for a condition
 */
export function getEmptyStateMessage(type = 'noData') {
  return EMPTY_STATE_MESSAGES[type] || EMPTY_STATE_MESSAGES.noData;
}

/**
 * Render error state in a container
 * @param {HTMLElement} container - Target container
 * @param {string} title - Error title
 * @param {string} message - Error message
 * @param {Function} onRetry - Optional retry callback
 */
export function renderErrorState(container, title = 'Error', message = 'An error occurred', onRetry = null) {
  if (!container) return;

  container.innerHTML = '';
  container.className = 'error-state';

  const titleEl = document.createElement('h3');
  titleEl.className = 'error-state-title';
  titleEl.textContent = title;
  container.appendChild(titleEl);

  const msgEl = document.createElement('p');
  msgEl.className = 'error-state-message';
  msgEl.textContent = message;
  container.appendChild(msgEl);

  if (typeof onRetry === 'function') {
    const retryBtn = document.createElement('button');
    retryBtn.className = 'error-state-retry-btn';
    retryBtn.type = 'button';
    retryBtn.textContent = 'Try Again';
    retryBtn.addEventListener('click', onRetry);
    container.appendChild(retryBtn);
  }
}

/**
 * Render loading state in a container
 * @param {HTMLElement} container - Target container
 * @param {string} message - Loading message
 */
export function renderLoadingState(container, message = 'Loading...') {
  if (!container) return;

  container.innerHTML = '';
  container.className = 'loading-state';

  const spinner = document.createElement('div');
  spinner.className = 'loading-spinner';
  container.appendChild(spinner);

  const msgEl = document.createElement('p');
  msgEl.className = 'loading-message';
  msgEl.textContent = message;
  container.appendChild(msgEl);
}
