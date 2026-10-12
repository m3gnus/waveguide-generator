import { SEMI_INDUCTANCE_FIELDS, type ExteriorTransducerForm } from '../stores/exteriorTransducer';

/** Optional physical models; disabled models retain their setup values. */
export function ExteriorDriverOptions({ channelId, form, onChange }: {
  channelId: string; form: ExteriorTransducerForm; onChange: (form: ExteriorTransducerForm) => void;
}) {
  const semi = form.semi_inductance;
  const chamber = form.lumped_sealed_rear_chamber;
  return <>
    <label className="cad-driver-hint"><input type="checkbox" aria-label={`Semi-inductance for ${channelId}`}
      checked={semi?.enabled ?? false} onChange={event => onChange({ ...form, version: 2,
        semi_inductance: { ...semi, enabled: event.target.checked } })}/>Semi-inductance (Thorborg–Futtrup)</label>
    {semi?.enabled && <>
      <p className="cad-driver-hint">Re′, Leb and the parallel Le/Ke/Rss model replace the simple Re/Le electrical model. All five values must be positive. Ke is in H/√s.</p>
      <div className="cad-driver-grid">{SEMI_INDUCTANCE_FIELDS.map(([key, label, unit]) => <label key={key} className="cad-driver-field">
        <span>{label} ({unit}) *</span><input type="number" min="0" step="any" aria-label={`${label} for ${channelId}`} value={semi[key] ?? ''}
          onChange={event => onChange({ ...form, semi_inductance: { ...semi, [key]: event.target.value === '' ? undefined : Number(event.target.value) } })}/>
      </label>)}</div>
    </>}
    <label className="cad-driver-hint"><input type="checkbox" aria-label={`Lumped sealed rear chamber for ${channelId}`}
      checked={chamber?.enabled ?? false} onChange={event => onChange({ ...form, version: 2,
        lumped_sealed_rear_chamber: { ...chamber, enabled: event.target.checked } })}/>Lumped sealed rear chamber</label>
    {chamber?.enabled && <>
      <p className="cad-driver-hint">Adds lossless sealed-box stiffness ρc²Sd²/V. Sd comes from the moving faces; front and rear projected areas are averaged, with a warning above 10% difference. Use this for an unmeshed rear chamber.</p>
      <label className="cad-driver-field"><span>Rear chamber volume (m³) *</span>
        <input type="number" min="0" step="any" aria-label={`Rear chamber volume for ${channelId}`} value={chamber.volume_m3 ?? ''}
          onChange={event => onChange({ ...form, lumped_sealed_rear_chamber: { ...chamber, volume_m3: event.target.value === '' ? undefined : Number(event.target.value) } })}/>
      </label>
    </>}
  </>;
}
