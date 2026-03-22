#!/usr/bin/env node
// Bundle size gate script.
// Usage: node scripts/check-bundle-size.js [threshold-kib]
// Defaults: intermediate threshold 550 KiB, target threshold 500 KiB.
// Exits non-zero if bundle exceeds the threshold.

import { statSync, existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const BUNDLE_PATH = resolve(__dirname, '..', 'dist', 'bundle.js');

const INTERMEDIATE_THRESHOLD_KIB = 550;
const TARGET_THRESHOLD_KIB = 500;

const argThreshold = parseFloat(process.argv[2]);
const thresholdKib = Number.isFinite(argThreshold) ? argThreshold : INTERMEDIATE_THRESHOLD_KIB;
const thresholdBytes = thresholdKib * 1024;

if (!existsSync(BUNDLE_PATH)) {
  console.error(`[bundle-gate] Bundle not found at ${BUNDLE_PATH}`);
  console.error('[bundle-gate] Run `npm run build` first.');
  process.exit(1);
}

const { size } = statSync(BUNDLE_PATH);
const sizeKib = (size / 1024).toFixed(1);
const targetNote =
  thresholdKib === INTERMEDIATE_THRESHOLD_KIB
    ? ` (target: ${TARGET_THRESHOLD_KIB} KiB)`
    : '';

if (size > thresholdBytes) {
  console.error(
    `[bundle-gate] FAIL: bundle.js is ${sizeKib} KiB — exceeds ${thresholdKib} KiB threshold${targetNote}.`
  );
  process.exit(1);
} else {
  console.log(
    `[bundle-gate] PASS: bundle.js is ${sizeKib} KiB — within ${thresholdKib} KiB threshold${targetNote}.`
  );
}
