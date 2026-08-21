import { BAND_ROLES, channelLabel, channelRole, channelTitle } from './channelLabel';
import { combinedChannelId, resultChannels, type ResultPayload } from './types';
import { COMBINED_VIEW, resolveResultView, type ResultView } from '../stores/resultView';

export interface ResultViewEntry {
  /** What is stored when this entry is chosen: the combined sentinel, or an id. */
  view: ResultView;
  channelId: string;
  label: string;
  title: string;
}

const PASSIVE_CARDIOID_CHANNEL_ID = 'passive_cardioid';

/**
 * The views a run offers, left to right.
 *
 * Combined first because it is what the run adds up to, then the drivers
 * highest band first so the labels read the way the crossovers are spoken.
 * A channel with no band role keeps its place in the solver's own channel
 * order rather than being guessed at, and the coupled cardioid channel goes
 * last: it is a port pair, not a driver in the chain.
 */
export function resultViewEntries(result: ResultPayload): ResultViewEntry[] {
  const combined = combinedChannelId(result);
  const rank = (channelId: string, index: number): number => {
    if (channelId === combined) return -1;
    if (channelId === PASSIVE_CARDIOID_CHANNEL_ID) return 1_000;
    const role = channelRole(result, channelId);
    const band = role ? BAND_ROLES.indexOf(role as typeof BAND_ROLES[number]) : -1;
    return band >= 0 ? band : 100 + index;
  };
  return resultChannels(result)
    .map(({ id }, index) => ({ id, index, order: rank(id, index) }))
    .sort((a, b) => a.order - b.order || a.index - b.index)
    .map(({ id }) => ({
      view: id === combined ? COMBINED_VIEW : id,
      channelId: id,
      label: channelLabel(result, id),
      title: channelTitle(result, id),
    }));
}

/**
 * One run, one view: the dock's single selector for which channel every chart,
 * the summary and the export describe. A run with one channel has no choice to
 * offer and renders nothing, exactly as the old per-channel chips did.
 */
export function ResultViewSwitch({ result, view, onSelect }: {
  result: ResultPayload;
  view: ResultView;
  onSelect: (view: ResultView) => void;
}) {
  const entries = resultViewEntries(result);
  if (entries.length < 2) return null;
  const active = resolveResultView(result, view);
  return <span className="result-view-switch" role="radiogroup" aria-label="Result view">
    {entries.map((entry) => <button
      key={entry.channelId}
      type="button"
      role="radio"
      aria-checked={entry.channelId === active}
      className={entry.channelId === active ? 'on' : undefined}
      title={entry.title}
      onClick={() => onSelect(entry.view)}
    >{entry.label}</button>)}
  </span>;
}
