import { useState, type ReactNode } from 'react';
import { useDesignStore, type DesignFamily } from '../stores/design';
import { NumberField } from './NumberField';

interface SectionProps {
  title: string;
  summary: string;
  children: ReactNode;
  initiallyOpen?: boolean;
}

function Section({ title, summary, children, initiallyOpen = true }: SectionProps) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <section className={`param-section${open ? '' : ' closed'}`}>
      <button className="section-head" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span className="chevron">⌄</span><span className="section-name">{title}</span><span className="spacer" />
        <span className="section-summary">{summary}</span>
      </button>
      {open && <div className="section-body">{children}</div>}
    </section>
  );
}

export function ParamPanel() {
  const design = useDesignStore((state) => state.design);
  const update = useDesignStore((state) => state.updateField);
  const setFamily = useDesignStore((state) => state.setFamily);
  const beginDrag = useDesignStore((state) => state.beginDrag);
  const endDrag = useDesignStore((state) => state.endDrag);
  const setQuadrants = useDesignStore((state) => state.setQuadrants);
  const setSourceConvention = useDesignStore((state) => state.setSourceConvention);
  const field = (path: string) => ({
    onCommit: (value: number) => update(path, value),
    onBeginDrag: beginDrag,
    onEndDrag: endDrag,
  });

  const profile = design.formula === 'R-OSSE' ? (
    <>
      <NumberField label="Mouth radius" symbol="R" value={design.R ?? 150} unit="mm" min={40} max={300} step={.5} modified {...field('R')} />
      <NumberField label="Throat radius" symbol="r₀" value={design.r0 ?? 12.7} unit="mm" min={2} max={60} step={.1} {...field('r0')} />
      <NumberField label="Throat angle" symbol="α₀" value={design.a0 ?? 15} unit="°" min={0} max={60} step={.25} {...field('a0')} />
      <NumberField label="Coverage" symbol="α" value={design.a ?? 42} unit="°" min={10} max={90} step={.25} modified {...field('a')} />
      <NumberField label="Termination" symbol="k" value={design.k ?? .82} min={0} max={1} step={.005} precision={3} modified {...field('k')} />
      <NumberField label="Rollback" symbol="r" value={design.r ?? .36} min={0} max={1} step={.005} precision={3} {...field('r')} />
      <NumberField label="Bend" symbol="b" value={design.b ?? .28} min={0} max={1} step={.005} precision={3} {...field('b')} />
      <NumberField label="Morph exponent" symbol="m" value={design.m ?? .85} min={0} max={2} step={.005} precision={3} modified {...field('m')} />
      <div className="derived"><span>Axial length</span><b>167.4 mm</b></div>
    </>
  ) : design.formula === 'OSSE' ? (
    <>
      <NumberField label="Axial length" symbol="L" value={design.L ?? 167.4} unit="mm" min={40} max={400} step={.5} {...field('L')} />
      <NumberField label="Throat radius" symbol="r₀" value={design.r0 ?? 12.7} unit="mm" min={2} max={60} step={.1} {...field('r0')} />
      <NumberField label="Throat angle" symbol="α₀" value={design.a0 ?? 15} unit="°" min={0} max={60} step={.25} {...field('a0')} />
      <NumberField label="Coverage" symbol="α" value={design.a ?? 42} unit="°" min={10} max={90} step={.25} {...field('a')} />
      <NumberField label="Termination" symbol="k" value={design.k ?? .82} min={0} max={1} step={.005} precision={3} {...field('k')} />
    </>
  ) : <div className="coming-soon"><b>{design.formula}</b><span>Parameter editing is coming in a later phase.</span></div>;

  return (
    <div className="param-panel panel-scroll">
      <div className="panel-meta"><span className="pill accent">{design.formula}</span><span>live design</span></div>
      <Section title="Profile" summary={design.formula === 'R-OSSE' ? '8 params' : design.formula === 'OSSE' ? '5 params' : 'later phase'}>
        <div className="select-row">
          <label htmlFor="family">Family</label>
          <select id="family" value={design.formula} onChange={(event) => setFamily(event.target.value as DesignFamily)}>
            <option>OSSE</option><option>R-OSSE</option><option>ICW</option><option>FREEFORM</option>
          </select>
        </div>
        {profile}
      </Section>
      <Section title="Source" summary="sph. cap">
        <div className="select-row"><label>Shape</label><span className="static-select">Spherical cap⌄</span></div>
        <div className="field-row"><span className="field-label">Drive convention</span><div className="segments">
          {(['normal', 'axial'] as const).map((mode) => <button key={mode} className={design.source.velocity_convention === mode ? 'on' : ''} onClick={() => setSourceConvention(mode)}>{mode === 'normal' ? 'Velocity' : 'Acceleration'}</button>)}
        </div></div>
        <NumberField label="Amplitude" value={design.source.velocity} unit="m/s²" min={0} max={2} step={.01} precision={3} {...field('source.velocity')} />
      </Section>
      <Section title="Symmetry" summary="half · Q1 Q2">
        <div className="quadrant-wrap"><div className="quadrants">
          {[2, 1, 3, 4].map((quadrant) => <button key={quadrant} className={design.quadrants.includes(quadrant) ? 'on' : ''} onClick={() => {
            const next = design.quadrants.includes(quadrant) ? design.quadrants.filter((item) => item !== quadrant) : [...design.quadrants, quadrant];
            if (next.length) setQuadrants(next);
          }}>Q{quadrant}</button>)}
        </div><div className="quadrant-meta"><b>Half domain</b><span>{design.quadrants.length} of 4 quadrants</span><em>≈ 3.4× faster solve</em></div></div>
      </Section>
      <Section title="Enclosure" summary="rounded box">
        <div className="select-row"><label>Shape</label><span className="static-select">Rounded box⌄</span></div>
        <NumberField label="Depth" value={design.enclosure.depth} unit="mm" min={80} max={500} step={1} precision={0} modified {...field('enclosure.depth')} />
        <NumberField label="Edge radius" value={design.enclosure.edge_radius} unit="mm" min={0} max={80} step={.5} {...field('enclosure.edge_radius')} />
        <NumberField label="Baffle margin" value={design.enclosure.baffle_margin} unit="mm" min={0} max={100} step={.5} {...field('enclosure.baffle_margin')} />
      </Section>
      <Section title="Mesh" summary="48.3k el">
        <NumberField label="Angular segments" value={design.mesh.angular_segments} min={24} max={256} step={1} precision={0} {...field('mesh.angular_segments')} />
        <NumberField label="Length segments" value={design.mesh.length_segments} min={12} max={160} step={1} precision={0} {...field('mesh.length_segments')} />
        <NumberField label="Max edge" value={design.mesh.mouth_resolution} unit="mm" min={.1} max={2.86} step={.05} invalidMessage={design.mesh.mouth_resolution > 2.86 ? 'Must be ≤ 2.86 mm — λ/6 at 20 kHz' : undefined} {...field('mesh.mouth_resolution')} />
        {design.mesh.mouth_resolution > 2.86 && <div className="field-error">△ Must be ≤ 2.86 mm — λ/6 at 20 kHz</div>}
        <div className="derived"><span>Elements</span><b>48 312</b></div>
      </Section>
    </div>
  );
}
