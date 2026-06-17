# Product Direction

## Positioning

SlideRefine is a Figma-first post-processing tool for GPT-generated slide images.

The product does not try to perfectly recreate a final PPT. It reconstructs a slide into:

- editable SVG vector regions
- text candidate guides
- handoff metadata for Figma cleanup

## Core workflow

1. Upload a slide image, a folder of slides, or an image-backed PPTX.
2. Separate probable text blocks from non-text visual regions.
3. Vectorize the non-text regions into grouped SVG paths.
4. Export a package that Figma can import and refine.

## MVP boundary

The current MVP is intentionally narrow:

- prioritize editability over exact fidelity
- default to excluding text from traced vectors
- focus on clean shapes, cards, icons, and layout blocks
- leave final text rebuilding and polish to Figma

## Output contract

Each bundle should contain:

- `slides/*.svg`: editable vectors
- `references/*.png`: visual references
- `manifest.json`: layer counts, slide dimensions, text candidate boxes
- `handoff.md`: usage instructions

## Near-term roadmap

1. OCR-assisted text reconstruction
2. icon and repeated-asset extraction
3. optional Figma plugin importer
4. hybrid PDF/PPTX export as secondary handoff routes
