export type UpdateAvailability = 'available' | 'current' | 'ahead' | 'incomplete' | 'unknown';
/**
 * Which releases this installation is offered.
 *
 * `stable` follows GitHub's `releases/latest`, which excludes pre-releases;
 * `beta` also sees them, so packaging and Windows problems surface before a
 * stable version number is spent on them.
 */
export type UpdateChannel = 'stable' | 'beta';
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

export interface BundleReleaseAsset {
  name: string;
  url: string;
  sha256Url: string;
  bytes: number;
  sha256Bytes: number;
  layer: 'app' | 'runtime' | 'manifest' | 'installer';
}

export interface CheckoutUpdateRelease {
  version: string;
  tag: string;
  url: string;
  publishedAt: string | null;
  assetsReady: boolean;
}

export interface BundleUpdateRelease extends CheckoutUpdateRelease {
  runtimeId: string;
  bundleAssets: BundleReleaseAsset[];
}

export type UpdateRelease = CheckoutUpdateRelease | BundleUpdateRelease;

interface CheckoutStatusBase {
  branch: string | null;
  head: string | null;
  atDeclaredTag: boolean;
  trackedChanges: boolean;
  aheadCount: number | null;
  behindCount: number | null;
  updateSupported: boolean;
  reason: string | null;
}

export interface RepositoryCheckoutStatus extends CheckoutStatusBase {
  kind: Exclude<CheckoutKind, 'bundle'>;
}

export interface BundleCheckoutStatus extends CheckoutStatusBase {
  kind: 'bundle';
  installedVersion: string;
  runtimeId: string | null;
}

export type CheckoutStatus = RepositoryCheckoutStatus | BundleCheckoutStatus;

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
  channel: UpdateChannel;
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
  activeVersion: string | null;
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
  activeVersion: string;
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

function isAvailability(value: unknown): value is UpdateAvailability {
  return value === 'available' || value === 'current' || value === 'ahead' || value === 'incomplete' || value === 'unknown';
}

function isFreshness(value: unknown): value is UpdateFreshness {
  return value === 'fresh' || value === 'stale' || value === 'unknown';
}

function isCheckoutKind(value: unknown): value is CheckoutKind {
  return value === 'release' || value === 'development' || value === 'detached' || value === 'modified' || value === 'unsupported' || value === 'bundle';
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

/**
 * Three numbers, optionally followed by a SemVer pre-release label.
 *
 * Widened for the beta channel, and it has to be: a beta install's whole status
 * payload -- release, running version, the version being downloaded -- carries
 * `0.4.0-beta.1`, and the narrow shape would have rejected the response
 * wholesale rather than any one field. This mirrors `TAG_RE` on the server;
 * build metadata (`+sha`) is not accepted there either.
 */
function isVersion(value: unknown): value is string {
  return typeof value === 'string'
    && /^\d+\.\d+\.\d+(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/.test(value);
}

function isChannel(value: unknown): value is UpdateChannel {
  return value === 'stable' || value === 'beta';
}

function isRuntimeId(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{12}$/.test(value);
}

function isNonNegativeNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

function isPositiveInteger(value: unknown): value is number {
  return isNonNegativeNumber(value) && Number.isInteger(value) && value > 0;
}

function isNullableNonNegativeInteger(value: unknown): value is number | null {
  return value === null || (isNonNegativeNumber(value) && Number.isInteger(value));
}

function isBundleReleaseAsset(value: unknown): value is BundleReleaseAsset {
  return isRecord(value)
    && typeof value.name === 'string'
    && typeof value.url === 'string'
    && typeof value.sha256Url === 'string'
    && isPositiveInteger(value.bytes)
    && isPositiveInteger(value.sha256Bytes)
    && (value.layer === 'app' || value.layer === 'runtime' || value.layer === 'manifest' || value.layer === 'installer');
}

function isUpdateRelease(value: unknown): value is UpdateRelease {
  if (!isRecord(value)
    || !isVersion(value.version)
    || value.tag !== `v${value.version}`
    || typeof value.url !== 'string'
    || !isNullableString(value.publishedAt)
    || typeof value.assetsReady !== 'boolean') return false;
  const hasRuntimeId = Object.hasOwn(value, 'runtimeId');
  const hasBundleAssets = Object.hasOwn(value, 'bundleAssets');
  if (hasRuntimeId !== hasBundleAssets) return false;
  return !hasRuntimeId || (
    isRuntimeId(value.runtimeId)
    && Array.isArray(value.bundleAssets)
    && value.bundleAssets.every(isBundleReleaseAsset)
  );
}

function isCheckoutStatus(value: unknown): value is CheckoutStatus {
  if (!isRecord(value)
    || !isCheckoutKind(value.kind)
    || !isNullableString(value.branch)
    || !isNullableString(value.head)
    || typeof value.atDeclaredTag !== 'boolean'
    || typeof value.trackedChanges !== 'boolean'
    || !isNullableNonNegativeInteger(value.aheadCount)
    || !isNullableNonNegativeInteger(value.behindCount)
    || typeof value.updateSupported !== 'boolean'
    || !isNullableString(value.reason)) return false;
  if (value.kind !== 'bundle') {
    return !Object.hasOwn(value, 'installedVersion') && !Object.hasOwn(value, 'runtimeId');
  }
  return isVersion(value.installedVersion)
    && (value.runtimeId === null || isRuntimeId(value.runtimeId));
}

function isBundleUpdateAsset(value: unknown): value is BundleUpdateAsset {
  return isRecord(value)
    && typeof value.name === 'string'
    && typeof value.url === 'string'
    && typeof value.sha256Url === 'string'
    && isPositiveInteger(value.bytes)
    && (value.layer === 'app' || value.layer === 'runtime');
}

function isUpdateAction(value: unknown): value is UpdateAction | null {
  if (value === null) return true;
  if (!isRecord(value)) return false;
  if (value.kind === 'copy_command') {
    return typeof value.shell === 'string' && typeof value.command === 'string';
  }
  if (value.kind === 'bundle_download') {
    if (!Array.isArray(value.assets) || !value.assets.every(isBundleUpdateAsset)) return false;
    return value.assets.some((asset) => asset.layer === 'app')
      && isNonNegativeNumber(value.downloadBytes)
      && Number.isInteger(value.downloadBytes)
      && value.downloadBytes === value.assets.reduce((total, asset) => total + asset.bytes, 0);
  }
  return false;
}

function isUpdateStatus(value: unknown): value is UpdateStatus {
  return isRecord(value)
    && value.schemaVersion === 1
    && typeof value.runningVersion === 'string'
    && isChannel(value.channel)
    && isAvailability(value.availability)
    && isFreshness(value.freshness)
    && typeof value.cached === 'boolean'
    && (value.release === null || isUpdateRelease(value.release))
    && isNullableString(value.checkedAt)
    && isNullableString(value.nextCheckAt)
    && isCheckoutStatus(value.checkout)
    && isUpdateAction(value.action)
    && typeof value.canInstall === 'boolean'
    && isNullableString(value.lastError)
    && isInstallState(value.installState)
    && (value.installState === 'idle' ? value.activeVersion === null : isVersion(value.activeVersion))
    && isNonNegativeNumber(value.downloadedBytes)
    && isNonNegativeNumber(value.totalBytes)
    && value.downloadedBytes <= value.totalBytes
    && isNullableString(value.error);
}

export async function getUpdateStatus(refresh = false, signal?: AbortSignal): Promise<UpdateStatus> {
  const url = `/api/updates/status${refresh ? '?refresh=true' : ''}`;
  const response = signal ? await fetch(url, { signal }) : await fetch(url);
  if (!response.ok) throw new Error(`Update status request failed (${response.status})`);
  const payload: unknown = await response.json();
  if (!isUpdateStatus(payload)) {
    throw new Error('Update status response is invalid');
  }
  return payload;
}

export async function getUpdateChannel(signal?: AbortSignal): Promise<UpdateChannel> {
  const response = signal ? await fetch('/api/updates/channel', { signal }) : await fetch('/api/updates/channel');
  if (!response.ok) throw new Error(`Update channel request failed (${response.status})`);
  const payload: unknown = await response.json();
  if (!isRecord(payload) || !isChannel(payload.channel)) {
    throw new Error('Update channel response is invalid');
  }
  return payload.channel;
}

export async function setUpdateChannel(channel: UpdateChannel): Promise<UpdateChannel> {
  const response = await fetch('/api/updates/channel', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel }),
  });
  if (!response.ok) {
    let detail = `Update channel could not be saved (${response.status})`;
    try {
      const payload: unknown = await response.json();
      if (isRecord(payload) && typeof payload.detail === 'string') detail = payload.detail;
    } catch {
      // Keep the status-based fallback when the response is not JSON.
    }
    throw new Error(detail);
  }
  const payload: unknown = await response.json();
  if (!isRecord(payload) || !isChannel(payload.channel)) {
    throw new Error('Update channel response is invalid');
  }
  return payload.channel;
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
    && isVersion(payload.version)
    && isInstallState(payload.installState)
    && payload.activeVersion === payload.version
    && isNonNegativeNumber(payload.downloadedBytes)
    && isNonNegativeNumber(payload.totalBytes)
    && payload.downloadedBytes <= payload.totalBytes
    && isNullableString(payload.error);
  if (!checkoutAccepted && !bundleAccepted) {
    throw new Error('Update installation response is invalid');
  }
  return payload as unknown as UpdateInstallAccepted;
}
