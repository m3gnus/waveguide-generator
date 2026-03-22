import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export default {
    mode: 'production',
    entry: './src/main.js',
    output: {
        path: path.resolve(__dirname, 'dist'),
        filename: 'bundle.js',
        clean: true
    },
    externalsType: 'module',
    externals: {
        // Three.js loaded via importmap in index.html
        'three': 'three',
        'three/addons/controls/OrbitControls.js': 'three/addons/controls/OrbitControls.js',
        'three/addons/exporters/STLExporter.js': 'three/addons/exporters/STLExporter.js'
    },
    experiments: {
        outputModule: true
    },
    module: {
        rules: [
            {
                test: /\.wasm$/,
                type: 'asset/resource'
            }
        ]
    },
    resolve: {
        extensions: ['.js'],
        fallback: {
            fs: false,
            path: false,
            perf_hooks: false,
            os: false,
            worker_threads: false,
            crypto: false,
            stream: false
        }
    }
};
