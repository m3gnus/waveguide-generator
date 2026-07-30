"""Tests for the bempp-cl OpenCL workarounds.

These pin a real, measured defect: on an install path containing a space,
bempp-cl's unquoted -I include option made its OpenCL assembly path fail to
compile, silently forcing the much slower numba backend. On the reference horn
mesh that cost 3.63x on warm sweeps (2.582 s/freq -> 0.711 s/freq) and 80x on
cold start (62.74 s -> 0.78 s).
"""

from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch

from solver import bempp_compat


class _FakeKernels:
    def __init__(self, include_path):
        self._INCLUDE_PATH = include_path


def _reset_state():
    bempp_compat._state.update(
        applied=False,
        includePathQuoted=False,
        cacheEnvDefaulted=False,
        reason="not applied",
    )


class QuoteIncludePathTest(unittest.TestCase):
    def setUp(self):
        _reset_state()
        self.addCleanup(_reset_state)

    def _with_fake_bempp(self, include_path):
        """Install a fake bempp_cl.core.opencl_kernels for the duration."""
        kernels = _FakeKernels(include_path)
        core = types.ModuleType("bempp_cl.core")
        core.opencl_kernels = kernels
        root = types.ModuleType("bempp_cl")
        root.core = core
        modules = {
            "bempp_cl": root,
            "bempp_cl.core": core,
            "bempp_cl.core.opencl_kernels": kernels,
        }
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        return kernels

    def test_path_with_space_is_quoted(self):
        """The regression case: a space in the path split the -I option."""
        kernels = self._with_fake_bempp(r"C:\Users\me\Hornlab - Workspace\bempp_cl\core\sources\include")

        status = bempp_compat.apply_bempp_opencl_workarounds()

        self.assertTrue(status["includePathQuoted"])
        self.assertEqual(
            kernels._INCLUDE_PATH,
            r'"C:\Users\me\Hornlab - Workspace\bempp_cl\core\sources\include"',
        )
        # pyopencl joins options on spaces, so the quoted form must survive as
        # a single token.
        self.assertTrue(kernels._INCLUDE_PATH.startswith('"'))
        self.assertTrue(kernels._INCLUDE_PATH.endswith('"'))

    def test_path_without_space_is_left_alone(self):
        """No-op on hosts that never had the problem."""
        original = r"C:\hornlab\bempp_cl\core\sources\include"
        kernels = self._with_fake_bempp(original)

        status = bempp_compat.apply_bempp_opencl_workarounds()

        self.assertFalse(status["includePathQuoted"])
        self.assertEqual(kernels._INCLUDE_PATH, original)

    def test_already_quoted_path_is_not_double_quoted(self):
        original = r'"C:\Users\me\My Projects\include"'
        kernels = self._with_fake_bempp(original)

        bempp_compat.apply_bempp_opencl_workarounds()

        self.assertEqual(kernels._INCLUDE_PATH, original)

    def test_is_idempotent(self):
        kernels = self._with_fake_bempp(r"C:\a b\include")

        bempp_compat.apply_bempp_opencl_workarounds()
        first = kernels._INCLUDE_PATH
        bempp_compat.apply_bempp_opencl_workarounds()

        self.assertEqual(kernels._INCLUDE_PATH, first)

    def test_missing_bempp_is_reported_not_raised(self):
        with patch.dict(sys.modules, {"bempp_cl": None, "bempp_cl.core": None}):
            status = bempp_compat.apply_bempp_opencl_workarounds()

        self.assertTrue(status["applied"])
        self.assertFalse(status["includePathQuoted"])
        self.assertIn("unavailable", status["reason"].lower())


class CacheFailureEnvTest(unittest.TestCase):
    def setUp(self):
        _reset_state()
        self.addCleanup(_reset_state)

    def test_missing_cache_env_var_is_defaulted(self):
        """pyopencl's cache error handler indexes this without a default.

        When a build fails and the variable is unset, the handler itself raises
        KeyError('PYOPENCL_CACHE_FAILURE_FATAL'), destroying the real compiler
        error. Providing a falsy default keeps the true diagnostic visible.
        """
        env = {k: v for k, v in os.environ.items() if k != "PYOPENCL_CACHE_FAILURE_FATAL"}
        with patch.dict(os.environ, env, clear=True):
            status = bempp_compat.apply_bempp_opencl_workarounds()

            self.assertTrue(status["cacheEnvDefaulted"])
            self.assertIn("PYOPENCL_CACHE_FAILURE_FATAL", os.environ)
            self.assertFalse(bool(os.environ["PYOPENCL_CACHE_FAILURE_FATAL"]))

    def test_existing_cache_env_var_is_respected(self):
        with patch.dict(os.environ, {"PYOPENCL_CACHE_FAILURE_FATAL": "1"}):
            status = bempp_compat.apply_bempp_opencl_workarounds()

            self.assertFalse(status["cacheEnvDefaulted"])
            self.assertEqual(os.environ["PYOPENCL_CACHE_FAILURE_FATAL"], "1")


if __name__ == "__main__":
    unittest.main()
