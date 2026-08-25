import { DRIVER_FIELD_LABELS, type DriverFieldKey, type PassiveCardioidNumberField } from '../stores/cadReturn';
import type { ParameterTab } from './parameterRegistry';

export const CAD_CONTROL_SECTIONS = {
  linkedDesign: 'Linked design',
  realizedDimensions: 'Realized dimensions',
  frequencySweep: 'Frequency Sweep',
  directivityMap: 'Directivity Map',
  driveChannels: 'Drivers',
  crossover: 'Crossover',
  passiveCardioid: 'Passive cardioid',
  solveOptions: 'Solve options',
  meshDetail: 'Mesh detail',
} as const;

export type CadControlSection = typeof CAD_CONTROL_SECTIONS[keyof typeof CAD_CONTROL_SECTIONS];

export interface CadControlDescriptor {
  /** Stable palette identity; this is deliberately not a design-document path. */
  id: string;
  label: string;
  section: CadControlSection;
  tab: ParameterTab;
  keywords: readonly string[];
  availability: 'cad-mode' | 'ingested-return';
  /** The panel owns the DOM target. The palette only queues this semantic id,
   * so a dock panel that has not mounted yet can still claim and reveal it. */
  reveal: { id: string; target: 'control'; fallbackId?: string };
}

function control(
  id: string,
  label: string,
  section: CadControlSection,
  tab: ParameterTab,
  keywords: readonly string[],
  availability: CadControlDescriptor['availability'] = 'ingested-return',
  fallbackId?: string,
): CadControlDescriptor {
  return { id, label, section, tab, keywords, availability, reveal: { id, target: 'control', fallbackId } };
}

/**
 * Search metadata for rail controls whose values live outside the design
 * document. Keeping these beside, rather than inside, PARAMETER_REGISTRY is the
 * important distinction: none of these ids claims to be a serializable path.
 */
export const CAD_CONTROLS = {
  linkedDesign: control(
    'cad.linked-design', 'Linked design', CAD_CONTROL_SECTIONS.linkedDesign, 'geometry',
    ['Fusion', 'CAD Link', 'connection', 'sync', 'send', 'rebuild'], 'cad-mode',
  ),
  realizedDimensions: control(
    'cad.realized-dimensions', 'Realized dimensions', CAD_CONTROL_SECTIONS.realizedDimensions, 'geometry',
    ['published', 'interface', 'throat diameter', 'mouth width', 'mouth height', 'depth', 'wall thickness', 'enclosure', 'vertical offset'], 'cad-mode',
  ),
  frequencySweep: control(
    'cad.frequency', 'Frequency Sweep', CAD_CONTROL_SECTIONS.frequencySweep, 'simulation',
    ['CAD sweep', 'imported range', 'Hz'],
  ),
  sweepStart: control(
    'cad.frequency.start', 'Sweep start', CAD_CONTROL_SECTIONS.frequencySweep, 'simulation',
    ['CAD sweep', 'frequency start', 'minimum Hz', 'frequencyStartHz'],
  ),
  sweepEnd: control(
    'cad.frequency.end', 'Sweep end', CAD_CONTROL_SECTIONS.frequencySweep, 'simulation',
    ['CAD sweep', 'frequency end', 'maximum Hz', 'frequencyEndHz'],
  ),
  frequencySamples: control(
    'cad.frequency.samples', 'Frequency samples', CAD_CONTROL_SECTIONS.frequencySweep, 'simulation',
    ['CAD sweep', 'frequency count', 'points', 'frequencyCount'],
  ),
  directivityMap: control(
    'cad.directivity', 'Directivity Map', CAD_CONTROL_SECTIONS.directivityMap, 'simulation',
    ['polar', 'angular step', 'measurement distance', 'normalization angle', 'directivity planes', 'measurement origin', '3D balloon', 'field plane'],
  ),
  driveChannels: control(
    'cad.drive-channels', 'Drivers', CAD_CONTROL_SECTIONS.driveChannels, 'simulation',
    // The section lost the solver's own vocabulary from its name, so the
    // keywords have to carry it: "drive channel" is still what the wire, the
    // server refusals and the CAD roles call this.
    ['drive channel', 'drive channels', 'source assignment', 'channel', 'motion', 'driver', 'Thiele-Small', 'T/S'],
  ),
  channelMotion: control(
    'cad.drive-channel.motion', 'Motion', CAD_CONTROL_SECTIONS.driveChannels, 'simulation',
    ['normal motion', 'axial motion', 'velocity'], 'ingested-return', 'cad.drive-channels',
  ),
  driverSearch: control(
    'cad.driver.search', 'Find driver', CAD_CONTROL_SECTIONS.driveChannels, 'simulation',
    ['driver library', 'search drivers', 'preset', 'database', 'brand', 'model', 'compression driver', 'woofer'],
    'ingested-return', 'cad.drive-channels',
  ),
  driverEdit: control(
    'cad.driver.edit', 'Edit T/S', CAD_CONTROL_SECTIONS.driveChannels, 'simulation',
    ['Thiele-Small sheet', 'datasheet', 'override', 'Qes', 'Qts', 'sensitivity', 'impedance variant'],
    'ingested-return', 'cad.drive-channels',
  ),
  driveVoltage: control(
    'cad.driver.voltage', 'Drive voltage', CAD_CONTROL_SECTIONS.driveChannels, 'simulation',
    ['RMS', 'volts', '2.83 V', 'driveVoltageV'], 'ingested-return', 'cad.drive-channels',
  ),
  maxDriveVoltage: control(
    'cad.driver.max-voltage', 'Amplifier limit', CAD_CONTROL_SECTIONS.driveChannels, 'simulation',
    ['amplifier', 'headroom', 'maximum output', 'max SPL', 'clipping', 'maxDriveVoltageV'],
    'ingested-return', 'cad.drive-channels',
  ),
  crossover: control(
    'cad.crossover', 'Crossover', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['combined output', 'LR4', 'sum', 'adjacent bands'],
  ),
  combinedOutput: control(
    'cad.crossover.enabled', 'Combined output', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['crossover', 'combine', 'sum'], 'ingested-return', 'cad.crossover',
  ),
  crossoverFrequency: control(
    'cad.crossover.frequency', 'Crossover frequency', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['Hz', 'band transition', 'combineSpec'], 'ingested-return', 'cad.crossover',
  ),
  crossoverFamily: control(
    'cad.crossover.family', 'Filter family & slope', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['Linkwitz-Riley', 'Butterworth', 'Bessel', 'linear phase', 'LR4', 'BW3', 'order', 'dB/oct'],
    'ingested-return', 'cad.crossover',
  ),
  levelMatch: control(
    'cad.crossover.level-match', 'Level match members', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['equalise', 'equalize', 'gain', 'auto', 'manual'], 'ingested-return', 'cad.crossover',
  ),
  timeAlign: control(
    'cad.crossover.time-align', 'Time-align members', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['phase', 'delay', 'align', 'auto', 'manual'], 'ingested-return', 'cad.crossover',
  ),
  crossoverAdvanced: control(
    'cad.crossover.advanced', 'Advanced crossover (per channel)', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['high-pass', 'low-pass', 'per channel', 'basic', 'advanced view'], 'ingested-return', 'cad.crossover',
  ),
  crossoverReference: control(
    'cad.crossover.reference', 'Alignment reference channel', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['0 ms', 'pinned', 'datum'], 'ingested-return', 'cad.crossover.advanced',
  ),
  crossoverHighPass: control(
    'cad.crossover.high-pass', 'Channel high-pass', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['HP', 'per channel', 'corner'], 'ingested-return', 'cad.crossover.advanced',
  ),
  crossoverLowPass: control(
    'cad.crossover.low-pass', 'Channel low-pass', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['LP', 'per channel', 'corner'], 'ingested-return', 'cad.crossover.advanced',
  ),
  crossoverGain: control(
    'cad.crossover.gain', 'Channel gain', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['dB', 'level', 'trim', 'per channel'], 'ingested-return', 'cad.crossover.advanced',
  ),
  crossoverDelay: control(
    'cad.crossover.delay', 'Channel delay', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['ms', 'offset', 'time', 'per channel'], 'ingested-return', 'cad.crossover.advanced',
  ),
  crossoverInvert: control(
    'cad.crossover.invert', 'Channel polarity', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['invert', 'phase flip', 'per channel'], 'ingested-return', 'cad.crossover.advanced',
  ),
  passiveCardioid: control(
    'cad.passive-cardioid', 'Passive cardioid', CAD_CONTROL_SECTIONS.passiveCardioid, 'simulation',
    ['cardioid', 'rear chamber', 'port', 'foam', 'radiation impedance', 'aperture', 'back radiation'],
  ),
  cardioidEnabled: control(
    'cad.passive-cardioid.enabled', 'Passive-cardioid campaign', CAD_CONTROL_SECTIONS.passiveCardioid, 'simulation',
    ['cardioid', 'chamber', 'enable', 'rear volume'], 'ingested-return', 'cad.passive-cardioid',
  ),
  cardioidPortAreaSource: control(
    'cad.passive-cardioid.port-area-source', 'Port area source', CAD_CONTROL_SECTIONS.passiveCardioid, 'simulation',
    ['provenance', 'user', 'BEM aperture', 'port_area_source'], 'ingested-return', 'cad.passive-cardioid',
  ),
  cardioidInvertPort: control(
    'cad.passive-cardioid.invert-port', 'Invert port', CAD_CONTROL_SECTIONS.passiveCardioid, 'simulation',
    ['rear drive sign', 'polarity', 'passive_cardioid_invert_port'], 'ingested-return', 'cad.passive-cardioid',
  ),
  cardioidCoupled: control(
    'cad.passive-cardioid.coupled', 'Coupled', CAD_CONTROL_SECTIONS.passiveCardioid, 'simulation',
    ['derived channel', 'cone excursion', 'passive_cardioid_coupled'], 'ingested-return', 'cad.passive-cardioid',
  ),
  solveOptions: control(
    'cad.solve-options', 'Solve options', CAD_CONTROL_SECTIONS.solveOptions, 'simulation',
    ['Metal', 'full 3-D', 'cut planes', 'mesh validation', 'sweep points', 'sweep spacing', 'explicit list', 'verbose logging'],
  ),
  meshDetail: control(
    'cad.mesh-detail', 'Mesh detail', CAD_CONTROL_SECTIONS.meshDetail, 'simulation',
    ['CAD mesh', 'surface sizing', 'rebuild mesh', 'ingest'],
  ),
  rigidSize: control(
    'cad.mesh.rigid-size', 'Cabinet & waveguide', CAD_CONTROL_SECTIONS.meshDetail, 'simulation',
    ['rigid CAD surfaces', 'mesh size', 'rigidSizeMm'], 'ingested-return', 'cad.mesh-detail',
  ),
  transitionSize: control(
    'cad.mesh.transition', 'Size transition', CAD_CONTROL_SECTIONS.meshDetail, 'simulation',
    ['mesh regions', 'transitionMm'], 'ingested-return', 'cad.mesh-detail',
  ),
  exteriorOnly: control(
    'cad.mesh.exterior-only', 'Exterior-only Phase 2 solve', CAD_CONTROL_SECTIONS.meshDetail, 'simulation',
    ['FEM air volumes', 'free space', 'exteriorOnly'], 'ingested-return', 'cad.mesh-detail',
  ),
  sourceSize: control(
    'cad.mesh.source-size', 'Source mesh size', CAD_CONTROL_SECTIONS.meshDetail, 'simulation',
    ['HF source', 'MF source', 'LF source', 'suggested resolution', 'sourceSizesMm'], 'ingested-return', 'cad.mesh-detail',
  ),
  skipSource: control(
    'cad.mesh.skip-source', 'Skip optional source', CAD_CONTROL_SECTIONS.meshDetail, 'simulation',
    ['exclude source', 'skippedSourceIds'], 'ingested-return', 'cad.mesh-detail',
  ),
  areaDrift: control(
    'cad.mesh.area-drift', 'Allow recorded area drift', CAD_CONTROL_SECTIONS.meshDetail, 'simulation',
    ['source area mismatch', 'override', 'areaDriftOverrides'], 'ingested-return', 'cad.mesh-detail',
  ),
} as const satisfies Record<string, CadControlDescriptor>;

export interface CadDriverFieldDescriptor extends CadControlDescriptor {
  driverKey: DriverFieldKey;
  unit: string;
  step: number;
}

/** The label is `DRIVER_FIELD_LABELS`', so a field is named the same here, in
 * the shortfall hint, and in the solve gate's refusal. */
const driverField = (
  driverKey: DriverFieldKey,
  unit: string,
  step: number,
): CadDriverFieldDescriptor => ({
  ...control(
    `cad.driver.${driverKey}`, DRIVER_FIELD_LABELS[driverKey], CAD_CONTROL_SECTIONS.driveChannels, 'simulation',
    ['driver', 'T/S', 'Thiele-Small', driverKey], 'ingested-return', 'cad.drive-channels',
  ),
  driverKey,
  unit,
  step,
});

export const CAD_DRIVER_FIELD_CONTROLS: readonly CadDriverFieldDescriptor[] = [
  driverField('sd_cm2', 'cm²', 5),
  driverField('bl_t_m', 'T·m', 0.5),
  driverField('re_ohm', 'Ω', 0.1),
  driverField('le_mh', 'mH', 0.05),
  driverField('mmd_g', 'g', 1),
  driverField('mms_g', 'g', 1),
  driverField('cms_m_per_n', 'm/N', 0.0001),
  driverField('vas_l', 'L', 1),
  driverField('fs_hz', 'Hz', 5),
  driverField('qms', '', 0.1),
  driverField('rms_kg_per_s', 'kg/s', 0.1),
  driverField('xmax_mm', 'mm', 0.5),
  driverField('power_w', 'W', 10),
  driverField('z_nom_ohm', 'Ω', 1),
  driverField('count', '', 1),
  driverField('rear_volume_l', 'L', 0.5),
];

/**
 * The datasheet fields the *Edit T/S* sheet offers, in reading order.
 *
 * Mmd and Cms are deliberately absent: a picked driver states Mms, Fs and Vas,
 * and offering their substitutes beside them invites a spec carrying two
 * masses, which the server refuses outright. They remain available in the
 * manual grid, which is what a hand-entered driver uses.
 */
export const CAD_DRIVER_SHEET_FIELDS: readonly CadDriverFieldDescriptor[] = [
  'sd_cm2', 'bl_t_m', 're_ohm', 'le_mh', 'mms_g', 'fs_hz', 'vas_l', 'qms', 'xmax_mm',
  'power_w', 'z_nom_ohm', 'count', 'rear_volume_l',
].map((key) => CAD_DRIVER_FIELD_CONTROLS.find((control) => control.driverKey === key)!);

export interface CadCardioidFieldDescriptor extends CadControlDescriptor {
  formKey: PassiveCardioidNumberField;
  /** The unit the rail shows. Areas are typed in cm² and stored in m². */
  unit: string;
  step: number;
  /** Displayed value = stored wire value × this. */
  displayScale: number;
  /** Exclusive when the server's bound is `gt`, inclusive when it is `ge`. */
  minimum: { value: number; exclusive: boolean };
  help: string;
}

const cardioidField = (
  formKey: PassiveCardioidNumberField,
  label: string,
  unit: string,
  step: number,
  displayScale: number,
  minimum: { value: number; exclusive: boolean },
  help: string,
  keywords: readonly string[],
): CadCardioidFieldDescriptor => ({
  ...control(
    `cad.passive-cardioid.${formKey}`, label, CAD_CONTROL_SECTIONS.passiveCardioid, 'simulation',
    ['cardioid', ...keywords], 'ingested-return', 'cad.passive-cardioid',
  ),
  formKey,
  unit,
  step,
  displayScale,
  minimum,
  help,
});

/**
 * The five numeric campaign inputs.
 *
 * The two port areas are separate entries deliberately: one drives the
 * chamber/port physics and the other records the aperture the BEM matrix was
 * solved over, and resolving both from a single "port area" box is a measured
 * ~40% error on the volume-velocity ratio. There is no compliance entry —
 * `chamber_compliance_m3_per_pa` is derived from the rear volume, not typed.
 */
export const CAD_CARDIOID_FIELD_CONTROLS: readonly CadCardioidFieldDescriptor[] = [
  cardioidField(
    'rearVolumeL', 'Rear volume', 'L', 0.5, 1, { value: 0, exclusive: true },
    'Sealed volume behind the MF diaphragm. This is a volume; the chamber compliance in the run summary is derived from it as V/(rho c²), never typed here. Setting it is what turns the campaign on.',
    ['rear volume', 'chamber', 'litres', 'passive_cardioid_rear_volume_l'],
  ),
  cardioidField(
    'portLengthMm', 'Port length', 'mm', 1, 1, { value: 0, exclusive: false },
    'Acoustic length of the port duct between the rear chamber and the port exit.',
    ['port length', 'duct', 'passive_cardioid_port_length_mm'],
  ),
  cardioidField(
    'modelPortAreaM2', 'Physical port area', 'cm²', 10, 1e4, { value: 0, exclusive: true },
    'The port area the chamber/port physics uses. This is yours to state and is NOT the meshed aperture area: substituting the geometric area for it shifts the volume-velocity ratio by about 40%.',
    ['model port area', 'physical area', 'model_port_area_m2'],
  ),
  cardioidField(
    'bemPortAreaM2', 'BEM port area', 'cm²', 1, 1e4, { value: 0, exclusive: true },
    'Geometric area of the PORT_EXIT faces in the mesh, which the radiation-impedance matrix is solved over. The solve refuses if it disagrees with the matrix it actually computed.',
    ['BEM port area', 'aperture area', 'bem_port_area_m2'],
  ),
  cardioidField(
    'foamResistancePaSM3', 'Foam resistance', 'Pa·s/m³', 1000, 1, { value: 0, exclusive: false },
    'Acoustic resistance of the damping material in the port. Zero is an undamped port.',
    ['foam', 'damping', 'resistance', 'passive_cardioid_foam_resistance_pa_s_m3'],
  ),
];

/**
 * The number to show for a stored value under a unit scale.
 *
 * Naive multiplication is not enough: 0.00037 m² × 1e4 is 3.6999999999999997,
 * so a field the user typed 3.7 into would redraw as binary noise the moment
 * anything else in the section re-rendered. Rounding blindly is not enough
 * either — 12 significant digits would drop real precision from a BEM aperture
 * area. So this takes the shortest rounding that still converts back to the
 * exact stored value, which reproduces what the user typed when they typed it
 * and preserves every digit of a measured area when they did not.
 */
export function cadDisplayValue(stored: number, scale: number): number {
  const scaled = stored * scale;
  for (let digits = 12; digits < 17; digits += 1) {
    const candidate = Number(scaled.toPrecision(digits));
    if (candidate / scale === stored) return candidate;
  }
  return scaled;
}

export const CAD_CONTROL_DESCRIPTORS: readonly CadControlDescriptor[] = [
  ...Object.values(CAD_CONTROLS),
  ...CAD_DRIVER_FIELD_CONTROLS,
  ...CAD_CARDIOID_FIELD_CONTROLS,
];

export function cadControlIsAvailable(descriptor: CadControlDescriptor, cadReturnReady: boolean): boolean {
  return descriptor.availability === 'cad-mode' || cadReturnReady;
}

export function cadControlMatchesQuery(descriptor: CadControlDescriptor, query: string): boolean {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return true;
  const searchable = [descriptor.label, descriptor.section, descriptor.id, ...descriptor.keywords]
    .join(' ').toLocaleLowerCase();
  // CAD vocabulary is naturally compound ("driver Sd", "mesh transition").
  // Requiring the words to be adjacent would make the explicit keywords less
  // useful than the palette's ordinary label/detail/keyword search.
  return normalized.split(/\s+/).every((token) => searchable.includes(token));
}
