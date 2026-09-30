from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import re

import pytest

from scripts import gen_opencl_guidance as generator

ARTIFACTS = ["installers/windows/opencl-guidance.iss", "shared/opencl-guidance.html"]
COMPONENT = "frontend/src/shell/OpenClGuidance.tsx"
CPU_RUNTIME_URLS = {
    "https://www.intel.com/content/www/us/en/developer/articles/technical/intel-cpu-runtime-for-opencl-applications-with-sycl-support.html",
    "https://portablecl.org/",
}
URL = re.compile(r"(?:[a-z][a-z0-9+.-]*:)?//[^\s\"'<>]+", re.IGNORECASE)


def read_guidance() -> dict:
    return json.loads((generator.ROOT / generator.SOURCE).read_text(encoding="utf-8"))


def assert_cpu_only_guidance(root: Path) -> None:
    for name in [generator.SOURCE, *ARTIFACTS]:
        content = (root / name).read_text(encoding="utf-8")
        if name == generator.SOURCE:
            guidance = json.loads(content)
            for platform in guidance["platforms"].values():
                assert all(step["url"] in CPU_RUNTIME_URLS for step in platform["steps"]), name
            content = json.dumps(guidance, ensure_ascii=False)
        urls = URL.findall(content)
        if name.endswith(".html"):
            parser = HelpContent()
            parser.feed(content)
            urls.extend(parser.links)
        assert set(urls) <= CPU_RUNTIME_URLS, f"{name}: unexpected URLs {set(urls) - CPU_RUNTIME_URLS}"
    assert not URL.search((root / COMPONENT).read_text(encoding="utf-8")), COMPONENT


def test_guidance_contains_no_gpu_driver_links_or_instructions() -> None:
    assert_cpu_only_guidance(generator.ROOT)


def test_versioned_contract_and_cpu_runtime_content() -> None:
    guidance = read_guidance()
    assert set(guidance) == {"version", "title", "labels", "warnings", "reasons", "platforms"}
    assert guidance["title"] == "CPU OpenCL runtime"
    assert guidance["labels"] == {"heading": guidance["title"], "ariaLabel": guidance["title"]}
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
        assert path.read_bytes() == rendered.encode("utf-8"), (
            f"{path.name} is stale; run scripts/gen_opencl_guidance.py --write"
        )
    assert generator.main(["--check"]) == 0


@pytest.fixture
def isolated_guidance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "shared").mkdir()
    (tmp_path / "installers/windows").mkdir(parents=True)
    (tmp_path / COMPONENT).parent.mkdir(parents=True)
    (tmp_path / COMPONENT).write_bytes((generator.ROOT / COMPONENT).read_bytes())
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


@pytest.mark.parametrize("field", ["title", "heading", "summary", "label", "url", "note", "warning"])
def test_source_changes_require_regeneration(isolated_guidance: Path, field: str) -> None:
    assert generator.main(["--write"]) == 0
    guidance = json.loads(isolated_guidance.read_text(encoding="utf-8"))
    if field == "title":
        guidance["title"] += " Updated."
    elif field == "heading":
        guidance["labels"][field] += " Updated."
    elif field == "summary":
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
@pytest.mark.parametrize("mutation", [
    "https://www.nvidia.com/Download/index.aspx",
    "https://www.amd.com/drivers",
    "https://www.intel.com/content/www/us/en/download/785597/intel-arc-iris-xe-graphics-windows.html",
    "https://example.com/unreviewed-runtime",
])
def test_cpu_only_guard_rejects_unapproved_urls(isolated_guidance: Path, name: str, mutation: str) -> None:
    assert generator.main(["--write"]) == 0
    # Use an isolated copy: neither source nor checked-in outputs are mutated.
    path = generator.ROOT / name
    content = path.read_text(encoding="utf-8")
    if name == generator.SOURCE:
        guidance = json.loads(content)
        guidance["review_mutation"] = mutation
        content = json.dumps(guidance)
    else:
        content += mutation
    path.write_text(content, encoding="utf-8")
    with pytest.raises(AssertionError, match=re.escape(name)):
        assert_cpu_only_guidance(generator.ROOT)


def test_reviewer_linux_intel_graphics_mutation_fails(isolated_guidance: Path) -> None:
    guidance = json.loads(isolated_guidance.read_text(encoding="utf-8"))
    guidance["platforms"]["linux"]["steps"][1]["url"] = (
        "https://www.intel.com/content/www/us/en/download/785597/intel-arc-iris-xe-graphics-windows.html"
    )
    isolated_guidance.write_text(json.dumps(guidance), encoding="utf-8")
    assert generator.main(["--write"]) == 0
    assert generator.main(["--check"]) == 0
    with pytest.raises(AssertionError, match=re.escape(generator.SOURCE)):
        assert_cpu_only_guidance(generator.ROOT)


@pytest.mark.parametrize("url", [
    "https://www.nvidia.com/Download/index.aspx",
    "javascript:alert(1)",
])
def test_cpu_only_guard_checks_decoded_step_urls(isolated_guidance: Path, url: str) -> None:
    guidance = json.loads(isolated_guidance.read_text(encoding="utf-8"))
    guidance["platforms"]["linux"]["steps"][1]["url"] = url
    isolated_guidance.write_text(json.dumps(guidance).replace("/", r"\u002f"), encoding="utf-8")
    assert generator.main(["--write"]) == 0
    with pytest.raises(AssertionError, match=re.escape(generator.SOURCE)):
        assert_cpu_only_guidance(generator.ROOT)


@pytest.mark.parametrize("url", ["https://www.nvidia.com/Download/index.aspx", *sorted(CPU_RUNTIME_URLS)])
def test_component_rejects_even_cpu_url_literals(isolated_guidance: Path, url: str) -> None:
    assert generator.main(["--write"]) == 0
    component = generator.ROOT / COMPONENT
    component.write_text(component.read_text(encoding="utf-8") + f'\nconst link = <a href="{url}">Download</a>;\n', encoding="utf-8")
    with pytest.raises(AssertionError, match=re.escape(COMPONENT)):
        assert_cpu_only_guidance(generator.ROOT)


@pytest.mark.parametrize("name", ARTIFACTS)
def test_write_pins_lf_and_check_rejects_crlf_bytes(isolated_guidance: Path, name: str, capsys: pytest.CaptureFixture) -> None:
    assert generator.main(["--write"]) == 0
    output = generator.ROOT / name
    expected = generator.generated_files(generator.ROOT)[output].encode("utf-8")
    assert output.read_bytes() == expected
    assert b"\r" not in expected
    output.write_bytes(expected.replace(b"\n", b"\r\n"))
    # Text-mode reading hides the corruption that the byte check must catch.
    assert output.read_text(encoding="utf-8") == expected.decode("utf-8")
    assert generator.main(["--check"]) == 1
    assert name in capsys.readouterr().err
    assert generator.main(["--write"]) == 0
    assert output.read_bytes() == expected
    assert generator.main(["--check"]) == 0


def test_wording_is_only_in_json() -> None:
    guidance = read_guidance()
    for name in [COMPONENT, "scripts/gen_opencl_guidance.py"]:
        source = (generator.ROOT / name).read_text(encoding="utf-8")
        for text in [guidance["title"], *guidance["labels"].values(), *guidance["reasons"].values()]:
            assert text not in source, name


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
    assert f"OpenClGuidanceTitle = {generator.pascal_string(guidance['title'])};" in rendered
    for text in [guidance["platforms"]["windows"]["summary"], guidance["warnings"][0]["text"],
                 *guidance["platforms"]["windows"]["steps"][0].values()]:
        if text != "intel":
            assert text.replace("'", "''") in rendered
    assert guidance["platforms"]["linux"]["steps"][0]["url"] not in rendered
    guidance["warnings"][0]["platforms"] = ["linux"]
    assert guidance["warnings"][0]["text"] not in generator.render_include(guidance)


@pytest.mark.parametrize(("text", "expected"), [
    ("before\rafter", "'before' + #13 + 'after'"),
    ("before\nafter", "'before' + #13#10 + 'after'"),
    ("before\r\nafter", "'before' + #13#10 + 'after'"),
    ("before\tafter", "'before' + #9 + 'after'"),
    ("before\0after", "'before' + #0 + 'after'"),
])
def test_pascal_escapes_control_characters(text: str, expected: str) -> None:
    assert generator.pascal_string(text) == expected
