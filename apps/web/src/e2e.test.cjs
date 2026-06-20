const http = require("http");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");
const WebSocket = require("ws");
const repoRoot = path.resolve(__dirname, "..", "..", "..");
const evidenceRoot = process.env.SLIDEREFINE_EVIDENCE_DIR || path.join(repoRoot, "evidence", "working");

function which(command) {
  const paths = (process.env.PATH || "").split(path.delimiter);
  const extensions = process.platform === "win32" ? [".exe", ".cmd", ".bat", ""] : [""];
  for (const dir of paths) {
    for (const ext of extensions) {
      const candidate = path.join(dir, `${command}${ext}`);
      if (fs.existsSync(candidate)) return candidate;
    }
  }
  return "";
}

function chromePath() {
  const candidates = [
    process.env.CHROME_PATH || "",
    which("google-chrome"),
    which("google-chrome-stable"),
    which("chromium"),
    which("chromium-browser"),
    which("chrome"),
    which("msedge"),
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
  ];
  const found = candidates.find((candidate) => fs.existsSync(candidate));
  if (!found) throw new Error("Chrome or Edge is required for browser E2E");
  return found;
}

function contentType(file) {
  if (file.endsWith(".html")) return "text/html; charset=utf-8";
  if (file.endsWith(".js")) return "text/javascript; charset=utf-8";
  if (file.endsWith(".css")) return "text/css; charset=utf-8";
  return "application/octet-stream";
}

function startServer(root) {
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, "http://127.0.0.1");
    if (url.pathname.startsWith("/api/")) {
      response.writeHead(404, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: "not-found" }));
      return;
    }
    const requested = url.pathname === "/" ? "/index.html" : url.pathname;
    const file = path.join(root, requested.replace(/^\/+/, ""));
    if (!file.startsWith(root) || !fs.existsSync(file)) {
      response.writeHead(404);
      response.end("not found");
      return;
    }
    response.writeHead(200, { "content-type": contentType(file) });
    response.end(fs.readFileSync(file));
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

function getAvailablePort() {
  const server = http.createServer();
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function httpJson(url, method = "GET") {
  return new Promise((resolve, reject) => {
    const request = http.request(url, { method }, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        body += chunk;
      });
      response.on("end", () => {
        try {
          resolve(JSON.parse(body));
        } catch (error) {
          reject(error);
        }
      });
    });
    request.on("error", reject);
    request.end();
  });
}

async function waitForDebugPort(port, chrome) {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    if (chrome.exitCode !== null) {
      throw new Error(`Chrome exited before DevTools opened with code ${chrome.exitCode}`);
    }
    try {
      return await httpJson(`http://127.0.0.1:${port}/json/version`);
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  throw new Error("Chrome DevTools port did not open");
}

async function stopChrome(chrome) {
  if (chrome.exitCode !== null) return;
  chrome.kill();
  await new Promise((resolve) => {
    const timeout = setTimeout(resolve, 5000);
    chrome.once("exit", () => {
      clearTimeout(timeout);
      resolve();
    });
  });
}

async function launchChrome(chromeExecutable, appUrl, userDataDir) {
  let lastError;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const debugPort = await getAvailablePort();
    const chromeStderr = [];
    const chrome = spawn(chromeExecutable, [
      "--headless=new",
      "--disable-gpu",
      "--disable-dev-shm-usage",
      "--disable-extensions",
      "--disable-background-networking",
      "--disable-software-rasterizer",
      "--no-sandbox",
      "--no-first-run",
      "--no-default-browser-check",
      "--remote-debugging-address=127.0.0.1",
      "--remote-allow-origins=*",
      `--user-data-dir=${userDataDir}`,
      `--remote-debugging-port=${debugPort}`,
      appUrl
    ], { stdio: ["ignore", "ignore", "pipe"] });
    chrome.stderr.on("data", (chunk) => {
      chromeStderr.push(chunk.toString());
    });
    chrome.on("error", (error) => {
      chromeStderr.push(error.message);
    });
    try {
      await waitForDebugPort(debugPort, chrome);
      return { chrome, debugPort };
    } catch (error) {
      const stderrTail = chromeStderr.join("").slice(-4000);
      lastError = new Error(`${error.message}${stderrTail ? `\nChrome stderr:\n${stderrTail}` : ""}`);
      await stopChrome(chrome);
      if (attempt < 3) {
        await new Promise((resolve) => setTimeout(resolve, 500 * attempt));
      }
    }
  }
  throw new Error(`Chrome DevTools port did not open after retries: ${lastError.message}`);
}

function createCdpClient(webSocketDebuggerUrl) {
  const socket = new WebSocket(webSocketDebuggerUrl);
  let nextId = 1;
  const pending = new Map();
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result);
    }
  };
  return new Promise((resolve, reject) => {
    socket.onopen = () => {
      resolve({
        send(method, params = {}) {
          const id = nextId++;
          socket.send(JSON.stringify({ id, method, params }));
          return new Promise((sendResolve, sendReject) => {
            pending.set(id, { resolve: sendResolve, reject: sendReject });
          });
        },
        close() {
          socket.close();
        }
      });
    };
    socket.onerror = (event) => reject(new Error(`CDP websocket error: ${event.message || "unknown"}`));
  });
}

async function evaluate(cdp, expression) {
  const result = await cdp.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true
  });
  if (result.exceptionDetails) {
    const details = result.exceptionDetails;
    const description = details.exception?.description || details.text || JSON.stringify(details);
    throw new Error(description);
  }
  return result.result.value;
}

async function waitFor(cdp, expression) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    if (await evaluate(cdp, expression)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for ${expression}`);
}

(async () => {
  const root = __dirname;
  const externalUrl = process.env.SLIDEREFINE_E2E_URL || "";
  const server = externalUrl ? null : await startServer(root);
  const appPort = server ? server.address().port : 0;
  const appUrl = externalUrl || `http://127.0.0.1:${appPort}/index.html`;
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "sliderefine-chrome-"));
  const chromeExecutable = chromePath();
  const transcript = [];
  let chrome;
  let debugPort;
  let cdp;
  try {
    ({ chrome, debugPort } = await launchChrome(chromeExecutable, appUrl, userDataDir));
    const targets = await httpJson(`http://127.0.0.1:${debugPort}/json`);
    const pageTarget = targets.find((target) => target.type === "page");
    cdp = await createCdpClient(pageTarget.webSocketDebuggerUrl);
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Page.navigate", { url: appUrl });

    await waitFor(cdp, "Boolean(document.querySelector('.slide-canvas'))");
    transcript.push("open");

    if (!externalUrl) {
      const visibleBeforeFilter = await evaluate(cdp, "document.querySelectorAll('.node').length");
      await evaluate(cdp, "document.querySelector('#confidenceFilter').checked=true; document.querySelector('#confidenceFilter').dispatchEvent(new Event('change', { bubbles: true }));");
      await waitFor(cdp, `document.querySelectorAll('.node').length < ${visibleBeforeFilter}`);
      await evaluate(cdp, "document.querySelector('#confidenceFilter').checked=false; document.querySelector('#confidenceFilter').dispatchEvent(new Event('change', { bubbles: true }));");
      await waitFor(cdp, `document.querySelectorAll('.node').length === ${visibleBeforeFilter}`);
      transcript.push("confidence-filter");
    }

    const textNodeId = await evaluate(
      cdp,
      "(() => { const textNode = document.querySelector('.node[data-node-id=\"text-title\"]') || [...document.querySelectorAll('.node')].find((node) => node.tagName.toLowerCase() === 'text'); return textNode?.dataset.nodeId || ''; })()"
    );
    if (textNodeId) {
      await evaluate(cdp, `document.querySelector('.node[data-node-id="${textNodeId}"]').dispatchEvent(new MouseEvent('click', { bubbles: true }))`);
      const textEnabled = await evaluate(cdp, "!document.querySelector('#textValue').disabled");
      if (!textEnabled) throw new Error("Text node inspector did not enable text editing");
      await evaluate(cdp, "const t=document.querySelector('#textValue'); t.value='Browser edited title'; t.dispatchEvent(new Event('change', { bubbles: true }));");
      await waitFor(cdp, "document.querySelector('.slide-canvas').textContent.includes('Browser edited title')");
      transcript.push("text-edit");
    }

    const selectedNodeId = await evaluate(
      cdp,
      "(() => { const preferred = document.querySelector('.node[data-node-id=\"rect-hero\"]') || document.querySelector('rect.node') || document.querySelector('ellipse.node') || document.querySelector('path.node') || document.querySelector('.node'); preferred.dispatchEvent(new MouseEvent('click', { bubbles: true })); return preferred.dataset.nodeId; })()"
    );
    await evaluate(cdp, "const x=document.querySelector('#xValue'); x.value='128'; x.dispatchEvent(new Event('change', { bubbles: true }));");
    await waitFor(cdp, `Boolean(document.querySelector('.node[data-node-id="${selectedNodeId}"]')) && document.querySelector('.node[data-node-id="${selectedNodeId}"]').getAttribute('transform').includes('128')`);
    transcript.push("move");

    const selectedTag = await evaluate(cdp, `document.querySelector('.node[data-node-id="${selectedNodeId}"]').tagName.toLowerCase()`);
    if (["rect", "ellipse"].includes(selectedTag)) {
      await evaluate(cdp, "const w=document.querySelector('#widthValue'); w.value='240'; w.dispatchEvent(new Event('change', { bubbles: true }));");
      await waitFor(cdp, `document.querySelector('.node[data-node-id="${selectedNodeId}"]').outerHTML.includes('240')`);
      transcript.push("resize");
    }

    const fillEnabled = await evaluate(cdp, "!document.querySelector('#fillValue').disabled");
    if (fillEnabled) {
      await evaluate(cdp, "const fill=document.querySelector('#fillValue'); fill.value='#ff0000'; fill.dispatchEvent(new Event('change', { bubbles: true }));");
      await waitFor(cdp, `document.querySelector('.node[data-node-id="${selectedNodeId}"]').outerHTML.includes('#ff0000') || document.querySelector('.node[data-node-id="${selectedNodeId}"]').outerHTML.includes('rgb(255, 0, 0)')`);
      transcript.push("fill");
    }

    await evaluate(cdp, "const rotation=document.querySelector('#rotationValue'); rotation.value='15'; rotation.dispatchEvent(new Event('change', { bubbles: true }));");
    await waitFor(cdp, `document.querySelector('.node[data-node-id="${selectedNodeId}"]').getAttribute('transform').includes('rotate(15')`);
    transcript.push("rotate");

    await evaluate(cdp, "(() => { const input=document.querySelector('#lockedValue'); input.checked=true; input.dispatchEvent(new Event('change', { bubbles: true })); })()");
    await waitFor(cdp, "document.querySelector('#lockedValue').checked && document.querySelector('#xValue').disabled");
    await evaluate(cdp, "(() => { const input=document.querySelector('#lockedValue'); input.checked=false; input.dispatchEvent(new Event('change', { bubbles: true })); })()");
    await waitFor(cdp, "!document.querySelector('#lockedValue').checked && !document.querySelector('#xValue').disabled");
    transcript.push("lock");

    await evaluate(cdp, "document.querySelector('#bringForward').click()");
    await evaluate(cdp, "new Promise((resolve) => setTimeout(resolve, 250))");
    transcript.push("reorder");

    const canGroup = await evaluate(cdp, "Boolean(document.querySelector('.node[data-node-id=\"rect-hero\"]') && document.querySelector('.node[data-node-id=\"ellipse-accent\"]'))");
    if (canGroup) {
      await evaluate(cdp, "document.querySelector('.node[data-node-id=\"rect-hero\"]').dispatchEvent(new MouseEvent('click', { bubbles: true }))");
      await evaluate(cdp, "document.querySelector('.node[data-node-id=\"ellipse-accent\"]').dispatchEvent(new MouseEvent('click', { bubbles: true, ctrlKey: true }))");
      await evaluate(cdp, "document.querySelector('#groupNodes').click()");
      await waitFor(cdp, "Boolean(document.querySelector('.node[data-node-id^=\"group-\"]'))");
      transcript.push("group");
      await evaluate(cdp, "document.querySelector('#ungroupNode').click()");
      await waitFor(cdp, "!document.querySelector('.node[data-node-id^=\"group-\"]')");
      transcript.push("ungroup");
    }

    await evaluate(cdp, `document.querySelector('.node[data-node-id="${selectedNodeId}"]').dispatchEvent(new MouseEvent('click', { bubbles: true }))`);
    await waitFor(cdp, `document.querySelector('.node[data-node-id="${selectedNodeId}"]').getAttribute('aria-selected') === 'true'`);
    const copyPrefix = `${selectedNodeId}-copy`;
    const beforeCopies = await evaluate(cdp, `document.querySelectorAll('.node[data-node-id^="${copyPrefix}"]').length`);
    await evaluate(cdp, "document.querySelector('#duplicateNode').click()");
    await waitFor(cdp, `document.querySelectorAll('.node[data-node-id^="${copyPrefix}"]').length > ${beforeCopies}`);
    await waitFor(cdp, "document.querySelector('#status').textContent.includes('Duplicated')");
    transcript.push("duplicate");

    await evaluate(cdp, "document.querySelector('#undo').click()");
    await waitFor(cdp, `document.querySelectorAll('.node[data-node-id^="${copyPrefix}"]').length === ${beforeCopies}`);
    transcript.push("undo");

    await evaluate(cdp, "document.querySelector('#redo').click()");
    await waitFor(cdp, `document.querySelectorAll('.node[data-node-id^="${copyPrefix}"]').length > ${beforeCopies}`);
    transcript.push("redo");

    await evaluate(cdp, "document.querySelector('#deleteNode').click()");
    await waitFor(cdp, `document.querySelectorAll('.node[data-node-id^="${copyPrefix}"]').length === ${beforeCopies}`);
    transcript.push("delete");

    if (externalUrl) {
      const exported = await evaluate(
        cdp,
        `(async () => {
          const token = new URLSearchParams(location.search).get('token');
          const response = await fetch('/api/v1/documents/current/export', {
            method: 'POST',
            headers: { 'content-type': 'application/json', 'x-sliderefine-token': token },
            body: JSON.stringify({ format: 'svg' })
          });
          if (!response.ok) return false;
          const payload = await response.json();
          return payload.status === 'ok' && payload.byteLength > 0;
        })()`
      );
      if (!exported) throw new Error("Local API SVG export failed");
      const pngExported = await evaluate(
        cdp,
        `(async () => {
          const token = new URLSearchParams(location.search).get('token');
          const response = await fetch('/api/v1/documents/current/export', {
            method: 'POST',
            headers: { 'content-type': 'application/json', 'x-sliderefine-token': token },
            body: JSON.stringify({ format: 'png' })
          });
          if (!response.ok) return false;
          const payload = await response.json();
          return payload.status === 'ok' && payload.byteLength > 0;
        })()`
      );
      if (!pngExported) throw new Error("Local API PNG export failed");
      await evaluate(cdp, "document.querySelector('#saveDocument').click()");
      await waitFor(cdp, "document.querySelector('#status').textContent.includes('Saved through local API')");
      const saved = await evaluate(
        cdp,
        `(async () => {
          const token = new URLSearchParams(location.search).get('token');
          const response = await fetch('/api/v1/documents/current', {
            headers: { 'x-sliderefine-token': token }
          });
          if (!response.ok) return false;
          const payload = await response.json();
          return payload.revision >= 1 && Object.keys(payload.nodes).length > 0;
        })()`
      );
      if (!saved) throw new Error("Local API save persistence check failed");
      transcript.push("save");
    } else {
      await evaluate(cdp, "document.querySelector('#exportSvg').click()");
      await waitFor(cdp, "document.querySelector('#status').textContent.includes('SVG export')");
      await evaluate(cdp, "document.querySelector('#exportPng').click()");
      await waitFor(cdp, "document.querySelector('#status').textContent.includes('PNG export')");
    }
    transcript.push("export");

    const screenshot = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    const screenshotPath = path.join(evidenceRoot, "browser", "playwright-report", "editor.png");
    fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
    fs.writeFileSync(screenshotPath, Buffer.from(screenshot.data, "base64"));
    const reportPath = path.join(evidenceRoot, "browser", "report.json");
    fs.mkdirSync(path.dirname(reportPath), { recursive: true });
    fs.writeFileSync(
      reportPath,
      JSON.stringify({ status: "ok", engine: "chrome-cdp", chromeExecutable, transcript, screenshotPath }, null, 2)
    );

    console.log(JSON.stringify({ status: "ok", engine: "chrome-cdp", chromeExecutable, transcript }));
  } finally {
    if (cdp) cdp.close();
    if (chrome) await stopChrome(chrome);
    if (server) server.close();
    try {
      fs.rmSync(userDataDir, { recursive: true, force: true });
    } catch {
      // Chrome can keep profile databases locked briefly on Windows.
    }
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
