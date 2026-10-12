import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';
import { FemPortsPanel, portChartOption } from './FemPortsPanel';
import { femPortsOf, portRadiationTraces, portVelocityTraces, type FemPorts } from './femPorts';
import type { ResultPayload } from './types';
import { readChartTokens } from './EChart';

vi.mock('./EChart', async (original) => ({ ...await original<typeof import('./EChart')>(),
  EChart: ({ label }: { label: string }) => <div aria-label={label}/> }));

const data: FemPorts = {
  frequencies: [100, 200], drive_normalization: 'unit_acceleration', reference_voltage_rms_v: null,
  phase_convention: 'engineering_exp_plus_jwt', observation_angles_deg: [0, 45],
  interfaces: ['front', 'rear'].map((label, i) => ({
    id: `interface:${label}`, label, represented_area_m2: .01,
    average_normal_velocity: [{ real: 1 + i, imaginary: -2 }, { real: null, imaginary: null }],
    radiated_pressure: { horizontal: [[{ real: 1 + i, imaginary: -1 }, { real: 3, imaginary: 4 }],
      [{ real: 0, imaginary: 0 }, { real: null, imaginary: null }]],
    vertical: [[{ real: 4, imaginary: 0 }, { real: 5, imaginary: 0 }]] },
  })),
  diaphragm_radiated_pressure: { horizontal: [[{ real: 2, imaginary: 0 }]] },
  total_radiated_pressure: { horizontal: [[{ real: 5, imaginary: -2 }]], vertical: [] },
};
const result = { frequencies: data.frequencies, metadata: { fem_ports: data } } as ResultPayload;

describe('FEM port traces', () => {
  it('labels each interface and retains real, imaginary and missing velocity samples', () => {
    expect(portVelocityTraces(data)).toEqual([
      { name: 'front · Re', values: [1, null] }, { name: 'front · Im', values: [-2, null] },
      { name: 'rear · Re', values: [2, null] }, { name: 'rear · Im', values: [-2, null] },
    ]);
  });
  it('reads the selected observation, with port, diaphragm and total radiation', () => {
    expect(portRadiationTraces(data, 'horizontal', 0, 'imaginary').map((s) => s.values[0])).toEqual([-1, -1, 0, -2]);
    const offAxis = portRadiationTraces(data, 'horizontal', 1, 'spl');
    expect(offAxis[0].values[0]).toBeCloseTo(20 * Math.log10(5 / 20e-6));
    expect(portRadiationTraces(data, 'horizontal', 0, 'phase')[0].values).toEqual([-45, null]);
    expect(portRadiationTraces(data, 'vertical', 0, 'real')[0].values).toEqual([4, null]);
    expect(offAxis[0].values[1]).toBeNull();
  });
  it('keeps old and interior-only results unchanged', () => {
    expect(femPortsOf({ metadata: {} } as ResultPayload)).toBeNull();
    const host = document.createElement('div');
    const root = createRoot(host);
    act(() => root.render(<FemPortsPanel result={{ metadata: {} } as ResultPayload}/>));
    expect(host.textContent).toBe('');
    act(() => root.unmount());
  });
  it('uses frequency-valued log axes and gaps in the existing chart renderer', () => {
    const option = portChartOption(data.frequencies, portVelocityTraces(data), 'm/s', readChartTokens());
    expect(option.xAxis).toMatchObject({ type: 'log' });
    expect(option.series).toMatchObject([{ data: [[100, 1], [200, null]], connectNulls: false }, {}, {}, {}]);
  });
  it('opens the port panels and changes observation and display controls', () => {
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    act(() => root.render(<FemPortsPanel result={result}/>));
    expect(host.textContent).toContain('Ports · 2 interfaces');
    const details = host.querySelector('details')!;
    act(() => { details.open = true; details.dispatchEvent(new Event('toggle')); });
    expect(host.querySelector('[aria-label="Port radiation by frequency"]')).not.toBeNull();
    expect(host.textContent).toContain('1 m/s² drive');
    for (const [label, value] of [['Port radiation plane', 'vertical'], ['Port radiation angle', '1'], ['Port radiation display', 'phase']]) {
      const control = host.querySelector<HTMLSelectElement>(`[aria-label="${label}"]`)!;
      act(() => { control.value = value; control.dispatchEvent(new Event('change', { bubbles: true })); });
      expect(control.value).toBe(value);
    }
    act(() => root.unmount());
    host.remove();
  });
});
