import { expect, it } from 'vitest';
import cases from '../../../server/tests/fixtures/cad_draft_journal_parity.json';
import { journalKey, mergeCadDrafts, type CadDraftEntry, type CadDraftJournal } from './cadDraftJournal';

const key = (id: number) => `${id.toString(16).padStart(16, '0')}_${'0'.repeat(16)}`;
interface Range { start: number; count: number; step?: number }
interface Group extends Range {
  timestamp: number; timestampStep?: number; value: string | null; repeat?: number; baseRepeat?: number;
}
const parityCases: Array<{
  name: string; journals: Group[][]; expected: Range[]; values?: Record<string, string | null>;
}> = cases;

// The same fixtures are checked by server/tests/test_settings_store.py.
it.each(parityCases)('matches Python journal retention: $name', fixture => {
  const journals = fixture.journals.map(groups => {
    const journal: CadDraftJournal = {};
    for (const group of groups) for (let i = 0; i < group.count; i += 1) {
      journal[key(group.start + i * (group.step ?? 1))] = {
        draft: group.value === null ? null : { value: group.value.repeat(group.repeat ?? 1) },
        updatedAt: group.timestamp + i * (group.timestampStep ?? 0),
        baseRevisionId: 'r'.repeat(group.baseRepeat ?? 1),
      };
    }
    return journal;
  });
  const merged = mergeCadDrafts(...journals);
  expect(Object.keys(merged)).toEqual(fixture.expected.flatMap(group =>
    Array.from({ length: group.count }, (_, i) => key(group.start + i * (group.step ?? 1)))));
  for (const [id, value] of Object.entries(fixture.values ?? {})) {
    expect(merged[key(Number(id))].draft).toEqual(value === null ? null : { value });
  }
});

it('keeps a live incomplete draft through newer publishes and rejects a stale cached draft', () => {
  const live = journalKey('incomplete', 'inventory');
  const cleared = journalKey('published', 'inventory');
  const entry = (updatedAt: number, draft: CadDraftEntry['draft']): CadDraftEntry =>
    ({ updatedAt, draft, baseRevisionId: null });
  let journal = mergeCadDrafts({ [live]: entry(0, { driver: {} }), [cleared]: entry(1, null) });
  for (let i = 0; i < 63; i += 1) journal = mergeCadDrafts(journal, {
    [journalKey(`other-${i}`, 'inventory')]: entry(i + 2, null),
  });
  journal = mergeCadDrafts(journal, { [cleared]: entry(0, { stale: true }) });
  expect(journal[live].draft).toEqual({ driver: {} });
  expect(journal[cleared].draft).toBeNull();
  expect(Object.values(journal).filter(value => value.draft === null)).toHaveLength(64);
});
