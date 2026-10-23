import { namespaceStorage } from '../stores/durableSettings';

const storage = namespaceStorage('designFiles');
interface KnownFile { uid: string; source: string; name: string; fileKeys?: string[] }
interface FileMemory { originals: Record<string, KnownFile> }

function memory(): FileMemory {
  try {
    const value = JSON.parse(storage.getItem('') ?? '{}') as Partial<FileMemory>;
    // Discard legacy copy decisions and accepted-copy keys. Only the first
    // observation identifies the original; choosing Keep/New never changes it.
    return { originals: Object.fromEntries(Object.entries(value.originals ?? {}).map(([uid, file]) => [uid, {
      uid, source: file.source, name: file.name, fileKeys: file.fileKeys?.slice(0, 1),
    }])) };
  } catch { return { originals: {} }; }
}

/** A full path when the host exposes one; browser uploads expose only a name. */
export function designFileSource(file: { name: string; path?: string; webkitRelativePath?: string }): string {
  return file.path || file.webkitRelativePath || file.name;
}

export function inspectDesignFile(uid: string, source: string, fileKey?: string): { uid: string; originalName?: string } {
  const original = memory().originals[uid];
  // The original native file key survives moves; browser uploads expose only a name.
  const sameFile = original?.source === source || (fileKey && original?.fileKeys?.includes(fileKey));
  return original && !sameFile ? { uid, originalName: original.name } : { uid };
}

/** Keep the first observed file for each uid, solely to recognize shared stamps. */
export function rememberDesignFile(uid: string, source: string, name: string, fileKey?: string): void {
  const known = memory();
  known.originals[uid] ??= { uid, source, name, fileKeys: fileKey ? [fileKey] : undefined };
  storage.setItem('', JSON.stringify(known));
}
