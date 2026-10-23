import { namespaceStorage } from '../stores/durableSettings';

const storage = namespaceStorage('designFiles');
interface KnownFile { uid: string; source: string; name: string; fileKeys?: string[] }
interface FileMemory { originals: Record<string, KnownFile> }

function memory(): FileMemory {
  try {
    const value = JSON.parse(storage.getItem('') ?? '{}') as Partial<FileMemory>;
    return { originals: value.originals ?? {} };
  } catch { return { originals: {} }; }
}

/** A full path when the host exposes one; browser uploads expose only a name. */
export function designFileSource(file: { name: string; path?: string; webkitRelativePath?: string }): string {
  return file.path || file.webkitRelativePath || file.name;
}

export function inspectDesignFile(uid: string, source: string, fileKey?: string): { uid: string; originalName?: string } {
  const original = memory().originals[uid];
  // Native file keys survive a move and distinguish replacement at an old path.
  // Browser uploads expose only a name; they cannot persist a copy decision.
  const sameFile = original?.source === source || (fileKey && original?.fileKeys?.includes(fileKey));
  return original && !sameFile ? { uid, originalName: original.name } : { uid };
}

/** Record an observed on-disk uid only, never an old-stamp/path -> chosen-uid mapping. */
export function rememberDesignFile(uid: string, source: string, name: string, fileKey?: string): void {
  const known = memory();
  const original = known.originals[uid] ??= { uid, source, name };
  if (fileKey && !original.fileKeys?.includes(fileKey)) {
    original.fileKeys = [...original.fileKeys ?? [], fileKey];
  }
  storage.setItem('', JSON.stringify(known));
}
