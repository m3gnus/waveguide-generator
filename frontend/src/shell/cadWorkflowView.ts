import type { FusionCadStatus } from '../api/cadlink';
import type { OnshapeStatus } from '../api/onshape';

/** Shared by the CAD Link panel, the rail card, and the coordinator's send
 * path, so every surface derives the same outbound action from one status. */
export interface CadWorkflowView {
  state: 'checking' | 'closed' | 'addin-offline' | 'no-document' | 'not-linked' | 'current' | 'stale' | 'not-configured';
  headline: string;
  detail: string;
  action: 'open' | 'update' | null;
}

/** The Onshape analogue of {@link fusionWorkflowView}.
 *
 * There is no process to detect and no add-in to hear from: Onshape is a web
 * service, so the only questions are whether WG has a key pair and whether the
 * design on screen still matches what was last sent. Both are answered from
 * WG's own registry, which is why this view never reports "checking".
 */
export function onshapeWorkflowView(status: OnshapeStatus | null): CadWorkflowView {
  if (status === null) return {
    state: 'checking',
    headline: 'Checking Onshape…',
    detail: 'WG is looking for an Onshape API key pair and any linked document.',
    action: null,
  };
  if (status.state === 'not_configured') return {
    state: 'not-configured',
    headline: 'Onshape is not connected',
    detail: `In Onshape, open My account → Developer → API keys, create a pair, and save it to ${status.credentials.credentialsPath}. WG never asks you to type it here.`,
    action: null,
  };
  if (status.state === 'not_linked') return {
    state: 'not-linked',
    headline: 'Not in Onshape yet',
    detail: 'WG will create an Onshape document, import this waveguide as a part, and add its managed parameters as a Variable Studio.',
    action: 'open',
  };
  const documentName = status.link?.documentName ?? 'the linked document';
  if (status.state === 'current') return {
    state: 'current',
    headline: `Onshape · ${documentName}`,
    detail: `Up to date. Sequence ${status.link?.lastSequence ?? '—'} is the imported part, and the managed WG parameters are synchronized.`,
    action: null,
  };
  return {
    state: 'stale',
    headline: `WG design changed · ${documentName}`,
    detail: `WG parameters changed after this Onshape part was built. Sending again replaces the imported part in place, so features you built on it in Onshape are kept.`,
    action: 'update',
  };
}

function explainedStaleDetail(status: FusionCadStatus, detail: string): string {
  const explanation = status.staleDetectionExplanation?.trim();
  return explanation ? `${detail} ${explanation}` : detail;
}

export function fusionWorkflowView(status: FusionCadStatus | null): CadWorkflowView {
  if (status === null) return {
    state: 'checking',
    headline: 'Checking Fusion 360…',
    detail: 'WG is looking for the WGLink add-in and its active document.',
    action: 'open',
  };
  if (status.cadFolderConfigured === false) return {
    state: 'not-configured',
    headline: 'Fusion connection needs a WGLink folder',
    detail: 'Choose the shared exchange folder in Settings → CAD Link. WG and the WGLink add-in will then use it automatically.',
    action: null,
  };
  if (status.cadConnectionIssue === 'addin_upgrade_required') return {
    state: 'addin-offline',
    headline: 'WGLink needs to be updated',
    detail: 'Fusion is running an older WGLink build that cannot confirm the selected exchange folder. Update the add-in, restart Fusion, and check again.',
    action: null,
  };
  if (status.cadConnectionIssue === 'folder_unreadable') return {
    state: 'addin-offline',
    headline: 'WGLink cannot access the selected folder',
    detail: 'Check that the folder still exists and that Fusion is running as the same user as WG. Then choose it again in Settings → CAD Link if needed.',
    action: null,
  };
  if (status.cadConnectionIssue === 'folder_mismatch') return {
    state: 'addin-offline',
    headline: 'WG and Fusion are using different folders',
    detail: 'WGLink reloads this setting automatically; it should clear within a few seconds. If it remains, update the add-in and restart Fusion.',
    action: null,
  };
  if (status.state === 'closed') return {
    state: 'closed',
    headline: 'Fusion 360 is closed',
    detail: 'Open this WG design in Fusion 360. WG will start Fusion and WGLink will create a Design document if needed.',
    action: 'open',
  };
  if (status.state === 'addin_offline') return {
    state: 'addin-offline',
    headline: 'Fusion 360 is open · WGLink add-in is offline',
    detail: 'Fusion is running, but its WGLink add-in has not reported in. In Fusion, open Utilities → Scripts and Add-Ins, then stop and run WGLink.',
    action: null,
  };
  if (status.state === 'no_document') return {
    state: 'no-document',
    headline: 'Fusion 360 is open · no Design document',
    detail: 'WGLink will create a Design document and insert this waveguide.',
    action: 'open',
  };
  if (status.state === 'not_linked') return {
    state: 'not-linked',
    headline: `Fusion 360 is open${status.documentName ? ` · ${status.documentName}` : ''}`,
    detail: 'This WG design is not linked in the active Fusion document yet.',
    action: 'open',
  };
  const parameterCopy = status.link?.parameterCount
    ? `${status.link.parameterCount} managed CAD parameters`
    : 'the managed CAD parameters';
  if (status.state === 'current') return {
    state: 'current',
    headline: `Fusion 360 is open · ${status.documentName ?? 'active document'}`,
    detail: `Up to date. The full WG config and ${parameterCopy} are synchronized.`,
    action: null,
  };
  if (!status.wgChangesAvailable && status.fusionChangesAvailable) return {
    state: 'stale',
    headline: `Fusion geometry has changed${status.documentName ? ` · ${status.documentName}` : ''}`,
    detail: explainedStaleDetail(status, 'The parametric WG design has not changed. Bring the Fusion geometry into WG before rebuilding the Fusion waveguide from WG.'),
    action: null,
  };
  if (!status.wgChangesAvailable) return {
    state: 'stale',
    headline: `Fusion has local geometry changes${status.documentName ? ` · ${status.documentName}` : ''}`,
    detail: explainedStaleDetail(status, 'The parametric WG design matches Fusion. The latest Fusion geometry has already been returned to WG for simulation.'),
    action: null,
  };
  const fusionFormula = status.fusionFormula?.toLocaleUpperCase();
  const currentFormula = status.currentFormula.toLocaleUpperCase();
  const mismatch = fusionFormula && fusionFormula !== currentFormula
    ? `Fusion has ${fusionFormula}; WG is now ${currentFormula}.`
    : 'WG parameters changed after this Fusion waveguide was built.';
  const configCopy = status.link?.configPresent
    ? ''
    : ' This link also predates full WG config synchronization.';
  const localEditCopy = status.link?.parameterDriftCount
    ? ` ${status.link.parameterDriftCount} managed Fusion parameter${status.link.parameterDriftCount === 1 ? ' has' : 's have'} local edits.`
    : status.link?.localBodyState && status.link.localBodyState !== 'unmodified'
      ? ` The managed Fusion body is ${status.link.localBodyState}.`
      : '';
  const conflictCopy = status.fusionChangesAvailable
    ? ' Fusion geometry also changed; choose which direction to synchronize.'
    : '';
  return {
    state: 'stale',
    headline: `${status.fusionChangesAvailable ? 'WG and Fusion both changed' : 'WG design changed'}${status.documentName ? ` · ${status.documentName}` : ''}`,
    detail: explainedStaleDetail(status, `${mismatch}${configCopy}${localEditCopy}${conflictCopy}`),
    action: 'update',
  };
}
