# SlideRefine

SlideRefine converts PNG/JPG/WEBP/BMP/TIFF images, image folders, and image-backed PPTX files into an editable `.srf` scene document. The same document and operation contract is used by the CLI, local web editor, and MCP tools.

Figma support is optional compatibility work. The core workflow does not require Figma.

## Install

```powershell
python -m pip install -e ".[dev]"
```

Installed command:

```powershell
sliderefine --help
```

## Core CLI

```powershell
sliderefine doctor --json
sliderefine capabilities --json

sliderefine convert tests/fixtures/shape-heavy.png -o .artifacts/shape-heavy.srf --deterministic --json
sliderefine validate .artifacts/shape-heavy.srf --json
sliderefine inspect .artifacts/shape-heavy.srf --json
sliderefine query .artifacts/shape-heavy.srf --type text --json

sliderefine apply .artifacts/shape-heavy.srf --operations tests/fixtures/ops/edit-title.json -o .artifacts/edited.srf --json
sliderefine render .artifacts/edited.srf --slide slide-01 -o .artifacts/preview.png --json
sliderefine export .artifacts/edited.srf --format svg -o .artifacts/result.svg --json
sliderefine export .artifacts/edited.srf --format png -o .artifacts/result.png --json
```

Supported inputs:

- single image: PNG, JPG, JPEG, WEBP, BMP, TIFF
- directory of slide images
- image-backed PPTX
- existing `.srf` documents for validate/inspect/query/apply/render/export

## Local Web Editor

```powershell
sliderefine edit tests/fixtures/shape-heavy.png
```

For automated verification:

```powershell
sliderefine edit tests/fixtures/shape-heavy.png --headless-e2e
```

The local editor binds to loopback, uses a random token, checks exact Origin for API calls, saves through the `.srf` document contract, and exports SVG/PNG through the same application services as the CLI.

## MCP

```powershell
sliderefine mcp --transport stdio
```

Initial tools:

- `convert_image`
- `inspect_document`
- `query_nodes`
- `apply_operations`
- `render_preview`
- `export_document`

The MCP adapter enforces workspace roots, uses revision-checked operation transactions, supports dry-run, and returns structured JSON-RPC errors.

## Machine Output

Commands that support `--json` write the final JSON envelope to stdout. Logs and diagnostics belong on stderr. Errors use the same envelope shape with a stable `code`.

Common exit codes:

- `0`: success
- `1`: unexpected command/runtime error
- `7`: conversion timeout
- `64`: command usage error
- `65`: validation/data error
- `66`: input file is unavailable
- `70`: configured source size limit exceeded

## Scene Document

`.srf` is a deterministic ZIP container:

```text
document.srf
├─ mimetype
├─ manifest.json
├─ document.json
└─ assets/
```

Document JSON contains node maps, slide child ordering, provenance, diagnostics, and operation logs. Binary assets are content-addressed files in the archive, not Base64 fields in document JSON.

## Verification

Release evidence is generated per Git SHA:

```powershell
py -3 -m tools.generate_evidence --evidence evidence/<git-sha>
```

The final gate is:

```powershell
py -3 -m tools.release_gate --evidence evidence/<git-sha> --output evidence/<git-sha>/release-verdict.json
```

Completion requires `release-verdict.json.overall == "pass"` for the same source SHA, plus passing Windows and Linux CI artifacts.

## Legacy Script

The original Figma handoff script is retained as a compatibility wrapper:

```powershell
python .\vectorize_ppt_svg.py .\slide.png --mode svg --output .\slide.svg
```
