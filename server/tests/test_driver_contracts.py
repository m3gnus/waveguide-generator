"""Driver contracts remain independent of job orchestration."""

import subprocess
import sys
import textwrap

from server.contracts import DriverSpec, JobModel
from server.jobs.models import DriverSpec as LegacyDriverSpec, JobModel as LegacyJobModel
from server.solver.driver_lem import DriverSpec as SolverDriverSpec


def test_legacy_and_solver_imports_share_the_contract_classes():
    assert DriverSpec is LegacyDriverSpec is SolverDriverSpec
    assert JobModel is LegacyJobModel
    assert issubclass(DriverSpec, JobModel)


def test_driver_contract_validates_without_other_server_packages():
    # A fresh interpreter catches transitive dependencies hidden by pytest's
    # already-imported jobs/solver modules. The linter also checks type imports.
    source = textwrap.dedent("""
        import importlib.abc
        import sys

        class ContractsOnly(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.startswith('server.') and not (
                    fullname == 'server.contracts'
                    or fullname.startswith('server.contracts.')
                ):
                    raise AssertionError(f'non-leaf import: {fullname}')

        sys.meta_path.insert(0, ContractsOnly())
        from pydantic import ValidationError
        from server.contracts import DriverSpec

        values = dict(sd_cm2=210, bl_t_m=10.5, re_ohm=5.3,
                      mmd_g=12, cms_m_per_n=4e-4, label='  Driver  ')
        spec = DriverSpec.model_validate(values)
        assert spec.label == 'Driver'
        assert spec.count == 1
        assert spec.le_mh == 0
        for changes in (
            dict(unrecognized=1), dict(sd_cm2=float('inf')),
            dict(mms_g=13), dict(mmd_g=None),
            dict(cms_m_per_n=None), dict(le2_mh=1.4), dict(re2_ohm=8),
        ):
            try:
                DriverSpec.model_validate(values | changes)
            except ValidationError:
                pass
            else:
                raise AssertionError(f'invalid driver accepted: {changes}')
    """)
    result = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
