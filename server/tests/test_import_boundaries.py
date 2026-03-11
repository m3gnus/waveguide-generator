import ast
import unittest
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]


class ImportBoundaryTest(unittest.TestCase):
    def _iter_py_files(self, subdir: str):
        base = SERVER_ROOT / subdir
        for path in sorted(base.rglob('*.py')):
            if path.name.startswith('.'):
                continue
            yield path

    def _import_roots(self, file_path: Path):
        tree = ast.parse(file_path.read_text(encoding='utf-8'), filename=str(file_path))
        roots = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    roots.add(node.module.split('.')[0])

        return roots

    def _import_from_nodes(self, file_path: Path):
        tree = ast.parse(file_path.read_text(encoding='utf-8'), filename=str(file_path))
        return [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]

    def test_server_tests_do_not_import_app_module_shortcuts(self):
        violations = []
        for test_file in self._iter_py_files('tests'):
            roots = self._import_roots(test_file)
            if 'app' in roots:
                rel = test_file.relative_to(SERVER_ROOT)
                violations.append(str(rel))

        self.assertEqual(
            violations,
            [],
            'Server tests must import target modules directly, not via app.py shortcuts: '
            + ', '.join(violations),
        )

    def test_api_package_does_not_import_app_module(self):
        violations = []
        for api_file in self._iter_py_files('api'):
            roots = self._import_roots(api_file)
            if 'app' in roots:
                rel = api_file.relative_to(SERVER_ROOT)
                violations.append(str(rel))

        self.assertEqual(
            violations,
            [],
            'server/api must not import app.py: ' + ', '.join(violations),
        )

    def test_app_module_only_imports_assembly_dependencies(self):
        app_file = SERVER_ROOT / 'app.py'
        violations = []

        allowed_route_import = {'router'}
        allowed_job_runtime_import = {'startup_jobs_runtime'}
        allowed_solver_bootstrap_import = {
            'GMSH_OCC_RUNTIME_READY',
            'SOLVER_AVAILABLE',
            'WAVEGUIDE_BUILDER_AVAILABLE',
        }

        for node in self._import_from_nodes(app_file):
            module = node.module
            names = {alias.name for alias in node.names}

            if module == 'models':
                violations.append('app.py must not re-export request/response models')
            elif module and module.startswith('api.routes_'):
                bad_names = sorted(names - allowed_route_import)
                if bad_names:
                    violations.append(
                        f"{module} imports non-router names: {', '.join(bad_names)}"
                    )
            elif module == 'services.job_runtime':
                bad_names = sorted(names - allowed_job_runtime_import)
                if bad_names:
                    violations.append(
                        'services.job_runtime imports non-lifecycle names: '
                        + ', '.join(bad_names)
                    )
            elif module and module.startswith('services.'):
                violations.append(f'app.py must not import {module}')
            elif module == 'solver_bootstrap':
                bad_names = sorted(names - allowed_solver_bootstrap_import)
                if bad_names:
                    violations.append(
                        'solver_bootstrap imports non-runtime-status names: '
                        + ', '.join(bad_names)
                    )

        self.assertEqual(
            violations,
            [],
            'server/app.py must stay as assembly only: ' + '; '.join(violations),
        )

    def test_services_package_does_not_import_api_package(self):
        violations = []
        for service_file in self._iter_py_files('services'):
            roots = self._import_roots(service_file)
            if 'api' in roots:
                rel = service_file.relative_to(SERVER_ROOT)
                violations.append(str(rel))

        self.assertEqual(
            violations,
            [],
            'server/services must not import server/api modules: ' + ', '.join(violations),
        )

    def test_solver_package_does_not_import_api_or_services_packages(self):
        violations = []
        for solver_file in self._iter_py_files('solver'):
            roots = self._import_roots(solver_file)
            bad = sorted(root for root in ('api', 'services') if root in roots)
            if bad:
                rel = solver_file.relative_to(SERVER_ROOT)
                violations.append(f"{rel} imports {', '.join(bad)}")

        self.assertEqual(
            violations,
            [],
            'server/solver must not import server/api or server/services modules: '
            + '; '.join(violations),
        )


if __name__ == '__main__':
    unittest.main()
