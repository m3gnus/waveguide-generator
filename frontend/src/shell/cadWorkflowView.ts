import type { FusionCadStatus, WgLinkRefreshReport } from '../api/cadlink';
import type { OnshapeStatus } from '../api/onshape';

/** Shared by the CAD Link panel, the rail card, and the coordinator's send
 * path, so every surface derives the same outbound action from one status. */
export interface CadWorkflowView {
  /** `refresh-needed` and `unmeasured` are the two ways WG cannot tell: the
   * measured half of Fusion's heartbeat describes a revision the document has
   * already left, or nothing has measured the document at all. Neither is
   * `current` and neither is `stale`, which both claim a comparison. */
  state: 'checking' | 'closed' | 'addin-offline' | 'addin-outdated' | 'recovery-required' | 'no-document' | 'not-linked' | 'instance-selection' | 'current' | 'refresh-needed' | 'unmeasured' | 'stale' | 'not-configured';
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
  if (status.state === 'instance_selection_required') return {
    state: 'instance-selection',
    headline: 'Choose an Onshape link',
    detail: 'This design lineage has more than one managed Onshape Part Studio link. Choose the exact link before WG updates, returns, or unlinks it.',
    action: null,
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

const DEFAULT_STATUS_TTL_S = 20;

/** When a status WG holds stops describing now, in ms, or null when it never
 * claimed to (no heartbeat, or already marked as a last report). */
export function statusExpiresAt(status: FusionCadStatus | null): number | null {
  if (!status?.running || status.statusObserved === false) return null;
  const observed = Date.parse(status.updatedAt ?? '');
  if (!Number.isFinite(observed)) return null;
  return observed + (status.statusTtlSeconds ?? DEFAULT_STATUS_TTL_S) * 1000;
}

/**
 * A status held past its freshness window, withdrawn to what it can still
 * honestly say: this is what Fusion last reported, and when (the server's own
 * `_not_observed_since`, applied by the page to a status it holds, so that
 * holding one without polling never turns it into a claim about now).
 */
export function agedFusionStatus(status: FusionCadStatus): FusionCadStatus {
  const measured = status.observationFreshness === 'current' || status.observationFreshness === 'unknown';
  return {
    ...status,
    statusObserved: false,
    observedAt: status.updatedAt ?? null,
    ...(measured ? {
      observationFreshness: 'stale' as const,
      documentChangeDetectable: false,
      staleDetectionExplanation: 'stale detection unavailable: WG has not observed Fusion since this report',
    } : {}),
    state: status.state === 'current' ? 'stale' : status.state,
  };
}

function explainedStaleDetail(status: FusionCadStatus, detail: string): string {
  const explanation = status.staleDetectionExplanation?.trim();
  return explanation ? `${detail} ${explanation}` : detail;
}

/** Activation verdicts that changed Fusion's WGLink folder (server/cadlink/addin_update.py). */
const ACTIVATED_VERDICTS = new Set(['installed', 'updated', 'replaced']);
/** Registry findings that decide whether Fusion loads what WG installed. */
const REGISTRATION_PROBLEMS = new Set(['duplicate', 'elsewhere', 'manual']);
const EXCHANGE_NOTHING = 'Until then WG and Fusion exchange nothing.';

function sentence(text: string): string {
  const trimmed = text.trim().replace(/[.;:]+$/, '');
  return trimmed ? `${trimmed.charAt(0).toUpperCase()}${trimmed.slice(1)}.` : '';
}

/** What follows any add-in message: discarded pending work, and Fusion's own registration. */
function activationFootnotes(refresh: WgLinkRefreshReport | null | undefined, withRegistration: boolean): string {
  const notes: string[] = [];
  if (refresh?.superseded) notes.push(sentence(refresh.superseded));
  const registration = refresh?.registration;
  if (withRegistration && registration && REGISTRATION_PROBLEMS.has(registration.state)) {
    notes.push(registration.detail);
  }
  return notes.length ? ` ${notes.join(' ')}` : '';
}

/** The remedy for an add-in older than WG, from what WG's activation did about it.
 *
 * Activation never changes the add-in while Fusion is open, and "activated"
 * means Fusion's next start loads the new copy -- never that it runs it. */
function outdatedAddinDetail(status: FusionCadStatus): string {
  const refresh = status.addinRefresh;
  const verdict = refresh?.verdict ?? null;
  const notes = activationFootnotes(refresh, true);
  if (verdict === null) {
    return 'WG is still checking Fusion\'s WGLink add-in. Until Fusion runs the WGLink that matches this Waveguide Generator, they exchange nothing.';
  }
  if (verdict === 'pending') {
    return `WGLink activation is pending until Fusion closes. Close Fusion to finish updating WGLink: WG installs the add-in that matches it once Fusion is closed, and says here when Fusion can be reopened. ${EXCHANGE_NOTHING}${notes}`;
  }
  if (ACTIVATED_VERDICTS.has(verdict)) {
    return `WG has installed the WGLink add-in that matches it, and Fusion loads it the next time it starts. ${EXCHANGE_NOTHING}${notes}`;
  }
  if (verdict === 'current') {
    return `Fusion's WGLink folder already holds the add-in that matches WG, but Fusion is running an older one. Restart Fusion 360 so it loads the installed copy. ${EXCHANGE_NOTHING}${notes}`;
  }
  if (verdict === 'awaiting-startup') {
    return `WG changes Fusion's WGLink only once this start is confirmed, so an update that rolls back never leaves its add-in behind (${refresh?.detail ?? ''}). ${EXCHANGE_NOTHING}${notes}`;
  }
  if (verdict === 'superseded') {
    return `WG did not change Fusion's WGLink: ${refresh?.detail ?? ''}. Restart Waveguide Generator so the installed build finishes updating it. ${EXCHANGE_NOTHING}${notes}`;
  }
  if (verdict === 'external') {
    return `Fusion's WGLink belongs to another Waveguide Generator installation, which is older than this one. Update or remove that installation, then restart Fusion 360. (${refresh?.detail ?? ''})${notes}`;
  }
  if (verdict === 'developer') {
    return `Fusion is running a developer copy of WGLink that is older than this Waveguide Generator. Sync it again, then restart WGLink in Fusion.${notes}`;
  }
  if (verdict === 'failed' || verdict === 'unavailable' || verdict === 'not-detected') {
    return `WG could not install the WGLink add-in that matches it: ${refresh?.detail ?? 'no detail'}. Reinstall Waveguide Generator, then restart Fusion 360.${notes}`;
  }
  if (verdict === 'disabled') {
    return `WG does not update WGLink here (${refresh?.detail ?? ''}). Install the WGLink that matches this Waveguide Generator, then restart Fusion 360.${notes}`;
  }
  return `Fusion is running a WGLink add-in older than this Waveguide Generator, and WG did not replace it (${refresh?.detail ?? verdict}). ${EXCHANGE_NOTHING}${notes}`;
}

/** What every other state adds about WGLink activation, or '' when there is nothing to add. */
function activationNote(state: CadWorkflowView['state'], refresh: WgLinkRefreshReport | null | undefined): string {
  const verdict = refresh?.verdict ?? null;
  // Where WGLink is not working, Fusion's own registration may be why.
  const troubled = state === 'addin-offline' || state === 'closed';
  const notes = activationFootnotes(refresh, troubled);
  if (verdict === 'pending') {
    return `WGLink activation is pending until Fusion closes: close Fusion to finish updating WGLink.${notes}`;
  }
  if (verdict === 'failed') {
    return `WG could not update WGLink (${refresh?.detail ?? 'no detail'}).${notes}`;
  }
  if (verdict !== null && ACTIVATED_VERDICTS.has(verdict) && state === 'closed') {
    return `WG has installed the WGLink add-in that matches it; Fusion loads it when it next starts.${notes}`;
  }
  return troubled ? activationFootnotes(refresh, true).trim() : '';
}

/**
 * WGLink with its automatic coordination off reports only when it runs a
 * command, so the status WG holds is the one it last reported. That is neither
 * an offline add-in nor a closed Fusion: say what it is, and keep the action
 * the last report implies -- Send reads the status again when it is pressed,
 * and WGLink checks the document itself before it changes anything.
 */
function notObservedView(view: CadWorkflowView, status: FusionCadStatus): CadWorkflowView {
  const at = status.observedAt ? new Date(status.observedAt) : null;
  const when = at && !Number.isNaN(at.getTime())
    ? ` at ${at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
    : '';
  return {
    ...view,
    headline: `Fusion 360 · last reported${when}`,
    detail: `WGLink reports only when it runs a command (its automatic coordination is off), so WG has not observed Fusion since${when ? ` then` : ''}. Send, Solve and Refresh still work: each checks Fusion when it runs. As of that report: ${view.detail}`,
  };
}

export function fusionWorkflowView(status: FusionCadStatus | null): CadWorkflowView {
  const reported = fusionConnectionView(status);
  const view = status?.statusObserved === false ? notObservedView(reported, status) : reported;
  // With no CAD folder chosen the note still belongs here: WG updates WGLink
  // either way, and a user without a folder must still learn that Fusion has
  // to close to finish it (the updater review §3.8).
  if (status === null || view.state === 'checking' || view.state === 'addin-outdated') {
    return view;
  }
  const note = activationNote(view.state, status.addinRefresh);
  return note ? { ...view, detail: `${view.detail} ${note}` } : view;
}

function fusionConnectionView(status: FusionCadStatus | null): CadWorkflowView {
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
  // Before every other reading of the heartbeat: an older add-in is refused
  // outright, so nothing it reports about the document is acted on.
  if (status.state === 'addin_outdated') return {
    state: 'addin-outdated',
    headline: 'WGLink add-in is out of date',
    detail: outdatedAddinDetail(status),
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
  if (status.recoveryRequired) return {
    state: 'recovery-required',
    headline: `Update interrupted — recovery required${status.documentName ? ` · ${status.documentName}` : ''}`,
    detail: `A WG ${status.recoveryRequired.kind === 'insert' ? 'insert' : 'update'} started changing this Fusion model and did not finish, so WGLink will not repeat it. Fusion has no transaction covering these edits. Use Undo in Fusion to recover the document, or repair the link; do not continue modelling on a partially failed rebuild. Dismissing WG's recovery notice does not repair Fusion.`,
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
  if (status.state === 'instance_selection_required') return {
    state: 'instance-selection',
    headline: `Choose a Fusion instance${status.documentName ? ` · ${status.documentName}` : ''}`,
    detail: 'This design appears more than once in the active document. Choose the exact managed body before WG reads freshness, requests geometry, or sends an update.',
    action: null,
  };
  const parameterCopy = status.link?.parameterCount
    ? `${status.link.parameterCount} managed CAD parameters`
    : 'the managed CAD parameters';
  /** What changed on the WG side, which is read from stored identity rather
   * than from any measurement of Fusion. One definition, used by the plain
   * "WG design changed" reading at the end and by the freshness branch below,
   * so the two can no longer say different things about the same facts. */
  const wgChangeDetail = (): string => {
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
    return `${mismatch}${configCopy}${localEditCopy}`;
  };
  // Before any reading that turns an absence of reported change into "nothing
  // changed": WGLink's heartbeat publishes a cached measurement, and unless its
  // revision tokens agree that measurement describes a revision the document
  // may already have left. Positive evidence is exempt, not because a
  // difference cannot stop being one (an undo back to the returned state leaves
  // one reporting a difference the document no longer has) but because
  // over-reporting is the conservative direction, so this only covers the case
  // where silence would be read as agreement.
  const freshness = status.observationFreshness;
  if ((freshness === 'stale' || freshness === 'none') && !status.fusionChangesAvailable) {
    const named = status.documentName ? ` · ${status.documentName}` : '';
    // Two facts, known in two different ways. The WG side is read from stored
    // identity and is known; the Fusion side is explicitly not. So the known
    // fact leads -- it is the one with an action attached -- and the unknown
    // one qualifies it, while `state` goes on carrying the uncertainty so no
    // surface reads this as a measurement. Dropping the WG copy here was A5
    // review finding D5, handed to A6: `action` stayed `update`, so the only
    // thing lost was the copy that says what actually changed.
    const wgCopy = status.wgChangesAvailable ? ` ${wgChangeDetail()}` : '';
    const remedy = ' Bring the Fusion geometry into WG to find out: WGLink measures the model as it exports it.';
    const headline = (unknownHalf: string): string => (
      status.wgChangesAvailable ? `WG design changed${named}` : `${unknownHalf}${named}`
    );
    return freshness === 'none' ? {
      state: 'unmeasured',
      headline: headline('Fusion geometry not measured yet'),
      detail: explainedStaleDetail(status, `WGLink has not measured this document's geometry, so WG cannot tell whether it still matches what was returned.${wgCopy}${remedy}`),
      action: status.wgChangesAvailable ? 'update' : null,
    } : {
      state: 'refresh-needed',
      headline: headline('Fusion geometry may have changed'),
      detail: explainedStaleDetail(status, `The Fusion model has moved on since WGLink measured it, so WG cannot tell whether its geometry still matches what was returned.${wgCopy}${remedy}`),
      action: status.wgChangesAvailable ? 'update' : null,
    };
  }
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
  const conflictCopy = status.fusionChangesAvailable
    ? ' Fusion geometry also changed; choose which direction to synchronize.'
    : '';
  return {
    state: 'stale',
    headline: `${status.fusionChangesAvailable ? 'WG and Fusion both changed' : 'WG design changed'}${status.documentName ? ` · ${status.documentName}` : ''}`,
    detail: explainedStaleDetail(status, `${wgChangeDetail()}${conflictCopy}`),
    action: 'update',
  };
}
