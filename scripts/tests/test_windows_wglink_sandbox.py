"""Automated Windows installer runs must never use real data or Fusion AddIns."""

from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def installer_launches(source: str) -> list[tuple[int, str]]:
    launches = []
    for match in re.finditer(r"Start-Process\b[^\n]*", source, re.IGNORECASE):
        command = match.group()
        if not re.search(r"-FilePath\s+\$(?:setup\b|unins\.FullName)", command, re.IGNORECASE):
            continue
        if command.rstrip().endswith("@("):
            command += source[match.end():source.index("\n", source.index(")", match.end()))]
        launches.append((match.start(), command))
    assert launches, "no installer invocations were checked"
    return launches


def assert_private_launches(source: str, *, workflow: bool = False) -> None:
    launches = installer_launches(source)
    if workflow:
        assert '$root = Join-Path $env:RUNNER_TEMP ' in source
        data_assignment = '$env:WG2_DATA_DIR = Join-Path $root "data"'
        variables = {"addins": '$addins = Join-Path $root "fusion-addins"'}
    else:
        assert '$gateRoot = Join-Path $env:TEMP ' in source
        assert '$gateData = Join-Path $gateRoot "data"' in source
        data_assignment = '$env:WG2_DATA_DIR = $gateData'
        variables = {
            "wglinkAddins": '$wglinkAddins = Join-Path $gateRoot "Fusion\\API\\AddIns"',
            "developerAddins": '$developerAddins = Join-Path $gateRoot "Developer\\API\\AddIns"',
        }
    assert data_assignment in source
    assigned_at = source.index(data_assignment)
    for offset, command in launches:
        assert assigned_at < offset, "data isolation must precede every installer"
        between = source[assigned_at + len(data_assignment):offset]
        assert not re.search(r"\$env:WG2_DATA_DIR\s*=", between), "data isolation was replaced"
        override = re.search(r"/WGLINKADDINSDIR=(?:`\")?\$(\w+)", command)
        assert override, f"AddIns isolation missing: {command}"
        variable = override[1]
        assert variable in variables
        before = source[:offset]
        assert variables[variable] in before
        creations = re.findall(r"New-Item -ItemType Directory[^\n]+", before)
        assert any(f"${variable}" in creation for creation in creations), "AddIns must exist first"
        data_variable = "$env:WG2_DATA_DIR" if workflow else "$gateData"
        assert any(data_variable in creation for creation in creations), "private data must exist first"
        assert not re.search(r"Remove-Item[^\n]*\$gateRoot", before), "fixture removed before launch"


def test_every_gate_setup_and_uninstall_is_sandboxed() -> None:
    source = (ROOT / "installers/windows/gates.ps1").read_text()
    assert_private_launches(source)
    assert 'finally {' in source
    assert '$env:WG2_DATA_DIR = $previousDataDir' in source


def test_every_windows_rc_qualification_installer_is_sandboxed() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/rc-build.yml").read_text())
    sources = [
        step["run"] for step in workflow["jobs"]["windows-bundle"]["steps"]
        if "Start-Process" in step.get("run", "")
    ]
    assert sources
    for source in sources:
        assert_private_launches(source, workflow=True)


@pytest.mark.parametrize("mutation", ["missing-override", "missing-data", "late-data", "missing-mkdir"])
def test_sandbox_check_rejects_unsafe_invocations(mutation: str) -> None:
    source = (ROOT / "installers/windows/gates.ps1").read_text()
    if mutation == "missing-override":
        source = re.sub(r', "/WGLINKADDINSDIR=[^\n]+?"(?= -PassThru)', '', source, count=1)
    elif mutation == "missing-data":
        source = source.replace('$env:WG2_DATA_DIR = $gateData', '')
    elif mutation == "late-data":
        source = source.replace('$env:WG2_DATA_DIR = $gateData', '') + '\n$env:WG2_DATA_DIR = $gateData\n'
    else:
        source = source.replace('New-Item -ItemType Directory -Force $wglinkAddins, $developerAddins, $gateData | Out-Null', '')
    with pytest.raises(AssertionError):
        assert_private_launches(source)


def assert_closed_override(source: str) -> None:
    specified = source.split('function WgLinkAddInsOverrideSpecified()', 1)[1].split('function WgLinkAddInsDirectory()', 1)[0]
    assert "Argument = '/WGLINKADDINSDIR'" in specified
    assert "Pos('/WGLINKADDINSDIR=', Argument) = 1" in specified
    resolver = source.split('function WgLinkAddInsDirectory()', 1)[1].split('function WgLinkTarget', 1)[0]
    override, fallback = resolver.split('  Legacy :=', 1)
    assert 'if WgLinkAddInsOverrideSpecified() then' in override
    assert "Result := '';" in override
    assert "if (OverrideDir <> '') and DirExists(OverrideDir) then" in override
    assert "else\n      Log('WGLink: invalid /WGLINKADDINSDIR" in override
    assert override.rstrip().endswith('exit;\n  end;')
    assert 'userappdata' in fallback
    install = source.split('procedure InstallWGLink()', 1)[1].split('procedure UninstallWGLink()', 1)[0]
    assert "'WGLink could not be installed because /WGLINKADDINSDIR" in install
    for procedure in ('InstallWGLink', 'UninstallWGLink'):
        body = source.split(f'procedure {procedure}()', 1)[1].split('\nprocedure ', 1)[0]
        assert body.index("if AddInsDirectory = '' then") < body.index('Target := WgLinkTarget')
        assert 'exit;' in body[body.index("if AddInsDirectory = '' then"):body.index('Target := WgLinkTarget')]


def test_invalid_addins_override_exits_before_real_fusion_discovery() -> None:
    assert_closed_override((ROOT / "installers/windows/bundle-setup.iss").read_text())


@pytest.mark.parametrize("mutation", ["fallthrough", "valid-only-guard", "missing-failure-status"])
def test_override_check_rejects_fallback_or_missing_failure(mutation: str) -> None:
    source = (ROOT / "installers/windows/bundle-setup.iss").read_text()
    if mutation == "fallthrough":
        source = source.replace("    exit;\n  end;\n\n  Legacy :=", "  end;\n\n  Legacy :=", 1)
    elif mutation == "valid-only-guard":
        source = source.replace("if WgLinkAddInsOverrideSpecified() then", "if (OverrideDir <> '') and DirExists(OverrideDir) then", 1)
    else:
        source = source.replace("WGLink could not be installed because /WGLINKADDINSDIR", "WGLink skipped because /WGLINKADDINSDIR", 1)
    with pytest.raises(AssertionError):
        assert_closed_override(source)
