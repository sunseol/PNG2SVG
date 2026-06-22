export function createSampleDocument() {
  return {
    schemaVersion: "1.0.0",
    documentId: "doc-web-sample",
    revision: 1,
    source: { kind: "sample", digest: "sha256:sample" },
    slides: {
      "slide-001": {
        id: "slide-001",
        name: "Sample",
        width: 640,
        height: 360,
        children: ["rect-hero", "text-title", "ellipse-accent"]
      }
    },
    nodes: {
      "rect-hero": {
        id: "rect-hero",
        type: "rect",
        name: "Panel",
        parentId: "slide-001",
        transform: [1, 0, 0, 1, 56, 64],
        opacity: 1,
        visible: true,
        locked: false,
        bounds: { x: 0, y: 0, width: 420, height: 180 },
        fill: { type: "solid", color: "#f0f4f8", opacity: 1 },
        provenance: { stage: "sample", engine: "web", sourceRegionId: "rect-hero", confidence: 1 },
        extensions: {}
      },
      "text-title": {
        id: "text-title",
        type: "text",
        name: "Title",
        parentId: "slide-001",
        transform: [1, 0, 0, 1, 92, 118],
        opacity: 1,
        visible: true,
        locked: false,
        bounds: { x: 0, y: 0, width: 360, height: 64 },
        text: "Editable title",
        style: {
          fontFamily: "Arial",
          fontSize: 36,
          fontWeight: 700,
          lineHeight: 1.2,
          letterSpacing: 0,
          align: "left",
          fill: { type: "solid", color: "#18212f", opacity: 1 }
        },
        layout: { width: 360, height: 64, overflow: "visible" },
        recognition: {
          rawText: "Editable title",
          normalizedText: "Editable title",
          language: "en",
          confidence: 1,
          status: "usable",
          alternatives: []
        },
        provenance: { stage: "sample", engine: "web", sourceRegionId: "text-title", confidence: 1 },
        extensions: {}
      },
      "ellipse-accent": {
        id: "ellipse-accent",
        type: "ellipse",
        name: "Accent",
        parentId: "slide-001",
        transform: [1, 0, 0, 1, 480, 86],
        opacity: 1,
        visible: true,
        locked: false,
        bounds: { x: 0, y: 0, width: 92, height: 92 },
        fill: { type: "solid", color: "#2f80ed", opacity: 1 },
        provenance: { stage: "sample", engine: "web", sourceRegionId: "ellipse-accent", confidence: 1 },
        extensions: {}
      }
    },
    assets: {},
    diagnostics: [],
    operationLog: []
  };
}

let transactionCounter = 0;

export function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

export function createEditorState(document) {
  const activeSlideId = Object.keys(document.slides)[0];
  return {
    document,
    activeSlideId,
    selectedNodeIds: [],
    undoStack: [],
    redoStack: [],
    originalOverlayOpacity: initialOverlayOpacity(document),
    confidenceFilterEnabled: false,
    confidenceThreshold: 0.8
  };
}

function initialOverlayOpacity(document) {
  const sourceImage = Object.values(document.nodes || {}).find((node) => node.reason === "original_overlay");
  return sourceImage ? Number(sourceImage.opacity ?? 1) : 0.2;
}

export function nodeTransform(node) {
  const t = node.transform || [1, 0, 0, 1, 0, 0];
  const matrix = `matrix(${t.map((value) => Number(value).toFixed(3)).join(" ")})`;
  const degrees = Number(node.extensions?.rotationDegrees || 0);
  if (!degrees) return matrix;
  const bounds = node.bounds || { width: 0, height: 0 };
  const cx = Number(bounds.width || 0) / 2;
  const cy = Number(bounds.height || 0) / 2;
  return `${matrix} rotate(${degrees.toFixed(3)} ${cx.toFixed(3)} ${cy.toFixed(3)})`;
}

export function applyTransaction(document, transaction) {
  if (transaction.expectedRevision !== document.revision) {
    throw new Error(`REVISION_CONFLICT expected ${transaction.expectedRevision} found ${document.revision}`);
  }
  const next = clone(document);
  const changed = new Set();
  for (const op of transaction.operations || []) {
    applyOperation(next, op, changed);
  }
  const result = {
    status: "ok",
    previousRevision: document.revision,
    revision: transaction.dryRun ? document.revision : document.revision + 1,
    changedNodeIds: Array.from(changed).sort(),
    warnings: [],
    inverseOperations: []
  };
  if (transaction.dryRun) {
    return { document, result };
  }
  next.revision += 1;
  next.operationLog = next.operationLog || [];
  next.operationLog.push({ operationId: transaction.operationId, transaction, result });
  return { document: next, result };
}

export function applyOperation(document, op, changed) {
  const node = op.nodeId ? document.nodes[op.nodeId] : null;
  switch (op.type) {
    case "set_text":
      requireNode(node, op.nodeId);
      node.text = String(op.text || "");
      if (node.recognition) node.recognition.normalizedText = node.text;
      changed.add(op.nodeId);
      break;
    case "translate":
      for (const nodeId of op.nodeIds || []) {
        const target = document.nodes[nodeId];
        requireNode(target, nodeId);
        target.transform[4] += Number(op.dx || 0);
        target.transform[5] += Number(op.dy || 0);
        changed.add(nodeId);
      }
      break;
    case "set_opacity":
      requireNode(node, op.nodeId);
      node.opacity = Number(op.opacity);
      changed.add(op.nodeId);
      break;
    case "set_visibility":
      requireNode(node, op.nodeId);
      node.visible = Boolean(op.visible);
      changed.add(op.nodeId);
      break;
    case "resize":
      requireNode(node, op.nodeId);
      node.bounds.width = Number(op.width);
      node.bounds.height = Number(op.height);
      if (node.layout) {
        node.layout.width = node.bounds.width;
        node.layout.height = node.bounds.height;
      }
      changed.add(op.nodeId);
      break;
    case "rotate":
      requireNode(node, op.nodeId);
      node.extensions = node.extensions || {};
      node.extensions.rotationDegrees = Number(op.degrees || 0);
      changed.add(op.nodeId);
      break;
    case "set_fill":
      requireNode(node, op.nodeId);
      node.fill = { type: "solid", color: op.color, opacity: Number(op.opacity ?? 1) };
      if (node.type === "text") {
        node.style = node.style || {};
        node.style.fill = node.fill;
      }
      changed.add(op.nodeId);
      break;
    case "set_stroke":
      requireNode(node, op.nodeId);
      node.stroke = { type: "solid", color: op.color, width: Number(op.width ?? 1) };
      changed.add(op.nodeId);
      break;
    case "set_locked":
      requireNode(node, op.nodeId);
      node.locked = Boolean(op.locked);
      changed.add(op.nodeId);
      break;
    case "reorder":
      reorderNode(document, op.parentId, op.nodeId, Number(op.index));
      changed.add(op.nodeId);
      break;
    case "group":
      {
        const groupId = op.groupId || `group-${Date.now()}`;
        groupNodes(document, op.nodeIds || [], groupId, op.name || "Group");
        changed.add(groupId);
      }
      for (const nodeId of op.nodeIds || []) changed.add(nodeId);
      break;
    case "ungroup":
      ungroupNode(document, op.nodeId);
      changed.add(op.nodeId);
      break;
    case "duplicate":
      duplicateNode(document, op.nodeId, op.newNodeId);
      changed.add(op.newNodeId);
      break;
    case "delete":
      for (const nodeId of op.nodeIds || []) {
        deleteNode(document, nodeId);
        changed.add(nodeId);
      }
      break;
    default:
      throw new Error(`UNKNOWN_OPERATION ${op.type}`);
  }
}

export function reorderNode(document, parentId, nodeId, index) {
  const parent = document.slides[parentId] || document.nodes[parentId];
  if (!parent?.children?.includes(nodeId)) throw new Error(`NODE_NOT_IN_PARENT ${nodeId}`);
  parent.children = parent.children.filter((childId) => childId !== nodeId);
  parent.children.splice(Math.max(0, Math.min(index, parent.children.length)), 0, nodeId);
}

export function groupNodes(document, nodeIds, groupId, name) {
  if (!nodeIds.length) throw new Error("EMPTY_GROUP");
  const first = document.nodes[nodeIds[0]];
  requireNode(first, nodeIds[0]);
  const parentId = first.parentId;
  const parent = document.slides[parentId] || document.nodes[parentId];
  if (!parent) throw new Error(`PARENT_NOT_FOUND ${parentId}`);
  for (const nodeId of nodeIds) {
    const node = document.nodes[nodeId];
    requireNode(node, nodeId);
    if (node.parentId !== parentId) throw new Error("MIXED_PARENT");
  }
  const insertAt = Math.min(...nodeIds.map((nodeId) => parent.children.indexOf(nodeId)).filter((index) => index >= 0));
  const group = {
    id: groupId,
    type: "group",
    name,
    parentId,
    transform: [1, 0, 0, 1, 0, 0],
    opacity: 1,
    visible: true,
    locked: false,
    bounds: { x: 0, y: 0, width: 0, height: 0 },
    children: [],
    provenance: { stage: "web_operation", engine: "web", sourceRegionId: groupId, confidence: 1 },
    extensions: {}
  };
  parent.children = parent.children.filter((childId) => !nodeIds.includes(childId));
  parent.children.splice(insertAt, 0, groupId);
  for (const nodeId of nodeIds) {
    document.nodes[nodeId].parentId = groupId;
    group.children.push(nodeId);
  }
  document.nodes[groupId] = group;
}

export function ungroupNode(document, groupId) {
  const group = document.nodes[groupId];
  requireNode(group, groupId);
  if (group.type !== "group") throw new Error(`INVALID_NODE_TYPE ${groupId}`);
  const parent = document.slides[group.parentId] || document.nodes[group.parentId];
  const index = parent.children.indexOf(groupId);
  parent.children = parent.children.filter((childId) => childId !== groupId);
  const released = group.children || [];
  released.forEach((childId, offset) => {
    document.nodes[childId].parentId = group.parentId;
    parent.children.splice(index + offset, 0, childId);
  });
  delete document.nodes[groupId];
}

export function duplicateNode(document, nodeId, explicitId) {
  const source = document.nodes[nodeId];
  requireNode(source, nodeId);
  const newId = explicitId || `${nodeId}-copy-${Date.now()}`;
  const copy = clone(source);
  copy.id = newId;
  copy.name = `${source.name || nodeId} copy`;
  copy.transform[4] += 16;
  copy.transform[5] += 16;
  document.nodes[newId] = copy;
  const parent = document.slides[copy.parentId] || document.nodes[copy.parentId];
  parent.children.push(newId);
  return newId;
}

export function deleteNode(document, nodeId) {
  const node = document.nodes[nodeId];
  if (!node) return;
  const parent = document.slides[node.parentId] || document.nodes[node.parentId];
  if (parent?.children) {
    parent.children = parent.children.filter((childId) => childId !== nodeId);
  }
  delete document.nodes[nodeId];
}

export function makeTransaction(document, operations, operationId = `op-web-${Date.now()}-${(transactionCounter += 1)}`) {
  return {
    schemaVersion: "1.0.0",
    operationId,
    expectedRevision: document.revision,
    dryRun: false,
    operations
  };
}

export function renderDocumentSvg(document, slideId, selectedNodeIds = []) {
  const slide = document.slides[slideId];
  const selected = new Set(selectedNodeIds);
  const body = slide.children
    .map((nodeId) => renderNode(document, document.nodes[nodeId], selected))
    .join("\n");
  return `<svg class="slide-canvas" xmlns="http://www.w3.org/2000/svg" width="${slide.width}" height="${slide.height}" viewBox="0 0 ${slide.width} ${slide.height}" role="img" aria-label="${escapeAttr(slide.name)}">
${body}
</svg>`;
}

export function renderNode(document, node, selected = new Set()) {
  if (!node || node.visible === false) return "";
  const common = `class="node" data-node-id="${escapeAttr(node.id)}" data-node-type="${escapeAttr(node.type)}" aria-selected="${selected.has(node.id) ? "true" : "false"}" transform="${nodeTransform(node)}" opacity="${node.opacity ?? 1}"`;
  const b = node.bounds || { x: 0, y: 0, width: 0, height: 0 };
  if (node.type === "group") {
    const children = (node.children || []).map((childId) => renderNode(document, document.nodes[childId], selected)).join("\n");
    return `<g ${common}>${children}</g>`;
  }
  if (node.type === "rect") {
    return `<rect ${common} x="${b.x}" y="${b.y}" width="${b.width}" height="${b.height}" fill="${escapeAttr(fillColor(node))}" ${strokeAttrs(node)}></rect>`;
  }
  if (node.type === "ellipse") {
    return `<ellipse ${common} cx="${b.width / 2}" cy="${b.height / 2}" rx="${b.width / 2}" ry="${b.height / 2}" fill="${escapeAttr(fillColor(node))}" ${strokeAttrs(node)}></ellipse>`;
  }
  if (node.type === "path") {
    return `<path ${common} d="${escapeAttr(node.d || "")}" fill="${escapeAttr(fillColor(node))}" fill-rule="${escapeAttr(node.fillRule || "evenodd")}" ${strokeAttrs(node)}></path>`;
  }
  if (node.type === "text") {
    const style = node.style || {};
    return `<text ${common} x="0" y="${style.fontSize || 16}" fill="${escapeAttr(fillColor(node))}" font-family="${escapeAttr(style.fontFamily || "Arial")}" font-size="${style.fontSize || 16}" font-weight="${escapeAttr(style.fontWeight || 400)}">${escapeText(node.text || "")}</text>`;
  }
  if (node.type === "image") {
    const href = assetHref(document, node);
    if (!href) return "";
    return `<image ${common} width="${b.width}" height="${b.height}" href="${escapeAttr(href)}" preserveAspectRatio="xMidYMid meet"></image>`;
  }
  return "";
}

export function exportSvg(document, slideId) {
  return `<?xml version="1.0" encoding="UTF-8"?>\n${renderDocumentSvg(document, slideId, [])}\n`;
}

function requireNode(node, nodeId) {
  if (!node) throw new Error(`NODE_NOT_FOUND ${nodeId}`);
}

function fillColor(node) {
  if (node.type === "text") {
    return node.style?.fill?.color || "#111111";
  }
  return node.fill?.color || "none";
}

function strokeAttrs(node) {
  if (!node.stroke?.color) return "";
  return `stroke="${escapeAttr(node.stroke.color)}" stroke-width="${Number(node.stroke.width || 1)}"`;
}

function assetHref(document, node) {
  const asset = document.assets?.[node.assetId];
  return asset?.dataUri || asset?.uri || "";
}

function escapeAttr(value) {
  return String(value).replace(/[&"<>]/g, (char) => ({ "&": "&amp;", "\"": "&quot;", "<": "&lt;", ">": "&gt;" })[char]);
}

function escapeText(value) {
  return String(value).replace(/[&<>]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[char]);
}
