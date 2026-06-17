const UI_WIDTH = 420;
const UI_HEIGHT = 620;
const DEFAULT_GAP = 160;
const GUIDE_COLOR = { r: 1, g: 0.42, b: 0 };
const GUIDE_FILL_COLOR = { r: 1, g: 0.42, b: 0 };
const GUIDE_FILL_OPACITY = 0.08;
const DEFAULT_TEXT_FONT = { family: "Arial", style: "Regular" };
const loadedFonts = {};

figma.showUI(__html__, {
  width: UI_WIDTH,
  height: UI_HEIGHT,
  themeColors: true,
  title: "SlideRefine Importer",
});

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function encodePluginData(node, key, value) {
  if (value === undefined || value === null) return;
  try {
    node.setPluginData(key, String(value));
  } catch (_) {
  }
}

function getAnalysisProfile(slide) {
  if (!slide || !slide.analysis) {
    return null;
  }
  return slide.analysis.profile || null;
}

async function ensureFontLoaded() {
  try {
    await figma.loadFontAsync(DEFAULT_TEXT_FONT);
    return true;
  } catch (_) {
    return false;
  }
}

function hasHangul(text) {
  return /[\u3131-\u318E\uAC00-\uD7A3]/.test(text || "");
}

function hexToRgb(hex) {
  if (!hex || typeof hex !== "string" || hex.length !== 7) {
    return { r: 0, g: 0, b: 0 };
  }
  return {
    r: parseInt(hex.slice(1, 3), 16) / 255,
    g: parseInt(hex.slice(3, 5), 16) / 255,
    b: parseInt(hex.slice(5, 7), 16) / 255,
  };
}

function isEditableTextCandidate(candidate) {
  if (!candidate || !candidate.text) {
    return false;
  }
  return (
    candidate.text_status === "usable" ||
    candidate.text_status === "usable_corrected" ||
    candidate.text_status === "needs_review"
  );
}

function isVisibleReplacementCandidate(candidate) {
  return (
    candidate &&
    (
      candidate.replacement_policy === "visible_replace" ||
      candidate.replacement_policy === "visible_review"
    )
  );
}

function getTextCandidateMap(slide) {
  var map = {};
  var candidates = slide.text_candidates || [];
  for (var i = 0; i < candidates.length; i += 1) {
    map[candidates[i].id] = candidates[i];
  }
  return map;
}

function getVisualRegionTextIds(slide) {
  var ids = {};
  var regions = slide.visual_regions || [];
  for (var i = 0; i < regions.length; i += 1) {
    var regionIds = regions[i].text_candidate_ids || [];
    for (var j = 0; j < regionIds.length; j += 1) {
      ids[regionIds[j]] = true;
    }
  }
  return ids;
}

async function loadFontWithFallback(text, fontStyle) {
  var style = fontStyle || "Regular";
  var families = hasHangul(text)
    ? ["Malgun Gothic", "Noto Sans KR", "Arial", "Inter"]
    : ["Inter", "Arial", "Helvetica"];

  for (var i = 0; i < families.length; i += 1) {
    var key = families[i] + "::" + style;
    if (loadedFonts[key]) {
      return { family: families[i], style: style };
    }
    try {
      await figma.loadFontAsync({ family: families[i], style: style });
      loadedFonts[key] = true;
      return { family: families[i], style: style };
    } catch (_) {
    }
  }

  if (style !== "Regular") {
    return loadFontWithFallback(text, "Regular");
  }

  return null;
}

function buildGuideRect(candidate) {
  const rect = figma.createRectangle();
  rect.name = candidate.id;
  rect.x = candidate.x;
  rect.y = candidate.y;
  rect.resize(candidate.width, candidate.height);
  rect.fills = [{ type: "SOLID", color: GUIDE_FILL_COLOR, opacity: GUIDE_FILL_OPACITY }];
  rect.strokes = [{ type: "SOLID", color: GUIDE_COLOR }];
  rect.strokeWeight = 2;
  try {
    rect.dashPattern = [8, 4];
  } catch (_) {
  }
  encodePluginData(rect, "slide-refine:id", candidate.id);
  encodePluginData(rect, "slide-refine:source", candidate.source);
  encodePluginData(rect, "slide-refine:text", candidate.text);
  encodePluginData(rect, "slide-refine:text_status", candidate.text_status);
  return rect;
}

function buildReferenceRect(slide) {
  const imageBytes = figma.base64Decode(slide.assets.reference_png_base64);
  const image = figma.createImage(imageBytes);
  const rect = figma.createRectangle();
  rect.name = "Reference Raster";
  rect.resize(slide.canvas.width, slide.canvas.height);
  rect.fills = [
    {
      type: "IMAGE",
      scaleMode: "FILL",
      imageHash: image.hash,
    },
  ];
  rect.locked = true;
  return rect;
}

function buildNonTextRasterRect(slide) {
  var imageBytes = figma.base64Decode(slide.assets.non_text_raster_png_base64);
  var image = figma.createImage(imageBytes);
  var rect = figma.createRectangle();
  rect.name = "Non-text Raster Base";
  rect.resize(slide.canvas.width, slide.canvas.height);
  rect.fills = [
    {
      type: "IMAGE",
      scaleMode: "FILL",
      imageHash: image.hash,
    },
  ];
  return rect;
}

function buildBackgroundRasterRect(slide) {
  var base64 = slide.assets.background_raster_png_base64 || slide.assets.non_text_raster_png_base64;
  var imageBytes = figma.base64Decode(base64);
  var image = figma.createImage(imageBytes);
  var rect = figma.createRectangle();
  rect.name = "Background Raster";
  rect.resize(slide.canvas.width, slide.canvas.height);
  rect.fills = [
    {
      type: "IMAGE",
      scaleMode: "FILL",
      imageHash: image.hash,
    },
  ];
  return rect;
}

function buildRasterRegionRect(region) {
  var imageBytes = figma.base64Decode(region.image_base64);
  var image = figma.createImage(imageBytes);
  var rect = figma.createRectangle();
  rect.name = region.id;
  rect.x = region.x;
  rect.y = region.y;
  rect.resize(region.width, region.height);
  rect.fills = [
    {
      type: "IMAGE",
      scaleMode: "FILL",
      imageHash: image.hash,
    },
  ];
  encodePluginData(rect, "slide-refine:id", region.id);
  encodePluginData(rect, "slide-refine:mean_error", region.mean_error);
  return rect;
}

function buildRasterRegionsFrame(slide) {
  var regions = slide.raster_regions || [];
  if (!regions.length) {
    return null;
  }

  var frame = figma.createFrame();
  frame.name = "Raster Regions";
  frame.resize(slide.canvas.width, slide.canvas.height);
  frame.fills = [];
  frame.strokes = [];
  frame.clipsContent = false;

  for (var i = 0; i < regions.length; i += 1) {
    frame.appendChild(buildRasterRegionRect(regions[i]));
  }
  return frame;
}

function buildVisualRegionImageRect(region) {
  var imageBytes = figma.base64Decode(region.image_base64);
  var image = figma.createImage(imageBytes);
  var rect = figma.createRectangle();
  rect.name = region.kind ? `${region.id} ${region.kind}` : region.id;
  rect.x = region.x;
  rect.y = region.y;
  rect.resize(region.width, region.height);
  rect.fills = [
    {
      type: "IMAGE",
      scaleMode: "FILL",
      imageHash: image.hash,
    },
  ];
  encodePluginData(rect, "slide-refine:id", region.id);
  encodePluginData(rect, "slide-refine:kind", region.kind);
  return rect;
}

async function appendMovableVisualRegions(parent, slide) {
  var regions = slide.visual_regions || [];
  if (!regions.length) {
    return {};
  }

  var candidateMap = getTextCandidateMap(slide);
  var assignedIds = {};
  for (var i = 0; i < regions.length; i += 1) {
    var region = regions[i];
    if (!region.image_base64) {
      continue;
    }

    var nodes = [];
    var imageRect = buildVisualRegionImageRect(region);
    parent.appendChild(imageRect);
    nodes.push(imageRect);

    var textIds = region.text_candidate_ids || [];
    for (var j = 0; j < textIds.length; j += 1) {
      var candidate = candidateMap[textIds[j]];
      if (!isEditableTextCandidate(candidate)) {
        continue;
      }
      var textNodes = await createTextNodesForCandidate(candidate, 0, 0);
      if (textNodes.length) {
        assignedIds[candidate.id] = true;
      }
      for (var k = 0; k < textNodes.length; k += 1) {
        parent.appendChild(textNodes[k]);
        nodes.push(textNodes[k]);
      }
    }

    if (nodes.length > 1) {
      var group = figma.group(nodes, parent);
      group.name = region.kind ? `${region.kind} ${region.id}` : region.id;
      encodePluginData(group, "slide-refine:id", region.id);
      encodePluginData(group, "slide-refine:kind", region.kind);
    }
  }

  return assignedIds;
}

function buildGuideFrame(slide) {
  const frame = figma.createFrame();
  frame.name = "Text Guides";
  frame.resize(slide.canvas.width, slide.canvas.height);
  frame.fills = [];
  frame.strokes = [];
  frame.clipsContent = false;
  for (const candidate of slide.text_candidates || []) {
    frame.appendChild(buildGuideRect(candidate));
  }
  return frame;
}

function buildGuideFrameWithVisibility(slide, isVisible) {
  var frame = buildGuideFrame(slide);
  frame.visible = isVisible;
  return frame;
}

async function createTextNodesForCandidate(candidate, offsetX, offsetY) {
  var nodes = [];
  var segments = candidate.text_segments && candidate.text_segments.length
    ? candidate.text_segments
    : [
        {
          text: candidate.text,
          x: candidate.x,
          y: candidate.y,
          width: candidate.width,
          height: candidate.height,
          style: candidate.text_style || null,
        },
      ];

  for (var i = 0; i < segments.length; i += 1) {
    var segment = segments[i];
    var style = segment.style || candidate.text_style || {};
    var font = await loadFontWithFallback(segment.text, style.font_style || "Regular");
    if (!font) {
      continue;
    }

    var text = figma.createText();
    text.name = i === 0 ? `${candidate.id} text` : `${candidate.id} segment ${i + 1}`;
    text.fontName = font;
    text.characters = segment.text;
    text.fontSize = style.font_size_px || clamp(segment.height * 0.62, 12, 56);
    text.textAlignHorizontal = style.text_align || "LEFT";
    text.fills = [{ type: "SOLID", color: hexToRgb(style.fill || "#111111") }];
    try {
      text.lineHeight = {
        unit: "PIXELS",
        value: style.line_height_px || Math.max(14, text.fontSize * 1.15),
      };
    } catch (_) {
    }
    text.x = segment.x - offsetX;
    text.y = segment.y - offsetY;
    text.textAutoResize = "NONE";
    text.resize(
      Math.max(segment.width, 32),
      Math.max(segment.height, (style.line_height_px || 0) + 4)
    );
    if ((segment.text || "").indexOf("\n") === -1) {
      try {
        text.textAutoResize = "WIDTH_AND_HEIGHT";
      } catch (_) {
      }
    }
    text.visible = isVisibleReplacementCandidate(candidate);
    encodePluginData(text, "slide-refine:id", candidate.id);
    encodePluginData(text, "slide-refine:source", candidate.source);
    encodePluginData(text, "slide-refine:text", segment.text);
    encodePluginData(text, "slide-refine:replacement_policy", candidate.replacement_policy);
    encodePluginData(text, "slide-refine:replacement_reason", candidate.replacement_reason);
    nodes.push(text);
  }
  return nodes;
}

async function buildTextDraftFrame(slide, isVisible, excludedIds) {
  const usable = (slide.text_candidates || []).filter(
    (candidate) => isEditableTextCandidate(candidate) && !(excludedIds && excludedIds[candidate.id])
  );
  if (!usable.length) return null;

  const frame = figma.createFrame();
  frame.name = "Editable Text Drafts";
  frame.resize(slide.canvas.width, slide.canvas.height);
  frame.fills = [];
  frame.strokes = [];
  frame.clipsContent = false;
  frame.visible = isVisible;

  for (const candidate of usable) {
    var textNodes = await createTextNodesForCandidate(candidate, 0, 0);
    for (var i = 0; i < textNodes.length; i += 1) {
      frame.appendChild(textNodes[i]);
    }
  }

  return frame;
}

function attachMetadata(node, slide) {
  encodePluginData(node, "slide-refine:slide_id", slide.id);
  encodePluginData(node, "slide-refine:slide_name", slide.name);
  encodePluginData(node, "slide-refine:profile", getAnalysisProfile(slide));
  encodePluginData(node, "slide-refine:recommended_mode", slide.recommended_mode);
}

async function createVectorSlide(slide, options) {
  const frame = figma.createFrame();
  frame.name = slide.name;
  frame.resize(slide.canvas.width, slide.canvas.height);
  frame.fills = [];
  frame.strokes = [];
  frame.clipsContent = false;
  attachMetadata(frame, slide);

  const rasterBase = buildBackgroundRasterRect(slide);
  frame.appendChild(rasterBase);

  const guides = buildGuideFrameWithVisibility(slide, false);
  frame.appendChild(guides);

  if (options.attachHiddenReference) {
    const referenceRect = buildReferenceRect(slide);
    referenceRect.name = "Reference Raster (Hidden)";
    referenceRect.visible = false;
    frame.appendChild(referenceRect);
  }

  if (options.includeHiddenVectorCandidate) {
    const vectorFrame = figma.createNodeFromSvg(slide.assets.vector_base_svg || slide.assets.vector_svg);
    vectorFrame.name = "Editable Vector Candidate";
    vectorFrame.visible = false;
    frame.appendChild(vectorFrame);
  }

  const assignedTextIds = await appendMovableVisualRegions(frame, slide);

  const rasterFrame = buildRasterRegionsFrame(slide);
  if (rasterFrame) {
    rasterFrame.visible = false;
    frame.appendChild(rasterFrame);
  }

  if (options.createTextDrafts) {
    const drafts = await buildTextDraftFrame(slide, true, assignedTextIds);
    if (drafts) {
      frame.appendChild(drafts);
    }
  }

  return frame;
}

async function createHybridSlide(slide, options) {
  const frame = figma.createFrame();
  frame.name = slide.name;
  frame.resize(slide.canvas.width, slide.canvas.height);
  frame.fills = [];
  frame.strokes = [];
  frame.clipsContent = false;
  attachMetadata(frame, slide);

  const referenceRect = buildReferenceRect(slide);
  frame.appendChild(referenceRect);

  const guides = buildGuideFrameWithVisibility(slide, true);
  frame.appendChild(guides);

  if (options.includeHiddenVectorCandidate) {
    const vectorFrame = figma.createNodeFromSvg(slide.assets.vector_base_svg || slide.assets.vector_svg);
    vectorFrame.name = "Editable Vector Candidate";
    vectorFrame.visible = false;
    frame.appendChild(vectorFrame);
  }

  if (options.createTextDrafts) {
    const drafts = await buildTextDraftFrame(slide, false, null);
    if (drafts) {
      frame.appendChild(drafts);
    }
  }

  return frame;
}

function resolveSlideMode(slide, globalMode) {
  if (globalMode === "vector") return "vector";
  if (globalMode === "hybrid-safe") return "hybrid-safe";
  return slide.recommended_mode || "vector";
}

async function importSlides(payload) {
  const { packageData, mode, createTextDrafts, attachHiddenReference, includeHiddenVectorCandidate } = payload;
  const slides = packageData.slides || [];
  if (!slides.length) {
    figma.notify("No slides found in figma-import.json.");
    return;
  }

  const viewportCenter = figma.viewport.center;
  const totalHeight =
    slides.reduce((sum, slide) => sum + slide.canvas.height, 0) +
    DEFAULT_GAP * Math.max(0, slides.length - 1);
  const maxWidth = Math.max(...slides.map((slide) => slide.canvas.width));
  let currentX = viewportCenter.x - maxWidth / 2;
  let currentY = viewportCenter.y - totalHeight / 2;

  const created = [];
  for (const slide of slides) {
    const slideMode = resolveSlideMode(slide, mode);
    const node =
      slideMode === "hybrid-safe"
        ? await createHybridSlide(slide, {
            createTextDrafts,
            includeHiddenVectorCandidate,
          })
        : await createVectorSlide(slide, {
            createTextDrafts,
            attachHiddenReference,
          });

    node.x = currentX;
    node.y = currentY;
    created.push(node);
    currentY += slide.canvas.height + DEFAULT_GAP;
  }

  figma.currentPage.selection = created;
  figma.viewport.scrollAndZoomIntoView(created);
  figma.notify(`Imported ${created.length} slide${created.length > 1 ? "s" : ""}.`);
}

figma.ui.onmessage = async (msg) => {
  if (!msg || !msg.type) return;

  if (msg.type === "import-package") {
    try {
      await importSlides(msg.payload);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      figma.notify(`SlideRefine import failed: ${message}`, { error: true });
    }
    return;
  }

  if (msg.type === "cancel") {
    figma.closePlugin();
  }
};
