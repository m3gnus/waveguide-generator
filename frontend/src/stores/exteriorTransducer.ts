/** Bare driver parameters for BEAT's coupled exterior solve. SI throughout. */
export const EXTERIOR_FIELDS = [
  ['re_ohm', 'Resistance', 'Ω'], ['le_h', 'Inductance', 'H'],
  ['bl_n_per_a', 'Force factor', 'N/A'], ['mmd_kg', 'Bare moving mass (Mmd)', 'kg'],
  ['cms_m_per_n', 'Compliance', 'm/N'], ['rms_n_s_per_m', 'Mechanical resistance', 'N·s/m'],
] as const;
export type ExteriorKey = typeof EXTERIOR_FIELDS[number][0];
export type ExteriorTransducerForm = Partial<Record<ExteriorKey, number>> & {
  version: number; motion_axis: [number, number, number];
};

export function exteriorTransducerError(form: ExteriorTransducerForm): string | null {
  if (form.version !== 1) return 'Unsupported exterior driver version. Clear and re-enter the driver.';
  for (const [key, label] of EXTERIOR_FIELDS) {
    const value = form[key];
    if (typeof value !== 'number' || !Number.isFinite(value) || (key === 'le_h' ? value < 0 : value <= 0)) {
      return `Enter ${label.toLowerCase()} in the stated SI units.`;
    }
  }
  if (form.motion_axis.length !== 3 || !form.motion_axis.every(Number.isFinite) || !form.motion_axis.some(v => v !== 0)) {
    return 'Enter a finite nonzero motion axis in the returned mesh frame.';
  }
  return null;
}

/** Keep malformed/unknown saved modes visibly blocked rather than dropping physics. */
export function parseExteriorTransducer(value: unknown): ExteriorTransducerForm {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { version: 0, motion_axis: [0, 0, 0] };
  const raw = value as Record<string, unknown>;
  const known = new Set<string>(['version', 'motion_axis', ...EXTERIOR_FIELDS.map(([key]) => key)]);
  if (Object.keys(raw).some(key => !known.has(key))) return { version: 0, motion_axis: [0, 0, 0] };
  return { ...Object.fromEntries(EXTERIOR_FIELDS.flatMap(([key]) => typeof raw[key] === 'number' && Number.isFinite(raw[key]) ? [[key, raw[key]]] : [])),
    version: typeof raw.version === 'number' ? raw.version : 0,
    motion_axis: Array.isArray(raw.motion_axis) && raw.motion_axis.length === 3 && raw.motion_axis.every(v => typeof v === 'number' && Number.isFinite(v))
      ? raw.motion_axis as [number, number, number] : [0, 0, 0] };
}
