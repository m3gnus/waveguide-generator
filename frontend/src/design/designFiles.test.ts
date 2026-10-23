import { beforeEach, expect, it } from 'vitest';
import { durableSettings } from '../stores/durableSettings';
import { inspectDesignFile, rememberDesignFile } from './designFiles';

const uid = 'wgu_01K00000000000000000000000';
const newUid = 'wgu_01K00000000000000000000001';
beforeEach(() => durableSettings.set('designFiles', null));

it('recognizes copies, remembers Keep by file identity, and recognizes a moved file', () => {
  rememberDesignFile(uid, '/designs/asro.cfg', 'asro', '1:1');
  expect(inspectDesignFile(uid, '/designs/asro.cfg', '1:1')).toEqual({ uid });
  expect(inspectDesignFile(uid, '/copy/asro.cfg', '1:2')).toEqual({ uid, originalName: 'asro' });
  rememberDesignFile(uid, '/copy/asro.cfg', 'asro', '1:2');
  expect(inspectDesignFile(uid, '/moved/asro.cfg', '1:2')).toEqual({ uid });
  // A different file at the accepted path is still a copy.
  expect(inspectDesignFile(uid, '/copy/asro.cfg', '1:3')).toEqual({ uid, originalName: 'asro' });
});

it('uses the uid written to the new-design copy, never an old-stamp/path decision', () => {
  rememberDesignFile(uid, 'asro.cfg', 'asro', '1:1');
  rememberDesignFile(newUid, 'copy.cfg', 'copy', '1:2');
  expect(inspectDesignFile(newUid, 'moved.cfg', '1:2')).toEqual({ uid: newUid });
  expect(inspectDesignFile(uid, 'copy.cfg', '1:3')).toEqual({ uid, originalName: 'asro' });
  expect(JSON.parse(durableSettings.get('designFiles')!)).not.toHaveProperty('decisions');
});

it('ignores legacy path decisions and drops them on the next observation', () => {
  durableSettings.set('designFiles', JSON.stringify({
    originals: { [uid]: { uid, source: 'original.cfg', name: 'original' } },
    decisions: { [JSON.stringify([uid, 'copy.cfg'])]: newUid },
  }));
  expect(inspectDesignFile(uid, 'copy.cfg')).toEqual({ uid, originalName: 'original' });
  rememberDesignFile(uid, 'original.cfg', 'original');
  expect(JSON.parse(durableSettings.get('designFiles')!)).not.toHaveProperty('decisions');
});

it('cannot remember a browser copy decision without writing the selected file', () => {
  rememberDesignFile(uid, 'original.cfg', 'original');
  rememberDesignFile(uid, 'copy.cfg', 'copy');
  expect(inspectDesignFile(uid, 'copy.cfg')).toEqual({ uid, originalName: 'original' });
});
