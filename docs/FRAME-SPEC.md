# FRAME-SPEC v1 — binary geometry/array frames

Status: Phase 1 contract (implements plan §4.3/§4.5; supersedes spike `WGF0`).
Used by: `/ws/preview` geometry frames; later by measured-large HTTP result fields (balloon chunks). `.msh` artifacts are NOT frames — they stay original streamed text files.

## Layout (little-endian throughout)

```
offset  size  field
0       4     magic = "WGF1"
4       4     u32 headerLength (bytes of UTF-8 JSON that follow)
8       H     JSON header
8+H     …     payload sections, each 8-byte aligned from payload base
```

Payload base = `8 + headerLength` rounded up to 8. Sections are contiguous, in header order, each padded to 8-byte alignment.

## JSON header

```json
{
  "v": 1,
  "kind": "preview" | "curve" | "result-chunk",
  "epoch": 3,
  "seq": 412,
  "designRevision": 57,
  "lod": "coarse",
  "evalMs": 2.1,
  "sections": [
    {"name": "positions", "dtype": "f32", "shape": [1764, 3], "byteOffset": 0,   "byteLength": 21168},
    {"name": "indices",   "dtype": "u32", "shape": [10080],  "byteOffset": 21168, "byteLength": 40320}
  ]
}
```

- `dtype` ∈ `f32 | f64 | u32 | u16 | u8 | i32`. Preview geometry is always `f32` positions + `u32` indices.
- Section names for `preview`: `positions`, `indices`, optional `outerPositions`, `outerIndices`, optional `sectionCurves` (f32, [n,3] polyline concat) + `sectionCurveOffsets` (u32). Unknown extra sections MUST be ignored by decoders (forward compatibility).
- `kind:"result-chunk"` adds `"jobId"`, `"field"` (e.g. `balloon`), `"chunk"` (e.g. frequency index) — spec'd now, implemented when measurement justifies (plan §4.5).

## Validation rules (both languages, enforced, fuzz-tested)

Decoders MUST reject (error, never crash):
1. Bad magic or `v` major ≠ supported.
2. `headerLength` > 64 KiB or beyond message end.
3. Header JSON invalid, or `sections` missing/not a list.
4. Any section: unknown dtype; `byteOffset`/`byteLength` outside payload; overlap with another section; `byteLength ≠ prod(shape) × dtypeSize`; shape values < 0 or product > 2^28 elements.
5. Total message > negotiated `maxFrameBytes` (from WS `hello`; default 32 MiB).
6. For `preview`: `indices` values ≥ `positions` row count (spot-check first/last 1024 entries at decode; full check in tests only — hot path stays O(1)-ish).

Encoders MUST: emit sections in header order, 8-byte aligned, exact lengths; never emit NaN/Inf in `positions` (server validates before send; a geometry that produces non-finite values is a `validation` error on the WS channel, not a frame).

## Language bindings

- Python: `server/protocol/frame.py` — `encode(header_fields, arrays: dict[str, np.ndarray]) -> bytes`, `decode(bytes) -> (Header, dict[str, np.ndarray])` (zero-copy views into the input buffer).
- TypeScript: `frontend/src/api/frame.ts` — `decodeFrame(buf: ArrayBuffer): {header, sections: Record<string, TypedArray>}` returning typed-array **views** (no copies); encode only needed in tests.
- Shared fixtures: `shared/frame-fixtures/*.bin` + expected JSON, round-tripped by both languages in CI; plus a malformed-frame corpus (each validation rule above violated once) that both decoders must reject.

## Performance notes (spike-measured ✓)

Decode cost at fine-LOD sizes (170–335 KB): ~0.1 ms in browser. Alignment + views make GPU upload allocation-free when sizes are unchanged. Fine OSSE frame ≈ 174 KB at 96×48 — at 30 Hz worst case ≈ 5 MB/s localhost, negligible.
