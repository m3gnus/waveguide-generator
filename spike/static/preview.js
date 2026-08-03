import * as THREE from './vendor/three.module.js';

const canvas = document.querySelector('#viewport');
const familyControl = document.querySelector('#family');
const lodControl = document.querySelector('#lod');
const paramControl = document.querySelector('#param');
const paramValue = document.querySelector('#paramValue');
const sweepButton = document.querySelector('#sweep');
const statusNode = document.querySelector('#status');
const statsNode = document.querySelector('#stats');

const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setClearColor(0x080b10, 1);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 10000);
camera.position.set(260, 210, 310);
camera.lookAt(0, 0, 65);
scene.add(new THREE.HemisphereLight(0xd9edff, 0x202936, 1.8));
const keyLight = new THREE.DirectionalLight(0xffffff, 2.4);
keyLight.position.set(200, 300, 400);
scene.add(keyLight);
const model = new THREE.Group();
model.rotation.x = -0.12;
model.rotation.z = 0.2;
scene.add(model);

const meshes = new Map();
window.__spike = { scene, camera, model, renderer, meshes };
(function debugLoop() {
  renderer.render(scene, camera);
  requestAnimationFrame(debugLoop);
})();
let fitted = false;
let sequence = 0;
let socket;
let sweep = null;
const requestTimes = new Map();

function resize() {
  const width = window.innerWidth;
  const height = window.innerHeight;
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  renderer.render(scene, camera);
}
window.addEventListener('resize', resize);
resize();

function decodeFrame(buffer) {
  const view = new DataView(buffer);
  if (view.byteLength < 8) throw new Error('short WGF0 frame');
  const magic = String.fromCharCode(...new Uint8Array(buffer, 0, 4));
  if (magic !== 'WGF0') throw new Error(`bad frame magic ${magic}`);
  const headerLength = view.getUint32(4, true);
  const headerEnd = 8 + headerLength;
  if (headerEnd > buffer.byteLength) throw new Error('truncated WGF0 header');
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 8, headerLength)));
  if (header.v !== 0) throw new Error(`unsupported frame version ${header.v}`);
  const constructors = {
    float32: Float32Array,
    float64: Float64Array,
    int8: Int8Array,
    uint8: Uint8Array,
    int16: Int16Array,
    uint16: Uint16Array,
    int32: Int32Array,
    uint32: Uint32Array,
  };
  const arrays = {};
  for (const section of header.sections) {
    const Constructor = constructors[section.dtype];
    if (!Constructor) throw new Error(`unsupported browser dtype ${section.dtype}`);
    const start = headerEnd + section.offset;
    const end = start + section.byteLength;
    if (start < headerEnd || end > buffer.byteLength) throw new Error('invalid section bounds');
    // JSON header length need not align to a typed-array element, so copy each
    // packed section into its own aligned ArrayBuffer.
    arrays[section.name] = new Constructor(buffer.slice(start, end));
  }
  return { header, arrays };
}

function updateMesh(name, positions, indices, color, opacity) {
  let mesh = meshes.get(name);
  if (!mesh) {
    const geometry = new THREE.BufferGeometry();
    const material = new THREE.MeshStandardMaterial({
      color,
      metalness: 0.18,
      roughness: 0.55,
      side: THREE.DoubleSide,
      transparent: opacity < 1,
      opacity,
    });
    mesh = new THREE.Mesh(geometry, material);
    meshes.set(name, mesh);
    model.add(mesh);
  }
  const geometry = mesh.geometry;
  const currentPosition = geometry.getAttribute('position');
  if (currentPosition && currentPosition.array.length === positions.length) {
    currentPosition.array.set(positions);
    currentPosition.needsUpdate = true;
  } else {
    geometry.deleteAttribute('normal');
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  }
  const currentIndex = geometry.getIndex();
  if (currentIndex && currentIndex.array.length === indices.length) {
    currentIndex.array.set(indices);
    currentIndex.needsUpdate = true;
  } else {
    geometry.setIndex(new THREE.BufferAttribute(indices, 1));
  }
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  mesh.visible = true;
  return mesh;
}

function uploadMeshes(arrays) {
  const inner = updateMesh('inner', arrays.positions, arrays.indices, 0x52b5ff, 0.82);
  const outer = meshes.get('outer');
  if (arrays.outerPositions && arrays.outerIndices) {
    updateMesh('outer', arrays.outerPositions, arrays.outerIndices, 0xffa85c, 0.28);
  } else if (outer) {
    outer.visible = false;
  }
  if (!fitted && inner.geometry.boundingSphere) {
    const sphere = inner.geometry.boundingSphere;
    const distance = Math.max(80, sphere.radius * 3.2);
    camera.position.set(
      sphere.center.x + distance,
      sphere.center.y + distance * 0.72,
      sphere.center.z + distance
    );
    camera.lookAt(sphere.center);
    camera.near = Math.max(0.01, distance / 1000);
    camera.far = distance * 20;
    camera.updateProjectionMatrix();
    fitted = true;
  }
}

function percentile(values, p) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const position = (ordered.length - 1) * p / 100;
  const lower = Math.floor(position);
  const upper = Math.min(lower + 1, ordered.length - 1);
  return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower);
}

function metricStats(values) {
  return {
    p50: percentile(values, 50),
    p95: percentile(values, 95),
    max: values.length ? Math.max(...values) : null,
  };
}

function formatNumber(value) {
  return value === null ? '—' : value.toFixed(2);
}

function finishSweep() {
  if (!sweep || sweep.finished) return;
  const completed = sweep;
  completed.finished = true;
  clearTimeout(completed.forceFinishTimer);
  const endedAt = completed.lastPaintAt || performance.now();
  const durationSeconds = (endedAt - completed.startedAt) / 1000;
  const result = {
    family: completed.family,
    lod: completed.lod,
    requested: completed.requested,
    framesPainted: completed.framesPainted,
    dropped: completed.dropped,
    fps: durationSeconds > 0 ? completed.framesPainted / durationSeconds : 0,
    serverEvalMs: metricStats(completed.serverEvalMs),
    decodeMs: metricStats(completed.decodeMs),
    uploadDrawMs: metricStats(completed.uploadDrawMs),
    endToEndMs: metricStats(completed.endToEndMs),
  };
  const rows = [
    'metric                    p50       p95        max',
    '-------------------------------------------------',
    ...[
      ['server eval ms', result.serverEvalMs],
      ['decode ms', result.decodeMs],
      ['upload + draw ms', result.uploadDrawMs],
      ['request → painted ms', result.endToEndMs],
    ].map(([name, values]) =>
      `${name.padEnd(23)} ${formatNumber(values.p50).padStart(8)} ${formatNumber(values.p95).padStart(9)} ${formatNumber(values.max).padStart(10)}`
    ),
    '',
    `frames painted: ${result.framesPainted}  dropped: ${result.dropped}  requested: ${result.requested}  fps: ${result.fps.toFixed(2)}`,
    JSON.stringify(result),
  ];
  statsNode.textContent = rows.join('\n');
  console.log(JSON.stringify(result));
  sweepButton.disabled = false;
  familyControl.disabled = false;
  lodControl.disabled = false;
  sweep = null;
}

function maybeFinishSweep() {
  if (sweep && sweep.sendingDone && sweep.pending.size === 0) finishSweep();
}

function sendPreview(trackSweep = false) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return null;
  const seq = ++sequence;
  const sentAt = performance.now();
  const sweepToken = trackSweep && sweep ? sweep.token : null;
  requestTimes.set(seq, { sentAt, sweepToken });
  if (sweepToken) {
    sweep.pending.add(seq);
    sweep.requested += 1;
  }
  socket.send(JSON.stringify({
    seq,
    family: familyControl.value,
    lod: lodControl.value,
    paramOverride: Number(paramControl.value),
  }));
  return seq;
}

function connect() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  socket = new WebSocket(`${protocol}//${location.host}/ws/preview`);
  socket.binaryType = 'arraybuffer';
  socket.addEventListener('open', () => {
    statusNode.textContent = 'connected';
    sendPreview(false);
  });
  socket.addEventListener('close', () => {
    statusNode.textContent = 'disconnected; retrying…';
    setTimeout(connect, 800);
  });
  socket.addEventListener('error', () => { statusNode.textContent = 'WebSocket error'; });
  socket.addEventListener('message', (event) => {
    if (typeof event.data === 'string') {
      const message = JSON.parse(event.data);
      if (message.type === 'dropped') {
        const timing = requestTimes.get(message.seq);
        requestTimes.delete(message.seq);
        if (sweep && timing?.sweepToken === sweep.token) {
          sweep.pending.delete(message.seq);
          sweep.dropped += 1;
          maybeFinishSweep();
        }
      } else if (message.type === 'error') {
        statusNode.textContent = `error: ${message.message}`;
        if (message.seq !== undefined) {
          const timing = requestTimes.get(message.seq);
          requestTimes.delete(message.seq);
          if (sweep && timing?.sweepToken === sweep.token) sweep.pending.delete(message.seq);
        }
        maybeFinishSweep();
      }
      return;
    }

    const decodeStarted = performance.now();
    const decoded = decodeFrame(event.data);
    const decodeMs = performance.now() - decodeStarted;
    const seq = decoded.header.seq;
    const timing = requestTimes.get(seq);
    requestTimes.delete(seq);
    const uploadStarted = performance.now();
    uploadMeshes(decoded.arrays);
    requestAnimationFrame(() => {
      renderer.render(scene, camera);
      const paintedAt = performance.now();
      if (sweep && timing?.sweepToken === sweep.token) {
        sweep.pending.delete(seq);
        sweep.serverEvalMs.push(Number(decoded.header.evalMs));
        sweep.decodeMs.push(decodeMs);
        sweep.uploadDrawMs.push(paintedAt - uploadStarted);
        sweep.endToEndMs.push(paintedAt - timing.sentAt);
        sweep.framesPainted += 1;
        sweep.lastPaintAt = paintedAt;
        maybeFinishSweep();
      }
    });
  });
}

function runSweep() {
  if (!socket || socket.readyState !== WebSocket.OPEN || sweep) return;
  sweepButton.disabled = true;
  familyControl.disabled = true;
  lodControl.disabled = true;
  statsNode.textContent = 'Sweep running for 10 seconds…';
  const startedAt = performance.now();
  sweep = {
    token: Symbol('sweep'),
    family: familyControl.value,
    lod: lodControl.value,
    startedAt,
    sendingDone: false,
    finished: false,
    requested: 0,
    framesPainted: 0,
    dropped: 0,
    pending: new Set(),
    serverEvalMs: [],
    decodeMs: [],
    uploadDrawMs: [],
    endToEndMs: [],
    lastPaintAt: null,
    forceFinishTimer: null,
  };
  const timer = setInterval(() => {
    const elapsed = performance.now() - startedAt;
    const phase = Math.min(1, elapsed / 10000);
    const value = 1 + 0.15 * Math.sin(phase * Math.PI * 8);
    paramControl.value = value.toFixed(3);
    paramValue.value = Number(paramControl.value).toFixed(3);
    sendPreview(true);
    if (elapsed >= 10000) {
      clearInterval(timer);
      sweep.sendingDone = true;
      sweep.forceFinishTimer = setTimeout(finishSweep, 5000);
      maybeFinishSweep();
    }
  }, 1000 / 30);
}

familyControl.addEventListener('change', () => { fitted = false; sendPreview(false); });
lodControl.addEventListener('change', () => { fitted = false; sendPreview(false); });
paramControl.addEventListener('input', () => {
  paramValue.value = Number(paramControl.value).toFixed(3);
  if (!sweep) sendPreview(false);
});
sweepButton.addEventListener('click', runSweep);
connect();
