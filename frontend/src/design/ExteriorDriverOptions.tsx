import { useRef } from 'react';
import { EXTERIOR_FIELDS, EXTERIOR_LIMIT_FIELDS, SEMI_INDUCTANCE_FIELDS, exteriorDisplayScale,
  normalizeExteriorOptions, type ExteriorTransducerForm } from '../stores/exteriorTransducer';

export function ExteriorDriverFields({ channelId, form, onChange }: {
  channelId: string; form: ExteriorTransducerForm; onChange: (form: ExteriorTransducerForm) => void;
}) {
  return <div className="cad-driver-grid">{EXTERIOR_FIELDS.map(([key, label, unit]) => <label key={key} className="cad-driver-field">
    <span>{label} ({unit}) *</span><input type="number" aria-label={`${label} for ${channelId}`}
      value={form[key] === undefined ? '' : form[key] * exteriorDisplayScale(key)}
      onChange={event => onChange({ ...form, [key]: event.target.value === '' ? undefined : Number(event.target.value) / exteriorDisplayScale(key) })}/>
    {key === 'le_h' && form[key] !== undefined && <small>{form[key]} H</small>}
  </label>)}</div>;
}

/** Optional physical models; disabled models retain their values in this editor. */
export function ExteriorDriverOptions({ channelId, form, onChange }: {
  channelId: string; form: ExteriorTransducerForm; onChange: (form: ExteriorTransducerForm) => void;
}) {
  const semiCache = useRef(form.semi_inductance);
  const chamberCache = useRef(form.lumped_sealed_rear_chamber);
  const semi = form.semi_inductance;
  const chamber = form.lumped_sealed_rear_chamber;
  return <>
    <label className="cad-driver-hint"><input type="checkbox" aria-label={`Semi-inductance for ${channelId}`}
      checked={semi?.enabled ?? false} onChange={event => {
        semiCache.current = semi ?? semiCache.current;
        onChange(normalizeExteriorOptions({ ...form,
          semi_inductance: { ...semiCache.current, enabled: event.target.checked } }));
      }}/>Semi-inductance (Thorborg–Futtrup)</label>
    {semi?.enabled && <>
      <p className="cad-driver-hint">Re′, Leb and the parallel Le/Ke/Rss model replace the simple Re/Le electrical model. All five values must be positive. Ke is entered in mH/√s and stored in H/√s.</p>
      <div className="cad-driver-grid">{SEMI_INDUCTANCE_FIELDS.map(([key, label, unit]) => <label key={key} className="cad-driver-field">
        <span>{label} ({unit}) *</span><input type="number" min="0" step="any" aria-label={`${label} for ${channelId}`} value={semi[key] === undefined ? '' : semi[key] * exteriorDisplayScale(key)}
          onChange={event => onChange({ ...form, semi_inductance: { ...semi, [key]: event.target.value === '' ? undefined : Number(event.target.value) / exteriorDisplayScale(key) } })}/>
        {exteriorDisplayScale(key) !== 1 && semi[key] !== undefined && <small>{semi[key]} {key === 'ke_semi_h' ? 'H/√s' : 'H'}</small>}
      </label>)}</div>
    </>}
    <label className="cad-driver-hint"><input type="checkbox" aria-label={`Lumped sealed rear chamber for ${channelId}`}
      checked={chamber?.enabled ?? false} onChange={event => {
        chamberCache.current = chamber ?? chamberCache.current;
        onChange(normalizeExteriorOptions({ ...form,
          lumped_sealed_rear_chamber: { ...chamberCache.current, enabled: event.target.checked } }));
      }}/>Lumped sealed rear chamber</label>
    {chamber?.enabled && <>
      <p className="cad-driver-hint">Adds lossless sealed-box stiffness ρc²Sd²/V using the net projected Sd of a front-only diaphragm on a closed body. Use this for an unmeshed rear chamber; untag exterior rear faces or disable this chamber.</p>
      <label className="cad-driver-field"><span>Rear chamber volume (litres) *</span>
        <input type="number" min="0" step="any" aria-label={`Rear chamber volume for ${channelId}`} value={chamber.volume_m3 === undefined ? '' : chamber.volume_m3 * 1000}
          onChange={event => onChange({ ...form, lumped_sealed_rear_chamber: { ...chamber, volume_m3: event.target.value === '' ? undefined : Number(event.target.value) / 1000 } })}/>
        {chamber.volume_m3 !== undefined && <small>{chamber.volume_m3} m³</small>}
      </label>
    </>}
    <div className="cad-driver-grid">{EXTERIOR_LIMIT_FIELDS.map(([key, label, unit]) => <label key={key} className="cad-driver-field">
      <span>{label} ({unit}, optional)</span><input type="number" min="0" step="any" aria-label={`${label} for ${channelId}`} value={form[key] ?? ''}
        onChange={event => onChange(normalizeExteriorOptions({ ...form, [key]: event.target.value === '' ? undefined : Number(event.target.value) }))}/>
    </label>)}</div>
  </>;
}
