import { parseExteriorTransducer } from './exteriorTransducer';
import type { CadReturnIngestRecord } from '../api/cadlink';
import type { CadSolveSetup } from '../api/cadOperations';
import type { CadSetup } from '../api/jobsSocket';
import { fromResult, parseWire } from '../results/crossoverSpec';
import { DRIVER_FIELD_KEYS, PASSIVE_CARDIOID_DEFAULTS, useCadReturnStore,
  type CadDriveChannel, type ChannelDriverForm, type DriverPreset, type PassiveCardioidForm } from './cadReturn';
import type { ImportedSetup } from '../jobs/importedSetup';
type CadHistorySetup = Pick<ReturnType<typeof useCadReturnStore.getState>,
  'meshSizes' | 'symmetryMode' | 'skippedSourceIds'
  | 'driveChannels' | 'exteriorOnly' | 'combineEnabled' | 'combineSpec'
  | 'channelDrivers' | 'passiveCardioid'
  | 'driveVoltageV' | 'maxDriveVoltageV'
  | 'frequencyStartHz' | 'frequencyEndHz' | 'frequencyCount'>;

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function finiteRecord(value: unknown): Record<string, number> | null {
  const record = object(value);
  if (!record) return null;
  const entries = Object.entries(record);
  if (entries.some(([key, item]) => !key || finite(item) === null)) return null;
  return Object.fromEntries(entries) as Record<string, number>;
}

function savedDriveChannels(setup: CadSetup | null | undefined, record: CadReturnIngestRecord): CadDriveChannel[] {
  const knownSources = new Set(record.sources.map((source) => source.id));
  const raw = setup?.drive_channels;
  if (!Array.isArray(raw) || raw.length === 0) return [];
  const assigned = new Set<string>();
  const channels = raw.flatMap((channel): CadDriveChannel[] => {
    if (!channel || typeof channel.id !== 'string' || !channel.id.trim()
      || !Array.isArray(channel.source_ids) || channel.source_ids.length === 0
      || (channel.motion !== undefined && channel.motion !== 'normal' && channel.motion !== 'axial')
      || channel.source_ids.some((id) => (
        typeof id !== 'string' || !knownSources.has(id) || assigned.has(id)
      ))) return [];
    channel.source_ids.forEach((id) => assigned.add(id));
    return [{ id: channel.id, source_ids: [...channel.source_ids], motion: channel.motion ?? 'normal',
      ...(channel.exterior_transducer !== undefined ? { exterior_transducer: parseExteriorTransducer(channel.exterior_transducer) } : {}) }];
  });
  return channels.length === raw.length ? channels : [];
}

function savedChannelDrivers(
  setup: CadSetup | null | undefined,
  channels: CadDriveChannel[],
  references: CadSolveSetup['driver_references'],
): Record<string, ChannelDriverForm> {
  const rawById = new Map((setup?.drive_channels ?? []).map((channel) => [channel.id, channel]));
  return Object.fromEntries(channels.flatMap((channel): Array<[string, ChannelDriverForm]> => {
    const driver = object(rawById.get(channel.id)?.driver);
    if (!driver) return [];
    const fields = Object.fromEntries(DRIVER_FIELD_KEYS.flatMap((key) => {
      const value = finite(driver[key]);
      return value === null ? [] : [[key, value]];
    }));
    const label = typeof driver.label === 'string' && driver.label.trim() ? driver.label.trim() : null;
    const reference = references?.[channel.id];
    const source = reference?.source;
    const preset: DriverPreset | null = label || reference
      ? { id: reference?.driver_id ?? `manual:${channel.id}`, label: label ?? reference!.driver_id,
        source: source === 'database' || source === 'mine' ? source : 'manual', kind: 'unknown', z_ohm: null, xo_min_hz: null, base: fields }
      : null;
    // A setup records effective values, not the original library/override split.
    // Keep them as explicit values so an automatic library refresh cannot change physics.
    return [[channel.id, { fields, frozen: true, preset: preset ? { ...preset, base: {} } : null }]];
  }));
}

function savedPassiveCardioid(setup: CadSetup | null | undefined): PassiveCardioidForm {
  const rearVolumeL = finite(setup?.passive_cardioid_rear_volume_l);
  if (rearVolumeL === null) return { ...PASSIVE_CARDIOID_DEFAULTS };
  const portAreaSource = setup?.port_area_source === 'bem_aperture' ? 'bem_aperture' : 'user';
  return {
    enabled: true,
    rearVolumeL,
    portLengthMm: finite(setup?.passive_cardioid_port_length_mm),
    modelPortAreaM2: finite(setup?.model_port_area_m2),
    bemPortAreaM2: finite(setup?.bem_port_area_m2),
    portAreaSource,
    foamResistancePaSM3: finite(setup?.passive_cardioid_foam_resistance_pa_s_m3),
    invertPort: setup?.passive_cardioid_invert_port !== false,
    coupled: setup?.passive_cardioid_coupled === true,
  };
}

/** Translate a setup (a run's or a model's) into the editable CAD rail. */
export function cadHistorySetup(saved: ImportedSetup, record: CadReturnIngestRecord): CadHistorySetup {
  const setup = saved.geometry as unknown as CadSetup;
  const options = saved.options as unknown as Record<string, unknown>;
  const mesh = object(setup?.mesh);
  const channels = savedDriveChannels(setup, record);
  const skippedSourceIds = Array.isArray(setup?.skipped_source_ids)
    && setup.skipped_source_ids.every((id) => typeof id === 'string')
    ? [...setup.skipped_source_ids]
    : [...record.skipped_source_ids];
  const fallbackChannels = (() => {
    const skipped = new Set(skippedSourceIds);
    const grouped = new Map<string, CadDriveChannel>();
    record.sources.filter((source) => !skipped.has(source.id)).forEach((source) => {
      const channel = grouped.get(source.default_drive_channel_id) ?? {
        id: source.default_drive_channel_id,
        source_ids: [],
        motion: 'normal' as const,
      };
      channel.source_ids.push(source.id);
      grouped.set(channel.id, channel);
    });
    return [...grouped.values()];
  })();
  const driveChannels = channels.length ? channels : fallbackChannels;
  const combine = object(setup?.combine);
  const combineSpec = parseWire(combine) ?? fromResult(combine ?? undefined);
  const validCombine = combineSpec !== null;
  const explicitFrequencies = Array.isArray(options.frequencies_hz)
    ? (options.frequencies_hz as unknown[]).filter((value): value is number => finite(value) !== null)
    : [];
  const range = options.frequency_range;
  const fallbackRange = Array.isArray(range) && range.length === 2 ? range.map(Number) : [200, 20_000];
  const sourceSizes = finiteRecord(mesh?.source_size_mm) ?? { ...record.mesh_sizes.source_size_mm };
  return {
    meshSizes: {
      ...record.mesh_sizes,
      ...(saved.geometry.mesh?.surface_groups != null ? { surface_groups: saved.geometry.mesh.surface_groups } : {}),
      source_size_mm: sourceSizes,
      rigid_size_mm: finite(mesh?.rigid_size_mm) ?? record.mesh_sizes.rigid_size_mm,
      transition_mm: finite(mesh?.transition_mm) ?? record.mesh_sizes.transition_mm,
    },
    symmetryMode: (saved as unknown as CadSolveSetup).preparation?.symmetry_mode ?? 'auto',
    skippedSourceIds,
    driveChannels,
    exteriorOnly: typeof setup?.exterior_only === 'boolean' ? setup.exterior_only : false,
    combineEnabled: setup ? validCombine : null,
    combineSpec,
    channelDrivers: savedChannelDrivers(setup, driveChannels, (saved as unknown as CadSolveSetup).driver_references),
    passiveCardioid: savedPassiveCardioid(setup),
    driveVoltageV: finite(setup?.drive_voltage_v) ?? 2.83,
    maxDriveVoltageV: finite(setup?.max_drive_voltage_v) ?? null,
    frequencyStartHz: explicitFrequencies[0] ?? fallbackRange[0],
    frequencyEndHz: explicitFrequencies.at(-1) ?? fallbackRange[1],
    frequencyCount: explicitFrequencies.length || Number(options.num_frequencies) || 1,
  };
}
