import { useId } from 'react';
import {
  driverXoMinNote,
  familyOrders,
  FILTER_FAMILIES,
  FILTER_FAMILY_LABELS,
  gainFromUnit,
  gainInUnit,
  gainText,
  GAIN_UNIT_LABELS,
  GAIN_UNITS,
  maxLimitNote,
  nearestOrder,
  relinkPairs,
  slopeLabel,
  withChannel,
  withReference,
  delayDistanceMm,
  type CrossoverSpec,
  type FilterFamily,
  type FilterSection,
  type GainUnit,
  type ResolvedChannel,
} from '../results/crossoverSpec';
import type { MaxOutputMemberTrace } from '../results/types';
import type { DriverPreset } from '../stores/cadReturn';

/**
 * The per-channel crossover editor, rendered inline as the Crossover section's
 * Advanced view.
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

function SectionEditor({ label, section, onChange }: {
  label: string;
  section: FilterSection | null;
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
        step="any"
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
          // Emptying the field is the same gesture as everywhere else in this
          // app: clear a value to give it back to whatever computes it.
          onChange={(event) => {
            if (event.target.value.trim() === '') { onMode('auto'); return; }
            const next = Number(event.target.value);
            if (Number.isFinite(next)) onValue(next);
          }}
        />
        <span className="crossover-unit">{unit}</span>
        {/* The number auto would have chosen stays visible beside the one the
            user typed; taking a value over should not hide what it replaced. */}
        <span className="crossover-auto-note">auto {autoText}</span>
        <button
          type="button"
          className="crossover-reset"
          title={`Go back to the automatic ${label.toLowerCase()} (${autoText}); clearing the field does the same`}
          onClick={() => onMode('auto')}
        >Reset to auto</button>
      </>}
    {extra && <span className="crossover-auto-note">{extra}</span>}
  </div>;
}

/**
 * How hard one member is driven, in the unit the question is being asked in.
 *
 * Three modes and three units, and the number the other two would have chosen
 * stays on screen in all of them. Auto is the level match; Max is the loudest
 * this member's own driver can be run, solved against the crossover it has;
 * Manual is whatever the user types. Taking a mode over should never begin by
 * hiding what it replaced, and never by moving the curve.
 *
 * dB is what is stored and what the crossover applies. Volts and watts are
 * views of it against the drive voltage the run was solved at -- volts exactly,
 * watts into the nominal impedance the driver's power rating is quoted against,
 * which is the only impedance that makes the two comparable.
 */
function GainField({
  mode, db, autoDb, maxDb, maxLimit, maxLimitHz, maxLimitAtBandEdge, usage, unit,
  driveVoltageV, impedanceOhm, onUnit, onMode, onValue,
}: {
  mode: 'auto' | 'manual' | 'max';
  db: number;
  autoDb: number | null;
  maxDb: number | null;
  maxLimit: 'xmax' | 'power' | 'voltage' | null;
  maxLimitHz: number | null;
  maxLimitAtBandEdge: boolean;
  usage: MaxOutputMemberTrace | null;
  unit: GainUnit;
  driveVoltageV: number | null;
  impedanceOhm: number | null;
  onUnit: (unit: GainUnit) => void;
  onMode: (mode: 'auto' | 'manual' | 'max') => void;
  onValue: (db: number) => void;
}) {
  const id = useId();
  const inUnit = (value: number | null) => (
    value === null ? null : gainInUnit(value, unit, driveVoltageV, impedanceOhm)
  );
  // A unit the run cannot form -- no drive voltage, or a driver with no
  // impedance to divide by -- would show every field as a dash. Fall back to
  // dB rather than blanking the control the user came here to read.
  const shown = unit === 'db' || inUnit(db) !== null ? unit : 'db';
  const value = shown === unit ? inUnit(db) : db;
  const autoText = gainText(shown === unit ? inUnit(autoDb) : autoDb, shown);
  const maxText = maxDb === null ? null : gainText(shown === unit ? inUnit(maxDb) : maxDb, shown);
  const limitNote = maxLimitNote(maxLimit, maxLimitHz, maxLimitAtBandEdge);
  return <>
    <div className="crossover-band">
      <label className="crossover-band-name" htmlFor={id}>Gain</label>
      <div className="crossover-segment" role="group" aria-label="Gain mode">
        <button type="button" aria-pressed={mode === 'auto'} className={mode === 'auto' ? 'on' : ''} onClick={() => onMode('auto')}>Auto</button>
        <button type="button" aria-pressed={mode === 'manual'} className={mode === 'manual' ? 'on' : ''} onClick={() => onMode('manual')}>Manual</button>
        <button
          type="button"
          aria-pressed={mode === 'max'}
          className={mode === 'max' ? 'on' : ''}
          disabled={maxDb === null}
          title={maxDb === null
            ? 'This channel has no driver limit to reach: give its driver an Xmax or a rated power, or set an amplifier ceiling.'
            : `Drive this channel as loud as its driver allows${limitNote ? ` (${limitNote})` : ''}`}
          onClick={() => onMode('max')}
        >Max</button>
      </div>
      {mode === 'manual'
        ? <input
            id={id}
            type="number"
            step={shown === 'db' ? 0.1 : 'any'}
            value={value ?? 0}
            aria-label={`Gain in ${GAIN_UNIT_LABELS[shown]}`}
            onChange={(event) => {
              if (event.target.value.trim() === '') { onMode('auto'); return; }
              const typed = Number(event.target.value);
              if (!Number.isFinite(typed)) return;
              const next = gainFromUnit(typed, shown, driveVoltageV, impedanceOhm);
              if (next !== null && Number.isFinite(next)) onValue(next);
            }}
          />
        // Dashed and read-only: the number is real, it just was not typed.
        : <output id={id} className="crossover-auto-value">{gainText(value, shown)}</output>}
      <select
        className="crossover-unit-select"
        aria-label="Gain unit"
        value={unit}
        onChange={(event) => onUnit(event.target.value as GainUnit)}
      >{GAIN_UNITS.map((option) => <option key={option} value={option}>{GAIN_UNIT_LABELS[option]}</option>)}</select>
      {mode !== 'auto' && <span className="crossover-auto-note">auto {autoText}</span>}
      {mode !== 'max' && maxText && <span className="crossover-auto-note">max {maxText}</span>}
      {mode !== 'auto' && <button
        type="button"
        className="crossover-reset"
        title={`Go back to the automatic gain (${autoText}); clearing the field does the same`}
        onClick={() => onMode('auto')}
      >Reset to auto</button>}
    </div>
    {(limitNote || usage) && <p className="crossover-advanced-note">
      {limitNote && <>Ceiling: {limitNote}. </>}
      {usage && ratingsText(usage)}
    </p>}
  </>;
}

/** "Xmax 62% \u00b7 rated power 31%" -- how much of each rating the shown
 * settings already spend, so the headroom is a fact rather than a promise. */
function ratingsText(usage: MaxOutputMemberTrace): string | null {
  const parts = [
    ['Xmax', usage.excursion_fraction],
    ['rated power', usage.power_fraction],
    ['amplifier', usage.voltage_fraction],
  ] as const;
  const shown = parts
    .filter(([, fraction]) => typeof fraction === 'number' && Number.isFinite(fraction))
    .map(([label, fraction]) => `${label} ${(100 * (fraction as number)).toFixed(0)}%`);
  return shown.length ? `At the shown level: ${shown.join(' \u00b7 ')}.` : null;
}

export function CrossoverAdvanced({
  spec,
  resolved = NO_RESOLVED,
  memberLabel,
  presetFor,
  gainUnit = 'db',
  onGainUnit = () => {},
  driveVoltageV = null,
  impedanceFor,
  usageFor,
  onChange,
}: {
  spec: CrossoverSpec;
  /** The values the latest shown result resolved, so auto can show a number. */
  resolved?: Record<string, ResolvedChannel>;
  memberLabel: (member: string) => string;
  /** The driver picked for a member's channel, if any — read for the
   * high-pass minimum-crossover note. */
  presetFor?: (member: string) => DriverPreset | null;
  /** Which unit the gain fields are read and typed in, and how to change it.
   * One choice for the whole section: comparing members across three different
   * units is not a comparison. */
  gainUnit?: GainUnit;
  onGainUnit?: (unit: GainUnit) => void;
  /** The voltage the run was solved at, which is what a gain in volts or watts
   * is measured from. */
  driveVoltageV?: number | null;
  /** The nominal impedance a member's watts are stated into. */
  impedanceFor?: (member: string) => number | null;
  /** How much of each rating a member already spends at the shown level. */
  usageFor?: (member: string) => MaxOutputMemberTrace | null;
  onChange: (spec: CrossoverSpec) => void;
}) {
  const referenceId = useId();
  return <div className="crossover-advanced-inline" role="group" aria-label="Crossover advanced settings">
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
      const preset = presetFor?.(member) ?? null;
      const hpXoNote = channel.hp && preset?.xo_min_hz != null && channel.hp.fcHz < preset.xo_min_hz
        ? driverXoMinNote(preset.label, preset.xo_min_hz, channel.hp.fcHz)
        : null;
      return <section key={member} className="crossover-channel" aria-label={`${memberLabel(member)} crossover`}>
        <h4>{memberLabel(member)}{member === spec.reference && <span className="crossover-reference-chip">reference</span>}</h4>
        <SectionEditor
          label="High-pass"
          section={channel.hp}
          onChange={(hp) => onChange(withChannel(spec, member, { hp }))}
        />
        {hpXoNote && <p className="section-note warning" role="status">{hpXoNote}</p>}
        <SectionEditor
          label="Low-pass"
          section={channel.lp}
          onChange={(lp) => onChange(withChannel(spec, member, { lp }))}
        />
        <GainField
          mode={channel.gain.mode}
          db={
            channel.gain.mode === 'manual'
              ? channel.gain.db
              : (channel.gain.mode === 'max' ? state?.gainMaxDb : state?.gainAutoDb) ?? 0
          }
          autoDb={state?.gainAutoDb ?? null}
          maxDb={state?.gainMaxDb ?? null}
          maxLimit={state?.maxLimit ?? null}
          maxLimitHz={state?.maxLimitHz ?? null}
          maxLimitAtBandEdge={state?.maxLimitAtBandEdge ?? false}
          usage={usageFor?.(member) ?? null}
          unit={gainUnit}
          driveVoltageV={driveVoltageV ?? null}
          impedanceOhm={impedanceFor?.(member) ?? null}
          onUnit={onGainUnit}
          onMode={(mode) => onChange(withChannel(spec, member, {
            gain: mode === 'manual'
              // Manual starts from whatever is on screen, so taking the level
              // over never begins by moving it.
              ? {
                mode: 'manual',
                db: channel.gain.mode === 'manual'
                  ? channel.gain.db
                  : (channel.gain.mode === 'max' ? state?.gainMaxDb : state?.gainAutoDb) ?? 0,
              }
              : mode === 'max' ? { mode: 'max' } : { mode: 'auto' },
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
  </div>;
}
