# slvs WASM build (SolveSpace libslvs 3.2)

Custom WebAssembly build of SolveSpace's constraint solver library (`slvs`), for the WG
CAD sketcher. Vendored here as the `slvs` dependency (`file:vendor/slvs`). To rebuild,
make an empty work directory with a `slvs/` folder holding `build.sh` and the patch from here,
then follow "Reproduce" below. Licence: GPL-3.0-or-later (SolveSpace). Built 2026-10-18.

Compared with the npm `slvs@3.2.0` package, only packaging and memory flags changed. The
solver source is unchanged.

## Source

| Item | Value |
|---|---|
| Repository | https://github.com/solvespace/solvespace |
| Tag | `v3.2` (lightweight tag) |
| Commit | `27b6a080c8b669421bd4d444650c3b8eddec5687` (2026-03-26, "fix windows single_core paths") |
| Submodule `extlib/mimalloc` | `f81bf1b31af819a31195e08f9546dc80f8931587` (v2.2.2) |
| Submodule `extlib/eigen` | `3147391d946bb4b6c68edd901f2add6ac1f31f8c` (3.4.0) |
| Source change | `slvs-wasm-packaging.patch` (one hunk in `src/slvs/CMakeLists.txt`) |

Notes on the npm package:
- npm `slvs@3.2.0` was published on 2026-03-25 19:13 UTC. That is before the commit the
  `v3.2` tag now points to, so the tag was probably moved after publishing.
- The commits in between (`0a811f31..27b6a080`) change only CI workflows, README and
  `pyproject.toml`. Nothing under `src/`, `include/`, `extlib/`, `cmake/` or
  `CMakeLists.txt` differs.

## Toolchain

| Item | Value |
|---|---|
| Image | `emscripten/emsdk:5.0.4@sha256:ef91f658e0104636cf40a702c99169273969cf04d939f4f08e5d0223965d5788` (pulled as linux/arm64) |
| emcc | 5.0.4 (`62e22652509fbe7a00609ce48a653d0d66f27ba5`) |
| `SOURCE_DATE_EPOCH` | the commit time of the source, `1774563433` |

**Why emsdk 5.0.4.** Upstream's `.github/scripts/build-wasmlib.sh` does not pin a
version. It clones emsdk and runs `./emsdk install latest`. When npm 3.2.0 was published,
the latest emsdk release was 5.0.4 (Docker Hub tag dated 2026-03-23; 5.0.5 followed on
2026-04-03). This is an inference from release dates: the npm JS does not record the
compiler version. Two observations support it:
- The upstream-equivalent variant below produces a `.wasm` of 5,447,598 bytes.
- The `.wasm` embedded in npm `slvs.js` is 5,453,353 bytes.

**Why `SOURCE_DATE_EPOCH`.** mimalloc's `src/options.c` embeds `__DATE__`/`__TIME__`.
Without a fixed `SOURCE_DATE_EPOCH`, two rebuilds give different `.wasm` bytes. With it,
two rebuilds of the stripped variant gave the same sha256.

## Flags changed vs upstream

Configure is the same as upstream `build-wasmlib.sh`:

```
-DCMAKE_BUILD_TYPE=RelWithDebInfo -DENABLE_GUI=OFF -DENABLE_CLI=OFF -DENABLE_TESTS=OFF
-DENABLE_COVERAGE=OFF -DENABLE_OPENMP=OFF -DFORCE_VENDORED_Eigen3=ON -DENABLE_LTO=ON
```

Link options of target `slvs-wasm` (`src/slvs/CMakeLists.txt`):

| Upstream | This build |
|---|---|
| `-s MODULARIZE=1` | unchanged |
| `-s EXPORT_NAME=solvespace` | unchanged |
| `-s SINGLE_FILE=1` | **removed**, so a separate `slvs.wasm` is emitted |
| (UMD/CommonJS export) | **`-s EXPORT_ES6=1`** added |
| `-s INITIAL_MEMORY=512MB` | **`-s INITIAL_MEMORY=16MB`** |
| `-s ALLOW_MEMORY_GROWTH` | `-s ALLOW_MEMORY_GROWTH=1` (same meaning) |
| `-O3 -flto --closure 1` | unchanged |
| (none) | **`-g0`** via `CMAKE_EXE_LINKER_FLAGS`, default "stripped" variant only |

**Where the large heap came from.** The 0.5–1.25 GB heap is set in
`src/slvs/CMakeLists.txt`, which has `"SHELL:-s INITIAL_MEMORY=512MB"`. Measured: the npm
`slvs.js` has a 512 MiB heap straight after loading. Growth on top of that explains the
larger figures seen in the Phase 0 bench.

**ES6 is not what upstream ships.** Upstream does **not** use `EXPORT_ES6`: the npm file
ends in a UMD/CommonJS export. ES6 was chosen so that:
- the default `.wasm` locator is `new URL("slvs.wasm", import.meta.url)`, which Vite
  bundles;
- the export matches upstream `js/slvs.d.ts` (`export default ModuleLoader`).

The consequence is that `require('slvs')` from CommonJS no longer works.

**What `-g0` does, and why there are two variants.** Upstream's `RelWithDebInfo` also links
with `-g`. That has two effects, and the npm build has both:
- the `.wasm` keeps about 5.1 MB of DWARF sections;
- emcc prints `running limited binaryen optimizations because DWARF info requested`.

`-g0` at link drops the DWARF, so the full post-link `wasm-opt` runs. It changes no
compile flags: objects are still compiled `-O2 -g` and linked with LTO at `-O3`.

So there are two variants:
- `slvs.js` + `slvs.wasm`: stripped (default, recommended).
- `with-dwarf/`: upstream link flags with only the packaging and memory changes, for
  provenance comparison. Its `slvs.wasm` rebuilds byte-identically. Its `slvs.js` varies
  between rebuilds in closure temporary variable names only (`$jscomp$logical$assign$tmpNNN`).

Full link commands are in `link-command.txt` and `with-dwarf/link-command.txt`.

## Outputs

| File | Bytes | sha256 |
|---|---|---|
| `slvs.js` | 24,089 | `0d1bf81dc570205a5b4edd66b7948542e852c47c29bceab7378fdd6a2938584f` |
| `slvs.wasm` | 275,467 (gzip -9: 105,642) | `d8b3489c59e8c41feec64363b273766ee598a742f6f03bd1d5844df1fde42f75` |
| `slvs.d.ts` (upstream `js/slvs.d.ts`, unmodified) | 6,673 | `2e25cf064e7aaab89ef51d845845f88ef63981504770802905352e0552a784c0` |
| `with-dwarf/slvs.js` | 99,811 | `4ec68f68…` (varies, see above) |
| `with-dwarf/slvs.wasm` | 5,447,598 | `4be1f8972bc2bb2bb2c9837a741943085a0bf7fe9a323ea65c6b86aaa7a256c4` |

For comparison, npm `slvs@3.2.0` ships a single `slvs.js` of 6,149,995 bytes (about 6.1 MB
on disk; the Phase 0 report called it 7.2 MB). It embeds a `.wasm` of 5,453,353 bytes.

## Memory

| | Initial | Maximum |
|---|---|---|
| This build | 16 MiB | 2048 MiB (emscripten default with growth) |
| npm 3.2.0 | 512 MiB (measured after load) | growable |

Smoke-test probes, in the same process:
- after a 60-segment chain (20 solves) the heap was 36.1 MiB;
- after a 256-segment chain (3 solves) it was still 36.1 MiB.

## Smoke test (Node 20.20.2)

`node slvs/smoke-test.mjs [dir]` (output in `smoke-test-output.txt`):

```
wasm memory limits: initial 16.0 MiB, max 2048.0 MiB
module load: 9.5 ms; heap after load 16.0 MiB
line: result=0 dof=2 nbad=0
  p0=(-10.000000, 7.000000) p1=(40.000000, 7.000000)
  length=50.000000000000 dy=0.00e+0 -> PASS
chain n=60: 20 solves, last result=0 dof=60, 1.4 ms/solve avg (incl. build), heap 36.1 MiB
chain n=256: 3 solves, last result=0 dof=256, 15.4 ms/solve avg (incl. build), heap 36.1 MiB
```

The test is a 2-point line with `horizontal` and `distance = 50`. Neither point is
dragged, so both move, and DOF = 4 − 2 = 2.

The `with-dwarf/` variant gives the same results.

**Quick solve timing.** This is an open chain of distance-constrained lines, not the
Phase 0 corpus. The median `solveSketch` time was the same for npm, stripped and
with-dwarf:
- 0.67 ms at 60 segments;
- 13.9–14.2 ms at 256 segments.

So in this probe the full `wasm-opt` pass did not change speed.

## Reproduce

From the `builds/` directory:

```
git clone https://github.com/solvespace/solvespace src/solvespace
git -C src/solvespace checkout 27b6a080c8b669421bd4d444650c3b8eddec5687
git -C src/solvespace submodule update --init extlib/mimalloc extlib/eigen
git -C src/solvespace apply "$PWD/slvs/slvs-wasm-packaging.patch"
slvs/build.sh            # stripped -> slvs/
slvs/build.sh dwarf      # upstream link flags -> slvs/with-dwarf/
```

**Compute broker.** Job `261018-105905-compute-00e3` (rc=0, 0.6 min) ran the first build,
with DWARF and before `SOURCE_DATE_EPOCH` was added. The later rebuilds, of both variants,
take about 30 s each and ran directly.

## Not done

- No WKWebView or WebView2 run. No re-run of the Phase 0 bench against this build.
- The duplicate-constraint DOF bug from Phase 0 was not touched.
