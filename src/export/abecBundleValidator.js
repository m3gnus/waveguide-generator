const DEFAULT_REQUIRED_FILES = Object.freeze([
    'Project.abec',
    'solving.txt',
    'observation.txt',
    'Results/coords.txt',
    'Results/static.txt'
]);

export const ATH_ABEC_PARITY_CONTRACT = Object.freeze({
    referenceBundle: '_references/testconfigs/260112aolo1/ABEC_FreeStanding',
    requiredFiles: DEFAULT_REQUIRED_FILES,
    requiredProjectSections: ['Project', 'Solving', 'Observation', 'MeshFiles'],
    requiredSolvingElements: ['SD1G0', 'SD1D1001'],
    requiredPhysicalGroups: ['SD1G0', 'SD1D1001'],
    requireBemMeshGeo: true,
    minimumPolarBlocks: 1
});

function normalizePathKey(input) {
    const text = String(input || '').replace(/\\/g, '/').replace(/^\.\/+/, '');
    return text.startsWith('/') ? text.slice(1) : text;
}

function withAutoRoot(entries) {
    const normalized = new Map();
    Object.entries(entries || {}).forEach(([key, value]) => {
        normalized.set(normalizePathKey(key), value);
    });

    if (normalized.has('Project.abec')) {
        return normalized;
    }

    const projectEntries = [...normalized.keys()].filter((key) => key.endsWith('/Project.abec'));
    if (projectEntries.length !== 1) {
        return normalized;
    }

    const rootPrefix = projectEntries[0].slice(0, -'/Project.abec'.length);
    const trimmed = new Map();
    normalized.forEach((value, key) => {
        if (key === rootPrefix) return;
        if (key.startsWith(`${rootPrefix}/`)) {
            trimmed.set(key.slice(rootPrefix.length + 1), value);
        }
    });
    return trimmed.size > 0 ? trimmed : normalized;
}

function parseSectionAssignments(text) {
    const sectionMap = new Map();
    let currentSection = '';

    String(text || '')
        .replace(/\r\n/g, '\n')
        .split('\n')
        .forEach((rawLine) => {
            const line = rawLine.trim();
            if (!line) return;
            if (line.startsWith('[') && line.endsWith(']')) {
                currentSection = line.slice(1, -1);
                if (!sectionMap.has(currentSection)) {
                    sectionMap.set(currentSection, []);
                }
                return;
            }
            const splitAt = line.indexOf('=');
            if (splitAt <= 0) return;
            const key = line.slice(0, splitAt).trim();
            const value = line.slice(splitAt + 1).trim();
            if (!sectionMap.has(currentSection)) {
                sectionMap.set(currentSection, []);
            }
            sectionMap.get(currentSection).push({ key, value });
        });

    return sectionMap;
}

function findSectionValue(sectionMap, sectionName, keyName) {
    const sectionEntries = sectionMap.get(sectionName);
    if (!sectionEntries) return null;
    const hit = sectionEntries.find((entry) => entry.key === keyName);
    return hit ? hit.value : null;
}

function extractMeshIncludeNames(solvingText) {
    const includes = [];
    const lines = String(solvingText || '').replace(/\r\n/g, '\n').split('\n');
    lines.forEach((line) => {
        const match = line.match(/\bMesh\s+Include\s+([A-Za-z0-9_\-]+)/);
        if (match) includes.push(match[1]);
    });
    return includes;
}

function parseMshPhysicalNames(mshText) {
    const lines = String(mshText || '').replace(/\r\n/g, '\n').split('\n');
    const physicalNames = new Map();
    let idx = 0;
    while (idx < lines.length) {
        if (lines[idx].trim() !== '$PhysicalNames') {
            idx += 1;
            continue;
        }
        const count = Number(lines[idx + 1] || 0);
        for (let i = 0; i < count; i += 1) {
            const row = (lines[idx + 2 + i] || '').trim();
            const match = row.match(/^(\d+)\s+(\d+)\s+"([^"]+)"$/);
            if (match) {
                physicalNames.set(match[3], {
                    dim: Number(match[1]),
                    tag: Number(match[2])
                });
            }
        }
        break;
    }
    return physicalNames;
}

function extractObservationPolarBlocks(observationText) {
    const blocks = [];
    const lines = String(observationText || '').replace(/\r\n/g, '\n').split('\n');
    let index = 0;

    while (index < lines.length) {
        if (lines[index].trim() !== 'BE_Spectrum') {
            index += 1;
            continue;
        }

        const blockLines = [];
        index += 1;
        while (index < lines.length) {
            const line = lines[index];
            const trimmed = line.trim();
            if (!trimmed) {
                if (blockLines.length > 0) break;
                index += 1;
                continue;
            }
            if (trimmed === 'BE_Spectrum') break;
            if (/^[A-Za-z0-9_]+$/.test(trimmed) && !trimmed.includes('=')) break;
            blockLines.push(trimmed);
            index += 1;
        }

        const joined = blockLines.join('\n');
        const headerMatch = joined.match(/GraphHeader="([^"]+)"/);
        const polarRangeMatch = joined.match(/PolarRange=([^\n]+)/);
        const inclinationMatch = joined.match(/Inclination=([^\s]+)/);
        blocks.push({
            graphHeader: headerMatch ? headerMatch[1] : null,
            polarRange: polarRangeMatch ? polarRangeMatch[1].trim() : null,
            inclination: inclinationMatch ? inclinationMatch[1].trim() : null
        });
    }

    return blocks;
}

export function validateAbecBundle(entries, options = {}) {
    const normalizedEntries = withAutoRoot(entries);
    const contract = {
        ...ATH_ABEC_PARITY_CONTRACT,
        ...options.contract
    };

    const mode = options.mode || null;
    const requireBemMeshGeo = options.requireBemMeshGeo ?? contract.requireBemMeshGeo;
    const errors = [];
    const warnings = [];

    contract.requiredFiles.forEach((requiredFile) => {
        if (!normalizedEntries.has(requiredFile)) {
            errors.push(`Missing required file: ${requiredFile}`);
        }
    });
    if (requireBemMeshGeo && !normalizedEntries.has('bem_mesh.geo')) {
        errors.push('Missing required file: bem_mesh.geo');
    }

    const projectText = normalizedEntries.get('Project.abec');
    const solvingText = normalizedEntries.get('solving.txt');
    const observationText = normalizedEntries.get('observation.txt');

    let meshFileName = null;
    if (projectText) {
        const sectionMap = parseSectionAssignments(projectText);
        contract.requiredProjectSections.forEach((sectionName) => {
            if (!sectionMap.has(sectionName)) {
                errors.push(`Project.abec missing section [${sectionName}]`);
            }
        });

        const solvingRef = findSectionValue(sectionMap, 'Solving', 'Scriptname_Solving');
        const observationRef = findSectionValue(sectionMap, 'Observation', 'C0');
        const meshRef = findSectionValue(sectionMap, 'MeshFiles', 'C0');
        if (!solvingRef) {
            errors.push('Project.abec missing Solving/Scriptname_Solving');
        } else if (!normalizedEntries.has(solvingRef)) {
            errors.push(`Project.abec references missing solving file: ${solvingRef}`);
        }
        if (!observationRef) {
            errors.push('Project.abec missing Observation/C0');
        } else if (!normalizedEntries.has(observationRef)) {
            errors.push(`Project.abec references missing observation file: ${observationRef}`);
        }
        if (!meshRef) {
            errors.push('Project.abec missing MeshFiles/C0');
        } else {
            const [meshName] = meshRef.split(',');
            meshFileName = (meshName || '').trim();
            if (!meshFileName) {
                errors.push('Project.abec has empty mesh file reference in MeshFiles/C0');
            } else if (!normalizedEntries.has(meshFileName)) {
                errors.push(`Project.abec references missing mesh file: ${meshFileName}`);
            }
        }
    }

    let includes = [];
    if (solvingText) {
        if (!/\bControl_Solver\b/.test(solvingText)) {
            errors.push('solving.txt missing Control_Solver block');
        }
        if (!/\bMeshFile_Properties\b/.test(solvingText)) {
            errors.push('solving.txt missing MeshFile_Properties block');
        }
        if (!/Driving\s+"S1001"/.test(solvingText)) {
            errors.push('solving.txt missing Driving "S1001" block');
        }

        includes = extractMeshIncludeNames(solvingText);
        contract.requiredSolvingElements.forEach((name) => {
            if (!includes.includes(name)) {
                errors.push(`solving.txt missing required mesh include: ${name}`);
            }
        });

        const hasInfiniteBaffle = /\bInfinite_Baffle\b/.test(solvingText);
        if (mode === 'ABEC_InfiniteBaffle' && !hasInfiniteBaffle) {
            errors.push('solving.txt missing Infinite_Baffle block for ABEC_InfiniteBaffle mode');
        }
        if (mode === 'ABEC_FreeStanding' && hasInfiniteBaffle) {
            errors.push('solving.txt contains Infinite_Baffle block for ABEC_FreeStanding mode');
        }
    }

    if (observationText) {
        if (!/\bDriving_Values\b/.test(observationText)) {
            errors.push('observation.txt missing Driving_Values block');
        }
        if (!/\bRadiation_Impedance\b/.test(observationText)) {
            errors.push('observation.txt missing Radiation_Impedance block');
        }
        const polarBlocks = extractObservationPolarBlocks(observationText);
        if (polarBlocks.length < contract.minimumPolarBlocks) {
            errors.push(
                `observation.txt has ${polarBlocks.length} BE_Spectrum block(s); expected at least ${contract.minimumPolarBlocks}`
            );
        }
        polarBlocks.forEach((block, idx) => {
            if (!block.graphHeader) {
                errors.push(`observation.txt BE_Spectrum #${idx + 1} missing GraphHeader`);
            }
            if (!block.polarRange) {
                errors.push(`observation.txt BE_Spectrum #${idx + 1} missing PolarRange`);
            }
            if (!block.inclination) {
                errors.push(`observation.txt BE_Spectrum #${idx + 1} missing Inclination`);
            }
        });
    }

    if (meshFileName && normalizedEntries.has(meshFileName)) {
        const physicalNames = parseMshPhysicalNames(normalizedEntries.get(meshFileName));
        contract.requiredPhysicalGroups.forEach((name) => {
            if (!physicalNames.has(name)) {
                errors.push(`mesh missing required physical group: ${name}`);
            }
        });
        includes.forEach((name) => {
            if (!physicalNames.has(name)) {
                errors.push(`solving.txt references physical group not present in mesh: ${name}`);
            }
        });
        if (physicalNames.size === 0) {
            warnings.push('mesh has no $PhysicalNames entries');
        }
    }

    return {
        ok: errors.length === 0,
        errors,
        warnings,
        details: {
            mode,
            meshFileName
        }
    };
}

