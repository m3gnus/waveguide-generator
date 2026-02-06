/**
 * Tessellator - Convert OpenCascade B-Rep to triangle mesh for Three.js display.
 *
 * Uses BRepMesh_IncrementalMesh for tessellation and TopExp_Explorer
 * to extract triangulated face data.
 */

/**
 * Tessellate a TopoDS_Shape into vertex/index/normal arrays.
 *
 * @param {Object} oc - OpenCascade instance
 * @param {TopoDS_Shape} shape - The B-Rep shape to tessellate
 * @param {Object} options
 * @param {number} options.linearDeflection - Max linear deviation from true surface (mm), default 0.5
 * @param {number} options.angularDeflection - Max angular deviation (radians), default 0.3
 * @returns {{ vertices: Float32Array, indices: Uint32Array, normals: Float32Array }}
 */
export function tessellate(oc, shape, options = {}) {
    const linearDeflection = options.linearDeflection ?? 0.5;
    const angularDeflection = options.angularDeflection ?? 0.3;

    // Perform incremental meshing on the shape
    new oc.BRepMesh_IncrementalMesh_2(shape, linearDeflection, false, angularDeflection, true);

    const vertices = [];
    const indices = [];
    const normals = [];

    let globalVertexOffset = 0;

    // Iterate over all faces in the shape
    const explorer = new oc.TopExp_Explorer_2(
        shape,
        oc.TopAbs_ShapeEnum.TopAbs_FACE,
        oc.TopAbs_ShapeEnum.TopAbs_SHAPE
    );

    while (explorer.More()) {
        const face = oc.TopoDS.Face_1(explorer.Current());
        const location = new oc.TopLoc_Location_1();
        const triangulationHandle = oc.BRep_Tool.Triangulation(face, location);

        if (!triangulationHandle.IsNull()) {
            const triangulation = triangulationHandle.get();
            const nbNodes = triangulation.NbNodes();
            const nbTriangles = triangulation.NbTriangles();
            const transform = location.Transformation();

            // Check face orientation for normal direction
            const faceOrientation = face.Orientation_1();
            const reversed = faceOrientation === oc.TopAbs_Orientation.TopAbs_REVERSED;

            // Extract vertices (1-indexed in OpenCascade)
            for (let i = 1; i <= nbNodes; i++) {
                const node = triangulation.Node(i);
                // Apply location transformation
                const transformed = node.Transformed(transform);
                vertices.push(transformed.X(), transformed.Y(), transformed.Z());
            }

            // Extract normals if available
            if (triangulation.HasNormals()) {
                for (let i = 1; i <= nbNodes; i++) {
                    const normal = triangulation.Normal(i);
                    const sign = reversed ? -1 : 1;
                    normals.push(normal.X() * sign, normal.Y() * sign, normal.Z() * sign);
                }
            } else {
                // Placeholder normals (will be computed later)
                for (let i = 0; i < nbNodes; i++) {
                    normals.push(0, 1, 0);
                }
            }

            // Extract triangle indices (1-indexed, convert to 0-indexed with offset)
            for (let i = 1; i <= nbTriangles; i++) {
                const tri = triangulation.Triangle(i);
                let n1 = tri.Value(1) - 1 + globalVertexOffset;
                let n2 = tri.Value(2) - 1 + globalVertexOffset;
                let n3 = tri.Value(3) - 1 + globalVertexOffset;

                // Respect face orientation
                if (reversed) {
                    indices.push(n1, n3, n2);
                } else {
                    indices.push(n1, n2, n3);
                }
            }

            globalVertexOffset += nbNodes;
        }

        explorer.Next();
    }

    console.log(`[Tessellator] Generated ${vertices.length / 3} vertices, ${indices.length / 3} triangles`);

    return {
        vertices: new Float32Array(vertices),
        indices: new Uint32Array(indices),
        normals: new Float32Array(normals)
    };
}

/**
 * Tessellate with face tagging for MSH export.
 * Returns per-triangle face group IDs.
 *
 * @param {Object} oc - OpenCascade instance
 * @param {TopoDS_Shape} shape - The B-Rep shape
 * @param {Object} options - Tessellation options
 * @returns {{ vertices: Float32Array, indices: Uint32Array, normals: Float32Array, faceGroups: Int32Array }}
 */
export function tessellateWithGroups(oc, shape, options = {}) {
    const linearDeflection = options.linearDeflection ?? 0.5;
    const angularDeflection = options.angularDeflection ?? 0.3;

    new oc.BRepMesh_IncrementalMesh_2(shape, linearDeflection, false, angularDeflection, true);

    const vertices = [];
    const indices = [];
    const normals = [];
    const faceGroups = [];

    let globalVertexOffset = 0;
    let faceIndex = 0;

    const explorer = new oc.TopExp_Explorer_2(
        shape,
        oc.TopAbs_ShapeEnum.TopAbs_FACE,
        oc.TopAbs_ShapeEnum.TopAbs_SHAPE
    );

    while (explorer.More()) {
        const face = oc.TopoDS.Face_1(explorer.Current());
        const location = new oc.TopLoc_Location_1();
        const triangulationHandle = oc.BRep_Tool.Triangulation(face, location);

        if (!triangulationHandle.IsNull()) {
            const triangulation = triangulationHandle.get();
            const nbNodes = triangulation.NbNodes();
            const nbTriangles = triangulation.NbTriangles();
            const transform = location.Transformation();
            const faceOrientation = face.Orientation_1();
            const reversed = faceOrientation === oc.TopAbs_Orientation.TopAbs_REVERSED;

            for (let i = 1; i <= nbNodes; i++) {
                const node = triangulation.Node(i);
                const transformed = node.Transformed(transform);
                vertices.push(transformed.X(), transformed.Y(), transformed.Z());
            }

            if (triangulation.HasNormals()) {
                for (let i = 1; i <= nbNodes; i++) {
                    const normal = triangulation.Normal(i);
                    const sign = reversed ? -1 : 1;
                    normals.push(normal.X() * sign, normal.Y() * sign, normal.Z() * sign);
                }
            } else {
                for (let i = 0; i < nbNodes; i++) {
                    normals.push(0, 1, 0);
                }
            }

            for (let i = 1; i <= nbTriangles; i++) {
                const tri = triangulation.Triangle(i);
                let n1 = tri.Value(1) - 1 + globalVertexOffset;
                let n2 = tri.Value(2) - 1 + globalVertexOffset;
                let n3 = tri.Value(3) - 1 + globalVertexOffset;

                if (reversed) {
                    indices.push(n1, n3, n2);
                } else {
                    indices.push(n1, n2, n3);
                }
                faceGroups.push(faceIndex);
            }

            globalVertexOffset += nbNodes;
        }

        faceIndex++;
        explorer.Next();
    }

    return {
        vertices: new Float32Array(vertices),
        indices: new Uint32Array(indices),
        normals: new Float32Array(normals),
        faceGroups: new Int32Array(faceGroups)
    };
}
