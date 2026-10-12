/** Bare driver parameters for BEAT's coupled exterior solve. SI throughout. */
export const EXTERIOR_FIELDS = [
  ['re_ohm', 'Resistance', 'Ω'], ['le_h', 'Inductance', 'mH'],
  ['bl_n_per_a', 'Force factor', 'N/A'], ['mmd_kg', 'Bare moving mass (Mmd)', 'kg'],
  ['cms_m_per_n', 'Compliance', 'm/N'], ['rms_n_s_per_m', 'Mechanical resistance', 'N·s/m'],
] as const;
export const SEMI_INDUCTANCE_FIELDS = [
  ['re_prime_ohm', 'Re′', 'Ω'], ['leb_h', 'Leb', 'mH'], ['le_h', 'Le', 'mH'],
  ['ke_semi_h', 'Ke', 'mH/√s'], ['rss_ohm', 'Rss', 'Ω'],
] as const;
export const EXTERIOR_SD_FIELD = ['sd_m2', 'Sd', 'cm²'] as const;
export const EXTERIOR_LIMIT_FIELDS = [['power_w', 'Rated power', 'W'], ['xmax_mm', 'Xmax', 'mm']] as const;
export function exteriorDisplayScale(key: string): number {
  if (key === 'sd_m2') return 10000;
  return ['le_h', 'leb_h', 'ke_semi_h', 'volume_m3'].includes(key) ? 1000 : 1;
}

/** Disabled options stay in the editor cache; saved specs regain v1 when possible. */
export function normalizeExteriorOptions(form: ExteriorTransducerForm): ExteriorTransducerForm {
  const { semi_inductance, lumped_sealed_rear_chamber, ...base } = form;
  return { ...base,
    ...(semi_inductance?.enabled ? { semi_inductance } : {}),
    ...(lumped_sealed_rear_chamber?.enabled ? { lumped_sealed_rear_chamber } : {}),
    version: semi_inductance?.enabled || lumped_sealed_rear_chamber?.enabled
      || form.sd_m2 !== undefined || form.power_w !== undefined || form.xmax_mm !== undefined ? 2 : 1 };
}

export type SemiInductanceForm = { enabled: boolean } & Partial<Record<typeof SEMI_INDUCTANCE_FIELDS[number][0], number>>;
export type RearChamberForm = { enabled: boolean; volume_m3?: number };
export type ExteriorKey = typeof EXTERIOR_FIELDS[number][0];
export type ExteriorTransducerForm = Partial<Record<ExteriorKey, number>> & {
  sd_m2?: number; power_w?: number; xmax_mm?: number;
  version: number; motion_axis: [number, number, number];
  semi_inductance?: SemiInductanceForm; lumped_sealed_rear_chamber?: RearChamberForm;
};

export function exteriorTransducerError(form: ExteriorTransducerForm): string | null {
  if (![1, 2].includes(form.version) || (form.version === 1 && (form.semi_inductance || form.lumped_sealed_rear_chamber || form.sd_m2 !== undefined || form.power_w !== undefined || form.xmax_mm !== undefined))) return 'Unsupported exterior driver version. Clear and re-enter the driver.';
  for (const [key, label] of EXTERIOR_FIELDS) {
    const value = form[key];
    if (typeof value !== 'number' || !Number.isFinite(value) || (key === 'le_h' ? value < 0 : value <= 0)) {
      return `Enter ${label.toLowerCase()} in the stated units.`;
    }
  }
  for (const [key, label] of [EXTERIOR_SD_FIELD, ...EXTERIOR_LIMIT_FIELDS]) {
    const value = form[key];
    if (value !== undefined && (!Number.isFinite(value) || value <= 0)) return `Enter positive ${label}.`;
  }
  if (form.motion_axis.length !== 3 || !form.motion_axis.every(Number.isFinite) || !form.motion_axis.some(v => v !== 0)) {
    return 'Enter a finite nonzero motion axis in the returned mesh frame.';
  }
  for (const [model, fields] of [
    [form.semi_inductance, SEMI_INDUCTANCE_FIELDS],
    [form.lumped_sealed_rear_chamber, [['volume_m3', 'Rear chamber volume', 'm³']]],
  ] as const) {
    if (!model) continue;
    if (typeof model.enabled !== 'boolean') return 'Choose whether the driver option is enabled.';
    for (const [key, label] of fields) {
      const value = (model as Record<string, unknown>)[key];
      if ((model.enabled || value !== undefined) && (typeof value !== 'number' || !Number.isFinite(value) || value <= 0)) {
        return `Enter positive ${label} in the stated units.`;
      }
    }
  }
  return null;
}

/** Keep malformed/unknown saved modes visibly blocked rather than dropping physics. */
export function parseExteriorTransducer(value: unknown): ExteriorTransducerForm {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { version: 0, motion_axis: [0, 0, 0] };
  const raw = value as Record<string, unknown>;
  const known = new Set<string>(['sd_m2', 'version', 'motion_axis', 'semi_inductance', 'lumped_sealed_rear_chamber', ...EXTERIOR_FIELDS.map(([key]) => key), ...EXTERIOR_LIMIT_FIELDS.map(([key]) => key)]);
  if (Object.keys(raw).some(key => !known.has(key))) return { version: 0, motion_axis: [0, 0, 0] };
  if ([EXTERIOR_SD_FIELD, ...EXTERIOR_LIMIT_FIELDS].some(([key]) => raw[key] !== undefined && (typeof raw[key] !== 'number' || !Number.isFinite(raw[key])))) return { version: 0, motion_axis: [0, 0, 0] };
  const options: Pick<ExteriorTransducerForm, 'semi_inductance' | 'lumped_sealed_rear_chamber'> = {};
  for (const [name, fields] of [
    ['semi_inductance', SEMI_INDUCTANCE_FIELDS.map(([key]) => key)],
    ['lumped_sealed_rear_chamber', ['volume_m3']],
  ] as const) {
    const model = raw[name];
    if (model === undefined) continue;
    if (!model || typeof model !== 'object' || Array.isArray(model)) return { version: 0, motion_axis: [0, 0, 0] };
    const nested = model as Record<string, unknown>;
    if (Object.keys(nested).some(key => key !== 'enabled' && !new Set<string>(fields).has(key))
      || (nested.enabled !== undefined && typeof nested.enabled !== 'boolean')
      || fields.some(key => nested[key] !== undefined && (typeof nested[key] !== 'number' || !Number.isFinite(nested[key])))) {
      return { version: 0, motion_axis: [0, 0, 0] };
    }
    options[name] = { ...nested, enabled: nested.enabled === true };
  }
  return { ...options, ...Object.fromEntries([...EXTERIOR_FIELDS, EXTERIOR_SD_FIELD, ...EXTERIOR_LIMIT_FIELDS].flatMap(([key]) => typeof raw[key] === 'number' && Number.isFinite(raw[key]) ? [[key, raw[key]]] : [])),
    version: typeof raw.version === 'number' ? raw.version : 0,
    motion_axis: Array.isArray(raw.motion_axis) && raw.motion_axis.length === 3 && raw.motion_axis.every(v => typeof v === 'number' && Number.isFinite(v))
      ? raw.motion_axis as [number, number, number] : [0, 0, 0] };
}

/** Library fields are in datasheet units; Mms must never stand in for bare Mmd. */
export function prefillExteriorDriver(form: ExteriorTransducerForm, values: Record<string, number | undefined>): ExteriorTransducerForm {
  const conversions = [
    ['sd_m2', 'sd_cm2', 10000], ['re_ohm', 're_ohm', 1], ['le_h', 'le_mh', 1000],
    ['bl_n_per_a', 'bl_t_m', 1], ['mmd_kg', 'mmd_g', 1000],
    ['cms_m_per_n', 'cms_m_per_n', 1], ['rms_n_s_per_m', 'rms_kg_per_s', 1],
    ['power_w', 'power_w', 1], ['xmax_mm', 'xmax_mm', 1],
  ] as const;
  const filled = { ...form };
  for (const [key, libraryKey, divisor] of conversions) {
    const value = values[libraryKey];
    if (filled[key] === undefined && value !== undefined && Number.isFinite(value)
      && (key === 'le_h' ? value >= 0 : value > 0)) filled[key] = value / divisor;
  }
  return normalizeExteriorOptions(filled);
}
