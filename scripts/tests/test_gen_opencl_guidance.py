from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import re

import pytest

from scripts import gen_opencl_guidance as generator

ARTIFACTS = ["installers/windows/opencl-guidance.iss", "shared/opencl-guidance.html"]
FORBIDDEN = re.compile(r"nvidia|amd\.com|geforce|radeon|gpu driver", re.IGNORECASE)


def read_guidance() -> dict:
    return json.loads((generator.ROOT / generator.SOURCE).read_text(encoding="utf-8"))


def assert_cpu_only_guidance(root: Path) -> None:
    for name in [generator.SOURCE, *ARTIFACTS]:
        assert not FORBIDDEN.search((root / name).read_text(encoding="utf-8")), name


def test_guidance_contains_no_gpu_driver_links_or_instructions() -> None:
    assert_cpu_only_guidance(generator.ROOT)


def test_versioned_contract_and_cpu_runtime_content() -> None:
    guidance = read_guidance()
    assert set(guidance) == {"version", "warnings", "reasons", "platforms"}
    assert guidance["version"] == 1
    assert set(guidance["reasons"]) == {"no_device", "smoke_test_failed", "smoke_test_timeout", "pocl_windows"}
    assert guidance["reasons"]["no_device"] == "No CPU OpenCL device was found."
    assert all(text and "\n" not in text for text in guidance["reasons"].values())
    assert set(guidance["platforms"]) == {"windows", "linux"}
    windows = guidance["platforms"]["windows"]
    assert len(windows["steps"]) == 1
    assert windows["steps"][0] == {
        "vendor": "intel",
        "label": "Intel CPU OpenCL runtime",
        "url": "https://www.intel.com/content/www/us/en/developer/articles/technical/intel-cpu-runtime-for-opencl-applications-with-sycl-support.html",
        "note": "Intel's CPU runtime officially supports Intel processors; it has also worked on AMD processors in our testing. If no OpenCL device works, WG falls back to its slower numba engine.",
    }
    for platform in guidance["platforms"].values():
        assert set(platform) == {"summary", "steps"}
        assert "BEMPP solver runs on the CPU" in platform["summary"]
        assert "much faster" in platform["summary"] and "without one, more slowly" in platform["summary"]
        for step in platform["steps"]:
            assert set(step) == {"vendor", "label", "url", "note"}
    assert {step["vendor"] for step in guidance["platforms"]["linux"]["steps"]} == {"pocl", "intel"}
    assert guidance["warnings"] == [{
        "id": "pocl_windows", "platforms": ["windows"],
        "text": "PoCL on Windows shows up as an OpenCL device but computes nothing, so installing it does not help.",
    }]


def test_committed_guidance_matches_the_shared_json() -> None:
    for path, rendered in generator.generated_files(generator.ROOT).items():
        assert path.read_text(encoding="utf-8") == rendered, (
            f"{path.name} is stale; run scripts/gen_opencl_guidance.py --write"
        )
    assert generator.main(["--check"]) == 0


@pytest.fixture
def isolated_guidance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "shared").mkdir()
    (tmp_path / "installers/windows").mkdir(parents=True)
    source = tmp_path / generator.SOURCE
    source.write_text(
        (generator.ROOT / generator.SOURCE).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    return source


@pytest.mark.parametrize("name", ARTIFACTS)
@pytest.mark.parametrize("mutation", ["missing", "edited"])
def test_check_rejects_missing_or_edited_outputs(
    isolated_guidance: Path, name: str, mutation: str, capsys: pytest.CaptureFixture
) -> None:
    assert generator.main(["--write"]) == 0
    output = generator.ROOT / name
    if mutation == "missing":
        output.unlink()
    else:
        output.write_text("stale output\n", encoding="utf-8")
    assert generator.main(["--check"]) == 1
    assert name in capsys.readouterr().err
    assert generator.main(["--write"]) == 0
    assert generator.main(["--check"]) == 0


@pytest.mark.parametrize("field", ["summary", "label", "url", "note", "warning"])
def test_source_changes_require_regeneration(isolated_guidance: Path, field: str) -> None:
    assert generator.main(["--write"]) == 0
    guidance = json.loads(isolated_guidance.read_text(encoding="utf-8"))
    if field == "summary":
        guidance["platforms"]["windows"]["summary"] += " Updated."
    elif field == "warning":
        guidance["warnings"][0]["text"] += " Updated."
    else:
        guidance["platforms"]["windows"]["steps"][0][field] += "updated"
    isolated_guidance.write_text(json.dumps(guidance), encoding="utf-8")
    assert generator.main(["--check"]) == 1
    assert generator.main(["--write"]) == 0
    assert generator.main(["--check"]) == 0


@pytest.mark.parametrize("name", [generator.SOURCE, *ARTIFACTS])
@pytest.mark.parametrize("mutation", ["NVIDIA", "https://www.amd.com/drivers", "GeForce", "Radeon", "GPU driver"])
def test_cpu_only_guard_rejects_forbidden_content(isolated_guidance: Path, name: str, mutation: str) -> None:
    assert generator.main(["--write"]) == 0
    # Use an isolated copy: neither source nor checked-in outputs are mutated.
    path = generator.ROOT / name
    path.write_text(path.read_text(encoding="utf-8") + mutation, encoding="utf-8")
    with pytest.raises(AssertionError, match=re.escape(name)):
        assert_cpu_only_guidance(generator.ROOT)


class HelpContent(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.links.append(dict(attrs)["href"])

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def test_help_has_windows_content_and_escapes_html() -> None:
    guidance = read_guidance()
    platform = guidance["platforms"]["windows"]
    platform["summary"] = '<CPU & "runtime">'
    platform["steps"][0].update({
        "label": '<Intel & "CPU">', "note": '<Install & "restart">',
        "url": 'https://example.com/?a=1&b="two"',
    })
    guidance["warnings"][0]["text"] = '<PoCL & "Windows">'
    guidance["warnings"].append({"id": "linux_only", "platforms": ["linux"], "text": "Linux-only warning"})
    rendered = generator.render_help(guidance)
    parser = HelpContent()
    parser.feed(rendered)
    assert parser.links == [step["url"] for step in platform["steps"]]
    for value in [platform["summary"], platform["steps"][0]["label"], platform["steps"][0]["note"], guidance["warnings"][0]["text"]]:
        assert value in parser.text
    assert "<CPU" not in rendered and "<Intel" not in rendered
    assert "<Install" not in rendered and "<PoCL" not in rendered
    assert "Linux-only warning" not in rendered
    assert guidance["platforms"]["linux"]["steps"][0]["url"] not in rendered


def test_include_quotes_apostrophes_and_line_breaks() -> None:
    assert generator.pascal_string("Intel's runtime\r\nNext line") == "'Intel''s runtime' + #13#10 + 'Next line'"
    guidance = read_guidance()
    rendered = generator.render_include(guidance)
    assert "OpenClGuidanceTitle = 'CPU OpenCL runtime';" in rendered
    for text in [guidance["platforms"]["windows"]["summary"], guidance["warnings"][0]["text"],
                 *guidance["platforms"]["windows"]["steps"][0].values()]:
        if text != "intel":
            assert text.replace("'", "''") in rendered
    assert guidance["platforms"]["linux"]["steps"][0]["url"] not in rendered
    guidance["warnings"][0]["platforms"] = ["linux"]
    assert guidance["warnings"][0]["text"] not in generator.render_include(guidance)
