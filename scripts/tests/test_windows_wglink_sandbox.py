"""Inventory and process-boundary guards for automated WG sandboxing.

Intentional user installs/relaunches in scripts/install*, scripts/uninstall*,
platform wrappers, generated shortcuts and launchers, and statusapp updater are
exempt: they operate on the user's installation. gate7-prepare only prints a
manual action. CI/preflight compile or run pytest (root conftest owns isolation).
RC DMG mount/ditto and Linux install --no-launch do not start WG or WGLink;
imported-same-mesh/ingest qualifiers only run fixture jobs. Backend/interpreter,
process-table, memory and sweep probes are checked separately from app starts.

PowerShell below is a deliberately bounded parser, not a general interpreter.
It tokenizes commands/assignments and tracks path values and directory lifetime
at each process boundary. Unknown launch syntax fails closed rather than making
an invocation disappear from the inventory. Python launch environments are
captured in test_build_bundle and the qualifier process-boundary tests.
"""

import ast
from dataclasses import dataclass
from pathlib import Path
import re
import shlex

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]

# Strings stay atomic, including escaped quotes, so comment/command words in
# strings cannot introduce a fake invocation. Parenthesized argument arrays and
# backtick continuations remain part of the same statement.
TOKEN = re.compile(
    r'"(?:`.|[^"`])*"|\'(?:\'\'|[^\'])*\'|'
    r'\$[\w:]+(?:\.[\w]+)*(?:\[\d+\])?(?:\.[\w]+)*|'
    r'[^\s(){}\[\],;=|&]+|[^\s]', re.I
)


def statements(source: str) -> list[list[str]]:
    result, current = [], []
    depth = 0
    # Tokenize newlines as separators outside strings/comments.
    lexer = re.compile(TOKEN.pattern + r'|\n', re.I)
    for line in source.splitlines(keepends=True):
        tokens = list(lexer.finditer(line))
        for index, match in enumerate(tokens):
            token = match.group()
            if token.startswith('#'):
                break
            if token == '`' and index + 1 == len(tokens) - 1:
                continue
            if token == '\n':
                continued = line.rstrip('\n').rstrip().endswith(('`', '|'))
                if depth == 0 and not continued:
                    if current:
                        result.append(current)
                    current = []
            elif token in (';', '{', '}') and depth == 0:
                if current:
                    result.append(current)
                current = []
            else:
                current.append(token)
                if token in ('(', '['):
                    depth += 1
                elif token in (')', ']'):
                    depth -= 1
        # A comment consumes its newline too.
        if tokens and any(m.group().startswith('#') for m in tokens) and depth == 0:
            if current:
                result.append(current)
            current = []
    if current:
        result.append(current)
    return result


@dataclass(frozen=True)
class PrivatePath:
    root: str
    parts: tuple[str, ...] = ()

    def child(self, name: str) -> 'PrivatePath':
        parts = tuple(part for part in re.split(r'[\\/]', name) if part)
        assert '..' not in parts and not any('$' in part or ':' in part for part in parts), 'unresolved or escaping sandbox path'
        return PrivatePath(self.root, self.parts + parts)

    def contains(self, other: 'PrivatePath') -> bool:
        return self.root == other.root and other.parts[:len(self.parts)] == self.parts


def path_value(tokens: list[str], values: dict[str, PrivatePath]) -> PrivatePath | None:
    if not tokens:
        return None
    if tokens[0].lower() == 'join-path':
        parent = values.get(tokens[1].lower())
        if parent and len(tokens) == 3 and tokens[2][0] in ('"', "'"):
            return parent.child(tokens[2][1:-1])
        if tokens[1].lower() in ('$env:temp', '$env:runner_temp'):
            # Only a per-run unique root qualifies, not an ambient directory.
            if any('newguid' in token.lower() for token in tokens):
                return PrivatePath(' '.join(tokens).lower())
        return None
    if len(tokens) == 1:
        token = tokens[0].lower()
        if token in values:
            return values[token]
        if token.startswith('"$') and token.endswith('"'):
            match = re.fullmatch(r'"(\$[\w:]+)(.*)"', token)
            if match and match[1] in values:
                return values[match[1]].child(match[2])
    return None


def assert_private_launches(source: str, *, expected: int = 5) -> None:
    values: dict[str, PrivatePath] = {}
    directories: set[PrivatePath] = set()
    launches = []
    for tokens in statements(source):
        lower = [token.lower() for token in tokens]
        if len(tokens) > 2 and tokens[0].startswith('$') and tokens[1] == '=':
            name = lower[0]
            value = path_value(tokens[2:], values)
            values.pop(name, None)
            if value:
                values[name] = value
        if 'new-item' in lower and '-itemtype' in lower:
            if lower[lower.index('-itemtype') + 1] == 'directory':
                # Recognize variable paths and parenthesized Join-Path values.
                for token in lower[lower.index('new-item') + 1:]:
                    if token in values:
                        directories.add(values[token])
                if '(' in tokens:
                    start = tokens.index('(') + 1
                    value = path_value(tokens[start:tokens.index(')', start)], values)
                    if value:
                        directories.add(value)
        if 'remove-item' in lower:
            removed = [path for token in tokens if (path := path_value([token], values))]
            directories = {path for path in directories if not any(p.contains(path) for p in removed)}
        if len(tokens) > 2 and tokens[0].startswith('$') and tokens[1] in ('+', '-') and tokens[2] == '=':
            values.pop(lower[0], None)
        # Changes whose filesystem/variable semantics this bounded parser does
        # not model must be reviewed instead of silently passing.
        if ('invoke-expression' in lower or
            any(command in lower for command in ('set-variable', 'clear-variable', 'remove-variable')) or
            (any(command in lower for command in ('move-item', 'rename-item', 'clear-item')) and
             any(token in values for token in lower))):
            raise AssertionError('unmodelled sandbox mutation or execution')
        if 'start-process' in lower:
            start = lower.index('start-process')
            command = tokens[start:]
        elif '&' in tokens:
            command = tokens[tokens.index('&'):]
        elif tokens[0].startswith('$') and len(tokens) > 1 and tokens[1].startswith(('/', '-')):
            command = tokens
        else:
            continue
        launches.append(command)
        flags = [token.lower() for token in command]
        if flags[0] == 'start-process':
            allowed = {'-filepath', '-argumentlist', '-passthru', '-wait', '-nonewwindow'}
            assert all(not token.startswith('-') or token in allowed for token in flags), 'unreviewed process option'
            target_index = flags.index('-filepath') + 1 if '-filepath' in flags else 1
            assert flags[target_index] in ('$setup', '$unins.fullname'), 'unreviewed executable identity'
        else:
            assert flags[1] in ('$setup', '$unins.fullname'), 'unreviewed executable identity'
        assert not set(flags) & {'-usenewenvironment', '-environment', '-credential', '-verb'}, 'child environment replaced'
        assert not any(token.startswith('@') and token != '@' for token in command), 'unmodelled splatted launch'
        data = values.get('$env:wg2_data_dir')
        assert data and data in directories, 'private data must exist at launch'
        overrides = [re.fullmatch(r'/wglinkaddinsdir=(?:`")?(\$[\w:]+)(?:`")?',
                                  token[1:-1].lower() if token.startswith('"') else token.lower())
                     for token in command]
        overrides = [match for match in overrides if match]
        assert len(overrides) == 1, f'AddIns isolation missing: {command}'
        addins = values.get(overrides[0][1])
        assert addins and addins in directories, 'private AddIns must exist at launch'
        assert addins.root == data.root, 'data/AddIns must share the private work area'
        # Silent setup skips [Run], which otherwise launches with user defaults.
        assert any('/verysilent' in token.lower() for token in command), 'interactive setup may launch WG'
    assert len(launches) == expected, f'unreviewed process inventory: {launches}'


def test_every_gate_setup_and_uninstall_is_sandboxed() -> None:
    source = (ROOT / 'installers/windows/gates.ps1').read_text()
    assert_private_launches(source)
    assert 'finally {' in source
    assert '$env:WG2_DATA_DIR = $previousDataDir' in source


ENTRYPOINTS = ('build_bundle.py', 'gates.ps1', 'qualify_installed_cpu.py', 'qualify_installed_quit.py')


@pytest.mark.parametrize('workflow_name,counts', [('rc-build', (3, 1, 3, 3)), ('release', (3, 1, 0, 0))])
def test_workflow_execution_inventory(workflow_name: str, counts: tuple[int, ...]) -> None:
    workflow = yaml.safe_load((ROOT / f'.github/workflows/{workflow_name}.yml').read_text())
    found = dict.fromkeys(ENTRYPOINTS, 0)
    installer_count = 0
    for job in workflow['jobs'].values():
        for step in job.get('steps', []):
            source = step.get('run', '')
            if step.get('shell') == 'pwsh':
                commands = statements(source)
                launches = [t for t in commands if any(x.lower() == 'start-process' for x in t)]
                if launches:
                    assert_private_launches(source, expected=1)
                    installer_count += 1
                for tokens in commands:
                    # Every call-operator invocation must delegate to an inventoried
                    # boundary; aliases/positional native starts are not exemptions.
                    if '&' in tokens:
                        target = tokens[tokens.index('&') + 1]
                        assert target.lower() in ('./installers/windows/gates.ps1', './scripts/ci/verify_inno_setup.ps1'), f'unreviewed launch: {tokens}'
                    if tokens[0].startswith('$') and len(tokens) > 1 and tokens[1].startswith(('/', '-')):
                        raise AssertionError(f'unreviewed positional launch: {tokens}')
            else:
                commands = [shlex.split(line, comments=True) for line in source.replace('\\\n', ' ').splitlines()]
            for tokens in commands:
                for token in tokens:
                    name = token.replace('\\', '/').rsplit('/', 1)[-1].strip('"\'')
                    if name in found:
                        found[name] += 1
                    # No independent app/launcher/setup command may hide behind
                    # the delegated build/qualification boundary.
                    assert name.lower() not in (
                        'serve.py', 'install_wglink.py', 'launch-wg.sh', 'launch-wg.bat',
                    ), tokens
                if tokens and (tokens[0].lower().endswith('.exe') or
                               tokens[0].replace('\\', '/').rsplit('/', 1)[-1].lower() == 'waveguide-generator'):
                    raise AssertionError(f'unreviewed literal executable launch: {tokens}')
    assert tuple(found.values()) == counts
    assert installer_count == (1 if workflow_name == 'rc-build' else 0)


@pytest.mark.parametrize('mutation', ['missing-override', 'missing-data', 'late-data', 'missing-mkdir',
                                     'candidate', 'call-operator', 'real-reassignment', 'new-environment', 'deleted-addins'])
def test_sandbox_check_rejects_unsafe_invocations(mutation: str) -> None:
    source = (ROOT / 'installers/windows/gates.ps1').read_text()
    boundary = '$p = Start-Process'
    if mutation == 'missing-override':
        source = re.sub(r', "/WGLINKADDINSDIR=[^\n]+?"(?= -PassThru)', '', source, count=1)
    elif mutation in ('missing-data', 'late-data'):
        source = source.replace('$env:WG2_DATA_DIR = $gateData', '')
        if mutation == 'late-data':
            source += '\n$env:WG2_DATA_DIR = $gateData\n'
    elif mutation == 'missing-mkdir':
        source = source.replace('New-Item -ItemType Directory -Force $wglinkAddins, $developerAddins, $gateData | Out-Null', '')
    elif mutation == 'candidate':
        source = source.replace(boundary, '$candidate = $Setup\nStart-Process -FilePath $candidate -Wait\n' + boundary, 1)
    elif mutation == 'call-operator':
        source = source.replace(boundary, '& $Setup /VERYSILENT\n' + boundary, 1)
    elif mutation == 'real-reassignment':
        source = source.replace(boundary, '$wglinkAddins = "$env:APPDATA\\Autodesk\\Autodesk Fusion 360\\API\\AddIns"\n' + boundary, 1)
    elif mutation == 'new-environment':
        source = source.replace('Start-Process -FilePath', 'Start-Process -UseNewEnvironment -FilePath', 1)
    else:
        source = source.replace(boundary, 'Remove-Item -Recurse -Force $wglinkAddins\n' + boundary, 1)
    assert source != (ROOT / 'installers/windows/gates.ps1').read_text()
    with pytest.raises(AssertionError):
        assert_private_launches(source)


def test_python_app_launch_inventory() -> None:
    # Enumerate every subprocess site in the qualifiers, including probes that
    # do not start WG. A new run/Popen/system call must be explicitly reviewed.
    expected = {
        'scripts/qualify_installed_cpu.py': {
            ('__init__', 'subprocess.Popen'): 1,
            ('check_pins', 'subprocess.run'): 1,
            ('diagnose_preparation', 'subprocess.run'): 1,
            ('stop_our_workers', 'subprocess.run'): 1,
            ('qualify_imported_return', 'Server'): 2,
            ('qualify', 'Server'): 1,
        },
        'scripts/qualify_installed_quit.py': {
            ('start', 'subprocess.Popen'): 1,
            ('sweep_in_runtime', 'subprocess.run'): 1,
            ('memory_ceiling', 'subprocess.run'): 1,
            ('process_table', 'subprocess.run'): 1,
            ('_kill', 'subprocess.run'): 1,
            ('run_gate', 'Run.start'): 2,
        },
        'scripts/build_bundle.py': {
            ('verify_bundle', 'self.process_factory'): 1,
            ('verify_bundle', 'self.runner'): 2,
            ('verify_bundle', 'self.run_command'): 1,
            ('verify_windows_bare_launch', 'self.process_factory'): 1,
            ('verify_linux_bare_launch', 'self.process_factory'): 1,
            ('_terminate_process_tree', 'self.runner'): 1,
        },
    }
    for path, inventory in expected.items():
        tree = ast.parse((ROOT / path).read_text())
        launches = {}
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if path.endswith('build_bundle.py') and function.name not in {key[0] for key in inventory}:
                continue  # Build-time compilers/package tools, before verification.
            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue
                callee = ast.unparse(node.func)
                if (callee.startswith('subprocess.') and callee.rsplit('.', 1)[-1] in ('run', 'Popen', 'call', 'check_call', 'check_output') or
                    callee in ('os.system', 'os.popen', 'self.process_factory', 'self.runner', 'self.run_command', 'Server', 'Run.start')):
                    key = (function.name, callee)
                    launches[key] = launches.get(key, 0) + 1
                    if callee in ('subprocess.Popen', 'self.process_factory'):
                        assert any(k.arg in ('env', None) for k in node.keywords), 'missing child environment'
        assert launches == inventory, f'unreviewed Python launch in {path}: {launches}'



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


def assert_early_override_refusal(source: str) -> None:
    validator = source.split('function ValidateWgLinkAddInsOverride(', 1)[1].split('\nfunction ', 1)[0]
    assert 'if not WgLinkAddInsOverrideSpecified() then\n    exit;' in validator
    assert "OverrideDir := ExpandConstant('{param:WGLINKADDINSDIR|}');" in validator
    assert "if (OverrideDir <> '') and DirExists(OverrideDir) then\n    exit;" in validator
    assert "Refusing /WGLINKADDINSDIR: supply an existing directory" in validator
    assert 'Log(Reason);' in validator
    assert 'if not Silent then\n    MsgBox(Reason, mbError, MB_OK);' in validator
    assert validator.rstrip().endswith('Result := False;\nend;')
    setup = source.split('function InitializeSetup(): Boolean;', 1)[1].split('\nfunction ', 1)[0]
    assert 'begin\n  Result := ValidateWgLinkAddInsOverride(WizardSilent());\n  if not Result then\n    exit;' in setup
    uninstall = source.split('function InitializeUninstall(): Boolean;', 1)[1].split('\nfunction ', 1)[0]
    assert 'Result := ValidateWgLinkAddInsOverride(UninstallSilent());' in uninstall
    assert uninstall.rstrip().endswith('end;')
    # Both initialization events return False (Inno's abort/nonzero contract).
    # Silent refusal must not be moved to the modal PrepareToInstall path.
    preparation = source.split('function PrepareToInstall(', 1)[1]
    assert 'WgLink' not in preparation


def test_invalid_override_refuses_setup_and_uninstall_during_initialization() -> None:
    assert_early_override_refusal((ROOT / 'installers/windows/bundle-setup.iss').read_text())


@pytest.mark.parametrize('mutation', ['setup-bypass', 'uninstall-bypass', 'success', 'silent-dialog', 'no-log', 'no-empty-guard'])
def test_early_refusal_guard_detects_bypasses(mutation: str) -> None:
    source = (ROOT / 'installers/windows/bundle-setup.iss').read_text()
    replacements = {
        'setup-bypass': ('Result := ValidateWgLinkAddInsOverride(WizardSilent());', 'Result := True;'),
        'uninstall-bypass': ('Result := ValidateWgLinkAddInsOverride(UninstallSilent());', 'Result := True;'),
        'success': ('  Result := False;\nend;\n\nfunction InitializeUninstall', '  Result := True;\nend;\n\nfunction InitializeUninstall'),
        'silent-dialog': ('if not Silent then\n    MsgBox(Reason', 'if Silent then\n    MsgBox(Reason'),
        'no-log': ('Log(Reason);', ''),
        'no-empty-guard': ("if (OverrideDir <> '') and DirExists(OverrideDir) then\n    exit;", 'if DirExists(OverrideDir) then\n    exit;'),
    }
    old, new = replacements[mutation]
    changed = source.replace(old, new, 1)
    assert changed != source
    with pytest.raises(AssertionError):
        assert_early_override_refusal(changed)


@pytest.mark.parametrize('kind', ['cpu', 'quit'])
def test_qualifier_server_process_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """No sockets or server needed to assert the actual Popen environment."""
    from scripts import qualify_installed_cpu as cpu, qualify_installed_quit as quit_gate

    class Captured(Exception):
        pass

    work = tmp_path / 'work'
    app = tmp_path / 'app'
    data = work / 'data'
    for name in ('WG2_DATA_DIR', 'WG2_FUSION_ADDINS_DIR'):
        monkeypatch.setenv(name, str(tmp_path / 'ambient' / name))
    environment = cpu.isolated_environment(app, work)

    def capture(command, **options):
        env = options['env']
        assert Path(env['WG2_DATA_DIR']) == data
        assert Path(env['WG2_FUSION_ADDINS_DIR']) == work / 'fusion-addins'
        assert all(Path(env[name]).is_dir() for name in ('WG2_DATA_DIR', 'WG2_FUSION_ADDINS_DIR'))
        assert command[command.index('--data-dir') + 1] == str(data)
        raise Captured

    monkeypatch.setattr(cpu.subprocess, 'Popen', capture)
    monkeypatch.setattr(cpu, 'free_port', lambda: 43110)
    with pytest.raises(Captured):
        if kind == 'cpu':
            cpu.Server(Path('python'), app, environment, data, work / 'control', work / 'server.log')
        else:
            quit_gate.Run.start(Path('python'), app, environment, data, work)
