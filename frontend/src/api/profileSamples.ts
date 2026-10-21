/** Mesher CSV coordinates are centimetres and already include global Scale. */
export function profileSections(text: string): number[][][] {
  return text.split(/\r?\n\s*\r?\n/).map((section) => section.split(/\r?\n/).flatMap((line) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) return [];
    const values = trimmed.split(';').map(Number);
    return values.length === 3 && values.every(Number.isFinite) ? [values] : [];
  })).filter((rows) => rows.length >= 2);
}

function axisSection(sections: number[][][], axis: 'H' | 'V'): number[][] {
  return sections.reduce<{ rows: number[][]; ratio: number; magnitude: number }>((best, rows) => {
    const magnitude = rows.reduce((sum, [x, y]) => sum + Math.abs(axis === 'H' ? x : y), 0) / rows.length;
    const cross = rows.reduce((sum, [x, y]) => sum + Math.abs(axis === 'H' ? y : x), 0) / rows.length;
    const ratio = cross / Math.max(magnitude, Number.EPSILON);
    return ratio < best.ratio || (ratio === best.ratio && magnitude > best.magnitude) ? { rows, ratio, magnitude } : best;
  }, { rows: [], ratio: Infinity, magnitude: -Infinity }).rows;
}

export interface ProfileSample {
  z: number;
  r: number;
}

function meridianSamples(rows: number[][], scale: number): ProfileSample[] {
  return rows.map(([x, y, z]) => ({ z: z * 10 / scale, r: Math.hypot(x, y) * 10 / scale }));
}

export interface ProfileSamples { H: ProfileSample[]; V: ProfileSample[] }

/** Physical mm by default; conversion to authored anchors can explicitly remove scale. */
export function profileSamples(text: string, scale = 1): ProfileSamples {
  const sections = profileSections(text);
  if (sections.length < 2) throw new Error('Profile export did not contain horizontal and vertical meridians.');
  return {
    H: meridianSamples(axisSection(sections, 'H'), scale),
    V: meridianSamples(axisSection(sections, 'V'), scale),
  };
}
