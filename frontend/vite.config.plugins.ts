import type { Plugin, Rollup } from 'vite';

type OutputBundle = Rollup.OutputBundle;
type OutputChunk = Rollup.OutputChunk;

export interface HtmlTag {
  tag: string;
  attrs: Record<string, string>;
  injectTo: 'head';
}

/**
 * Link tags that start the lazy chunks downloading with the entry, not after it.
 *
 * `index.html` links only the entry bundle, so the Viewport chunk — three.js,
 * fiber and drei — is not even requested until the entry has downloaded,
 * parsed and mounted. Measured in a real browser over localhost: the entry
 * finished at 30 ms and the Viewport chunk did not start until 100 ms, then
 * took 23 ms of its own. A `modulepreload` overlaps that fetch with the
 * entry's, which is pure win on a machine where parsing 711 kB is the slow
 * part; after the change every asset started within 14 ms and the Viewport
 * chunk was resident at 51 ms.
 *
 * The chart renderer gets `prefetch` rather than `modulepreload`: nothing needs
 * it until a solve has produced results, so it must never compete with the
 * viewport for bandwidth or for the parser.
 */
export function preloadTagsForBundle(
  bundle: OutputBundle,
  preload: readonly string[],
  prefetch: readonly string[],
): HtmlTag[] {
  const chunkFor = (name: string): OutputChunk | undefined => Object.values(bundle)
    .find((file): file is OutputChunk => file.type === 'chunk' && file.name === name);
  const tags: HtmlTag[] = [];
  for (const name of preload) {
    const chunk = chunkFor(name);
    if (!chunk) continue;
    tags.push({ tag: 'link', attrs: { rel: 'modulepreload', crossorigin: '', href: `/${chunk.fileName}` }, injectTo: 'head' });
    for (const style of chunk.viteMetadata?.importedCss ?? []) {
      tags.push({ tag: 'link', attrs: { rel: 'preload', as: 'style', href: `/${style}` }, injectTo: 'head' });
    }
  }
  for (const name of prefetch) {
    const chunk = chunkFor(name);
    if (!chunk) continue;
    tags.push({ tag: 'link', attrs: { rel: 'prefetch', as: 'script', href: `/${chunk.fileName}` }, injectTo: 'head' });
  }
  return tags;
}

export function lazyChunkPreloader(
  preload: readonly string[] = ['Viewport'],
  prefetch: readonly string[] = ['EChartRenderer'],
): Plugin {
  return {
    name: 'wg2:preload-lazy-chunks',
    transformIndexHtml: {
      order: 'post',
      handler(html, context) {
        if (!context.bundle) return html;
        return { html, tags: preloadTagsForBundle(context.bundle, preload, prefetch) };
      },
    },
  };
}
