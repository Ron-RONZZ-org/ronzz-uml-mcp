"""
MCP tools for diagram generation using the decorator pattern.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.types import ImageContent, TextContent
from pydantic import ValidationError

from ..core.config import MCP_SETTINGS
from ..core.diagram_catalog import get_diagram_types_dict
from ..core.diagram_service import DiagramRequest, generate_from_request
from ..core.diagram_validation import validate_uml_inputs
from .schemas import GenerateUMLInput
from .tool_decorator import get_tool_registry, mcp_tool, register_tools_with_server

logger = logging.getLogger(__name__)

ANNOTATIONS_DIAGRAM = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

ANNOTATIONS_VALIDATE = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

ANNOTATIONS_LIST = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


@mcp_tool(
    description=(
        "List supported diagram types with Kroki backend, description, and formats "
        "(same data as uml://types resource). Use when the client cannot read resources."
    ),
    category="uml",
    example="list_diagram_types()",
    annotations=ANNOTATIONS_LIST,
)
def list_diagram_types() -> Dict[str, Any]:
    """Return the uml://types payload as a structured dict."""
    logger.info("Called list_diagram_types tool")
    return get_diagram_types_dict()


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = err.get("loc", ())
        name = ".".join(str(x) for x in loc) if loc else "input"
        parts.append(f"{name}: {err.get('msg', '')}")
    return "; ".join(parts)


@mcp_tool(
    description=(
        "Generate multiple diagrams in one call. Each item is like generate_uml "
        "(diagram_type, code, output_format?, theme?, scale?). Optional shared output_dir "
        "for all items. Returns a list of per-index results or errors."
    ),
    category="uml",
    example=(
        "generate_uml_batch([{'diagram_type': 'mermaid', 'code': 'graph TD; A-->B;'}], "
        "output_dir=None)"
    ),
    annotations=ANNOTATIONS_DIAGRAM,
)
def generate_uml_batch(
    items: List[Dict[str, Any]],
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Render multiple diagrams; failures are collected per index without stopping the batch."""
    logger.info(
        "Called generate_uml_batch: count=%s, output_dir=%s",
        len(items) if items else 0,
        output_dir,
    )
    if not items:
        return {
            "error": "items must not be empty",
            "results": [],
        }
    max_n = MCP_SETTINGS.batch_max_items
    if len(items) > max_n:
        return {
            "error": f"At most {max_n} items allowed (MCP_BATCH_MAX_ITEMS).",
            "results": [],
        }
    results: List[Dict[str, Any]] = []
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            results.append(
                {
                    "index": i,
                    "error": f"items[{i}] must be an object with diagram_type and code",
                }
            )
            continue
        try:
            validated = GenerateUMLInput.model_validate(
                {**raw, "output_dir": output_dir}
            )
        except ValidationError as e:
            results.append({"index": i, "error": _format_validation_error(e)})
            continue
        out = generate_from_request(
            DiagramRequest(
                diagram_type=validated.diagram_type,
                code=validated.code,
                output_dir=validated.output_dir,
                output_format=validated.output_format,
                theme=validated.theme,
                scale=validated.scale,
            )
        )
        results.append({"index": i, **out})
    return {"results": results}


@mcp_tool(
    description="Generate any UML or diagram by type (class, sequence, mermaid, d2, etc.)",
    category="uml",
    example=(
        "generate_uml('mermaid', 'graph TD; A-->B;')  # URL/base64 only; "
        "or generate_uml('class', code, './output') to save"
    ),
    annotations=ANNOTATIONS_DIAGRAM,
)
def generate_uml(
    diagram_type: str,
    code: str,
    output_dir: Optional[str] = None,
    output_format: str = "svg",
    theme: Optional[str] = None,
    scale: float = 1.0,
) -> Dict[str, Any]:
    """Generate a diagram using the specified diagram type."""
    logger.info(
        "Called generate_uml tool: type=%s, code length=%s",
        diagram_type,
        len(code),
    )
    return generate_from_request(
        DiagramRequest(
            diagram_type=diagram_type,
            code=code,
            output_dir=output_dir,
            output_format=output_format,
            theme=theme,
            scale=scale,
        )
    )


@mcp_tool(
    description=(
        "Validate a diagram by compiling it on the renderer. Runs local structure "
        "checks first, then a real Kroki compile, so `valid` reflects what the "
        "renderer accepts (a TikZ figure that does not compile is reported invalid). "
        "Returns errors, suggestions, compiled, and compile_ms."
    ),
    category="uml",
    example="validate_uml('class', '@startuml\\nclass A\\n@enduml', 'svg')",
    annotations=ANNOTATIONS_VALIDATE,
)
def validate_uml(
    diagram_type: str,
    code: str,
    output_format: str = "svg",
    strict: bool = False,
) -> Dict[str, Any]:
    """Compile the diagram on Kroki and report renderer-accepted validity."""
    logger.info(
        "Called validate_uml tool: type=%s, code length=%s, strict=%s",
        diagram_type,
        len(code or ""),
        strict,
    )
    return validate_uml_inputs(diagram_type, code, output_format, strict=strict)


_IMAGE_MIME_BY_FORMAT = {
    "svg": "image/svg+xml",
    "png": "image/png",
    "jpeg": "image/jpeg",
}


def _summary_text(payload: Dict[str, Any]) -> TextContent:
    return TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))


@mcp_tool(
    description=(
        "Render a diagram and return it as an MCP image plus the server-side file path. "
        "Use this instead of generate_uml when the figure should appear in chat or be "
        "uploaded: the image needs no base64 copying in the conversation, and local_path "
        "is a file the wiki writer can upload directly."
    ),
    category="uml",
    example=(
        "render_diagram('tikz', r'\\begin{tikzpicture}\\draw (0,0) -- (1,1);"
        "\\end{tikzpicture}')"
    ),
    annotations=ANNOTATIONS_DIAGRAM,
)
def render_diagram(
    diagram_type: str,
    code: str,
    output_format: str = "svg",
    theme: Optional[str] = None,
    scale: float = 1.0,
) -> List[Any]:
    """Render to ``MCP_SETTINGS.output_dir``; return [text summary, image]."""
    logger.info(
        "Called render_diagram: type=%s, format=%s, code length=%s",
        diagram_type,
        output_format,
        len(code or ""),
    )
    out = generate_from_request(
        DiagramRequest(
            diagram_type=diagram_type,
            code=code,
            output_dir=MCP_SETTINGS.output_dir,
            output_format=output_format,
            theme=theme,
            scale=scale,
        )
    )

    if out.get("error"):
        return [
            _summary_text(
                {
                    "error": out["error"],
                    "diagram_type": diagram_type,
                    "local_path": None,
                }
            )
        ]

    local_path = out.get("local_path")
    if not local_path:
        return [
            _summary_text(
                {
                    "error": (
                        "The renderer produced no file. Set MCP_OUTPUT_DIR (or "
                        "UML_MCP_OUTPUT_DIR) to a writable directory."
                    ),
                    "diagram_type": diagram_type,
                    "local_path": None,
                }
            )
        ]

    try:
        content = Path(local_path).read_bytes()
    except OSError as exc:
        return [
            _summary_text(
                {
                    "error": f"Rendered, but the file could not be read back: {exc}",
                    "diagram_type": diagram_type,
                    "local_path": local_path,
                }
            )
        ]

    mime_type = _IMAGE_MIME_BY_FORMAT.get(output_format.lower())
    summary = {
        "diagram_type": diagram_type,
        "output_format": output_format,
        "local_path": local_path,
        "bytes": len(content),
        "mime_type": mime_type or out.get("mime_type"),
        "render_ms": out.get("render_ms"),
    }

    if mime_type is None:
        return [_summary_text(summary)]

    import base64

    return [
        _summary_text(summary),
        ImageContent(
            type="image",
            data=base64.b64encode(content).decode("ascii"),
            mimeType=mime_type,
        ),
    ]


def register_diagram_tools(server: Any) -> List[str]:
    """Register all diagram generation tools with the MCP server."""
    logger.info("Registering diagram tools")

    registered_tools = register_tools_with_server(server)
    MCP_SETTINGS.tools = registered_tools

    logger.info("Registered %s diagram tools successfully", len(registered_tools))
    logger.debug("Registered tools: %s", registered_tools)

    return registered_tools


def get_tool_info() -> Dict[str, Dict[str, Any]]:
    """Get information about all registered tools."""
    return get_tool_registry()
