"use strict";

// Headless Chrome interaction test. Uses only Node's built-in CDP WebSocket support.
const assert = require("node:assert/strict");
const net = require("node:net");
const { spawn } = require("node:child_process");

const [base, roomId, roomName, chromePath, profileDir] = process.argv.slice(2);
const secret = process.env.SOCKETROOM_TEST_SECRET;
assert(base && roomId && roomName && chromePath && profileDir && secret);

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(predicate, label, timeoutMs = 12000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await pause(100);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

class CDP {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
    ws.addEventListener("message", (event) => {
      const packet = JSON.parse(event.data);
      if (packet.id) {
        const item = this.pending.get(packet.id);
        if (!item) return;
        this.pending.delete(packet.id);
        if (packet.error) item.reject(new Error(JSON.stringify(packet.error)));
        else item.resolve(packet.result || {});
      } else {
        for (const listener of this.listeners.get(packet.method) || []) listener(packet.params || {});
      }
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  event(method, predicate = () => true, timeoutMs = 10000) {
    return new Promise((resolve, reject) => {
      const listeners = this.listeners.get(method) || new Set();
      const done = (params) => {
        if (!predicate(params)) return;
        clearTimeout(timer);
        listeners.delete(done);
        resolve(params);
      };
      const timer = setTimeout(() => {
        listeners.delete(done);
        reject(new Error(`Timed out waiting for CDP ${method}`));
      }, timeoutMs);
      listeners.add(done);
      this.listeners.set(method, listeners);
    });
  }

  async evaluate(expression) {
    const output = await this.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (output.exceptionDetails) throw new Error(JSON.stringify(output.exceptionDetails));
    return output.result?.value;
  }
}

async function main() {
  const debuggerPort = await freePort();
  const chrome = spawn(chromePath, [
    "--headless=new",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-gpu",
    "--disable-background-networking",
    "--remote-allow-origins=*",
    `--remote-debugging-port=${debuggerPort}`,
    `--user-data-dir=${profileDir}`,
    "about:blank",
  ], { stdio: "ignore", windowsHide: true });
  let ws;
  let cdp;
  try {
    let endpoint;
    await until(async () => {
      if (chrome.exitCode !== null) throw new Error(`Chrome exited ${chrome.exitCode}`);
      try {
        const response = await fetch(`http://127.0.0.1:${debuggerPort}/json/list`);
        if (!response.ok) return false;
        const pages = await response.json();
        endpoint = pages.find((page) => page.type === "page")?.webSocketDebuggerUrl;
        return !!endpoint;
      } catch (_) {
        return false;
      }
    }, "Chrome CDP endpoint", 15000);
    ws = new WebSocket(endpoint);
    await new Promise((resolve, reject) => {
      ws.addEventListener("open", resolve, { once: true });
      ws.addEventListener("error", reject, { once: true });
    });
    cdp = new CDP(ws);
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Page.navigate", { url: `${base}/?room=${encodeURIComponent(roomId)}` });
    await until(async () => cdp.evaluate("document.readyState === 'complete' && !!document.querySelector('#sr-login-name')"), "chat page");
    await until(async () => cdp.evaluate("!document.querySelector('#sr-login-layer').hidden"), "guest name prompt");
    await cdp.evaluate("document.querySelector('#sr-login-name').value='Browser Guest'; document.querySelector('#sr-login-form').requestSubmit();");
    await until(async () => cdp.evaluate(`document.querySelector('#sr-login-layer').hidden && document.querySelector('#sr-room-title').textContent === ${JSON.stringify(roomName)}`), "guest room login");
    assert.equal(await cdp.evaluate("document.querySelectorAll('[data-action=\"delete-room\"]:not([hidden])').length"), 0, "guest saw a delete action");

    const body = `Browser send ${Date.now()}`;
    await cdp.send("Fetch.enable", { patterns: [{ urlPattern: "*api/rooms/*/send", requestStage: "Request" }] });
    const requestPaused = cdp.event("Fetch.requestPaused", (params) => params.request?.method === "POST");
    await cdp.evaluate(`document.querySelector('#sr-message-input').value=${JSON.stringify(body)}; document.querySelector('[data-action="send-message"]').click();`);
    const paused = await requestPaused;
    assert.equal(await cdp.evaluate("document.querySelector('#sr-message-input').value"), body,
                 "composer cleared before the server accepted the send");
    assert.equal(await cdp.evaluate("document.querySelector('[data-action=\"send-message\"]').disabled"), true,
                 "send button remained enabled during request");
    await cdp.send("Fetch.continueRequest", { requestId: paused.requestId });
    await cdp.send("Fetch.disable");
    await until(async () => cdp.evaluate(`document.querySelector('#sr-message-input').value === '' && [...document.querySelectorAll('.sr-message-body')].some(el => el.textContent === ${JSON.stringify(body)})`), "confirmed message and cleared composer");
    assert.equal(await cdp.evaluate(`[...document.querySelectorAll('.sr-message-body')].filter(el => el.textContent === ${JSON.stringify(body)}).length`), 1,
                 "message was displayed more than once");

    await cdp.evaluate("document.querySelector('[data-action=\"admin-login\"]').click();");
    await until(async () => cdp.evaluate("!document.querySelector('#sr-admin-layer').hidden"), "admin login modal");
    await cdp.evaluate(`document.querySelector('#sr-admin-secret').value=${JSON.stringify(secret)}; document.querySelector('#sr-admin-form').requestSubmit();`);
    await until(async () => cdp.evaluate("document.querySelector('#sr-admin-layer').hidden && !!document.querySelector('[data-action=\"delete-room\"]')"), "admin controls");
    assert.equal(await cdp.evaluate("document.querySelector('[data-action=\"delete-room\"]').hidden"), false,
                 "admin delete button for the active shared room was missing");
    assert.equal(await cdp.evaluate("document.querySelector('#sr-room-title').textContent"), roomName);
    await cdp.evaluate("document.querySelector('[data-action=\"delete-room\"]').click();");
    await until(async () => cdp.evaluate("!document.querySelector('#sr-delete-layer').hidden"), "delete confirmation modal");
    await cdp.evaluate(`const input=document.querySelector('#sr-delete-confirm-name'); input.value=${JSON.stringify(roomName)}; input.dispatchEvent(new Event('input',{bubbles:true}));`);
    assert.equal(await cdp.evaluate("document.querySelector('#sr-delete-submit').disabled"), false,
                 "matching room name did not enable delete confirmation");
    await cdp.evaluate("document.querySelector('#sr-delete-submit').click();");
    await until(async () => {
      const response = await fetch(`${base}/api/rooms/${encodeURIComponent(roomId)}/messages`);
      return response.status === 404;
    }, "deleted room to disappear");
    await cdp.evaluate("document.querySelector('[data-action=\"logout\"]').click();");
    await until(async () => cdp.evaluate("!document.querySelector('#sr-login-layer').hidden && localStorage.getItem('socketroom.displayName') === null"), "guest logout and cleared profile");
    assert.equal(await cdp.evaluate("(async () => { const res = await fetch('/api/admin/me'); return res.status === 200 && (await res.json()).admin === false; })()"), true,
                 "admin session survived logout");
    console.log("PASS: Chrome guest send state, confirmed render, admin UI deletion, logout reset");
  } finally {
    if (cdp) {
      try { await cdp.send("Browser.close"); } catch (_) {}
    }
    if (ws && ws.readyState === WebSocket.OPEN) ws.close();
    if (chrome.exitCode === null) chrome.kill();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
