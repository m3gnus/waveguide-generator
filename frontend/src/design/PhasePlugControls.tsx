import type { PhasePlug } from '../api/sourceEditor';
import { NumberField } from './NumberField';

/** Shared rigid-body controls for both source hosts. */
export function PhasePlugControls({ plugs, onChange }: { plugs: PhasePlug[]; onChange(plugs: PhasePlug[]): void }) {
  return <>
      <h3>Phase plug and annular vanes</h3><p>Rigid, full circular bodies with independently tapered radii. All bodies share inlet and outlet planes.</p>
      <div className="source-editor-toolbar"><button className="btn" type="button" disabled={plugs.length >= 8 || plugs.some((p) => p.inner0_mm === 0)} onClick={() => {
        const first = plugs[0]; onChange([{ id: `core-${crypto.randomUUID()}`, z0_mm: first?.z0_mm ?? 2, z1_mm: first?.z1_mm ?? 6, inner0_mm: 0, inner1_mm: 0, outer0_mm: first ? first.inner0_mm / 2 : 1, outer1_mm: first ? first.inner1_mm / 2 : 1.4 }, ...plugs]);
      }}>Add central plug</button><button className="btn" type="button" disabled={plugs.length >= 8} onClick={() => {
        const last = plugs.at(-1); const inner0 = last ? last.outer0_mm + 1 : 2; const inner1 = last ? last.outer1_mm + 1 : 2.4;
        onChange([...plugs, { id: `vane-${crypto.randomUUID()}`, z0_mm: last?.z0_mm ?? 2, z1_mm: last?.z1_mm ?? 6, inner0_mm: inner0, outer0_mm: inner0 + .5, inner1_mm: inner1, outer1_mm: inner1 + .5 }]);
      }}>Add annular vane</button></div>
      {!!plugs.length && <div className="source-fields">{(['z0_mm','z1_mm'] as const).map((field) => <NumberField key={field} label={field === 'z0_mm' ? 'Plug inlet Z' : 'Plug outlet Z'} value={plugs[0][field]} unit="mm" precision={6} displayPrecision={2} step={.1} onCommit={(v) => onChange(plugs.map((p) => ({ ...p, [field]: v })))}/>)}</div>}
      {plugs.map((plug, i) => <section key={i}>
        <div className="source-fields">{(['inner0_mm','outer0_mm','inner1_mm','outer1_mm'] as const).map((field) => <NumberField key={field} label={`Body ${i+1} ${field.startsWith('inner') ? 'inner' : 'outer'} ${field.includes('0') ? 'inlet' : 'outlet'} radius`} value={plug[field]} unit="mm" precision={6} displayPrecision={2} step={.1} onCommit={(v) => onChange(plugs.map((p,j) => j === i ? { ...p, [field]: v } : p))}/>)}</div><button className="btn" type="button" onClick={() => onChange(plugs.filter((_,j) => j !== i))}>Remove body {i+1}</button></section>)}
  </>;
}
