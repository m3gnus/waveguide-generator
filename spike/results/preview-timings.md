# Preview timing spike

Generated: `2026-08-03T12:08:32.846905+00:00`  
Platform: `macOS-26.5.2-arm64-arm-64bit-Mach-O`  
Python: `3.13.1`  
Warm iterations per case: `40`

Cold process includes interpreter startup, imports, payload load, and the first viewport call. Import and first-call evaluation are also shown separately. Frame encoding includes point-grid tessellation plus WGF0 serialization.

| Family | LOD | Grid | Cold process ms | Cold import ms | Cold eval ms | Warm p50 ms | Warm p95 ms | Warm max ms | Frame p50 ms | Frame bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| OSSE | coarse | 20×4 | 141.46 | 88.35 | 1.82 | 0.94 | 0.98 | 1.01 | 0.05 | 3350 |
| OSSE | fine | 100×48 | 179.01 | 82.82 | 45.52 | 43.90 | 194.30 | 269.10 | 2.70 | 174238 |
| R-OSSE | coarse | 8×4 | 176.71 | 109.76 | 2.01 | 0.49 | 0.73 | 0.79 | 0.06 | 2900 |
| R-OSSE | fine | 96×48 | 206.15 | 98.92 | 42.64 | 33.21 | 37.64 | 44.29 | 5.12 | 334507 |
| ICW | coarse | 8×4 | 488.55 | 93.80 | 315.00 | 0.58 | 0.68 | 0.74 | 0.03 | 1475 |
| ICW | fine | 96×48 | 475.07 | 90.05 | 307.84 | 4.54 | 4.91 | 5.05 | 2.40 | 167277 |
| FREEFORM | coarse | 20×5 | 460.96 | 93.38 | 293.22 | 2.83 | 3.07 | 3.07 | 0.06 | 4071 |
| FREEFORM | fine | 96×49 | 465.46 | 94.27 | 298.87 | 13.76 | 14.33 | 14.49 | 2.59 | 170734 |
