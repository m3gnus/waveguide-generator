/**
 * How each woofer of a design's Sources and baffle assembly is driven.
 *
 * One Drive choice, offered where the woofer is placed and in Simulation, over
 * one state: the woofer's schema `drive` and its bound driver. Simulation also
 * sets the driver up, with the shared driver picker and fields, through the
 * shared Simulation input of the live design's assembly.
 */
import { assemblyDriverBlocker, assemblyDriverNotice } from '../jobs/importedSubmission';
import { commitAssembly, setWooferDrive, wooferDriveMode, type AssemblyWoofer, type WooferDriveMode } from '../stores/assembly';
import { assemblyRadiatorLabel } from '../stores/assemblySimulation';
import { useDesignStore } from '../stores/design';
import { exteriorTransducerError } from '../stores/exteriorTransducer';
import { useImportedSimulationSetup } from '../stores/importedSimulationSetup';
import { useScopedSolveOptions } from '../stores/solveOptionsContext';
import { ExteriorDriverLibraryPicker } from './DriverPicker';
import { ExteriorDriverFields, ExteriorDriverOptions } from './ExteriorDriverOptions';
import { NumberField } from './NumberField';
import { SelectRow } from './SelectRow';

const DRIVE_OPTIONS: Array<[WooferDriveMode, string]> = [
  ['velocity', 'Fixed velocity (1 m/s RMS)'],
  ['driver', 'Driver (T/S)'],
  ['rigid', 'Rigid (part of the baffle)'],
];

const DRIVE_HELP = 'Fixed velocity moves the diaphragm at 1 m/s RMS, and its results are per 1 m/s. '
  + 'Driver (T/S) drives it with the drive voltage through a driver from the library or entered by hand; '
  + 'BEAT solves it with the air load. Rigid keeps the diaphragm as part of the baffle: it does not move.';

export function WooferDriveSelect({ id, woofer, mode, onChange }: {
  id: string; woofer: AssemblyWoofer; mode: WooferDriveMode; onChange: (mode: WooferDriveMode) => void;
}) {
  return <SelectRow text={DRIVE_HELP}>
    <label htmlFor={id}>Drive</label>
    <select id={id} aria-label={`Drive for ${woofer.label}`} value={mode}
      onChange={event => onChange(event.currentTarget.value as WooferDriveMode)}>
      {DRIVE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select>
  </SelectRow>;
}

/** One woofer's drive and, when it has a driver, the driver itself. */
function WooferDriver({ woofer }: { woofer: AssemblyWoofer }) {
  const state = useImportedSimulationSetup();
  const assembly = useDesignStore(store => store.design.assembly)!;
  const mode = wooferDriveMode(assembly, woofer.id);
  const channel = state.driveChannels.find(item => item.id === woofer.id);
  const form = channel?.exterior_transducer;
  const problem = form ? exteriorTransducerError(form) : null;
  return <div className="cad-channel" data-woofer-id={woofer.id}>
    <div className="cad-channel-summary"><span>{woofer.label}</span></div>
    {woofer.enabled === false
      ? <p className="cad-driver-hint">Disabled in Sources and baffle, so it is not solved.</p>
      : <WooferDriveSelect id={`woofer-drive-${woofer.id}`} woofer={woofer} mode={mode}
        onChange={next => commitAssembly(setWooferDrive(useDesignStore.getState().design.assembly!, woofer.id, next))}/>}
    {channel && form && <>
      <p className="cad-driver-hint">Driven by the drive voltage along the woofer's axis. Enter bare Mmd, without air load: datasheet Mms is not Mmd.</p>
      <ExteriorDriverLibraryPicker channel={channel} displayName={woofer.label} roleHint="LF"/>
      <ExteriorDriverFields channelId={woofer.label} form={form} onChange={next => state.setExteriorTransducer(woofer.id, next)}/>
      <ExteriorDriverOptions channelId={woofer.label} form={form} onChange={next => state.setExteriorTransducer(woofer.id, next)}/>
      {problem && <p className="field-error" role="status">{problem}</p>}
    </>}
  </div>;
}

/** Simulation's woofer drivers for a parametric design with Sources and baffle woofers. */
export function WooferDrivers() {
  const state = useImportedSimulationSetup();
  const design = useDesignStore(store => store.design);
  const engine = useScopedSolveOptions(options => options.engine);
  const adaptive = useScopedSolveOptions(options => options.adaptiveFrequencySampling);
  const ground = useScopedSolveOptions(options => options.groundPlane.enabled);
  const woofers = design.assembly?.woofers ?? [];
  const driven = state.driveChannels.some(channel => channel.exterior_transducer);
  const options = { engine, adaptive_frequency_sampling: adaptive, ground_plane: { enabled: ground } } as Parameters<typeof assemblyDriverBlocker>[1];
  const blocker = assemblyDriverBlocker(state, options, id => assemblyRadiatorLabel(design, id));
  const notice = assemblyDriverNotice(state, options);
  const unfinished = state.driveChannels.some(channel => channel.exterior_transducer && exteriorTransducerError(channel.exterior_transducer));
  return <>
    <div className="cad-channel-list">{woofers.map(woofer => <WooferDriver key={woofer.id} woofer={woofer}/>)}</div>
    {driven && <NumberField label="Drive voltage" unit="V" value={state.driveVoltageV} min={0.01} step={0.1} precision={2}
      description="RMS voltage applied to every woofer driver (2.83 V ≈ 1 W into 8 Ω). The horn stays at its source velocity."
      onCommit={state.setDriveVoltage}/>}
    {notice && <p className="cad-driver-hint" role="status">{notice}</p>}
    {/* An unfinished driver is said beside its own fields; this line says the rest. */}
    {blocker && !unfinished && <div className="field-error" role="alert">{blocker}</div>}
  </>;
}
