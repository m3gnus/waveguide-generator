/** Vite URLs in the browser; bytes in Node tests (no fetch/file URL polyfills). */
export async function wasmInput(url: string): Promise<string | Uint8Array> {
  if ((globalThis as { process?: { versions?: { node?: string } } }).process?.versions?.node) {
    const { readFileSync } = await import('node:fs');
    return readFileSync(url.startsWith('/@fs/') ? url.slice(4) : `.${url}`);
  }
  return url;
}
