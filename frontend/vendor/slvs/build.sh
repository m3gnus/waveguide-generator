#!/usr/bin/env bash
# Reproducible build of SolveSpace libslvs WASM (separate .wasm, small growable heap).
# Run from the builds/ directory:  slvs/build.sh [stripped|dwarf]
#   stripped (default): adds -g0 at link -> no DWARF, full post-link wasm-opt; output slvs/
#   dwarf: upstream's RelWithDebInfo link as-is (DWARF kept, limited wasm-opt); output slvs/with-dwarf/
# Expects src/solvespace = solvespace v3.2 (27b6a080c8b669421bd4d444650c3b8eddec5687) with
# submodules extlib/mimalloc and extlib/eigen initialised and slvs/slvs-wasm-packaging.patch applied.
set -euo pipefail
cd "$(dirname "$0")/.."
VARIANT="${1:-stripped}"
IMAGE='emscripten/emsdk:5.0.4@sha256:ef91f658e0104636cf40a702c99169273969cf04d939f4f08e5d0223965d5788'
case "$VARIANT" in
  stripped) OUT=slvs; LDFLAGS='-g0' ;;
  dwarf) OUT=slvs/with-dwarf; LDFLAGS='' ;;
  *) echo "unknown variant $VARIANT" >&2; exit 2 ;;
esac
mkdir -p "$OUT"
# mimalloc's options.c embeds __DATE__/__TIME__; pin them to the source commit time so that
# rebuilds are byte-identical (clang honours SOURCE_DATE_EPOCH).
SOURCE_DATE_EPOCH="$(git -C src/solvespace log -1 --format=%ct)"
docker run --rm -e SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" -e LDFLAGS_EXTRA="$LDFLAGS" -v "$PWD/src/solvespace:/src" -v "$PWD/$OUT:/out" -w /src "$IMAGE" bash -euo pipefail -c '
  emcc --version | head -1
  rm -rf build-wasmlib && mkdir build-wasmlib && cd build-wasmlib
  # same configure line as upstream .github/scripts/build-wasmlib.sh (+ optional linker flags)
  emcmake cmake .. \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DENABLE_GUI=OFF -DENABLE_CLI=OFF -DENABLE_TESTS=OFF -DENABLE_COVERAGE=OFF \
    -DENABLE_OPENMP=OFF -DFORCE_VENDORED_Eigen3=ON -DENABLE_LTO=ON \
    -DCMAKE_EXE_LINKER_FLAGS="$LDFLAGS_EXTRA" > /dev/null
  cmake --build . -j"$(nproc)" --target slvs-wasm -- VERBOSE=1 > /tmp/build.log 2>&1 || { tail -50 /tmp/build.log; exit 1; }
  grep -F -- "-o ../../bin/slvs.js" /tmp/build.log | sed "s#^.*/em++ #em++ #" > /out/link-command.txt
  grep -i "warning" /tmp/build.log | grep -v "Wunused\|Wfloat\|Wmissing" | sort -u | tail -5 || true
  cp bin/slvs.js bin/slvs.wasm /out/
'
cat "$OUT/link-command.txt"
cp src/solvespace/js/slvs.d.ts "$OUT/slvs.d.ts"
ls -l "$OUT/slvs.js" "$OUT/slvs.wasm"
shasum -a 256 "$OUT/slvs.js" "$OUT/slvs.wasm" "$OUT/slvs.d.ts"
