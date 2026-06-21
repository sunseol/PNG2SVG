import {
  applyTransaction,
  createEditorState,
  createSampleDocument,
  exportSvg,
  makeTransaction,
  renderDocumentSvg
} from "./editor-core.js";

const elements = {
  slideSelect: document.querySelector("#slideSelect"),
  layerList: document.querySelector("#layerList"),
  canvasHost: document.querySelector("#canvasHost"),
  nodeName: document.querySelector("#nodeName"),
  textValue: document.querySelector("#textValue"),
  xValue: document.querySelector("#xValue"),
  yValue: document.querySelector("#yValue"),
  widthValue: document.querySelector("#widthValue"),
  heightValue: document.querySelector("#heightValue"),
  rotationValue: document.querySelector("#rotationValue"),
  fillValue: document.querySelector("#fillValue"),
  strokeValue: document.querySelector("#strokeValue"),
  opacityValue: document.querySelector("#opacityValue"),
  visibleValue: document.querySelector("#visibleValue"),
  lockedValue: document.querySelector("#lockedValue"),
  status: document.querySelector("#status"),
  fileInput: document.querySelector("#fileInput"),
  fileName: document.querySelector("#fileName"),
  overlayOpacity: document.querySelector("#overlayOpacity"),
  confidenceFilter: document.querySelector("#confidenceFilter"),
  confidenceThreshold: document.querySelector("#confidenceThreshold")
};

let state = createEditorState(createSampleDocument());
let apiToken = new URLSearchParams(window.location.search).get("token") || "";
let apiAvailable = false;
let localApiAvailable = false;

async function boot() {
  try {
    const response = await fetch("/api/v1/documents/current", { headers: apiHeaders({ accept: "application/json" }) });
    if (response.ok) {
      state = createEditorState(await response.json());
      apiAvailable = true;
      localApiAvailable = true;
      elements.fileName.textContent = "current document";
    }
  } catch {
    state = createEditorState(createSampleDocument());
  }
  bindEvents();
  render();
}

function bindEvents() {
  document.querySelector("#openSample").addEventListener("click", () => {
    state = createEditorState(createSampleDocument());
    apiAvailable = false;
    elements.fileName.textContent = "sample document";
    render();
  });
  elements.fileInput.addEventListener("change", () => {
    void attachFile();
  });
  document.querySelector("#saveDocument").addEventListener("click", saveDocument);
  document.querySelector("#exportSvg").addEventListener("click", () => downloadExport("svg"));
  document.querySelector("#exportPng").addEventListener("click", () => downloadExport("png"));
  document.querySelector("#undo").addEventListener("click", undo);
  document.querySelector("#redo").addEventListener("click", redo);
  document.querySelector("#duplicateNode").addEventListener("click", duplicateSelection);
  document.querySelector("#deleteNode").addEventListener("click", deleteSelection);
  document.querySelector("#bringForward").addEventListener("click", () => reorderSelection(1));
  document.querySelector("#sendBackward").addEventListener("click", () => reorderSelection(-1));
  document.querySelector("#groupNodes").addEventListener("click", groupSelection);
  document.querySelector("#ungroupNode").addEventListener("click", ungroupSelection);
  elements.slideSelect.addEventListener("change", () => {
    state.activeSlideId = elements.slideSelect.value;
    state.selectedNodeIds = [];
    render();
  });
  elements.textValue.addEventListener("change", () => {
    const node = selectedNode();
    if (node?.type === "text") {
      void commit([{ type: "set_text", nodeId: node.id, text: elements.textValue.value }]);
    }
  });
  elements.xValue.addEventListener("change", moveSelectionFromInputs);
  elements.yValue.addEventListener("change", moveSelectionFromInputs);
  elements.widthValue.addEventListener("change", resizeSelectionFromInputs);
  elements.heightValue.addEventListener("change", resizeSelectionFromInputs);
  elements.rotationValue.addEventListener("change", () => {
    const node = selectedNode();
    if (node && !node.locked) void commit([{ type: "rotate", nodeId: node.id, degrees: Number(elements.rotationValue.value) }]);
  });
  elements.fillValue.addEventListener("change", () => {
    const node = selectedNode();
    if (node && !node.locked) void commit([{ type: "set_fill", nodeId: node.id, color: elements.fillValue.value }]);
  });
  elements.strokeValue.addEventListener("change", () => {
    const node = selectedNode();
    if (node && !node.locked) void commit([{ type: "set_stroke", nodeId: node.id, color: elements.strokeValue.value, width: 1 }]);
  });
  elements.opacityValue.addEventListener("change", () => {
    const node = selectedNode();
    if (node && !node.locked) void commit([{ type: "set_opacity", nodeId: node.id, opacity: Number(elements.opacityValue.value) }]);
  });
  elements.visibleValue.addEventListener("change", () => {
    const node = selectedNode();
    if (node && !node.locked) void commit([{ type: "set_visibility", nodeId: node.id, visible: elements.visibleValue.checked }]);
  });
  elements.lockedValue.addEventListener("change", () => {
    const node = selectedNode();
    if (node) void commit([{ type: "set_locked", nodeId: node.id, locked: elements.lockedValue.checked }]);
  });
  elements.overlayOpacity.addEventListener("input", () => {
    state.originalOverlayOpacity = Number(elements.overlayOpacity.value);
    renderStatus("Overlay opacity changed");
  });
  elements.confidenceFilter.addEventListener("change", () => {
    state.confidenceFilterEnabled = elements.confidenceFilter.checked;
    state.selectedNodeIds = state.selectedNodeIds.filter((nodeId) => passesConfidenceFilter(state.document.nodes[nodeId]));
    render();
  });
  elements.confidenceThreshold.addEventListener("change", () => {
    state.confidenceThreshold = Math.max(0, Math.min(1, Number(elements.confidenceThreshold.value)));
    elements.confidenceThreshold.value = String(state.confidenceThreshold);
    state.selectedNodeIds = state.selectedNodeIds.filter((nodeId) => passesConfidenceFilter(state.document.nodes[nodeId]));
    render();
  });
}

function render() {
  renderSlides();
  renderLayers();
  const svgDocument = new DOMParser().parseFromString(
    renderDocumentSvg(documentForView(), state.activeSlideId, state.selectedNodeIds),
    "image/svg+xml"
  );
  elements.canvasHost.replaceChildren(document.importNode(svgDocument.documentElement, true));
  elements.canvasHost.querySelectorAll("[data-node-id]").forEach((nodeEl) => {
    nodeEl.addEventListener("click", (event) => {
      event.stopPropagation();
      selectNode(nodeEl.dataset.nodeId, event.ctrlKey || event.metaKey || event.shiftKey);
    });
  });
  elements.canvasHost.addEventListener("click", () => {
    state.selectedNodeIds = [];
    render();
  }, { once: true });
  renderInspector();
  renderStatus(`revision ${state.document.revision}`);
}

function renderSlides() {
  elements.slideSelect.replaceChildren();
  for (const slide of Object.values(state.document.slides)) {
    const option = document.createElement("option");
    option.value = slide.id;
    option.textContent = slide.name;
    elements.slideSelect.append(option);
  }
  elements.slideSelect.value = state.activeSlideId;
}

function renderLayers() {
  const slide = state.document.slides[state.activeSlideId];
  elements.layerList.replaceChildren();
  for (const nodeId of slide.children) {
    const node = state.document.nodes[nodeId];
    if (!passesConfidenceFilter(node)) continue;
    const item = document.createElement("li");
    item.dataset.nodeId = nodeId;
    item.setAttribute("aria-selected", String(state.selectedNodeIds.includes(nodeId)));
    if (node.locked) item.dataset.locked = "true";
    const name = document.createElement("span");
    name.textContent = node.name || node.id;
    const type = document.createElement("span");
    type.textContent = node.locked ? `${node.type} locked` : node.type;
    item.append(name, type);
    elements.layerList.append(item);
  }
  elements.layerList.querySelectorAll("[data-node-id]").forEach((item) => {
    item.addEventListener("click", (event) => selectNode(item.dataset.nodeId, event.ctrlKey || event.metaKey || event.shiftKey));
  });
}

function documentForView() {
  const view = JSON.parse(JSON.stringify(state.document));
  for (const node of Object.values(view.nodes)) {
    if (node.reason === "original_overlay") {
      node.opacity = state.originalOverlayOpacity;
      node.visible = state.originalOverlayOpacity > 0;
    } else if (!passesConfidenceFilter(node)) {
      node.visible = false;
    }
  }
  return view;
}

function passesConfidenceFilter(node) {
  if (!state.confidenceFilterEnabled || !node) return true;
  if (node.reason === "original_overlay") return true;
  const confidence = Number(node.provenance?.confidence ?? node.recognition?.confidence ?? 1);
  return confidence < state.confidenceThreshold;
}

function renderInspector() {
  const node = selectedNode();
  const disabled = !node;
  const locked = Boolean(node?.locked);
  elements.nodeName.textContent = node ? node.name || node.id : "No selection";
  elements.textValue.disabled = disabled || locked || node.type !== "text";
  elements.textValue.value = node?.text || "";
  elements.xValue.disabled = disabled || locked;
  elements.yValue.disabled = disabled || locked;
  elements.widthValue.disabled = disabled || locked || node.type === "group";
  elements.heightValue.disabled = disabled || locked || node.type === "group";
  elements.rotationValue.disabled = disabled || locked;
  elements.fillValue.disabled = disabled || locked || node.type === "group" || node.type === "image";
  elements.strokeValue.disabled = disabled || locked || node.type === "group" || node.type === "image";
  elements.opacityValue.disabled = disabled || locked;
  elements.visibleValue.disabled = disabled || locked;
  elements.lockedValue.disabled = disabled;
  elements.xValue.value = node ? Math.round(node.transform[4]) : "";
  elements.yValue.value = node ? Math.round(node.transform[5]) : "";
  elements.widthValue.value = node ? Math.round(node.bounds?.width || 0) : "";
  elements.heightValue.value = node ? Math.round(node.bounds?.height || 0) : "";
  elements.rotationValue.value = node ? Math.round(node.extensions?.rotationDegrees || 0) : "";
  elements.fillValue.value = node ? colorForInput(node.type === "text" ? node.style?.fill?.color : node.fill?.color) : "#000000";
  elements.strokeValue.value = node ? colorForInput(node.stroke?.color) : "#000000";
  elements.opacityValue.value = node ? node.opacity ?? 1 : "";
  elements.visibleValue.checked = node ? node.visible !== false : false;
  elements.lockedValue.checked = locked;
  elements.confidenceFilter.checked = state.confidenceFilterEnabled;
  elements.confidenceThreshold.value = String(state.confidenceThreshold);
}

function selectedNode() {
  return state.document.nodes[state.selectedNodeIds[0]];
}

function selectNode(nodeId, additive = false) {
  if (additive) {
    state.selectedNodeIds = state.selectedNodeIds.includes(nodeId)
      ? state.selectedNodeIds.filter((id) => id !== nodeId)
      : [...state.selectedNodeIds, nodeId];
  } else {
    state.selectedNodeIds = [nodeId];
  }
  render();
}

async function commit(operations) {
  const before = state.document;
  const transaction = makeTransaction(state.document, operations);
  if (apiAvailable) {
    const response = await fetch("/api/v1/documents/current/operations", {
      method: "POST",
      headers: apiHeaders({ "content-type": "application/json" }),
      body: JSON.stringify(transaction)
    });
    if (!response.ok) {
      apiAvailable = false;
      throw new Error(`Local API operation failed: ${response.status}`);
    }
    const payload = await response.json();
    state.document = payload.document;
  } else {
    const result = applyTransaction(state.document, transaction);
    state.document = result.document;
  }
  state.undoStack.push(before);
  state.redoStack = [];
  render();
}

async function attachFile() {
  const file = elements.fileInput.files?.[0];
  if (!file) return;
  if (!localApiAvailable) {
    renderStatus("File attach requires the local editor API");
    elements.fileInput.value = "";
    return;
  }
  renderStatus(`Opening ${file.name}`);
  try {
    const response = await fetch("/api/v1/documents/current/import", {
      method: "POST",
      headers: apiHeaders({
        "content-type": file.type || "application/octet-stream",
        "x-sliderefine-filename": encodeURIComponent(file.name)
      }),
      body: file
    });
    const payload = await response.json();
    if (!response.ok || payload.status !== "ok") {
      throw new Error(payload.message || `Import failed: ${response.status}`);
    }
    state = createEditorState(payload.document);
    apiAvailable = true;
    localApiAvailable = true;
    elements.fileName.textContent = file.name;
    render();
    renderStatus(`Opened ${file.name}`);
  } catch (error) {
    renderStatus(error instanceof Error ? error.message : "File attach failed");
  } finally {
    elements.fileInput.value = "";
  }
}

async function moveSelectionFromInputs() {
  const node = selectedNode();
  if (!node || node.locked) return;
  const dx = Number(elements.xValue.value) - Number(node.transform[4]);
  const dy = Number(elements.yValue.value) - Number(node.transform[5]);
  await commit([{ type: "translate", nodeIds: state.selectedNodeIds, dx, dy }]);
}

async function resizeSelectionFromInputs() {
  const node = selectedNode();
  if (!node || node.locked || node.type === "group") return;
  await commit([
    {
      type: "resize",
      nodeId: node.id,
      width: Number(elements.widthValue.value),
      height: Number(elements.heightValue.value)
    }
  ]);
}

async function reorderSelection(delta) {
  const node = selectedNode();
  if (!node || node.locked) return;
  const parent = state.document.slides[node.parentId] || state.document.nodes[node.parentId];
  const current = parent.children.indexOf(node.id);
  await commit([{ type: "reorder", parentId: node.parentId, nodeId: node.id, index: current + delta }]);
}

async function groupSelection() {
  if (state.selectedNodeIds.length < 2) return;
  if (state.selectedNodeIds.some((nodeId) => state.document.nodes[nodeId]?.locked)) return;
  const groupId = `group-${state.document.revision}`;
  await commit([{ type: "group", nodeIds: state.selectedNodeIds, groupId, name: "Group" }]);
  state.selectedNodeIds = [groupId];
  render();
}

async function ungroupSelection() {
  const node = selectedNode();
  if (node?.type !== "group" || node.locked) return;
  await commit([{ type: "ungroup", nodeId: node.id }]);
  state.selectedNodeIds = [];
  render();
}

async function duplicateSelection() {
  const node = selectedNode();
  if (!node || node.locked) return;
  const newNodeId = `${node.id}-copy-${state.document.revision}`;
  await commit([{ type: "duplicate", nodeId: node.id, newNodeId }]);
  state.selectedNodeIds = [newNodeId];
  render();
  renderStatus(`Duplicated ${newNodeId}`);
}

async function deleteSelection() {
  if (!state.selectedNodeIds.length) return;
  const deletable = state.selectedNodeIds.filter((nodeId) => !state.document.nodes[nodeId]?.locked);
  if (!deletable.length) return;
  await commit([{ type: "delete", nodeIds: deletable }]);
  state.selectedNodeIds = [];
  render();
}

function undo() {
  if (!state.undoStack.length) return;
  state.redoStack.push(state.document);
  state.document = state.undoStack.pop();
  void persistCurrentDocument();
  render();
  renderStatus("Undo");
}

function redo() {
  if (!state.redoStack.length) return;
  state.undoStack.push(state.document);
  state.document = state.redoStack.pop();
  void persistCurrentDocument();
  render();
}

async function saveDocument() {
  const payload = JSON.stringify(state.document, null, 2);
  try {
    const response = await fetch("/api/v1/documents/current", {
      method: "PUT",
      headers: apiHeaders({ "content-type": "application/json" }),
      body: payload
    });
    renderStatus(response.ok ? "Saved through local API" : "Local API save failed");
  } catch {
    renderStatus(payload);
  }
}

async function persistCurrentDocument() {
  if (!apiAvailable) return;
  try {
    const response = await fetch("/api/v1/documents/current", {
      method: "PUT",
      headers: apiHeaders({ "content-type": "application/json" }),
      body: JSON.stringify(state.document)
    });
    apiAvailable = response.ok;
  } catch {
    apiAvailable = false;
  }
}

function downloadExport(format) {
  if (apiAvailable) {
    fetch("/api/v1/documents/current/export", {
      method: "POST",
      headers: apiHeaders({ "content-type": "application/json" }),
      body: JSON.stringify({ format, slideId: state.activeSlideId })
    })
      .then((response) => response.json())
      .then((payload) => renderStatus(`${format.toUpperCase()} export prepared: ${payload.artifactUri || "local API"}`))
      .catch(() => renderStatus(`${format.toUpperCase()} export failed`));
    return;
  }
  if (format === "png") {
    renderStatus("PNG export requires the local API");
    return;
  }
  const svg = exportSvg(state.document, state.activeSlideId);
  const blob = new Blob([svg], { type: "image/svg+xml" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${state.activeSlideId}.svg`;
  anchor.click();
  URL.revokeObjectURL(url);
  renderStatus("SVG export prepared");
}

function renderStatus(message) {
  elements.status.textContent = message;
}

function colorForInput(value) {
  return /^#[0-9a-fA-F]{6}$/.test(value || "") ? value : "#000000";
}

function apiHeaders(headers = {}) {
  return apiToken ? { ...headers, "x-sliderefine-token": apiToken } : headers;
}

boot();
