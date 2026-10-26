import type { EditRecord } from '../../stores/designEdit';
import type { Lowered } from '../adapter';
import type { DomainAdapter } from './types';

export interface EditHost<TDoc> { commit(record: EditRecord<TDoc>): void }

/** A content key is exact serialised authoring, so it cannot collide or lose precision. */
export function editRecord<D, S, C>(adapter: DomainAdapter<D, S, C>, state: S, lastValid: D | null,
  lowered: Lowered<D> = adapter.lower(state)): EditRecord<D> {
  const status = adapter.status(state, lowered);
  const authoring = adapter.serializeAuthoring(state, status);
  return { document: status.kind === 'valid' && lowered.ok ? lowered.document : lastValid,
    authoring, status: status.kind, draftKey: JSON.stringify(authoring) };
}
