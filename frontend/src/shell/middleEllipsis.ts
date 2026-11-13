/**
 * Truncate from the middle, because run names differ at the end.
 *
 * Names in this product are `<design>_v<NN>` and are generated from a shared
 * base, so end-truncation removes the only part that identifies the run:
 * comparing `260308tritonia-q_v02` against `_v03` rendered both chips as
 * `260308trit…` and both chart legend entries as `260…`. The version suffix is
 * the identity; it is the last thing that may be dropped, not the first.
 */
export function middleEllipsis(value: string, max = 20): string {
  if (value.length <= max) return value;
  const keepEnd = Math.max(5, Math.floor((max - 1) / 2));
  const keepStart = Math.max(1, max - 1 - keepEnd);
  return `${value.slice(0, keepStart)}…${value.slice(-keepEnd)}`;
}
