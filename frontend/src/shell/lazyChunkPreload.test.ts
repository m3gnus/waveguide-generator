import { describe, expect, it } from 'vitest';
import type { Rollup } from 'vite';
import { preloadTagsForBundle } from '../../vite.config.plugins';

function bundle(): Rollup.OutputBundle {
  return {
    'assets/index-aaa.js': { type: 'chunk', name: 'index', fileName: 'assets/index-aaa.js', isEntry: true },
    'assets/Viewport-bbb.js': {
      type: 'chunk', name: 'Viewport', fileName: 'assets/Viewport-bbb.js',
      viteMetadata: { importedCss: new Set(['assets/Viewport-ccc.css']) },
    },
    'assets/EChartRenderer-ddd.js': { type: 'chunk', name: 'EChartRenderer', fileName: 'assets/EChartRenderer-ddd.js' },
    'assets/index-eee.css': { type: 'asset', fileName: 'assets/index-eee.css', source: '' },
  } as unknown as Rollup.OutputBundle;
}

describe('lazy chunk preloading', () => {
  // Measured over localhost: the entry finished downloading at 30 ms and the
  // Viewport chunk was not requested until 100 ms, because nothing asks for it
  // until the entry has parsed and mounted.
  it('preloads the viewport chunk and the stylesheet it pulls in', () => {
    const tags = preloadTagsForBundle(bundle(), ['Viewport'], []);
    expect(tags).toEqual([
      { tag: 'link', attrs: { rel: 'modulepreload', crossorigin: '', href: '/assets/Viewport-bbb.js' }, injectTo: 'head' },
      { tag: 'link', attrs: { rel: 'preload', as: 'style', href: '/assets/Viewport-ccc.css' }, injectTo: 'head' },
    ]);
  });

  // Nothing needs the chart renderer until a solve has produced results, so it
  // must not compete with the viewport for bandwidth or for the parser.
  it('only prefetches the chart renderer', () => {
    const tags = preloadTagsForBundle(bundle(), [], ['EChartRenderer']);
    expect(tags).toEqual([
      { tag: 'link', attrs: { rel: 'prefetch', as: 'script', href: '/assets/EChartRenderer-ddd.js' }, injectTo: 'head' },
    ]);
  });

  it('stays silent when a named chunk is not in the bundle', () => {
    expect(preloadTagsForBundle(bundle(), ['Nonexistent'], ['AlsoMissing'])).toEqual([]);
  });
});
