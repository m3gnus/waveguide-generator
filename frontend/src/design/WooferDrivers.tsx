/**
 * How each woofer of a design's Sources and baffle assembly is driven.
 *
 * Sources and baffle offers one Drive choice over the woofer's schema `drive`
 * and its bound driver. Simulation sets the driver up in the shared Drivers
 * section, the one CAD and file models use, through the shared Simulation
 * input of the design's assembly; this file adds only what an assembly says
 * there beyond an imported model.
 */
import { assemblyDriverBlocker, assemblyDriverNotice } from '../jobs/importedSubmission';
import type { AssemblyWoofer, WooferDriveMode } from '../stores/assembly';
import { assemblyRadiatorLabel } from '../stores/assemblySimulation';
import { useDesignStore } from '../stores/design';
import { exteriorTransducerError } from '../stores/exteriorTransducer';
import { useImportedSimulationSetup } from '../stores/importedSimulationSetup';
import { useScopedSolveOptions } from '../stores/solveOptionsContext';
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

/** What the chosen engine does with the assembly's drivers, and what stops Solve, under the shared Drivers section. */
export function AssemblyDriverStatus() {
  const state = useImportedSimulationSetup();
  const design = useDesignStore(store => store.design);
  const engine = useScopedSolveOptions(options => options.engine);
  const adaptive = useScopedSolveOptions(options => options.adaptiveFrequencySampling);
  const ground = useScopedSolveOptions(options => options.groundPlane.enabled);
  const options = { engine, adaptive_frequency_sampling: adaptive, ground_plane: { enabled: ground } } as Parameters<typeof assemblyDriverBlocker>[1];
  const blocker = assemblyDriverBlocker(state, options, id => assemblyRadiatorLabel(design, id));
  const notice = assemblyDriverNotice(state, options);
  const problems = state.driveChannels.flatMap(channel => {
    const problem = channel.exterior_transducer && exteriorTransducerError(channel.exterior_transducer);
    return problem ? [`${assemblyRadiatorLabel(design, channel.id)}: ${problem}`] : [];
  });
  return <>
    {notice && <p className="cad-driver-hint" role="status">{notice}</p>}
    {blocker ? <div className="field-error" role="alert">{blocker}</div>
      : problems.map(problem => <p className="field-error" role="status" key={problem}>{problem}</p>)}
  </>;
}
