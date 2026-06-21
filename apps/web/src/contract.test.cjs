const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

(async () => {
  const required = [
    "../../../schema/document.schema.json",
    "../../../schema/operations.schema.json",
    "editor-core.js",
    "app.js",
    "style.css"
  ];

  for (const rel of required) {
    const full = path.join(__dirname, rel);
    if (!fs.existsSync(full)) {
      throw new Error(`Missing web contract file: ${rel}`);
    }
  }

  const core = await import(pathToFileURL(path.join(__dirname, "editor-core.js")).href);
  const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
  if (!html.includes('id="fileInput"') || !html.includes('id="fileName"')) {
    throw new Error("File attach controls are missing from the web studio shell");
  }

  const documentState = core.createSampleDocument();
  const state = core.createEditorState(documentState);
  if (!state.activeSlideId || !documentState.nodes["text-title"]) {
    throw new Error("Editor state did not initialize from document contract");
  }

  const transaction = core.makeTransaction(documentState, [
    { type: "set_text", nodeId: "text-title", text: "Updated" },
    { type: "translate", nodeIds: ["text-title"], dx: 12, dy: 4 }
  ], "op-contract");
  const { document: updated, result } = core.applyTransaction(documentState, transaction);
  if (result.revision !== 2 || updated.nodes["text-title"].text !== "Updated") {
    throw new Error("Operation contract failed");
  }
  if (!core.exportSvg(updated, "slide-001").includes("Updated")) {
    throw new Error("SVG export contract failed");
  }

  const hostile = core.createSampleDocument();
  hostile.slides["slide-001"].name = "Slide <script>";
  hostile.nodes["text-title"].id = "text-title\"><script>";
  hostile.nodes["text-title"].text = "<script>alert(1)</script>";
  const rendered = core.renderDocumentSvg(hostile, "slide-001");
  if (rendered.includes("<script>") || rendered.includes("text-title\"><script>")) {
    throw new Error("Renderer did not escape document-controlled SVG content");
  }

  console.log(JSON.stringify({ status: "ok", checks: required.length, revision: updated.revision }));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
