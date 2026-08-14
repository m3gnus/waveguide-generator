import type { WgLinkExportResponse } from '../api/designIo';

/** One success format for every Send to CAD surface (menu, rail, run export). */
export function sentToCadMessage(result: WgLinkExportResponse): string {
  return `Sent to CAD · sequence ${result.sequence} · ${result.bundlePath}`;
}
