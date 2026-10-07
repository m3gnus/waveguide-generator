"""Inventory and process-boundary guards for automated WG sandboxing.

Intentional user installs/relaunches in scripts/install*, scripts/uninstall*,
platform wrappers, generated shortcuts and launchers, and statusapp updater are
exempt: they operate on the user's installation. gate7-prepare only prints a
manual action. CI/preflight compile or run pytest (root conftest owns isolation).
RC DMG mount/ditto and Linux install --no-launch do not start WG or WGLink;
imported-same-mesh/ingest qualifiers only run fixture jobs. Backend/interpreter,
process-table, memory and sweep probes are checked separately from app starts.

PowerShell command positions, types and member calls are allowlisted. Only the
checked helper bodies may start processes; sandbox inputs and environment writes
are fixed contracts. Python launch environments are captured in test_build_bundle
and the qualifier process-boundary tests.
"""

import ast
import hashlib
from pathlib import Path
import re
import shlex

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]

# This is a deliberately small, fail-closed PowerShell grammar. It discovers
# command positions independently of command names, including nested pipelines
# and expandable-string subexpressions. Unsupported syntax needs review.
LEXEME = re.compile(
    r'\[(?:[\w.]+(?:\[\])?|Parameter\(Mandatory\s*=\s*\$true\))\]|'
    r'\$[\w:]+|\.[A-Za-z_]\w*|::|(?<=::)[A-Za-z_]\w*|[+\-*/]?=|\+\+|--|'
    r'[^\s(){}\[\],;=|&\'"`]+|[^\s]', re.I
)
COMMANDS = frozenset('''
    Get-Item Get-ChildItem Test-Path Join-Path New-Item Remove-Item Wait-Process
    Stop-Process Get-Content Get-FileHash Sort-Object Where-Object ForEach-Object
    Group-Object Out-Null Set-Content ConvertFrom-Json ConvertTo-Json Select-Object
    New-Object Split-Path Rename-Item Start-Sleep Unblock-File
    Get-Process Get-CimInstance Select-String Copy-Item Get-ItemProperty
    Start-SandboxedSetup Start-StandIn Start-SandboxedNativeProbe Stop-SandboxedProcess Gate TreeFingerprint LayerFingerprint
'''.lower().split())
TYPES = {'[io.path]', '[guid]', '[pscustomobject]', '[string]', '[string[]]',
         '[switch]', '[parameter(mandatory = $true)]', '[datetime]',
         '[diagnostics.stopwatch]', '[threading.mutex]',
         '[threading.waithandlecannotbeopenedexception]', '[regex]', '[int]',
         '[int[]]', '[type]', '[wggatewindows]',
         '[security.principal.windowsprincipal]', '[security.principal.windowsidentity]',
         '[security.principal.windowsbuiltinrole]'}
METHODS = {'::getfullpath', '::newguid', '.tostring', '.trimend', '.substring',
           '.refresh', '.createshortcut', '::utcnow', '::startnew',
           '::openexisting', '::matches', '::isnullorempty', '::hasvisiblewindow',
           '.addseconds', '.dispose', '.waitforexit', '.stop',
           '::getcurrent', '::administrator', '.isinrole'}
KEYWORDS = {'param', 'if', 'elseif', 'else', 'foreach', 'try', 'catch', 'finally',
            'return', 'throw', 'exit', 'do', 'while', 'break', 'continue'}
FUNCTIONS = {'gate', 'treefingerprint', 'layerfingerprint', 'start-sandboxedsetup', 'start-standin',
             'start-sandboxednativeprobe', 'stop-sandboxedprocess'}

# Reviewed user32 window/PID queries only. Pin the entire literal C# body;
# Add-Type is never part of the general command allowlist.
WINDOW_PROBE_SHA256 = '0995b50d5e29146ca645957c33e0ec0f6a1632a9b7a01427b9b129c5eace14b6'


def powershell_tokens(source: str) -> list[str]:
    """Keep strings atomic; expose their executable $() contents for checking."""
    tokens = []
    pos = 0
    while pos < len(source):
        char = source[pos]
        if char in ' \t\r':
            pos += 1
        elif source.startswith('`\n', pos) or source.startswith('`\r\n', pos):
            pos += 2 if source[pos + 1] == '\n' else 3
        elif source.startswith("@'", pos):
            # A literal here-string cannot interpolate or execute its contents.
            assert re.match(r"@'\r?\n", source[pos:]), 'invalid here-string opening'
            closing = re.search(r"^'@(?=\r?$)", source[pos + 2:], re.M)
            assert closing, 'unclosed PowerShell here-string'
            end = pos + 2 + closing.end()
            tokens.append(source[pos:end])
            pos = end
        elif char == '#':
            end = source.find('\n', pos)
            pos = len(source) if end < 0 else end
        elif char in '\"\'':
            start, quote = pos, char
            pos += 1
            expansions = []
            while pos < len(source):
                if quote == '"' and source[pos] == '`':
                    pos += 2
                elif quote == "'" and source.startswith("''", pos):
                    pos += 2
                elif source[pos] == quote:
                    pos += 1
                    break
                elif quote == '"' and source.startswith('$(', pos):
                    # Quoted arguments may contain nested subexpressions.
                    end = expression_end(source, pos + 2)
                    expansions.extend([';'] + powershell_tokens(source[pos + 2:end]) + [';'])
                    pos = end + 1
                else:
                    pos += 1
            else:
                raise AssertionError('unclosed PowerShell string')
            tokens.append(source[start:pos])
            tokens.extend(expansions)
        elif source.startswith(('@(', '$('), pos):
            tokens.append(source[pos:pos + 2])
            pos += 2
        elif source.startswith('@{', pos):
            tokens.append('@{')
            pos += 2
        elif char == '\n':
            # A pipeline or a binary operator continues across a newline.
            if not tokens or tokens[-1] not in {'|', '-or', '-and', '+', ','}:
                tokens.append(';')
            pos += 1
        else:
            match = LEXEME.match(source, pos)
            assert match, f'unknown PowerShell syntax at {source[pos:pos + 40]!r}'
            token = match.group()
            assert token != '`', 'unreviewed escaped command spelling'
            tokens.append(token)
            pos = match.end()
    return tokens


def expression_end(source: str, pos: int) -> int:
    depth, quote = 1, None
    while pos < len(source):
        char = source[pos]
        if char == '`':
            pos += 2
            continue
        if quote:
            if char == quote:
                if quote == "'" and source[pos:pos + 2] == "''":
                    pos += 2
                    continue
                quote = None
        elif char in '\"\'':
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if not depth:
                return pos
        pos += 1
    raise AssertionError('unclosed PowerShell subexpression')


def command_elements(tokens: list[str]) -> list[list[str]]:
    """First word at each statement/pipeline/expression boundary, generically."""
    commands = []
    head = True
    for index, token in enumerate(tokens):
        lower = token.lower()
        following = tokens[index + 1] if index + 1 < len(tokens) else ''
        if token in {';', '|', '(', '@(', '$(', '{', '@{', '}', '='}:
            head = True
        elif token in {')', ']', ','}:
            head = False
        elif lower == 'function':
            assert following.lower() in FUNCTIONS, f'unreviewed function {following}'
            head = False
        elif head and lower in KEYWORDS:
            head = lower in {'return', 'throw', 'exit'}
        elif head:
            head = False
            if (token.startswith(('$', '"', "'", '[', '-')) or
                    token in {'@', '!', '+', '::'} or token[0].isdigit() or
                    following == '='):
                continue  # expression, parameter declaration or hashtable key
            end, depth = index + 1, 0
            while end < len(tokens):
                if depth == 0 and tokens[end] in {';', '|', '{', '}', ')'}:
                    break
                depth += (tokens[end] in {'(', '@(', '$('}) - (tokens[end] == ')')
                end += 1
            commands.append(tokens[index:end])
    return commands


def assert_allowed_powershell(tokens: list[str], *, extra_commands: set[str] = frozenset(),
                              delegated: set[str] = frozenset()) -> list[list[str]]:
    for index, token in enumerate(tokens):
        lower = token.lower()
        if lower.startswith('$') and ':' in lower and not lower.startswith('$env:'):
            assert lower == '$script:results', f'unreviewed scoped variable: {token}'
        if token in {'&', '.'}:
            assert token == '&' and tokens[index + 1].lower() in delegated, 'unreviewed call operator'
        if token.startswith('[') and not re.fullmatch(r'\[\d+\]', token):
            assert lower in TYPES, f'unreviewed .NET type: {token}'
            if lower == '[wggatewindows]':
                assert compact(tokens[index + 1:index + 3]) == ['::', 'hasvisiblewindow'], 'unreviewed window probe'
        if lower.startswith('.') and index + 1 < len(tokens) and tokens[index + 1] == '(':
            assert lower in METHODS, f'unreviewed member call: {token}'
        if token == '::':
            assert '::' + tokens[index + 1].lower() in METHODS, 'unreviewed static member'
    commands = command_elements(tokens)
    for command in commands:
        assert command[0].lower() in COMMANDS | extra_commands | delegated | {'&'}, f'unknown command: {command}'
        if command[0].lower() == 'new-object':
            assert [t.lower() for t in command] == ['new-object', '-comobject', 'wscript.shell'], 'unreviewed object factory'
        if command[0].lower() in {'new-item', 'remove-item', 'set-content'}:
            assert not any(t.strip('"\'').lower().startswith('env:') for t in command), 'environment provider write'
    return commands


def function_body(tokens: list[str], name: str) -> tuple[list[str], list[str]]:
    starts = [i for i, t in enumerate(tokens[:-1]) if t.lower() == 'function' and tokens[i + 1].lower() == name.lower()]
    assert len(starts) == 1, f'{name} must be defined once'
    start = starts[0]
    opening = tokens.index('{', start)
    assert compact(tokens[start:opening]) == ['function', name.lower()], 'unreviewed helper parameters'
    depth = 1
    end = opening + 1
    while depth:
        assert end < len(tokens), 'unclosed helper'
        depth += (tokens[end] in {'{', '@{'}) - (tokens[end] == '}')
        end += 1
    return tokens[opening + 1:end - 1], tokens[:start] + tokens[end:]


def compact(tokens: list[str]) -> list[str]:
    return [t.lower() for t in tokens if t != ';']


def assert_fragment(tokens: list[str], fragment: str) -> None:
    actual, expected = compact(tokens), compact(powershell_tokens(fragment))
    assert any(actual[i:i + len(expected)] == expected for i in range(len(actual))), fragment


def assert_environment_pair(tokens: list[str], expected: list[str]) -> None:
    writes = []
    for i, token in enumerate(tokens[:-1]):
        if token.lower().startswith('$env:'):
            assert not (i and tokens[i - 1] in {'++', '--'}), 'environment increment'
            if tokens[i + 1] == ',':
                tail = tokens[i + 1:]
                boundary = next((j for j, t in enumerate(tail) if t in {';', '|', '{', '}'}), len(tail))
                assert '=' not in tail[:boundary], 'environment tuple assignment'
        if token.lower().startswith('$env:') and tokens[i + 1] in {'=', '+=', '-=', '++', '--'}:
            end = i + 2
            while end < len(tokens) and tokens[end] not in {';', '}'}:
                end += 1
            writes.append(compact(tokens[i:end]))
    assert writes == [compact(powershell_tokens(line)) for line in expected], 'unreviewed environment writes'


def assert_gate_allowlist(source: str) -> None:
    tokens = powershell_tokens(source)
    setup_body, outside = function_body(tokens, 'Start-SandboxedSetup')
    standin_body, outside = function_body(outside, 'Start-StandIn')
    probe_body, outside = function_body(outside, 'Start-SandboxedNativeProbe')
    stop_body, outside = function_body(outside, 'Stop-SandboxedProcess')
    commands = assert_allowed_powershell(outside, extra_commands={'add-type'})
    compilations = [c for c in commands if c[0].lower() == 'add-type']
    assert len(compilations) == 1, 'exactly one reviewed Add-Type is allowed'
    compilation = compilations[0]
    assert len(compilation) == 3 and compact(compilation[:2]) == ['add-type', '-typedefinition']
    literal = compilation[2].replace('\r\n', '\n')
    assert literal.startswith("@'\n") and literal.endswith("\n'@"), 'only literal C# is allowed'
    body = literal[2:-2].strip()
    assert not re.search(r'\b(?:Process|CreateProcess|ShellExecute|WinExec|Environment)\b|System\.Diagnostics', body, re.I), 'unsafe C# API'
    assert hashlib.sha256(body.encode()).hexdigest() == WINDOW_PROBE_SHA256, 'changed window probe C#'
    # The helper bodies are a closed statement allowlist as well. Merely finding
    # guard text would let a surrounding `if ($false)` disable the guard, or an
    # extra assignment replace the checked arguments before Start-Process.
    setup_contract = """
        param([Parameter(Mandatory = $true)][string]$Executable,
              [string[]]$ExtraArguments = @(), [string]$AddIns = $wglinkAddins, [switch]$Wait)
        if ($Executable -ne $Setup -and $Executable -ne $unins.FullName) { throw "refused" }
        if ($env:WG2_DATA_DIR -ne $gateData) { throw "refused" }
        if (($AddIns -ne $wglinkAddins -and $AddIns -ne $developerAddins) -or
            -not (Test-Path -LiteralPath $AddIns -PathType Container)) { throw "refused" }
        foreach ($argument in $ExtraArguments) {
            if ($argument -match '^/(VERYSILENT|SUPPRESSMSGBOXES|WGLINKADDINSDIR)(=|$)') { throw "refused" }
        }
        if ($Executable -eq $Setup -and -not ($ExtraArguments | Where-Object { $_ -match '^/DIR=' })) {
            $ExtraArguments = @("/DIR=`"$installRoot`"") + $ExtraArguments
        }
        $arguments = @("/VERYSILENT", "/SUPPRESSMSGBOXES") + $ExtraArguments + @("/WGLINKADDINSDIR=`"$AddIns`"")
        $p = Start-Process -FilePath $Executable -ArgumentList $arguments -PassThru -Wait:$Wait -NoNewWindow
        $null = $p.Handle
        return $p
    """
    standin_contract = '''
        $p = Start-Process -FilePath "$env:SystemRoot\\System32\\ping.exe" -ArgumentList "-n", "600", "127.0.0.1" -RedirectStandardOutput (Join-Path $gateRoot "stand-in.log") -RedirectStandardError (Join-Path $gateRoot "stand-in-error.log") -PassThru -NoNewWindow
        $null = $p.Handle
        return $p
    '''
    probe_contract = r'''
        param([string]$ProbePath)
        if ($env:WG2_DATA_DIR -ne $gateData -or $env:WG2_FUSION_ADDINS_DIR -ne $wglinkAddins) { throw "refused" }
        if ($ProbePath -ne (Join-Path $gateRoot "recovered-loader-only.txt") -and
            $ProbePath -ne (Join-Path $gateRoot "recovered-installer-tree.txt")) { throw "refused" }
        $pythonPath = ConvertTo-Json -InputObject $ProbePath -Compress
        $pythonPath = $pythonPath -replace '^"|"$', '' -replace "'", '\u0027'
        $code = "from pathlib import Path;Path('$pythonPath').write_text('previous interpreter ran')"
        $p = Start-Process -FilePath (Join-Path $installRoot "Waveguide Generator.exe") -ArgumentList "-c", ('"' + $code + '"') -PassThru -NoNewWindow
        $null = $p.Handle
        return $p
    '''
    stop_contract = r'''
        param($Process, [switch]$Tree)
        $null = $Process.Handle
        if ($Process.HasExited) { return }
        $arguments = @("/PID", [string]$Process.Id, "/F")
        if ($Tree) { $arguments += "/T" }
        & "$env:SystemRoot\System32\taskkill.exe" @arguments | Out-Null
    '''
    for body, contract in ((setup_body, setup_contract), (standin_body, standin_contract), (probe_body, probe_contract)):
        helper_commands = assert_allowed_powershell(body, extra_commands={'start-process'})
        assert sum(c[0].lower() == 'start-process' for c in helper_commands) == 1
        normalized = compact(body)
        # Failure message wording does not affect the boundary contract.
        for i, token in enumerate(normalized[:-1]):
            if token == 'throw':
                assert normalized[i + 1].startswith(('"', "'"))
                normalized[i + 1] = '"refused"'
        assert normalized == compact(powershell_tokens(contract)), 'unreviewed helper body'
    taskkill = '"$env:systemroot\\system32\\taskkill.exe"'
    assert_allowed_powershell(stop_body, delegated={taskkill})
    assert compact(stop_body) == compact(powershell_tokens(stop_contract)), 'unreviewed stop helper body'
    assert_environment_pair(tokens, ['$env:WG2_DATA_DIR = $gateData',
                                     '$env:WG2_FUSION_ADDINS_DIR = $wglinkAddins',
                                     '$env:WG2_DATA_DIR = $previousDataDir',
                                     '$env:WG2_FUSION_ADDINS_DIR = $previousFusionAddins'])
    # The inputs to the helper are immutable private fixtures, created before
    # the first call. Pin their definitions rather than infer arbitrary PS code.
    fixtures = {
        '$installRoot': 'Join-Path $gateRoot "i"',
        '$longDir': 'Join-Path $gateRoot ("g" * 200)',
        '$contenderRoot': 'Join-Path $gateRoot "mutex-contender"',
        '$gateRoot': 'Join-Path $env:TEMP ("WG-inst-" + [guid]::NewGuid().ToString("N").Substring(0, 12))',
        '$wglinkAddins': 'Join-Path $gateRoot "Fusion\\API\\AddIns"',
        '$developerAddins': 'Join-Path $gateRoot "Developer\\API\\AddIns"',
        '$gateData': 'Join-Path $gateRoot "data"',
        '$previousDataDir': '$env:WG2_DATA_DIR',
        '$previousFusionAddins': '$env:WG2_FUSION_ADDINS_DIR',
        '$oldApp': 'Join-Path $installRoot "app\\gate_previous_app.txt"',
        '$oldRuntime': 'Join-Path $installRoot "runtime\\gate_previous_runtime.txt"',
        '$probePath': 'Join-Path $gateRoot "recovered-$label.txt"',
        '$freshRoot': 'Join-Path $gateRoot "f"',
        '$freshOutcome': 'Join-Path $gateRoot "fresh-timeout.json"',
        '$freshLog': 'Join-Path $gateRoot "fresh-timeout.log"',
        '$unins': 'Get-ChildItem $installRoot -Filter "unins*.exe" | Select-Object -First 1',
        '$planted': '"$installRoot\\app\\__pycache__"',
        '$plantedRecovery': '"$installRoot\\recovery\\__pycache__"',
        '$removedPackage': 'Join-Path $installRoot "runtime\\Lib\\site-packages\\gate_removed_package"',
        '$timeoutEvidence': 'Join-Path $env:TEMP ("WaveguideGenerator-timeout-evidence-" + [guid]::NewGuid().ToString("N"))',
    }
    for variable, value in fixtures.items():
        assert_fragment(outside, f'{variable} = {value}')
        assert sum(t.lower() == variable.lower() and outside[i + 1] in {'=', '+=', '-=', '++', '--'}
                   for i, t in enumerate(outside[:-1])) == 1, f'reassigned sandbox input {variable}'
    assert not any(t.lower() == '$setup' and outside[i + 1] in {'=', '+=', '-=', '++', '--'}
                   for i, t in enumerate(outside[:-1])), 'setup reassignment'
    assert_fragment(outside, '$label = if ($treeKill) { "installer-tree" } else { "loader-only" }')
    assert sum(t.lower() == '$label' and outside[i + 1] == '=' for i, t in enumerate(outside[:-1])) == 1
    # Every stop argument comes from its original checked launcher object,
    # never a later Get-Process lookup of a potentially unrelated/reused PID.
    factories = {
        '$interrupted': [['start-sandboxedsetup', '-executable', '$setup']],
        '$nativeprobe': [['start-sandboxednativeprobe', '-probepath', '$probepath']],
        '$freshstandin': [['start-standin']],
        '$freshtimingout': [['$null'], ['start-sandboxedsetup', '-executable', '$setup']],
    }
    for variable, expected in factories.items():
        positions = [i for i, t in enumerate(outside[:-1]) if t.lower() == variable and outside[i + 1] == '=']
        assert len(positions) == len(expected), f'reassigned process input {variable}'
        for position, prefix in zip(positions, expected):
            assert compact(outside[position + 2:position + 2 + len(prefix)]) == prefix, f'unreviewed process factory {variable}'
    mkdir = 'New-Item -ItemType Directory -Force $wglinkAddins, $developerAddins, $gateData | Out-Null'
    assert_fragment(outside, mkdir)
    assert_fragment(outside, '$previousDataDir = $env:WG2_DATA_DIR; $env:WG2_DATA_DIR = $gateData; $previousFusionAddins = $env:WG2_FUSION_ADDINS_DIR; $env:WG2_FUSION_ADDINS_DIR = $wglinkAddins; try {')
    assert_fragment(outside, '} finally { $env:WG2_DATA_DIR = $previousDataDir; $env:WG2_FUSION_ADDINS_DIR = $previousFusionAddins }')
    assert outside.index('New-Item') < outside.index('Start-SandboxedSetup')
    assert outside.index('$env:WG2_DATA_DIR', outside.index('$previousDataDir') + 1) < outside.index('Start-SandboxedSetup')
    # Only the two existing cleanup roots are permitted; neither command may
    # operate on the private AddIns/data fixtures or an indirect provider path.
    removable = {'$gateroot', '$installroot', '$oldapp', '$oldruntime'}
    directory_commands = [compact(powershell_tokens(line)) for line in (
        mkdir.split(' | ')[0],
        'New-Item -ItemType Directory -Force (Join-Path $developerAddins "WGLink")',
        'New-Item -ItemType Directory -Force $planted',
        'New-Item -ItemType Directory -Force $plantedRecovery',
        'New-Item -ItemType Directory -Force $removedPackage',
        'New-Item -ItemType Directory $timeoutEvidence',
    )]
    for command in commands:
        lower = compact(command)
        if lower[0] == 'new-item':
            assert lower in directory_commands, f'unreviewed directory creation: {command}'
        if lower[0] == 'copy-item':
            assert lower == ['copy-item', '-literalpath', '$evidencefile', '-destination', '$timeoutevidence'], 'unreviewed evidence copy'
        if lower[0] == 'get-itemproperty':
            assert lower == ['get-itemproperty', '-literalpath',
                             '"hklm:\\software\\microsoft\\windows\\currentversion\\policies\\system"',
                             '-name', '"enablelua"', '-erroraction', 'silentlycontinue'], 'unreviewed registry read'
        if lower[0] == 'get-ciminstance':
            assert lower == ['get-ciminstance', 'win32_process'], 'unreviewed process inventory'
        if lower[0] == 'remove-item':
            targets = [t for t in lower[1:] if not t.startswith('-') and t != ',']
            assert targets and all(t in removable for t in targets), f'unreviewed removal: {command}'
        if lower[0] == 'rename-item':
            assert len(lower) == 5 and lower[3:] == ['-erroraction', 'stop']
            assert (lower[1], lower[2]) in {
                ('"$installroot\\app"', '"app.gatetest"'), ('"$installroot\\app.gatetest"', '"app"'),
                ('"$installroot\\runtime"', '"runtime.gatetest"'), ('"$installroot\\runtime.gatetest"', '"runtime"'),
            }, 'unreviewed rename target'
        if lower[0] == 'start-standin':
            assert len(lower) == 1, 'stand-in takes no executable argument'
        if lower[0] == 'start-sandboxednativeprobe':
            assert lower == ['start-sandboxednativeprobe', '-probepath', '$probepath'], 'unreviewed native probe arguments'
        if lower[0] == 'stop-sandboxedprocess':
            assert lower[1:3] in (['-process', '$interrupted'], ['-process', '$nativeprobe'],
                                   ['-process', '$freshtimingout'], ['-process', '$freshstandin'])
            assert lower[3:] in ([], ['-tree'], ['-tree:$treekill']), 'unreviewed stop arguments'
        if lower[0] == 'start-sandboxedsetup':
            assert lower[1] == '-executable' and (lower[2] == '$setup' or lower[2:4] == ['$unins', '.fullname'])
            assert all(not t.startswith('-') or t in {'-executable', '-extraarguments', '-wait', '-addins'} for t in lower)
            assert not any(re.search(r'/(VERYSILENT|SUPPRESSMSGBOXES|WGLINKADDINSDIR)', t, re.I) for t in command)
            if '-addins' in lower:
                assert lower[lower.index('-addins') + 1] == '$developeraddins'
            for token in command:
                if token.upper().startswith('"/DIR='):
                    assert token in {f'"/DIR=`"{root}`""' for root in ('$installRoot', '$longDir', '$contenderRoot', '$freshRoot')}, 'unreviewed setup root'
    root_cleanup = [c for c in commands if c[0].lower() == 'remove-item' and '$gateroot' in compact(c)]
    assert len(root_cleanup) == 1, 'only one final private-root cleanup is allowed'
    cleanup = root_cleanup[0]
    cleanup_index = next(i for i in range(len(outside)) if outside[i:i + len(cleanup)] == cleanup)
    assert cleanup_index > max(i for i, t in enumerate(outside) if t.lower() == 'start-sandboxedsetup')



def test_every_gate_setup_and_uninstall_is_sandboxed() -> None:
    assert_gate_allowlist((ROOT / 'installers/windows/gates.ps1').read_text())


def test_reviewed_add_type_is_required_once_even_with_windows_line_endings() -> None:
    source = (ROOT / 'installers/windows/gates.ps1').read_text()
    assert_gate_allowlist(source.replace('\n', '\r\n'))
    compilation = next(c for c in command_elements(powershell_tokens(source)) if c[0].lower() == 'add-type')
    with pytest.raises(AssertionError, match='exactly one'):
        assert_gate_allowlist(source + '\n' + ' '.join(compilation) + '\n')


def test_literal_here_string_is_atomic() -> None:
    literal = "@'\n{ & $Setup; [Diagnostics.Process]::Start($Setup) }\n'@"
    assert powershell_tokens(literal) == [literal]
    with pytest.raises(AssertionError, match='unclosed'):
        powershell_tokens("@'\nbody\n  '@")
    assert powershell_tokens('[DateTime]::UtcNow.AddSeconds(30)') == [
        '[DateTime]', '::', 'UtcNow', '.AddSeconds', '(', '30', ')',
    ]


@pytest.mark.parametrize('old,new', [
    ('return found;', 'return false;'),
    ('return found;', 'System.Diagnostics.Process.Start("setup.exe"); return found;'),
    ("Add-Type -TypeDefinition @'", "Add-Type -TypeDefinition 'public class X {}'; Add-Type -TypeDefinition @'"),
    ('[WgGateWindows]::HasVisibleWindow', '[WgGateWindows]::Anything'),
    ('Get-CimInstance Win32_Process', 'Invoke-CimMethod -ClassName Win32_Process -MethodName Create'),
    ('Copy-Item -LiteralPath $evidenceFile -Destination $timeoutEvidence', 'Copy-Item -LiteralPath $evidenceFile -Destination Env:WG2_DATA_DIR'),
])
def test_window_probe_and_inventory_reject_mutations(old: str, new: str) -> None:
    source = (ROOT / 'installers/windows/gates.ps1').read_text()
    changed = source.replace(old, new, 1)
    assert changed != source
    with pytest.raises(AssertionError):
        assert_gate_allowlist(changed)


ENTRYPOINTS = ('build_bundle.py', 'gates.ps1', 'qualify_installed_cpu.py', 'qualify_installed_quit.py')


# rc-build runs qualify_installed_cpu.py six times: per OS the default-route CPU
# gate and, reviewed for the 0.3.6 BEAT switch, an official-engine gate on the
# same installed payload. The second run is the same entrypoint and argument
# shape with WG2_BEAT_PROVIDER=official scoped to its step; the qualifier's own
# isolated_environment sandboxes every launch exactly as on the default route.
# rc-build also runs qualify_installed_quit.py six times: per OS the default
# (BEMPP) Quit gate and the reviewed --engine beat Quit gate on the same
# installed payload. The second run is the same entrypoint and argument shape
# plus --engine beat and --official-runtime-work (the official CPU gate's work
# directory); the gate's own isolated environment sandboxes every launch.
@pytest.mark.parametrize('workflow_name,counts', [('rc-build', (3, 1, 6, 6)), ('release', (3, 1, 0, 0))])
def test_workflow_execution_inventory(workflow_name: str, counts: tuple[int, ...]) -> None:
    workflow = yaml.safe_load((ROOT / f'.github/workflows/{workflow_name}.yml').read_text())
    found = dict.fromkeys(ENTRYPOINTS, 0)
    installer_count = 0
    for job in workflow['jobs'].values():
        for step in job.get('steps', []):
            source = step.get('run', '')
            if step.get('shell') == 'pwsh':
                tokens = powershell_tokens(source)
                direct_setup = step.get('name') == INLINE_SETUP
                commands = assert_allowed_powershell(
                    tokens,
                    extra_commands={'uv', 'choco', 'out-file', 'write-host'} | ({'start-process'} if direct_setup else set()),
                    delegated={'./installers/windows/gates.ps1', './scripts/ci/verify_inno_setup.ps1'},
                )
                if direct_setup:
                    assert_inline_setup_allowlist(source)
                    installer_count += 1
                # Inventory all tokens, including script paths passed as native
                # arguments and nested expression calls, once per occurrence.
                commands = [tokens]
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


INLINE_SETUP = 'Qualify BEAT CPU on the candidate the Windows installer installed'


def assert_inline_setup_allowlist(source: str) -> None:
    tokens = powershell_tokens(source)
    commands = assert_allowed_powershell(tokens, extra_commands={'start-process', 'uv', 'out-file'})
    assert sum(c[0].lower() == 'start-process' for c in commands) == 1
    assert_environment_pair(tokens, ['$env:WG2_DATA_DIR = Join-Path $root "data"'])
    for fragment in (
        '$root = Join-Path $env:RUNNER_TEMP ("cpu-gate-" + [guid]::NewGuid().ToString("N"))',
        '$install = Join-Path $root "app"', '$installLog = Join-Path $root "inno-install.log"',
        '$addins = Join-Path $root "fusion-addins"',
        '$env:WG2_DATA_DIR = Join-Path $root "data"; New-Item -ItemType Directory -Path $addins, $env:WG2_DATA_DIR | Out-Null',
        '$process = Start-Process -FilePath $setup -Wait -PassThru -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", "`"/DIR=$install`"", "`"/LOG=$installLog`"", "`"/WGLINKADDINSDIR=$addins`"")',
    ):
        assert_fragment(tokens, fragment)
    for variable in ('$root', '$install', '$installLog', '$addins', '$setup', '$process'):
        assert sum(t.lower() == variable.lower() and tokens[i + 1] == '='
                   for i, t in enumerate(tokens[:-1])) == 1, f'reassigned inline input {variable}'
    assert_fragment(tokens, '$setup = (Get-ChildItem -File build/bundle/Waveguide.Generator-*-windows-x86_64-setup.exe | Select-Object -First 1).FullName')
    assert tokens.index('New-Item', tokens.index('$env:WG2_DATA_DIR')) < tokens.index('Start-Process')
    assert not any(c[0].lower() == 'remove-item' for c in commands), 'inline fixture removal'
    directories = [compact(powershell_tokens(line)) for line in (
        'New-Item -ItemType Directory -Path $root',
        'New-Item -ItemType Directory -Path $addins, $env:WG2_DATA_DIR',
    )]
    assert all(compact(c) in directories for c in commands if c[0].lower() == 'new-item')
    launch = next(c for c in commands if c[0].lower() == 'start-process')
    assert compact(launch) == compact(powershell_tokens(
        'Start-Process -FilePath $setup -Wait -PassThru -ArgumentList @('
        '"/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", '
        '"`"/DIR=$install`"", "`"/LOG=$installLog`"", "`"/WGLINKADDINSDIR=$addins`"")'))


@pytest.mark.parametrize('argument', ['/DIR=$install', '/LOG=$installLog', '/WGLINKADDINSDIR=$addins'])
def test_inline_setup_requires_literal_quotes_for_paths(argument: str) -> None:
    # Start-Process joins ArgumentList into a command line; PowerShell string
    # delimiters alone do not protect paths containing spaces.
    workflow = yaml.safe_load((ROOT / '.github/workflows/rc-build.yml').read_text())
    step = next(step for job in workflow['jobs'].values() for step in job.get('steps', [])
                if step.get('name') == INLINE_SETUP)
    assert step['shell'] == 'pwsh'
    source = step['run']
    assert_inline_setup_allowlist(source)
    changed = source.replace(f'"`"{argument}`""', f'"{argument}"', 1)
    assert changed != source
    with pytest.raises(AssertionError):
        assert_inline_setup_allowlist(changed)


UNSAFE_LINES = [
    'saps $Setup', 'start $Setup /VERYSILENT', '& $Setup /VERYSILENT',
    '. $Setup', 'iex "$Setup"', 'Invoke-Expression "$Setup"',
    '[Diagnostics.Process]::Start($Setup)', 'cmd /c $Setup',
    'Set-Item Env:WG2_DATA_DIR $env:APPDATA', '$env:WG2_DATA_DIR = $env:APPDATA',
    "[Environment]::SetEnvironmentVariable('WG2_DATA_DIR', $env:APPDATA)",
    'ri $wglinkAddins -Recurse', 'rm $gateData -Recurse',
    'Remove-Item $wglinkAddins -Recurse', 'Start-Process $Setup',
    "Add-Type -TypeDefinition 'public class X {}'",
    'do { [Diagnostics.Process]::Start($Setup) } while ($false)',
    "Add-Type -MemberDefinition '...' -Name N -Namespace W",
    'Start-Job { & $Setup }', 'Invoke-Command { & $Setup }',
    'Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$Setup}',
    'Invoke-WmiMethod -Class Win32_Process -Name Create -ArgumentList $Setup',
    '[WgGateWindows]::Anything($Setup)',
    'Set-CimInstance -ClassName Win32_Process -Property @{CommandLine=$Setup}',
]


@pytest.mark.parametrize('line', UNSAFE_LINES)
def test_sandbox_check_rejects_unsafe_invocations(line: str) -> None:
    source = (ROOT / 'installers/windows/gates.ps1').read_text()
    changed = source.replace('# --- Gate 1:', line + '\n# --- Gate 1:', 1)
    assert changed != source
    with pytest.raises(AssertionError):
        assert_gate_allowlist(changed)


@pytest.mark.parametrize('old,new', [
    ('"/VERYSILENT", ', ''),
    ('"/SUPPRESSMSGBOXES"', '"/NORESTART"'),
    ('/WGLINKADDINSDIR=', '/OTHER='),
    ('$env:WG2_DATA_DIR = $gateData', ''),
    ('New-Item -ItemType Directory -Force $wglinkAddins, $developerAddins, $gateData | Out-Null', ''),
    ('Start-Process -FilePath', 'Start-Process -UseNewEnvironment -FilePath'),
    ('$null = $p.Handle', ''),
    ('# --- Gate 1:', '$wglinkAddins = "$env:APPDATA"\n# --- Gate 1:'),
    ('# --- Gate 1:', 'Remove-Item $gateData -Recurse\n# --- Gate 1:'),
    ('# --- Gate 1:', '$shell.Run($Setup)\n# --- Gate 1:'),
    ('# --- Gate 1:', '"$(saps $Setup)"\n# --- Gate 1:'),
    ('# --- Gate 1:', 'Get-Item $Setup | saps $Setup\n# --- Gate 1:'),
    ('# --- Gate 1:', 'if ($true) { saps $Setup }\n# --- Gate 1:'),
    ('# --- Gate 1:', '$script:wglinkAddins = "$env:APPDATA"\n# --- Gate 1:'),
    ('# --- Gate 1:', '$setup = "other.exe"\n# --- Gate 1:'),
    ('# --- Gate 1:', '$target = "Env:WG2_DATA_DIR"; New-Item $target -Value $env:APPDATA\n# --- Gate 1:'),
    ('# --- Gate 1:', 'Remove-Item $gateRoot -Recurse\n# --- Gate 1:'),
    ('# --- Gate 1:', '--$env:WG2_DATA_DIR\n# --- Gate 1:'),
    ('# --- Gate 1:', '$env:WG2_DATA_DIR, $other = $env:APPDATA, 1\n# --- Gate 1:'),
    ('# --- Gate 1:', '$planted = "Env:WG2_DATA_DIR"\n# --- Gate 1:'),
    ('# --- Gate 1:', 'Rename-Item Env:WG2_DATA_DIR OTHER\n# --- Gate 1:'),
    ('$arguments = @(', '$arguments = @("/WGLINKADDINSDIR=other") ; $arguments = @('),
    ('if ($env:WG2_DATA_DIR -ne $gateData)', 'if ($false -and $env:WG2_DATA_DIR -ne $gateData)'),
])
def test_sandbox_contract_rejects_bypasses(old: str, new: str) -> None:
    source = (ROOT / 'installers/windows/gates.ps1').read_text()
    changed = source.replace(old, new, 1)
    assert changed != source
    with pytest.raises(AssertionError):
        assert_gate_allowlist(changed)


@pytest.mark.parametrize('line', UNSAFE_LINES)
def test_inline_setup_rejects_unsafe_invocations(line: str) -> None:
    workflow = yaml.safe_load((ROOT / '.github/workflows/rc-build.yml').read_text())
    source = next(step['run'] for job in workflow['jobs'].values() for step in job.get('steps', [])
                  if step.get('name') == INLINE_SETUP)
    with pytest.raises(AssertionError):
        assert_inline_setup_allowlist(source + '\n' + line)


def test_python_app_launch_inventory() -> None:
    # Enumerate every subprocess site in the qualifiers, including probes that
    # do not start WG. A new run/Popen/system call must be explicitly reviewed.
    expected = {
        'scripts/qualify_installed_cpu.py': {
            ('__init__', 'subprocess.Popen'): 1,
            ('check_pins', 'subprocess.run'): 1,
            # Reviewed with the official-engine gate: like check_pins, the packaged
            # interpreter runs a fixed read-only -c program (_READ_OFFICIAL_RUNTIME)
            # with env=isolated_environment and cwd=app; it starts no WG process.
            ('check_official_runtime', 'subprocess.run'): 1,
            ('diagnose_preparation', 'subprocess.run'): 1,
            ('stop_our_workers', 'subprocess.run'): 1,
            ('qualify_imported_return', 'Server'): 2,
            ('qualify', 'Server'): 1,
        },
        'scripts/qualify_installed_quit.py': {
            ('start', 'subprocess.Popen'): 1,
            ('sweep_in_runtime', 'subprocess.run'): 1,
            ('memory_ceiling', 'subprocess.run'): 1,
            # Fixed official inspection program uses the same isolated environment and app cwd.
            ('inspect_beat_hosts', 'subprocess.run'): 1,
            ('process_table', 'subprocess.run'): 1,
            ('_kill', 'subprocess.run'): 1,
            ('run_gate', 'Run.start'): 2,
        },
        'server/solver/beat_runtime/hardware.py': {
            # Fixed read-only NVIDIA inventory, resolved on PATH, bounded to 15 s,
            # with background_process_kwargs to hide packaged Windows consoles.
            # ROCm checks only directories/PATH; GPU Julia setup uses the existing
            # run_julia_step launch with CUDA/AMDGPU versioninfo and functional,
            # plus import CUDSS to fetch CUDA coupled-solve artifacts during setup.
            ('_nvidia_gpu_present', 'subprocess.run'): 1,
            # Existing macOS version fallback, fixed absolute executable, 2 s.
            ('_metal_hardware', 'subprocess.check_output'): 1,
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
    assert "else\n      WgLog('WGLink: invalid /WGLINKADDINSDIR" in override
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
    assert 'WgLog(Reason);' in validator
    assert 'if not Silent then\n    MsgBox(Reason, mbError, MB_OK);' in validator
    assert validator.rstrip().endswith('Result := False;\nend;')
    setup = source.split('function InitializeSetup(): Boolean;', 1)[1].split('\nfunction ', 1)[0]
    assert 'Result := ValidateWgLinkAddInsOverride(WizardSilent());\n  if not Result then\n    exit;' in setup
    assert setup.index('InitializeWgLog()') < setup.index('ValidateWgLinkAddInsOverride(WizardSilent())')
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
        'no-log': ('WgLog(Reason);', ''),
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


@pytest.mark.parametrize('old,new', [
    ('if ($Process.HasExited) { return }', 'if ($false) { return }'),
    ('@("/PID", [string]$Process.Id, "/F")', '@("/IM", "*", "/F")'),
    ('$null = $Process.Handle', '$null = Get-Process -Id $Process.Id'),
    ('if ($env:WG2_DATA_DIR -ne $gateData -or $env:WG2_FUSION_ADDINS_DIR -ne $wglinkAddins)', 'if ($false)'),
    ('(Join-Path $installRoot "Waveguide Generator.exe")', '$Setup'),
    ('Join-Path $gateRoot "i"', '"$env:LOCALAPPDATA\\Programs\\Waveguide Generator"'),
    ('Join-Path $installRoot "app\\gate_previous_app.txt"', '"C:\\unrelated.txt"'),
    ('if ($Executable -eq $Setup -and -not ($ExtraArguments | Where-Object { $_ -match \'^/DIR=\' }))', 'if ($false)'),
    ('Stop-SandboxedProcess -Process $nativeProbe -Tree', 'Stop-SandboxedProcess -Process $unrelated -Tree'),
])
def test_native_gate_closed_helpers_reject_boundary_mutations(old: str, new: str) -> None:
    source = (ROOT / 'installers/windows/gates.ps1').read_text()
    changed = source.replace(old, new, 1)
    assert changed != source
    with pytest.raises(AssertionError):
        assert_gate_allowlist(changed)


def test_native_helpers_do_not_enable_global_launch_or_taskkill() -> None:
    source = (ROOT / 'installers/windows/gates.ps1').read_text()
    for call in ('Start-Process -FilePath $Setup', '& "$env:SystemRoot\\System32\\taskkill.exe" /PID 123 /F'):
        with pytest.raises(AssertionError):
            assert_gate_allowlist(source + '\n' + call + '\n')


@pytest.mark.parametrize('old,new', [
    ('$freshStandIn = Start-StandIn', '$freshStandIn = Get-Process -Id 123'),
    ('$nativeProbe = Start-SandboxedNativeProbe -ProbePath $probePath', '$nativeProbe = Get-Process -Id 123'),
    ('$interrupted = Start-SandboxedSetup -Executable $Setup', '$interrupted = Get-Process -Id 123; Start-SandboxedSetup -Executable $Setup'),
    ('$freshTimingOut = Start-SandboxedSetup -Executable $Setup', '$freshTimingOut = Get-Process -Id 123; Start-SandboxedSetup -Executable $Setup'),
    ('$ProbePath -ne (Join-Path $gateRoot "recovered-loader-only.txt")', '$ProbePath -notlike "$gateRoot\\recovered-*.txt"'),
    ('Join-Path $gateRoot "recovered-$label.txt"', 'Join-Path $gateRoot "recovered-x\\..\\..\\outside.txt"'),
    ('Remove-Item -LiteralPath $oldApp, $oldRuntime', '$oldApp = "C:\\unrelated.txt"; Remove-Item -LiteralPath $oldApp, $oldRuntime'),
])
def test_native_probe_and_stop_inputs_cannot_escape_private_factories(old: str, new: str) -> None:
    source = (ROOT / 'installers/windows/gates.ps1').read_text()
    changed = source.replace(old, new, 1)
    assert changed != source
    with pytest.raises(AssertionError):
        assert_gate_allowlist(changed)


@pytest.mark.parametrize('old,new', [
    ('$longDir = Join-Path $gateRoot ("g" * 200)', '$longDir = "C:\\unrelated-install"'),
    ('$contenderRoot = Join-Path $gateRoot "mutex-contender"', '$contenderRoot = "C:\\unrelated-install"'),
    ('$p = Start-SandboxedSetup -Executable $Setup -ExtraArguments "/DIR=`"$longDir`""',
     '$longDir = "C:\\unrelated-install"; $p = Start-SandboxedSetup -Executable $Setup -ExtraArguments "/DIR=`"$longDir`""'),
    ('$contender = Start-SandboxedSetup -Executable $Setup',
     '$contenderRoot = "C:\\unrelated-install"; $contender = Start-SandboxedSetup -Executable $Setup'),
])
def test_explicit_setup_roots_cannot_escape_private_fixtures(old: str, new: str) -> None:
    source = (ROOT / 'installers/windows/gates.ps1').read_text()
    changed = source.replace(old, new, 1)
    assert changed != source
    with pytest.raises(AssertionError):
        assert_gate_allowlist(changed)
