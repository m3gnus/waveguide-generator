import { showCommandSuggestion, showError, showMessage, showSuccess } from '../ui/feedback.js';

const DEFAULT_BACKEND_URL = 'http://localhost:8000';

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

export async function checkForUpdates() {
  const button = document.getElementById('check-updates-btn');
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
