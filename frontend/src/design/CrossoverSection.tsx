import { Fragment, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { namespaceStorage } from '../stores/durableSettings';
import { CAD_CONTROLS } from './cadControlRegistry';
import { CrossoverAdvanced } from './CrossoverAdvanced';
import { NumberField } from './NumberField';
import { ToggleRow } from './SolveOptionsSections';
import { latestCombine } from '../results/latestCombine';
import { recombineJobResults } from '../api/results';
import {
  driverXoMinNote,
  familyOrders,
  FILTER_FAMILIES,
  FILTER_FAMILY_LABELS,
  gainText,
  isSimple,
  nominalImpedanceOhm,
  pairsOf,
  resolvedChannels,
  sharedDelayMode,
  sharedGainMode,
  slopeLabel,
  unlinkedPairNote,
  withDelayMode,
  withGainMode,
  withPair,
  type CrossoverSpec,
  type FilterFamily,
  type GainUnit,
  fromResult,
  sameSpec,
  toWire,
} from '../results/crossoverSpec';
import { driverValues } from '../stores/cadReturn';
import {
  combineChain,
  combineEnabledEffective,
  combineSpecEffective,
  useCadReturnStore,
  type CombinePair,
  type DriverPreset,
} from '../stores/cadReturn';

/** The bands a pair joins, in the words a designer uses for them. Unroled ends
 * fall back to the authored channel ids, which is all the return says. */
export function combinePairLabel(pair: CombinePair): string {
  return pair.lowerRole && pair.upperRole
    ? `${pair.lowerRole} → ${pair.upperRole}`
    : `${pair.lower} → ${pair.upper}`;
}

/** What an untouched field would hold and why, stated per pair rather than as
 * one note for the whole section: the pairs no longer share a rule. */
export function combinePairHint(pair: CombinePair): string {
  if (pair.defaultHz === undefined) return 'Default follows the current Frequency Sweep.';
  return pair.outsideSweep
    ? `${pair.defaultHz} Hz default is outside the sweep; using ${pair.hz} Hz.`
    : `${pair.defaultHz} Hz default.`;
}

/** Auto/Manual as one exclusive control. A mixed chain presses neither, so the
 * rail never claims one mode for a spec that holds two. */
function ModeRow({ label, help, revealId, mode, values, onSelect }: {
  label: string;
  help: string;
  revealId: string;
  mode: 'auto' | 'manual' | null;
  /** What the shown run resolved, one entry per member, already formatted.
   * Empty until a solved combined result is on screen. */
  values: Array<{ member: string; text: string }>;
  onSelect: (mode: 'auto' | 'manual') => void;
}) {
  return <>
    <div className="crossover-mode-row" data-control-reveal-id={revealId} title={help}>
      <span>{label}</span>
      <div className="crossover-segment" role="group" aria-label={`${label} mode`}>
        <button type="button" aria-pressed={mode === 'auto'} className={mode === 'auto' ? 'on' : ''} onClick={() => onSelect('auto')}>Auto</button>
        <button type="button" aria-pressed={mode === 'manual'} className={mode === 'manual' ? 'on' : ''} onClick={() => onSelect('manual')}>Manual</button>
      </div>
    </div>
    {/* Auto is not a black box once a run has been solved: the numbers it
        chose are right here, without having to open Advanced to read them. */}
    {values.length > 0 && <p className="section-note">
      {values.map(({ member, text }) => `${member} ${text}`).join(' \u00b7 ')}
    </p>}
  </>;
}

function PairRow({ pair, spec, preset, onChange }: {
  pair: CombinePair;
  spec: CrossoverSpec;
  preset: DriverPreset | null;
  onChange: (spec: CrossoverSpec) => void;
}) {
  const linked = pair.linked;
  const specPair = pairsOf(spec).find((item) => item.key === pair.key);
  // The upper channel's own high-pass corner, not the field's displayed
  // value: an unlinked pair's `pair.hz` can come from the lower channel's
  // low-pass instead, which would blame the wrong driver's minimum.
  const upperHpHz = specPair?.upperHp?.fcHz;
  const xoNote = preset?.xo_min_hz != null && upperHpHz !== undefined && upperHpHz < preset.xo_min_hz
    ? driverXoMinNote(preset.label, preset.xo_min_hz, upperHpHz)
    : null;
  return <Fragment>
    <NumberField
      label={combinePairLabel(pair)}
      revealId={CAD_CONTROLS.crossoverFrequency.reveal.id}
      unit="Hz"
      value={pair.hz}
      min={1}
      step={50}
      precision={0}
      disabled={!linked}
      disabledReason={linked ? undefined : 'This pair is not symmetric; edit each channel in Advanced.'}
      description={`${CAD_CONTROLS.crossoverFrequency.label} · ${pair.lower} → ${pair.upper}`}
      onCommit={(value) => onChange(withPair(spec, pair.key, { hz: value }))}
    />
    <div className={`crossover-shape-row${linked ? '' : ' field-disabled'}`} data-control-reveal-id={CAD_CONTROLS.crossoverFamily.reveal.id}>
      <select
        aria-label={`${combinePairLabel(pair)} filter family`}
        value={pair.family}
        disabled={!linked}
        onChange={(event) => onChange(withPair(spec, pair.key, { family: event.target.value as FilterFamily }))}
      >{FILTER_FAMILIES.map((family) => <option key={family} value={family}>{FILTER_FAMILY_LABELS[family]}</option>)}</select>
      <select
        aria-label={`${combinePairLabel(pair)} slope`}
        value={pair.order}
        disabled={!linked}
        onChange={(event) => onChange(withPair(spec, pair.key, { order: Number(event.target.value) }))}
      >{familyOrders(pair.family).map((order) => <option key={order} value={order}>{slopeLabel(order)}</option>)}</select>
    </div>
    {linked
      ? <p className="section-note">{combinePairHint(pair)}</p>
      : <p className="section-note warning" role="status">{specPair ? unlinkedPairNote(specPair) : 'This pair is not symmetric; edit it in Advanced.'}</p>}
    {xoNote && <p className="section-note warning" role="status">{xoNote}</p>}
  </Fragment>;
}

/** How long an edit may settle before it is applied to the shown run. */
const LIVE_RECOMBINE_DEBOUNCE_MS = 400;

/**
 * Which face of the section is shown, remembered across restarts. One string
 * per namespace -- 'basic' or 'advanced'; anything else reads as basic.
 * Settings hydrate before the app mounts, so the first paint is already right.
 */
const viewStorage = namespaceStorage('crossoverView');

function storedAdvancedView(): boolean {
  try { return viewStorage.getItem('crossoverView') === 'advanced'; } catch { return false; }
}

/** Which unit the Advanced gain fields are read in, remembered the same way
 * and in its own namespace: one namespace holds one value, and sharing the
 * view's would clobber it. */
const gainUnitStorage = namespaceStorage('crossoverGainUnit');

function storedGainUnit(): GainUnit {
  try {
    const stored = gainUnitStorage.getItem('crossoverGainUnit');
    return stored === 'v' || stored === 'w' ? stored : 'db';
  } catch { return 'db'; }
}

/** Whether the rail's spec and the shown run name the same members, in the
 * same order. A run combined from other channels is a different combine, and
 * no crossover edit here can be applied to it. */
function sameMembers(spec: CrossoverSpec, shown: ReturnType<typeof latestCombine.getSnapshot>): boolean {
  const members = shown?.combine.members ?? [];
  return spec.members.length === members.length
    && spec.members.every((member, index) => member === members[index]);
}

/**
 * Apply the rail's crossover to the shown run as it is edited.
 *
 * Recombining runs from stored bases in milliseconds, so the combined result
 * follows the settings live: whenever the effective spec differs from the one
 * the shown combined channel was computed with, the recombine is posted after
 * a short settle and the dock swaps the repainted result in through the
 * bridge's own callback. The dock then republishes, the specs compare equal,
 * and the loop rests. A run for different channels, a provisional live view,
 * or an incomplete run is never touched.
 */
function useLiveRecombine(
  spec: CrossoverSpec | null,
  enabled: boolean,
  shown: ReturnType<typeof latestCombine.getSnapshot>,
): { live: boolean; busy: boolean; error: string | null } {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    generation.current += 1;
    const request = generation.current;
    if (!enabled || !spec || !shown?.canApply) { setBusy(false); setError(null); return; }
    const applied = fromResult(shown.combine);
    if (!applied) return;
    if (!sameMembers(spec, shown)) return;
    if (sameSpec(spec, applied)) { setBusy(false); setError(null); return; }
    const timer = setTimeout(() => {
      void (async () => {
        setBusy(true); setError(null);
        try {
          const updated = await recombineJobResults(shown.jobId, { id: shown.channelId, ...toWire(spec) });
          if (generation.current === request) shown.onApplied(shown.jobId, updated);
        } catch (reason) {
          if (generation.current === request) {
            setError(reason instanceof Error ? reason.message : String(reason));
          }
        } finally {
          if (generation.current === request) setBusy(false);
        }
      })();
    }, LIVE_RECOMBINE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [spec, enabled, shown]);
  return { live: Boolean(shown?.canApply), busy, error };
}

/**
 * What the edit is doing to the run on screen, said in every case.
 *
 * A crossover change never needs a new solve -- the server recombines the
 * stored per-channel fields -- but that only holds for a run the rail owns.
 * When it does not, the reason is stated with the dock's own way out of it
 * rather than left as an edit that appears to have been swallowed.
 */
function LiveNote({ shown, live, busy, error, members }: {
  shown: NonNullable<ReturnType<typeof latestCombine.getSnapshot>>;
  live: boolean;
  busy: boolean;
  error: string | null;
  members: boolean;
}) {
  if (!live) {
    return <p className="section-note warning" role="status" aria-live="polite">
      {shown.blockedReason ?? 'Crossover changes cannot be applied to the shown run.'}
      {shown.recall && <> <button type="button" className="crossover-recall" onClick={shown.recall}>Load this run's model</button></>}
    </p>;
  }
  if (!members) {
    return <p className="section-note warning" role="status" aria-live="polite">
      The shown run combines {(shown.combine.members ?? []).join(' + ') || 'other channels'}, not the channels above, so these settings are not its crossover. Solve this return to combine it this way.
    </p>;
  }
  return <p className={error ? 'section-note warning' : 'section-note'} role="status" aria-live="polite">
    {error ?? (busy ? 'Updating the shown run…' : 'Changes apply to the shown combined result immediately — no new solve is needed.')}
  </p>;
}

export function CadCrossover() {
  const state = useCadReturnStore();
  const [advanced, setAdvanced] = useState(storedAdvancedView);
  const [gainUnit, setGainUnit] = useState<GainUnit>(storedGainUnit);
  const selectView = (next: boolean) => {
    setAdvanced(next);
    try { viewStorage.setItem('crossoverView', next ? 'advanced' : 'basic'); } catch { /* storage is optional */ }
  };
  const selectGainUnit = (next: GainUnit) => {
    setGainUnit(next);
    try { gainUnitStorage.setItem('crossoverGainUnit', next); } catch { /* storage is optional */ }
  };
  const shown = useSyncExternalStore(latestCombine.subscribe, latestCombine.getSnapshot, latestCombine.getSnapshot);
  const enabled = combineEnabledEffective(state);
  const spec = combineSpecEffective(state);
  const liveState = useLiveRecombine(spec, enabled && state.driveChannels.length >= 2, shown);
  if (state.driveChannels.length < 2) return <p className="section-note">Two or more drive channels are required for a combined output.</p>;
  const apply = (next: CrossoverSpec) => state.setCombineSpec(next);
  const resolved = resolvedChannels(shown?.combine);
  const maxOutput = shown?.combine.max_output ?? null;
  const { live, busy, error: liveError } = liveState;
  /** A member's short name for the Basic readouts: its band role when the
   * return gave it one, and its channel id when it did not. */
  const shortLabel = (member: string): string => {
    const pair = combineChain(state).find((item) => item.lower === member || item.upper === member);
    return (pair?.lower === member ? pair.lowerRole : pair?.upperRole) ?? member;
  };
  /** The nominal impedance a member's watts are stated into: what the driver
   * says, falling back to Re, divided across parallel drivers. */
  const impedanceFor = (member: string): number | null => {
    const form = state.channelDrivers[member];
    const values = driverValues(form);
    return nominalImpedanceOhm({
      z_nom_ohm: values.z_nom_ohm ?? form?.preset?.z_ohm ?? null,
      re_ohm: values.re_ohm ?? null,
      count: values.count ?? null,
    });
  };
  const autoReadout = (pick: (member: string) => string | null) => (spec?.members ?? [])
    .flatMap((member) => {
      const text = pick(member);
      return text === null ? [] : [{ member: shortLabel(member), text }];
    });
  return <>
    <ToggleRow id="cad-combine" label={CAD_CONTROLS.combinedOutput.label} revealId={CAD_CONTROLS.combinedOutput.reveal.id} help="Append a filtered, time-aligned crossover sum of the drive channels as one more result channel. On by default for a return with two or more drive channels; the chain runs lowest band first, ordered by the sources' return roles (LF → MF → HF)." checked={enabled} onChange={state.setCombineEnabled}/>
    {enabled && spec && <>
      <div className="crossover-view-row" data-control-reveal-id={CAD_CONTROLS.crossoverAdvanced.reveal.id}>
        <div className="crossover-segment" role="group" aria-label="Crossover view">
          <button
            type="button"
            aria-pressed={!advanced}
            className={advanced ? '' : 'on'}
            title="One symmetric crossover per pair, with chain-wide level match and time alignment"
            onClick={() => selectView(false)}
          >Basic</button>
          <button
            type="button"
            aria-pressed={advanced}
            className={advanced ? 'on' : ''}
            title="Per-channel high-pass, low-pass, gain, delay and polarity"
            onClick={() => selectView(true)}
          >Advanced</button>
        </div>
        {/* Basic shows the chain's shared story, so a spec it cannot express
            is flagged right where the Advanced view is one click away. */}
        {!advanced && !isSimple(spec) && <span className="crossover-advanced-flag">edited per channel</span>}
      </div>
      {!advanced && <>
        {combineChain(state).map((pair) => <PairRow
          key={pair.key}
          pair={pair}
          spec={spec}
          preset={state.channelDrivers[pair.upper]?.preset ?? null}
          onChange={apply}
        />)}
        <ModeRow
          label={CAD_CONTROLS.levelMatch.label}
          revealId={CAD_CONTROLS.levelMatch.reveal.id}
          help="Equalise member band levels before summing. Manual keeps the gain each channel is given in Advanced; auto defaults off when every member carries a driver model, because real voltage-driven levels should not be re-equalised."
          mode={sharedGainMode(spec)}
          values={autoReadout((member) => {
            const db = resolved[member]?.gainAutoDb;
            return db === null || db === undefined ? null : gainText(db, 'db');
          })}
          onSelect={(mode) => apply(withGainMode(spec, mode, Object.fromEntries(
            Object.entries(resolved).map(([id, channel]) => [id, channel.gainAutoDb]),
          )))}
        />
        <ModeRow
          label={CAD_CONTROLS.timeAlign.label}
          revealId={CAD_CONTROLS.timeAlign.reveal.id}
          help="Delay each member so the crossover sums the way its filter pair says it should, fitted from the phase of the solved fields across the pair's overlap. Manual keeps the delay each channel is given in Advanced."
          mode={sharedDelayMode(spec)}
          values={autoReadout((member) => {
            const ms = resolved[member]?.delayAutoMs;
            return ms === null || ms === undefined ? null : `${ms >= 0 ? '+' : ''}${ms.toFixed(2)} ms`;
          })}
          onSelect={(mode) => apply(withDelayMode(spec, mode, Object.fromEntries(
            Object.entries(resolved).map(([id, channel]) => [id, channel.delayAutoMs]),
          )))}
        />
      </>}
      {advanced && <CrossoverAdvanced
        spec={spec}
        resolved={resolved}
        memberLabel={(member) => {
          const pair = combineChain(state).find((item) => item.lower === member || item.upper === member);
          const role = pair?.lower === member ? pair.lowerRole : pair?.upperRole;
          return role ? `${role} · ${member}` : member;
        }}
        presetFor={(member) => state.channelDrivers[member]?.preset ?? null}
        gainUnit={gainUnit}
        onGainUnit={selectGainUnit}
        driveVoltageV={state.driveVoltageV}
        impedanceFor={impedanceFor}
        usageFor={(member) => maxOutput?.members?.[member] ?? null}
        onChange={apply}
      />}
      {shown && <LiveNote shown={shown} live={live} busy={busy} error={liveError} members={sameMembers(spec, shown)}/>}
    </>}
  </>;
}
