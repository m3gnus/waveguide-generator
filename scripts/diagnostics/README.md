# Diagnostics Scripts

These scripts are ad-hoc debugging helpers. They are not part of `npm test` or `npm run test:server`.

## Node.js diagnostics

Run from repository root:

```bash
node scripts/diagnostics/build-canonical-payload-from-reference.js
node scripts/diagnostics/check-reference-horn-geometry-artifacts.js
```

## Python diagnostics

Run from repository root:

```bash
python3 scripts/diagnostics/build-occ-mesh-reference-horn.py
python3 scripts/diagnostics/check-occ-closed-mesh.py
```

Generated files are written to `scripts/diagnostics/out/`.
