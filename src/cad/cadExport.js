/**
 * STEP Export - Write OpenCascade B-Rep shapes to STEP format.
 *
 * Uses STEPControl_Writer to produce AP214 STEP files
 * via the Emscripten virtual filesystem.
 */

/**
 * Export a TopoDS_Shape to STEP format.
 *
 * @param {Object} oc - OpenCascade instance
 * @param {TopoDS_Shape} shape - The shape to export
 * @param {string} filename - Virtual filesystem filename (default: 'horn.step')
 * @returns {Uint8Array} The STEP file contents as a byte array
 */
export function exportToSTEP(oc, shape, filename = 'horn.step') {
    const writer = new oc.STEPControl_Writer_1();

    // Transfer shape to STEP writer
    const status = writer.Transfer(
        shape,
        oc.STEPControl_StepModelType.STEPControl_AsIs,
        true,
        new oc.Message_ProgressRange_1()
    );

    if (status !== oc.IFSelect_ReturnStatus.IFSelect_RetDone) {
        throw new Error(`[STEPExport] Transfer failed with status: ${status}`);
    }

    // Write to Emscripten virtual filesystem
    const writeStatus = writer.Write(filename);
    if (writeStatus !== oc.IFSelect_ReturnStatus.IFSelect_RetDone) {
        throw new Error(`[STEPExport] Write failed with status: ${writeStatus}`);
    }

    // Read the file back from Emscripten FS
    const data = oc.FS.readFile('/' + filename);
    console.log(`[STEPExport] Exported ${data.length} bytes to ${filename}`);

    // Clean up virtual file
    try {
        oc.FS.unlink('/' + filename);
    } catch (e) {
        // Ignore cleanup errors
    }

    return data;
}

/**
 * Export shape to STEP and trigger browser download.
 *
 * @param {Object} oc - OpenCascade instance
 * @param {TopoDS_Shape} shape - The shape to export
 * @param {string} downloadName - The filename for the download
 */
export function downloadSTEP(oc, shape, downloadName = 'horn.step') {
    const data = exportToSTEP(oc, shape, 'export.step');
    const blob = new Blob([data], { type: 'application/step' });
    const url = URL.createObjectURL(blob);

    const link = document.createElement('a');
    link.href = url;
    link.download = downloadName;
    link.click();

    URL.revokeObjectURL(url);
}
