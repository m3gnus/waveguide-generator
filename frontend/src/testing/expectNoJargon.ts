import { expect } from 'vitest';

// CAD copy rules: docs/WORDING.md. Match words, not internal data attributes
// or substrings in ordinary words such as "returned" and "operational".
const JARGON = /\b(?:bundles?|returns?|wgreturn|manifests?|schemas?|request[ _-]?ids?|return[ _-]?ids?|uuids?|inbox|ipc|acknowledgements?|selectors?|entity_token|tracebacks?|main[ -]thread|events?|gates?|ingest(?:ed|ion|ing)?|operations?|lineage|finding[ _-]ids?|utf-?8|byte limits?|determinant|orthonormal|chirality|PASSIVE_CARDIOID|PORT_EXIT|Text Commands|refused)\b|\boptions\s*\[|\bfinding-[a-z0-9-]+|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|\b(?:wgi_|wgr_|sha256:)/i;

/** Check primary CAD copy, including accessible labels and tooltips. Raw
 * evidence inside an existing developer disclosure is deliberately excluded.
 * `names` exempts the user's own names, never application wording. */
export function expectNoJargon(element: Element | string, { names = [], forbidden = [] }: {
  names?: readonly string[];
  forbidden?: readonly string[];
} = {}): void {
  let words: string;
  if (typeof element === 'string') words = element;
  else {
    const primary = element.cloneNode(true) as Element;
    primary.querySelectorAll('.developer-details').forEach(node => node.remove());
    const attributes = [primary, ...primary.querySelectorAll('*')].flatMap(node =>
      ['title', 'aria-label', 'alt', 'placeholder'].map(name => node.getAttribute(name) ?? ''));
    const text = document.createTreeWalker(primary, NodeFilter.SHOW_TEXT);
    const lines: string[] = [];
    while (text.nextNode()) lines.push(text.currentNode.textContent ?? '');
    words = [...lines, ...attributes].join(' ');
  }
  for (const name of names) if (name.trim()) words = words.replaceAll(name, ' ');
  const match = JARGON.exec(words);
  const context = match ? words.slice(Math.max(0, match.index - 70), match.index + 100) : '';
  expect(match?.[0], context).toBeUndefined();
  for (const word of forbidden) expect(words).not.toContain(word);
}
