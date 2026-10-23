import { osseFeatures, osseFormula, OSSE_FEATURE_CONFLICT, OSSE_SWITCH_NOTICE, type OsseFeatures } from './osseFeatures';
import { FreeformProfileInspection } from './ProfileInspection';
import { registerParameterPanel } from './parameterPanelValidity';
import { nativeSymmetryLimitation } from './nativeFamilies';
import { EnclosureResolutionField } from './EnclosureResolutionField';
import { ExteriorDriverFields, ExteriorDriverOptions } from './ExteriorDriverOptions';
import { OpenclUnavailableHook } from './OpenclUnavailableHook';
import { Fragment, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { convertDesignToFreeform } from '../api/designIo';
import { previewSocket } from '../api/previewSocket';
import type { CadRealizedDimensions, CadRealizedParameter } from '../api/cadlink';
import { importedSubmissionBlocker, importedSubmissionNotices } from '../jobs/importedSubmission';
import { postSymmetry, toSolveDesign, type SymmetryResolution } from '../jobs/actions';
import { useActiveBackendCapability, useCapabilities, usePlannedBackendCapabilities } from '../jobs/useCapabilities';
import { backendLimitation, symmetryDomainLimitation } from './backendSupport';
import { cadApplicationName, usePreferences } from '../prefs/preferences';
import { CadCrossover } from './CrossoverSection';
import { useCadPreparationStore } from '../stores/cadPreparation';
import {
  assignableChannelIds,
  channelAcceptsDriver,
  channelDriverPresent,
  hasPassiveCardioidSurface,
  PASSIVE_CARDIOID_CHANNEL_ID,
  passiveCardioidBlocker,
  useCadReturnStore,
  type CadDriveChannel,
  type PortAreaSource,
} from '../stores/cadReturn';
import { ExteriorDriverLibraryPicker, ChannelDriverPicker } from './DriverPicker';
import { useWaveguideDefinitionApplies } from '../stores/waveguideLink';
import { defaultWallThickness, isNativeFamily, useDesignStore, type DesignDocument, type DesignFamily, type DesignValue } from '../stores/design';
import { athControlError, lipRadiusWallError, lipUnavailableReason, opusClampNote, opusMountingReason } from './athImportControls';
import { namespaceStorage } from '../stores/durableSettings';
import type { SymmetryMode } from '../stores/solveOptions';
import { SolveOptionsStoreProvider, useScopedSolveOptions } from '../stores/solveOptionsContext';
import { workspaceModeStore } from '../stores/workspaceMode';
import { AdaptiveFrequencyControls, FrequencySweepControls, MeshValidationControl, DirectivityMapControls, SolveOptionsControls, ToggleRow } from './SolveOptionsSections';
import { ExperimentalBadge } from './ExperimentalBadge';
import { EditableLookupTable, EditablePointTable, EditableStationTable, FreeformPointImport, FREEFORM_POSITION_NOTE, freeformAngleOverride } from './FreeformEditors';
import { lambdaSixthHint } from './lambdaLimit';
import { NumberField } from './NumberField';
import { SelectRow } from './SelectRow';
import { HelpTipRow } from './HelpTip';
import { Icon } from '../shell/icons';
import {
  LEGACY_PARAMETER_REGISTRY,
  CAD_PARAMETER_SECTION_DEFINITIONS,
  PARAMETER_REGISTRY,
  PARAMETER_SECTION_DEFINITIONS,
  fieldAppliesToFamily,
  fieldAcceptsExpression,
  fieldIsVisible,
  fieldMatchesQuery,
  fieldOptionsForBackend,
  fieldUnsupportedFeature,
  parameterSectionIsVisible,
  parameterGroup,
  throatStretchError,
  type ParameterDefinition,
  type ParameterSectionDefinition,
  type ParameterTab,
  optionKey,
} from './parameterRegistry';
import { cadWorkspace } from '../stores/cadWorkspace';
import { useCadFeedbackError } from '../shell/cadSetupPublisher';
import { workspaceNavigation } from '../shell/workspaceNavigation';
import {
  CAD_CARDIOID_FIELD_CONTROLS,
  CAD_CONTROLS,
  CAD_CONTROL_DESCRIPTORS,
  cadControlIsAvailable,
  cadControlMatchesQuery,
  cadDisplayValue,
  type CadControlSection,
} from './cadControlRegistry';
import {
  PARAMETRIC_CONTROLS,
  PARAMETRIC_CONTROL_DESCRIPTORS,
  parametricControlMatchesQuery,
  parametricControlIsAvailable,
} from './parametricControlRegistry';
import './paramPanel.css';
import { RunNameField } from '../shell/RunNameField';
import { MeshImportPanel } from './MeshImportPanel';

interface SectionProps {
  title: string;
  summary?: string;
  description?: string;
  children: ReactNode;
  forceOpen: boolean;
  revealId?: string;
}

/**
 * Which parameter sections the user has collapsed.
 *
 * These were one `localStorage` key per section title, which is the one piece
 * of remembered state that could not move to a fixed durable namespace as it
 * stood. They are kept as a single map instead, so collapsing the sections you
 * do not use survives the same things every other setting now survives. The
 * legacy keys are read once so an existing rail layout carries over.
 */
const LEGACY_SECTION_PREFIX = 'wg-param-section-open:';
const sectionStorage = namespaceStorage('paramSections');

function readSectionMap(): Record<string, boolean> {
  try {
    const raw = sectionStorage.getItem('paramSections');
    if (raw === null) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>)
        .filter((entry): entry is [string, boolean] => typeof entry[1] === 'boolean'),
    );
  } catch {
    return {};
  }
}

/**
 * Fold the pre-migration keys into the map once, on load.
 *
 * Reading them lazily per section would honour an existing rail layout but
 * never make it durable, so the very loss this store was moved to survive
 * would still take it. Publishing here means one collapsed-section layout
 * carries over, once, and is then held like every other setting.
 */
function migrateLegacySectionKeys(): void {
  try {
    if (sectionStorage.getItem('paramSections') !== null) return;
    const legacy: Record<string, boolean> = {};
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (key === null || !key.startsWith(LEGACY_SECTION_PREFIX)) continue;
      legacy[key.slice(LEGACY_SECTION_PREFIX.length)] = localStorage.getItem(key) === 'true';
    }
    if (Object.keys(legacy).length) sectionStorage.setItem('paramSections', JSON.stringify(legacy));
  } catch { /* storage is optional; defaults are a working rail */ }
}

migrateLegacySectionKeys();

function storedSectionState(title: string): boolean {
  const stored = readSectionMap()[title];
  return stored === undefined ? !['Export', 'Experimental', 'Advanced ICW', 'Import points'].includes(title) : stored;
}

function writeSectionState(title: string, open: boolean): void {
  try {
    sectionStorage.setItem('paramSections', JSON.stringify({ ...readSectionMap(), [title]: open }));
  } catch { /* storage is optional */ }
}

/**
 * Section blurbs explain the rail once and then cost height on every scroll —
 * a third of a metre of prose across the geometry tab. They are off by default
 * and toggled for the whole rail at once, so the explanation stays one click
 * away instead of permanently between the reader and the next field.
 */
const helpStorage = namespaceStorage('paramHelp');

function storedHelpVisible(): boolean {
  try { return helpStorage.getItem('paramHelp') === 'true'; } catch { return false; }
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
    try { helpStorage.setItem('paramHelp', String(this.value)); } catch { /* storage is optional */ }
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
    writeSectionState(title, next);
  };
  return (
    <section role="group" aria-label={title} className={`param-section${shownOpen ? '' : ' closed'}`} data-section={title} data-control-reveal-id={revealId}>
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

/**
 * Mirrors `outer_body_mode` in server/preview/translate.py exactly.
 *
 * The two implementations have to agree on "unset" as well as on a value. The
 * server keeps `mesh.wall_thickness` nullable so a CFG round-trip is lossless
 * and resolves an omitted wall to ATH's 5 mm (0 under an infinite baffle).
 * Hydration and edits refresh that display value while `_absent` keeps the
 * document unset. Only an explicit 0 means a bare shell.
 */
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

function WallEnclosureModeControl({ design, mounting = false, serverError }: { design: DesignDocument; mounting?: boolean; serverError?: string }) {
  const updateValues = useDesignStore((state) => state.updateValues);
  const [lastWallThickness, setLastWallThickness] = useState<number | null>(null);
  const [lastEnclosureDepth, setLastEnclosureDepth] = useState(() => design.enclosure.depth > 0 ? design.enclosure.depth : 280);
  const backend = useActiveBackendCapability();
  const backendPlan = usePlannedBackendCapabilities();
  const { engines, hostPlatform } = useCapabilities();
  const simulationField = LEGACY_PARAMETER_REGISTRY.find((field) => field.id === 'simulation.sim_type')!;
  const options = fieldOptionsForBackend(simulationField, design.simulation.sim_type, backend, backendPlan, engines);
  const baffleOption = options.find((option) => option.value === 'infinite-baffle');
  const unsupported = fieldUnsupportedFeature(simulationField, design.simulation.sim_type, backend, backendPlan);
  const ibEngine = backendPlan.find((candidate) => candidate.mountings?.includes('infinite-baffle'));
  const selectedMode = mounting ? resolveOuterBodyMode(design) : selectedOuterBodyMode(design);
  const resolvedMode = resolveOuterBodyMode(design);

  useEffect(() => {
    if (design.mesh.wall_thickness > 0 && !design._absent?.includes('mesh.wall_thickness')) setLastWallThickness(design.mesh.wall_thickness);
  }, [design.mesh.wall_thickness, design._absent]);
  useEffect(() => {
    if (design.enclosure.depth > 0) setLastEnclosureDepth(design.enclosure.depth);
  }, [design.enclosure.depth]);

  const chooseMode = (mode: OuterBodyMode) => {
    if (mounting && mode === 'infinite-baffle') {
      updateValues({ 'simulation.sim_type': 'infinite-baffle' });
      return;
    }
    const updates = mode === 'bare'
      ? { 'mesh.wall_thickness': 0, 'enclosure.depth': 0 }
      : mode === 'freestanding'
        ? { 'mesh.wall_thickness': lastWallThickness ?? defaultWallThickness(), 'enclosure.depth': 0 }
        : { 'mesh.wall_thickness': 0, 'enclosure.depth': lastEnclosureDepth };
    // Write only what changes. An import without Mesh.Enclosure keeps `enclosure`
    // in _absent, and any write under it (even depth 0 over 0) would drop that
    // marker and send a full object, which the mesher refuses with a lip or Opus.
    // An unset wall is ATH's 5 mm (0 only while infinite baffle hides it), so a
    // bare or enclosed body always needs an explicit 0, and a free-standing
    // one keeps the unset wall. An unset enclosure stays unset at depth 0.
    const wallUnset = Boolean(design._absent?.includes('mesh.wall_thickness'));
    const enclosureUnset = Boolean(design._absent?.includes('enclosure'));
    const changed: Record<string, number> = {};
    const wall = updates['mesh.wall_thickness'];
    if (wallUnset ? wall === 0 : design.mesh.wall_thickness !== wall) changed['mesh.wall_thickness'] = wall;
    const depth = updates['enclosure.depth'];
    if (enclosureUnset ? depth !== 0 : design.enclosure.depth !== depth) changed['enclosure.depth'] = depth;
    updateValues({ ...changed, ...(mounting && design.simulation.sim_type === 'infinite-baffle' ? { 'simulation.sim_type': 'freestanding' } : {}) });
  };

  return <div className="outer-body-mode" data-parameter-id={mounting ? 'simulation.sim_type' : undefined} data-control-reveal-id={mounting ? PARAMETRIC_CONTROLS.mounting.reveal.id : undefined}>
    <SelectRow text={mounting ? simulationField.description : undefined}>
      <label htmlFor="outer-body-mode">{mounting ? 'Mounting' : 'Outer body'}</label>
      <select id="outer-body-mode" aria-invalid={serverError ? true : undefined} value={selectedMode} onChange={(event) => chooseMode(event.target.value as OuterBodyMode)}>
        <option value="bare">Bare shell</option>
        <option value="freestanding">Thickened waveguide (freestanding)</option>
        <option value="enclosure">Waveguide in enclosure</option>
        {mounting && baffleOption && <option value="infinite-baffle" disabled={Boolean(baffleOption.unavailableReason)} title={baffleOption.unavailableReason}>Infinite baffle</option>}
      </select>
    </SelectRow>
    {mounting && selectedMode === 'infinite-baffle' && <p className="section-note">Infinite baffle ignores the wall and enclosure; they are kept for when you switch back.</p>}
    {mounting && serverError && <div className="field-error" role="alert">{serverError}</div>}
    {!mounting && <div className={`resolved-mode${resolvedMode === 'infinite-baffle' ? ' override' : ''}`}>
      <span>Resolved mode</span><b>{outerModeLabels[resolvedMode]}</b>
      {resolvedMode === 'infinite-baffle' && <small>Infinite baffle simulation overrides the outer body.</small>}
    </div>}
    {mounting && baffleOption?.unavailableReason && <div className="field-warning" role="status">Infinite baffle: {baffleOption.unavailableReason}</div>}
    {mounting && unsupported && <div className="field-warning" role="status">{backendLimitation(backend, unsupported, backendPlan)}</div>}
    {mounting && ibEngine?.name === 'bempp' && ibEngine.assembly_backend === 'numba' && <OpenclUnavailableHook platform={hostPlatform} engine={ibEngine} notice="infinite_baffle_numba" />}
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
      title={draft}
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
  if (field.id === 'icw.icw_seed') return design.icw_seed != null ? 'Seed present' : 'Not present';
  if (field.id === 'icw.icw_coeffs') return design.icw_coeffs != null ? `${design.icw_coeffs.length} coefficients present` : 'Not present';
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
  if (field.id === 'mesh.ath_roundover.radius' || field.id === 'mesh.wall_thickness') return lipRadiusWallError(design);
  return undefined;
}

/** A stored value that breaks the field's own bounds (an imported or hand-edited file). */
function storedValueError(field: ParameterDefinition, design: DesignDocument): string | undefined {
  const value = getAtPath(design, field.path);
  if (field.kind !== 'number' || typeof value !== 'number' || design._expressions?.[field.path ?? '']) return undefined;
  if (field.min !== undefined && (value < field.min || (field.exclusiveMin && value === field.min))) return 'Outside the allowed range.';
  if (field.max !== undefined && value > field.max) return 'Outside the allowed range.';
  return undefined;
}

function prospectiveValidation(field: ParameterDefinition, design: DesignDocument, value: number): string | undefined {
  if (['icw.kappa_abs_max', 'icw.dkappa_ds_abs_max', 'icw.icw_S'].includes(field.id) && value <= 0) return 'Must be greater than zero.';
  if (field.id === 'icw.theta_max_deg' && value <= 90) return 'Must be greater than 90° and at most 180°.';
  const athError = athControlError(field.path, value);
  if (athError) return athError;
  if (field.id.startsWith('cross_section.') && value <= 0) return 'Must be greater than zero.';
  if (field.id === 'mesh.ath_roundover.radius') return lipRadiusWallError(design, { radius: value });
  if (field.id === 'mesh.wall_thickness') return lipRadiusWallError(design, { wall: value });
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
  const frequencyMode = useScopedSolveOptions((state) => state.frequencyMode);
  const frequencyListText = useScopedSolveOptions((state) => state.frequencyListText);
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
  const backend = useActiveBackendCapability();
  // AUTO can skip a mounting-incompatible default and use another advertised
  // candidate; an explicit selection remains limited to that one backend.
  const backendPlan = usePlannedBackendCapabilities();
  const { engines: hostEngines, hostPlatform } = useCapabilities();
  const updateValue = useDesignStore((state) => state.updateValue);
  const updateValues = useDesignStore((state) => state.updateValues);
  const updateExpression = useDesignStore((state) => state.updateExpression);
  const beginDrag = useDesignStore((state) => state.beginDrag);
  const endDrag = useDesignStore((state) => state.endDrag);
  const value = getAtPath(design, field.path);
  const modeReason = field.disabledWhen?.(design);
  const disabledReason = field.disabledReason ?? modeReason;
  const disabled = Boolean(disabledReason);
  // Session memory so Off then On, or Opus to 1 to Opus, restores what was set.
  const lastLip = useRef<{ radius: number; segments: number } | null>(null);
  const lastOpus = useRef<{ offset: number; step: number } | null>(null);
  // Forget it when another design is loaded, so one import's lip never lands on the next.
  const loadGeneration = useDesignStore((state) => state.loadGeneration);
  useEffect(() => { lastLip.current = null; lastOpus.current = null; }, [loadGeneration]);
  const commit = (next: DesignValue) => {
    if (!field.path || disabled) return;
    if (field.id === 'mesh.ath_roundover') {
      if (next === 1) {
        if (lipUnavailableReason(design)) return;
        const remembered = lastLip.current;
        updateValue(field.path, remembered && remembered.radius > design.mesh.wall_thickness
          ? { ...remembered }
          : { radius: Math.max(10, design.mesh.wall_thickness + 5), segments: 5 });
      } else {
        if (design.mesh.ath_roundover) lastLip.current = { ...design.mesh.ath_roundover };
        updateValue(field.path, null);
      }
      return;
    }
    if (field.id === 'mesh.ath_rear_shape') {
      if (next === 3 && opusMountingReason(design)) return;
      if (next !== 3 && design.mesh.ath_rear_shape === 3) {
        lastOpus.current = { offset: design.mesh.ath_rear_front_offset ?? 40, step: design.mesh.ath_rear_step ?? 25 };
      }
      updateValues({
        [field.path]: next === '' ? null : next,
        'mesh.ath_rear_front_offset': next === 3 ? design.mesh.ath_rear_front_offset ?? lastOpus.current?.offset ?? 40 : null,
        'mesh.ath_rear_step': next === 3 ? design.mesh.ath_rear_step ?? lastOpus.current?.step ?? 25 : null,
      });
      return;
    }
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
    if (field.id === 'lookup.profile') return <EditableLookupTable field={field} points={design.lookup_profile ?? []} />;
    if (field.id === 'freeform.crossSections') return <EditableStationTable field={field} stations={Array.isArray(value) ? value : []} />;
    return <EditablePointTable field={field} points={Array.isArray(value) ? value : []} />;
  }
  if (field.disabledReason && !field.path) {
    return <div className="schema-gap" title={field.disabledReason}>
      <span>{field.label}</span><button disabled>{field.kind === 'toggle' ? 'Off' : 'Unavailable'}</button><small>{field.disabledReason}</small>
    </div>;
  }
  if (field.kind === 'select' || field.kind === 'toggle') {
    const options = fieldOptionsForBackend(field, value, backend, backendPlan, hostEngines).map((option) =>
      field.id === 'mesh.ath_rear_shape' && option.value === 3 ? { ...option, unavailableReason: opusMountingReason(design) }
        : field.id === 'mesh.ath_roundover' && option.value === 1 ? { ...option, unavailableReason: lipUnavailableReason(design) }
          : option);
    const selectedValue = field.id === 'mesh.ath_roundover' ? value ? 1 : 0 : value;
    const unsupported = fieldUnsupportedFeature(field, value, backend, backendPlan);
    const ibEngine = field.id === 'simulation.sim_type'
      ? backendPlan.find((candidate) => candidate.mountings?.includes('infinite-baffle'))
      : undefined;
    return <>
      <SelectRow className={disabled ? 'field-disabled' : undefined} text={field.description}>
        <label htmlFor={`parameter-${field.id}`} title={disabledReason}>{field.label}</label>
        <select id={`parameter-${field.id}`} value={optionKey(selectedValue)} disabled={disabled} onChange={(event) => {
          const option = field.options?.find((item) => optionKey(item.value) === event.target.value);
          commit(option ? option.value : event.target.value);
        }}>
          {options.map((option) => <option key={optionKey(option.value)} value={optionKey(option.value)} disabled={Boolean(option.unavailableReason)} title={option.unavailableReason}>{option.label}</option>)}
        </select>
      </SelectRow>
      {ibEngine?.name === 'bempp' && ibEngine.assembly_backend === 'numba' &&
        <OpenclUnavailableHook platform={hostPlatform} engine={ibEngine} notice="infinite_baffle_numba" />}
      {/* Listed but disabled: no engine on this host can run it. Say why here,
          because a title tooltip on a disabled option is easy to miss. */}
      {options.filter((option) => option.unavailableReason).map((option) => <div key={`unavailable-${String(option.value)}`} className="field-warning" role="status">{option.label}: {option.unavailableReason}</div>)}
      {field.id === 'mesh.ath_rear_shape' && opusClampNote(design) && <p className="section-note" role="status">{opusClampNote(design)}</p>}
      {/* The value survived the filter only because the design already holds
          it, so say plainly what will happen rather than leaving a control
          that looks ordinary and then fails a minute into the solve. */}
      {unsupported && <div className="field-warning" role="status">{backendLimitation(backend, unsupported, backendPlan)}</div>}
    </>;
  }
  if (field.id === 'enclosure.front_resolution' || field.id === 'enclosure.back_resolution') return <EnclosureResolutionField field={field} design={design} value={value} onCommit={commit} />;
  if (field.kind === 'text') return <TextField field={field} value={String(value ?? '')} disabled={disabled} onCommit={commit} />;
  const error = validationMessage(field, design);
  const optional = field.optional || field.path === 'mesh.max_edge';
  return <>
    <NumberField
      label={field.label}
      symbol={field.symbol}
      description={field.description}
      value={typeof value === 'number' ? value : field.id === 'mesh.ath_rear_front_offset' ? 40 : field.id === 'mesh.ath_rear_step' ? 25 : field.path === 'enclosure.depth_margin_mm' ? 1 : optional ? undefined : 0}
      expression={field.path ? design._expressions?.[field.path] : undefined}
      allowExpression={fieldAcceptsExpression(field)}
      expressionTupleSize={field.id === 'guide.superformula' ? 6 : undefined}
      unit={field.unit}
      min={field.min}
      exclusiveMin={field.exclusiveMin}
      max={field.max}
      step={field.step}
      precision={field.precision}
      disabled={disabled}
      disabledReason={disabledReason}
      invalidMessage={error ?? serverError}
      validate={(next) => prospectiveValidation(field, design, next)}
      formatRangeError={field.id.startsWith('mesh.ath_') ? (next) => athControlError(field.path, next) : field.path === 's1' || field.path === 's2'
        ? (next) => throatStretchError(field.path as 's1' | 's2', next) : undefined}
      onCommit={(next) => commit(next)}
      optional={optional}
      placeholder={field.placeholderWhen?.(design)}
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
  const planned = usePlannedBackendCapabilities();
  const selected = useActiveBackendCapability();
  const engine = useScopedSolveOptions((state) => state.engine);
  const candidates = engine === 'auto' ? planned : selected ? [selected] : [];
  const accuracy = useScopedSolveOptions((state) => state.accuracy);
  const symmetry = useScopedSolveOptions((state) => state.symmetry);
  const setSymmetry = useScopedSolveOptions((state) => state.setSymmetry);
  const native = isNativeFamily(design.formula);
  const modes: SymmetryMode[] = native ? ['full'] : Object.keys(symmetryModeLabels) as SymmetryMode[];
  // Saved Auto still resolves to Full for native formulas. Keep an invalid
  // saved domain explicit until the user selects Full.
  const selectedMode = native ? symmetry === 'auto' || symmetry === 'full' ? 'full' : '' : symmetry;
  const limitation = (mode: SymmetryMode) => {
    if (accuracy === 'accurate' && mode === 'half_xz') return 'Accurate cannot solve half xz domains. Choose Fast or another domain.';
    if (!candidates.length || candidates.some((candidate) => !symmetryDomainLimitation(candidate, mode))) return undefined;
    return engine === 'auto'
      ? `No available engine in the automatic plan supports ${mode.replaceAll('_', ' ')} domains.`
      : symmetryDomainLimitation(candidates[0], mode);
  };
  return <div className="solve-domain-control" data-control-reveal-id={PARAMETRIC_CONTROLS.solveDomain.reveal.id}>
    <div className="select-row">
      <label htmlFor="solve-symmetry">Solve domain</label>
      <select id="solve-symmetry" value={selectedMode} onChange={(event) => setSymmetry(event.target.value as SymmetryMode)}>
        {selectedMode === '' && <option value="" disabled hidden>Select Full</option>}
        {modes.map((mode) => <option key={mode} value={mode} disabled={Boolean(limitation(mode))}>{symmetryModeLabels[mode]}{limitation(mode) ? ' · unsupported' : ''}</option>)}
      </select>
    </div>
    {modes.filter((mode) => limitation(mode)).map((mode) => <small key={mode}>{limitation(mode)}</small>)}
    {nativeSymmetryLimitation(design.formula, symmetry) && <small className="field-error" role="alert">{nativeSymmetryLimitation(design.formula, symmetry)}</small>}
    {!native && symmetry === 'auto' && <AutoSymmetryReadout design={design} />}
    {!native && resolveOuterBodyMode(design) === 'enclosure' && <small>Unequal reflected front/back quadrant sizes prevent that symmetry reduction.</small>}
    {native && <small>This native formula requires the full circular mesh domain.</small>}
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
  const cadWorkspaceView = useSyncExternalStore(
    cadWorkspace.subscribe,
    cadWorkspace.getSnapshot,
    cadWorkspace.getSnapshot,
  );
  return <RealizedDimensionsSection
    snapshot={cadWorkspaceView.fusionStatus?.realizedDimensions ?? null}
    driftedParameters={cadWorkspaceView.fusionStatus?.link?.driftedParameters}
    forceOpen={forceOpen}
  />;
}

function CadFrequencySweep() {
  const cadReturn = useCadReturnStore();
  const solveStore = useScopedSolveOptions();
  const blocker = importedSubmissionBlocker(cadReturn, solveStore);
  const rangeMessage = blocker === 'Enter a valid explicit frequency sweep.' ? blocker : null;
  return <>
    <NumberField label={CAD_CONTROLS.sweepStart.label} revealId={CAD_CONTROLS.sweepStart.reveal.id} unit="Hz" value={cadReturn.frequencyStartHz} min={1} step={10} precision={0} description="Lowest frequency of the generated imported-CAD sweep." onCommit={(frequencyStartHz) => cadReturn.setSweep({ frequencyStartHz })}/>
    <NumberField label={CAD_CONTROLS.sweepEnd.label} revealId={CAD_CONTROLS.sweepEnd.reveal.id} unit="Hz" value={cadReturn.frequencyEndHz} min={1} step={10} precision={0} description="Highest frequency of the generated imported-CAD sweep." onCommit={(frequencyEndHz) => cadReturn.setSweep({ frequencyEndHz })}/>
    <NumberField label={CAD_CONTROLS.frequencySamples.label} revealId={CAD_CONTROLS.frequencySamples.reveal.id} value={cadReturn.frequencyCount} min={1} max={401} step={1} precision={0} description="How many frequencies are solved across the imported-CAD range." onCommit={(frequencyCount) => cadReturn.setSweep({ frequencyCount })}/>
    {rangeMessage && <div className="field-error" role="alert">{rangeMessage}</div>}
  </>;
}

/**
 * What a drive-channel header names.
 *
 * A channel and the single source feeding it say the same thing twice --
 * `drive-mf · source-mf` -- so the source ids are named only when they carry
 * something the channel id does not: a channel driven by more than one source.
 */
export function channelHeadingText(channel: Pick<CadDriveChannel, 'id' | 'source_ids'>): string {
  return channel.source_ids.length > 1 ? `${channel.id} · ${channel.source_ids.join(' + ')}` : channel.id;
}

/**
 * Per-channel driver setup.
 *
 * One card per drive channel, each carrying its motion and its driver picker.
 * There is no driver on/off switch and no source-to-channel assignment UI any
 * more: every source drives its own default channel, and a channel is
 * voltage-driven exactly when a driver is present on it — picked from the
 * library or typed by hand — and unit-driven when it is cleared.
 */
function CadDriveChannels() {
  const state = useCadReturnStore();
  const sources = state.selectedBundle?.sources ?? [];
  const activeSources = sources.filter((source) => !state.skippedSourceIds.includes(source.id));
  // The coupled campaign writes its derived output to a reserved channel id.
  // Withholding it from the assignable list is what turns a server refusal
  // into a collision that cannot be made in the first place.
  const channelIds = assignableChannelIds(
    [...new Set(sources.map((source) => source.defaultDriveChannelId))],
    state.passiveCardioid.enabled && state.passiveCardioid.coupled,
  );
  // The one place a reassignment select survives: a channel stranded on an id
  // that is no longer assignable — the id the coupled campaign reserves. The
  // refusal that raises tells the user to reassign that source, and hiding the
  // only control that can is how a blocker becomes a dead end.
  const showsAssignment = (channel: { id: string }) => !channelIds.includes(channel.id);
  return <>
    <div className="cad-channel-list">
      {state.driveChannels.map((channel) => {
        const driverForm = state.channelDrivers[channel.id];
        const driverEligible = channelAcceptsDriver(channel);
        return <div className="cad-channel" data-channel-id={channel.id} key={channel.id}>
          <div className="cad-channel-summary" data-control-reveal-id={CAD_CONTROLS.channelMotion.reveal.id}><span>{channelHeadingText(channel)}</span><select aria-label={`${CAD_CONTROLS.channelMotion.label} for ${channel.id}`} value={channel.motion} disabled={!!channel.exterior_transducer} onChange={(event) => state.setChannelMotion(channel.id, event.target.value as 'normal' | 'axial')}><option value="normal">Normal</option><option value="axial">Axial (pistonic)</option></select></div>
          <div className="cad-channel-hint">Normal moves each source face along its own normal. Axial moves the source as a rigid piston along its axis.</div>
          {showsAssignment(channel) && channel.source_ids
            .filter((sourceId) => activeSources.some((source) => source.id === sourceId))
            .map((sourceId) => <div className="cad-channel-row" key={sourceId}><b>{sourceId}</b><select aria-label={`Drive channel for ${sourceId}`} disabled={!!channel.exterior_transducer} value={channel.id} onChange={(event) => state.setSourceChannel(sourceId, event.target.value)}>{channelIds.map((id) => <option value={id} key={id}>{id}</option>)}</select></div>)}
          <label className="cad-driver-hint"><input type="checkbox" aria-label={`Solve driver in BEAT for ${channel.id}`} checked={!!channel.exterior_transducer}
            onChange={(event) => state.setExteriorTransducer(channel.id, event.target.checked ? { version: 1, motion_axis: [0, 0, 1] } : undefined)}/>
            Solve driver in BEAT</label>
          {channel.exterior_transducer && <>
            <p className="cad-driver-hint">Complete outward solids only. Enter bare Mmd, excluding air load; datasheet Mms is not Mmd. Other drivers are shorted for each excitation.</p>
            <ExteriorDriverLibraryPicker channel={channel}
              roleHint={activeSources.find(source => source.id === channel.source_ids[0])?.role}/>
            <ExteriorDriverFields channelId={channel.id} form={channel.exterior_transducer}
              onChange={form => state.setExteriorTransducer(channel.id, form)}/>
            <ExteriorDriverOptions channelId={channel.id} form={channel.exterior_transducer}
              onChange={form => state.setExteriorTransducer(channel.id, form)}/>
            <p className="cad-driver-hint">Motion axis in the returned mesh frame:</p>
            <div className="cad-driver-grid">{['x', 'y', 'z'].map((axis, index) => <label key={axis}><span>{axis}</span>
              <input type="number" aria-label={`Motion axis ${axis} for ${channel.id}`} value={channel.exterior_transducer!.motion_axis[index]}
                onChange={event => { const motion_axis = [...channel.exterior_transducer!.motion_axis] as [number, number, number]; motion_axis[index] = Number(event.target.value); state.setExteriorTransducer(channel.id, { ...channel.exterior_transducer!, motion_axis }); }}/>
            </label>)}</div>
          </>}
          {driverEligible && <ChannelDriverPicker
            channel={channel}
            form={driverForm}
            roleHint={activeSources.find((source) => source.id === channel.source_ids[0])?.role}
          />}
        </div>;
      })}
    </div>
    {state.driveChannels.some((channel) => channel.exterior_transducer || (channelAcceptsDriver(channel) && channelDriverPresent(state.channelDrivers[channel.id])))
      && <>
        <NumberField label={CAD_CONTROLS.driveVoltage.label} revealId={CAD_CONTROLS.driveVoltage.reveal.id} unit="V" value={state.driveVoltageV} min={0.01} step={0.1} precision={2} description="RMS voltage applied to every driver channel (2.83 V ≈ 1 W into 8 Ω)" onCommit={state.setDriveVoltage}/>
        {/* A ceiling, not a drive: it changes nothing about the response and
            only ever caps the maximum output the crossover section reports.
            Left empty, the drivers themselves are the only limit. */}
        <NumberField
          label={CAD_CONTROLS.maxDriveVoltage.label}
          revealId={CAD_CONTROLS.maxDriveVoltage.reveal.id}
          unit="V"
          value={state.maxDriveVoltageV ?? undefined}
          min={0.01}
          step={1}
          precision={1}
          optional
          onClear={() => state.setMaxDriveVoltage(null)}
          description="Most RMS volts the amplifier can swing. Caps the maximum output the Crossover section reports; leave empty when the drivers are the only limit."
          onCommit={state.setMaxDriveVoltage}
        />
      </>}
    {/* Said here, beside the drivers, and before the solve rather than after
        it: a channel with no driver is a good solve with consequences that are
        otherwise invisible until a chart has nothing to draw. */}
    {importedSubmissionNotices(state).map((notice) => (
      <p className="cad-driver-hint" role="status" key={notice}>{notice}</p>
    ))}
  </>;
}

/**
 * Passive-cardioid campaign inputs.
 *
 * Turning the section on is the same act as setting a rear volume: the wire's
 * opt-in boundary is `passive_cardioid_rear_volume_l`, and any other cardioid
 * field sent without it is a refusal naming the strays. So the toggle reveals
 * the whole set, the set is submitted together, and an incomplete set blocks
 * Solve rather than quietly reverting to the pre-campaign path.
 */
function CadPassiveCardioid() {
  const state = useCadReturnStore();
  const form = state.passiveCardioid;
  const blocker = passiveCardioidBlocker(state);
  const bemDriven = form.portAreaSource === 'bem_aperture';
  return <>
    <ToggleRow
      id="cad-passive-cardioid"
      label={CAD_CONTROLS.cardioidEnabled.label}
      revealId={CAD_CONTROLS.cardioidEnabled.reveal.id}
      help="Model a sealed rear chamber vented through a damped port, so the back radiation cancels behind the box. WG solves a separate radiation-impedance matrix over the PORT_EXIT aperture before the chamber and port model can run."
      checked={form.enabled}
      onChange={(enabled) => state.setPassiveCardioid({ enabled })}
    />
    {form.enabled && <>
      <p className="section-note">This adds a radiation-impedance campaign over the port aperture ahead of the main solve — roughly 20 seconds at the reference 160-point sweep. The run reports it as its own <b>radiation_impedance</b> stage.</p>
      <div className="cad-driver-grid">
        {CAD_CARDIOID_FIELD_CONTROLS.map(({ formKey, label, unit, step, displayScale, minimum, reveal, help }) => {
          const stored = form[formKey];
          const driven = bemDriven && formKey === 'modelPortAreaM2';
          return <label key={formKey} className="cad-driver-field" data-control-reveal-id={reveal.id} title={help}>
            <span>{label} ({unit}) *</span>
            <input
              type="number"
              min={minimum.value}
              step={step}
              disabled={driven}
              value={stored === null ? '' : cadDisplayValue(stored, displayScale)}
              aria-label={`${label} in ${unit}`}
              onChange={(event) => state.setPassiveCardioid({
                [formKey]: event.target.value === '' ? null : Number(event.target.value) / displayScale,
              })}
            />
          </label>;
        })}
      </div>
      <div className="select-row" data-control-reveal-id={CAD_CONTROLS.cardioidPortAreaSource.reveal.id}>
        <label htmlFor="cad-cardioid-port-area-source">{CAD_CONTROLS.cardioidPortAreaSource.label}</label>
        <select
          id="cad-cardioid-port-area-source"
          value={form.portAreaSource}
          onChange={(event) => state.setPassiveCardioid({ portAreaSource: event.target.value as PortAreaSource })}
        >
          <option value="user">User-stated physical area</option>
          <option value="bem_aperture">Same as the BEM aperture</option>
        </select>
      </div>
      <p className="section-note">{bemDriven
        ? 'Physical port area follows the BEM aperture area exactly; the solve refuses any drift between them, so it is not separately editable here.'
        : 'The physical area drives the chamber and port physics; the BEM area only identifies the meshed aperture. They are two different numbers unless the port exit is the whole port.'}</p>
      <ToggleRow
        id="cad-cardioid-invert"
        label={CAD_CONTROLS.cardioidInvertPort.label}
        revealId={CAD_CONTROLS.cardioidInvertPort.reveal.id}
        help="Drive the port out of phase with the diaphragm (rear drive sign −1). This is the cardioid arrangement and is on by default; turning it off makes the port an in-phase vent."
        checked={form.invertPort}
        onChange={(invertPort) => state.setPassiveCardioid({ invertPort })}
      />
      <ToggleRow
        id="cad-cardioid-coupled"
        label={CAD_CONTROLS.cardioidCoupled.label}
        revealId={CAD_CONTROLS.cardioidCoupled.reveal.id}
        help={`Solve the driver, chamber and port as one system and append the result as a derived "${PASSIVE_CARDIOID_CHANNEL_ID}" channel with its own electrical input impedance. Requires the MF diaphragm on one driver-carrying channel and every PORT_EXIT patch on one other channel.`}
        checked={form.coupled}
        onChange={(coupled) => state.setPassiveCardioid({ coupled })}
      />
      {blocker && <div className="field-error" role="alert">{blocker}</div>}
    </>}
  </>;
}

function CadMeshDetail() {
  const state = useCadReturnStore();
  const symmetryMode = useCadPreparationStore((current) => current.symmetryMode);
  const setSymmetryMode = useCadPreparationStore((current) => current.setSymmetryMode);
  const cadWorkspaceView = useSyncExternalStore(
    cadWorkspace.subscribe,
    cadWorkspace.getSnapshot,
    cadWorkspace.getSnapshot,
  );
  const error = useCadFeedbackError(cadWorkspaceView.error);
  const driftSources = new Set([...state.areaDriftSourceIds, ...(state.ingestRecord?.role_findings ?? [])
    .filter((finding) => String(finding.kind).includes('area-drift'))
    .map((finding) => String(finding.source_id))]);
  return <>
    <div className="cad-mesh-intro"><p>Smaller values are finer. Curved CAD faces receive bounded extra refinement automatically.</p><button className="primary" disabled={cadWorkspaceView.ingesting || !state.selectedBundle?.readable} onClick={() => void cadWorkspaceView.ingest()}>{cadWorkspaceView.ingesting ? 'Preparing…' : 'Rebuild mesh'}</button></div>
    <ToggleRow id="cad-force-full-domain" label="Force full domain" help="Disable automatic x=0/y=0 cutting for this preparation. Use this when a symmetry verdict is doubtful; it costs more memory and solve time but cannot remove a geometric half. A return the CAD author already cut is refused instead: the other half is not in the file." checked={symmetryMode === 'full'} onChange={(enabled) => {
      setSymmetryMode(enabled ? 'full' : 'auto');
      state.markIngestStale('The CAD symmetry preparation mode changed.');
    }}/>
    <NumberField label={CAD_CONTROLS.rigidSize.label} revealId={CAD_CONTROLS.rigidSize.reveal.id} unit="mm" value={state.rigidSizeMm} min={0.01} step={0.5} precision={2} description="Maximum target size for rigid CAD surfaces; tight curvature may be refined further." onCommit={state.setRigidSize}/>
    <NumberField label={CAD_CONTROLS.transitionSize.label} revealId={CAD_CONTROLS.transitionSize.reveal.id} unit="mm" value={state.transitionMm} min={0.01} step={0.5} precision={2} description="Distance over which the mesh grades from source size to wall size." onCommit={state.setTransition}/>
    {((state.ingestRecord?.evidence?.fem_air_volumes?.length ?? 0) > 0 || (state.ingestRecord?.evidence?.fem_regions?.length ?? 0) > 0) && <ToggleRow id="cad-exterior-only" label={CAD_CONTROLS.exteriorOnly.label} revealId={CAD_CONTROLS.exteriorOnly.reveal.id} help="Explicitly exclude the returned FEM air volumes and FEM regions. This solves only the exterior free-space problem." checked={state.exteriorOnly} onChange={state.setExteriorOnly}/>}
    {(state.selectedBundle?.sources ?? []).map((source) => <div className={`cad-source ${state.skippedSourceIds.includes(source.id) ? 'skipped' : ''}`} key={source.id}>
      <NumberField label={`${source.role} source`} revealId={CAD_CONTROLS.sourceSize.reveal.id} unit="mm" value={state.sourceSizesMm[source.id] ?? source.suggestedResolutionMm} min={0.01} step={0.25} precision={2} description={`${source.id} · suggested ${source.suggestedResolutionMm} mm`} disabled={state.skippedSourceIds.includes(source.id)} onCommit={(value) => state.setSourceSize(source.id, value)}/>
      {!source.required && <ToggleRow id={`skip-${source.id}`} label={CAD_CONTROLS.skipSource.label} revealId={CAD_CONTROLS.skipSource.reveal.id} help="Exclude this optional source from ingestion and the solve. This creates a blocking finding." checked={state.skippedSourceIds.includes(source.id)} onChange={(checked) => state.setSkipped(source.id, checked)}/>}
      {driftSources.has(source.id) && <ToggleRow id={`drift-${source.id}`} label={CAD_CONTROLS.areaDrift.label} revealId={CAD_CONTROLS.areaDrift.reveal.id} help="Explicitly accept the source-area mismatch and re-ingest. The override is recorded as a finding on the prepared model." checked={state.areaDriftOverrides.includes(source.id)} onChange={(checked) => state.setAreaDriftOverride(source.id, checked)}/>}
    </div>)}
    {state.ingestStaleReason && <div className="field-error" role="status">{state.ingestStaleReason} Rebuild the mesh before solving.</div>}
    {error && <div className="field-error" role="alert">{error}</div>}
    {cadWorkspaceView.status && <p className="section-note" role="status">{cadWorkspaceView.status}</p>}
  </>;
}

function CadSimulationEmpty() {
  const cadApplication = cadApplicationName(usePreferences().cadApplication);
  return <div className="cad-mode-empty" role="status"><b>Prepare CAD geometry to unlock these inputs</b><span>Drivers, crossover, sweep, directivity, solve options, and mesh detail appear here after CAD Link brings the finished geometry back from {cadApplication}.</span><button className="primary" onClick={() => workspaceNavigation.navigate('geometry')}>Open CAD Link setup</button></div>;
}

const osseChoices = [
  { key: 'throat', label: 'Throat', options: [['none', 'None'], ['authored', 'Adapter (authored)'], ['controls', 'Adapter (controls)']] },
  { key: 'mouth', label: 'Mouth end', options: [['plain', 'Plain'], ['roundover', 'Roundover'], ['arc', 'Terminating arc']] },
  { key: 'axial', label: 'Axial scale', options: [['off', 'Off'], ['on', 'On']] },
] as const;

function OsseFeatureChoices({ design, onChange }: { design: DesignDocument; onChange: (family: DesignFamily) => void }) {
  const features = osseFeatures(design.formula)!;
  return <fieldset className="osse-features" data-control-reveal-id={PARAMETRIC_CONTROLS.osseFeatures.reveal.id}>
    <legend>OSSE features</legend>
    <p className="section-note">Choose one feature, or an authored adapter with axial scale. Native features use circular OSSE bodies; switching is undoable.</p>
    {osseChoices.map(({ key, label, options }) => <SelectRow key={key}>
      <label htmlFor={`osse-feature-${key}`}>{label}</label>
      <select id={`osse-feature-${key}`} value={features[key]} onChange={(event) => {
        const formula = osseFormula({ ...features, [key]: event.target.value } as OsseFeatures);
        if (formula && formula !== design.formula) onChange(formula);
      }}>
        {options.map(([value, name]) => {
          const disabled = !osseFormula({ ...features, [key]: value } as OsseFeatures);
          return <option key={value} value={value} disabled={disabled} title={disabled ? OSSE_FEATURE_CONFLICT : undefined}>
            {name}{disabled ? ` — ${OSSE_FEATURE_CONFLICT}` : ''}
          </option>;
        })}
      </select>
    </SelectRow>)}
    {design.formula !== 'OSSE' && <p className="section-note">{OSSE_FEATURE_CONFLICT}; authored adapter + axial scale is supported. Clear the active choice before selecting another feature.</p>}
  </fieldset>;
}

export function ParamPanel({ tab, embedded = false }: { tab: ParameterTab; embedded?: boolean }) {
  const design = useDesignStore((state) => state.design);
  const designRevision = useDesignStore((state) => state.designRevision);
  const updateValues = useDesignStore((state) => state.updateValues);
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
  // The campaign needs a port aperture in the geometry. Offering it to a model
  // that has none can only produce a server refusal, so the section is not
  // shown at all -- and the submission drops the form to match.
  const cardioidSurface = useCadReturnStore(
    (state) => hasPassiveCardioidSurface(state.selectedBundle?.sources ?? []),
  );
  const waveguideLinked = useWaveguideDefinitionApplies();
  const setFamily = useDesignStore((state) => state.setFamily);
  const replaceDocument = useDesignStore((state) => state.replaceDocument);
  const helpVisible = useParameterHelp();
  const [query, setQuery] = useState('');
  const [freeformChoice, setFreeformChoice] = useState(false);
  const [conversionError, setConversionError] = useState<string | null>(null);
  const [conversionNotice, setConversionNotice] = useState<string | null>(null);
  const [converting, setConverting] = useState(false);
  const conversionGeneration = useRef(0);
  const searching = Boolean(query.trim());
  const definitions = useMemo(() => (workspaceMode === 'cad' ? CAD_PARAMETER_SECTION_DEFINITIONS : PARAMETER_SECTION_DEFINITIONS)
    .filter((definition) => definition.tab === tab)
    .filter((definition) => parameterSectionIsVisible(definition, { mode: workspaceMode, design, waveguideLinked })),
    [design, tab, waveguideLinked, workspaceMode]);
  const fieldsBySection = useMemo(() => new Map(definitions.map(({ title }) => {
    const fields = (workspaceMode === 'cad' ? LEGACY_PARAMETER_REGISTRY : PARAMETER_REGISTRY).filter((field) => field.section === title)
      .filter((field) => field.section !== 'Imported ATH values' || fieldIsVisible(field, design))
      .filter((field) => !field.id.startsWith('mesh.ath_') || fieldIsVisible(field, design))
      .filter((field) => query.trim() ? fieldAppliesToFamily(field, design.formula) : fieldIsVisible(field, design))
      .filter((field) => fieldMatchesQuery(field, query));
    return [title, fields] as const;
  })), [definitions, design, query, workspaceMode]);
  const matchingCadSections = useMemo(() => new Set(CAD_CONTROL_DESCRIPTORS
    .filter((descriptor) => workspaceMode === 'cad' && descriptor.tab === tab)
    .filter((descriptor) => cadControlIsAvailable(descriptor, Boolean(ingestRecord)))
    .filter((descriptor) => cadControlMatchesQuery(descriptor, query))
    .map((descriptor) => descriptor.section)), [ingestRecord, query, tab, workspaceMode]);
  const cadSectionMatches = (section: CadControlSection) => matchingCadSections.has(section);
  const matchingParametricSections = useMemo(() => new Set(PARAMETRIC_CONTROL_DESCRIPTORS
    .filter((descriptor) => workspaceMode === 'parametric' && descriptor.tab === tab)
    .filter((descriptor) => parametricControlIsAvailable(descriptor, design.formula))
    .filter((descriptor) => parametricControlMatchesQuery(descriptor, query))
    .map((descriptor) => descriptor.section)), [design.formula, query, tab, workspaceMode]);

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
    {freeformAngleOverride(field, design) && <p className="section-note warning">{freeformAngleOverride(field, design)}</p>}
    {field.kind !== 'number' && previewErrorForParameter(field, currentPreviewFields) && <div className="field-error" role="alert">{previewErrorForParameter(field, currentPreviewFields)}</div>}
  </div>;

  const importedAthValues = design.text_import_version === 'ath-2026-09a-v2' ? <>
        {design.ath_nac_present && <div className="passthrough-row"><span>NAC</span><b>{design.ath_nac ?? 'Present'} · preserves angular samples</b></div>}
  </> : null;

  const renderRegistrySection = (definition: ParameterSectionDefinition) => {
    const fields = fieldsBySection.get(definition.title) ?? [];
    const showSolveDomain = definition.title === PARAMETRIC_CONTROLS.solveDomain.section
      && (!searching || matchingParametricSections.has(definition.title));
    const showOuterBodyMode = ['Wall & Enclosure', 'Mounting'].includes(definition.title) && !isNativeFamily(design.formula)
      && (workspaceMode === 'cad' || !searching || matchingParametricSections.has('Mounting') || fields.some((field) => field.id === 'simulation.sim_type'));
    const hasCustomMode = (showOuterBodyMode && (workspaceMode !== 'cad' || !searching)) || showSolveDomain;
    const showCustom = workspaceMode === 'parametric' && (!searching || matchingParametricSections.has(definition.title));
    const custom = showCustom ? definition.title === 'Directivity map' ? <DirectivityMapControls />
      : definition.title === 'Solver' ? <SolveOptionsControls includeSampling={false} showOpenclGuidance />
      : definition.title === 'Frequency sweep' ? <><FrequencySweepControls idPrefix="design-solve" context="design" /><AdaptiveFrequencyControls /></>
      : null : null;
    const slots = definition.fieldOrder ?? fields.map((field) => field.id);
    if (fields.length === 0 && !hasCustomMode && !custom && !(definition.title === 'Imported ATH values' && design.text_import_version === 'ath-2026-09a-v2')) return null;
    return <Section
      key={definition.title}
      title={definition.title}
      description={definition.descriptionWhen?.(design) ?? definition.description}
      forceOpen={searching || (definition.title === 'Imported ATH values' && design.text_import_version === 'ath-2026-09a-v2')}
      revealId={workspaceMode === 'parametric' ? `parametric.section.${definition.title}` : undefined}
    >
      {showOuterBodyMode && <WallEnclosureModeControl design={design} mounting={workspaceMode === 'parametric'} serverError={workspaceMode === 'parametric' ? previewErrorForParameter(PARAMETER_REGISTRY.find((field) => field.id === 'simulation.sim_type')!, currentPreviewFields) : undefined} />}
      {/* Domain is an execution option, not a saved design parameter. Keeping
          it independent of mesh.quadrants prevents the round-trip-only ATH
          field from masquerading as a second solve/export control, while its
          section remains hidden wholesale in CAD mode. */}
      {slots.map((id) => {
        if (id === 'control.solveDomain') return showSolveDomain ? <SolveDomainControl key={id} design={design} /> : null;
        if (id === 'control.meshValidation') return showCustom ? <MeshValidationControl key={id} /> : null;
        const field = fields.find((candidate) => candidate.id === id);
        return field && !parameterGroup(field, design) && (workspaceMode === 'cad' || id !== 'simulation.sim_type') ? <Fragment key={id}>{renderField(field)}</Fragment> : null;
      })}
      {definition.groups?.filter((group) => !group.visibleWhen || group.visibleWhen(design)).map((group) => {
        const groupFields = fields.filter((field) => parameterGroup(field, design) === group.title);
        const groupSlots = group.fieldOrder ?? groupFields.map((field) => field.id);
        const hasCustomSlot = (showCustom || (workspaceMode === 'cad' && !searching)) && groupSlots.some((id) => id.startsWith('control.freeform'));
        if (groupFields.length === 0 && !hasCustomSlot) return null;
        // Search and errors reveal grouped inputs through the same mechanism
        // used for the optional ICW controls.
        const hasError = groupFields.some((field) => Boolean(previewErrorForParameter(field, currentPreviewFields)
          || validationMessage(field, design) || storedValueError(field, design)));
        return <Section key={group.title} title={group.title} summary={group.summary?.(design)} description={group.description} forceOpen={searching || hasError}>
          {groupSlots.map((id) => {
            if (id === 'control.freeformImport') return hasCustomSlot ? <FreeformPointImport key={id} /> : null;
            if (id === 'control.freeformInspection') return hasCustomSlot ? <FreeformProfileInspection key={id} /> : null;
            const field = groupFields.find((candidate) => candidate.id === id);
            return field ? renderField(field) : null;
          })}
          {group.title === 'Throat and length' && <p className="position-note">{FREEFORM_POSITION_NOTE}</p>}
          {group.title === 'Advanced ICW' && (design.icw_seed != null || design.icw_coeffs != null) && <div className="parameter-entry">
            <button type="button" className="secondary" data-action="clear-imported-icw"
              title="Remove the imported seed and direct coefficients, so the target controls apply again."
              onClick={() => updateValues({ icw_seed: null, icw_coeffs: null, icw_S: null })}>Clear imported seed / coefficients</button>
          </div>}
        </Section>;
      })}
      {custom}
      {definition.title === 'Imported ATH values' && importedAthValues}
      {searching && fields.some((field) => !fieldIsVisible(field, design)) && <p className="filter-note">Some matches are normally hidden by the active mode; they are shown here for discoverability.</p>}
    </Section>;
  };

  const sectionResetNotice = (family: DesignFamily) => design.cross_section
    && (design.cross_section.exponent !== 2 || design.cross_section.aspect_ratio !== 1)
    && (family === 'FREEFORM' || isNativeFamily(family))
    ? `${family} uses its own section shape. The base cross-section exponent and aspect ratio were reset to 2 and 1.`
    : null;

  const modelTypeSection = <Section
    title="Model Type"
    description="Select the horn family that defines the profile equation and its primary dimensions."
    summary={osseFeatures(design.formula) ? 'OSSE' : design.formula}
    forceOpen={searching}
  >
    <SelectRow className="family-row">
      <label htmlFor="family">Family</label>
      <select id="family" value={osseFeatures(design.formula) ? 'OSSE' : design.formula} onChange={(event) => {
        const family = event.target.value as DesignFamily;
        if (family === 'OSSE' && osseFeatures(design.formula)) return;
        setConversionNotice(null);
        if (family === 'FREEFORM' && design.formula !== 'FREEFORM') setFreeformChoice(true);
        else { setFreeformChoice(false); setFamily(family); setConversionNotice(family === 'LOOKUP' ? 'LOOKUP starts with a conical table defining the whole meridian (12.7 mm throat to 140 mm mouth over 120 mm, scaled by the current Scale). Edit or paste [z, r] samples in Profile. Analytic coefficients, throat extensions, slots, adapters and imported ATH geometry controls were removed.' : sectionResetNotice(family)); }
      }}>
        <option>OSSE</option><option>R-OSSE</option><option>ICW</option><option>FREEFORM</option>
        {(design.formula === 'LOOKUP' || design.formula === 'SOURCE-DISK') && <option value={design.formula}>
          {design.formula === 'LOOKUP' ? 'LOOKUP (EXPERIMENTAL)' : design.formula}
        </option>}
      </select>
    </SelectRow>
    {osseFeatures(design.formula) && <OsseFeatureChoices design={design} onChange={(family) => {
      setFamily(family, { preserveOsseBody: true });
      setConversionNotice(OSSE_SWITCH_NOTICE);
    }} />}
    {design.formula === 'LOOKUP' && <ExperimentalBadge />}
    {conversionNotice && <div className="field-warning conversion-notice" role="status">
      {conversionNotice} <button onClick={() => setConversionNotice(null)}>Dismiss</button>
    </div>}
    {freeformChoice && <div className="family-switch-choice" role="group" aria-label="Switch to FREEFORM">
      <b>Switch to FREEFORM</b><span>Choose how to initialize the editable profiles.</span>
      <div><button onClick={() => { setFamily('FREEFORM'); setConversionNotice(sectionResetNotice('FREEFORM')); setFreeformChoice(false); }}>Start blank</button><button disabled={converting} onClick={() => {
        const conversion = ++conversionGeneration.current;
        const sourceRevision = useDesignStore.getState().designRevision;
        setConverting(true); setConversionError(null);
        void convertDesignToFreeform(design).then(({ design: converted, notice }) => {
          if (conversion !== conversionGeneration.current) return;
          if (useDesignStore.getState().designRevision !== sourceRevision) {
            setConversionError('The design changed while it was being converted. Review the edits and try again.');
            return;
          }
          replaceDocument(converted, { keepHistory: true });
          // The choice closes on success, so the notice lives outside it.
          setConversionNotice([notice, sectionResetNotice('FREEFORM')].filter(Boolean).join(' ') || null);
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

  if (workspaceMode === 'cad' && workspaceModeStore.getSnapshot().importedInput === 'file') return <MeshImportPanel tab={tab} embedded={embedded}/>;

  return (
    <SolveOptionsStoreProvider mode={workspaceMode}>
    <div ref={registerParameterPanel} className={`param-panel${embedded ? '' : ' panel-scroll'}`} data-param-tab={tab}>
      {workspaceMode === 'parametric' && tab === 'geometry' && <RunNameField/>}
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
      {workspaceMode === 'parametric' && tab === 'geometry' && (!searching || (osseFeatures(design.formula) && matchingParametricSections.has('Model Type'))) && modelTypeSection}
      {workspaceMode === 'parametric' ? definitions.map(renderRegistrySection)
        : <>
          {definitions.map(renderRegistrySection)}
          {tab === 'geometry' && cadSectionMatches(CAD_CONTROLS.realizedDimensions.section) && <RealizedDimensionsCard forceOpen={searching}/>}
          {!searching && tab === 'simulation' && !ingestRecord && <CadSimulationEmpty/>}
          {tab === 'simulation' && ingestRecord && <>
            {cadSectionMatches(CAD_CONTROLS.frequencySweep.section) && <Section title={CAD_CONTROLS.frequencySweep.section} description="The explicit range submitted with this imported CAD geometry." forceOpen={searching} revealId={CAD_CONTROLS.frequencySweep.reveal.id}><CadFrequencySweep/></Section>}
            {cadSectionMatches(CAD_CONTROLS.directivityMap.section) && <Section title={CAD_CONTROLS.directivityMap.section} description="Display-plane and angular sampling controls, including the effective imported-CAD grid." forceOpen={searching} revealId={CAD_CONTROLS.directivityMap.reveal.id}><DirectivityMapControls effectiveDerivation={ingestRecord.polar_grid_derivation}/></Section>}
            {cadSectionMatches(CAD_CONTROLS.driveChannels.section) && <Section title={CAD_CONTROLS.driveChannels.section} description="Per-channel driver setup: which sources each channel drives, its motion, voltage drive, and Thiele-Small data. Assign two sources to the same channel to drive them together." forceOpen={searching} revealId={CAD_CONTROLS.driveChannels.reveal.id}><CadDriveChannels/></Section>}
            {cadSectionMatches(CAD_CONTROLS.crossover.section) && <Section title={CAD_CONTROLS.crossover.section} description="Optional combined output of adjacent drive channels: a filter family and slope per pair, with automatic or manual level, delay and polarity per channel." forceOpen={searching} revealId={CAD_CONTROLS.crossover.reveal.id}><CadCrossover/></Section>}
            {cardioidSurface && cadSectionMatches(CAD_CONTROLS.passiveCardioid.section) && <Section title={CAD_CONTROLS.passiveCardioid.section} description="Sealed rear chamber vented through a damped port, and the extra radiation-impedance campaign it needs." forceOpen={searching} revealId={CAD_CONTROLS.passiveCardioid.reveal.id}><CadPassiveCardioid/></Section>}
            {cadSectionMatches(CAD_CONTROLS.solveOptions.section) && <Section title={CAD_CONTROLS.solveOptions.section} description="Imported-CAD validation, frequency selection, and diagnostic controls. Geometry fixes the backend and domain." forceOpen={searching} revealId={CAD_CONTROLS.solveOptions.reveal.id}><SolveOptionsControls mode="cad" ingestRecord={ingestRecord}/></Section>}
            {cadSectionMatches(CAD_CONTROLS.meshDetail.section) && <Section title={CAD_CONTROLS.meshDetail.section} description="Imported-CAD surface sizing, optional-source policy, domain choice, and mesh regeneration." forceOpen={searching} revealId={CAD_CONTROLS.meshDetail.reveal.id}><CadMeshDetail/></Section>}
          </>}
        </>}
      {searching && [...fieldsBySection.values()].every((fields) => fields.length === 0) && matchingCadSections.size === 0 && matchingParametricSections.size === 0 && <div className="parameter-empty">No parameter labels or keys match “{query}”.</div>}
    </div>
    </SolveOptionsStoreProvider>
  );
}
