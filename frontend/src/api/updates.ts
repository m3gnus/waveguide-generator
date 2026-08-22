export type UpdateAvailability = 'available' | 'current' | 'ahead' | 'incomplete' | 'unknown';
export type UpdateFreshness = 'fresh' | 'stale' | 'unknown';
export type CheckoutKind = 'release' | 'development' | 'detached' | 'modified' | 'unsupported' | 'bundle';
export type UpdateInstallState = 'idle' | 'downloading' | 'verifying' | 'ready' | 'failed';

export interface BundleUpdateAsset {
  name: string;
  url: string;
  sha256Url: string;
  bytes: number;
  layer: 'app' | 'runtime';
}

export interface UpdateRelease {
  version: string;
  tag: string;
  url: string;
  publishedAt: string | null;
  assetsReady: boolean;
  runtimeId?: string;
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
  installedVersion?: string;
  runtimeId?: string | null;
}

export interface CopyCommandUpdateAction {
  kind: 'copy_command';
  shell: string;
  command: string;
}

export interface BundleDownloadUpdateAction {
  kind: 'bundle_download';
  assets: BundleUpdateAsset[];
  downloadBytes: number;
}

export type UpdateAction = CopyCommandUpdateAction | BundleDownloadUpdateAction;

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
  installState: UpdateInstallState;
  downloadedBytes: number;
  totalBytes: number;
  error: string | null;
}

export interface CheckoutUpdateInstallAccepted {
  accepted: true;
  tag: string;
}

export interface BundleUpdateInstallAccepted {
  accepted: true;
  version: string;
  installState: UpdateInstallState;
  downloadedBytes: number;
  totalBytes: number;
  error: string | null;
}

export type UpdateInstallAccepted = CheckoutUpdateInstallAccepted | BundleUpdateInstallAccepted;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isInstallState(value: unknown): value is UpdateInstallState {
  return value === 'idle' || value === 'downloading' || value === 'verifying' || value === 'ready' || value === 'failed';
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
    || !isInstallState(payload.installState)
    || typeof payload.downloadedBytes !== 'number'
    || typeof payload.totalBytes !== 'number'
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
  const checkoutAccepted = isRecord(payload) && payload.accepted === true && typeof payload.tag === 'string';
  const bundleAccepted = isRecord(payload)
    && payload.accepted === true
    && typeof payload.version === 'string'
    && isInstallState(payload.installState)
    && typeof payload.downloadedBytes === 'number'
    && typeof payload.totalBytes === 'number';
  if (!checkoutAccepted && !bundleAccepted) {
    throw new Error('Update installation response is invalid');
  }
  return payload as unknown as UpdateInstallAccepted;
}
