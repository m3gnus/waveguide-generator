// Native WebKit check using an already installed Playwright and browser. No downloads.
// Optional WG_PLAYWRIGHT_MODULE and WG_WEBKIT_EXECUTABLE point to existing installs.
// Run with Node 20: node scripts/smoke_chart_clipboard.mjs
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const frontend = createRequire(new URL('../frontend/package.json', import.meta.url));
const { webkit } = frontend(process.env.WG_PLAYWRIGHT_MODULE || 'playwright');
const { rolldown } = await import(pathToFileURL(frontend.resolve('rolldown')).href);
const bundle = await rolldown({ input: fileURLToPath(new URL('../frontend/src/results/chartImage.ts', import.meta.url)), platform: 'browser' });
let code;
try { ({ output: [{ code }] } = await bundle.generate({ format: 'iife', name: 'chartImage' })); }
finally { await bundle.close(); }

const temporary = await mkdtemp(join(tmpdir(), 'wg-chart-clipboard-'));
let browser;
try {
  const pageFile = join(temporary, 'clipboard.html');
  await writeFile(pageFile, '<!doctype html><button id="copy">Copy PNG</button><div id="chart" style="width:80px;height:40px"><canvas width="160" height="80" style="width:80px;height:40px"></canvas></div><div id="paste" contenteditable="true">Paste here</div>');
  browser = await webkit.launch({ headless: true, timeout: 10_000,
    ...(process.env.WG_WEBKIT_EXECUTABLE ? { executablePath: process.env.WG_WEBKIT_EXECUTABLE } : {}) });
  const page = await browser.newPage();
  await page.goto(pathToFileURL(pageFile).href);
  await page.addScriptTag({ content: code });
  await page.evaluate(async () => {
    const chart = document.querySelector('#chart');
    const layer = chart.querySelector('canvas');
    const context = layer.getContext('2d');
    context.fillStyle = '#123456'; context.fillRect(0, 0, layer.width, layer.height);
    // Both operations use the production composition owner. Capture A and
    // redraw its retained layer as B before writing the captured image.
    const captured = chartImage.composeChartCanvases(chart, '#fff');
    const expected = await chartImage.chartPngBlob(chart, '#fff');
    context.fillStyle = '#fedcba'; context.fillRect(0, 0, layer.width, layer.height);
    globalThis.exportMatches = false;
    globalThis.fetch = async (_url, { body }) => {
      const file = body.get('file');
      const a = new Uint8Array(await expected.arrayBuffer());
      const b = new Uint8Array(await file.arrayBuffer());
      globalThis.exportMatches = file.name === 'A.png' && a.length === b.length && a.every((value, i) => value === b[i]);
      return new Response(JSON.stringify({ directory: 'chosen', files: ['A.png'] }));
    };
    await chartImage.saveChartPng(captured, 'A.png', 'chosen');

    const nativeToBlob = HTMLCanvasElement.prototype.toBlob;
    // Keep native PNG encoding pending across an event-loop turn.
    HTMLCanvasElement.prototype.toBlob = function (callback, ...args) {
      setTimeout(() => nativeToBlob.call(this, callback, ...args), 50);
    };
    globalThis.copyResult = null;
    document.querySelector('#copy').onclick = () => {
      chartImage.copyChartPng(chart, '#fff').then(() => { globalThis.copyResult = 'copied'; },
        (error) => { globalThis.copyResult = `${error.name}: ${error.message}`; });
    };
    globalThis.pastePixels = null;
    document.querySelector('#paste').onpaste = async (event) => {
      event.preventDefault();
      const file = [...event.clipboardData.files].find((item) => item.type === 'image/png');
      if (!file) { globalThis.pastePixels = 'No PNG on clipboard'; return; }
      const url = URL.createObjectURL(file);
      try {
        const image = new Image(); image.src = url; await image.decode();
        const output = document.createElement('canvas'); output.width = image.width; output.height = image.height;
        const pixels = output.getContext('2d'); pixels.drawImage(image, 0, 0);
        globalThis.pastePixels = [...pixels.getImageData(0, 0, 1, 1).data];
      } finally { URL.revokeObjectURL(url); }
    };
  });
  assert.equal(await page.evaluate(() => globalThis.exportMatches), true, 'Captured A PNG bytes and filename must remain identical');
  await page.click('#copy');
  await page.waitForFunction(() => globalThis.copyResult !== null, null, { timeout: 5_000 });
  assert.equal(await page.evaluate(() => globalThis.copyResult), 'copied');
  await page.click('#paste');
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+V' : 'Control+V');
  await page.waitForFunction(() => globalThis.pastePixels !== null, null, { timeout: 5_000 });
  assert.deepEqual(await page.evaluate(() => globalThis.pastePixels), [254, 220, 186, 255]);
  console.log('2 checks passed: identical captured PNG bytes; native WebKit PNG copy/paste with deferred encoding.');
} finally {
  if (browser) await browser.close();
  await rm(temporary, { recursive: true, force: true });
}
