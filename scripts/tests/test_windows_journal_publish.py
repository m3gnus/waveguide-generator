"""Exercise the native publication retry policy with deterministic Win32 returns."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def publisher(tmp_path_factory):
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("portable C compiler unavailable; real Win32 coverage runs separately")
    source = (ROOT / "launchers/windows/launcher.c").read_text()
    function = source[source.index("static int publish_file("):source.index("static int durable_write(")]
    folder = tmp_path_factory.mktemp("journal-publish")
    harness = folder / "policy.c"
    harness.write_text(r'''
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
typedef unsigned long DWORD;
typedef unsigned long long ULONGLONG;
#define ERROR_ACCESS_DENIED 5
#define ERROR_SHARING_VIOLATION 32
#define ERROR_LOCK_VIOLATION 33
static DWORD failure, last_error, expected_flags;
static unsigned attempts, failures, sleeps;
static ULONGLONG clock_ms;
static DWORD GetLastError(void) { return last_error; }
static void SetLastError(DWORD value) { last_error = value; }
static ULONGLONG GetTickCount64(void) { return clock_ms; }
static void Sleep(DWORD delay) { clock_ms += delay; ++sleeps; }
static int MoveFileExW(const wchar_t *from, const wchar_t *to, DWORD flags) {
    if (wcscmp(from, L"flushed-temporary") || wcscmp(to, L"previous-journal") || flags != expected_flags) abort();
    ++attempts;
    if (attempts <= failures) { SetLastError(failure); return 0; }
    return 1;
}
''' + function + r'''
int main(int argc, char **argv) {
    int ok;
    if (argc != 4) return 2;
    failure = strtoul(argv[1], NULL, 10); failures = (unsigned)strtoul(argv[2], NULL, 10);
    expected_flags = strtoul(argv[3], NULL, 10);
    ok = publish_file(L"flushed-temporary", L"previous-journal", expected_flags);
    printf("%d %u %u %llu %lu\n", ok, attempts, sleeps, clock_ms, GetLastError());
    return 0;
}
''')
    executable = folder / "policy"
    subprocess.run([compiler, "-std=c99", "-Wall", "-Wextra", "-Werror", str(harness), "-o", str(executable)], check=True)
    return executable


def run(publisher, error, failures, flags=9):
    result = subprocess.run([str(publisher), str(error), str(failures), str(flags)], check=True,
                            capture_output=True, text=True, timeout=5)
    return tuple(map(int, result.stdout.split()))


@pytest.mark.parametrize("error", [5, 32, 33])
@pytest.mark.parametrize("flags", [8, 9])
def test_transient_contention_retries_the_same_publication(publisher, error, flags):
    ok, attempts, sleeps, elapsed, _ = run(publisher, error, 3, flags)
    assert (ok, attempts, sleeps, elapsed) == (1, 4, 3, 75)


@pytest.mark.parametrize("error", [5, 32, 33])
def test_permanent_contention_is_bounded_and_preserves_the_error(publisher, error):
    ok, attempts, sleeps, elapsed, last_error = run(publisher, error, 10000)
    assert (ok, last_error) == (0, error)
    assert attempts == sleeps + 1
    assert 0 < elapsed <= 2000


@pytest.mark.parametrize("error", [2, 3, 80, 112, 183])
def test_other_failures_do_not_retry_or_change_move_flags(publisher, error):
    assert run(publisher, error, 10000, flags=8) == (0, 1, 0, 0, error)


def test_successful_publication_never_sleeps(publisher):
    assert run(publisher, 5, 0)[:4] == (1, 1, 0, 0)
