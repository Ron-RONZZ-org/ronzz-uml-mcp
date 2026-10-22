"""Tests for the figure pipeline: preamble hoisting, class stripping, render, compile."""

import base64
from pathlib import Path

import pytest
from mcp.types import ImageContent, TextContent

from mcp_core.core import config
from mcp_core.core.diagram_validation import validate_uml_inputs
from mcp_core.tools.diagram_tools import render_diagram
from tools.kroki.tikz import strip_tikz_documentclass, wrap_tikz_standalone

SNIPPET = r"\begin{tikzpicture}\draw (0,0) -- (1,1);\end{tikzpicture}"


def test_wrap_hoists_any_package():
    snippet = "\\usepackage{chemfig}\n" + SNIPPET
    out = wrap_tikz_standalone(snippet)
    preamble, body = out.split("\\begin{document}", 1)
    assert "\\usepackage{chemfig}" in preamble
    assert "\\usepackage{chemfig}" not in body


def test_wrap_hoists_tikz_library_and_pgfplotsset():
    snippet = "\\usetikzlibrary{arrows.meta}\n\\pgfplotsset{compat=1.18}\n" + SNIPPET
    out = wrap_tikz_standalone(snippet)
    preamble, body = out.split("\\begin{document}", 1)
    assert "\\usetikzlibrary{arrows.meta}" in preamble
    assert "\\pgfplotsset{compat=1.18}" in preamble
    assert "\\usetikzlibrary" not in body
    assert "\\pgfplotsset" not in body


def test_wrap_pgfplots_package_not_emitted_as_library():
    snippet = "\\begin{axis}\\addplot {x^2};\\end{axis}"
    out = wrap_tikz_standalone(snippet)
    assert "\\usepackage{pgfplots}" in out
    assert "\\usetikzlibrary{pgfplots" not in out


def test_strip_documentclass_keeps_packages_on_one_line():
    document = (
        r"\documentclass[border=2pt]{standalone}\usepackage{chemfig}"
        r"\begin{document}x\end{document}"
    )
    out = strip_tikz_documentclass(document)
    assert "\\documentclass" not in out
    assert r"\usepackage{chemfig}" in out
    assert "\\begin{document}" in out


def test_validate_reports_compile_failure(monkeypatch):
    from mcp_core.core import diagram_validation

    monkeypatch.setattr(
        diagram_validation,
        "_compile_errors",
        lambda cfg, prepared: (["! LaTeX Error: Missing \\begin{document}."], 9.5),
    )
    out = validate_uml_inputs(
        "tikz", r"\begin{tikzpicture}\draw (0,0) -- ;\end{tikzpicture}"
    )
    assert out["valid"] is False
    assert out["compiled"] is True
    assert "LaTeX Error" in out["errors"][0]


def test_render_diagram_returns_image_and_path(tmp_path, monkeypatch):
    target = tmp_path / "tikz.svg"
    target.write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'/>")

    monkeypatch.setattr(
        "mcp_core.tools.diagram_tools.generate_from_request",
        lambda request: {
            "local_path": str(target),
            "render_ms": 1.5,
            "mime_type": "image/svg+xml",
        },
    )

    result = render_diagram("tikz", SNIPPET)
    assert len(result) == 2
    assert isinstance(result[0], TextContent)
    assert isinstance(result[1], ImageContent)
    assert result[1].mime_type == "image/svg+xml"
    assert base64.b64decode(result[1].data).startswith(b"<svg")
    assert str(target) in result[0].text
    assert "content_base64" not in result[0].text


def test_render_diagram_reports_missing_file(monkeypatch):
    monkeypatch.setattr(
        "mcp_core.tools.diagram_tools.generate_from_request",
        lambda request: {"error": "Diagram service unavailable", "local_path": None},
    )
    result = render_diagram("tikz", SNIPPET)
    assert len(result) == 1
    assert "Diagram service unavailable" in result[0].text


def test_available_diagram_types_drops_unprobed_companion(monkeypatch):
    monkeypatch.setenv("MCP_DIAGRAM_PROBE", "true")
    monkeypatch.setattr(config, "_probe_companion_types", lambda server: {"bpmn"})
    monkeypatch.setattr(config, "_probe_done", False)
    monkeypatch.setattr(config, "_probe_attempted_at", 0.0)
    monkeypatch.setattr(config, "_unavailable_types", set())

    names = config.MCP_SETTINGS.available_diagram_types()
    assert "bpmn" not in names
    assert "tikz" in names
    assert "mermaid" in names


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeClient:
    def __init__(self, statuses):
        self._statuses = statuses

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, content=None):
        for diagram_type, status in self._statuses.items():
            if url.endswith(f"/{diagram_type}/svg"):
                return _FakeResponse(status)
        return _FakeResponse(200)


def test_probe_marks_503_as_unavailable(monkeypatch):
    import httpx

    fake = _FakeClient({"mermaid": 503, "bpmn": 200, "excalidraw": 404})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: fake)

    unavailable = config._probe_companion_types("http://kroki:8000")
    assert unavailable == {"mermaid", "excalidraw"}


def test_probe_fails_open_when_kroki_unreachable(monkeypatch):
    import httpx

    class _DownClient(_FakeClient):
        def post(self, url, content=None):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _DownClient({}))
    assert config._probe_companion_types("http://kroki:8000") is None
