# ronzz.ORG fork notes

This fork (`Ron-RONZZ-org/ronzz-uml-mcp`) carries four changes used by
Open WebUI on ronzz-linux-server-2. Upstream: `antoinebou12/uml-mcp` (MIT).

## Changes

1. **`render_diagram(diagram_type, code, …)`** — renders through the same
   pipeline as `generate_uml`, always writes the result under
   `MCP_OUTPUT_DIR` (default `./output`), and returns
   `[TextContent(json summary), ImageContent]`. The summary carries
   `local_path`, `bytes`, `mime_type` and `render_ms`; the bytes are **not**
   echoed as base64, so a client can display the figure without paying
   output tokens for it. `local_path` is what the MediaWiki writer's
   `upload-file` needs.

2. **Companion availability probe** — Kroki serves `mermaid`, `bpmn` and
   `excalidraw` from optional companion containers. When one is absent the
   gateway answers `503`; the probe records that once (cached, 60 s retry
   after a connection failure) and withholds the type from
   `list_diagram_types` and from the `diagram_type` validator. Set
   `MCP_DIAGRAM_PROBE=false` to disable.

3. **`validate_uml` compiles** — structural checks stay as a cheap pre-pass,
   but a type that passes them is compiled on Kroki before being called
   valid. `compiled` and `compile_ms` are reported. This removes the old
   `valid: true` for broken TikZ (there were no TikZ checks at all).

4. **TikZ preamble normalisation** — `wrap_tikz_standalone` hoists *any*
   `\usepackage`, `\usetikzlibrary` and `\pgfplotsset` line out of the
   snippet body, and `prepare_diagram_code` strips the `\documentclass` line
   before sending to Kroki (Kroki replaces a class line and drops its
   options; it prepends its own when absent). A snippet may therefore request
   `chemfig`, `mhchem`, `circuitikz`, … without writing a full document.

## Image

`ghcr.io/ron-ronzz-org/ronzz-uml-mcp:v1.3.1-ronzz1` (linux/arm64), built by
`.github/workflows/ronzz-ci.yml` on pushes to `master`.

Deployment runbook: `RonzzIT:Runbook/Kroki` on wikibase.ronzz.org.
