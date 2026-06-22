# Product Direction

## Positioning

SlideRefine is a local-first conversion and editing tool for slide images and image-backed PPTX files. It creates a neutral `.srf` scene document that can be edited by people through CLI/web workflows and by AI through CLI JSON or MCP tools.

The product no longer depends on Figma as the core editing surface. Figma import/export can exist as an optional integration, but core conversion, editing, validation, rendering, and export must work without Figma.

## Core Workflow

1. Convert a slide image, folder of slide images, or image-backed PPTX into `.srf`.
2. Preserve source assets, editable nodes, provenance, diagnostics, and stable node IDs.
3. Inspect/query the document through JSON contracts.
4. Apply revision-checked document operations.
5. Render previews and export SVG/PNG.
6. Use the same application services from CLI, local HTTP editor, and MCP.

## MVP Boundary

The MVP is intentionally focused on a reliable standalone contract:

- deterministic `.srf` package roundtrip
- strict schema/domain validation
- stable CLI JSON envelope and exit codes
- local web editing for selection, transform, style, layer operations, undo/redo, save, and export
- MCP feedback loop for convert/query/apply/render/export
- malicious input, performance, visual, browser, and CI evidence gates

The MVP does not attempt full Office semantics, advanced Bezier point editing, multi-user collaboration, pure browser-side OCR/vectorization, or perfect font reconstruction.

## Output Contract

Primary outputs:

- `.srf`: editable scene document with content-addressed assets
- SVG: export format
- PNG: rendered preview/export
- JSON: machine-readable command result envelope
- evidence bundle: release gate artifacts tied to a Git SHA

Legacy Figma handoff output remains available only through the retained compatibility script and should not be treated as the core product contract.

## Release Definition

A release candidate is acceptable only when the same Git SHA has:

- Windows and Linux CI passing
- fixed fixture CLI roundtrip passing
- browser editor journey passing
- MCP standard feedback loop passing
- security and performance gates passing
- retained visual/browser/MCP/security/performance evidence
- `release-verdict.json.overall == "pass"`

## Roadmap

1. Harden the standalone CLI, scene document, local editor, and MCP contracts.
2. Expand fixture coverage and visual/text quality metrics.
3. Improve OCR and text reconstruction behind the existing document model.
4. Add optional Figma compatibility as an integration after the standalone release gates remain green.
