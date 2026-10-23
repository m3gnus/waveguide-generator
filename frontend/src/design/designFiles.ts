import { namespaceStorage } from '../stores/durableSettings';
import { isDesignUid } from '../stores/designUid';

const storage = namespaceStorage('designFiles');
interface KnownFile { uid: string; source: string; name: string }
interface FileMemory { originals: Record<string, KnownFile>; decisions: Record<string, string> }

function memory(): FileMemory {
  try {
    const value = JSON.parse(storage.getItem('') ?? '{}') as Partial<FileMemory>;
    return { originals: value.originals ?? {}, decisions: value.decisions ?? {} };
  } catch { return { originals: {}, decisions: {} }; }
}

/** A full path when the host exposes one; browser uploads expose only a name. */
export function designFileSource(file: { name: string; path?: string; webkitRelativePath?: string }): string {
  return file.path || file.webkitRelativePath || file.name;
}

export function inspectDesignFile(uid: string, source: string): { uid: string; originalName?: string } {
  const known = memory();
  const remembered = known.decisions[JSON.stringify([uid, source])];
  if (isDesignUid(remembered)) return { uid: remembered };
  const original = known.originals[uid];
  return original && original.source !== source
    ? { uid, originalName: original.name }
    : { uid };
}

/** Record only after the open passes its stale-response guard, or a write succeeds. */
export function rememberDesignFile(stamp: string, source: string, name: string, uid = stamp): void {
  const known = memory();
  known.originals[uid] ??= { uid, source, name };
  known.decisions[JSON.stringify([stamp, source])] = uid;
  storage.setItem('', JSON.stringify(known));
}
