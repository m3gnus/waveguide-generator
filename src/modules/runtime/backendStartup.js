export async function fetchBackendStartupStatus({ fetchImpl = fetch } = {}) {
  const response = await fetchImpl('/api/backend-startup-status', { cache: 'no-store' });
  if (!response.ok) {
    throw new Error('Backend startup status fetch failed.');
  }
  const status = await response.json();
  return status && typeof status === 'object' ? status : null;
}

export function describeBackendStartupStatus(status) {
  const state = String(status?.state || 'unknown').toLowerCase();
  const reason = String(status?.reason || '').trim();
  const guidance = String(status?.guidance || '').trim();
  const help = [reason, guidance].filter(Boolean).join(' ');

  if (state === 'starting') {
    return {
      label: 'Backend starting…',
      help: help || 'The Python backend process is starting.',
    };
  }
  if (state === 'error') {
    return {
      label: 'Backend failed to start',
      help: help || 'The backend process exited before it became reachable.',
    };
  }
  if (state === 'stopped') {
    return {
      label: 'Backend stopped',
      help: help || 'The backend process is no longer running.',
    };
  }
  return {
    label: 'Solver offline',
    help: help || 'The Python backend is not reachable on localhost:8000.',
  };
}
