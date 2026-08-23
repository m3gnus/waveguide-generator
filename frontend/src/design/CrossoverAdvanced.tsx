import { useId, type RefObject } from 'react';
import { AnchoredPanel } from '../prefs/AnchoredPanel';
import {
  familyOrders,
  FILTER_FAMILIES,
  FILTER_FAMILY_LABELS,
  nearestOrder,
  relinkPairs,
  slopeLabel,
  withChannel,
  withReference,
  delayDistanceMm,
  type CrossoverSpec,
  type FilterFamily,
  type FilterSection,
  type ResolvedChannel,
} from '../results/crossoverSpec';

/**
 * The per-channel crossover editor, shared by the rail and the results strip.
 *
 * The rail's pair fields describe a chain where both sides of every crossover
 * agree. This is where they stop having to: each channel owns its high-pass,
 * low-pass, gain, delay and polarity, and a pair that ends up asymmetric is
 * reported as unlinked rather than silently averaged back together.
 *
 * Auto is a value you can see. Every automatic field shows the number the last
 * shown result actually chose, dashed to say it was not typed; switching to
 * Manual starts from that number, so taking over never begins by moving the
 * curve.
 */

const NO_RESOLVED: Record<string, ResolvedChannel> = {};

function decimals(value: number, places: number): string {
  return Number.isFinite(value) ? value.toFixed(places) : '—';
}

function SectionEditor({ label, section, autoNote, onChange }: {
  label: string;
  section: FilterSection | null;
  autoNote?: string;
  onChange: (section: FilterSection | null) => void;
}) {
  const id = useId();
  const orders = section ? familyOrders(section.family) : [];
  return <div className="crossover-band">
    <label className="crossover-band-name" htmlFor={`${id}-hz`}>{label}</label>
    <button
      type="button"
      className={`crossover-band-toggle${section ? ' on' : ''}`}
      aria-pressed={section !== null}
      title={section ? `Turn the ${label.toLowerCase()} off` : `Turn the ${label.toLowerCase()} on`}
      onClick={() => onChange(section ? null : { family: 'lr', order: 4, fcHz: 1_000 })}
    >{section ? 'On' : 'Off'}</button>
    {section && <>
      <input
        id={`${id}-hz`}
        type="number"
        min={1}
        step={10}
        value={section.fcHz}
        aria-label={`${label} frequency in hertz`}
        onChange={(event) => {
          const fcHz = Number(event.target.value);
          if (Number.isFinite(fcHz) && fcHz > 0) onChange({ ...section, fcHz });
        }}
      />
      <span className="crossover-unit">Hz</span>
      <select
        aria-label={`${label} family`}
        value={section.family}
        onChange={(event) => {
          const family = event.target.value as FilterFamily;
          onChange({ family, order: nearestOrder(family, section.order), fcHz: section.fcHz });
        }}
      >{FILTER_FAMILIES.map((family) => <option key={family} value={family}>{FILTER_FAMILY_LABELS[family]}</option>)}</select>
      <select
        aria-label={`${label} slope`}
        value={section.order}
        onChange={(event) => onChange({ ...section, order: Number(event.target.value) })}
      >{orders.map((order) => <option key={order} value={order}>{slopeLabel(order)}</option>)}</select>
    </>}
    {!section && autoNote && <span className="crossover-auto-note">{autoNote}</span>}
  </div>;
}

function AutoManualField({ label, unit, precision, step, mode, value, autoValue, extra, onMode, onValue }: {
  label: string;
  unit: string;
  precision: number;
  step: number;
  mode: 'auto' | 'manual';
  value: number;
  autoValue: number | null;
  extra?: string;
  onMode: (mode: 'auto' | 'manual') => void;
  onValue: (value: number) => void;
}) {
  const id = useId();
  const autoText = autoValue === null ? 'auto' : `${decimals(autoValue, precision)} ${unit}`;
  return <div className="crossover-band">
    <label className="crossover-band-name" htmlFor={id}>{label}</label>
    <div className="crossover-segment" role="group" aria-label={`${label} mode`}>
      <button type="button" aria-pressed={mode === 'auto'} className={mode === 'auto' ? 'on' : ''} onClick={() => onMode('auto')}>Auto</button>
      <button type="button" aria-pressed={mode === 'manual'} className={mode === 'manual' ? 'on' : ''} onClick={() => onMode('manual')}>Manual</button>
    </div>
    {mode === 'auto'
      // Dashed and read-only: the number is real, it just was not typed. An
      // empty box here would hide the one thing the user came to check.
      ? <output id={id} className="crossover-auto-value">{autoText}</output>
      : <>
        <input
          id={id}
          type="number"
          step={step}
          value={value}
          aria-label={`${label} in ${unit}`}
          onChange={(event) => {
            const next = Number(event.target.value);
            if (Number.isFinite(next)) onValue(next);
          }}
        />
        <span className="crossover-unit">{unit}</span>
        <button
          type="button"
          className="crossover-reset"
          title={`Go back to the automatic ${label.toLowerCase()} (${autoText})`}
          onClick={() => onMode('auto')}
        >Reset to auto</button>
      </>}
    {extra && <span className="crossover-auto-note">{extra}</span>}
  </div>;
}

export function CrossoverAdvanced({ anchorRef, onClose, spec, resolved = NO_RESOLVED, memberLabel, onChange }: {
  anchorRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  spec: CrossoverSpec;
  /** The values the latest shown result resolved, so auto can show a number. */
  resolved?: Record<string, ResolvedChannel>;
  memberLabel: (member: string) => string;
  onChange: (spec: CrossoverSpec) => void;
}) {
  const referenceId = useId();
  return <AnchoredPanel anchorRef={anchorRef} onClose={onClose} className="crossover-advanced" label="Crossover advanced settings">
    <div className="crossover-advanced-head">
      <label className="crossover-reference" htmlFor={referenceId}>Reference
        <select
          id={referenceId}
          value={spec.reference}
          onChange={(event) => onChange(withReference(spec, event.target.value))}
        >{spec.members.map((member) => <option key={member} value={member}>{memberLabel(member)}</option>)}</select>
      </label>
      <button
        type="button"
        title="Make every pair symmetric again, taking each crossover from the lower channel's low-pass"
        onClick={() => onChange(relinkPairs(spec))}
      >Relink pairs</button>
    </div>
    <p className="crossover-advanced-note">The reference channel is pinned at 0 ms; automatic delays move the others around it.</p>
    {spec.members.map((member) => {
      const channel = spec.channels[member];
      if (!channel) return null;
      const state = resolved[member];
      const invertLabel = state?.inverted === undefined || state?.inverted === null
        ? 'Auto'
        : `Auto (${state.inverted ? 'inverted' : 'in phase'})`;
      return <section key={member} className="crossover-channel" aria-label={`${memberLabel(member)} crossover`}>
        <h4>{memberLabel(member)}{member === spec.reference && <span className="crossover-reference-chip">reference</span>}</h4>
        <SectionEditor
          label="High-pass"
          section={channel.hp}
          onChange={(hp) => onChange(withChannel(spec, member, { hp }))}
        />
        <SectionEditor
          label="Low-pass"
          section={channel.lp}
          onChange={(lp) => onChange(withChannel(spec, member, { lp }))}
        />
        <AutoManualField
          label="Gain"
          unit="dB"
          precision={2}
          step={0.1}
          mode={channel.gain.mode}
          value={channel.gain.mode === 'manual' ? channel.gain.db : state?.gainAutoDb ?? 0}
          autoValue={state?.gainAutoDb ?? null}
          onMode={(mode) => onChange(withChannel(spec, member, {
            gain: mode === 'manual'
              ? { mode: 'manual', db: channel.gain.mode === 'manual' ? channel.gain.db : state?.gainAutoDb ?? 0 }
              : { mode: 'auto' },
          }))}
          onValue={(db) => onChange(withChannel(spec, member, { gain: { mode: 'manual', db } }))}
        />
        <AutoManualField
          label="Delay"
          unit="ms"
          precision={2}
          step={0.01}
          mode={channel.delay.mode}
          value={channel.delay.mode === 'manual' ? channel.delay.ms : state?.delayAutoMs ?? 0}
          autoValue={state?.delayAutoMs ?? null}
          // A delay is a baffle offset by another name, so the distance it
          // stands for is stated beside it rather than left to be worked out.
          extra={`${decimals(delayDistanceMm(channel.delay.mode === 'manual' ? channel.delay.ms : state?.delayAutoMs ?? 0), 1)} mm at 343 m/s`}
          onMode={(mode) => onChange(withChannel(spec, member, {
            delay: mode === 'manual'
              ? { mode: 'manual', ms: channel.delay.mode === 'manual' ? channel.delay.ms : state?.delayAutoMs ?? 0 }
              : { mode: 'auto' },
          }))}
          onValue={(ms) => onChange(withChannel(spec, member, { delay: { mode: 'manual', ms } }))}
        />
        <div className="crossover-band">
          <span className="crossover-band-name">Invert</span>
          <div className="crossover-segment" role="group" aria-label={`${memberLabel(member)} polarity`}>
            <button type="button" aria-pressed={channel.invert === null} className={channel.invert === null ? 'on' : ''} onClick={() => onChange(withChannel(spec, member, { invert: null }))}>{invertLabel}</button>
            <button type="button" aria-pressed={channel.invert === true} className={channel.invert === true ? 'on' : ''} onClick={() => onChange(withChannel(spec, member, { invert: true }))}>On</button>
            <button type="button" aria-pressed={channel.invert === false} className={channel.invert === false ? 'on' : ''} onClick={() => onChange(withChannel(spec, member, { invert: false }))}>Off</button>
          </div>
        </div>
      </section>;
    })}
  </AnchoredPanel>;
}
