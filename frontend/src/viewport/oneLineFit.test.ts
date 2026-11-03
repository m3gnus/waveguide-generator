import { describe, expect, it } from 'vitest';
import { fitOneLine } from './oneLineFit';

/** A row whose width is the sum of its visible items' widths, as layout would give it. */
function row(initialWidth: number, html: string) {
  let width = initialWidth;
  const element = Object.assign(document.createElement('div'), { resize: (next: number) => { width = next; } });
  element.innerHTML = html;
  const visibleWidth = (node: Element): number => {
    if (node instanceof HTMLElement && node.hidden) return 0;
    const own = Number((node as HTMLElement).dataset?.w ?? 0);
    return own * (element.classList.contains('compact') ? .9 : 1)
      + [...node.children].reduce((sum, child) => sum + visibleWidth(child), 0);
  };
  Object.defineProperty(element, 'clientWidth', { get: () => width });
  Object.defineProperty(element, 'scrollWidth', { get: () => visibleWidth(element) });
  return element;
}
const shown = (element: HTMLElement) => [...element.querySelectorAll<HTMLElement>('[data-w]')]
  .filter((item) => !item.hidden && !item.closest('[hidden]')).map((item) => item.dataset.name);

const HEADER = `
  <span data-w="40" data-name="kind" data-drop="7"></span>
  <span data-drop-group="">
    <span data-w="100" data-name="mouth"></span>
    <span data-w="60" data-name="depth" data-drop="2"></span>
    <span data-w="120" data-name="enclosure" data-drop="3"></span>
  </span>
  <span data-drop-group=""><span data-w="110" data-name="domain" data-drop="5"></span></span>`;

describe('the one-line viewport header', () => {
  it('keeps everything at full size when it fits', () => {
    const element = row(500, HEADER);
    fitOneLine(element);
    expect(element.classList.contains('compact')).toBe(false);
    expect(shown(element)).toEqual(['kind', 'mouth', 'depth', 'enclosure', 'domain']);
  });

  it('steps the text down before it drops anything', () => {
    const element = row(400, HEADER);
    fitOneLine(element);
    expect(element.classList.contains('compact')).toBe(true);
    expect(shown(element)).toEqual(['kind', 'mouth', 'depth', 'enclosure', 'domain']);
  });

  it('drops the highest rank first, takes an emptied group with it, and never drops the mouth', () => {
    const element = row(300, HEADER);
    fitOneLine(element);
    expect(shown(element)).toEqual(['mouth', 'depth', 'enclosure']);
    expect(element.querySelectorAll<HTMLElement>('[data-drop-group]')[1].hidden).toBe(true);

    const narrow = row(50, HEADER);
    fitOneLine(narrow);
    expect(shown(narrow)).toEqual(['mouth']);
  });

  it('gives a dropped fact back when the row widens again', () => {
    const element = row(300, HEADER);
    fitOneLine(element);
    element.resize(500);
    fitOneLine(element);
    expect(element.classList.contains('compact')).toBe(false);
    expect(shown(element)).toEqual(['kind', 'mouth', 'depth', 'enclosure', 'domain']);
  });
});
