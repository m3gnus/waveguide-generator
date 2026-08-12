import { OrbitControls } from '@react-three/drei';
import { Canvas, useThree } from '@react-three/fiber';
import { useEffect, useMemo, useState } from 'react';
import { BufferAttribute, BufferGeometry, Color, DoubleSide } from 'three';
import { readChartTokens, useChartTokens } from './EChart';
import type { ResultPayload } from './types';

const RANGE_DB = 30;

/**
 * The balloon reads the same ramp as the directivity map, so a level means the
 * same colour whichever way you look at it. It used to hardcode viridis, which
 * belonged to neither theme and disagreed with every other view of the same
 * numbers.
 */
function colorForDb(value: number, colormap: string[]): string {
  const position = Math.max(0, Math.min(1, 1 + value / RANGE_DB)) * (colormap.length - 1);
  return colormap[Math.round(position)];
}
const BALLOON_CAMERA = [2.35, 1.55, 2.75] as const;


/** Build a closed, vertex-coloured radiation surface from a spherical grid. */
export function buildBalloonGeometry(
  thetaDegrees: number[],
  phiDegrees: number[],
  grid: Array<Array<number | null>>,
  colormap: string[] = readChartTokens().colormap,
): BufferGeometry {
  const geometry = new BufferGeometry();
  if (thetaDegrees.length < 2 || phiDegrees.length < 3) return geometry;

  // Repeat the first azimuth at +360 degrees. This closes the surface while
  // keeping separate vertices at the seam for clean normals on both sides.
  const columns = phiDegrees.length + 1;
  const positions = new Float32Array(thetaDegrees.length * columns * 3);
  const colors = new Float32Array(positions.length);
  const color = new Color();
  let offset = 0;
  for (let thetaIndex = 0; thetaIndex < thetaDegrees.length; thetaIndex += 1) {
    const theta = thetaDegrees[thetaIndex] * Math.PI / 180;
    for (let phiIndex = 0; phiIndex < columns; phiIndex += 1) {
      const sourcePhi = phiIndex % phiDegrees.length;
      const phi = (phiDegrees[sourcePhi] + (phiIndex === phiDegrees.length ? 360 : 0)) * Math.PI / 180;
      const rawDb = Number(grid[thetaIndex]?.[sourcePhi]);
      const db = Number.isFinite(rawDb) ? Math.max(-RANGE_DB, Math.min(0, rawDb)) : -RANGE_DB;
      // A small core at the display floor keeps deep nulls from looking like
      // broken or inside-out geometry.
      const radius = .1 + .9 * (1 + db / RANGE_DB);
      positions[offset] = radius * Math.sin(theta) * Math.cos(phi);
      positions[offset + 1] = radius * Math.sin(theta) * Math.sin(phi);
      positions[offset + 2] = radius * Math.cos(theta);
      color.set(colorForDb(db, colormap));
      colors[offset] = color.r;
      colors[offset + 1] = color.g;
      colors[offset + 2] = color.b;
      offset += 3;
    }
  }

  const indices: number[] = [];
  for (let row = 0; row < thetaDegrees.length - 1; row += 1) {
    for (let column = 0; column < columns - 1; column += 1) {
      const topLeft = row * columns + column;
      const bottomLeft = (row + 1) * columns + column;
      indices.push(topLeft, bottomLeft, topLeft + 1, topLeft + 1, bottomLeft, bottomLeft + 1);
    }
  }
  geometry.setAttribute('position', new BufferAttribute(positions, 3));
  geometry.setAttribute('color', new BufferAttribute(colors, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  return geometry;
}

function CameraReset({ nonce }: { nonce: number }) {
  const { camera } = useThree();
  useEffect(() => {
    camera.position.set(...BALLOON_CAMERA);
    camera.up.set(0, 1, 0);
    camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();
  }, [camera, nonce]);
  return null;
}

function BalloonScene({ result, frequencyIndex, resetNonce }: { result: ResultPayload; frequencyIndex: number; resetNonce: number }) {
  const balloon = result.balloon!;
  const tokens = useChartTokens();
  const geometry = useMemo(
    () => buildBalloonGeometry(balloon.theta_deg, balloon.phi_deg, balloon.spl_norm_db[frequencyIndex] ?? [], tokens.colormap),
    [balloon, frequencyIndex, tokens],
  );
  useEffect(() => () => geometry.dispose(), [geometry]);

  return <>
    <CameraReset nonce={resetNonce}/>
    <ambientLight intensity={1.25}/>
    {/* The scene lights are neutral-to-warm. A blue key and a blue-white sky
        tinted every vertex colour on the balloon towards a hue the ramp does
        not contain, so the surface never matched its own legend. */}
    <hemisphereLight args={['#f4ece0', '#1b1917', 1.8]}/>
    <directionalLight position={[3, 4, 5]} intensity={2.7}/>
    <directionalLight position={[-3, -1, -2]} intensity={.9} color="#c9a98f"/>
    <mesh geometry={geometry}>
      <meshStandardMaterial vertexColors side={DoubleSide} roughness={.32} metalness={.08}/>
    </mesh>
    <mesh geometry={geometry} scale={1.003}>
      <meshBasicMaterial color={tokens.foreground} wireframe transparent opacity={.105} depthWrite={false}/>
    </mesh>
    <axesHelper args={[1.22]}/>
    <OrbitControls
      key={resetNonce}
      makeDefault
      enableDamping
      dampingFactor={.075}
      rotateSpeed={.75}
      zoomSpeed={.9}
      panSpeed={.65}
      minDistance={1.35}
      maxDistance={7}
      target={[0, 0, 0]}
    />
  </>;
}

export default function InteractiveBalloon({ result, frequencyIndex }: { result: ResultPayload; frequencyIndex: number }) {
  const [resetNonce, setResetNonce] = useState(0);
  return <div className="balloon-3d" role="img" aria-label="Interactive 3D directivity balloon">
    <Canvas
      camera={{ position: [...BALLOON_CAMERA], fov: 38, near: .05, far: 50 }}
      dpr={[1, 2]}
      frameloop="demand"
      gl={{ antialias: true, alpha: true }}
    >
      <BalloonScene result={result} frequencyIndex={frequencyIndex} resetNonce={resetNonce}/>
    </Canvas>
    <div className="balloon-3d-help" aria-hidden="true">Drag rotate · Wheel zoom · Right-drag pan</div>
    <button className="balloon-3d-reset" type="button" onClick={() => setResetNonce((value) => value + 1)}>Reset view</button>
  </div>;
}
