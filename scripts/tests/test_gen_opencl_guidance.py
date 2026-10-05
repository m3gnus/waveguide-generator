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
GPU_TERMS = re.compile(
    r"\b(?:geforce|radeon|arc[\s-]+graphics|iris[\s-]+xe"
    r"|(?:gpu|graphics)[\s-]+drivers?)\b",
    re.IGNORECASE,
)
# Also catch advice with intervening words or the driver named before the GPU,
# e.g. "Install the latest driver for your graphics card."
GPU_DRIVER_ADVICE = re.compile(
    r"(?=[^.!?]*\b(?:install(?:ing|ation)?|updat(?:e|ing))\b)"
    r"(?=[^.!?]*\b(?:gpu|graphics)\b)(?=[^.!?]*\bdrivers?\b)[^.!?]+",
    re.IGNORECASE,
)


def assert_cpu_only_prose(text: str, name: str) -> None:
    for sentence in re.split(r"[.!?]", text):
        assert not GPU_TERMS.search(sentence), f"{name}: GPU product or driver wording"
        assert not GPU_DRIVER_ADVICE.search(sentence), f"{name}: GPU driver installation advice"
        if re.search(r"\b(?:amd|intel)\b", sentence, re.IGNORECASE):
            assert not re.search(r"\b(?:gpu|graphics)\b", sentence, re.IGNORECASE), f"{name}: GPU vendor wording"
        if re.search(r"\b(?:nvidia|cuda)\b", sentence, re.IGNORECASE):
            # The only GPU pointer is NVIDIA + BEAT + CUDA, within one sentence.
            assert all(re.search(rf"\b{word}\b", sentence, re.IGNORECASE)
                       for word in ("nvidia", "beat", "cuda")), f"{name}: GPU alternative wording"
            assert not re.search(r"\bopencl\b", sentence, re.IGNORECASE), f"{name}: GPU OpenCL advice"
            assert not (re.search(r"\bdrivers?\b", sentence, re.IGNORECASE)
                        and re.search(r"\b(?:install(?:ing|ation)?|updat(?:e|ing))\b", sentence, re.IGNORECASE)), (
                f"{name}: GPU driver installation advice"
            )


def guidance_strings(value: object):
    """Scan decoded values recursively so every visible field is covered."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from guidance_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from guidance_strings(child)


def read_guidance() -> dict:
    return json.loads((generator.ROOT / generator.SOURCE).read_text(encoding="utf-8"))


def assert_cpu_only_guidance(root: Path) -> None:
    for name in [generator.SOURCE, *ARTIFACTS]:
        content = (root / name).read_text(encoding="utf-8")
        if name == generator.SOURCE:
            guidance = json.loads(content)
            for platform in guidance["platforms"].values():
                assert all(step["url"] in CPU_RUNTIME_URLS for step in platform["steps"]), name
            for value in guidance_strings(guidance):
                assert_cpu_only_prose(value, name)
            content = json.dumps(guidance, ensure_ascii=False)
        urls = URL.findall(content)
        if name.endswith(".html"):
            parser = HelpContent()
            parser.feed(content)
            urls.extend(parser.links)
        assert set(urls) <= CPU_RUNTIME_URLS, f"{name}: unexpected URLs {set(urls) - CPU_RUNTIME_URLS}"
        # JSON fields are separate prose; serialization must not join a title
        # mentioning OpenCL to an unrelated NVIDIA alternative sentence.
        if name != generator.SOURCE:
            assert_cpu_only_prose(content, name)
        if name.endswith(".html"):
            assert_cpu_only_prose(" ".join(parser.text), name)
        elif name.endswith(".iss"):
            # Decode Pascal literals and control characters as the installer does.
            tokens = re.findall(r"'(?:[^']|'')*'|#\d+", content)
            prose = "".join(
                chr(int(token[1:])) if token.startswith("#")
                else token[1:-1].replace("''", "'")
                for token in tokens
            )
            assert_cpu_only_prose(prose, name)
    assert not URL.search((root / COMPONENT).read_text(encoding="utf-8")), COMPONENT


def test_guidance_contains_no_gpu_driver_links_or_instructions() -> None:
    assert_cpu_only_guidance(generator.ROOT)


@pytest.mark.parametrize("text", [
    "NVIDIA", "GeForce", "RADEON", "Arc Graphics", "Iris Xe", "CUDA",
    "GPU driver", "graphics driver", "graphics-driver", "GPU\ndrivers",
    "Install the latest driver for your GPU.",
    "Update the display driver for your graphics card.",
    "Your graphics card needs a driver update.",
    "Install the NVIDIA GPU driver to enable OpenCL.",
    "update your graphics driver",
    "install the Radeon driver",
    "Use BEAT with CUDA on an NVIDIA GPU to enable OpenCL.",
    "Use BEAT with CUDA on NVIDIA and install the driver.",
    "Use BEAT with CUDA on NVIDIA and update the driver.",
    "Use NVIDIA with OpenCL. Choose BEAT with CUDA.",
    "NVIDIA. Use BEAT with CUDA.",
    "Use NVIDIA graphics with OpenCL.",
    "Use AMD graphics with OpenCL.",
    "Use Intel graphics with OpenCL.",
    "Use OpenCL on an AMD GPU.",
    "Use OpenCL on an Intel GPU.",
])
def test_prose_guard_rejects_gpu_terms_and_driver_advice(text: str) -> None:
    with pytest.raises(AssertionError, match="prose mutation: GPU"):
        assert_cpu_only_prose(text, "prose mutation")


@pytest.mark.parametrize("text", [
    "AMD processors", "Intel processors", "graphics-card solver",
    "Install your distribution's PoCL CPU runtime package.",
    "A graphics-card solver is supported. Install a CPU OpenCL runtime.",
    "use BEAT with CUDA on an NVIDIA GPU",
    "Use BEAT with CUDA on an NVIDIA GPU. BEMPP uses CPU OpenCL.",
])
def test_prose_guard_allows_cpu_and_solver_wording(text: str) -> None:
    assert_cpu_only_prose(text, "legitimate wording")


def test_prose_guard_allows_exact_gpu_alternative() -> None:
    assert_cpu_only_prose(read_guidance()["gpu_alternatives"][0]["text"], "GPU alternative")


def test_versioned_contract_and_cpu_runtime_content() -> None:
    guidance = read_guidance()
    assert set(guidance) == {"version", "title", "labels", "warnings", "notices", "reasons", "platforms", "gpu_alternatives"}
    assert guidance["gpu_alternatives"] == [{
        "id": "nvidia_beat_cuda", "platforms": ["windows", "linux"],
        "text": "On a computer with an NVIDIA graphics card, choose WG's BEAT solver with CUDA instead of BEMPP. BEMPP is for computers without a supported graphics card.",
    }]
    assert guidance["title"] == "CPU OpenCL runtime"
    assert guidance["labels"] == {"heading": guidance["title"], "ariaLabel": guidance["title"]}
    assert guidance["version"] == 1
    assert set(guidance["reasons"]) == {"no_device", "smoke_test_failed", "smoke_test_timeout", "inventory_timeout", "pocl_windows"}
    assert guidance["reasons"]["no_device"] == "No CPU OpenCL device was found."
    assert all(text and "\n" not in text for text in guidance["reasons"].values())
    assert set(guidance["platforms"]) == {"windows", "linux"}
    windows = guidance["platforms"]["windows"]
    assert len(windows["steps"]) == 1
    assert windows["steps"][0] == {
        "vendor": "intel",
        "label": "Intel CPU OpenCL runtime",
        "url": "https://www.intel.com/content/www/us/en/developer/articles/technical/intel-cpu-runtime-for-opencl-applications-with-sycl-support.html",
        "note": "Intel's CPU runtime officially supports Intel processors; it has also worked on AMD processors in our testing. Installing it needs administrator rights, which WG itself does not. If no OpenCL device works, WG falls back to its slower numba engine.",
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


@pytest.mark.parametrize("mutation", [
    "Install the NVIDIA GPU driver to enable OpenCL.",
    "To enable OpenCL, update your graphics driver.",
])
def test_reviewer_linux_intel_prose_mutation_fails(isolated_guidance: Path, mutation: str) -> None:
    guidance = json.loads(isolated_guidance.read_text(encoding="utf-8"))
    guidance["platforms"]["linux"]["steps"][1]["note"] = mutation
    isolated_guidance.write_text(json.dumps(guidance), encoding="utf-8")
    assert generator.main(["--write"]) == 0
    assert generator.main(["--check"]) == 0
    with pytest.raises(AssertionError, match=re.escape(generator.SOURCE) + ": GPU"):
        assert_cpu_only_guidance(generator.ROOT)


@pytest.mark.parametrize("field", [
    ("title",), ("labels", "heading"), ("labels", "ariaLabel"),
    ("reasons", "no_device"), ("warnings", 0, "text"),
    ("gpu_alternatives", 0, "text"),
    ("platforms", "windows", "summary"), ("platforms", "linux", "summary"),
    ("platforms", "windows", "steps", 0, "label"),
    ("platforms", "windows", "steps", 0, "note"),
    ("platforms", "linux", "steps", 0, "label"),
    ("platforms", "linux", "steps", 0, "note"),
])
def test_prose_guard_checks_every_visible_field(isolated_guidance: Path, field: tuple) -> None:
    guidance = json.loads(isolated_guidance.read_text(encoding="utf-8"))
    entry = guidance
    for key in field[:-1]:
        entry = entry[key]
    entry[field[-1]] = "Update your graphics driver."
    # JSON escapes must not conceal the decoded user-visible wording.
    isolated_guidance.write_text(json.dumps(guidance).replace("graphics", r"\u0067raphics"), encoding="utf-8")
    assert generator.main(["--write"]) == 0
    with pytest.raises(AssertionError, match=re.escape(generator.SOURCE) + ": GPU"):
        assert_cpu_only_guidance(generator.ROOT)


@pytest.mark.parametrize("name", ARTIFACTS)
@pytest.mark.parametrize("mutation", [
    "Install the NVIDIA GPU driver to enable OpenCL.",
    "Update your graphics driver.",
    "Install the latest driver for your GPU.",
])
def test_prose_guard_checks_generated_artifacts(isolated_guidance: Path, name: str, mutation: str) -> None:
    assert generator.main(["--write"]) == 0
    guidance = read_guidance()
    guidance["platforms"]["windows"]["steps"][0]["note"] = mutation
    rendered = generator.render_help(guidance) if name.endswith(".html") else generator.render_include(guidance)
    (generator.ROOT / name).write_text(rendered, encoding="utf-8")
    with pytest.raises(AssertionError, match=re.escape(name) + ": GPU"):
        assert_cpu_only_guidance(generator.ROOT)


@pytest.mark.parametrize("name, mutation", [
    ("shared/opencl-guidance.html", "<p>Update your gr&#97;phics driver.</p>"),
    ("installers/windows/opencl-guidance.iss", "Notice = 'Update your gr' + #97 + 'phics' + #13#10 + 'driver.';"),
    ("shared/opencl-guidance.html", "<p>Use BEAT with CUDA on NVIDIA for Open&#67;L.</p>"),
    ("installers/windows/opencl-guidance.iss", "Notice = 'Use BEAT with CUDA on NVIDIA for Open' + #67 + 'L.';"),
])
def test_prose_guard_checks_decoded_artifacts(isolated_guidance: Path, name: str, mutation: str) -> None:
    assert generator.main(["--write"]) == 0
    path = generator.ROOT / name
    path.write_text(path.read_text(encoding="utf-8") + mutation, encoding="utf-8")
    with pytest.raises(AssertionError, match=re.escape(name) + ": GPU"):
        assert_cpu_only_guidance(generator.ROOT)


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


@pytest.mark.parametrize("field", ["title", "heading", "summary", "label", "url", "note", "warning", "gpu_alternative"])
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
    elif field == "gpu_alternative":
        guidance["gpu_alternatives"][0]["text"] += " Updated."
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
        for text in [guidance["title"], *guidance["labels"].values(), *guidance["reasons"].values(),
                     guidance["gpu_alternatives"][0]["text"]]:
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
    guidance["gpu_alternatives"][0]["text"] = '<BEAT & "CUDA">'
    guidance["gpu_alternatives"].append({"id": "linux_only", "platforms": ["linux"], "text": "Linux-only alternative"})
    guidance["warnings"].append({"id": "linux_only", "platforms": ["linux"], "text": "Linux-only warning"})
    rendered = generator.render_help(guidance)
    parser = HelpContent()
    parser.feed(rendered)
    assert parser.links == [step["url"] for step in platform["steps"]]
    for value in [platform["summary"], platform["steps"][0]["label"], platform["steps"][0]["note"],
                  guidance["warnings"][0]["text"], guidance["gpu_alternatives"][0]["text"]]:
        assert value in parser.text
    assert "<CPU" not in rendered and "<Intel" not in rendered
    assert "<Install" not in rendered and "<PoCL" not in rendered
    assert "Linux-only warning" not in rendered
    assert "<BEAT" not in rendered and "Linux-only alternative" not in rendered
    assert parser.text.index(platform["summary"]) < parser.text.index(guidance["gpu_alternatives"][0]["text"])
    assert parser.text.index(guidance["gpu_alternatives"][0]["text"]) < parser.text.index(platform["steps"][0]["label"])
    assert guidance["platforms"]["linux"]["steps"][0]["url"] not in rendered


def test_include_quotes_apostrophes_and_line_breaks() -> None:
    assert generator.pascal_string("Intel's runtime\r\nNext line") == "'Intel''s runtime' + #13#10 + 'Next line'"
    guidance = read_guidance()
    rendered = generator.render_include(guidance)
    assert f"OpenClGuidanceTitle = {generator.pascal_string(guidance['title'])};" in rendered
    for text in [guidance["platforms"]["windows"]["summary"], guidance["warnings"][0]["text"],
                 guidance["gpu_alternatives"][0]["text"],
                 *guidance["platforms"]["windows"]["steps"][0].values()]:
        if text != "intel":
            assert text.replace("'", "''") in rendered
    assert guidance["platforms"]["linux"]["steps"][0]["url"] not in rendered
    alternative = guidance["gpu_alternatives"][0]["text"].replace("'", "''")
    assert rendered.index(guidance["platforms"]["windows"]["summary"].replace("'", "''")) < rendered.index(alternative)
    assert rendered.index(alternative) < rendered.index(guidance["platforms"]["windows"]["steps"][0]["label"])
    guidance["gpu_alternatives"][0]["platforms"] = ["linux"]
    assert alternative not in generator.render_include(guidance)
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


# -- The approved wording, pinned ------------------------------------------------
#
# This is the guarantee that the guidance never sends anyone to a GPU OpenCL
# driver: the complete approved content of shared/opencl-driver-guidance.v1.json
# is held here, and the file must equal it exactly. A word list cannot be
# complete (a reviewer got "AMD video card driver", "GPU runtime" and "Use BEAT
# with CUDA on an NVIDIA GPU. Install its OpenCL driver." past one), so the prose
# patterns above are only a secondary check. Any change to the wording, however
# small, fails here until this copy is changed in the same commit, which makes
# every wording change an explicit, reviewable act. The installer text and the
# help page are generated from the JSON and drift-checked, so pinning the JSON
# pins them too.
APPROVED_GUIDANCE = {'version': 1,
 'title': 'CPU OpenCL runtime',
 'labels': {'heading': 'CPU OpenCL runtime', 'ariaLabel': 'CPU OpenCL runtime'},
 'gpu_alternatives': [{'id': 'nvidia_beat_cuda',
                       'platforms': ['windows', 'linux'],
                       'text': "On a computer with an NVIDIA graphics card, choose WG's BEAT "
                               'solver with CUDA instead of BEMPP. BEMPP is for computers without '
                               'a supported graphics card.'}],
 'warnings': [{'id': 'pocl_windows',
               'platforms': ['windows'],
               'text': 'PoCL on Windows shows up as an OpenCL device but computes nothing, so '
                       'installing it does not help.'}],
 'notices': {'infinite_baffle_numba': "Infinite baffle runs on BEMPP's CPU (numba) engine on this "
                                      'computer. It is correct but slow, and the first solve includes '
                                      'about a minute of warm-up.'},
 'reasons': {'no_device': 'No CPU OpenCL device was found.',
             'smoke_test_failed': 'The CPU OpenCL runtime failed its test calculation.',
             'smoke_test_timeout': "The CPU OpenCL runtime's test calculation timed out. WG will check again; "
                                   'restarting WG also retries.',
             'inventory_timeout': 'Checking for a CPU OpenCL runtime took too long, so WG is using its slower '
                                  'engine for now. WG will check again; restarting WG also retries.',
             'pocl_windows': 'PoCL on Windows cannot run BEMPP calculations.'},
 'platforms': {'windows': {'summary': "On computers without a supported graphics-card solver, WG's "
                                      'BEMPP solver runs on the CPU. Installing a CPU OpenCL '
                                      'runtime makes it much faster. It still works without one, '
                                      'more slowly.',
                           'steps': [{'vendor': 'intel',
                                      'label': 'Intel CPU OpenCL runtime',
                                      'url': 'https://www.intel.com/content/www/us/en/developer/articles/technical/intel-cpu-runtime-for-opencl-applications-with-sycl-support.html',
                                      'note': "Intel's CPU runtime officially supports Intel "
                                              'processors; it has also worked on AMD processors in '
                                              'our testing. Installing it needs administrator '
                                              'rights, which WG itself does not. If no OpenCL '
                                              'device works, WG falls '
                                              'back to its slower numba engine.'}]},
               'linux': {'summary': "On computers without a supported graphics-card solver, WG's "
                                    'BEMPP solver runs on the CPU. Installing a CPU OpenCL runtime '
                                    'makes it much faster. It still works without one, more '
                                    'slowly.',
                         'steps': [{'vendor': 'pocl',
                                    'label': 'PoCL CPU OpenCL runtime',
                                    'url': 'https://portablecl.org/',
                                    'note': "Install your distribution's PoCL CPU runtime "
                                            'package.'},
                                   {'vendor': 'intel',
                                    'label': 'Intel CPU OpenCL runtime',
                                    'url': 'https://www.intel.com/content/www/us/en/developer/articles/technical/intel-cpu-runtime-for-opencl-applications-with-sycl-support.html',
                                    'note': "Alternatively, install Intel's CPU runtime through "
                                            "your distribution or Intel's packages."}]}}}


def _assert_guidance_is_the_approved_text(data: object) -> None:
    assert data == APPROVED_GUIDANCE, (
        "shared/opencl-driver-guidance.v1.json differs from the approved wording "
        "pinned in this test. If the change is intended and approved, update "
        "APPROVED_GUIDANCE in the same commit."
    )


def _text_leaves(node: object, path: tuple = ()) -> list[tuple]:
    if isinstance(node, dict):
        return [leaf for key, value in node.items() for leaf in _text_leaves(value, path + (key,))]
    if isinstance(node, list):
        return [leaf for index, value in enumerate(node) for leaf in _text_leaves(value, path + (index,))]
    return [path] if isinstance(node, str) else []


def _with_leaf(data: object, path: tuple, value: str) -> object:
    clone = json.loads(json.dumps(data))
    node = clone
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return clone


def test_guidance_json_is_exactly_the_approved_wording() -> None:
    data = json.loads((generator.ROOT / generator.SOURCE).read_text(encoding="utf-8"))
    _assert_guidance_is_the_approved_text(data)


@pytest.mark.parametrize("path", _text_leaves(APPROVED_GUIDANCE), ids=lambda p: "/".join(map(str, p)))
def test_any_edit_to_a_text_field_fails_the_pin(path: tuple) -> None:
    original = APPROVED_GUIDANCE
    for key in path:
        original = original[key]
    with pytest.raises(AssertionError):
        _assert_guidance_is_the_approved_text(_with_leaf(APPROVED_GUIDANCE, path, original + " "))


_LINUX_INTEL_NOTE = ("platforms", "linux", "steps", 1, "note")
_GPU_ALTERNATIVE = ("gpu_alternatives", 0, "text")


@pytest.mark.parametrize(
    ("path", "evasion"),
    [
        (_LINUX_INTEL_NOTE, "Install the AMD video card driver."),
        (_LINUX_INTEL_NOTE, "Install the Intel Arc / Intel Iris OpenCL driver."),
        (_LINUX_INTEL_NOTE, "Install a GPU runtime."),
        (_LINUX_INTEL_NOTE, "Update your video card driver."),
        (_GPU_ALTERNATIVE, "Use BEAT with CUDA on an NVIDIA GPU. Install its OpenCL driver."),
        (_GPU_ALTERNATIVE, "Use BEAT with CUDA on an NVIDIA GPU and set up the driver."),
        (_GPU_ALTERNATIVE, "On a computer with an NVIDIA graphics card, choose BEMPP instead of BEAT with CUDA."),
    ],
)
def test_reviewer_prose_evasions_fail_the_pin(path: tuple, evasion: str) -> None:
    """The seven evasions that passed the word-list guard (lander review round 3)."""

    with pytest.raises(AssertionError):
        _assert_guidance_is_the_approved_text(_with_leaf(APPROVED_GUIDANCE, path, evasion))


# -- What only ISCC would otherwise catch -----------------------------------------


def test_generated_include_obeys_the_inno_textual_rules() -> None:
    include = (generator.ROOT / "installers/windows/opencl-guidance.iss").read_text(encoding="utf-8")
    generator.check_inno_source(include)
    assert not [line for line in include.split("\n") if line.lstrip().startswith("#")]


@pytest.mark.parametrize(
    "source",
    [
        "  A = 'x' +\n  #13#10 + 'y';\n",  # a continuation line starting with a character code
        "#13#10\n",
        "{ installs beside {app}, never inside it }\n",  # a brace comment closed early by {app}
        "{ {from, to} }\n",
        "  A = 'unterminated;\n",
        "{ unterminated comment\n",
    ],
)
def test_inno_check_refuses_what_iscc_refuses(source: str) -> None:
    with pytest.raises(ValueError):
        generator.check_inno_source(source)


@pytest.mark.parametrize(
    "source",
    [
        "{ a plain comment }\n  A = 'text with {app} and # inside a string';\n",
        "  A = 'it''s' + #13#10 + 'fine';\n",
    ],
)
def test_inno_check_accepts_valid_source(source: str) -> None:
    generator.check_inno_source(source)


def test_guidance_text_cannot_make_the_include_uncompilable() -> None:
    """Line breaks and braces in the wording stay inside one quoted line."""

    guidance = json.loads(json.dumps(APPROVED_GUIDANCE))
    guidance["title"] = "A {title}\n#13 on its own line"
    guidance["platforms"]["windows"]["summary"] = "First line\n# second line {app}"
    rendered = generator.render_include(guidance)
    generator.check_inno_source(rendered)
    assert len(rendered.split("\n")) == 5  # comment (2 lines), title, text, trailing newline
