import { useState } from 'react';
import type { EChartsOption } from 'echarts';
import { EChart, useChartTokens, type ChartTokens } from './EChart';
import { femPortsOf, portRadiationTraces, portVelocityTraces, type FemPorts, type PortTrace, type PortRadiationDisplay } from './femPorts';
import type { ResultPayload } from './types';

export function portChartOption(frequencies: number[], traces: PortTrace[], units: string, tokens: ChartTokens): EChartsOption {
  return {
    backgroundColor: tokens.background,
    textStyle: { color: tokens.foreground },
    color: tokens.series,
    grid: { left: 65, right: 16, top: 55, bottom: 45 },
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', top: 24, textStyle: { color: tokens.foreground } },
    xAxis: { type: 'log', name: 'Hz', axisLabel: { color: tokens.muted }, splitLine: { lineStyle: { color: tokens.grid } } },
    yAxis: { type: 'value', name: units, axisLabel: { color: tokens.muted }, splitLine: { lineStyle: { color: tokens.grid } } },
    series: traces.map((trace) => ({ name: trace.name, type: 'line', showSymbol: false, connectNulls: false,
      data: frequencies.map((frequency, i) => [frequency, trace.values[i] ?? null]) })),
  };
}

function PortPlots({ data }: { data: FemPorts }) {
  const tokens = useChartTokens();
  const [plane, setPlane] = useState('horizontal');
  const [angle, setAngle] = useState(0);
  const [display, setDisplay] = useState<PortRadiationDisplay>('spl');
  const planes = Object.keys(data.total_radiated_pressure).filter((p) => p !== 'sphere');
  const selectedPlane = planes.includes(plane) ? plane : planes[0];
  const selectedAngle = angle < data.observation_angles_deg.length ? angle : 0;
  const units = display === 'spl' ? 'SPL [dB]' : display === 'phase' ? 'Phase [°]' : 'Pressure [Pa]';
  return <>
    <div className="results-toolbar">
      <label>Plane <select aria-label="Port radiation plane" value={selectedPlane} onChange={(e) => setPlane(e.target.value)}>
        {planes.map((p) => <option key={p} value={p}>{p}</option>)}
      </select></label>
      <label>Angle <select aria-label="Port radiation angle" value={selectedAngle} onChange={(e) => setAngle(Number(e.target.value))}>
        {data.observation_angles_deg.map((a, i) => <option key={i} value={i}>{a}°</option>)}
      </select></label>
      <label>Display <select aria-label="Port radiation display" value={display} onChange={(e) => setDisplay(e.target.value as PortRadiationDisplay)}>
        <option value="spl">SPL</option><option value="phase">Phase</option><option value="real">Real</option><option value="imaginary">Imaginary</option>
      </select></label>
      <span>{data.drive_normalization === 'voltage_rms' ? `${data.reference_voltage_rms_v} V RMS` : '1 m/s² drive'} · outward normal · engineering phase</span>
    </div>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 6 }}>
      <section className="result-card" style={{ height: 240 }} aria-label="Port velocity">
        <EChart label="Port average normal velocity by frequency" option={portChartOption(data.frequencies, portVelocityTraces(data), 'Velocity [m/s]', tokens)}/>
        <div className="result-chrome"><span className="result-title"><b>Port velocity</b><em>m/s</em></span></div>
      </section>
      <section className="result-card" style={{ height: 240 }} aria-label="Port radiation">
        <EChart label="Port radiation by frequency" option={portChartOption(data.frequencies, portRadiationTraces(data, selectedPlane, selectedAngle, display), units, tokens)}/>
        <div className="result-chrome"><span className="result-title"><b>Port radiation</b></span></div>
      </section>
    </div>
  </>;
}

export function FemPortsPanel({ result }: { result: ResultPayload }) {
  const data = femPortsOf(result);
  const [open, setOpen] = useState(false);
  if (!data) return null;
  return <details className="result-diagnostics" onToggle={(e) => setOpen(e.currentTarget.open)}>
    <summary>Ports · {data.interfaces.length} {data.interfaces.length === 1 ? 'interface' : 'interfaces'}</summary>
    {open && <div className="result-power-details"><PortPlots data={data}/></div>}
  </details>;
}
