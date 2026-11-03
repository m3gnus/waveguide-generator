import { useEffect, useState } from 'react';
import { suggestMmd, type MmdSuggestion } from '../api/drivers';
import { importedSimulationSetup, useImportedSimulationSetup } from '../stores/importedSimulationSetup';
import { failureWords } from '../api/httpError';

/** Shared by CAD, mesh files and live assembly drivers. No mass is inferred locally. */
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
  return <>
    {(missingMmd || mms != null) && <label className="cad-driver-field">
      <span>Moving mass with air load (Mms) (g, for Mmd suggestion)</span>
      <input type="number" min="0" step="any" aria-label={`Mms for ${channelId}`} value={mms ?? ''}
        onChange={event => state.setExteriorTransducer(channelId, form, { ...inputs,
          mms_g: event.target.value === '' ? undefined : Number(event.target.value) })}/>
    </label>}
    {missingMmd && mms != null && (!Number.isFinite(mms) || mms <= 0)
      && <p className="section-note">Enter positive Mms in grams to suggest Mmd.</p>}
    {missingMmd && mms != null && !(sd && sd > 0)
      && <p className="section-note">Enter Sd in the driver options to suggest Mmd.</p>}
    {missingMmd && answer && <p className="section-note" role="status">
      {suggestion ? <>
        Suggested Mmd: {suggestion.mmd_kg.toPrecision(6)} kg. Suggested from Mms and Sd (free-air air-load removed).
        {' '}{(suggestion.correction_kg * 1000).toPrecision(4)} g removed ({(suggestion.correction_fraction * 100).toFixed(1)}% of Mms).
        {answer.warning && <> {answer.warning}</>}
        {' '}<button type="button" onClick={() => {
          const current = importedSimulationSetup();
          const latest = current.driveChannels.find(channel => channel.id === channelId);
          // A response or click from an old driver/model must not fill its replacement.
          if (current.input !== state.input || !latest?.exterior_transducer || latest.exterior_transducer.mmd_kg !== undefined
            || latest?.exterior_transducer?.sd_m2 !== sd || latest?.exterior_driver_inputs?.mms_g !== mms
            || latest?.exterior_driver_inputs?.driver_id !== inputs?.driver_id) return;
          current.setExteriorTransducer(channelId, { ...latest.exterior_transducer, mmd_kg: suggestion.mmd_kg },
            { ...latest.exterior_driver_inputs, derived_mmd: suggestion });
        }}>Use suggested Mmd</button>
      </> : <>No Mmd suggestion: {answer.reason}</>}
    </p>}
    {derived && <p className="cad-driver-hint">
      Mmd {form.mmd_kg === derived.mmd_kg ? 'derived' : 'edited after derivation'} from Mms {derived.mms_g} g and Sd {derived.sd_cm2} cm² (free-air air-load removed).
      {form.mmd_kg === derived.mmd_kg && <> <button type="button" onClick={() => {
        state.setExteriorTransducer(channelId, { ...form, mmd_kg: undefined }, { ...inputs, derived_mmd: undefined });
      }}>Undo Mmd suggestion</button></>}
    </p>}
  </>;
}
