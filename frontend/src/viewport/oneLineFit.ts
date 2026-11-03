/**
 * Keeps the viewport header on one line, whatever the window.
 *
 * The row first sets its text a step smaller (`compact`). If it still does not
 * fit, items marked `data-drop` are hidden one at a time, the highest rank
 * first, until it does; a group whose every item is hidden goes with them, so
 * no empty hairline is left behind. What carries no rank is never dropped.
 *
 * It refits when the header changes size and when anything written in the row
 * changes, so a value that grows a digit cannot push the row onto a second line.
 */
export function fitOneLine(row: HTMLElement): void {
  row.classList.remove('compact');
  const ranked = [...row.querySelectorAll<HTMLElement>('[data-drop]')]
    .sort((a, b) => Number(b.dataset.drop) - Number(a.dataset.drop));
  for (const item of ranked) item.hidden = false;
  for (const group of row.children) if (group instanceof HTMLElement && group.dataset.dropGroup !== undefined) group.hidden = false;
  const overflows = () => row.scrollWidth > row.clientWidth + .5;
  if (!overflows()) return;
  row.classList.add('compact');
  for (const item of ranked) {
    if (!overflows()) return;
    item.hidden = true;
    const group = item.parentElement?.closest<HTMLElement>('[data-drop-group]');
    if (group && row.contains(group) && [...group.children].every((fact) => fact instanceof HTMLElement && fact.hidden)) {
      group.hidden = true;
    }
  }
}

/** Ref callback: fits the row now and whenever its header or its text changes. */
export function oneLineRow(row: HTMLElement | null): (() => void) | void {
  if (!row) return;
  const fit = () => fitOneLine(row);
  fit();
  const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(fit);
  // The header, not the row: the row's own width changes as items drop.
  if (row.parentElement) resize?.observe(row.parentElement);
  for (const sibling of row.parentElement?.children ?? []) if (sibling !== row) resize?.observe(sibling);
  const writes = new MutationObserver(fit);
  writes.observe(row, { childList: true, subtree: true, characterData: true });
  return () => { resize?.disconnect(); writes.disconnect(); };
}
