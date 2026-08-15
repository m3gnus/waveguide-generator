import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { convertDesignToFreeform } from '../api/designIo';
import { previewSocket } from '../api/previewSocket';
import type { CadRealizedDimensions, CadRealizedParameter } from '../api/cadlink';
import { importedSubmissionBlocker } from '../jobs/importedSubmission';
import { postSymmetry, toSolveDesign, type SymmetryResolution } from '../jobs/actions';
import { usePreferences } from '../prefs/preferences';
import {
  combineChain,
  combineLevelMatchDefault,
  DRIVER_REQUIRED_KEYS,
  useCadReturnStore,
  type CadDriveChannel,
  type ChannelDriverForm,
  type DriverFieldKey,
} from '../stores/cadReturn';
import { useDesignStore, type DesignDocument, type DesignFamily, type DesignValue } from '../stores/design';
import { useSolveOptionsStore, type SymmetryMode } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { DirectivityMapControls, SolveOptionsControls, ToggleRow } from './SolveOptionsSections';
import { EditablePointTable, EditableStationTable } from './FreeformEditors';
import { lambdaSixthHint } from './lambdaLimit';
import { NumberField } from './NumberField';
import { HelpTipRow } from './HelpTip';
import { Icon } from '../shell/icons';
import {
  PARAMETER_REGISTRY,
  PARAMETER_SECTION_DEFINITIONS,
  fieldAppliesToFamily,
  fieldAcceptsExpression,
  fieldIsVisible,
  fieldMatchesQuery,
  parameterSectionIsVisible,
  type ParameterDefinition,
  type ParameterSectionDefinition,
  type ParameterTab,
} from './parameterRegistry';
import { cadLinkCoordinatorBridge } from '../shell/CadLinkCoordinator';
import { fusionWorkflowView, onshapeWorkflowView } from '../shell/CadLinkPanel';
import { workspaceNavigation } from '../shell/workspaceNavigation';
import {
  CAD_CONTROLS,
  CAD_CONTROL_DESCRIPTORS,
  CAD_DRIVER_FIELD_CONTROLS,
  cadControlIsAvailable,
  cadControlMatchesQuery,
  type CadControlSection,
} from './cadControlRegistry';
import {
  PARAMETRIC_CONTROLS,
  PARAMETRIC_CONTROL_DESCRIPTORS,
  parametricControlMatchesQuery,
} from './parametricControlRegistry';
import './paramPanel.css';

interface SectionProps {
  title: string;
  summary?: string;
  description?: string;
  children: ReactNode;
  forceOpen: boolean;
  revealId?: string;
}

const storageKey = (title: string) => `wg-param-section-open:${title}`;

function storedSectionState(title: string): boolean {
  try {
    const value = localStorage.getItem(storageKey(title));
    return value === null ? true : value === 'true';
  } catch {
    return true;
  }
}

/**
 * Section blurbs explain the rail once and then cost height on every scroll —
 * a third of a metre of prose across the geometry tab. They are off by default
 * and toggled for the whole rail at once, so the explanation stays one click
 * away instead of permanently between the reader and the next field.
 */
const HELP_KEY = 'wg-param-help-visible';

function storedHelpVisible(): boolean {
  try { return localStorage.getItem(HELP_KEY) === 'true'; } catch { return false; }
}

class ParameterHelpStore {
  private value = storedHelpVisible();
  private readonly listeners = new Set<() => void>();
  getSnapshot = (): boolean => this.value;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  toggle(): void {
    this.value = !this.value;
    try { localStorage.setItem(HELP_KEY, String(this.value)); } catch { /* storage is optional */ }
    this.listeners.forEach((listener) => listener());
  }
  resetForTests(): void { this.value = false; }
}

export const parameterHelpStore = new ParameterHelpStore();

export function useParameterHelp(): boolean {
  return useSyncExternalStore(parameterHelpStore.subscribe, parameterHelpStore.getSnapshot, parameterHelpStore.getSnapshot);
}

function Section({ title, summary, description, children, forceOpen, revealId }: SectionProps) {
  const [open, setOpen] = useState(() => storedSectionState(title));
  const helpVisible = useParameterHelp();
  const shownOpen = forceOpen || open;
  const toggle = () => {
    const next = !open;
    setOpen(next);
    try { localStorage.setItem(storageKey(title), String(next)); } catch { /* storage is optional */ }
  };
  return (
    <section className={`param-section${shownOpen ? '' : ' closed'}`} data-section={title} data-control-reveal-id={revealId}>
      {/* A real heading wrapping the disclosure button. The rail is the primary
          way into ~119 parameters and had no headings at all, so a screen
          reader offered no structure to navigate by and the only way to reach a
          parameter was to tab past every one before it. This is the standard
          disclosure pattern: the heading carries the structure, the button
          carries the state. */}
      <h3 className="section-heading">
        <button className="section-head" onClick={toggle} aria-expanded={shownOpen}>
          <span className="chevron" aria-hidden="true">⌄</span><span className="section-name">{title}</span><span className="spacer" />
          {summary && <span className="section-summary">{summary}</span>}
        </button>
      </h3>
      {shownOpen && <div className="section-body">
        {description && helpVisible && <p className="section-description">{description}</p>}
        {children}
      </div>}
    </section>
  );
}

export type OuterBodyMode = 'infinite-baffle' | 'enclosure' | 'freestanding' | 'bare';
type SelectableOuterBodyMode = Exclude<OuterBodyMode, 'infinite-baffle'>;

/** Mirrors server/preview/translate.py's outer-body precedence exactly. */
export function resolveOuterBodyMode(design: DesignDocument): OuterBodyMode {
  if (design.simulation.sim_type === 'infinite-baffle') return 'infinite-baffle';
  if (design.enclosure.depth > 0) return 'enclosure';
  if (design.mesh.wall_thickness > 0) return 'freestanding';
  return 'bare';
}

function selectedOuterBodyMode(design: DesignDocument): SelectableOuterBodyMode {
  if (design.enclosure.depth > 0) return 'enclosure';
  if (design.mesh.wall_thickness > 0) return 'freestanding';
  return 'bare';
}

const outerModeLabels: Record<OuterBodyMode, string> = {
  'infinite-baffle': 'Infinite baffle',
  enclosure: 'Waveguide in enclosure',
  freestanding: 'Thickened waveguide (freestanding)',
  bare: 'Bare shell',
};

function WallEnclosureModeControl({ design }: { design: DesignDocument }) {
  const updateValues = useDesignStore((state) => state.updateValues);
  const [lastWallThickness, setLastWallThickness] = useState(() => design.mesh.wall_thickness > 0 ? design.mesh.wall_thickness : 5);
  const [lastEnclosureDepth, setLastEnclosureDepth] = useState(() => design.enclosure.depth > 0 ? design.enclosure.depth : 280);
  const selectedMode = selectedOuterBodyMode(design);
  const resolvedMode = resolveOuterBodyMode(design);

  useEffect(() => {
    if (design.mesh.wall_thickness > 0) setLastWallThickness(design.mesh.wall_thickness);
  }, [design.mesh.wall_thickness]);
  useEffect(() => {
    if (design.enclosure.depth > 0) setLastEnclosureDepth(design.enclosure.depth);
  }, [design.enclosure.depth]);

  const chooseMode = (mode: SelectableOuterBodyMode) => {
    const updates = mode === 'bare'
      ? { 'mesh.wall_thickness': 0, 'enclosure.depth': 0 }
      : mode === 'freestanding'
        ? { 'mesh.wall_thickness': lastWallThickness, 'enclosure.depth': 0 }
        : { 'mesh.wall_thickness': 0, 'enclosure.depth': lastEnclosureDepth };
    updateValues(updates);
  };

  return <div className="outer-body-mode">
    <div className="select-row">
      <label htmlFor="outer-body-mode">Outer body</label>
      <select id="outer-body-mode" value={selectedMode} onChange={(event) => chooseMode(event.target.value as SelectableOuterBodyMode)}>
        <option value="bare">Bare shell</option>
        <option value="freestanding">Thickened waveguide (freestanding)</option>
        <option value="enclosure">Waveguide in enclosure</option>
      </select>
    </div>
    <div className={`resolved-mode${resolvedMode === 'infinite-baffle' ? ' override' : ''}`}>
      <span>Resolved mode</span><b>{outerModeLabels[resolvedMode]}</b>
      {resolvedMode === 'infinite-baffle' && <small>Infinite baffle simulation overrides the outer body.</small>}
    </div>
  </div>;
}

function getAtPath(design: DesignDocument, path: string | undefined): unknown {
  if (!path) return undefined;
  return path.split('.').reduce<unknown>((value, part) => {
    if (typeof value !== 'object' || value === null) return undefined;
    const key = part === '$last' && Array.isArray(value) ? String(value.length - 1) : part;
    return (value as Record<string, unknown>)[key];
  }, design);
}

function TextField({ field, value, disabled, onCommit }: {
  field: ParameterDefinition;
  value: string;
  disabled?: boolean;
  onCommit: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return <HelpTipRow className={`field-row param-text-row${disabled ? ' field-disabled' : ''}`} text={field.description}>
    <label className="field-label" htmlFor={`parameter-${field.id}`} title={disabled ? field.disabledReason : undefined}>{field.label}</label>
    <input
      id={`parameter-${field.id}`}
      value={draft}
      disabled={disabled}
      spellCheck={false}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => { if (draft !== value) onCommit(draft); }}
      onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
    />
  </HelpTipRow>;
}

function passthroughStatus(field: ParameterDefinition, design: DesignDocument): string {
  // Mesh.Quadrants is modeled, unlike the block/key indicators below, but it
  // is deliberately read-only here. Editing this stored ATH value would bring
  // back a second apparent domain control even though solve and export discard
  // it; the value remains visible so imported config fidelity is inspectable.
  if (field.id === 'mesh.quadrants') return String(getAtPath(design, field.path) ?? 'Not present');
  const names = Object.keys(design.extra_blocks);
  if (field.id === 'passthrough.abec') {
    const count = names.filter((name) => (
      name.toLocaleUpperCase().startsWith('ABEC')
      && !name.startsWith('ABEC.Polars:')
    )).length;
    return count ? `${count} block${count === 1 ? '' : 's'} present` : 'No blocks present';
  }
  if (field.id === 'passthrough.report') return names.some((name) => name.toLocaleLowerCase() === 'report') ? 'Block present' : 'No block present';
  if (field.id === 'passthrough.keys') {
    const count = Object.keys(design.extra_keys).length;
    return count ? `${count} key${count === 1 ? '' : 's'} present` : 'No keys present';
  }
  const count = names.filter((name) => !name.toLocaleUpperCase().startsWith('ABEC') && name.toLocaleLowerCase() !== 'report').length;
  return count ? `${count} block${count === 1 ? '' : 's'} present` : 'No blocks present';
}

function validationMessage(field: ParameterDefinition, design: DesignDocument): string | undefined {
  if (field.id === 'icw.hold_end' && (design.hold_end ?? 0) <= (design.hold_start ?? 0)) return 'Must exceed coverage hold start.';
  if (field.id === 'simulation.f2' && design.simulation.f2 <= design.simulation.f1) return 'Must exceed sweep start.';
  if (field.id === 'source.velocity' && design.source.velocity_convention === 'legacy' && ![1, 2].includes(design.source.velocity)) return 'Legacy velocity must be 1 (normal) or 2 (axial).';
  return undefined;
}

function prospectiveValidation(field: ParameterDefinition, design: DesignDocument, value: number): string | undefined {
  if (field.id === 'simulation.f1' && value >= design.simulation.f2) return 'Must be below sweep end.';
  if (field.id === 'simulation.f2' && value <= design.simulation.f1) return 'Must exceed sweep start.';
  if (field.id === 'source.velocity' && design.source.velocity_convention === 'legacy' && ![1, 2].includes(value)) return 'Legacy velocity must be 1 (normal) or 2 (axial).';
  return undefined;
}

/**
 * How fine the mouth mesh has to be for the sweep this design will actually run.
 *
 * Both halves of this used to be the literal 20 kHz sweep -- the label said
 * "λ/6 at 20 kHz ≈ 2.86 mm" and the amber warning fired above 2.86 mm -- while
 * the top of the sweep is the user's to set. The default 400 Hz – 16 kHz design
 * therefore had 3.2 mm flagged as too coarse when its real limit is 3.57 mm,
 * and the stated number belonged to somebody else's design.
 *
 * Its own component so the solve-options subscription re-renders one hint rather
 * than every field in the rail.
 */
function MouthResolutionHint({ design, value }: { design: DesignDocument; value: unknown }) {
  const frequencyMode = useSolveOptionsStore((state) => state.frequencyMode);
  const frequencyListText = useSolveOptionsStore((state) => state.frequencyListText);
  const hint = lambdaSixthHint(design, { frequencyMode, frequencyListText });
  if (!hint) return null;
  return <div className={`lambda-hint${Number(value) > hint.limitMm ? ' warning' : ''}`}>
    λ/6 at {hint.frequencyLabel} ≈ {hint.limitMm} mm
  </div>;
}

/** Match both direct preview keys and Pydantic's design/root-qualified paths. */
export function previewErrorForParameter(
  field: ParameterDefinition,
  fields: Readonly<Record<string, string>> | null,
): string | undefined {
  if (!fields) return undefined;
  const paths = new Set([field.id, field.path].filter((path): path is string => Boolean(path)));
  for (const path of [...paths]) {
    paths.add(`design.${path}`);
    paths.add(`design.root.${path}`);
  }
  for (const path of paths) {
    const message = fields[path];
    if (message) return message;
  }
  return undefined;
}

function FieldControl({ field, design, serverError }: { field: ParameterDefinition; design: DesignDocument; serverError?: string }) {
  const updateValue = useDesignStore((state) => state.updateValue);
  const updateValues = useDesignStore((state) => state.updateValues);
  const updateExpression = useDesignStore((state) => state.updateExpression);
  const beginDrag = useDesignStore((state) => state.beginDrag);
  const endDrag = useDesignStore((state) => state.endDrag);
  const value = getAtPath(design, field.path);
  const modeReason = field.disabledWhen?.(design);
  const disabledReason = field.disabledReason ?? modeReason;
  const disabled = Boolean(disabledReason);
  const commit = (next: DesignValue) => {
    if (!field.path || disabled) return;
    if (field.mirrorPaths?.length) {
      updateValues(Object.fromEntries([field.path, ...field.mirrorPaths].map((path) => [path, next])));
    } else {
      updateValue(field.path, next);
    }
  };

  if (field.kind === 'indicator') {
    const storedAthQuadrants = field.id === 'mesh.quadrants';
    return <HelpTipRow className={`passthrough-row${storedAthQuadrants ? ' stored-ath-readout' : ''}`} text={field.description}>
      <span>{field.label}{storedAthQuadrants && <small>Preserved for ATH .cfg round-trip; WG overwrites it on solve and ignores it on export.</small>}</span>
      <b>{passthroughStatus(field, design)}</b>
    </HelpTipRow>;
  }
  if (field.kind === 'table') {
    if (field.id === 'freeform.crossSections') return <EditableStationTable field={field} stations={Array.isArray(value) ? value : []} />;
    return <EditablePointTable field={field} points={Array.isArray(value) ? value : []} />;
  }
  if (field.disabledReason && !field.path) {
    return <div className="schema-gap" title={field.disabledReason}>
      <span>{field.label}</span><button disabled>{field.kind === 'toggle' ? 'Off' : 'Unavailable'}</button><small>{field.disabledReason}</small>
    </div>;
  }
  if (field.kind === 'select' || field.kind === 'toggle') {
    return <HelpTipRow className={`select-row${disabled ? ' field-disabled' : ''}`} text={field.description}>
      <label htmlFor={`parameter-${field.id}`} title={disabledReason}>{field.label}</label>
      <select id={`parameter-${field.id}`} value={String(value ?? '')} disabled={disabled} onChange={(event) => {
        const option = field.options?.find((item) => String(item.value) === event.target.value);
        commit(option?.value ?? event.target.value);
      }}>
        {(field.options ?? []).map((option) => <option key={String(option.value)} value={String(option.value)}>{option.label}</option>)}
      </select>
    </HelpTipRow>;
  }
  if (field.kind === 'text') return <TextField field={field} value={String(value ?? '')} disabled={disabled} onCommit={commit} />;
  const error = validationMessage(field, design);
  const optional = field.path === 'mesh.max_edge';
  return <>
    <NumberField
      label={field.label}
      symbol={field.symbol}
      description={field.description}
      value={typeof value === 'number' ? value : optional ? undefined : 0}
      expression={field.path ? design._expressions?.[field.path] : undefined}
      allowExpression={fieldAcceptsExpression(field)}
      unit={field.unit}
      min={field.min}
      max={field.max}
      step={field.step}
      precision={field.precision}
      disabled={disabled}
      disabledReason={disabledReason}
      invalidMessage={error ?? serverError}
      validate={(next) => prospectiveValidation(field, design, next)}
      onCommit={(next) => commit(next)}
      optional={optional}
      onClear={optional ? () => commit(null) : undefined}
      onCommitExpression={(expression) => { if (field.path && !disabled) updateExpression(field.path, expression); }}
      onBeginDrag={beginDrag}
      onEndDrag={endDrag}
    />
    {error && <div className="field-error">△ {error}</div>}
    {field.id === 'mesh.mouth_resolution' && <MouthResolutionHint design={design} value={value} />}
    {disabledReason && <div className="disabled-reason">{disabledReason}</div>}
  </>;
}

const symmetryModeLabels: Record<SymmetryMode, string> = {
  auto: 'Auto — smallest domain the geometry allows',
  full: 'Full domain',
  half_xz: 'Half domain (mirror about XZ)',
  half_yz: 'Half domain (mirror about YZ)',
  quarter: 'Quarter domain',
};

export function domainName(quadrants: number): string {
  if (quadrants === 1) return 'Quarter domain';
  if (quadrants === 12) return 'Half domain (XZ)';
  if (quadrants === 14) return 'Half domain (YZ)';
  return 'Full domain';
}

/** Why auto could not go smaller, in the resolver's own words. */
export function symmetrySummary(resolution: SymmetryResolution): string {
  const rejected = [
    ...(resolution.xz ? [] : resolution.reasons.xz),
    ...(resolution.yz ? [] : resolution.reasons.yz),
  ];
  if (!rejected.length) return 'Both mirror planes hold.';
  return rejected.join(' ');
}

/** Resolving samples the surface, so it must not run on every keystroke. */
const SYMMETRY_DEBOUNCE_MS = 400;

/**
 * Long enough that an editing session never re-resolves a shape it has already
 * seen, finite so a page that outlives a server restart eventually asks the new
 * process rather than trusting the old one's answer.
 */
const SYMMETRY_STALE_MS = 5 * 60_000;

/** How long a superseded payload's answer is kept in case the user comes back. */
const SYMMETRY_GC_MS = 60_000;

/** Hold a value back until it has stopped changing for `delay` milliseconds. */
function useSettled<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

function AutoSymmetryReadout({ design }: { design: DesignDocument }) {
  // Key on the wire payload, not the revision. Surface sampling costs 57-150 ms
  // per call, and most revisions -- anything the solve design does not carry,
  // plus every undo back to a shape already resolved -- produce bytes we have
  // already answered. React Query then dedupes in flight and caches the rest.
  //
  // The debounce, not the signal, is what bounds server work: the resolver runs
  // in `asyncio.to_thread`, so aborting a superseded request frees the
  // connection and discards the answer but does not stop the thread.
  const settledDesign = useSettled(design, SYMMETRY_DEBOUNCE_MS);
  const body = useMemo(() => JSON.stringify(toSolveDesign(settledDesign)), [settledDesign]);
  const { data: resolution, error: queryError, isPending } = useQuery({
    queryKey: ['symmetry', body],
    queryFn: ({ signal }) => postSymmetry(body, fetch, signal),
    staleTime: SYMMETRY_STALE_MS,
    // Only the settled key is ever active, so every superseded payload is
    // inactive and collected a minute later. The cache is bounded by time, not
    // by count: pausing on many distinct values inside one minute does keep
    // them all. Each entry is a ~125-byte resolution, so that is affordable,
    // and a short window is what keeps it so.
    gcTime: SYMMETRY_GC_MS,
    retry: false,
  });
  const error = queryError ? queryError instanceof Error ? queryError.message : String(queryError) : null;

  if (error) return <div className="resolved-mode"><span>Auto resolves to</span><b>—</b><small>{error}</small></div>;
  if (!resolution) return <div className="resolved-mode"><span>Auto resolves to</span><b>{isPending ? 'resolving…' : '—'}</b></div>;
  return <div className="resolved-mode">
    <span>Auto resolves to</span><b>{domainName(resolution.quadrants)}</b>
    <small>{symmetrySummary(resolution)}</small>
  </div>;
}

function SolveDomainControl({ design }: { design: DesignDocument }) {
  const symmetry = useSolveOptionsStore((state) => state.symmetry);
  const setSymmetry = useSolveOptionsStore((state) => state.setSymmetry);
  return <div className="solve-domain-control" data-control-reveal-id={PARAMETRIC_CONTROLS.solveDomain.reveal.id}>
    <div className="select-row">
      <label htmlFor="solve-symmetry">Solve domain</label>
      <select id="solve-symmetry" value={symmetry} onChange={(event) => setSymmetry(event.target.value as SymmetryMode)}>
        {(Object.keys(symmetryModeLabels) as SymmetryMode[]).map((mode) => <option key={mode} value={mode}>{symmetryModeLabels[mode]}</option>)}
      </select>
    </div>
    {symmetry === 'auto' && <AutoSymmetryReadout design={design} />}
  </div>;
}

export const REVEAL_PARAMETER_EVENT = 'wg:reveal-parameter';

export interface RevealRequest {
  id: string;
  tab: ParameterTab;
  query: string;
  /** Older callers omit this and retain the registry-parameter route. */
  target?: 'parameter' | 'control';
  fallbackId?: string;
}

/**
 * A reveal request that waits to be claimed.
 *
 * Dockview mounts only the active panel in a group, so routing a parameter from
 * the command palette activates a tab and then talks to a panel that may not
 * exist yet. A plain event only reaches listeners that are already attached, and
 * which side of the panel's mount the announcement lands on is not something the
 * caller can know. Holding the request until its panel claims it removes the
 * ordering question rather than betting on an answer.
 */
class ParameterRevealRequest {
  private request: RevealRequest | null = null;
  private readonly listeners = new Set<() => void>();

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): RevealRequest | null => this.request;

  set(detail: RevealRequest): void {
    this.request = detail;
    this.listeners.forEach((listener) => listener());
  }

  /** Take the request if it belongs to `tab`, leaving another tab's alone. */
  claim(tab: ParameterTab): RevealRequest | null {
    if (this.request?.tab !== tab) return null;
    const claimed = this.request;
    this.request = null;
    return claimed;
  }
}

export const parameterRevealRequest = new ParameterRevealRequest();

export function requestParameterReveal(detail: RevealRequest): void {
  parameterRevealRequest.set(detail);
  window.dispatchEvent(new CustomEvent(REVEAL_PARAMETER_EVENT, { detail }));
}

function LinkedDesignCard({ forceOpen = false }: { forceOpen?: boolean }) {
  const preferences = usePreferences();
  const cadCoordinator = useSyncExternalStore(
    cadLinkCoordinatorBridge.subscribe,
    cadLinkCoordinatorBridge.getSnapshot,
    cadLinkCoordinatorBridge.getSnapshot,
  );
  const onshape = preferences.cadApplication === 'onshape';
  const workflow = onshape
    ? onshapeWorkflowView(cadCoordinator.onshapeStatus)
    : fusionWorkflowView(cadCoordinator.fusionStatus);
  const driftCount = cadCoordinator.fusionStatus?.link?.parameterDriftCount ?? 0;
  const send = () => {
    // Onshape's public-document consent stays in CAD Link. Fusion sends use
    // the coordinator's unified path; its dialog holds the two-way conflict.
    if (onshape) {
      workspaceNavigation.activate('cadlink');
      return;
    }
    void cadCoordinator.sendWgToFusion().catch(() => undefined);
  };
  const actionLabel = onshape
    ? workflow.action === 'update' ? 'Send WG changes to Onshape' : 'Create in Onshape'
    : workflow.action === 'update' ? 'Send WG changes to Fusion' : 'Open in Fusion 360';
  return <Section
    title={CAD_CONTROLS.linkedDesign.section}
    description="The CAD document linked to this design, its aggregate freshness, and the outbound rebuild action."
    forceOpen={forceOpen}
    revealId={CAD_CONTROLS.linkedDesign.reveal.id}
  >
    <div className={`linked-design-card cad-connection-${workflow.state}`}>
      <span className="cad-connection-dot" aria-hidden="true"/>
      <div><b>{workflow.headline}</b><span>{workflow.detail}</span></div>
    </div>
    <FusionParameterDrift
      parameterDriftCount={driftCount}
      driftedParameters={cadCoordinator.fusionStatus?.link?.driftedParameters}
    />
    {workflow.action && <button className="primary linked-design-action" disabled={cadCoordinator.sendingToFusion} onClick={send}>{cadCoordinator.sendingToFusion ? 'Sending…' : actionLabel}</button>}
    {cadCoordinator.error && <div className="field-error" role="alert">{cadCoordinator.error}</div>}
    {cadCoordinator.status && <p className="section-note" role="status">{cadCoordinator.status}</p>}
  </Section>;
}

/** New WGLink builds name every edited managed parameter. Keep the aggregate
 * fallback for older add-ins, but enumerate exact Fusion names when available:
 * enclosure builds also manage a synthetic `mouth_overshoot` parameter which
 * has no corresponding row in the exported realized-dimensions table. */
export function FusionParameterDrift({ parameterDriftCount, driftedParameters }: {
  parameterDriftCount: number;
  driftedParameters?: readonly string[];
}) {
  if (parameterDriftCount <= 0) return null;
  return <div className="linked-design-drift">
    <p>{parameterDriftCount} managed parameter{parameterDriftCount === 1 ? ' has' : 's have'} local edits</p>
    {driftedParameters && driftedParameters.length > 0 && <ul aria-label="Locally edited Fusion parameters">
      {driftedParameters.map((name) => <li key={name}><code>{name}</code></li>)}
    </ul>}
  </div>;
}

const REALIZED_DIMENSION_LABELS: ReadonlyArray<{ suffix: string; label: string }> = [
  { suffix: '_throat_dia', label: 'Realized throat diameter' },
  { suffix: '_mouth_w', label: 'Mouth width' },
  { suffix: '_mouth_h', label: 'Mouth height' },
  { suffix: '_depth', label: 'Realized depth' },
  { suffix: '_wall_t', label: 'Wall thickness' },
  { suffix: '_vertical_offset', label: 'Vertical offset' },
  { suffix: '_enc_w', label: 'Enclosure width' },
  { suffix: '_enc_h', label: 'Enclosure height' },
  { suffix: '_enc_depth', label: 'Enclosure depth' },
  { suffix: '_enc_edge', label: 'Enclosure edge radius' },
  { suffix: '_enc_x0', label: 'Enclosure X minimum' },
  { suffix: '_enc_y0', label: 'Enclosure Y minimum' },
  { suffix: '_enc_z_front', label: 'Enclosure front Z' },
];

function realizedDimensionLabel(name: string): string {
  return realizedDimensionDefinition(name)?.label ?? name;
}

function realizedDimensionDefinition(name: string): typeof REALIZED_DIMENSION_LABELS[number] | undefined {
  // `enc_depth` also ends in `depth`; prefer the most specific contract suffix
  // while retaining the table's semantic order for presentation.
  return REALIZED_DIMENSION_LABELS
    .filter(({ suffix }) => name.endsWith(suffix))
    .sort((left, right) => right.suffix.length - left.suffix.length)[0];
}

function realizedDimensionOrder(parameter: CadRealizedParameter): number {
  const definition = realizedDimensionDefinition(parameter.name);
  const index = definition ? REALIZED_DIMENSION_LABELS.indexOf(definition) : -1;
  return index < 0 ? REALIZED_DIMENSION_LABELS.length : index;
}

function formatRealizedValue(value: number): string {
  const normalized = Object.is(value, -0) ? 0 : value;
  return normalized.toFixed(3).replace(/\.000$/, '').replace(/(\.\d*?)0+$/, '$1');
}

const REALIZED_EMPTY_COPY: Record<Exclude<CadRealizedDimensions['state'], 'current' | 'stale'>, { title: string; detail: string }> = {
  no_link: {
    title: 'No CAD link yet',
    detail: 'Send this design to Fusion to publish cabinet-reference dimensions.',
  },
  link_unavailable: {
    title: 'Linked instance unavailable',
    detail: 'Open the linked design in Fusion so WG can identify its exact instance and export.',
  },
  export_missing: {
    title: 'Linked export unavailable',
    detail: 'This Fusion link\'s exact export is not available in this WG registry.',
  },
  not_captured: {
    title: 'Realized dimensions were not captured',
    detail: 'This CAD link predates parameter capture. Send the design to Fusion again to publish them.',
  },
  unavailable: {
    title: 'Realized dimensions unavailable',
    detail: 'WG could not read a valid parameter table from the linked export manifest.',
  },
};

/** D7a outputs are evidence, never controls. Their manifest role decides what
 * belongs here; suffixes only supply friendly labels and a stable visual order. */
export function RealizedDimensionsSection({ snapshot, driftedParameters = [], forceOpen = false }: {
  snapshot: CadRealizedDimensions | null;
  driftedParameters?: readonly string[];
  forceOpen?: boolean;
}) {
  const drifted = new Set(driftedParameters);
  const interfaceParameters = (snapshot?.parameters ?? [])
    .filter((parameter) => parameter.role === 'interface')
    .sort((left, right) => realizedDimensionOrder(left) - realizedDimensionOrder(right)
      || left.name.localeCompare(right.name));
  const valuesAvailable = snapshot?.state === 'current' || snapshot?.state === 'stale';
  const empty = snapshot && snapshot.state !== 'current' && snapshot.state !== 'stale'
    ? REALIZED_EMPTY_COPY[snapshot.state]
    : null;

  return <Section
    title={CAD_CONTROLS.realizedDimensions.section}
    description="Read-only dimensions published by WG as the cabinet-facing CAD interface."
    forceOpen={forceOpen}
    revealId={CAD_CONTROLS.realizedDimensions.reveal.id}
  >
    {!snapshot && <div className="realized-dimensions-empty" role="status"><b>Checking linked export…</b><span>WG is resolving the active Fusion instance and its published parameter table.</span></div>}
    {empty && <div className="realized-dimensions-empty" role="status"><b>{empty.title}</b><span>{empty.detail}</span></div>}
    {valuesAvailable && snapshot.state === 'stale' && <div className="realized-dimensions-warning" role="status"><b>From an older CAD export</b><span>These values were published before the design now on screen changed. They are historical, not current dimensions.</span></div>}
    {valuesAvailable && <>
      <p className="realized-dimensions-intro">These read-only facts are the values WG published to CAD and the cabinet references. Change the formula parameters above, then rebuild in Fusion, to produce new values.</p>
      <small className="realized-dimensions-meta">Instance <code>{snapshot.instanceId ?? 'unknown'}</code> · export <code>{snapshot.exportId ?? 'unknown'}</code></small>
      {interfaceParameters.length > 0
        ? <dl className={`realized-dimension-list${snapshot.state === 'stale' ? ' stale' : ''}`}>
          {interfaceParameters.map((parameter) => <div
            className={`realized-dimension-row${drifted.has(parameter.name) ? ' locally-edited' : ''}`}
            data-instance-id={parameter.instanceId ?? ''}
            data-role={parameter.role}
            data-locally-edited={drifted.has(parameter.name) ? 'true' : undefined}
            key={`${parameter.instanceId ?? 'unknown'}:${parameter.name}`}
          >
            <dt><span>{realizedDimensionLabel(parameter.name)}{drifted.has(parameter.name) && <small className="realized-dimension-drift">Edited in Fusion</small>}</span><code>{parameter.name}</code></dt>
            <dd>{formatRealizedValue(parameter.value)}{parameter.unit && <small>{parameter.unit}</small>}</dd>
          </div>)}
        </dl>
        : <div className="realized-dimensions-empty" role="status"><b>No interface-role dimensions</b><span>The manifest contains no dimensions marked for the cabinet-facing CAD interface.</span></div>}
    </>}
  </Section>;
}

function RealizedDimensionsCard({ forceOpen = false }: { forceOpen?: boolean }) {
  const cadCoordinator = useSyncExternalStore(
    cadLinkCoordinatorBridge.subscribe,
    cadLinkCoordinatorBridge.getSnapshot,
    cadLinkCoordinatorBridge.getSnapshot,
  );
  return <RealizedDimensionsSection
    snapshot={cadCoordinator.fusionStatus?.realizedDimensions ?? null}
    driftedParameters={cadCoordinator.fusionStatus?.link?.driftedParameters}
    forceOpen={forceOpen}
  />;
}

function CadFrequencySweep() {
  const cadReturn = useCadReturnStore();
  const solveStore = useSolveOptionsStore();
  const blocker = importedSubmissionBlocker(cadReturn, solveStore);
  const rangeMessage = blocker === 'Enter a valid explicit frequency sweep.' ? blocker : null;
  return <>
    <NumberField label={CAD_CONTROLS.sweepStart.label} revealId={CAD_CONTROLS.sweepStart.reveal.id} unit="Hz" value={cadReturn.frequencyStartHz} min={1} step={10} precision={0} description="Lowest frequency of the generated imported-CAD sweep." onCommit={(frequencyStartHz) => cadReturn.setSweep({ frequencyStartHz })}/>
    <NumberField label={CAD_CONTROLS.sweepEnd.label} revealId={CAD_CONTROLS.sweepEnd.reveal.id} unit="Hz" value={cadReturn.frequencyEndHz} min={1} step={10} precision={0} description="Highest frequency of the generated imported-CAD sweep." onCommit={(frequencyEndHz) => cadReturn.setSweep({ frequencyEndHz })}/>
    <NumberField label={CAD_CONTROLS.frequencySamples.label} revealId={CAD_CONTROLS.frequencySamples.reveal.id} value={cadReturn.frequencyCount} min={1} max={401} step={1} precision={0} description="How many frequencies are solved across the imported-CAD range." onCommit={(frequencyCount) => cadReturn.setSweep({ frequencyCount })}/>
    {rangeMessage && <div className="field-error" role="alert">{rangeMessage}</div>}
  </>;
}

/** Hornresp-unit T/S entry for one drive channel. Plain inputs are required:
 * an empty field means "not provided", which NumberField cannot represent. */
function DriverFields({ channel, form, onField }: {
  channel: CadDriveChannel;
  form: ChannelDriverForm | undefined;
  onField: (field: DriverFieldKey, value: number | null) => void;
}) {
  const missing = DRIVER_REQUIRED_KEYS.filter((key) => form?.fields[key] === undefined);
  return <div className="cad-driver-grid">
    {CAD_DRIVER_FIELD_CONTROLS.map(({ driverKey, label, unit, step, reveal }) => <label key={driverKey} className="cad-driver-field" data-control-reveal-id={reveal.id}>
      <span>{label}{unit ? ` (${unit})` : ''}{DRIVER_REQUIRED_KEYS.includes(driverKey) ? ' *' : ''}</span>
      <input
        type="number"
        min={0}
        step={step}
        value={form?.fields[driverKey] ?? ''}
        aria-label={`${label} for ${channel.id}`}
        onChange={(event) => onField(driverKey, event.target.value === '' ? null : Number(event.target.value))}
      />
    </label>)}
    {missing.length > 0 && <p className="cad-driver-hint">Required: {missing.map((key) => CAD_DRIVER_FIELD_CONTROLS.find((item) => item.driverKey === key)?.label ?? key).join(', ')}. The channel solves as a unit-drive basis until they are set. Vas/Fs/Qms alternatives are accepted through the API.</p>}
  </div>;
}

function CadDriveChannels() {
  const state = useCadReturnStore();
  const activeSources = (state.selectedBundle?.sources ?? []).filter((source) => !state.skippedSourceIds.includes(source.id));
  const channelIds = [...new Set((state.selectedBundle?.sources ?? []).map((source) => source.defaultDriveChannelId))];
  return <>
    <p className="section-note">Assign two sources to the same channel to drive them together.</p>
    <div className="cad-channel-list">
      {activeSources.map((source) => {
        const channel = state.driveChannels.find((item) => item.source_ids.includes(source.id));
        return <div className="cad-channel-row" data-control-reveal-id={CAD_CONTROLS.channelAssignment.reveal.id} key={source.id}><b>{source.id}</b><select aria-label={`${CAD_CONTROLS.channelAssignment.label} for ${source.id}`} value={channel?.id ?? ''} onChange={(event) => state.setSourceChannel(source.id, event.target.value)}>{channelIds.map((id) => <option value={id} key={id}>{id}</option>)}</select></div>;
      })}
      {state.driveChannels.map((channel) => {
        const driverForm = state.channelDrivers[channel.id];
        const driverEligible = channel.source_ids.length === 1 && channel.motion === 'normal';
        return <div className="cad-channel" key={channel.id}>
          <div className="cad-channel-summary" data-control-reveal-id={CAD_CONTROLS.channelMotion.reveal.id}><span>{channel.id} · {channel.source_ids.join(' + ')}</span><select aria-label={`${CAD_CONTROLS.channelMotion.label} for ${channel.id}`} value={channel.motion} onChange={(event) => state.setChannelMotion(channel.id, event.target.value as 'normal' | 'axial')}><option value="normal">Normal motion</option><option value="axial">Axial motion</option></select></div>
          {driverEligible && <ToggleRow id={`cad-driver-${channel.id}`} label={`${CAD_CONTROLS.driverToggle.label} · ${channel.id}`} revealId={CAD_CONTROLS.driverToggle.reveal.id} help="Voltage-driven Thiele-Small coupling. The channel's levels become absolute at the drive voltage and its impedance chart becomes the electrical input impedance in ohms." checked={driverForm?.enabled ?? false} onChange={(checked) => state.setChannelDriverEnabled(channel.id, checked)}/>}
          {driverEligible && driverForm?.enabled && <DriverFields channel={channel} form={driverForm} onField={(field, value) => state.setChannelDriverField(channel.id, field, value)}/>}
        </div>;
      })}
    </div>
    {state.driveChannels.some((channel) => state.channelDrivers[channel.id]?.enabled)
      && <NumberField label={CAD_CONTROLS.driveVoltage.label} revealId={CAD_CONTROLS.driveVoltage.reveal.id} unit="V" value={state.driveVoltageV} min={0.01} step={0.1} precision={2} description="RMS voltage applied to every driver channel (2.83 V ≈ 1 W into 8 Ω)" onCommit={state.setDriveVoltage}/>}
  </>;
}

function CadCrossover() {
  const state = useCadReturnStore();
  if (state.driveChannels.length < 2) return <p className="section-note">Two or more drive channels are required for a combined output.</p>;
  return <>
    <ToggleRow id="cad-combine" label={CAD_CONTROLS.combinedOutput.label} revealId={CAD_CONTROLS.combinedOutput.reveal.id} help="Append an LR4 crossover sum of the drive channels as one more result channel. The chain runs lowest band first, ordered by the sources' return roles (LF → MF → HF)." checked={state.combineEnabled} onChange={state.setCombineEnabled}/>
    {state.combineEnabled && <>
      <p className="section-note">Untouched crossover defaults follow the current Frequency Sweep.</p>
      {combineChain(state).map((pair) => <NumberField key={pair.key} label={`${pair.lower} → ${pair.upper}`} revealId={CAD_CONTROLS.crossoverFrequency.reveal.id} unit="Hz" value={pair.hz} min={1} step={50} precision={0} description={CAD_CONTROLS.crossoverFrequency.label} onCommit={(value) => state.setCombineCrossover(pair.key, value)}/>)}
      <ToggleRow id="cad-combine-level" label={CAD_CONTROLS.levelMatch.label} revealId={CAD_CONTROLS.levelMatch.reveal.id} help="Equalise member band levels before summing. Defaults off when every member carries a driver model — real voltage-driven levels should not be re-equalised." checked={state.combineLevelMatch ?? combineLevelMatchDefault(state)} onChange={state.setCombineLevelMatch}/>
      <ToggleRow id="cad-combine-align" label={CAD_CONTROLS.timeAlign.label} revealId={CAD_CONTROLS.timeAlign.reveal.id} help="Delay each member so the crossover sums coherently, from the phase of the solved fields at each crossover frequency. Off sums the members as solved." checked={state.combineAlign ?? true} onChange={state.setCombineAlign}/>
    </>}
  </>;
}

function CadMeshDetail() {
  const state = useCadReturnStore();
  const cadCoordinator = useSyncExternalStore(
    cadLinkCoordinatorBridge.subscribe,
    cadLinkCoordinatorBridge.getSnapshot,
    cadLinkCoordinatorBridge.getSnapshot,
  );
  const driftSources = new Set([...state.areaDriftSourceIds, ...(state.ingestRecord?.role_findings ?? [])
    .filter((finding) => String(finding.kind).includes('area-drift'))
    .map((finding) => String(finding.source_id))]);
  return <>
    <div className="cad-mesh-intro"><p>Smaller values are finer. Curved CAD faces receive bounded extra refinement automatically.</p><button className="primary" disabled={cadCoordinator.ingesting || !state.selectedBundle?.readable} onClick={() => void cadCoordinator.ingest()}>{cadCoordinator.ingesting ? 'Preparing…' : 'Rebuild mesh'}</button></div>
    <NumberField label={CAD_CONTROLS.rigidSize.label} revealId={CAD_CONTROLS.rigidSize.reveal.id} unit="mm" value={state.rigidSizeMm} min={0.01} step={0.5} precision={2} description="Maximum target size for rigid CAD surfaces; tight curvature may be refined further." onCommit={state.setRigidSize}/>
    <NumberField label={CAD_CONTROLS.transitionSize.label} revealId={CAD_CONTROLS.transitionSize.reveal.id} unit="mm" value={state.transitionMm} min={0.01} step={0.5} precision={2} description="Maximum size transition between adjacent mesh regions." onCommit={state.setTransition}/>
    {(state.ingestRecord?.evidence?.fem_air_volumes?.length ?? 0) > 0 && <ToggleRow id="cad-exterior-only" label={CAD_CONTROLS.exteriorOnly.label} revealId={CAD_CONTROLS.exteriorOnly.reveal.id} help="Explicitly exclude the returned FEM air volumes. Phase 2 solves only the exterior Metal free-space problem." checked={state.exteriorOnly} onChange={state.setExteriorOnly}/>}
    {(state.selectedBundle?.sources ?? []).map((source) => <div className={`cad-source ${state.skippedSourceIds.includes(source.id) ? 'skipped' : ''}`} key={source.id}>
      <NumberField label={`${source.role} source`} revealId={CAD_CONTROLS.sourceSize.reveal.id} unit="mm" value={state.sourceSizesMm[source.id] ?? source.suggestedResolutionMm} min={0.01} step={0.25} precision={2} description={`${source.id} · suggested ${source.suggestedResolutionMm} mm`} disabled={state.skippedSourceIds.includes(source.id)} onCommit={(value) => state.setSourceSize(source.id, value)}/>
      {!source.required && <ToggleRow id={`skip-${source.id}`} label={CAD_CONTROLS.skipSource.label} revealId={CAD_CONTROLS.skipSource.reveal.id} help="Exclude this optional source from ingestion and the solve. This creates a blocking finding." checked={state.skippedSourceIds.includes(source.id)} onChange={(checked) => state.setSkipped(source.id, checked)}/>}
      {driftSources.has(source.id) && <ToggleRow id={`drift-${source.id}`} label={CAD_CONTROLS.areaDrift.label} revealId={CAD_CONTROLS.areaDrift.reveal.id} help="Explicitly accept the source-area mismatch and re-ingest. The override remains a finding that must be acknowledged." checked={state.areaDriftOverrides.includes(source.id)} onChange={(checked) => state.setAreaDriftOverride(source.id, checked)}/>}
    </div>)}
    {state.ingestStaleReason && <div className="field-error" role="status">{state.ingestStaleReason} Rebuild the mesh before solving.</div>}
    {cadCoordinator.error && <div className="field-error" role="alert">{cadCoordinator.error}</div>}
    {cadCoordinator.status && <p className="section-note" role="status">{cadCoordinator.status}</p>}
  </>;
}

function CadSimulationEmpty() {
  return <div className="cad-mode-empty" role="status"><b>Finish the Fusion CAD workflow</b><span>Use CAD Link to set up the connection, open this design in Fusion, and bring the finished geometry back for simulation.</span><button className="primary" onClick={() => workspaceNavigation.activate('cadlink')}>Open CAD Link setup</button></div>;
}

export function ParamPanel({ tab }: { tab: ParameterTab }) {
  const design = useDesignStore((state) => state.design);
  const designRevision = useDesignStore((state) => state.designRevision);
  const previewErrorFields = useSyncExternalStore(
    previewSocket.subscribe,
    () => previewSocket.getSnapshot().errorFields,
    () => previewSocket.getSnapshot().errorFields,
  );
  const previewErrorRevision = useSyncExternalStore(
    previewSocket.subscribe,
    () => previewSocket.getSnapshot().errorRevision,
    () => previewSocket.getSnapshot().errorRevision,
  );
  // Keep a superseded failure in the viewport's global explanation, but do not
  // pin it beside a value that the user has already changed.
  const currentPreviewFields = previewErrorRevision === designRevision ? previewErrorFields : null;
  const workspaceMode = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot).mode;
  const ingestRecord = useCadReturnStore((state) => state.ingestRecord);
  const setFamily = useDesignStore((state) => state.setFamily);
  const loadDesign = useDesignStore((state) => state.loadDesign);
  const helpVisible = useParameterHelp();
  const [query, setQuery] = useState('');
  const [freeformChoice, setFreeformChoice] = useState(false);
  const [conversionError, setConversionError] = useState<string | null>(null);
  const [converting, setConverting] = useState(false);
  const conversionGeneration = useRef(0);
  const searching = Boolean(query.trim());
  const definitions = useMemo(() => PARAMETER_SECTION_DEFINITIONS
    .filter((definition) => definition.tab === tab)
    .filter((definition) => parameterSectionIsVisible(definition, workspaceMode, design)), [design, tab, workspaceMode]);
  const fieldsBySection = useMemo(() => new Map(definitions.map(({ title }) => {
    const fields = PARAMETER_REGISTRY.filter((field) => field.section === title)
      .filter((field) => query.trim() ? fieldAppliesToFamily(field, design.formula) : fieldIsVisible(field, design))
      .filter((field) => fieldMatchesQuery(field, query));
    return [title, fields] as const;
  })), [definitions, design, query]);
  const matchingCadSections = useMemo(() => new Set(CAD_CONTROL_DESCRIPTORS
    .filter((descriptor) => workspaceMode === 'cad' && descriptor.tab === tab)
    .filter((descriptor) => cadControlIsAvailable(descriptor, Boolean(ingestRecord)))
    .filter((descriptor) => cadControlMatchesQuery(descriptor, query))
    .map((descriptor) => descriptor.section)), [ingestRecord, query, tab, workspaceMode]);
  const cadSectionMatches = (section: CadControlSection) => matchingCadSections.has(section);
  const matchingParametricSections = useMemo(() => new Set(PARAMETRIC_CONTROL_DESCRIPTORS
    .filter((descriptor) => workspaceMode === 'parametric' && descriptor.tab === tab)
    .filter((descriptor) => parametricControlMatchesQuery(descriptor, query))
    .map((descriptor) => descriptor.section)), [query, tab, workspaceMode]);

  useEffect(() => {
    const apply = () => {
      const detail = parameterRevealRequest.claim(tab);
      if (!detail) return;
      setQuery(detail.query);
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const entries = detail.target === 'control'
          ? document.querySelectorAll<HTMLElement>(`[data-param-tab="${tab}"] [data-control-reveal-id]`)
          : document.querySelectorAll<HTMLElement>(`[data-param-tab="${tab}"] [data-parameter-id]`);
        const revealIds = [detail.id, detail.fallbackId].filter((id): id is string => Boolean(id));
        const candidates = [...entries];
        // Prefer the leaf target. Conditional CAD controls (driver fields,
        // crossover options) name their owning section as a fallback so a
        // collapsed or currently disabled child still reveals useful context.
        const entry = detail.target === 'control'
          ? revealIds.map((id) => candidates.find((element) => element.dataset.controlRevealId === id)).find(Boolean)
          : candidates.find((element) => element.dataset.parameterId === detail.id);
        // jsdom has no scrollIntoView, and losing focus matters more than losing
        // the scroll, so never let the nicety take the necessity down with it.
        try { entry?.scrollIntoView({ block: 'center' }); } catch { /* not scrollable here */ }
        const focusTarget = entry?.querySelector<HTMLElement>('input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled])') ?? entry;
        focusTarget?.focus();
      }));
    };
    // Claim on mount and on every later request; whichever comes second is a
    // no-op, so this panel gets its request regardless of the order.
    apply();
    const unsubscribe = parameterRevealRequest.subscribe(apply);
    const onEvent = () => apply();
    window.addEventListener(REVEAL_PARAMETER_EVENT, onEvent);
    return () => {
      unsubscribe();
      window.removeEventListener(REVEAL_PARAMETER_EVENT, onEvent);
    };
  }, [tab]);

  useEffect(() => () => { conversionGeneration.current += 1; }, []);

  const renderField = (field: ParameterDefinition) => <div className="parameter-entry" data-parameter-id={field.id} data-parameter-key={field.legacyKey} tabIndex={field.kind === 'indicator' ? -1 : undefined} key={field.id}>
    <FieldControl field={field} design={design} serverError={previewErrorForParameter(field, currentPreviewFields)} />
    {field.kind !== 'number' && previewErrorForParameter(field, currentPreviewFields) && <div className="field-error" role="alert">{previewErrorForParameter(field, currentPreviewFields)}</div>}
  </div>;

  const renderRegistrySection = (definition: ParameterSectionDefinition) => {
    const fields = fieldsBySection.get(definition.title) ?? [];
    const showSolveDomain = definition.title === PARAMETRIC_CONTROLS.solveDomain.section
      && (!searching || matchingParametricSections.has(definition.title));
    const hasCustomMode = (definition.title === 'Wall & Enclosure' && !searching) || showSolveDomain;
    if (fields.length === 0 && !hasCustomMode) return null;
    return <Section
      key={definition.title}
      title={definition.title}
      description={definition.description}
      forceOpen={searching}
    >
      {definition.title === 'Wall & Enclosure' && <WallEnclosureModeControl design={design} />}
      {/* Domain is an execution option, not a saved design parameter. Keeping
          it independent of mesh.quadrants prevents the round-trip-only ATH
          field from masquerading as a second solve/export control, while its
          section remains hidden wholesale in CAD mode. */}
      {showSolveDomain && <SolveDomainControl design={design} />}
      {fields.map(renderField)}
      {searching && fields.some((field) => !fieldIsVisible(field, design)) && <p className="filter-note">Some matches are normally hidden by the active mode; they are shown here for discoverability.</p>}
    </Section>;
  };

  const modelTypeSection = <Section
    title="Model Type"
    description="Select the horn family that defines the profile equation and its primary dimensions."
    summary={design.formula}
    forceOpen={false}
  >
    <div className="select-row family-row">
      <label htmlFor="family">Family</label>
      <select id="family" value={design.formula} onChange={(event) => {
        const family = event.target.value as DesignFamily;
        if (family === 'FREEFORM' && design.formula !== 'FREEFORM') setFreeformChoice(true);
        else setFamily(family);
      }}>
        <option>OSSE</option><option>R-OSSE</option><option>ICW</option><option>FREEFORM</option>
      </select>
    </div>
    {freeformChoice && <div className="family-switch-choice" role="group" aria-label="Switch to FREEFORM">
      <b>Switch to FREEFORM</b><span>Choose how to initialize the editable profiles.</span>
      <div><button onClick={() => { setFamily('FREEFORM'); setFreeformChoice(false); }}>Start blank</button><button disabled={converting} onClick={() => {
        const conversion = ++conversionGeneration.current;
        const sourceRevision = useDesignStore.getState().designRevision;
        setConverting(true); setConversionError(null);
        void convertDesignToFreeform(design).then((converted) => {
          if (conversion !== conversionGeneration.current) return;
          if (useDesignStore.getState().designRevision !== sourceRevision) {
            setConversionError('The design changed while it was being converted. Review the edits and try again.');
            return;
          }
          loadDesign(converted);
          setFreeformChoice(false);
        }).catch((error) => {
          if (conversion === conversionGeneration.current) setConversionError(error instanceof Error ? error.message : String(error));
        }).finally(() => {
          if (conversion === conversionGeneration.current) setConverting(false);
        });
      }}>{converting ? 'Converting…' : 'Convert current design'}</button><button onClick={() => {
        conversionGeneration.current += 1;
        setConverting(false);
        setConversionError(null);
        setFreeformChoice(false);
      }}>Cancel</button></div>
      {conversionError && <div className="field-error" role="alert">{conversionError}</div>}
    </div>}
  </Section>;

  return (
    <div className="param-panel panel-scroll" data-param-tab={tab}>
      <div className="parameter-search">
        <label className="sr-only" htmlFor={`parameter-filter-${tab}`}>Filter {tab} parameters</label>
        <input id={`parameter-filter-${tab}`} type="search" value={query} placeholder="Filter labels or keys…" onChange={(event) => setQuery(event.target.value)} />
        {query && <button aria-label="Clear parameter filter" onClick={() => setQuery('')}>×</button>}
        <button
          className={`parameter-help-toggle${helpVisible ? ' on' : ''}`}
          aria-label="Show section descriptions"
          aria-pressed={helpVisible}
          title={helpVisible ? 'Hide section descriptions' : 'Show section descriptions'}
          onClick={() => parameterHelpStore.toggle()}
        ><Icon name="info" /></button>
      </div>
      {workspaceMode === 'cad' && tab === 'geometry' && cadSectionMatches(CAD_CONTROLS.linkedDesign.section) && <LinkedDesignCard forceOpen={searching}/>}
      {!searching && tab === 'geometry' && modelTypeSection}
      {workspaceMode === 'parametric' ? definitions.map((definition) => <div key={definition.title}>
          {renderRegistrySection(definition)}
          {!searching && definition.title === 'Frequency Sweep' && <Section title="Directivity Map" description="Polar planes and angular sampling used for directivity exports and plots." forceOpen={false}><DirectivityMapControls /></Section>}
          {!searching && definition.title === 'Source Definition' && <Section title="Solve options" description="Backend engine, validation, which frequencies get solved, and diagnostic output controls." forceOpen={false}><SolveOptionsControls /></Section>}
        </div>)
        : <>
          {definitions.map(renderRegistrySection)}
          {tab === 'geometry' && cadSectionMatches(CAD_CONTROLS.realizedDimensions.section) && <RealizedDimensionsCard forceOpen={searching}/>}
          {!searching && tab === 'simulation' && !ingestRecord && <CadSimulationEmpty/>}
          {tab === 'simulation' && ingestRecord && <>
            {cadSectionMatches(CAD_CONTROLS.frequencySweep.section) && <Section title={CAD_CONTROLS.frequencySweep.section} description="The explicit range submitted with this imported CAD geometry." forceOpen={searching} revealId={CAD_CONTROLS.frequencySweep.reveal.id}><CadFrequencySweep/></Section>}
            {cadSectionMatches(CAD_CONTROLS.directivityMap.section) && <Section title={CAD_CONTROLS.directivityMap.section} description="Display-plane and angular sampling controls, including the effective imported-CAD grid." forceOpen={searching} revealId={CAD_CONTROLS.directivityMap.reveal.id}><DirectivityMapControls effectiveDerivation={ingestRecord.polar_grid_derivation}/></Section>}
            {cadSectionMatches(CAD_CONTROLS.driveChannels.section) && <Section title={CAD_CONTROLS.driveChannels.section} description="Source-to-channel assignment, motion, voltage drive, and per-channel Thiele-Small data." forceOpen={searching} revealId={CAD_CONTROLS.driveChannels.reveal.id}><CadDriveChannels/></Section>}
            {cadSectionMatches(CAD_CONTROLS.crossover.section) && <Section title={CAD_CONTROLS.crossover.section} description="Optional LR4 combination of adjacent drive channels, including level and phase alignment choices." forceOpen={searching} revealId={CAD_CONTROLS.crossover.reveal.id}><CadCrossover/></Section>}
            {cadSectionMatches(CAD_CONTROLS.solveOptions.section) && <Section title={CAD_CONTROLS.solveOptions.section} description="Imported-CAD validation, frequency selection, and diagnostic controls. Geometry fixes the backend and domain." forceOpen={searching} revealId={CAD_CONTROLS.solveOptions.reveal.id}><SolveOptionsControls mode="cad" ingestRecord={ingestRecord}/></Section>}
            {cadSectionMatches(CAD_CONTROLS.meshDetail.section) && <Section title={CAD_CONTROLS.meshDetail.section} description="Imported-CAD surface sizing, optional-source policy, domain choice, and mesh regeneration." forceOpen={searching} revealId={CAD_CONTROLS.meshDetail.reveal.id}><CadMeshDetail/></Section>}
          </>}
        </>}
      {searching && [...fieldsBySection.values()].every((fields) => fields.length === 0) && matchingCadSections.size === 0 && <div className="parameter-empty">No parameter labels or keys match “{query}”.</div>}
    </div>
  );
}
