import { useRef, useState, type FormEvent } from 'react';
import { recombineJobResults, type JobResults } from '../api/results';
import { CrossoverAdvanced } from '../design/CrossoverAdvanced';
import {
  familyOrders,
  FILTER_FAMILIES,
  FILTER_FAMILY_LABELS,
  fromResult,
  isSimple,
  pairsOf,
  resolvedChannels,
  sameSpec,
  sharedDelayMode,
  sharedGainMode,
  slopeLabel,
  toWire,
  unlinkedPairNote,
  withDelayMode,
  withGainMode,
  withPair,
  type CrossoverSpec,
  type FilterFamily,
} from '../results/crossoverSpec';
import type { CombineMetadata } from '../results/types';
import { useCadReturnStore } from '../stores/cadReturn';

/**
 * The crossover editor over a finished run.
 *
 * Everything here recombines from the job's stored complex bases server-side,
 * so a change repaints without a re-solve. The applied spec is also written
 * back to the CAD rail, so the dock and the pre-solve fields are one setting
 * rather than two that disagree.
 *
 * The strip carries the same controls as the rail plus what only a result can
 * say: the delays alignment actually chose, and how deep the reverse null goes
 * — the one number that says whether the two members are really summing.
 */

/** A null shallower than this is not a null; the pair is not summing the way
 * its filter says it should, and the chip says so in amber. */
const SHALLOW_NULL_DB = -10;
/** Above this the phase fit is a guess, which the server also warns about. */
const POOR_FIT_DEG = 30;

function decimals(value: number | undefined, places: number): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(places) : '—';
}

/** Minus signs are typographic here: these are readings, not expressions. */
function signed(value: number, places: number): string {
  return `${value < 0 ? '−' : ''}${Math.abs(value).toFixed(places)}`;
}

export function memberLabelOf(combine: CombineMetadata, member: string): string {
  const index = combine.members.indexOf(member);
  return combine.member_roles?.[index] ?? member;
}

/** `LF 2.62 · MF 0.38 · HF ref ms` — the whole alignment in one chip. */
export function delayChipText(combine: CombineMetadata): string {
  const reference = combine.reference;
  const stated = combine.members.map((member) => {
    if (member === reference) return `${memberLabelOf(combine, member)} ref`;
    const ms = combine.channels?.[member]?.delay_ms ?? combine.delays_ms?.[member];
    return `${memberLabelOf(combine, member)} ${decimals(ms, 2)}`;
  });
  return `${stated.join(' · ')} ms`;
}

export interface NullVerdict {
  text: string;
  warn: boolean;
  title: string;
}

/** The worst pair's reverse-null depth, and whether it should read as caution.
 * Null when the payload predates the pair metrics. */
export function nullVerdict(combine: CombineMetadata): NullVerdict | null {
  const pairs = Object.entries(combine.pairs ?? {});
  const depths = pairs.flatMap(([name, pair]) => (
    typeof pair.reverse_null_db === 'number' && Number.isFinite(pair.reverse_null_db)
      ? [{ name, depth: pair.reverse_null_db, fit: pair.fit_residual_deg }]
      : []
  ));
  if (!depths.length) return null;
  // "Worst" is the shallowest: a null at -6 dB is the one that is not working.
  const worst = depths.reduce((best, item) => (item.depth > best.depth ? item : best));
  const poorFit = depths.some((item) => typeof item.fit === 'number' && item.fit > POOR_FIT_DEG);
  const warn = worst.depth > SHALLOW_NULL_DB || poorFit || (combine.warnings?.length ?? 0) > 0;
  return {
    text: `null ${signed(worst.depth, 0)} dB`,
    warn,
    title: `Deepest cancellation with one member inverted, over pair ${worst.name}: `
      + `${signed(worst.depth, 1)} dB relative to the sum. A shallow null means the two members are `
      + `not summing the way this filter pair says they should.`
      + (poorFit ? ' At least one pair’s phase fit is uncertain.' : ''),
  };
}

export function CrossoverStrip({ jobId, channelId, combine, onApplied }: {
  jobId: string;
  channelId: string;
  combine: CombineMetadata;
  onApplied: (jobId: string, updated: JobResults) => void;
}) {
  const applied = fromResult(combine);
  const appliedKey = `${jobId}:${channelId}:${applied ? JSON.stringify(toWire(applied)) : ''}`;
  const [spec, setSpec] = useState<CrossoverSpec | null>(applied);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const advancedAnchor = useRef<HTMLButtonElement | null>(null);
  const lastApplied = useRef(appliedKey);
  if (lastApplied.current !== appliedKey) {
    lastApplied.current = appliedKey;
    setSpec(applied);
    setError(null);
  }
  if (!spec || !applied) return null;
  const resolved = resolvedChannels(combine);
  const dirty = !sameSpec(spec, applied);
  const delays = delayChipText(combine);
  const verdict = nullVerdict(combine);
  const memberLabel = (member: string) => memberLabelOf(combine, member);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const updated = await recombineJobResults(jobId, { id: channelId, ...toWire(spec) });
      onApplied(jobId, updated);
      useCadReturnStore.getState().setCombineSpecFromResult(spec);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return <form className="results-toolbar result-recombine" onSubmit={(event) => void submit(event)}>
    {pairsOf(spec).map((pair) => {
      // Bands below, because that is how a crossover is spoken. The authored
      // ids stay the fallback and remain the accessible name either way, so an
      // unroled return still says which channels each field joins.
      const name = `${memberLabel(pair.lower)} → ${memberLabel(pair.upper)}`;
      const section = pair.lowerLp ?? pair.upperHp;
      return <label key={pair.key} className="result-recombine-pair">
        <span>{name}</span>
        <input
          type="number"
          min={1}
          step={10}
          disabled={!pair.linked}
          value={section?.fcHz ?? ''}
          title={pair.linked ? undefined : unlinkedPairNote(pair)}
          aria-label={`Crossover ${pair.lower} to ${pair.upper} in hertz`}
          onChange={(event) => {
            const hz = Number(event.target.value);
            if (Number.isFinite(hz) && hz > 0) setSpec(withPair(spec, pair.key, { hz }));
          }}
        />
        <span>Hz</span>
        <select
          aria-label={`Crossover ${pair.lower} to ${pair.upper} family`}
          disabled={!pair.linked}
          value={section?.family ?? 'lr'}
          onChange={(event) => setSpec(withPair(spec, pair.key, { family: event.target.value as FilterFamily }))}
        >{FILTER_FAMILIES.map((family) => <option key={family} value={family}>{FILTER_FAMILY_LABELS[family]}</option>)}</select>
        <select
          aria-label={`Crossover ${pair.lower} to ${pair.upper} slope`}
          disabled={!pair.linked}
          value={section?.order ?? 4}
          onChange={(event) => setSpec(withPair(spec, pair.key, { order: Number(event.target.value) }))}
        >{familyOrders(section?.family ?? 'lr').map((order) => <option key={order} value={order}>{slopeLabel(order)}</option>)}</select>
        {!pair.linked && <span className="result-recombine-unlinked" role="status">{unlinkedPairNote(pair)}</span>}
      </label>;
    })}
    <span className="result-recombine-mode">Levels
      <span className="crossover-segment" role="group" aria-label="Levels mode">
        <button type="button" aria-pressed={sharedGainMode(spec) === 'auto'} className={sharedGainMode(spec) === 'auto' ? 'on' : ''} onClick={() => setSpec(withGainMode(spec, 'auto'))}>Auto</button>
        <button type="button" aria-pressed={sharedGainMode(spec) === 'manual'} className={sharedGainMode(spec) === 'manual' ? 'on' : ''} onClick={() => setSpec(withGainMode(spec, 'manual', Object.fromEntries(Object.entries(resolved).map(([id, channel]) => [id, channel.gainAutoDb]))))}>Manual</button>
      </span>
    </span>
    <span className="result-recombine-mode">Delay
      <span className="crossover-segment" role="group" aria-label="Delay mode">
        <button type="button" aria-pressed={sharedDelayMode(spec) === 'auto'} className={sharedDelayMode(spec) === 'auto' ? 'on' : ''} onClick={() => setSpec(withDelayMode(spec, 'auto'))}>Auto</button>
        <button type="button" aria-pressed={sharedDelayMode(spec) === 'manual'} className={sharedDelayMode(spec) === 'manual' ? 'on' : ''} onClick={() => setSpec(withDelayMode(spec, 'manual', Object.fromEntries(Object.entries(resolved).map(([id, channel]) => [id, channel.delayAutoMs]))))}>Manual</button>
      </span>
    </span>
    <button
      ref={advancedAnchor}
      type="button"
      className={advancedOpen ? 'on' : ''}
      aria-expanded={advancedOpen}
      aria-haspopup="dialog"
      title="Per-channel high-pass, low-pass, gain, delay and polarity"
      onClick={() => setAdvancedOpen((open) => !open)}
    >Advanced ▸</button>
    {!isSimple(spec) && <span className="result-recombine-chip">edited per channel</span>}
    <span className="result-recombine-chip" title="Delay applied to each member; the reference channel is pinned at 0 ms.">{delays}</span>
    {verdict && <span className={`result-recombine-chip${verdict.warn ? ' warn' : ''}`} title={verdict.title}>{verdict.text}</span>}
    <button type="submit" disabled={busy || !dirty} title="Recompute the combined channel from the stored solve — no re-solve needed">{busy ? 'Recombining…' : 'Apply'}</button>
    {error && <span className="result-recombine-error" role="alert">{error}</span>}
    {advancedOpen && <CrossoverAdvanced
      anchorRef={advancedAnchor}
      onClose={() => setAdvancedOpen(false)}
      spec={spec}
      resolved={resolved}
      memberLabel={memberLabel}
      onChange={setSpec}
    />}
  </form>;
}
