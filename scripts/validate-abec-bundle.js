#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import JSZip from 'jszip';

import { validateAbecBundle } from '../src/export/abecBundleValidator.js';

function printUsage() {
    console.log([
        'Usage:',
        '  node scripts/validate-abec-bundle.js <bundle-path> [--mode=free|infinite|auto] [--allow-missing-geo]',
        '',
        'Examples:',
        '  node scripts/validate-abec-bundle.js _references/testconfigs/260112aolo1/ABEC_FreeStanding --mode=free',
        '  node scripts/validate-abec-bundle.js /tmp/export.zip --mode=auto'
    ].join('\n'));
}

function parseArgs(argv) {
    const options = {
        mode: 'auto',
        requireBemMeshGeo: true
    };
    const positional = [];

    argv.forEach((arg) => {
        if (arg === '--allow-missing-geo') {
            options.requireBemMeshGeo = false;
            return;
        }
        if (arg.startsWith('--mode=')) {
            options.mode = arg.slice('--mode='.length).trim().toLowerCase();
            return;
        }
        if (arg === '--help' || arg === '-h') {
            options.help = true;
            return;
        }
        positional.push(arg);
    });

    return { options, positional };
}

function modeToContractMode(modeText, fallbackFromName = '') {
    const mode = String(modeText || '').toLowerCase();
    if (mode === 'free') return 'ABEC_FreeStanding';
    if (mode === 'infinite') return 'ABEC_InfiniteBaffle';
    if (mode !== 'auto') return null;

    const name = fallbackFromName.toLowerCase();
    if (name.includes('infinite')) return 'ABEC_InfiniteBaffle';
    if (name.includes('free')) return 'ABEC_FreeStanding';
    return null;
}

function readEntriesFromDirectory(rootDir) {
    const entries = {};

    function walk(currentDir) {
        const children = fs.readdirSync(currentDir, { withFileTypes: true });
        children.forEach((child) => {
            const absolutePath = path.join(currentDir, child.name);
            if (child.isDirectory()) {
                walk(absolutePath);
                return;
            }
            const relPath = path.relative(rootDir, absolutePath).replace(/\\/g, '/');
            entries[relPath] = fs.readFileSync(absolutePath, 'utf8');
        });
    }

    walk(rootDir);
    return entries;
}

async function readEntriesFromZip(zipPath) {
    const data = fs.readFileSync(zipPath);
    const zip = await JSZip.loadAsync(data);
    const entries = {};

    const tasks = [];
    Object.values(zip.files).forEach((zipEntry) => {
        if (zipEntry.dir) return;
        tasks.push(
            zipEntry.async('string').then((content) => {
                entries[zipEntry.name] = content;
            })
        );
    });
    await Promise.all(tasks);
    return entries;
}

function splitBundles(entries) {
    const projectFiles = Object.keys(entries).filter((key) => key === 'Project.abec' || key.endsWith('/Project.abec'));
    if (projectFiles.length <= 1) {
        return [{ name: '', entries }];
    }

    const bundles = [];
    projectFiles.forEach((projectPath) => {
        const root = projectPath === 'Project.abec'
            ? ''
            : projectPath.slice(0, -'/Project.abec'.length);
        const subset = {};
        Object.entries(entries).forEach(([key, value]) => {
            if (!root || key === root || key.startsWith(`${root}/`)) {
                const relative = root ? key.slice(root.length + 1) : key;
                if (relative) subset[relative] = value;
            }
        });
        bundles.push({ name: root, entries: subset });
    });
    return bundles;
}

async function main() {
    const { options, positional } = parseArgs(process.argv.slice(2));
    if (options.help || positional.length !== 1) {
        printUsage();
        process.exitCode = options.help ? 0 : 1;
        return;
    }

    const inputPath = path.resolve(positional[0]);
    if (!fs.existsSync(inputPath)) {
        console.error(`[abec-validator] input does not exist: ${inputPath}`);
        process.exitCode = 1;
        return;
    }

    const stat = fs.statSync(inputPath);
    let rawEntries;
    if (stat.isDirectory()) {
        rawEntries = readEntriesFromDirectory(inputPath);
    } else if (stat.isFile() && inputPath.toLowerCase().endsWith('.zip')) {
        rawEntries = await readEntriesFromZip(inputPath);
    } else {
        console.error('[abec-validator] input must be an ABEC bundle directory or a .zip file');
        process.exitCode = 1;
        return;
    }

    const bundleSets = splitBundles(rawEntries);
    let hadErrors = false;

    bundleSets.forEach((bundleSet) => {
        const mode = modeToContractMode(options.mode, bundleSet.name);
        const result = validateAbecBundle(bundleSet.entries, {
            mode,
            requireBemMeshGeo: options.requireBemMeshGeo
        });

        const label = bundleSet.name || path.basename(inputPath);
        if (result.ok) {
            console.log(`[abec-validator] PASS ${label}`);
            if (result.warnings.length > 0) {
                result.warnings.forEach((warning) => {
                    console.log(`  warning: ${warning}`);
                });
            }
            return;
        }

        hadErrors = true;
        console.log(`[abec-validator] FAIL ${label}`);
        result.errors.forEach((error) => {
            console.log(`  error: ${error}`);
        });
        result.warnings.forEach((warning) => {
            console.log(`  warning: ${warning}`);
        });
    });

    process.exitCode = hadErrors ? 1 : 0;
}

main().catch((err) => {
    console.error(`[abec-validator] fatal: ${err.message}`);
    process.exitCode = 1;
});

