from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts import bootstrap


@pytest.mark.parametrize("selector,module", [("official", "server.solver.beat_runtime.cli"),
                                            (" official ", "server.solver.beat_runtime.cli"),
                                            ("Official", "hornlab_beat_bem.provision"),
                                            ("hbb", "hornlab_beat_bem.provision"),
                                            ("", "hornlab_beat_bem.provision")])
def test_shared_selector_bootstrap_commands(monkeypatch, selector, module):
    monkeypatch.setenv("WG2_BEAT_PROVIDER", selector)
    monkeypatch.delenv("WG2_SKIP_GPU_PROVISION", raising=False)
    monkeypatch.delenv("WG2_SKIP_BEAT_CPU_PROVISION", raising=False)
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    calls = []
    monkeypatch.setattr(bootstrap, "_run", lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(bootstrap, "_beat_provision_facts", lambda python: {"cpu": True, "gpu": "metal", "per_backend": True})
    bootstrap._provision_beat_runtime(Path("python"))
    assert calls == [["python", "-c", "import beat_engine" if module == "server.solver.beat_runtime.cli" else "import hornlab_beat_bem.provision"],
                     ["python", "-m", module, "--if-gpu"], ["python", "-m", module, "--backend", "cpu"]]


def test_optional_official_engine_absent_skips_setup(monkeypatch):
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    calls = []
    monkeypatch.setattr(bootstrap, "_run", lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=1))
    bootstrap._provision_beat_runtime(Path("python"))
    assert calls == [["python", "-c", "import beat_engine"]]


@pytest.mark.parametrize("skip", ["WG2_SKIP_GPU_PROVISION", "WG2_SKIP_BEAT_CPU_PROVISION"])
def test_official_bootstrap_preserves_skip_flags(monkeypatch, skip):
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    monkeypatch.setenv(skip, "1")
    monkeypatch.setattr(bootstrap, "_run", lambda *args, **kwargs: pytest.fail("opted-out stage ran"))
    action = bootstrap._provision_gpu_runtime if skip == "WG2_SKIP_GPU_PROVISION" else bootstrap._provision_beat_cpu_runtime
    action(Path("python"))


def test_official_provision_failure_keeps_bootstrap_nonfatal(monkeypatch, capsys):
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    monkeypatch.delenv("WG2_SKIP_BEAT_CPU_PROVISION", raising=False)
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Windows")
    monkeypatch.setattr(bootstrap, "_run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    bootstrap._provision_beat_cpu_runtime(Path("python"))
    assert "WARNING" in capsys.readouterr().out


def test_app_module_is_importable_from_bootstrap_subprocess_cwd(tmp_path, monkeypatch, capfd):
    monkeypatch.chdir(tmp_path)
    # Force absence even if a developer has the optional engine installed.
    (tmp_path / "beat_engine.py").write_text("raise ImportError('absent fixture engine')\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("WG2_BEAT_RUNTIME_DIR", str(tmp_path / "data"))
    root = tmp_path / "runtime"
    command = [sys.executable, "-m", "server.solver.beat_runtime.cli"]
    completed = bootstrap._run([*command, "status", "--backend", "cpu", "--dir", str(root)])
    assert completed.returncode == 0
    assert json.loads(capfd.readouterr().out)["cpu"]["state"] == "package-unusable"
    completed = bootstrap._run([*command, "--backend", "cpu", "--dir", str(root)])
    assert completed.returncode == 1 and "not importable" in capfd.readouterr().err
    assert not root.exists() and not (tmp_path / "data").exists()
