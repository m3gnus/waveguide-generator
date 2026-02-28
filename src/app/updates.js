import { showCommandSuggestion, showError, showMessage, showSuccess } from '../ui/feedback.js';
import { DEFAULT_BACKEND_URL } from '../config/backendUrl.js';

function shortCommit(sha) {
  const text = String(sha || '').trim();
  return text ? text.slice(0, 7) : 'unknown';
}

function parseErrorDetail(payload) {
  if (!payload) return '';
  if (typeof payload === 'string') return payload;
  if (typeof payload.detail === 'string') return payload.detail;
  return '';
}

/**
 * Check for available application updates via the backend `/api/updates/check` endpoint.
 *
 * @param {HTMLElement|null} [buttonEl] - Optional reference to the triggering button element.
 *   When provided, the button is disabled and its label updated while the request is in flight.
 *   Falls back to `document.getElementById('check-updates-btn')` for backwards compatibility.
 */
export async function checkForUpdates(buttonEl) {
  const button = buttonEl ?? document.getElementById('check-updates-btn');
  const originalLabel = button?.textContent || 'Check for App Updates';

  if (button) {
    button.disabled = true;
    button.textContent = 'Checking...';
  }

  try {
    const response = await fetch(`${DEFAULT_BACKEND_URL}/api/updates/check`);
    let payload = null;

    try {
      payload = await response.json();
    } catch {
      payload = null;
    }

    if (!response.ok) {
      const detail = parseErrorDetail(payload) || `HTTP ${response.status}`;
      throw new Error(detail);
    }

    const behind = Number(payload?.behindCount || 0);
    const ahead = Number(payload?.aheadCount || 0);
    const branch = String(payload?.defaultBranch || 'main');
    const localSha = shortCommit(payload?.currentCommit);
    const remoteSha = shortCommit(payload?.remoteCommit);

    if (behind > 0) {
      const pullCommand = `git pull --ff-only origin ${branch}`;
      const copied = await showCommandSuggestion({
        title: 'Update Available',
        subtitle: `${behind} commit(s) behind origin/${branch} (${localSha} -> ${remoteSha}).`,
        command: pullCommand
      });

      if (!copied) {
        showMessage(`Run in terminal: ${pullCommand}`, { type: 'info', duration: 7000 });
      }
      return;
    }

    if (ahead > 0) {
      showMessage(
        `Local branch is ${ahead} commit(s) ahead of origin/${branch} (${localSha}).`,
        { type: 'info', duration: 4200 }
      );
      return;
    }

    showSuccess(`Up to date with origin/${branch} (${localSha}).`);
  } catch (error) {
    showError(`Update check failed: ${error?.message || 'Unknown error'}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  }
}
