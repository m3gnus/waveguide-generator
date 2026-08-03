export function JobsPanel() {
  return <div className="jobs-panel panel-scroll">
    <div className="panel-meta"><span className="pill">1 running</span><span>local queue</span></div>
    <article className="job-card running"><header><i/><b>tritonia_mk2 <em>· v4</em></b><time>02:41</time></header><p>Metal · GPU · half-sym · 48.3k el · 320 f</p><div className="job-stage"><span>assembling matrices…</span><b>62%</b></div><div className="progress"><i/></div><p>198 / 320 f · eta 1:34 · 3.1 GB</p><footer><button>Stop</button><button>Log</button></footer></article>
    <article className="job-card complete"><header><i/><b>tritonia_mk2 <em>· v3</em></b><time>12:04</time></header><p>2.94 s · 48.3k el · flatness 1.6 dB</p><div className="score"><span>★★★★<em>★</em></span><b>91.4</b></div><footer><button className="primary">Load design</button><button>Rerun</button></footer></article>
    <article className="job-card failed"><header><i/><b>sweep_a42_k088 <em>· #7</em></b><time>11:47</time></header><p>failed after 0.8 s · mesh stage</p><div className="job-error">Open edge at mouth rim — 2 edges unwelded.</div><footer><button>Retry</button><button>Open log</button></footer></article>
    <div className="earlier"><span>Earlier today</span><i/></div>
    {['m_12 boxed', 'tritonia_mk2 · v2', 'osse_ref_90x60'].map((name, index) => <button className="mini-job" key={name}><i/><span>{name}</span><em>{index < 2 ? '★★★★☆' : '★★☆☆☆'}</em><time>0{9-index}:12</time></button>)}
  </div>;
}
