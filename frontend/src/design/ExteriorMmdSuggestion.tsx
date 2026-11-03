import { useEffect, useState } from 'react';
import { suggestMmd, type MmdSuggestion } from '../api/drivers';
import { importedSimulationSetup, useImportedSimulationSetup } from '../stores/importedSimulationSetup';
import { failureWords } from '../api/httpError';

/** Grams for display: two decimals below 100 g, one above. */
export function formatGrams(kg: number): string {
  const grams = kg * 1000;
  return grams >= 100 ? grams.toFixed(1) : grams.toFixed(2);
}
/** A stored input echoed back without float noise (124.69999999999999 → 124.7). */
const echo = (value: number) => String(Number(value.toPrecision(6)));
const sentence = (text: string) => /[.!?]$/.test(text.trim()) ? text.trim() : `${text.trim()}.`;

/**
 * Shared by CAD, mesh files and live assembly drivers. No mass is inferred locally.
 * Rendered inside the T/S grid directly under the Mmd field it fills.
 */
export function ExteriorMmdSuggestion({ channelId }: { channelId: string }) {
  const state = useImportedSimulationSetup();
  const channel = state.driveChannels.find(channel => channel.id === channelId);
  const form = channel?.exterior_transducer;
  const inputs = channel?.exterior_driver_inputs;
  const mms = inputs?.mms_g;
  const sd = form?.sd_m2;
  const missingMmd = form?.mmd_kg === undefined;
  const identity = `${state.input}:${channelId}:${inputs?.driver_id ?? ''}`;
  const key = `${identity}:${mms}:${sd}`;
  const [result, setResult] = useState<{ key: string; value: MmdSuggestion } | null>(null);
  useEffect(() => {
    if (!missingMmd || !mms || !sd || !Number.isFinite(mms) || !Number.isFinite(sd) || mms <= 0 || sd <= 0) return;
    let active = true;
    void suggestMmd(mms, sd * 10000).then(value => {
      if (active) setResult({ key, value });
    }).catch(error => {
      if (active) setResult({ key, value: { reason: failureWords(error) } });
    });
    return () => { active = false; };
  }, [key, mms, sd, missingMmd]);
  if (!form) return null;
  const answer = result?.key === key ? result.value : null;
  const suggestion = answer?.suggestion;
  const derived = inputs?.derived_mmd;
  const showMms = missingMmd || mms != null;
  if (!showMms && !derived) return null;
  const unchanged = derived != null && form.mmd_kg === derived.mmd_kg;
  return <div className="mmd-suggest">
    {showMms && <label className="cad-driver-field">
      <span>Datasheet Mms (g)</span>
      <input type="number" min="0" step="any" aria-label={`Mms for ${channelId}`} value={mms ?? ''}
        onChange={event => state.setExteriorTransducer(channelId, form, { ...inputs,
          mms_g: event.target.value === '' ? undefined : Number(event.target.value) })}/>
    </label>}
    {missingMmd && mms != null && (!Number.isFinite(mms) || mms <= 0)
      && <p className="mmd-suggest-hint">Enter a positive Mms to suggest Mmd.</p>}
    {missingMmd && mms != null && !(sd && sd > 0)
      && <p className="mmd-suggest-hint">Enter Sd to suggest Mmd.</p>}
    {missingMmd && answer && (suggestion
      ? <div className={`mmd-suggest-note${answer.warning ? ' warn' : ''}`} role="status">
        <div className="mmd-suggest-head"><p>Suggested Mmd <b>{formatGrams(suggestion.mmd_kg)} g</b></p>
        <button className="btn" type="button" onClick={() => {
          const current = importedSimulationSetup();
          const latest = current.driveChannels.find(channel => channel.id === channelId);
          // A response or click from an old driver/model must not fill its replacement.
          if (current.input !== state.input || !latest?.exterior_transducer || latest.exterior_transducer.mmd_kg !== undefined
            || latest?.exterior_transducer?.sd_m2 !== sd || latest?.exterior_driver_inputs?.mms_g !== mms
            || latest?.exterior_driver_inputs?.driver_id !== inputs?.driver_id) return;
          current.setExteriorTransducer(channelId, { ...latest.exterior_transducer, mmd_kg: suggestion.mmd_kg },
            { ...latest.exterior_driver_inputs, derived_mmd: suggestion });
        }}>Use suggested Mmd</button></div>
        <p>Mms minus {formatGrams(suggestion.correction_kg)} g free-air load
          {' '}({(suggestion.correction_fraction * 100).toFixed(1)}% of Mms).</p>
        {answer.warning && <p className="mmd-suggest-warning">{answer.warning}</p>}
      </div>
      : <p className="mmd-suggest-note warn" role="status">
        <span><b>No Mmd suggestion.</b> {sentence(answer.reason ?? '')}
        {!/\bMmd\b/.test(answer.reason ?? '') && ' Enter Mmd from the datasheet.'}</span>
      </p>)}
    {derived && <p className="mmd-suggest-hint">
      {unchanged
        ? `Mmd derived from Mms ${echo(derived.mms_g)} g and Sd ${echo(derived.sd_cm2)} cm² (free-air load removed).`
        : `Mmd edited by hand. The suggestion was ${formatGrams(derived.mmd_kg)} g, from Mms ${echo(derived.mms_g)} g and Sd ${echo(derived.sd_cm2)} cm².`}
      {unchanged && <> <button className="btn-link" type="button" onClick={() => {
        state.setExteriorTransducer(channelId, { ...form, mmd_kg: undefined }, { ...inputs, derived_mmd: undefined });
      }}>Undo Mmd suggestion</button></>}
    </p>}
  </div>;
}
