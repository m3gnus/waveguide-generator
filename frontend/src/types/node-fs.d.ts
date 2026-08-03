declare module 'node:fs' {
  export function readFileSync(path: string): Uint8Array<ArrayBuffer>;
}
