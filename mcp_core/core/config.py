"""
Configuration settings for MCP server.
"""

import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from tools.kroki.kroki import LANGUAGE_OUTPUT_SUPPORT

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "")
    if v == "":
        return default
    return v.lower() in ("true", "1", "yes")


def _env_use_local_kroki() -> bool:
    return os.environ.get("USE_LOCAL_KROKI", "false").lower() in (
        "true",
        "1",
        "yes",
    )


def _diagram_fallback_default() -> bool:
    """
    When False, a Kroki failure does not try PlantUML server or Mermaid.ink.

    MCP_DIAGRAM_FALLBACK overrides when set. Otherwise disabled on Vercel or when
    USE_LOCAL_KROKI=true (Docker / local Kroki stack). Enabled for typical desktop
    use with public Kroki.
    """
    explicit = os.environ.get("MCP_DIAGRAM_FALLBACK", "").strip().lower()
    if explicit in ("true", "1", "yes"):
        return True
    if explicit in ("false", "0", "no"):
        return False
    if _env_bool("VERCEL", False):
        return False
    if _env_use_local_kroki():
        return False
    return True


def _default_output_dir() -> str:
    """Default diagram output directory (current working directory / output)."""
    return str(Path.cwd() / "output")


def _memory_only_default() -> bool:
    """
    When True, diagram generation never writes to disk (URL + base64 only).

    On Vercel (VERCEL set), defaults to True so serverless stays within /tmp limits
    and avoids redundant file I/O. Set MCP_MEMORY_ONLY=false to allow writes to
    VERCEL_OUTPUT_DIR. Locally, default False unless MCP_MEMORY_ONLY=true.
    """
    explicit = os.environ.get("MCP_MEMORY_ONLY", "").strip().lower()
    if explicit in ("true", "1", "yes"):
        return True
    if explicit in ("false", "0", "no"):
        return False
    return _env_bool("VERCEL", False)


def _url_only_default() -> bool:
    """
    When True, diagram pipeline returns Kroki/playground URLs only: no HTTP fetch of
    rendered bytes, no content_base64, no image bytes in the serverless function.

    On Vercel (VERCEL set), defaults to True. Set MCP_URL_ONLY=false to fetch and
    return base64 when not saving to disk. Locally, default False unless
    MCP_URL_ONLY=true.
    """
    explicit = os.environ.get("MCP_URL_ONLY", "").strip().lower()
    if explicit in ("true", "1", "yes"):
        return True
    if explicit in ("false", "0", "no"):
        return False
    return _env_bool("VERCEL", False)


def _get_output_dir() -> str:
    """Output dir: on Vercel use writable /tmp; else MCP env or cwd/output."""
    if os.environ.get("VERCEL"):
        return os.environ.get("VERCEL_OUTPUT_DIR", "/tmp/diagrams")
    return (
        os.environ.get("MCP_OUTPUT_DIR")
        or os.environ.get("UML_MCP_OUTPUT_DIR")
        or _default_output_dir()
    )


class DiagramType(BaseModel):
    """Configuration for a diagram type."""

    backend: str
    description: str
    formats: List[str] = ["png", "svg"]


# Kroki serves these through *companion* containers. Every other backend is
# baked into the core image, so only these can be missing at runtime. An absent
# companion answers 503 (or 404) instead of rendering.
COMPANION_DIAGRAM_TYPES = ("mermaid", "bpmn", "excalidraw")
_PROBE_RETRY_SECONDS = 60.0
_probe_lock = threading.Lock()
_probe_done = False
_probe_attempted_at: Optional[float] = None
_unavailable_types: set[str] = set()


def _probe_companion_types(kroki_server: str) -> Optional[set[str]]:
    """Return the companion types Kroki cannot serve, or None when unreachable."""
    import httpx

    unavailable: set[str] = set()
    timeout = httpx.Timeout(connect=1.0, read=2.0, write=2.0, pool=1.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            for diagram_type in COMPANION_DIAGRAM_TYPES:
                response = client.post(
                    f"{kroki_server.rstrip('/')}/{diagram_type}/svg", content=b"x"
                )
                if response.status_code in (404, 503):
                    unavailable.add(diagram_type)
                logger.info(
                    "Diagram availability probe: %s -> HTTP %s",
                    diagram_type,
                    response.status_code,
                )
    except httpx.HTTPError as exc:
        logger.warning(
            "Diagram availability probe could not reach Kroki at %s: %s",
            kroki_server,
            exc,
        )
        return None
    return unavailable


def _available_diagram_types(settings: "MCPSettings") -> Dict[str, DiagramType]:
    """Diagram types the deployment can render, probed once against Kroki."""
    global _probe_done, _probe_attempted_at, _unavailable_types
    if not _env_bool("MCP_DIAGRAM_PROBE", True):
        return settings.diagram_types
    with _probe_lock:
        now = time.monotonic()
        due = _probe_attempted_at is None or now - _probe_attempted_at >= _PROBE_RETRY_SECONDS
        if not _probe_done and due:
            _probe_attempted_at = now
            probed = _probe_companion_types(settings.kroki_server)
            if probed is not None:
                _unavailable_types = probed
                _probe_done = True
                if probed:
                    logger.warning(
                        "Diagram types disabled (companion container missing): %s",
                        ", ".join(sorted(probed)),
                    )
        unavailable = set(_unavailable_types)
    return {
        name: config
        for name, config in settings.diagram_types.items()
        if name not in unavailable
    }


class MCPSettings(BaseModel):
    """Configuration settings for MCP server."""

    server_name: str = "uml_mcp"  # MCP naming: {service}_mcp (protocol)
    display_name: str = "UML Diagram Generator"  # Human-readable for UI
    version: str = "1.3.0"
    read_only: bool = Field(default_factory=lambda: _env_bool("MCP_READ_ONLY", False))
    memory_only: bool = Field(default_factory=_memory_only_default)
    url_only: bool = Field(default_factory=_url_only_default)
    diagram_fallback_enabled: bool = Field(default_factory=_diagram_fallback_default)
    description: str = "Generate UML and other diagrams through MCP"
    config_schema_url: str = (
        ""  # Optional URL for session config schema (improves Configuration UX score)
    )
    max_code_length: int = Field(
        default_factory=lambda: int(os.environ.get("MCP_MAX_CODE_LENGTH", "500000"))
    )
    max_render_seconds: float = Field(
        default_factory=lambda: float(os.environ.get("MCP_MAX_RENDER_SECONDS", "30"))
    )
    batch_max_items: int = Field(
        default_factory=lambda: int(os.environ.get("MCP_BATCH_MAX_ITEMS", "20"))
    )
    rate_limit_per_minute: int = Field(
        default_factory=lambda: int(os.environ.get("MCP_RATE_LIMIT_PER_MINUTE", "0"))
    )
    output_dir: str = _get_output_dir()
    tools: List[str] = []
    prompts: List[str] = []
    resources: List[str] = []  # Added resources field
    diagram_types: Dict[str, DiagramType] = {}
    plantuml_server: str = os.environ.get(
        "PLANTUML_SERVER", "http://plantuml-server:8080"
    )
    kroki_server: str = os.environ.get("KROKI_SERVER", "https://kroki.io")

    @property
    def output_path(self) -> Path:
        """Output directory as a Path for file operations."""
        return Path(self.output_dir)

    def available_diagram_types(self) -> Dict[str, DiagramType]:
        """Diagram types this deployment can actually render.

        Companion-served types (mermaid, bpmn, excalidraw) are withheld when
        their container is not running in front of this Kroki.
        """
        return _available_diagram_types(self)


# Define supported diagram types with their backends. Formats from https://kroki.io/
DIAGRAM_TYPES = {
    # UML diagram types (PlantUML)
    "class": DiagramType(
        backend="plantuml",
        description="Shows classes, attributes, methods and relationships between classes",
        formats=LANGUAGE_OUTPUT_SUPPORT["plantuml"],
    ),
    "sequence": DiagramType(
        backend="plantuml",
        description="Shows object interactions arranged in time sequence",
        formats=LANGUAGE_OUTPUT_SUPPORT["plantuml"],
    ),
    "activity": DiagramType(
        backend="plantuml",
        description="Shows workflows or business processes",
        formats=LANGUAGE_OUTPUT_SUPPORT["plantuml"],
    ),
    "usecase": DiagramType(
        backend="plantuml",
        description="Shows system functionality and actors who interact with it",
        formats=LANGUAGE_OUTPUT_SUPPORT["plantuml"],
    ),
    "state": DiagramType(
        backend="plantuml",
        description="Shows states of an object during its lifecycle",
        formats=LANGUAGE_OUTPUT_SUPPORT["plantuml"],
    ),
    "component": DiagramType(
        backend="plantuml",
        description="Shows components and dependencies",
        formats=LANGUAGE_OUTPUT_SUPPORT["plantuml"],
    ),
    "deployment": DiagramType(
        backend="plantuml",
        description="Shows physical architecture of a system",
        formats=LANGUAGE_OUTPUT_SUPPORT["plantuml"],
    ),
    "object": DiagramType(
        backend="plantuml",
        description="Shows instances of classes and their relationships",
        formats=LANGUAGE_OUTPUT_SUPPORT["plantuml"],
    ),
    # Other diagram types
    "mermaid": DiagramType(
        backend="mermaid",
        description="A JavaScript based diagramming and charting tool",
        formats=LANGUAGE_OUTPUT_SUPPORT["mermaid"],
    ),
    "d2": DiagramType(
        backend="d2",
        description="A modern diagram scripting language",
        formats=LANGUAGE_OUTPUT_SUPPORT["d2"],
    ),
    "graphviz": DiagramType(
        backend="graphviz",
        description="Graph visualization software",
        formats=LANGUAGE_OUTPUT_SUPPORT["graphviz"],
    ),
    "erd": DiagramType(
        backend="erd",
        description="Entity-relationship diagrams",
        formats=LANGUAGE_OUTPUT_SUPPORT["erd"],
    ),
    "blockdiag": DiagramType(
        backend="blockdiag",
        description="Simple block diagram images",
        formats=LANGUAGE_OUTPUT_SUPPORT["blockdiag"],
    ),
    "packetdiag": DiagramType(
        backend="packetdiag",
        description="Network packet layout diagrams",
        formats=LANGUAGE_OUTPUT_SUPPORT["packetdiag"],
    ),
    "bpmn": DiagramType(
        backend="bpmn",
        description="Business Process Model and Notation",
        formats=LANGUAGE_OUTPUT_SUPPORT["bpmn"],
    ),
    "c4plantuml": DiagramType(
        backend="c4plantuml",
        description="C4 model diagrams using PlantUML",
        formats=LANGUAGE_OUTPUT_SUPPORT["c4plantuml"],
    ),
    # Kroki backend types (https://kroki.io/)
    "actdiag": DiagramType(
        backend="actdiag",
        description="Activity and workflow diagrams (blockdiag family)",
        formats=LANGUAGE_OUTPUT_SUPPORT["actdiag"],
    ),
    "bytefield": DiagramType(
        backend="bytefield",
        description="Binary protocol and byte layout diagrams",
        formats=LANGUAGE_OUTPUT_SUPPORT["bytefield"],
    ),
    "seqdiag": DiagramType(
        backend="seqdiag",
        description="Sequence diagrams (blockdiag family)",
        formats=LANGUAGE_OUTPUT_SUPPORT["seqdiag"],
    ),
    "nwdiag": DiagramType(
        backend="nwdiag",
        description="Network diagrams (blockdiag family)",
        formats=LANGUAGE_OUTPUT_SUPPORT["nwdiag"],
    ),
    "rackdiag": DiagramType(
        backend="rackdiag",
        description="Rack and server layout diagrams (blockdiag family)",
        formats=LANGUAGE_OUTPUT_SUPPORT["rackdiag"],
    ),
    "dbml": DiagramType(
        backend="dbml",
        description="Database Markup Language schema diagrams",
        formats=LANGUAGE_OUTPUT_SUPPORT["dbml"],
    ),
    "ditaa": DiagramType(
        backend="ditaa",
        description="Diagrams Through ASCII Art",
        formats=LANGUAGE_OUTPUT_SUPPORT["ditaa"],
    ),
    "excalidraw": DiagramType(
        backend="excalidraw",
        description="Excalidraw whiteboard-style diagrams",
        formats=LANGUAGE_OUTPUT_SUPPORT["excalidraw"],
    ),
    "nomnoml": DiagramType(
        backend="nomnoml",
        description="UML-style diagrams from shorthand syntax",
        formats=LANGUAGE_OUTPUT_SUPPORT["nomnoml"],
    ),
    "pikchr": DiagramType(
        backend="pikchr",
        description="Pikchr diagram scripting language",
        formats=LANGUAGE_OUTPUT_SUPPORT["pikchr"],
    ),
    "plantuml": DiagramType(
        backend="plantuml",
        description="PlantUML raw diagram source (all diagram kinds)",
        formats=LANGUAGE_OUTPUT_SUPPORT["plantuml"],
    ),
    "structurizr": DiagramType(
        backend="structurizr",
        description="Structurizr C4 and architecture DSL",
        formats=LANGUAGE_OUTPUT_SUPPORT["structurizr"],
    ),
    "svgbob": DiagramType(
        backend="svgbob",
        description="ASCII art to SVG diagrams",
        formats=LANGUAGE_OUTPUT_SUPPORT["svgbob"],
    ),
    "symbolator": DiagramType(
        backend="symbolator",
        description="Digital logic and schematic symbols",
        formats=LANGUAGE_OUTPUT_SUPPORT["symbolator"],
    ),
    "tikz": DiagramType(
        backend="tikz",
        description="TikZ/PGF graphics (LaTeX)",
        formats=LANGUAGE_OUTPUT_SUPPORT["tikz"],
    ),
    "vega": DiagramType(
        backend="vega",
        description="Vega visualization grammar",
        formats=LANGUAGE_OUTPUT_SUPPORT["vega"],
    ),
    "vegalite": DiagramType(
        backend="vegalite",
        description="Vega-Lite visualization grammar",
        formats=LANGUAGE_OUTPUT_SUPPORT["vegalite"],
    ),
    "wavedrom": DiagramType(
        backend="wavedrom",
        description="Waveform and digital timing diagrams",
        formats=LANGUAGE_OUTPUT_SUPPORT["wavedrom"],
    ),
    "wireviz": DiagramType(
        backend="wireviz",
        description="Cable and wiring diagrams",
        formats=LANGUAGE_OUTPUT_SUPPORT["wireviz"],
    ),
}

# Create MCP settings
MCP_SETTINGS = MCPSettings(diagram_types=DIAGRAM_TYPES)

# Override servers when local backends are requested
_use_local_kroki = _env_use_local_kroki()
_use_local_plantuml = os.environ.get("USE_LOCAL_PLANTUML", "false").lower() in (
    "true",
    "1",
    "yes",
)
if _use_local_kroki:
    MCP_SETTINGS.kroki_server = os.environ.get("KROKI_SERVER", "http://kroki:8000")
if _use_local_plantuml:
    MCP_SETTINGS.plantuml_server = os.environ.get(
        "PLANTUML_SERVER", "http://plantuml-server:8080"
    )
