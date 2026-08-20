import type { RadiationImpedancePresentation } from '../api/results';

export interface RadiationImpedanceTrace {
  name: string;
  component: 'real' | 'imaginary';
  data: Array<[number, number]>;
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** Curves shown for a stored radiation matrix.
 *
 * The in-phase port reduction is what the passive-cardioid LEM consumes, so it
 * is the first presentation choice. Older/partial artifacts without a reduced
 * port set fall back to each aperture's engineering self impedance. No result
 * smoothing is applied: smoothing a complex load can alter passivity and turn
 * a display preference into different physics.
 */
export function radiationImpedanceTraces(
  presentation: RadiationImpedancePresentation,
): RadiationImpedanceTrace[] {
  const frequencies = presentation.frequencies_hz;
  const reduced = presentation.in_phase_termination;
  const curves = reduced.aperture_names.length
    ? reduced.aperture_names.map((name, index) => ({
      name: `${name} · in-phase ports`,
      real: reduced.real.map((row) => row[index]),
      imaginary: reduced.imaginary.map((row) => row[index]),
    }))
    : presentation.apertures.map(({ name }, index) => ({
      name: `${name} · self`,
      real: presentation.engineering_matrix.real.map((matrix) => matrix[index]?.[index]),
      imaginary: presentation.engineering_matrix.imaginary.map((matrix) => matrix[index]?.[index]),
    }));

  return curves.flatMap(({ name, real, imaginary }) => ([
    {
      name: `${name} · Re`,
      component: 'real' as const,
      data: frequencies.flatMap((frequency, index) => (
        finite(frequency) && frequency > 0 && finite(real[index])
          ? [[frequency, real[index]] as [number, number]] : []
      )),
    },
    {
      name: `${name} · Im`,
      component: 'imaginary' as const,
      data: frequencies.flatMap((frequency, index) => (
        finite(frequency) && frequency > 0 && finite(imaginary[index])
          ? [[frequency, imaginary[index]] as [number, number]] : []
      )),
    },
  ])).filter(({ data }) => data.length);
}
