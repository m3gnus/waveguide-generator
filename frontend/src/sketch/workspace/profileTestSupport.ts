// Minimal profile-shaped adapter shared only by the W4 acceptance and UI tests.
import type { FreeformProfile } from '../../stores/design';
import { editRecord } from './editRecord';
import type { EditRecord, EmbeddedDesignUpdates } from '../../stores/designEdit';
import type { ProjectBinding } from './projectHistoryAuthority';
import type { DomainAdapter } from './types';

export type State = { points: FreeformProfile['points']; complete: boolean; label: string };
export type Command = Partial<Pick<State, 'complete' | 'label'>> & { r?: number };
export const initial: State = { points: [{ t: 0, r: 12.7 }, { t: 1, r: 140 }], complete: true, label: 'original' };
export const adapter: DomainAdapter<FreeformProfile, State, Command> = {
  id: 'test-profile', axes: { x: { label: 'z', unit: 'mm' }, y: { label: 'r', unit: 'mm' } },
  seed: (document, authoring) => authoring as State ?? { ...initial, points: document.points },
  lower: state => state.complete ? { ok: true, document: { points: state.points } } : { ok: false, problems: [] },
  status: state => state.complete ? { kind: 'valid' } : { kind: 'unfinished', hint: 'Finish profile' },
  serializeAuthoring: state => state,
  apply: (state, { r, ...rest }) => ({ ...state, ...rest, points: r === undefined ? state.points : state.points.map((p, i) => i ? { ...p, r } : p) }),
  remove: () => [], tools: [], properties: () => [], hits: () => [], marquee: () => [],
  display: (state, ui) => ({ context: [], bounds: () => ({ minX: 0, maxX: 1, minY: 0, maxY: 200 }),
    items: state.points.map((p, i) => ({ id: String(i), kind: 'point', role: 'anchor', editable: true, state: ui.selection.includes(String(i)) ? ['selected'] : [], at: { x: p.t, y: p.r } })) }),
  dragSet: (_state, ids) => ({ ids, move: (_state, _delta, at) => [{ r: at.y }] }),
};
export function profileBinding(onMissing: () => void, extraPath = false): ProjectBinding<State> {
  return {
    paths: extraPath ? ['profile_h', 'profile_v'] : ['profile_h'], onMissing,
    read: values => {
      const record = values.profile_h as EditRecord<FreeformProfile> | undefined;
      return record && adapter.seed(record.document!, record.authoring);
    },
    write: (state, values): EmbeddedDesignUpdates => {
      const previous = values.profile_h as EditRecord<FreeformProfile>;
      const next = editRecord(adapter, state, previous.document);
      return extraPath ? { profile_h: next, profile_v: next.document } : { profile_h: next };
    },
  };
}
