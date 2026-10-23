import { beforeEach, expect, it } from 'vitest';
import { durableSettings } from '../stores/durableSettings';
import { inspectDesignFile, rememberDesignFile } from './designFiles';

const uid = 'wgu_01K00000000000000000000000';
const newUid = 'wgu_01K00000000000000000000001';
beforeEach(() => durableSettings.set('designFiles', null));

it('recognizes a copied name or path and remembers Keep for that file', () => {
  rememberDesignFile(uid, '/designs/asro.cfg', 'asro');
  expect(inspectDesignFile(uid, '/designs/asro.cfg')).toEqual({ uid });
  expect(inspectDesignFile(uid, '/copy/asro.cfg')).toEqual({ uid, originalName: 'asro' });
  expect(inspectDesignFile(uid, '/designs/copy.cfg')).toEqual({ uid, originalName: 'asro' });
  rememberDesignFile(uid, '/copy/asro.cfg', 'asro', uid);
  expect(inspectDesignFile(uid, '/copy/asro.cfg')).toEqual({ uid });
});

it('remembers a new-design decision while the disk still carries the old stamp', () => {
  rememberDesignFile(uid, 'asro.cfg', 'asro');
  rememberDesignFile(uid, 'copy.cfg', 'copy', newUid);
  expect(inspectDesignFile(uid, 'copy.cfg')).toEqual({ uid: newUid });
  expect(inspectDesignFile(uid, 'asro.cfg')).toEqual({ uid });
  expect(inspectDesignFile(newUid, 'copy.cfg')).toEqual({ uid: newUid });
  expect(JSON.parse(durableSettings.get('designFiles')!).decisions).toBeTruthy();
});
