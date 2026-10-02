/** Full-installer update contract: server/updates/installer_client.py. */
export type UpdateChannel = 'stable' | 'beta';
export type UpdateInstallState = 'idle' | 'downloading' | 'verifying' | 'ready' | 'failed';
export interface UpdateOutcome {
  from: string; to: string; when: string; log: string;
  result: 'installed' | 'failed' | 'rollback_incomplete';
  previousKept?: boolean; backupPath?: string | null;
}
export interface UpdateStatus {
  schemaVersion: 2;
  runningVersion: string;
  channel: UpdateChannel;
  availability: 'unknown' | 'incomplete' | 'available' | 'current' | 'ahead';
  freshness: 'fresh' | 'stale' | 'unknown';
  cached: boolean; checking: boolean;
  checkedAt: string | null; nextCheckAt: string | null; lastError: string | null;
  release: {
    version: string; tag: string; url: string; publishedAt: string | null; notes: string; assetsReady: boolean;
    installer: { name: string; url: string; size: number; sha256: string } | null;
  } | null;
  checkout: {
    kind: 'macos' | 'windows' | 'linux' | 'source' | 'portable' | 'unsupported';
    installRoot: string | null; updateSupported: boolean; reason: string | null;
  };
  action: { kind: 'full_installer'; version: string; tag: string; name: string; size: number } | null;
  canInstall: boolean;
  installState: UpdateInstallState;
  activeVersion: string | null; downloadedBytes: number; totalBytes: number; error: string | null;
  lastOutcome: UpdateOutcome | null;
}
export interface UpdateInstallAccepted {
  accepted: true; version: string; installState: UpdateInstallState; activeVersion: string;
  downloadedBytes: number; totalBytes: number; error: string | null;
}
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function nullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}
function bytes(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}
function version(value: unknown): value is string {
  return typeof value === 'string' && /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/.test(value);
}
function channel(value: unknown): value is UpdateChannel {
  return value === 'stable' || value === 'beta';
}
function installState(value: unknown): value is UpdateInstallState {
  return typeof value === 'string' && ['idle', 'downloading', 'verifying', 'ready', 'failed'].includes(value);
}
function outcome(value: unknown): value is UpdateOutcome | null {
  return value === null || (record(value) && typeof value.from === 'string' && typeof value.to === 'string'
    && typeof value.when === 'string' && typeof value.log === 'string'
    && typeof value.result === 'string' && ['installed', 'failed', 'rollback_incomplete'].includes(value.result)
    && (value.previousKept === undefined || typeof value.previousKept === 'boolean')
    && (value.backupPath === undefined || nullableString(value.backupPath)));
}
function status(value: unknown): value is UpdateStatus {
  if (!record(value) || value.schemaVersion !== 2 || typeof value.runningVersion !== 'string' || !channel(value.channel)
    || typeof value.availability !== 'string' || !['unknown', 'incomplete', 'available', 'current', 'ahead'].includes(value.availability)
    || typeof value.freshness !== 'string' || !['fresh', 'stale', 'unknown'].includes(value.freshness)
    || typeof value.cached !== 'boolean' || typeof value.checking !== 'boolean'
    || !nullableString(value.checkedAt) || !nullableString(value.nextCheckAt) || !nullableString(value.lastError)
    || !record(value.checkout) || typeof value.checkout.kind !== 'string'
    || !['macos', 'windows', 'linux', 'source', 'portable', 'unsupported'].includes(value.checkout.kind)
    || !nullableString(value.checkout.installRoot) || typeof value.checkout.updateSupported !== 'boolean'
    || !nullableString(value.checkout.reason) || typeof value.canInstall !== 'boolean'
    || !installState(value.installState) || !nullableString(value.error)
    || !bytes(value.downloadedBytes) || !bytes(value.totalBytes) || value.downloadedBytes > value.totalBytes
    || !outcome(value.lastOutcome)) return false;
  if (value.installState === 'idle' ? value.activeVersion !== null
    : value.installState === 'failed' ? value.activeVersion !== null && !version(value.activeVersion)
      : !version(value.activeVersion)) return false;
  if (value.release !== null) {
    const release = value.release;
    if (!record(release) || !version(release.version) || release.tag !== `v${release.version}`
      || release.url !== `https://github.com/m3gnus/waveguide-generator/releases/tag/${release.tag}`
      || !nullableString(release.publishedAt) || typeof release.notes !== 'string' || typeof release.assetsReady !== 'boolean') return false;
    if (release.installer !== null && (!record(release.installer) || typeof release.installer.name !== 'string'
      || typeof release.installer.url !== 'string' || !bytes(release.installer.size) || release.installer.size === 0
      || typeof release.installer.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(release.installer.sha256))) return false;
  }
  if (['available', 'current', 'ahead'].includes(value.availability) && value.release === null) return false;
  if (value.action !== null) {
    const action = value.action;
    if (!record(action) || action.kind !== 'full_installer' || !version(action.version) || action.tag !== `v${action.version}`
      || typeof action.name !== 'string' || !bytes(action.size) || !record(value.release) || !record(value.release.installer)
      || action.tag !== value.release.tag || action.name !== value.release.installer.name
      || action.size !== value.release.installer.size || value.release.assetsReady !== true) return false;
  }
  return !value.canInstall || (value.action !== null && value.checkout.updateSupported === true && value.availability === 'available');
}
async function responseError(response: Response, fallback: string): Promise<Error> {
  try {
    const payload: unknown = await response.json();
    if (record(payload) && typeof payload.detail === 'string') return new Error(payload.detail);
    if (record(payload) && record(payload.error) && typeof payload.error.message === 'string') return new Error(payload.error.message);
  } catch { /* Use the HTTP result when no JSON detail exists. */ }
  return new Error(`${fallback} (${response.status})`);
}
export async function getUpdateStatus(refresh = false, signal?: AbortSignal): Promise<UpdateStatus> {
  const response = await fetch(`/api/updates/status${refresh ? '?refresh=true' : ''}`, { signal });
  if (!response.ok) throw await responseError(response, 'Update status request failed');
  const payload: unknown = await response.json();
  if (!status(payload)) throw new Error('Update status response is invalid');
  return payload;
}
export async function getUpdateChannel(signal?: AbortSignal): Promise<UpdateChannel> {
  const response = await fetch('/api/updates/channel', { signal });
  if (!response.ok) throw await responseError(response, 'Update channel request failed');
  const payload: unknown = await response.json();
  if (!record(payload) || !channel(payload.channel)) throw new Error('Update channel response is invalid');
  return payload.channel;
}
export async function setUpdateChannel(next: UpdateChannel): Promise<UpdateChannel> {
  const response = await fetch('/api/updates/channel', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ channel: next }),
  });
  if (!response.ok) throw await responseError(response, 'Update channel could not be saved');
  const payload: unknown = await response.json();
  if (!record(payload) || !channel(payload.channel)) throw new Error('Update channel response is invalid');
  return payload.channel;
}
export async function installApplicationUpdate(): Promise<UpdateInstallAccepted> {
  const response = await fetch('/api/updates/install', { method: 'POST', headers: { 'X-WG-Update': 'install' } });
  if (!response.ok) throw await responseError(response, 'Update installation request failed');
  const payload: unknown = await response.json();
  if (!record(payload) || payload.accepted !== true || !version(payload.version) || !installState(payload.installState)
    || payload.activeVersion !== payload.version || !bytes(payload.downloadedBytes) || !bytes(payload.totalBytes)
    || payload.downloadedBytes > payload.totalBytes || !nullableString(payload.error)) throw new Error('Update installation response is invalid');
  return payload as unknown as UpdateInstallAccepted;
}
