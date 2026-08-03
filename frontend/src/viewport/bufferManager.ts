import { BufferAttribute, BufferGeometry, DynamicDrawUsage } from 'three';

export interface SurfaceBuffers {
  positions: Float32Array;
  normals: Float32Array;
  indices: Uint32Array;
  colors?: Float32Array | null;
}

export type BufferUpdate = 'replaced' | 'reused';

function dynamicAttribute(array: Float32Array | Uint32Array, itemSize: number): BufferAttribute {
  return new BufferAttribute(array, itemSize).setUsage(DynamicDrawUsage);
}

export class SurfaceBufferManager {
  readonly geometry = new BufferGeometry();
  private byteLengths: [number, number, number] | null = null;

  update(buffers: SurfaceBuffers): BufferUpdate {
    const nextLengths: [number, number, number] = [
      buffers.positions.byteLength,
      buffers.normals.byteLength,
      buffers.indices.byteLength,
    ];
    const reusable = this.byteLengths?.every((length, index) => length === nextLengths[index]) ?? false;
    if (!reusable) {
      if (this.byteLengths) this.geometry.dispose();
      this.geometry.deleteAttribute('position');
      this.geometry.deleteAttribute('normal');
      this.geometry.deleteAttribute('color');
      this.geometry.setIndex(null);
      this.geometry.setAttribute('position', dynamicAttribute(buffers.positions, 3));
      this.geometry.setAttribute('normal', dynamicAttribute(buffers.normals, 3));
      this.geometry.setIndex(dynamicAttribute(buffers.indices, 1));
      if (buffers.colors) this.geometry.setAttribute('color', dynamicAttribute(buffers.colors, 3));
      this.byteLengths = nextLengths;
      this.geometry.boundingBox = null;
      this.geometry.boundingSphere = null;
      return 'replaced';
    }

    const position = this.geometry.getAttribute('position') as BufferAttribute;
    const normal = this.geometry.getAttribute('normal') as BufferAttribute;
    const index = this.geometry.getIndex() as BufferAttribute;
    position.array = buffers.positions;
    normal.array = buffers.normals;
    index.array = buffers.indices;
    position.needsUpdate = true;
    normal.needsUpdate = true;
    index.needsUpdate = true;
    if (buffers.colors) {
      const color = this.geometry.getAttribute('color') as BufferAttribute | undefined;
      if (color && color.array.byteLength === buffers.colors.byteLength) {
        color.array = buffers.colors;
        color.needsUpdate = true;
      } else {
        if (color) this.geometry.dispose();
        this.geometry.setAttribute('color', dynamicAttribute(buffers.colors, 3));
      }
    }
    this.geometry.boundingBox = null;
    this.geometry.boundingSphere = null;
    return 'reused';
  }

  dispose(): void {
    this.geometry.dispose();
    this.byteLengths = null;
  }
}
