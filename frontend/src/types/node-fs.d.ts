declare module 'node:fs' {
  export function writeFileSync(path: string, data: string): void;
  export function readFileSync(path: string): Uint8Array<ArrayBuffer>;
}
