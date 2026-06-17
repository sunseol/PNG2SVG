# SlideRefine Importer

Local Figma plugin for importing `figma-import.json` produced by `vectorize_ppt_svg.py`.

## What it does

- Reads one `figma-import.json` file.
- Imports each slide using either:
  - `recommended`
  - `vector`
  - `hybrid-safe`
- In `vector`, creates a background raster, movable visual groups, and editable text layers.
- In `hybrid-safe`, preserves the visible source design with a locked raster base.
- Optionally creates hidden editable text drafts and hidden vector candidates.

## Install in Figma

1. Open Figma desktop.
2. Go to `Plugins` -> `Development` -> `Import plugin from manifest...`
3. Select [manifest.json](/C:/Users/keduall/Documents/Codex/2026-04-23-gpt-ppt-svg/figma-plugin/slide-refine-importer/manifest.json)

## Use

1. Generate a SlideRefine package.
2. In the package folder, select `figma-import.json`.
3. Open the plugin and load that file.
4. Choose an import mode.
5. Import the slides into the current Figma page.

## Current behavior

- `recommended` chooses vector import for clean infographic slides.
- `recommended` chooses hybrid-safe import for poster/photo-heavy slides.
- Vector mode groups panel rasters with their editable text, so panels can be moved as units.
- Small, corrected, or low-confidence text layers are visible editable review layers, and the source raster text is removed from movable visual groups.
- Photo-like regions are imported as separate image layers when the exporter can isolate them.
- Hybrid-safe mode does not visually alter the design because it uses the exported raster reference as the visible base.
