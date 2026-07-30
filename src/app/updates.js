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

export function getInstallUpdateCommand(platform = '') {
  const normalized = String(platform || '').toLowerCase();
  return normalized.includes('win')
    ? 'install\\install-and-update.bat'
    : 'bash install/install-and-update.sh';
}

function clientPlatform() {
  return globalThis.navigator?.userAgentData?.platform || globalThis.navigator?.platform || '';
}

/**
 * Check for available application updates via the backend `/api/updates/check` endpoint.
 *
 * @param {HTMLElement|null} [buttonEl] - Optional reference to the triggering button element.
 *   When provided, the button is disabled and its label updated while the request is in flight.
 *   Falls back to `document.getElementById('check-updates-btn')` for backwards compatibility.
 */
export async function checkForUpdates(buttonEl, ui = {}) {
  const button = buttonEl ?? globalThis.document?.getElementById('check-updates-btn') ?? null;
  const originalLabel = button?.textContent || 'Check for App Updates';

  if (button) {
    button.disabled = true;
    button.textContent = 'Checking...';
  }

  try {
    const response = await fetch(`${DEFAULT_BACKEND_URL}/api/updates/check`, { method: 'POST' });
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
    const upstreamRef = String(payload?.upstreamRef || 'upstream');
    const localSha = shortCommit(payload?.currentCommit);
    const remoteSha = shortCommit(payload?.remoteCommit);

    if (behind > 0 && ahead > 0) {
      ui.showMessage?.(
        `This checkout has diverged from ${upstreamRef} (${ahead} ahead, ${behind} behind). ` +
          'Resolve the Git branch manually before updating.',
        { type: 'warning', duration: 8000 }
      );
      return;
    }

    if (behind > 0) {
      const updateCommand = getInstallUpdateCommand(clientPlatform());
      const copied =
        (await ui.showCommandSuggestion?.({
          title: 'Update Available',
          subtitle:
            `${behind} commit(s) behind ${upstreamRef} (${localSha} -> ${remoteSha}). ` +
            'Stop the running app, then run the full updater.',
          command: updateCommand,
        })) ?? false;

      if (!copied) {
        ui.showMessage?.(`Stop the app, then run: ${updateCommand}`, {
          type: 'info',
          duration: 8000,
        });
      }
      return;
    }

    if (ahead > 0) {
      ui.showMessage?.(
        `Local branch is ${ahead} commit(s) ahead of ${upstreamRef} (${localSha}).`,
        { type: 'info', duration: 4200 }
      );
      return;
    }

    ui.showSuccess?.(`Up to date with ${upstreamRef} (${localSha}).`);
  } catch (error) {
    ui.showError?.(`Update check failed: ${error?.message || 'Unknown error'}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  }
}
