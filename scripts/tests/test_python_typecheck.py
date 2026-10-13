"""Prove the configured gate rejects a type defect in a selected boundary."""

from pathlib import Path
import subprocess
import sys


def test_typecheck_rejects_wrong_runtime_identity_return(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    original = root / "shared" / "runtime_id.py"
    shadow = tmp_path / "runtime_id.py"
    source = original.read_text(encoding="utf-8")
    assert "return digest.hexdigest()[:12]" in source
    shadow.write_text(
        source.replace("return digest.hexdigest()[:12]", "return 42"), encoding="utf-8"
    )
    checked = subprocess.run(
        [
            sys.executable, "-m", "mypy", "--cache-dir", str(tmp_path / "cache"),
            "--shadow-file", str(original), str(shadow),
        ],
        cwd=root, capture_output=True, text=True, check=False, timeout=60,
    )
    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert "shared/runtime_id.py" in checked.stdout
    assert "[return-value]" in checked.stdout
