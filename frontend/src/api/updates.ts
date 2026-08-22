export type UpdateAvailability = 'available' | 'current' | 'ahead' | 'incomplete' | 'unknown';
export type UpdateFreshness = 'fresh' | 'stale' | 'unknown';
export type CheckoutKind = 'release' | 'development' | 'detached' | 'modified' | 'unsupported' | 'bundle';

export interface UpdateRelease {
  version: string;
  tag: string;
  url: string;
  publishedAt: string | null;
  assetsReady: boolean;
}

export interface CheckoutStatus {
  kind: CheckoutKind;
  branch: string | null;
  head: string | null;
  atDeclaredTag: boolean;
  trackedChanges: boolean;
  aheadCount: number | null;
  behindCount: number | null;
  updateSupported: boolean;
  reason: string | null;
}

export interface UpdateAction {
  kind: 'copy_command';
  shell: string;
  command: string;
}

export interface UpdateStatus {
  schemaVersion: 1;
  runningVersion: string;
  availability: UpdateAvailability;
  freshness: UpdateFreshness;
  cached: boolean;
  release: UpdateRelease | null;
  checkedAt: string | null;
  nextCheckAt: string | null;
  checkout: CheckoutStatus;
  action: UpdateAction | null;
  canInstall: boolean;
  lastError: string | null;
}

export interface UpdateInstallAccepted {
  accepted: true;
  tag: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

export async function getUpdateStatus(refresh = false): Promise<UpdateStatus> {
  const response = await fetch(`/api/updates/status${refresh ? '?refresh=true' : ''}`);
  if (!response.ok) throw new Error(`Update status request failed (${response.status})`);
  const payload: unknown = await response.json();
  if (
    !isRecord(payload)
    || payload.schemaVersion !== 1
    || typeof payload.runningVersion !== 'string'
    || !isRecord(payload.checkout)
    || typeof payload.checkout.kind !== 'string'
    || typeof payload.canInstall !== 'boolean'
  ) {
    throw new Error('Update status response is invalid');
  }
  return payload as unknown as UpdateStatus;
}

export async function installApplicationUpdate(): Promise<UpdateInstallAccepted> {
  const response = await fetch('/api/updates/install', {
    method: 'POST',
    headers: { 'X-WG-Update': 'install' },
  });
  if (!response.ok) {
    let detail = `Update installation request failed (${response.status})`;
    try {
      const payload: unknown = await response.json();
      if (isRecord(payload) && typeof payload.detail === 'string') detail = payload.detail;
    } catch {
      // Keep the status-based fallback when the response is not JSON.
    }
    throw new Error(detail);
  }
  const payload: unknown = await response.json();
  if (!isRecord(payload) || payload.accepted !== true || typeof payload.tag !== 'string') {
    throw new Error('Update installation response is invalid');
  }
  return payload as unknown as UpdateInstallAccepted;
}
