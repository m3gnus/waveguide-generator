const toFiniteNumber = (value, fallback = 0) => {
  const num = Number(value);
  return Number.isFinite(num) ? num : fallback;
};

export function mapVertexToAth(x, y, z, { verticalOffset = 0, offsetSign = 1 } = {}) {
  return [
    x,
    z + (verticalOffset * offsetSign),
    y
  ];
}

export function transformVerticesToAth(vertices, options = {}) {
  const verticalOffset = toFiniteNumber(options.verticalOffset, 0);
  const offsetSign = toFiniteNumber(options.offsetSign, 1);
  const source = Array.from(vertices);
  const out = new Array(source.length);

  for (let i = 0; i < source.length; i += 3) {
    const [athX, athY, athZ] = mapVertexToAth(source[i], source[i + 1], source[i + 2], {
      verticalOffset,
      offsetSign
    });
    out[i] = athX;
    out[i + 1] = athY;
    out[i + 2] = athZ;
  }

  return out;
}
