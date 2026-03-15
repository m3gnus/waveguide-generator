const PARAMETER_SECTION_INVENTORY = Object.freeze({
    geometry: [
        Object.freeze({
            id: 'model-type',
            title: 'Model Type',
            owner: 'paramPanel',
            kind: 'model-selector'
        }),
        Object.freeze({
            id: 'core-profile',
            title: 'Profile Dimensions',
            description: 'Primary dimensions for the selected horn family. Labels keep the canonical ATH symbol in parentheses where it helps.',
            owner: 'paramPanel',
            groups: Object.freeze([
                Object.freeze({
                    group: 'R-OSSE',
                    whenTypes: Object.freeze(['R-OSSE']),
                    keys: Object.freeze(['scale', 'R', 'a', 'a0', 'r0', 'k', 'm', 'b', 'r', 'q', 'tmax'])
                }),
                Object.freeze({
                    group: 'OSSE',
                    whenTypes: Object.freeze(['OSSE']),
                    keys: Object.freeze(['scale', 'L', 'a', 'a0', 'r0', 'k', 's', 'n', 'q', 'h'])
                })
            ])
        }),
        Object.freeze({
            id: 'morph-target',
            title: 'Morph Target',
            description: 'Post-profile shaping used by OSSE designs to transition the mouth toward another target shape.',
            owner: 'paramPanel',
            whenTypes: Object.freeze(['OSSE']),
            groups: Object.freeze([
                Object.freeze({
                    group: 'MORPH',
                    keys: Object.freeze([
                        'morphTarget',
                        'morphWidth',
                        'morphHeight',
                        'morphCorner',
                        'morphRate',
                        'morphFixed',
                        'morphAllowShrinkage'
                    ])
                })
            ])
        }),
        Object.freeze({
            id: 'wall-enclosure',
            title: 'Wall & Enclosure',
            description: 'Freestanding wall-shell controls and enclosure clearances that change the exported or simulated solid.',
            owner: 'paramPanel',
            groups: Object.freeze([
                Object.freeze({
                    group: 'MESH',
                    keys: Object.freeze(['wallThickness'])
                }),
                Object.freeze({
                    group: 'ENCLOSURE',
                    keys: Object.freeze([
                        'encDepth',
                        'encEdge',
                        'encEdgeType',
                        'encSpaceL',
                        'encSpaceT',
                        'encSpaceR',
                        'encSpaceB'
                    ])
                })
            ])
        }),
        Object.freeze({
            id: 'profile-path',
            title: 'Profile Path & Guiding Curve',
            description: 'Advanced throat, rotation, and guiding-curve controls used to bend or infer the horn profile.',
            owner: 'paramPanel',
            groups: Object.freeze([
                Object.freeze({
                    group: 'GEOMETRY',
                    keys: Object.freeze([
                        'throatProfile',
                        'throatExtAngle',
                        'throatExtLength',
                        'slotLength',
                        'rot',
                        'gcurveType',
                        'gcurveDist',
                        'gcurveWidth',
                        'gcurveAspectRatio',
                        'gcurveSeN',
                        'gcurveSf',
                        'gcurveSfA',
                        'gcurveSfB',
                        'gcurveSfM1',
                        'gcurveSfM2',
                        'gcurveSfN1',
                        'gcurveSfN2',
                        'gcurveSfN3',
                        'gcurveRot',
                        'circArcTermAngle',
                        'circArcRadius'
                    ])
                })
            ])
        })
    ],
    simulation: [
        Object.freeze({
            id: 'frequency-sweep',
            title: 'Frequency Sweep',
            description: 'Backend BEM sweep start, end, and sample count. These stay aligned with import and export config keys.',
            owner: 'paramPanel',
            groups: Object.freeze([
                Object.freeze({
                    group: 'SIMULATION',
                    keys: Object.freeze(['freqStart', 'freqEnd', 'numFreqs'])
                })
            ])
        }),
        Object.freeze({
            id: 'directivity-map',
            title: 'Directivity Map',
            description: 'Polar planes and angular sampling used for directivity exports and plots.',
            owner: 'polarSettings'
        }),
        Object.freeze({
            id: 'source-definition',
            title: 'Source Definition',
            description: 'Source surface, orientation, and contour inputs used to build the radiating boundary.',
            owner: 'paramPanel',
            groups: Object.freeze([
                Object.freeze({
                    group: 'SOURCE',
                    keys: Object.freeze(['sourceShape', 'sourceRadius', 'sourceCurv', 'sourceVelocity', 'sourceContours'])
                })
            ])
        }),
        Object.freeze({
            id: 'preview-mesh',
            title: 'Preview Mesh',
            description: 'Three.js tessellation controls for the live viewport only. They do not change backend OCC or BEM mesh sizes.',
            owner: 'paramPanel',
            groups: Object.freeze([
                Object.freeze({
                    group: 'MESH',
                    keys: Object.freeze([
                        'angularSegments',
                        'lengthSegments',
                        'cornerSegments',
                        'throatSegments',
                        'throatSliceDensity'
                    ])
                })
            ])
        }),
        Object.freeze({
            id: 'solve-export-mesh',
            title: 'Solve & Export Mesh',
            description: 'Backend OCC mesh sizing and export-coordinate controls used for solves, downloads, and persisted mesh artifacts.',
            owner: 'paramPanel',
            groups: Object.freeze([
                Object.freeze({
                    group: 'MESH',
                    keys: Object.freeze(['throatResolution', 'mouthResolution', 'rearResolution', 'verticalOffset', 'quadrants'])
                }),
                Object.freeze({
                    group: 'ENCLOSURE',
                    keys: Object.freeze(['encFrontResolution', 'encBackResolution'])
                })
            ])
        })
    ]
});

function cloneGroup(group) {
    return {
        ...group,
        keys: Array.isArray(group.keys) ? [...group.keys] : []
    };
}

function matchesModelType(entry, modelType) {
    return !Array.isArray(entry.whenTypes) || entry.whenTypes.includes(modelType);
}

export function getParameterSections(tab, modelType) {
    const sections = PARAMETER_SECTION_INVENTORY[tab] || [];
    return sections
        .filter((section) => matchesModelType(section, modelType))
        .map((section) => ({
            ...section,
            groups: Array.isArray(section.groups)
                ? section.groups.filter((group) => matchesModelType(group, modelType)).map(cloneGroup)
                : []
        }));
}

export function getParameterSection(tab, sectionId, modelType) {
    return getParameterSections(tab, modelType).find((section) => section.id === sectionId) || null;
}

export { PARAMETER_SECTION_INVENTORY };
