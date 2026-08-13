import type { DriverFieldKey } from '../stores/cadReturn';
import type { ParameterTab } from './parameterRegistry';

export const CAD_CONTROL_SECTIONS = {
  linkedDesign: 'Linked design',
  realizedDimensions: 'Realized dimensions',
  frequencySweep: 'Frequency Sweep',
  directivityMap: 'Directivity Map',
  driveChannels: 'Drive channels & drivers',
  crossover: 'Crossover',
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
    ['polar', 'angular step', 'measurement distance', 'normalization angle', 'directivity planes', 'measurement origin', '3D balloon'],
  ),
  driveChannels: control(
    'cad.drive-channels', 'Drive channels & drivers', CAD_CONTROL_SECTIONS.driveChannels, 'simulation',
    ['source assignment', 'channel', 'driver', 'Thiele-Small', 'T/S'],
  ),
  channelAssignment: control(
    'cad.drive-channel.assignment', 'Drive channel', CAD_CONTROL_SECTIONS.driveChannels, 'simulation',
    ['source assignment', 'channel id'], 'ingested-return', 'cad.drive-channels',
  ),
  channelMotion: control(
    'cad.drive-channel.motion', 'Motion', CAD_CONTROL_SECTIONS.driveChannels, 'simulation',
    ['normal motion', 'axial motion', 'velocity'], 'ingested-return', 'cad.drive-channels',
  ),
  driverToggle: control(
    'cad.driver.enabled', 'Driver T/S', CAD_CONTROL_SECTIONS.driveChannels, 'simulation',
    ['Thiele-Small', 'voltage driven', 'driver model'], 'ingested-return', 'cad.drive-channels',
  ),
  driveVoltage: control(
    'cad.driver.voltage', 'Drive voltage', CAD_CONTROL_SECTIONS.driveChannels, 'simulation',
    ['RMS', 'volts', '2.83 V', 'driveVoltageV'], 'ingested-return', 'cad.drive-channels',
  ),
  crossover: control(
    'cad.crossover', 'Crossover', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['combined output', 'LR4', 'sum', 'adjacent bands'],
  ),
  combinedOutput: control(
    'cad.crossover.enabled', 'Combined output (LR4 sum)', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['crossover', 'combine', 'LR4'], 'ingested-return', 'cad.crossover',
  ),
  crossoverFrequency: control(
    'cad.crossover.frequency', 'Crossover frequency', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['Hz', 'band transition', 'combineCrossoversHz'], 'ingested-return', 'cad.crossover',
  ),
  levelMatch: control(
    'cad.crossover.level-match', 'Level match members', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['equalise', 'equalize', 'combineLevelMatch'], 'ingested-return', 'cad.crossover',
  ),
  timeAlign: control(
    'cad.crossover.time-align', 'Time-align members', CAD_CONTROL_SECTIONS.crossover, 'simulation',
    ['phase', 'delay', 'combineAlign'], 'ingested-return', 'cad.crossover',
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

const driverField = (
  driverKey: DriverFieldKey,
  label: string,
  unit: string,
  step: number,
): CadDriverFieldDescriptor => ({
  ...control(
    `cad.driver.${driverKey}`, label, CAD_CONTROL_SECTIONS.driveChannels, 'simulation',
    ['driver', 'T/S', 'Thiele-Small', driverKey], 'ingested-return', 'cad.drive-channels',
  ),
  driverKey,
  unit,
  step,
});

export const CAD_DRIVER_FIELD_CONTROLS: readonly CadDriverFieldDescriptor[] = [
  driverField('sd_cm2', 'Sd', 'cm²', 5),
  driverField('bl_t_m', 'Bl', 'T·m', 0.5),
  driverField('re_ohm', 'Re', 'Ω', 0.1),
  driverField('le_mh', 'Le', 'mH', 0.05),
  driverField('mmd_g', 'Mmd', 'g', 1),
  driverField('cms_m_per_n', 'Cms', 'm/N', 0.0001),
  driverField('rms_kg_per_s', 'Rms', 'kg/s', 0.1),
  driverField('xmax_mm', 'Xmax', 'mm', 0.5),
  driverField('count', 'Count', '', 1),
  driverField('rear_volume_l', 'Rear vol', 'L', 0.5),
];

export const CAD_CONTROL_DESCRIPTORS: readonly CadControlDescriptor[] = [
  ...Object.values(CAD_CONTROLS),
  ...CAD_DRIVER_FIELD_CONTROLS,
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
