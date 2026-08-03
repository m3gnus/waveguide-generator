import * as echarts from './vendor/echarts.esm.min.js';

const background = '#080b10';
const textColor = '#cbd7e6';
const axisColor = '#52647a';
const charts = {
  fr: echarts.init(document.querySelector('#fr'), 'dark', { renderer: 'canvas' }),
  heatmap: echarts.init(document.querySelector('#heatmap'), 'dark', { renderer: 'canvas' }),
  polar: echarts.init(document.querySelector('#polar'), 'dark', { renderer: 'canvas' }),
};
const measureButton = document.querySelector('#measure');
const frequencySlider = document.querySelector('#frequency');
const frequencyValue = document.querySelector('#frequencyValue');
const noteNode = document.querySelector('#note');
const statsNode = document.querySelector('#stats');
let result;
let polarAxis = 'horizontal';

function baseOption(title) {
  return {
    backgroundColor: background,
    animation: false,
    title: { text: title, left: 12, top: 10, textStyle: { color: textColor } },
    textStyle: { color: textColor },
  };
}

function renderFrequencyResponse() {
  const frequencies = result.spl_on_axis.frequencies || result.frequencies;
  const spl = result.spl_on_axis.spl;
  const series = Array.from({ length: 20 }, (_, index) => ({
    name: index === 0 ? 'on-axis SPL' : `offset copy ${index}`,
    type: 'line',
    showSymbol: false,
    sampling: 'lttb',
    lineStyle: { width: index === 0 ? 2.5 : 1, opacity: index === 0 ? 1 : 0.48 },
    data: frequencies.map((frequency, pointIndex) => [frequency, spl[pointIndex] - index * 1.25]),
  }));
  charts.fr.setOption({
    ...baseOption('FR overlay — real on-axis SPL + 19 offset copies'),
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', top: 42, textStyle: { color: textColor } },
    grid: { left: 70, right: 28, top: 92, bottom: 64 },
    dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 12 }],
    xAxis: {
      type: 'log', name: 'Hz', min: Math.min(...frequencies), max: Math.max(...frequencies),
      axisLine: { lineStyle: { color: axisColor } }, splitLine: { show: true },
    },
    yAxis: { type: 'value', name: 'dB', axisLine: { lineStyle: { color: axisColor } } },
    series,
  }, true);
}

function renderHeatmap() {
  const axisData = result.directivity[polarAxis];
  const angles = axisData[0].map((point) => point[0]);
  const frequencyLabels = result.frequencies.map((frequency) => Math.round(frequency).toString());
  const heatmapData = [];
  axisData.forEach((section, frequencyIndex) => {
    section.forEach((point, angleIndex) => {
      heatmapData.push([angleIndex, frequencyIndex, point[1]]);
    });
  });
  const levels = heatmapData.map((point) => point[2]);
  charts.heatmap.setOption({
    ...baseOption(`${polarAxis} directivity heatmap — angle × frequency`),
    tooltip: {
      formatter(params) {
        const [angleIndex, frequencyIndex, db] = params.value;
        return `${frequencyLabels[frequencyIndex]} Hz<br>${angles[angleIndex]}°: ${db.toFixed(2)} dB`;
      },
    },
    grid: { left: 76, right: 92, top: 58, bottom: 70 },
    xAxis: { type: 'category', name: 'angle (deg)', data: angles, axisLabel: { interval: 5 } },
    yAxis: { type: 'category', name: 'frequency (Hz)', data: frequencyLabels, axisLabel: { interval: 5 } },
    visualMap: {
      min: Math.floor(Math.min(...levels)), max: 0, calculable: true, orient: 'vertical',
      right: 8, top: 86, inRange: { color: ['#081126', '#173d78', '#1da3a3', '#f2d448', '#e84a35'] },
    },
    series: [{ type: 'heatmap', data: heatmapData, progressive: 2500, emphasis: { itemStyle: { borderColor: '#fff', borderWidth: 1 } } }],
  }, true);
}

function renderPolar() {
  const index = Number(frequencySlider.value);
  const frequency = result.frequencies[index];
  const half = result.directivity[polarAxis][index];
  const mirrored = [
    ...half.map(([angle, db]) => [db, angle]),
    ...half.slice(1, -1).reverse().map(([angle, db]) => [db, 360 - angle]),
  ];
  frequencyValue.value = `${Math.round(frequency)} Hz`;
  charts.polar.setOption({
    ...baseOption(`${polarAxis} polar section — ${Math.round(frequency)} Hz`),
    tooltip: { trigger: 'item', formatter: (params) => `${params.value[1].toFixed(0)}°: ${params.value[0].toFixed(2)} dB` },
    polar: { radius: '72%' },
    angleAxis: { type: 'value', min: 0, max: 360, startAngle: 90, interval: 30 },
    radiusAxis: { type: 'value', min: -40, max: 0, splitNumber: 4 },
    series: [{ type: 'line', coordinateSystem: 'polar', showSymbol: false, data: mirrored, lineStyle: { width: 2, color: '#52b5ff' } }],
  }, true);
}

function percentile(values, p) {
  const ordered = [...values].sort((a, b) => a - b);
  const position = (ordered.length - 1) * p / 100;
  const lower = Math.floor(position);
  const upper = Math.min(lower + 1, ordered.length - 1);
  return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower);
}

function runMeasurement() {
  measureButton.disabled = true;
  statsNode.textContent = 'Measuring scripted interactions for about five seconds…';
  const startedAt = performance.now();
  let previousFrame = startedAt;
  let lastAction = -1;
  const frameTimes = [];
  function frame(now) {
    frameTimes.push(now - previousFrame);
    previousFrame = now;
    const elapsed = now - startedAt;
    const action = Math.floor(elapsed / 140);
    if (action !== lastAction) {
      lastAction = action;
      const frequencyIndex = action % result.frequencies.length;
      const zoomWidth = 42;
      const start = (action * 3) % (100 - zoomWidth);
      charts.fr.dispatchAction({ type: 'dataZoom', start, end: start + zoomWidth });
      charts.fr.dispatchAction({ type: 'showTip', seriesIndex: action % 20, dataIndex: frequencyIndex });
      charts.heatmap.dispatchAction({
        type: 'showTip',
        seriesIndex: 0,
        dataIndex: (frequencyIndex * result.directivity[polarAxis][0].length + action) %
          (result.frequencies.length * result.directivity[polarAxis][0].length),
      });
      frequencySlider.value = String(frequencyIndex);
      renderPolar();
    }
    if (elapsed < 5000) {
      requestAnimationFrame(frame);
      return;
    }
    const measured = frameTimes.slice(1);
    const summary = {
      durationMs: elapsed,
      frames: measured.length,
      frameMs: {
        p50: percentile(measured, 50),
        p95: percentile(measured, 95),
        max: Math.max(...measured),
      },
    };
    statsNode.textContent = [
      'metric          p50 ms    p95 ms     max ms',
      '---------------------------------------------',
      `rAF frame ${summary.frameMs.p50.toFixed(2).padStart(13)} ${summary.frameMs.p95.toFixed(2).padStart(10)} ${summary.frameMs.max.toFixed(2).padStart(10)}`,
      `frames: ${summary.frames}  duration: ${summary.durationMs.toFixed(0)} ms`,
      JSON.stringify(summary),
    ].join('\n');
    console.log(JSON.stringify(summary));
    measureButton.disabled = false;
  }
  requestAnimationFrame(frame);
}

async function start() {
  const response = await fetch('/api/results/real');
  if (!response.ok) throw new Error(`real results request failed: HTTP ${response.status}`);
  result = await response.json();
  const axes = Object.keys(result.directivity || {});
  if (!axes.length) throw new Error('real result has no directivity axes');
  polarAxis = axes.includes('horizontal') ? 'horizontal' : axes[0];
  const count = result.frequencies.length;
  noteNode.textContent = `Largest real row: ${count} frequencies and ${axes.length} directivity axes. This probe cannot validate denser frequency sampling than the persisted row.`;
  frequencySlider.max = String(count - 1);
  frequencySlider.value = String(Math.floor(count / 2));
  renderFrequencyResponse();
  renderHeatmap();
  renderPolar();
  measureButton.disabled = false;
}

frequencySlider.addEventListener('input', renderPolar);
measureButton.addEventListener('click', runMeasurement);
window.addEventListener('resize', () => Object.values(charts).forEach((chart) => chart.resize()));
start().catch((error) => {
  noteNode.textContent = `Failed: ${error.message}`;
  statsNode.textContent = error.stack || String(error);
});
