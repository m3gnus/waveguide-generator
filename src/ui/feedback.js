import { trapFocus } from './focusTrap.js';

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
  const raf =
    typeof requestAnimationFrame === 'function'
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

/**
 * Show error with categorization for better UX
 * Automatically determines duration and can extract details from errors
 */
export function showDetailedError(error, { duration = 6000, context = '' } = {}) {
  let message = 'An error occurred';

  if (error instanceof Error) {
    message = error.message;
  } else if (typeof error === 'string') {
    message = error;
  } else if (error && typeof error === 'object') {
    message = error.message || error.detail || String(error);
  }

  // Add context prefix if provided
  if (context) {
    message = `${context}: ${message}`;
  }

  showError(message, Math.max(duration, 4000)); // Minimum 4 seconds for errors
}

export function showSuccess(message, duration = 2400) {
  showMessage(message, { type: 'success', duration });
}

export function showInfo(message, duration = 3200) {
  showMessage(message, { type: 'info', duration });
}

export function showCopyConfirmation(text = 'Copied!') {
  if (!hasDom()) {
    console.log(text);
    return;
  }

  const chip = document.createElement('div');
  chip.className = 'copy-confirmation-chip';
  chip.setAttribute('role', 'status');
  chip.setAttribute('aria-live', 'polite');

  const icon = document.createElement('span');
  icon.className = 'chip-icon';
  icon.textContent = '✓';

  const label = document.createElement('span');
  label.className = 'chip-label';
  label.textContent = text;

  chip.appendChild(icon);
  chip.appendChild(label);
  document.body.appendChild(chip);

  const hide = () => {
    chip.classList.add('is-fading');
    setTimeout(() => chip.remove(), 150);
  };

  setTimeout(hide, 1200);
}

export function showAlertDialog({
  title = 'Notice',
  message = '',
  tone = 'info',
  closeLabel = 'Close',
} = {}) {
  if (!hasDom()) return Promise.resolve(false);

  const bodyText = String(message || '').trim();
  if (!bodyText) return Promise.resolve(false);

  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'ui-choice-backdrop';

    const dialog = document.createElement('div');
    dialog.className = 'ui-choice-dialog';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-label', String(title || 'Notice'));

    const titleEl = document.createElement('h4');
    titleEl.className = 'ui-choice-title';
    titleEl.textContent = String(title || 'Notice');
    dialog.appendChild(titleEl);

    const messageEl = document.createElement('div');
    messageEl.className = `ui-choice-message ui-choice-message-${String(tone || 'info').trim() || 'info'}`;
    messageEl.textContent = bodyText;
    dialog.appendChild(messageEl);

    const actions = document.createElement('div');
    actions.className = 'ui-choice-actions';
    dialog.appendChild(actions);

    let releaseFocus;
    let settled = false;
    const finalize = (acknowledged) => {
      if (settled) return;
      settled = true;
      window.removeEventListener('keydown', onKeyDown);
      if (releaseFocus) releaseFocus();
      backdrop.remove();
      resolve(Boolean(acknowledged));
    };

    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        finalize(false);
      }
    };

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'ui-choice-btn';
    closeBtn.textContent = String(closeLabel || 'Close');
    closeBtn.addEventListener('click', () => finalize(true));
    actions.appendChild(closeBtn);

    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop) {
        finalize(false);
      }
    });

    window.addEventListener('keydown', onKeyDown);
    backdrop.appendChild(dialog);
    document.body.appendChild(backdrop);
    releaseFocus = trapFocus(dialog, { initialFocus: closeBtn });
  });
}

/**
 * Confirm a model switch to FREEFORM. Conversion is prepared inside the
 * dialog so its measured loss/rollback report is visible before state changes.
 */
export function showFreeformConversionDialog({ convertCurrentDesign } = {}) {
  if (!hasDom() || typeof convertCurrentDesign !== 'function') {
    return Promise.resolve({ action: 'cancel' });
  }

  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'ui-choice-backdrop';

    const dialog = document.createElement('div');
    dialog.className = 'ui-choice-dialog';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-label', 'Switch to FREEFORM');

    const title = document.createElement('h4');
    title.className = 'ui-choice-title';
    title.textContent = 'Switch to FREEFORM';
    dialog.appendChild(title);

    const subtitle = document.createElement('p');
    subtitle.className = 'ui-choice-subtitle';
    subtitle.textContent = 'Start blank or convert the current design into editable profiles.';
    dialog.appendChild(subtitle);

    const message = document.createElement('div');
    message.className = 'ui-choice-message';
    message.hidden = true;
    dialog.appendChild(message);

    const actions = document.createElement('div');
    actions.className = 'ui-choice-actions';
    dialog.appendChild(actions);

    let releaseFocus;
    let settled = false;
    let busy = false;
    const windowTarget = typeof window !== 'undefined' ? window : null;
    const finalize = (result) => {
      if (settled) return;
      settled = true;
      windowTarget?.removeEventListener?.('keydown', onKeyDown);
      releaseFocus?.();
      backdrop.remove();
      resolve(result);
    };
    const onKeyDown = (event) => {
      if (event.key === 'Escape' && !busy) {
        event.preventDefault();
        finalize({ action: 'cancel' });
      }
    };

    const makeButton = (label, help, className = '') => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `ui-choice-btn ${className}`.trim();
      const labelEl = document.createElement('span');
      labelEl.className = 'ui-choice-btn-label';
      labelEl.textContent = label;
      button.appendChild(labelEl);
      if (help) {
        const helpEl = document.createElement('span');
        helpEl.className = 'ui-choice-btn-help';
        helpEl.textContent = help;
        button.appendChild(helpEl);
      }
      return button;
    };

    const blankButton = makeButton('Start blank', 'Use the current FREEFORM defaults.');
    const convertButton = makeButton(
      'Convert current design',
      'Sample the current built surface and create editable H/V anchors.'
    );
    const cancelButton = makeButton('Cancel', '', 'secondary');
    actions.appendChild(blankButton);
    actions.appendChild(convertButton);
    actions.appendChild(cancelButton);

    blankButton.onclick = () => finalize({ action: 'blank' });
    cancelButton.onclick = () => finalize({ action: 'cancel' });
    convertButton.onclick = async () => {
      busy = true;
      for (const button of actions.children) button.disabled = true;
      message.hidden = false;
      message.className = 'ui-choice-message';
      message.textContent = 'Sampling current design…';
      try {
        const conversion = await convertCurrentDesign();
        message.textContent = conversion.summary;
        actions.innerHTML = '';
        const applyButton = makeButton(
          'Use converted design',
          'Replace the current design; Undo restores it.'
        );
        const backButton = makeButton('Cancel', '', 'secondary');
        applyButton.onclick = () => finalize({ action: 'convert', conversion });
        backButton.onclick = () => finalize({ action: 'cancel' });
        actions.appendChild(applyButton);
        actions.appendChild(backButton);
        busy = false;
        applyButton.focus?.();
      } catch (error) {
        message.className = 'ui-choice-message ui-choice-message-warning';
        message.textContent = error?.message || 'Could not convert the current design.';
        for (const button of actions.children) button.disabled = false;
        busy = false;
      }
    };

    backdrop.onclick = (event) => {
      if (event.target === backdrop && !busy) finalize({ action: 'cancel' });
    };
    windowTarget?.addEventListener?.('keydown', onKeyDown);
    backdrop.appendChild(dialog);
    document.body.appendChild(backdrop);
    releaseFocus = trapFocus(dialog, { initialFocus: convertButton });
  });
}

export function showCommandSuggestion({
  title = 'Command Suggestion',
  subtitle = '',
  command = '',
} = {}) {
  if (!hasDom()) return Promise.resolve(false);

  const commandText = String(command || '').trim();
  if (!commandText) return Promise.resolve(false);

  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'ui-choice-backdrop';

    const dialog = document.createElement('div');
    dialog.className = 'ui-choice-dialog';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-label', String(title || 'Command Suggestion'));

    const titleEl = document.createElement('h4');
    titleEl.className = 'ui-choice-title';
    titleEl.textContent = String(title || 'Command Suggestion');
    dialog.appendChild(titleEl);

    if (subtitle) {
      const subtitleEl = document.createElement('p');
      subtitleEl.className = 'ui-choice-subtitle';
      subtitleEl.textContent = String(subtitle);
      dialog.appendChild(subtitleEl);
    }

    const commandEl = document.createElement('pre');
    commandEl.className = 'ui-command-box';
    commandEl.textContent = commandText;
    dialog.appendChild(commandEl);

    const actions = document.createElement('div');
    actions.className = 'ui-choice-actions';
    dialog.appendChild(actions);

    let releaseFocus;
    let settled = false;
    const finalize = (copied) => {
      if (settled) return;
      settled = true;
      window.removeEventListener('keydown', onKeyDown);
      if (releaseFocus) releaseFocus();
      backdrop.remove();
      resolve(Boolean(copied));
    };

    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        finalize(false);
      }
    };

    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'ui-choice-btn';
    copyBtn.textContent = 'Copy Command';
    copyBtn.addEventListener('click', async () => {
      try {
        if (navigator?.clipboard?.writeText) {
          await navigator.clipboard.writeText(commandText);
          showSuccess('Copied update command to clipboard.');
          finalize(true);
          return;
        }
      } catch {
        // Fall through to user-facing error.
      }

      showError('Clipboard not available. Copy the command manually.');
      finalize(false);
    });
    actions.appendChild(copyBtn);

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'ui-choice-btn secondary';
    closeBtn.textContent = 'Close';
    closeBtn.addEventListener('click', () => finalize(false));
    actions.appendChild(closeBtn);

    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop) {
        finalize(false);
      }
    });

    window.addEventListener('keydown', onKeyDown);
    backdrop.appendChild(dialog);
    document.body.appendChild(backdrop);
    releaseFocus = trapFocus(dialog);
  });
}
