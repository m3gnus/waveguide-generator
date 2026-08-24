/** Time wording shared by every CAD Link surface, so they agree on "2 hr ago". */

export function relativeTime(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return 'time unavailable';
  const elapsedSeconds = Math.max(0, Math.round((Date.now() - timestamp) / 1_000));
  if (elapsedSeconds < 60) return 'just now';
  const elapsedMinutes = Math.round(elapsedSeconds / 60);
  if (elapsedMinutes < 60) return `${elapsedMinutes} min ago`;
  const elapsedHours = Math.round(elapsedMinutes / 60);
  if (elapsedHours < 24) return `${elapsedHours} hr ago`;
  const elapsedDays = Math.round(elapsedHours / 24);
  return `${elapsedDays} ${elapsedDays === 1 ? 'day' : 'days'} ago`;
}

export function fullTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Time unavailable' : date.toLocaleString();
}

export function pluralized(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}
