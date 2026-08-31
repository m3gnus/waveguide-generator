"""The scrubber is the reason the report can be sent at all."""

from __future__ import annotations

from server.diagnostics.scrub import scrub_rules, scrub_text, scrub_value


WINDOWS_ENV = {"APPDATA": r"C:\Users\Ada\AppData\Roaming", "USERPROFILE": r"C:\Users\Ada"}


def windows_rules():
    return scrub_rules(home=r"C:\Users\Ada", environ=WINDOWS_ENV, system="Windows")


def posix_rules():
    return scrub_rules(home="/home/ada", environ={"HOME": "/home/ada"}, system="Linux")


def test_windows_home_is_replaced_in_both_separator_forms() -> None:
    rules = windows_rules()
    assert scrub_text(r"C:\Users\Ada\Documents\Horns", rules) == r"~\Documents\Horns"
    assert scrub_text("C:/Users/Ada/Documents/Horns", rules) == "~/Documents/Horns"


def test_windows_matching_ignores_case() -> None:
    """The same directory is written three ways by the shell, Python and Tk."""

    rules = windows_rules()
    assert scrub_text(r"c:\users\ada\logs", rules) == r"~\logs"
    assert scrub_text(r"C:\USERS\ADA\logs", rules) == r"~\logs"


def test_json_escaped_paths_are_scrubbed() -> None:
    """A path that was JSON-encoded before it was logged has doubled slashes."""

    rules = windows_rules()
    line = r'{"data_dir": "C:\\Users\\Ada\\AppData\\Roaming\\WaveguideGenerator"}'
    assert "Ada" not in scrub_text(line, rules)


def test_home_appearing_mid_line_is_scrubbed() -> None:
    rules = windows_rules()
    line = r"2026-08-31 ERROR wg.jobs: could not read C:\Users\Ada\runs\job.log (denied)"
    scrubbed = scrub_text(line, rules)
    assert scrubbed.endswith(r"could not read ~\runs\job.log (denied)")
    assert "Ada" not in scrubbed


def test_scrubbing_is_idempotent() -> None:
    rules = windows_rules()
    once = scrub_text(r"C:\Users\Ada\logs", rules)
    assert scrub_text(once, rules) == once


def test_appdata_under_home_does_not_get_its_own_placeholder() -> None:
    """``~/AppData/Roaming`` reads better than ``%APPDATA%`` and says as much."""

    scrubbed = scrub_text(r"C:\Users\Ada\AppData\Roaming\WaveguideGenerator", windows_rules())
    assert scrubbed == r"~\AppData\Roaming\WaveguideGenerator"


def test_location_variable_outside_home_keeps_its_name() -> None:
    rules = scrub_rules(
        home=r"C:\Users\Ada",
        environ={"APPDATA": r"D:\Roaming"},
        system="Windows",
    )
    assert scrub_text(r"D:\Roaming\WaveguideGenerator", rules) == r"%APPDATA%\WaveguideGenerator"


def test_posix_matching_is_case_sensitive() -> None:
    """Two accounts on Linux may differ only by case, and both are real."""

    rules = posix_rules()
    assert scrub_text("/home/ada/designs", rules) == "~/designs"
    assert scrub_text("/home/Ada/designs", rules) == "/home/Ada/designs"


def test_a_home_shaped_substring_of_another_path_is_left_alone() -> None:
    """``/opt/home/ada`` is not the home directory and must survive intact."""

    assert scrub_text("/opt/home/ada/cache", posix_rules()) == "/opt/home/ada/cache"


def test_scrub_value_covers_keys_and_nested_structures() -> None:
    rules = windows_rules()
    scrubbed = scrub_value(
        {r"C:\Users\Ada\projects": [{"path": r"C:\Users\Ada\out"}, 3, None]},
        rules,
    )
    assert scrubbed == {"~\\projects": [{"path": "~\\out"}, 3, None]}


def test_empty_rules_pass_text_through() -> None:
    rules = scrub_rules(home="", environ={}, system="Linux")
    assert not rules
    assert scrub_text("/home/ada", rules) == "/home/ada"


def test_a_filesystem_root_is_never_a_rule() -> None:
    """``HOME=/`` would otherwise rewrite every path in the report to ``~``."""

    for home in ("/", "\\", "C:\\", ".", ""):
        rules = scrub_rules(home=home, environ={}, system="Linux")
        assert scrub_text("/home/ada/designs", rules) == "/home/ada/designs"


def test_windows_rules_compile_on_a_posix_host() -> None:
    """Most of the CI matrix is Linux, and it must still test Windows rules."""

    rules = scrub_rules(home=r"C:\Users\Ada", environ={}, system="Windows")
    assert scrub_text(r"C:\Users\Ada\logs", rules) == r"~\logs"
