import { useRef } from 'react';
import { EXTERIOR_FIELDS, EXTERIOR_LIMIT_FIELDS, EXTERIOR_SD_FIELD, SEMI_INDUCTANCE_FIELDS, exteriorDisplayScale,
  normalizeExteriorOptions, isExteriorDriverReplacement, type ExteriorTransducerForm } from '../stores/exteriorTransducer';

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
  const previousForm = useRef(form);
  // An explicit library pick clears the previous driver's disabled cache too.
  // Ordinary field edits retain values for toggling an option back on.
  if (previousForm.current !== form && isExteriorDriverReplacement(form)) {
    semiCache.current = form.semi_inductance;
  }
  previousForm.current = form;
  const semi = form.semi_inductance;
  const chamber = form.lumped_sealed_rear_chamber;
  return <>
    <label className="cad-driver-field"><span>Sd (cm², optional)</span>
      <input type="number" min="0" step="any" aria-label={`Sd for ${channelId}`}
        value={form.sd_m2 === undefined ? '' : form.sd_m2 * exteriorDisplayScale(EXTERIOR_SD_FIELD[0])}
        onChange={event => onChange(normalizeExteriorOptions({ ...form,
          sd_m2: event.target.value === '' ? undefined : Number(event.target.value) / 10000 }))}/>
    </label>
    <p className="cad-driver-hint">Sd is the driver's effective projected area. Check the library value or enter it from the datasheet. Declared Sd controls chamber stiffness and acoustic impedance; geometry is a cross-check.</p>
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
      <p className="cad-driver-hint">Adds lossless sealed-box stiffness ρc²Sd²/V using the declared Sd. Use this for a front-only diaphragm with an unmeshed rear chamber; untag exterior rear faces or disable this chamber.</p>
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
