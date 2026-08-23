import type { Preferences } from '../prefs/preferences';
import { familyOrders, isFilterFamily, type FilterFamily } from './crossoverSpec';
import { buildOnAxisFrd } from './frd';
import { normalizePhaseConvention, phaseSpatialSign, wrapPhaseDegrees } from './phaseConvention';
import { resultChannelFileSuffix, resultChannels, scopeResultChannel, type ResultPayload } from './types';

export interface VituixCadTextFile {
  filename: string;
  text: string;
  type: string;
}

function assertUniquePortableFilenames(files: readonly VituixCadTextFile[]): void {
  const portable = new Set<string>();
  files.forEach(({ filename }) => {
    const key = filename.normalize('NFKC').toLocaleLowerCase();
    if (portable.has(key)) {
      throw new Error(`VituixCAD project export refused: ${filename} aliases another bundle member on a portable filesystem.`);
    }
    portable.add(key);
  });
}

interface ProjectDriver {
  id: string;
  model: string;
  frdFilename: string;
  zmaFilename: string;
}

/** One active block in the exported schematic: VituixCAD's own Shape and Order
 * plus the corner it sits at. */
interface ActiveFilterSection {
  kind: 'hp' | 'lp';
  shape: string;
  order: number;
  frequencyHz: number;
}

interface ActiveProjectSpec {
  members: string[];
  chains: Record<string, ActiveFilterSection[]>;
  gainsDb: Record<string, number>;
  delaysMs: Record<string, number>;
  inverted: Record<string, boolean>;
  /** Whether any section was a linear-phase one. VituixCAD has no such shape,
   * so it is exported as the Linkwitz-Riley of the same order and the project
   * description says so — a silent substitution would be a different filter. */
  linearPhase: boolean;
}

/** VituixCAD's Shape vocabulary for each family this app offers. */
const VITUIXCAD_SHAPES: Record<FilterFamily, string> = {
  lr: 'Linkwitz-Riley',
  butterworth: 'Butterworth',
  bessel: 'Bessel',
  linear_phase: 'Linkwitz-Riley',
};

interface XmlNode {
  tag: string;
  attributes?: Record<string, string>;
  text?: string;
  children: XmlNode[];
}

const DELIMITER = '\t';
const ENGINEERING_IMPEDANCE_CONVENTIONS = new Set([
  'engineering-exp-plus-jwt', 'engineering-exp(+jwt)', 'exp(+jwt)', 'e(+jwt)', '+jwt',
]);

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function fixed(value: number, precision: number): string {
  const formatted = value.toFixed(precision);
  if (!/[eE]/.test(formatted)) return formatted;
  const [coefficient, exponentText] = value.toExponential(precision).split('e');
  const sign = coefficient.startsWith('-') ? '-' : '';
  const digits = coefficient.replace('-', '').replace('.', '');
  const exponent = Number(exponentText);
  return `${sign}${digits}${'0'.repeat(Math.max(0, exponent - precision))}.${'0'.repeat(precision)}`;
}

function electricalImpedance(result: ResultPayload): NonNullable<ResultPayload['impedance']> {
  // This gate is the safety contract, not merely a units label for the header:
  // an un-driven channel's impedance arrays are unit-acceleration acoustics.
  // Their numbers can look perfectly plausible as ohms, so shape inspection or
  // a conversion factor would manufacture a crossover curve without warning.
  if (result.metadata?.impedance_units !== 'ohms') {
    throw new Error(
      'ZMA export refused: this channel is not tagged with impedance_units "ohms". '
      + 'Only a channel with a driver model has electrical input impedance; unit-drive acoustic impedance cannot be exported as ZMA.',
    );
  }
  if (result.metadata?.impedance_quantity !== 'electrical_input_impedance') {
    throw new Error(
      'ZMA export refused: this channel is not tagged with impedance_quantity "electrical_input_impedance". '
      + 'Ohmic units alone do not prove the impedance quantity or its phase convention.',
    );
  }
  if (!result.impedance) {
    throw new Error('ZMA export refused: the driver-modelled channel has no electrical impedance samples.');
  }
  return result.impedance;
}

export function hasElectricalImpedance(result: ResultPayload): boolean {
  return result.metadata?.impedance_units === 'ohms'
    && result.metadata?.impedance_quantity === 'electrical_input_impedance'
    && result.impedance !== undefined;
}

function impedancePhaseRule(result: ResultPayload): { factor: 1 | -1; note: string } {
  const explicit = result.metadata?.impedance_phase_convention;
  const normalized = normalizePhaseConvention(explicit);
  if (ENGINEERING_IMPEDANCE_CONVENTIONS.has(normalized)) {
    return { factor: 1, note: `engineering exp(+jωt), from impedance tag ${String(explicit)}` };
  }
  if (explicit != null && String(explicit).trim()) {
    const spatialSign = phaseSpatialSign(explicit);
    if (spatialSign !== null) {
      // Raw exp(+ikr) phasors are the conjugate of engineering +jωt; the
      // opposite spatial sign is already engineering-oriented. This shares
      // FRD's tag resolver so additions to the accepted vocabulary affect both.
      return {
        factor: -spatialSign as 1 | -1,
        note: `converted to engineering exp(+jωt) from impedance tag ${String(explicit)}`,
      };
    }
    throw new Error(`ZMA export refused: unsupported impedance phase convention ${String(explicit)}.`);
  }

  // electricalImpedance has already required both the ohms unit and hornlab_sim's
  // engineering-domain electrical_input_impedance quantity. Only that exact
  // legacy contract makes a missing phase tag unambiguous; ohms alone never does.
  return {
    factor: 1,
    note: 'engineering exp(+jωt), inferred from HornLab electrical-ohms contract (impedance tag absent)',
  };
}

/** Build VituixCAD frequency / magnitude-ohms / engineering-phase data. */
export function buildZma(result: ResultPayload, channelId?: string): string {
  result = scopeResultChannel(result, channelId);
  const impedance = electricalImpedance(result);
  const phaseRule = impedancePhaseRule(result);
  const frequencies = impedance.frequencies?.length ? impedance.frequencies : result.frequencies;
  const rows = frequencies.flatMap((frequency, index) => {
    const real = impedance.real?.[index];
    const imaginary = impedance.imaginary?.[index];
    if (!finite(frequency) || !finite(real) || !finite(imaginary)) return [];
    const magnitude = Math.hypot(real, imaginary);
    const phase = wrapPhaseDegrees(phaseRule.factor * Math.atan2(imaginary, real) * 180 / Math.PI);
    return [{ frequency, line: [
      fixed(frequency, 6), fixed(magnitude, 6), fixed(phase, 4),
    ].join(DELIMITER) }];
  }).sort((left, right) => left.frequency - right.frequency);
  if (!rows.length) throw new Error('ZMA export refused: the electrical impedance curve has no complete finite samples.');
  return `${[
    '* HornLab electrical input impedance',
    `* Phase convention: ${phaseRule.note}`,
    `* ${['Freq(Hz)', 'Magnitude(ohms)', 'Phase(degrees)'].join(DELIMITER)}`,
    ...rows.map(({ line }) => line),
  ].join('\n')}\n`;
}

function xml(tag: string, text?: string | number, attributes?: Record<string, string>): XmlNode {
  return { tag, ...(attributes ? { attributes } : {}), ...(text !== undefined ? { text: String(text) } : {}), children: [] };
}

function add(parent: XmlNode, tag: string, value?: string | number): XmlNode {
  const child = xml(tag, value);
  parent.children.push(child);
  return child;
}

function escapeXml(value: string): string {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&apos;');
}

function renderXml(node: XmlNode, depth = 0): string {
  const indent = '  '.repeat(depth);
  const attributes = Object.entries(node.attributes ?? {})
    .map(([name, value]) => ` ${name}="${escapeXml(value)}"`).join('');
  if (!node.children.length && node.text === undefined) return `${indent}<${node.tag}${attributes} />`;
  if (!node.children.length) return `${indent}<${node.tag}${attributes}>${escapeXml(node.text ?? '')}</${node.tag}>`;
  return `${indent}<${node.tag}${attributes}>\n${node.children.map((child) => renderXml(child, depth + 1)).join('\n')}\n${indent}</${node.tag}>`;
}

function vxpNumber(value: number): string {
  const normalized = Math.abs(value) < 5e-13 ? 0 : value;
  return Number(normalized.toPrecision(12)).toString();
}

function addTarget(parent: XmlNode, tag: string, spl: number): void {
  const target = add(parent, tag);
  add(target, 'FreqMin', '20.0');
  add(target, 'FreqMax', '20000.0');
  add(target, 'SPL', spl.toFixed(1));
  add(target, 'Tilt', '0.0');
  add(target, 'DrvN', 1);
  add(target, 'Invert', 'False');
  add(target, 'FreeLF', 'False');
  add(target, 'FreeHF', 'False');
}

function addParameter(
  parent: XmlNode,
  index: number,
  name: string,
  value: number,
  unit: string,
  minimum: number,
  maximum: number,
): void {
  const parameter = xml('PARAM', undefined, { pi: String(index) });
  parent.children.push(parameter);
  add(parameter, 'Name', name);
  add(parameter, 'Value', vxpNumber(value));
  add(parameter, 'Unit', unit);
  add(parameter, 'Optimize', 'False');
  add(parameter, 'Expression');
  add(parameter, 'Min', vxpNumber(minimum));
  add(parameter, 'Max', vxpNumber(maximum));
  add(parameter, 'OptiBlock', 'False');
}

function addWirePoints(parent: XmlNode, points: Array<[number, number]>): void {
  points.forEach(([x, y], index) => {
    const wire = xml('WIRE', undefined, { wi: String(index) });
    parent.children.push(wire);
    add(wire, 'X', x);
    add(wire, 'Y', y);
  });
}

function addPart(parent: XmlNode, index: number, type: string, centerX: number, centerY: number): XmlNode {
  const part = xml('PART', undefined, { xi: String(index) });
  parent.children.push(part);
  add(part, 'Type', type);
  add(part, 'CenX', centerX);
  add(part, 'CenY', centerY);
  return part;
}

function addProjectDefaults(root: XmlNode, active: ActiveProjectSpec | null): void {
  add(root, 'Description', active
    ? `HornLab active crossover project${active.linearPhase
      ? ' — linear-phase sections exported as Linkwitz-Riley of the same order; VituixCAD has no zero-phase shape'
      : ''}`
    : 'HornLab VituixCAD project');
  [
    ['ReferenceAngle', 0], ['DualPlane', 'False'], ['KeywordHor', 'hor'], ['KeywordVer', 'ver'],
    ['AngleMultiplier', 1], ['XMin', 20], ['XMax', 20000], ['Interpolate', 'True'],
    ['UserAnglesHor', undefined], ['UserAnglesVer', undefined], ['IntensitySphere', 'True'],
    ['IntensityCylinder', 'False'], ['IncludeHor', 'True'], ['IncludeVer', 'True'],
    ['HalfSpace', 'False'], ['Corner', 'False'], ['LiswinDI', 'True'],
    ['CTA2034Aweights', 'True'], ['AngleStep', 10], ['FrontWall', 'False'],
    ['FrontWallZ', 1000], ['LeftWall', 'False'], ['LeftWallX', -1000],
    ['Ceiling', 'False'], ['CeilingY', 1500], ['Floor', 'False'], ['FloorY', -1000],
    ['Toein', 25], ['AbsorpWall', 2], ['AbsorpCeil', 2], ['AbsorpFloor', 2],
    ['ReferDistance', 2000], ['PlaneRotation', 0], ['DrvOffsetX', 0], ['DrvOffsetY', 0],
  ].forEach(([tag, value]) => add(root, String(tag), value as string | number | undefined));
  addTarget(root, 'AxialTarget', 85);
  addTarget(root, 'PowerTarget', 85);
}

function addDriverHeader(root: XmlNode, driver: ProjectDriver, index: number): void {
  const node = xml('DRIVER', undefined, { di: String(index) });
  root.children.push(node);
  add(node, 'Model', driver.model);
  add(node, 'SPL', 80);
  add(node, 'Z', 8);
  add(node, 'ExtendedData', 'False');
  add(node, 'ResponseDirectory', '.');
  add(node, 'ResponseScale', 1);
  add(node, 'ResponseDelay', 0);
  add(node, 'ResponseInvert', 'False');
  add(node, 'ResponseMute', 'False');
  add(node, 'MinimumPhase', 'False');
  add(node, 'ResponseSmooth', 'None');
  add(node, 'ImpedanceFile', driver.zmaFilename);
  add(node, 'ImpedanceScale', 1);
  const response = xml('RESPONSE', undefined, { ri: '0' });
  node.children.push(response);
  add(response, 'FileName', driver.frdFilename);
  add(response, 'Hor', 0);
  add(response, 'Ver', 0);
}

function addGenerator(crossover: XmlNode, partIndex: number): void {
  const generator = addPart(crossover, partIndex, 'Generator', 3, 9);
  add(generator, 'PartID', 'G1');
  add(generator, 'GUID');
  addParameter(generator, 0, 'Eg', 2.83, 'V', 0.01, 400);
  addParameter(generator, 1, 'Tg', 0, 'us', -50000, 50000);
  addParameter(generator, 2, 'Rg', 0.001, 'Ω', 0.001, 1000);
  addWirePoints(generator, [[3, 6], [3, 12]]);
}

function addGround(crossover: XmlNode, partIndex: number, x: number, y: number): void {
  const ground = addPart(crossover, partIndex, 'Ground', x, y + 1);
  add(ground, 'Open', 'False');
  add(ground, 'Rotated', 'False');
  add(ground, 'GUID');
  addWirePoints(ground, [[x, y]]);
}

function addWire(crossover: XmlNode, partIndex: number, points: Array<[number, number]>): void {
  const centerX = Math.round(points.reduce((sum, [x]) => sum + x, 0) / points.length);
  const centerY = Math.round(points.reduce((sum, [, y]) => sum + y, 0) / points.length);
  const wire = addPart(crossover, partIndex, 'Wire', centerX, centerY);
  add(wire, 'Open', 'False');
  add(wire, 'GUID');
  addWirePoints(wire, points);
}

function addActiveFilter(
  crossover: XmlNode,
  partIndex: number,
  section: ActiveFilterSection,
  centerX: number,
  signalY: number,
  unitId: number,
): void {
  const { kind, frequencyHz } = section;
  const filter = addPart(crossover, partIndex, kind === 'hp' ? 'Active High pass' : 'Active Low pass', centerX, signalY + 1);
  add(filter, 'Shape', section.shape);
  add(filter, 'Order', section.order);
  add(filter, 'Open', 'False');
  add(filter, 'Shorted', 'False');
  add(filter, 'PartID', `U${unitId}`);
  add(filter, 'GUID');
  addParameter(filter, 0, 'f', frequencyHz, 'Hz', 5, 40000);
  addWirePoints(filter, [[centerX - 3, signalY], [centerX - 3, signalY + 2], [centerX + 3, signalY], [centerX + 3, signalY + 2]]);
}

function addBuffer(
  crossover: XmlNode,
  partIndex: number,
  centerX: number,
  signalY: number,
  gainDb: number,
  delayMs: number,
  inverted: boolean,
  bufferId: number,
): void {
  const buffer = addPart(crossover, partIndex, 'Buffer', centerX, signalY + 1);
  add(buffer, 'Shape');
  add(buffer, 'Open', 'False');
  add(buffer, 'Shorted', 'False');
  add(buffer, 'Inverted', inverted ? 'True' : 'False');
  add(buffer, 'PartID', `A${bufferId}`);
  add(buffer, 'GUID');
  addParameter(buffer, 0, 'A', gainDb, 'dB', -100, 100);
  addParameter(buffer, 1, 'dt', delayMs * 1000, 'us', -50000, 50000);
  addWirePoints(buffer, [[centerX - 3, signalY], [centerX - 3, signalY + 2], [centerX + 3, signalY], [centerX + 3, signalY + 2]]);
}

function addDriverPart(crossover: XmlNode, partIndex: number, driver: ProjectDriver, centerX: number, centerY: number, driverId: number): void {
  const part = addPart(crossover, partIndex, 'Driver', centerX, centerY);
  add(part, 'Model', driver.model);
  add(part, 'Open', 'False');
  add(part, 'Shorted', 'False');
  add(part, 'Muted', 'False');
  add(part, 'Hidden', 'False');
  add(part, 'Inverted', 'False');
  add(part, 'PartID', `D${driverId}`);
  add(part, 'GUID');
  addTarget(part, 'DriverTarget', 85);
  addTarget(part, 'FilterTarget', 0);
  addParameter(part, 0, 'X', 0, 'mm', -2000, 2000);
  addParameter(part, 1, 'Y', 0, 'mm', -5000, 5000);
  addParameter(part, 2, 'Z', 0, 'mm', -2000, 2000);
  addParameter(part, 3, 'R', 0, 'deg', -180, 180);
  addParameter(part, 4, 'T', 0, 'deg', -180, 180);
  addWirePoints(part, [[centerX - 1, centerY - 3], [centerX - 1, centerY + 3]]);
}

function numberOf(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** One channel's active blocks, or null when a section names a family/order
 * this app does not evaluate — which would export a filter nobody solved. */
function chainOf(channel: Record<string, unknown> | undefined): ActiveFilterSection[] | null {
  const sections: ActiveFilterSection[] = [];
  for (const kind of ['hp', 'lp'] as const) {
    const raw = channel?.[kind];
    if (raw === null || raw === undefined) continue;
    if (typeof raw !== 'object') return null;
    const record = raw as Record<string, unknown>;
    const family = record.family;
    const order = numberOf(record.order);
    const frequencyHz = numberOf(record.fc_hz);
    if (!isFilterFamily(family) || order === null || frequencyHz === null) return null;
    if (!familyOrders(family).includes(order)) return null;
    sections.push({ kind, shape: VITUIXCAD_SHAPES[family], order, frequencyHz });
  }
  return sections;
}

/** The LR4 chain a payload from before the filter library describes. */
function legacyChains(members: string[], crossoversHz: number[]): Record<string, ActiveFilterSection[]> {
  return Object.fromEntries(members.map((member, index) => {
    const sections: ActiveFilterSection[] = [];
    if (index > 0) sections.push({ kind: 'hp', shape: 'Linkwitz-Riley', order: 4, frequencyHz: crossoversHz[index - 1] });
    if (index < crossoversHz.length) sections.push({ kind: 'lp', shape: 'Linkwitz-Riley', order: 4, frequencyHz: crossoversHz[index] });
    return [member, sections];
  }));
}

function activeProjectSpec(result: ResultPayload, eligibleIds: string[]): ActiveProjectSpec | null {
  const eligible = new Set(eligibleIds);
  for (const { result: channel } of resultChannels(result)) {
    const combine = channel.metadata?.combine;
    if (!combine || typeof combine !== 'object') continue;
    const record = combine as Record<string, unknown>;
    // 'lr4_time_aligned_sum' is the pre-filter-library name of the same
    // payload; results solved before that rename still carry it.
    if (record.type !== 'filtered_time_aligned_sum' && record.type !== 'lr4_time_aligned_sum') continue;
    const members = Array.isArray(record.members) ? record.members.map(String) : [];
    if (members.length !== eligible.size || members.some((id) => !eligible.has(id))) continue;
    const resolved = record.channels && typeof record.channels === 'object'
      ? record.channels as Record<string, unknown>
      : null;
    let chains: Record<string, ActiveFilterSection[]> | null = null;
    let linearPhase = false;
    if (resolved && members.every((id) => resolved[id] && typeof resolved[id] === 'object')) {
      const built = members.map((id) => chainOf(resolved[id] as Record<string, unknown>));
      if (built.some((sections) => sections === null)) continue;
      chains = Object.fromEntries(members.map((id, index) => [id, built[index]!]));
      linearPhase = members.some((id) => {
        const entry = resolved[id] as Record<string, unknown>;
        return [entry.hp, entry.lp].some((section) => (
          section && typeof section === 'object'
          && (section as Record<string, unknown>).family === 'linear_phase'
        ));
      });
    } else {
      // An unlinked pair reports a null crossover, which this LR4-shaped
      // fallback cannot represent; Number(null) would write it as 0 Hz.
      const crossoversHz = Array.isArray(record.crossovers_hz)
        ? record.crossovers_hz.map((value) => (value === null ? Number.NaN : Number(value))) : [];
      if (crossoversHz.length !== members.length - 1 || crossoversHz.some((value) => !Number.isFinite(value))) continue;
      chains = legacyChains(members, crossoversHz);
    }
    const levelMatch = record.level_match && typeof record.level_match === 'object'
      ? record.level_match as Record<string, unknown> : {};
    const gains = levelMatch.gains_db && typeof levelMatch.gains_db === 'object'
      ? levelMatch.gains_db as Record<string, unknown> : {};
    const delays = record.delays_ms && typeof record.delays_ms === 'object'
      ? record.delays_ms as Record<string, unknown> : {};
    const channelValue = (id: string, key: string): unknown => (
      resolved && resolved[id] && typeof resolved[id] === 'object'
        ? (resolved[id] as Record<string, unknown>)[key]
        : undefined
    );
    return {
      members,
      chains,
      gainsDb: Object.fromEntries(members.map((id) => [
        id, numberOf(channelValue(id, 'gain_db')) ?? numberOf(Number(gains[id])) ?? 0,
      ])),
      delaysMs: Object.fromEntries(members.map((id) => [
        id, numberOf(channelValue(id, 'delay_ms')) ?? numberOf(Number(delays[id])) ?? 0,
      ])),
      inverted: Object.fromEntries(members.map((id) => [id, channelValue(id, 'inverted') === true])),
      linearPhase,
    };
  }
  return null;
}

/**
 * Version-2 VXP schema ported from the Fusion add-in's exercised writer.
 * Keeping the complete schematic fields matters: VituixCAD treats this as a
 * project graph, not as a loose XML manifest of response filenames.
 */
export function buildVxp(drivers: ProjectDriver[], activeSpec: ActiveProjectSpec | null): string {
  const root = xml('SPEAKER');
  addProjectDefaults(root, activeSpec);
  drivers.forEach((driver, index) => addDriverHeader(root, driver, index));
  add(root, 'Variant', 0);
  const crossover = add(root, 'CROSSOVER');
  add(crossover, 'DSP', 'Analog');
  add(crossover, 'SampleRate', 96000);
  add(crossover, 'DSPSettings');
  add(crossover, 'DSPTemplate');

  let partIndex = 0;
  addGenerator(crossover, partIndex++);
  addGround(crossover, partIndex++, 3, 12);
  let unitId = 1;
  let bufferId = 1;
  [...drivers].reverse().forEach((driver, rowIndex) => {
    const memberIndex = drivers.indexOf(driver);
    const signalY = 6 + rowIndex * 14;
    let currentX = 10;
    addWire(crossover, partIndex++, signalY === 6
      ? [[3, 6], [currentX, signalY]]
      : [[3, 6], [6, 6], [6, signalY], [currentX, signalY]]);
    if (activeSpec) {
      (activeSpec.chains[driver.id] ?? []).forEach((section) => {
        addActiveFilter(crossover, partIndex++, section, currentX + 3, signalY, unitId++);
        currentX += 6;
      });
      addBuffer(
        crossover, partIndex++, currentX + 3, signalY,
        activeSpec.gainsDb[driver.id] ?? 0, activeSpec.delaysMs[driver.id] ?? 0,
        activeSpec.inverted[driver.id] === true, bufferId++,
      );
      currentX += 6;
    }
    const driverPinX = currentX + 8;
    addWire(crossover, partIndex++, [[currentX, signalY], [driverPinX, signalY]]);
    addDriverPart(crossover, partIndex++, driver, driverPinX + 1, signalY + 3, memberIndex + 1);
    addGround(crossover, partIndex++, driverPinX, signalY + 6);
  });

  return `${[
    '<?xml version="1.0" encoding="utf-8"?>',
    '<!--VituixCAD PROJECT-->',
    '<!--Version 2-->',
    renderXml(root),
  ].join('\n')}\n`;
}

/** Build one self-contained project bundle: VXP plus every referenced FRD/ZMA. */
export function buildVituixCadProjectFiles(
  result: ResultPayload,
  preferences: Pick<Preferences, 'smoothing'>,
  jobStem: string,
): VituixCadTextFile[] {
  const wrappedChannels = resultChannels(result);
  const candidates = wrappedChannels.length
    ? wrappedChannels.map(({ id, result: channel }) => ({ id, channelId: id as string | undefined, result: channel }))
    : [{
      id: String(result.metadata?.drive_channel_id ?? jobStem),
      channelId: undefined,
      result,
    }];
  const eligible = candidates.filter(({ result: channel }) => hasElectricalImpedance(channel));
  if (!eligible.length) {
    throw new Error(
      'VituixCAD project export refused: no channel has driver-modelled electrical impedance tagged with impedance_units "ohms" and impedance_quantity "electrical_input_impedance".',
    );
  }

  const drivers: ProjectDriver[] = eligible.map(({ id, channelId }) => {
    const baseName = `${jobStem}${resultChannelFileSuffix(result, channelId)}`;
    return { id, model: id, frdFilename: `${baseName}.frd`, zmaFilename: `${baseName}.zma` };
  });
  const active = wrappedChannels.length ? activeProjectSpec(result, drivers.map(({ id }) => id)) : null;
  const orderedDrivers = active
    ? active.members.map((id) => drivers.find((driver) => driver.id === id)!)
    : drivers;

  const files: VituixCadTextFile[] = [];
  eligible.forEach(({ channelId }, index) => {
    const driver = drivers[index];
    files.push({
      filename: driver.frdFilename,
      text: buildOnAxisFrd(result, preferences, channelId),
      type: 'text/plain;charset=utf-8',
    }, {
      filename: driver.zmaFilename,
      text: buildZma(result, channelId),
      type: 'text/plain;charset=utf-8',
    });
  });
  files.push({
    filename: `${jobStem}.vxp`,
    text: buildVxp(orderedDrivers, active),
    type: 'application/xml;charset=utf-8',
  });
  // Preflight before runExportFormat writes the first dependency. The suffix
  // allocator should make this tautological; retaining the bundle-level guard
  // ensures a future filename family cannot reintroduce silent overwrites.
  assertUniquePortableFilenames(files);
  return files;
}
