const cards = [
  ['SPL', 'normalized · 1 m'], ['Directivity', 'horizontal'], ['Polar', '4.20 kHz'], ['Impedance', 'Z/ρc'], ['Beamwidth', '−6 dB'],
];

export function ResultsPanel() {
  return <div className="results-panel panel-scroll">
    <div className="results-toolbar"><span className="result-chip"><i />tritonia_mk2 v3</span><span className="result-chip muted"><i />m_12 boxed</span><button>+ compare</button><span className="spacer"/><span>cursor</span><b>4.20 kHz</b></div>
    <div className="result-grid">{cards.map(([title, subtitle], index) => <section key={title} className={`result-card result-${index}`}><header><b>{title}</b><span>{subtitle}</span></header><div className="chart-placeholder"><i/><i/><i/><svg viewBox="0 0 200 70" preserveAspectRatio="none"><path d={index % 2 ? 'M0 54 C35 30 54 48 82 26 S140 48 200 12' : 'M0 48 C22 46 35 20 58 35 S88 22 115 30 S154 10 200 22'} /></svg></div></section>)}</div>
  </div>;
}
