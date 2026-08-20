# ECharts live-update benchmark

This browser benchmark exercises the production `heatmapOption` and
`lineOption` builders through the same ECharts canvas registration and live
animation settings as `EChartRenderer`. The workload is one bounded live
directivity map (up to 8,687 cells) and three line charts, with solve-shaped
snapshots growing from 12 to 60 frequencies. Each chart is 480 × 300 CSS pixels
at a 2× backing-canvas ratio.

Run it from `frontend`:

```sh
npm run benchmark:echarts
```

The page prints synchronous `setOption` time and end-to-end time through the
ECharts `finished` event. It compares the production wholesale replacement
against ordinary merge and `replaceMerge: ['series']`. The first pass warms the
JIT and canvas path; the reported distribution uses the following two passes.
Option construction is deliberately outside the timing because this benchmark
isolates the renderer update named in the backlog.

## Baseline decision

Representative run on 2026-08-20 in Chromium 151 on macOS, with the browser at
device pixel ratio 2:

| update strategy | dashboard painted median | dashboard painted p95 | synchronous p95 | zoom after update |
| --- | ---: | ---: | ---: | ---: |
| `notMerge: true` (production) | 28.3 ms | 41.6 ms | 4.3 ms | 0–100% |
| merge | 27.8 ms | 41.7 ms | 4.5 ms | 30–70% |
| `replaceMerge: ['series']` | 27.4 ms | 41.0 ms | 3.9 ms | 30–70% |

The current path consumes 16.6% of the 250 ms live publication interval at p95,
and neither incremental candidate improves it. The dominant work is repainting
the changing heatmap cells, which all three strategies must do. A generic merge
also risks retaining series that disappear between options, while the retained
data-zoom window has previously clipped a new result. Therefore the profile
does not justify changing the renderer. Completed results continue to use the
known-correct wholesale replacement path, and live results keep the same path
with animation disabled.
