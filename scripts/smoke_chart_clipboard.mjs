// Native WebKit check using an already installed Playwright and browser. No downloads.
// Optional WG_PLAYWRIGHT_MODULE and WG_WEBKIT_EXECUTABLE point to existing installs.
// Run with Node 20: node scripts/smoke_chart_clipboard.mjs
//
// Chart images are drawn by hornlab-plots on the server, so a card's Copy PNG
// hands the clipboard a blob that is still being fetched. WebKit refuses a
// clipboard write that starts after an await, which is why `copyChartPng`
// starts the write inside the click with the pending blob. This checks that
// rule in a real WebKit, with a drawing that resolves a turn later, and that
// `saveChartPng` writes exactly the drawn bytes. `chartImage.smoke.test.ts`
// fails when this script calls a chartImage function that no longer exists.
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
  await writeFile(pageFile, '<!doctype html><button id="copy">Copy PNG</button><div id="paste" contenteditable="true">Paste here</div>');
  browser = await webkit.launch({ headless: true, timeout: 10_000,
    ...(process.env.WG_WEBKIT_EXECUTABLE ? { executablePath: process.env.WG_WEBKIT_EXECUTABLE } : {}) });
  const page = await browser.newPage();
  await page.goto(pathToFileURL(pageFile).href);
  await page.addScriptTag({ content: code });
  await page.evaluate(async () => {
    // Stand-in for the hornlab-plots image: a PNG of one colour, ready only
    // after an event-loop turn, as a server render would be.
    const drawn = (color) => new Promise((resolve) => setTimeout(() => {
      const canvas = document.createElement('canvas'); canvas.width = 8; canvas.height = 4;
      const context = canvas.getContext('2d'); context.fillStyle = color; context.fillRect(0, 0, 8, 4);
      canvas.toBlob(resolve, 'image/png');
    }, 50));
    // Export: the bytes written are the bytes drawn.
    const expected = await drawn('#123456');
    globalThis.exportMatches = false;
    globalThis.fetch = async (_url, { body }) => {
      const file = body.get('file');
      const a = new Uint8Array(await expected.arrayBuffer());
      const b = new Uint8Array(await file.arrayBuffer());
      globalThis.exportMatches = file.name === 'A.png' && a.length === b.length && a.every((value, i) => value === b[i]);
      return new Response(JSON.stringify({ directory: 'chosen', files: ['A.png'] }));
    };
    await chartImage.saveChartPng(Promise.resolve(expected), 'A.png', 'chosen');

    // Copy: the clipboard write starts in the click, the image lands later.
    globalThis.copyResult = null;
    document.querySelector('#copy').onclick = () => {
      chartImage.copyChartPng(() => drawn('#fedcba')).then(() => { globalThis.copyResult = 'copied'; },
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
  assert.equal(await page.evaluate(() => globalThis.exportMatches), true, 'Exported PNG bytes and filename must be the drawn ones');
  await page.click('#copy');
  await page.waitForFunction(() => globalThis.copyResult !== null, null, { timeout: 5_000 });
  assert.equal(await page.evaluate(() => globalThis.copyResult), 'copied');
  await page.click('#paste');
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+V' : 'Control+V');
  await page.waitForFunction(() => globalThis.pastePixels !== null, null, { timeout: 5_000 });
  assert.deepEqual(await page.evaluate(() => globalThis.pastePixels), [254, 220, 186, 255]);
  console.log('2 checks passed: exported PNG bytes are the drawn ones; native WebKit copy/paste of a PNG drawn after the click.');
} finally {
  if (browser) await browser.close();
  await rm(temporary, { recursive: true, force: true });
}
