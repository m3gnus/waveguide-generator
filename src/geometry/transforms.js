const toFiniteNumber = (value, fallback = 0) => {
  const num = Number(value);
  return Number.isFinite(num) ? num : fallback;
};

export function mapVertexToAth(x, y, z) {
  return [x, z, y];
}

export function transformVerticesToAth(vertices, options = {}) {
  const verticalOffset = toFiniteNumber(options.verticalOffset, 0);
  const offsetSign = toFiniteNumber(options.offsetSign, 1);
  const source = Array.from(vertices);
  const out = new Array(source.length);

  for (let i = 0; i < source.length; i += 3) {
    const [athX, athY, athZ] = mapVertexToAth(source[i], source[i + 1], source[i + 2]);
    out[i] = athX;
    out[i + 1] = athY + verticalOffset * offsetSign;
    out[i + 2] = athZ;
  }

  return out;
}
