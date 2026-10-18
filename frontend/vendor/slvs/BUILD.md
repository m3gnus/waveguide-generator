# slvs WASM build (SolveSpace libslvs 3.2, hornlab.2)

This is our WebAssembly build of SolveSpace's constraint solver library, used by the WG
sketcher through `file:vendor/slvs`. Licence: GPL-3.0-or-later, see `COPYING.txt`.

Compared with upstream v3.2 it makes two kinds of change, both in `slvs-hornlab.patch`:
- **packaging:** a separate `.wasm` file, an ES module, a 16 MiB initial heap and an
  exported `HEAPU8`;
- **a memory-leak fix in `Slvs_SolveSketch`.** The solver algorithms are unchanged.

## Source

| Item | Value |
|---|---|
| Repository | https://github.com/solvespace/solvespace |
| Tag | `v3.2` = `27b6a080c8b669421bd4d444650c3b8eddec5687` |
| Submodules | `extlib/mimalloc` `f81bf1b31af819a31195e08f9546dc80f8931587` (v2.2.2), `extlib/eigen` `3147391d946bb4b6c68edd901f2add6ac1f31f8c` (3.4.0) |
| Our changes | `slvs-hornlab.patch` (`src/slvs/CMakeLists.txt`, `src/slvs/lib.cpp`) |

Upstream's npm `slvs@3.2.0` was published shortly before the commit the tag now names.
The commits in between change only CI, the README and `pyproject.toml`.

## Toolchain

| Item | Value |
|---|---|
| Image | `emscripten/emsdk:5.0.4@sha256:ef91f658e0104636cf40a702c99169273969cf04d939f4f08e5d0223965d5788` (built on linux/arm64; amd64 byte identity not verified) |
| `SOURCE_DATE_EPOCH` | `1774563433` (the source commit time; mimalloc embeds `__DATE__`/`__TIME__`) |

Upstream's `build-wasmlib.sh` installs emsdk `latest`. Version 5.0.4 was the latest
release when npm 3.2.0 was published. Rebuilding with a fixed `SOURCE_DATE_EPOCH` gives
byte-identical `.wasm` output.

## Changes in `slvs-hornlab.patch`

**Link options of `slvs-wasm`.** The configure step is the same as upstream's
`RelWithDebInfo` wasmlib build.

| Upstream | This build | Why |
|---|---|---|
| `-s SINGLE_FILE=1` | removed | Ship a separate 276 KB `.wasm` instead of a 6.1 MB JS file with the WASM inlined. |
| UMD/CommonJS export | `-s EXPORT_ES6=1` | Vite resolves `new URL("slvs.wasm", import.meta.url)`, and the module matches `slvs.d.ts`. CommonJS `require` no longer works. |
| `-s INITIAL_MEMORY=512MB` | `-s INITIAL_MEMORY=16MB` | Upstream reserves 512 MiB at load. Growth stays on. |
| — | `-s EXPORTED_RUNTIME_METHODS=['HEAPU8']` | Lets callers read the heap size. |
| link `-g` | `-g0`, set in `build.sh` | Drops about 5.1 MB of DWARF, and lets the full post-link `wasm-opt` run. |

**`Slvs_SolveSketch` leak fix (`lib.cpp`).** The stateful API that the JS binding uses
never released the memory of each solve:
- its `badList`;
- the system's equation lists;
- the temporary arena holding the generated expression trees.

So every `solveSketch` call leaked memory, including drag frames. Measured with the
unpatched build:
- 30,000 drag frames on an 8-segment chain grew the heap from 36 to 373 MiB;
- rebuild-and-solve loops ran out of memory at the 2 GiB cap.

The legacy `Slvs_Solve` already frees all three. The fix does the same at the end of
`Slvs_SolveSketch`. Persistent sketch state (`SK.*` and the dragged set) is untouched,
and constraint parameters live in `SK.param`, so nothing outlives the arena.

After the fix, the heap stays at 36.2 MiB over:
- 30,000 drag frames;
- 20,000 rebuild-and-solve cycles, with and without arcs;
- 3,000 rebuilds of a 60-segment chain.

WG's solver conformance suite gives the same results as before.

## Outputs

| File | Bytes | sha256 |
|---|---|---|
| `slvs.js` | 24,098 | `3965602328c5591d2c911e12de2cabdb85b9c54ea9a6c8cd78af2d2fe865c3e3` |
| `slvs.wasm` | 275,552 (gzip -9: 105,642) | `c12fcaa40db42e710cbe263ddef1d9b9d3aa0d31e404da7f25803ceed9686ef5` |
| `slvs.d.ts` (upstream `js/slvs.d.ts`, unmodified) | 6,673 | `2e25cf064e7aaab89ef51d845845f88ef63981504770802905352e0552a784c0` |
| `slvs-hornlab.patch` | — | `6b02e0386ea88384214140c97a1d5907974e9bbec0e6e4952f2e9262dff971b1` |

## Reproduce

In an empty work directory, with Docker:

```
mkdir slvs && cp <this folder>/build.sh <this folder>/slvs-hornlab.patch slvs/
git clone https://github.com/solvespace/solvespace src/solvespace
git -C src/solvespace checkout 27b6a080c8b669421bd4d444650c3b8eddec5687
git -C src/solvespace submodule update --init extlib/mimalloc extlib/eigen
git -C src/solvespace apply "$PWD/slvs/slvs-hornlab.patch"
bash slvs/build.sh stripped     # writes slvs/slvs.js, slvs/slvs.wasm, slvs/slvs.d.ts
```

Then compare the sha256 values with the table above and copy the three files here. The
build takes about 30 seconds.

## Known upstream defects not fixed here

- **Wrong DOF for a duplicated constraint.** It reports 2 instead of 3. WG's conformance
  suite records this as an expected failure, and the sketcher dedupes identical
  constraints.
- **Unreachable drag targets.** The projection misses the nearest point on the locus by
  up to 0.016 mm.
