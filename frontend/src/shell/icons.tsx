import type { SVGProps } from 'react';

type IconName = 'undo' | 'redo' | 'search' | 'play' | 'moon' | 'sun' | 'layout' | 'settings' | 'reset' | 'chip' | 'folder' | 'clay' | 'wire' | 'xray' | 'zebra' | 'curve' | 'section' | 'box' | 'close' | 'expand' | 'metrics';

const paths: Record<IconName, string[]> = {
  undo: ['M6 4.2 3.1 7.1 6 10', 'M3.1 7.1h6.1a3.7 3.7 0 0 1 0 7.4H6.4'],
  redo: ['m10 4.2 2.9 2.9L10 10', 'M12.9 7.1H6.8a3.7 3.7 0 0 0 0 7.4h2.8'],
  search: ['M11.1 11.1 14 14', 'M12 7.2a4.8 4.8 0 1 1-9.6 0 4.8 4.8 0 0 1 9.6 0Z'],
  play: ['M5.6 3.6 12 8l-6.4 4.4Z'],
  moon: ['M13.4 9.8A5.8 5.8 0 0 1 6.2 2.6a5.9 5.9 0 1 0 7.2 7.2Z'],
  sun: ['M11 8a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z', 'M8 1.4V3M8 13v1.6M1.4 8H3M13 8h1.6M3.5 3.5l1.1 1.1m6.8 6.8 1.1 1.1m0-9-1.1 1.1m-6.8 6.8-1.1 1.1'],
  layout: ['M2 3h12v10H2Z', 'M6.2 3v10M11 6.4h3'],
  settings: ['M6.4 1.8h3.2l.35 1.45c.38.14.74.35 1.06.61l1.42-.43 1.6 2.76-1.08 1.02c.03.26.05.52.05.79s-.02.53-.05.79l1.08 1.02-1.6 2.76-1.42-.43c-.32.26-.68.47-1.06.61L9.6 14.2H6.4l-.35-1.45a5.2 5.2 0 0 1-1.06-.61l-1.42.43-1.6-2.76 1.08-1.02A6 6 0 0 1 3 8c0-.27.02-.53.05-.79L1.97 6.19l1.6-2.76 1.42.43c.32-.26.68-.47 1.06-.61Z', 'M10.25 8A2.25 2.25 0 1 1 5.75 8a2.25 2.25 0 0 1 4.5 0Z'],
  reset: ['M3 8a5 5 0 1 0 1.6-3.65', 'M2.8 2.4v3.2H6'],
  chip: ['M4.4 4.4h7.2v7.2H4.4Z', 'M6.6 2v2.4M9.4 2v2.4M6.6 11.6V14M9.4 11.6V14M2 6.6h2.4M2 9.4h2.4m7.2-2.8H14m-2.4 2.8H14'],
  folder: ['M1.8 12.4V4.6h4L7 6.2h7.2v6.2Z'],
  clay: ['M13.4 8A5.4 5.4 0 1 1 2.6 8a5.4 5.4 0 0 1 10.8 0Z'],
  wire: ['M13.4 8A5.4 5.4 0 1 1 2.6 8a5.4 5.4 0 0 1 10.8 0Z', 'M8 2.6c3.3 2.8 3.3 8 0 10.8M8 2.6C4.7 5.4 4.7 10.6 8 13.4M2.6 8h10.8'],
  xray: ['M13.4 8A5.4 5.4 0 1 1 2.6 8a5.4 5.4 0 0 1 10.8 0Z', 'M10 8a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z'],
  zebra: ['M13.4 8A5.4 5.4 0 1 1 2.6 8a5.4 5.4 0 0 1 10.8 0Z', 'M4.1 4.6 6 12.6M6.9 3.2 9.2 13M9.8 3.6l2 8.5'],
  curve: ['M2.6 10.7c2.6.9 5.1-.5 6.4-2.4 1-1.4 1.7-2.6 4.4-3.1'],
  section: ['M13.4 8A5.4 5.4 0 1 1 2.6 8a5.4 5.4 0 0 1 10.8 0Z', 'M8 2.6v10.8'],
  box: ['M8 2.4 13.4 5.6V11L8 14.2 2.6 11V5.6Z', 'M8 8.2l5.4-2.6M8 8.2 2.6 5.6M8 8.2v6'],
  close: ['m4 4 8 8M12 4l-8 8'],
  expand: ['M6.2 3H3v3.2M9.8 13H13V9.8M3 6.2 6.3 3M13 9.8 9.7 13'],
  metrics: ['M3 12V8.8M6.3 12V5.8M9.7 12V7.1M13 12V3.4'],
};

export function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  return <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name].map((path) => <path key={path} d={path} />)}</svg>;
}

export function BrandMark() {
  return <svg className="brand-mark" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" aria-hidden="true"><path d="M4.6 12h3" strokeWidth="1.7"/><path d="M7.6 12C7.6 8.2 10.6 4.4 15.4 2.8M7.6 12c0 3.8 3 7.6 7.8 9.2" strokeWidth="1.6"/><path d="M11.4 6.2c0-2.4 2.4-4 5.1-4.4M11.4 17.8c0 2.4 2.4 4 5.1 4.4" opacity=".45"/><circle cx="4.6" cy="12" r="1.5" fill="currentColor" stroke="none"/></svg>;
}
