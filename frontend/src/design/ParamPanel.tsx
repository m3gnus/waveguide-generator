import { useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { convertDesignToFreeform } from '../api/designIo';
import { postSymmetry, toSolveDesign, type SymmetryResolution } from '../jobs/actions';
import { useDesignStore, type DesignDocument, type DesignFamily, type DesignValue } from '../stores/design';
import { useSolveOptionsStore, type SymmetryMode } from '../stores/solveOptions';
import { DirectivityMapControls, SolveOptionsControls } from './SolveOptionsSections';
import { EditablePointTable, EditableStationTable } from './FreeformEditors';
import { NumberField } from './NumberField';
import { Icon } from '../shell/icons';
import {
  PARAMETER_REGISTRY,
  PARAMETER_SECTION_DEFINITIONS,
  fieldAppliesToFamily,
  fieldAcceptsExpression,
  fieldIsVisible,
  fieldMatchesQuery,
  type ParameterDefinition,
  type ParameterSectionDefinition,
  type ParameterTab,
} from './parameterRegistry';
import './paramPanel.css';

interface SectionProps {
  title: string;
  summary: string;
  description?: string;
  children: ReactNode;
  forceOpen: boolean;
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

function Section({ title, summary, description, children, forceOpen }: SectionProps) {
  const [open, setOpen] = useState(() => storedSectionState(title));
  const helpVisible = useParameterHelp();
  const shownOpen = forceOpen || open;
  const toggle = () => {
    const next = !open;
    setOpen(next);
    try { localStorage.setItem(storageKey(title), String(next)); } catch { /* storage is optional */ }
  };
  return (
    <section className={`param-section${shownOpen ? '' : ' closed'}`} data-section={title}>
      <button className="section-head" onClick={toggle} aria-expanded={shownOpen}>
        <span className="chevron">⌄</span><span className="section-name">{title}</span><span className="spacer" />
        <span className="section-summary">{summary}</span>
      </button>
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
  return <div className={`field-row param-text-row${disabled ? ' field-disabled' : ''}`} title={disabled ? field.disabledReason : field.description}>
    <label className="field-label" htmlFor={`parameter-${field.id}`}>{field.label}</label>
    <input
      id={`parameter-${field.id}`}
      value={draft}
      disabled={disabled}
      spellCheck={false}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => { if (draft !== value) onCommit(draft); }}
      onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
    />
  </div>;
}

function passthroughStatus(field: ParameterDefinition, design: DesignDocument): string {
  const names = Object.keys(design.extra_blocks);
  if (field.id === 'passthrough.abec') {
    const count = names.filter((name) => name.toLocaleUpperCase().startsWith('ABEC')).length;
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

function FieldControl({ field, design }: { field: ParameterDefinition; design: DesignDocument }) {
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
    return <div className="passthrough-row"><span>{field.label}</span><b>{passthroughStatus(field, design)}</b></div>;
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
    return <div className={`select-row${disabled ? ' field-disabled' : ''}`} title={disabledReason ?? field.description}>
      <label htmlFor={`parameter-${field.id}`}>{field.label}</label>
      <select id={`parameter-${field.id}`} value={String(value ?? '')} disabled={disabled} onChange={(event) => {
        const option = field.options?.find((item) => String(item.value) === event.target.value);
        commit(option?.value ?? event.target.value);
      }}>
        {(field.options ?? []).map((option) => <option key={String(option.value)} value={String(option.value)}>{option.label}</option>)}
      </select>
    </div>;
  }
  if (field.kind === 'text') return <TextField field={field} value={String(value ?? '')} disabled={disabled} onCommit={commit} />;
  const error = validationMessage(field, design);
  return <>
    <NumberField
      label={field.label}
      symbol={field.symbol}
      value={typeof value === 'number' ? value : 0}
      expression={field.path ? design._expressions?.[field.path] : undefined}
      allowExpression={fieldAcceptsExpression(field)}
      unit={field.unit}
      min={field.min}
      max={field.max}
      step={field.step}
      precision={field.precision}
      disabled={disabled}
      disabledReason={disabledReason}
      invalidMessage={error}
      validate={(next) => prospectiveValidation(field, design, next)}
      onCommit={(next) => commit(next)}
      onCommitExpression={(expression) => { if (field.path && !disabled) updateExpression(field.path, expression); }}
      onBeginDrag={beginDrag}
      onEndDrag={endDrag}
    />
    {error && <div className="field-error">△ {error}</div>}
    {field.id === 'mesh.mouth_resolution' && <div className={`lambda-hint${Number(value) > 2.86 ? ' warning' : ''}`}>λ/6 at 20 kHz ≈ 2.86 mm</div>}
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

function QuadrantControl({ design, label }: { design: DesignDocument; label: string }) {
  const setQuadrants = useDesignStore((state) => state.setQuadrants);
  const symmetry = useSolveOptionsStore((state) => state.symmetry);
  const setSymmetry = useSolveOptionsStore((state) => state.setSymmetry);
  return <div className="quadrant-wrap">
    <div className="select-row">
      <label htmlFor="solve-symmetry">Solve domain</label>
      <select id="solve-symmetry" value={symmetry} onChange={(event) => setSymmetry(event.target.value as SymmetryMode)}>
        {(Object.keys(symmetryModeLabels) as SymmetryMode[]).map((mode) => <option key={mode} value={mode}>{symmetryModeLabels[mode]}</option>)}
      </select>
    </div>
    {symmetry === 'auto' && <AutoSymmetryReadout design={design} />}
    <span className="quadrant-title">{label}</span>
    {/* The tile grid and its readout are the only pair that belongs side by
        side; everything else in this control is a full-width row. */}
    <div className="quadrant-picker">
      <div className="quadrants">
        {[2, 1, 3, 4].map((quadrant) => <button key={quadrant} className={design.quadrants.includes(quadrant) ? 'on' : ''} onClick={() => {
          const next = design.quadrants.includes(quadrant) ? design.quadrants.filter((item) => item !== quadrant) : [...design.quadrants, quadrant];
          if (next.length) setQuadrants(next);
        }}>Q{quadrant}</button>)}
      </div>
      <div className="quadrant-meta">
        <b>{domainName(design.mesh.quadrants)}</b>
        <span>{design.quadrants.length} of 4 quadrants</span>
        <em>schema mask {design.mesh.quadrants}</em>
      </div>
    </div>
    {symmetry === 'auto' && <p className="section-note">This is the design&rsquo;s own ATH <code>Mesh.Quadrants</code> field and is saved as written. Auto decides the solved domain instead.</p>}
  </div>;
}

export const REVEAL_PARAMETER_EVENT = 'wg:reveal-parameter';

export interface RevealRequest { id: string; tab: ParameterTab; query: string }

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

export function ParamPanel({ tab }: { tab: ParameterTab }) {
  const design = useDesignStore((state) => state.design);
  const setFamily = useDesignStore((state) => state.setFamily);
  const loadDesign = useDesignStore((state) => state.loadDesign);
  const helpVisible = useParameterHelp();
  const [query, setQuery] = useState('');
  const [freeformChoice, setFreeformChoice] = useState(false);
  const [conversionError, setConversionError] = useState<string | null>(null);
  const [converting, setConverting] = useState(false);
  const searching = Boolean(query.trim());
  const fieldsBySection = useMemo(() => new Map(PARAMETER_SECTION_DEFINITIONS.filter((definition) => definition.tab === tab).map(({ title }) => {
    const fields = PARAMETER_REGISTRY.filter((field) => field.section === title)
      .filter((field) => query.trim() ? fieldAppliesToFamily(field, design.formula) : fieldIsVisible(field, design))
      .filter((field) => fieldMatchesQuery(field, query));
    return [title, fields] as const;
  })), [design, query, tab]);

  useEffect(() => {
    const apply = () => {
      const detail = parameterRevealRequest.claim(tab);
      if (!detail) return;
      setQuery(detail.query);
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const entry = [...document.querySelectorAll<HTMLElement>(`[data-param-tab="${tab}"] [data-parameter-id]`)]
          .find((element) => element.dataset.parameterId === detail.id);
        // jsdom has no scrollIntoView, and losing focus matters more than losing
        // the scroll, so never let the nicety take the necessity down with it.
        try { entry?.scrollIntoView({ block: 'center' }); } catch { /* not scrollable here */ }
        entry?.querySelector<HTMLElement>('input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled])')?.focus();
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

  const renderField = (field: ParameterDefinition) => <div className="parameter-entry" data-parameter-id={field.id} data-parameter-key={field.legacyKey} key={field.id}>
    {field.id === 'mesh.quadrants' ? <QuadrantControl design={design} label={field.label} /> : <FieldControl field={field} design={design} />}
  </div>;

  const renderRegistrySection = (definition: ParameterSectionDefinition) => {
    const fields = fieldsBySection.get(definition.title) ?? [];
    const hasCustomMode = definition.title === 'Wall & Enclosure' && !searching;
    if (fields.length === 0 && !hasCustomMode) return null;
    return <Section
      key={definition.title}
      title={definition.title}
      description={definition.description}
      summary={`${fields.length} ${fields.length === 1 ? 'field' : 'fields'}`}
      forceOpen={searching}
    >
      {definition.title === 'Wall & Enclosure' && <WallEnclosureModeControl design={design} />}
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
        const sourceRevision = useDesignStore.getState().designRevision;
        setConverting(true); setConversionError(null);
        void convertDesignToFreeform(design).then((converted) => {
          if (useDesignStore.getState().designRevision !== sourceRevision) {
            setConversionError('The design changed while it was being converted. Review the edits and try again.');
            return;
          }
          loadDesign(converted);
          setFreeformChoice(false);
        }).catch((error) => setConversionError(error instanceof Error ? error.message : String(error))).finally(() => setConverting(false));
      }}>{converting ? 'Converting…' : 'Convert current design'}</button><button onClick={() => setFreeformChoice(false)}>Cancel</button></div>
      {conversionError && <div className="field-error" role="alert">{conversionError}</div>}
    </div>}
  </Section>;

  const definitions = PARAMETER_SECTION_DEFINITIONS.filter((definition) => definition.tab === tab);

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
      {!searching && tab === 'geometry' && modelTypeSection}
      {definitions.map((definition) => <div key={definition.title}>
        {renderRegistrySection(definition)}
        {!searching && definition.title === 'Frequency Sweep' && <Section title="Directivity Map" description="Polar planes and angular sampling used for directivity exports and plots." summary="11 controls" forceOpen={false}><DirectivityMapControls /></Section>}
        {!searching && definition.title === 'Source Definition' && <Section title="Solve options" description="Backend engine, validation, which frequencies get solved, and diagnostic output controls." summary="5 options" forceOpen={false}><SolveOptionsControls /></Section>}
      </div>)}
      {searching && [...fieldsBySection.values()].every((fields) => fields.length === 0) && <div className="parameter-empty">No parameter labels or keys match “{query}”.</div>}
    </div>
  );
}
