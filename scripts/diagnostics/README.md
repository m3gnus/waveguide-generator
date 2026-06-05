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
npm run diag:mesher:reference-horn
npm run diag:mesher:closed
```

Generated files are written to `scripts/diagnostics/out/`.
