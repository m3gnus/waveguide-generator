import { useSyncExternalStore } from 'react';
import { activityStore, activityVisible, type Activity } from './activity';
import { etaSeconds, formatClock, useSolveClock } from './solveProgress';

/** Time left from the rate the counted work has kept so far, less the time
 * since it last gained, so the readout keeps counting down between steps. */
export function activityTimeLeft(activity: Activity, now: number): number | null {
  const { progress, progressAt } = activity;
  if (!progress || progressAt === null) return null;
  const eta = etaSeconds((progressAt - activity.startedAt) / 1000, progress);
  return eta === null ? null : Math.max(0, eta - (now - progressAt) / 1000);
}

/** What one activity says beside its bar: how far and how long left when it
 * can be counted, otherwise how long it usually takes here. */
export function activityMeta(activity: Activity, now: number, typicalMs: number | null): string {
  const elapsed = formatClock((now - activity.startedAt) / 1000);
  const { progress } = activity;
  if (progress && progress.total > 0) {
    const percent = Math.round(Math.min(1, progress.done / progress.total) * 100);
    const left = activityTimeLeft(activity, now);
    return [`${percent}%`, left !== null ? `${formatClock(left)} left` : null, elapsed].filter(Boolean).join(' · ');
  }
  return typicalMs !== null ? `${elapsed} · usually ${formatClock(typicalMs / 1000)}` : elapsed;
}

/**
 * The status bar's activity slot: the oldest wait that has earned a place
 * (two seconds, or known to take longer), with a progress bar and, when it
 * can be stopped, a Stop button. Further waits are counted beside it.
 */
export function ActivityStatus() {
  const activities = useSyncExternalStore(activityStore.subscribe, activityStore.getSnapshot, activityStore.getSnapshot);
  const now = useSolveClock(activities.length > 0);
  const shown = activities.filter((item) => activityVisible(item, now, activityStore.typicalMs(item.kind)));
  if (!shown.length) return null;
  const [activity, ...others] = shown;
  const typical = activityStore.typicalMs(activity.kind);
  const counted = activity.progress && activity.progress.total > 0;
  const percent = counted ? Math.round(Math.min(1, activity.progress!.done / activity.progress!.total) * 100) : null;
  const meta = activityMeta(activity, now, typical);
  return <div className="status-item status-activity" role="status" aria-live="polite">
    <span className="status-activity-label">{activity.label}</span>
    <div
      className={`progress${counted ? '' : ' indeterminate'}`}
      role="progressbar"
      aria-label={activity.label}
      aria-valuemin={0}
      aria-valuemax={counted ? 100 : undefined}
      aria-valuenow={percent ?? undefined}
      aria-valuetext={`${activity.label} · ${meta}`}
    ><i style={percent !== null ? { width: `${percent}%` } : undefined}/></div>
    <span className="status-activity-meta">{meta}</span>
    {others.length > 0 && <span className="status-activity-more" title={others.map((item) => item.label).join('\n')}>+{others.length} more</span>}
    {activity.cancel && <button type="button" className="statusbar-link" onClick={activity.cancel}>Stop</button>}
  </div>;
}
