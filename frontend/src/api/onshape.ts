import { serializeDesign, type DesignDocument } from '../stores/design';
import { designWireWithAthPolars } from '../stores/athPolars';
import type { DesignIdentity } from '../stores/document';
import type { WgLinkExportResponse } from './designIo';
import type { CadReturnIngestRecord } from './cadlink';

export type OnshapeState = 'not_configured' | 'not_linked' | 'current' | 'stale';

export interface OnshapeCredentialsState {
  configured: boolean;
  credentialsPath: string;
  detail: string | null;
  /** The key file is readable by other accounts on this machine. */
  insecureKeyFile: boolean;
}

export interface OnshapeLink {
  designId: string;
  accountId: string;
  documentId: string;
  workspaceId: string;
  documentName: string;
  documentUrl: string | null;
  isPublic: boolean;
  partStudioElementId: string | null;
  variableStudioElementId: string | null;
  featureStudioElementId: string | null;
  nativeFeatureId: string | null;
  datumFeatureStudioElementId: string | null;
  datumFeatureId: string | null;
  buildMode: 'import' | 'native';
  lastSequence: number | null;
  updatedAt: string;
}

export interface OnshapeStatus {
  state: OnshapeState;
  credentials: OnshapeCredentialsState;
  link: OnshapeLink | null;
  wgChangesAvailable: boolean;
  currentFormula: string;
}

export interface OnshapeConnection extends OnshapeCredentialsState {
  reachable: boolean;
  account: { id: string | null; name: string | null } | null;
  plan: { name: string | null; group: string | null; publicOnly: boolean | null } | null;
  rateLimitRemaining?: number | null;
}

export interface OnshapeSendResult extends WgLinkExportResponse {
  onshape: {
    documentId: string;
    workspaceId: string;
    documentName: string;
    documentUrl: string;
    createdDocument: boolean;
    isPublic: boolean;
    variablesPushed: number;
    partNames: string[];
    accountId: string;
  };
}

export interface OnshapeReturnResult {
  translationId: string;
  bundle: {
    name: string;
    bundlePath: string;
    documentName: string | null;
    sourceCount: number;
    instanceCount: number;
  };
  ingest: CadReturnIngestRecord;
}

/** Raised when Onshape can only create world-readable documents and the user
 * has not yet said that is acceptable. The panel turns this into a confirm. */
export class OnshapePublicConsentRequired extends Error {}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: string | { message?: string } };
    if (typeof body.detail === 'string') return body.detail;
    if (body.detail?.message) return body.detail.message;
  } catch { /* the status text remains useful */ }
  return `Request failed: ${response.status} ${response.statusText}`.trim();
}

export async function getOnshapeConnection(
  refresh = false,
  fetcher: typeof fetch = fetch,
): Promise<OnshapeConnection> {
  const response = await fetcher(`/api/cadlink/onshape/connection${refresh ? '?refresh=true' : ''}`);
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<OnshapeConnection>;
}

/** Cheap and local: the server answers this from its own registry without
 * calling Onshape, which is what makes it safe for the panel to poll. */
export async function getOnshapeStatus(
  design: DesignDocument,
  identity: DesignIdentity | null,
  fetcher: typeof fetch = fetch,
): Promise<OnshapeStatus> {
  const response = await fetcher('/api/cadlink/onshape/status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ design: serializeDesign(design), identity }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<OnshapeStatus>;
}

export async function sendDesignToOnshape(
  design: DesignDocument,
  designRevision: number,
  baseName: string,
  identity: DesignIdentity | null,
  options: { allowPublic?: boolean; polarConfig?: unknown } = {},
  fetcher: typeof fetch = fetch,
  idempotencyKey: string = globalThis.crypto?.randomUUID?.()
    ?? `onshape-${Date.now()}-${Math.random().toString(16).slice(2)}`,
): Promise<OnshapeSendResult> {
  // Unlike the Fusion leg there is no workspace folder to choose: the bundle
  // is written under WG's own data directory and uploaded from there.
  const response = await fetcher('/api/cadlink/onshape/send', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({
      design: designWireWithAthPolars(serializeDesign(design), options.polarConfig),
      designRevision,
      baseName,
      identity,
      allowPublic: options.allowPublic === true,
    }),
  });
  if (response.status === 428) throw new OnshapePublicConsentRequired(await errorMessage(response));
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<OnshapeSendResult>;
}

export async function unlinkOnshapeDocument(
  designId: string,
  fetcher: typeof fetch = fetch,
): Promise<{ unlinked: boolean }> {
  const response = await fetcher('/api/cadlink/onshape/unlink', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ designId }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<{ unlinked: boolean }>;
}

export async function returnOnshapeToWg(
  designId: string,
  fetcher: typeof fetch = fetch,
): Promise<OnshapeReturnResult> {
  const response = await fetcher('/api/cadlink/onshape/return', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ designId }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<OnshapeReturnResult>;
}
