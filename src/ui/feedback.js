const TOAST_CONTAINER_ID = 'ui-toast-container';

function hasDom() {
  return typeof document !== 'undefined' && typeof document.createElement === 'function';
}

function ensureToastContainer() {
  if (!hasDom()) return null;
  let container = document.getElementById(TOAST_CONTAINER_ID);
  if (container) return container;

  container = document.createElement('div');
  container.id = TOAST_CONTAINER_ID;
  container.className = 'ui-toast-container';
  container.setAttribute('aria-live', 'polite');
  container.setAttribute('aria-atomic', 'false');
  document.body.appendChild(container);
  return container;
}

export function showMessage(message, { type = 'info', duration = 3200 } = {}) {
  const text = String(message || '').trim();
  if (!text) return;

  if (!hasDom()) {
    const logger = type === 'error' ? console.error : console.log;
    logger(text);
    return;
  }

  const container = ensureToastContainer();
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `ui-toast ui-toast-${type}`;
  toast.textContent = text;

  container.appendChild(toast);
  const raf = typeof requestAnimationFrame === 'function'
    ? requestAnimationFrame
    : (callback) => setTimeout(callback, 0);
  raf(() => {
    toast.classList.add('visible');
  });

  const hide = () => {
    toast.classList.remove('visible');
    setTimeout(() => toast.remove(), 180);
  };

  const timeoutMs = Number.isFinite(duration) ? Math.max(600, duration) : 3200;
  setTimeout(hide, timeoutMs);
}

export function showError(message, duration = 5000) {
  showMessage(message, { type: 'error', duration });
}

export function showSuccess(message, duration = 2400) {
  showMessage(message, { type: 'success', duration });
}

export function chooseExportFormat() {
  if (!hasDom()) return Promise.resolve(null);

  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'ui-choice-backdrop';

    const dialog = document.createElement('div');
    dialog.className = 'ui-choice-dialog';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-label', 'Export results');

    const title = document.createElement('h4');
    title.className = 'ui-choice-title';
    title.textContent = 'Export Results';
    dialog.appendChild(title);

    const subtitle = document.createElement('p');
    subtitle.className = 'ui-choice-subtitle';
    subtitle.textContent = 'Choose output format';
    dialog.appendChild(subtitle);

    const actions = document.createElement('div');
    actions.className = 'ui-choice-actions';
    dialog.appendChild(actions);

    const options = [
      { id: '1', label: 'PNG/SVG Image' },
      { id: '2', label: 'CSV Data' },
      { id: '3', label: 'JSON Data' },
      { id: '4', label: 'Text Report' }
    ];

    let settled = false;
    const finalize = (value) => {
      if (settled) return;
      settled = true;
      window.removeEventListener('keydown', onKeyDown);
      backdrop.remove();
      resolve(value);
    };

    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        finalize(null);
      }
    };

    options.forEach((opt) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'ui-choice-btn';
      button.textContent = opt.label;
      button.addEventListener('click', () => finalize(opt.id));
      actions.appendChild(button);
    });

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'ui-choice-btn secondary';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', () => finalize(null));
    actions.appendChild(cancelBtn);

    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop) {
        finalize(null);
      }
    });

    window.addEventListener('keydown', onKeyDown);
    backdrop.appendChild(dialog);
    document.body.appendChild(backdrop);
  });
}
