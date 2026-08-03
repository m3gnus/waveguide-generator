import { useEffect, useMemo, useRef, useState } from 'react';
import type { ResultPayload } from './types';

const RANGE_DB = 30;
const COLORS = ['#440154', '#482878', '#3e4a89', '#31688e', '#26828e', '#1f9e89', '#35b779', '#6ece58', '#b5de2b', '#fde725'];

export function hasBalloonData(result: ResultPayload): boolean {
  const balloon = result.balloon;
  return Boolean(balloon && balloon.theta_deg.length >= 2 && balloon.phi_deg.length >= 3 && balloon.spl_norm_db.length > 0);
}

export function balloonMissingReason(result: ResultPayload, label: string): string {
  const sampling = result.metadata?.balloon_sampling;
  const status = sampling && typeof sampling === 'object' ? String((sampling as Record<string, unknown>).status ?? '') : '';
  if (status === 'backend_unsupported') return `${label} needs spherical sampling, but the solver backend did not configure a sphere grid.`;
  if (status === 'missing_result') return `${label} was requested, but this solve returned no spherical result data.`;
  return `${label} requires “3D Balloon Sampling”; enable it and run a new solve.`;
}

export function closestFrequencyIndex(frequencies: number[], target = 1_000): number {
  if (!frequencies.length) return 0;
  return frequencies.reduce((best, value, index) => Math.abs(Math.log((value || 1) / target)) < Math.abs(Math.log((frequencies[best] || 1) / target)) ? index : best, 0);
}

function colorForDb(value: number): string {
  const position = Math.max(0, Math.min(1, 1 + value / RANGE_DB)) * (COLORS.length - 1);
  return COLORS[Math.round(position)];
}

function sizeCanvas(canvas: HTMLCanvasElement): CanvasRenderingContext2D | null {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(120, Math.round(rect.width || canvas.clientWidth || 300));
  const height = Math.max(100, Math.round(rect.height || canvas.clientHeight || 180));
  const ratio = Math.min(globalThis.devicePixelRatio || 1, 2);
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  let context: CanvasRenderingContext2D | null = null;
  try { context = canvas.getContext('2d'); } catch { return null; }
  context?.scale(ratio, ratio);
  return context;
}

function drawBalloon(canvas: HTMLCanvasElement, result: ResultPayload, frequencyIndex: number): void {
  const balloon = result.balloon;
  if (!balloon) return;
  const context = sizeCanvas(canvas);
  if (!context) return;
  const width = canvas.width / Math.min(globalThis.devicePixelRatio || 1, 2);
  const height = canvas.height / Math.min(globalThis.devicePixelRatio || 1, 2);
  context.clearRect(0, 0, width, height);
  const points: Array<{ x: number; y: number; depth: number; db: number }> = [];
  const grid = balloon.spl_norm_db[frequencyIndex] ?? [];
  balloon.theta_deg.forEach((thetaValue, thetaIndex) => {
    const theta = thetaValue * Math.PI / 180;
    balloon.phi_deg.forEach((phiValue, phiIndex) => {
      const phi = phiValue * Math.PI / 180;
      const db = Number(grid[thetaIndex]?.[phiIndex] ?? -RANGE_DB);
      const radius = Math.max(.03, Math.min(1, 1 + db / RANGE_DB));
      const x = radius * Math.sin(theta) * Math.cos(phi);
      const y = radius * Math.sin(theta) * Math.sin(phi);
      const z = radius * Math.cos(theta);
      points.push({ x: width / 2 + (x + z * .22) * height * .36, y: height / 2 - (y - z * .14) * height * .36, depth: z, db });
    });
  });
  points.sort((a, b) => a.depth - b.depth).forEach((point) => {
    context.fillStyle = colorForDb(point.db);
    context.globalAlpha = .35 + .65 * ((point.depth + 1) / 2);
    context.beginPath(); context.arc(point.x, point.y, Math.max(1.5, height / 95), 0, Math.PI * 2); context.fill();
  });
  context.globalAlpha = 1;
  context.strokeStyle = 'rgba(220,80,70,.65)'; context.beginPath(); context.moveTo(width * .12, height / 2); context.lineTo(width * .88, height / 2); context.stroke();
  context.strokeStyle = 'rgba(75,140,225,.65)'; context.beginPath(); context.moveTo(width / 2, height * .12); context.lineTo(width / 2, height * .88); context.stroke();
}

function wrappedPhiInterval(phiDegrees: number[], queryDegrees: number) {
  const first = Number(phiDegrees[0]);
  const query = ((((queryDegrees - first) % 360) + 360) % 360) + first;
  let lower = phiDegrees.length - 1;
  for (let index = 0; index < phiDegrees.length - 1; index += 1) if (query >= phiDegrees[index] && query < phiDegrees[index + 1]) { lower = index; break; }
  const upper = (lower + 1) % phiDegrees.length;
  const low = phiDegrees[lower];
  const high = upper === 0 ? first + 360 : phiDegrees[upper];
  return { lower, upper, weight: high > low ? Math.max(0, Math.min(1, (query - low) / (high - low))) : 0 };
}

export function sampleBalloonGrid(thetaDegrees: number[], phiDegrees: number[], grid: Array<Array<number | null>>, thetaQuery: number, phiQuery: number): number {
  if (thetaDegrees.length < 2 || phiDegrees.length < 2) return Number.NaN;
  const theta = Math.max(thetaDegrees[0], Math.min(thetaQuery, thetaDegrees.at(-1)!));
  let lowerTheta = thetaDegrees.length - 2;
  for (let index = 0; index < thetaDegrees.length - 1; index += 1) if (theta >= thetaDegrees[index] && theta <= thetaDegrees[index + 1]) { lowerTheta = index; break; }
  const upperTheta = lowerTheta + 1;
  const thetaWeight = (theta - thetaDegrees[lowerTheta]) / (thetaDegrees[upperTheta] - thetaDegrees[lowerTheta] || 1);
  const phi = wrappedPhiInterval(phiDegrees, phiQuery);
  const values = [grid[lowerTheta]?.[phi.lower], grid[lowerTheta]?.[phi.upper], grid[upperTheta]?.[phi.lower], grid[upperTheta]?.[phi.upper]].map(Number);
  if (!values.every(Number.isFinite)) return Number.NaN;
  const low = values[0] + (values[1] - values[0]) * phi.weight;
  const high = values[2] + (values[3] - values[2]) * phi.weight;
  return low + (high - low) * thetaWeight;
}

function drawForwardMap(canvas: HTMLCanvasElement, result: ResultPayload, frequencyIndex: number): void {
  const balloon = result.balloon;
  if (!balloon) return;
  const context = sizeCanvas(canvas);
  if (!context) return;
  const ratio = Math.min(globalThis.devicePixelRatio || 1, 2);
  const width = canvas.width / ratio;
  const height = canvas.height / ratio;
  const radius = Math.max(1, Math.min(width, height) * .46);
  const centerX = width / 2;
  const centerY = height / 2;
  const grid = balloon.spl_norm_db[frequencyIndex] ?? [];
  const step = Math.max(2, Math.ceil(radius / 80));
  for (let y = -radius; y <= radius; y += step) for (let x = -radius; x <= radius; x += step) {
    const radial = Math.hypot(x, y);
    if (radial > radius) continue;
    const theta = radial / radius * 90;
    const phi = Math.atan2(-y, x) * 180 / Math.PI;
    const db = sampleBalloonGrid(balloon.theta_deg, balloon.phi_deg, grid, theta, phi);
    context.fillStyle = colorForDb(Number.isFinite(db) ? db : -RANGE_DB);
    context.fillRect(centerX + x, centerY + y, step + 1, step + 1);
  }
  context.strokeStyle = 'rgba(255,255,255,.6)'; context.lineWidth = 1;
  [1 / 3, 2 / 3, 1].forEach((fraction) => { context.beginPath(); context.arc(centerX, centerY, radius * fraction, 0, Math.PI * 2); context.stroke(); });
  context.beginPath(); context.moveTo(centerX - radius, centerY); context.lineTo(centerX + radius, centerY); context.moveTo(centerX, centerY - radius); context.lineTo(centerX, centerY + radius); context.stroke();
}

function formatFrequency(value: number | undefined): string {
  if (!Number.isFinite(value)) return '—';
  return value! >= 1_000 ? `${(value! / 1_000).toFixed(value! >= 10_000 ? 1 : 2)} kHz` : `${Math.round(value!)} Hz`;
}

function FrequencyCanvas({ result, kind }: { result: ResultPayload; kind: 'balloon' | 'beam' }) {
  const frequencies = result.balloon?.frequencies ?? [];
  const [index, setIndex] = useState(() => closestFrequencyIndex(frequencies));
  const canvas = useRef<HTMLCanvasElement>(null);
  const beam = result.beam_shape;
  useEffect(() => setIndex(closestFrequencyIndex(frequencies)), [result.balloon]);
  useEffect(() => {
    if (!canvas.current) return;
    const draw = () => canvas.current && (kind === 'balloon' ? drawBalloon(canvas.current, result, index) : drawForwardMap(canvas.current, result, index));
    draw();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(draw);
    observer?.observe(canvas.current);
    return () => observer?.disconnect();
  }, [result, index, kind]);
  const readout = useMemo(() => {
    const parts = [formatFrequency(frequencies[index])];
    if (kind === 'beam') {
      const horizontal = beam?.horizontal_beamwidth_deg?.[index];
      const vertical = beam?.vertical_beamwidth_deg?.[index];
      const di = beam?.spherical_di_db?.[index];
      if (horizontal != null && vertical != null) parts.push(`−6 dB ${horizontal.toFixed(0)}° H × ${vertical.toFixed(0)}° V`);
      if (di != null) parts.push(`DI ${di.toFixed(1)} dB`);
    }
    return parts.join(' · ');
  }, [beam, frequencies, index, kind]);
  return <div style={{ position: 'absolute', inset: 0, display: 'grid', gridTemplateRows: 'minmax(0, 1fr) auto' }}>
    <canvas ref={canvas} role="img" aria-label={kind === 'balloon' ? '3D directivity balloon' : 'Front-facing directivity map'} style={{ width: '100%', height: '100%' }}/>
    <label style={{ display: 'flex', gap: 7, alignItems: 'center', padding: '2px 5px', color: 'var(--fg3)', fontSize: 9 }}><input aria-label={kind === 'balloon' ? 'Balloon frequency' : 'Forward beam frequency'} type="range" min={0} max={Math.max(0, frequencies.length - 1)} value={index} onChange={(event) => setIndex(Number(event.target.value))} style={{ flex: 1 }}/><span>{readout}</span></label>
  </div>;
}

export function BalloonRenderer({ result }: { result: ResultPayload }) {
  return hasBalloonData(result) ? <FrequencyCanvas result={result} kind="balloon"/> : <ChartStub reason={balloonMissingReason(result, '3D Balloon')}/>;
}

export function ForwardBeamRenderer({ result }: { result: ResultPayload }) {
  return hasBalloonData(result) ? <FrequencyCanvas result={result} kind="beam"/> : <ChartStub reason={balloonMissingReason(result, 'Forward Beam Map')}/>;
}

export function ChartStub({ reason }: { reason: string }) {
  return <div role="status" style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', padding: 16, color: 'var(--fg3)', fontSize: 10, textAlign: 'center' }}>{reason}</div>;
}
