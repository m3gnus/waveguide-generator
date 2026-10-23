/** Permanent document identity; never part of DesignConfig or its content key. */
export function isDesignUid(value: unknown): value is string {
  return typeof value === 'string' && /^wgu_[0-7][0-9A-HJKMNP-TV-Z]{25}$/.test(value);
}

export function mintDesignUid(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  let timestamp = Date.now();
  for (let i = 5; i >= 0; i -= 1) {
    bytes[i] = timestamp % 256;
    timestamp = Math.floor(timestamp / 256);
  }
  let value = bytes.reduce((n, byte) => (n << 8n) | BigInt(byte), 0n);
  let encoded = '';
  for (let i = 0; i < 26; i += 1) {
    encoded = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'[Number(value & 31n)] + encoded;
    value >>= 5n;
  }
  return `wgu_${encoded}`;
}
