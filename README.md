# SlideRefine Prototype

This workspace now targets a Figma-first workflow:

`GPT slide image -> structured SVG package -> Figma editing`

Instead of treating PPT as the primary output, the current MVP produces a handoff bundle that Figma can import as editable vector layers.

## What the prototype does

- Accepts a single slide image, a directory of slide images, or an image-based `.pptx`.
- Detects probable text regions and, by default, excludes them from vector tracing.
- Uses Windows OCR when available to recover real text blocks before falling back to heuristic text boxes.
- Applies light OCR correction for common small Korean label errors before creating editable text drafts.
- Converts non-text regions into grouped SVG paths.
- Builds movable raster visual regions for panels, icon blocks, and photo-like main images.
- Fills the vacated area behind movable regions with a low-frequency paper-tone background to reduce eraser artifacts.
- Emits a Figma handoff package with:
  - `slides/*.svg`
  - `slides/*.hybrid.svg`
  - `references/*.png`
  - `manifest.json`
  - `figma-import.json`
  - `handoff.md`

## Why this direction is better

- SVG imports into Figma as editable vector layers.
- Text is easier to rebuild manually in Figma than to clean up thousands of traced glyph paths.
- The product can optimize for editability and structure instead of pixel-perfect reconstruction.

## CLI

Main entrypoint:

- `vectorize_ppt_svg.py`

### Package mode

Single image:

```powershell
python .\vectorize_ppt_svg.py .\slide.png
```

Directory of slide images:

```powershell
python .\vectorize_ppt_svg.py .\slides-dir
```

PPTX:

```powershell
python .\vectorize_ppt_svg.py .\deck.pptx --output .\out\deck-figma
```

### SVG-only mode

Single SVG:

```powershell
python .\vectorize_ppt_svg.py .\slide.png --mode svg --output .\slide.svg
```

Per-slide SVG directory:

```powershell
python .\vectorize_ppt_svg.py .\deck.pptx --mode svg --output .\out-svg
```

## Important options

- `--colors 10..16`: fewer colors means cleaner editable layers.
- `--simplify 1.0..2.0`: reduces point count.
- `--min-area 16..40`: removes tiny noise.
- `--merge-distance 12..24`: merges antialias colors.
- `--text-handling exclude|vectorize`: defaults to `exclude`.
- `--text-padding 4..10`: expands detected text exclusion zones.
- `--skip-text-detection`: useful when the slide has almost no text.
- `--ocr-engine auto|windows|none`: controls OCR-backed text extraction.

## Package layout

Example:

```text
deck-figma-package/
  figma-import.json
  handoff.md
  manifest.json
  references/
    slide-01.png
  slides/
    slide-01.svg
    slide-01.hybrid.svg
```

## Figma plugin

Local importer plugin:

- [figma-plugin/slide-refine-importer/manifest.json](/C:/Users/keduall/Documents/Codex/2026-04-23-gpt-ppt-svg/figma-plugin/slide-refine-importer/manifest.json)

The plugin loads one `figma-import.json` file and imports slides using either:

- `recommended`
- `vector`
- `hybrid-safe`

`vector` / `Editable Text` preserves visual detail as movable raster groups and places OCR-backed editable text on top.

`hybrid-safe` preserves the visible design by placing the exported raster reference as the locked base layer and adding editable guides on top.

## Current MVP assumptions

- Best on text-light, shape-heavy slides.
- Text is exported as OCR-backed editable draft layers when using the importer plugin.
- When Windows OCR is available, recognized and lightly corrected text strings are included in `manifest.json` and as `data-text` attributes on SVG guides.
- Text candidates include a `replacement_policy`: high-confidence text uses `visible_replace`, while small or corrected text uses `visible_review`; both are editable text layers and are removed from raster assets.
- `figma-import.json` includes `visual_regions` so panels and photo-like regions can be moved independently in Figma.
- `*.hybrid.svg` preserves the original visual design by embedding the source raster and overlaying editable text guides.
- Complex gradients, glow, photos, and dense illustrations still create heavy vectors.
- PPTX support is aimed at image-backed slides and picture placement, not full Office semantics.

## Next logical step

The next upgrade is tighter typography reconstruction:

1. Group related text lines into stronger text styles.
2. Improve font, weight, line-height, and alignment matching.
3. Add a user-editable OCR correction dictionary.
4. Keep expanding movable visual region detection for more slide layouts.
