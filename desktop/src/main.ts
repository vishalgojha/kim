import { getCurrentWindow, LogicalSize } from "@tauri-apps/api/window";
import { WebviewWindow } from "@tauri-apps/api/webviewWindow";
import "./styles.css";

type Page = "chat" | "browser" | "approvals";
const app = document.querySelector<HTMLDivElement>("#app")!;
const DEFAULT_SERVER = "https://app.vishalojha.me";
const win = getCurrentWindow();
const savedServer = localStorage.getItem("kim.server") || "";
const configVersion = localStorage.getItem("kim.server.version");
const pointsToLocalMachine = /^(https?:\/\/)?(127\.0\.0\.1|localhost)(:\d+)?$/i.test(savedServer.replace(/\/$/, ""));
if (configVersion !== "2") {
  // Version 1 pointed the desktop at the laptop-only API. The web client uses
  // the hosted Kim API, so migrate existing installs to the shared backend.
  localStorage.removeItem("kim.server");
  localStorage.setItem("kim.server.version", "2");
}
const state = {
  page: "chat" as Page,
  compact: localStorage.getItem("kim.view") !== "full",
  base: configVersion === "2" && savedServer && !pointsToLocalMachine ? savedServer : DEFAULT_SERVER,
  pin: localStorage.getItem("kim.pin") || "",
  activeUser: localStorage.getItem("kim.user") || "Vishal",
  conversationId: localStorage.getItem(`kim.conversation.${localStorage.getItem("kim.user") || "Vishal"}`) || crypto.randomUUID(),
  messages: [] as { role: string; text: string; attachmentName?: string; toolsUsed?: string[] }[],
  attachment: null as { name: string; text: string } | null,
  pending: "" as string,
  voice: "online",
};
localStorage.setItem(`kim.conversation.${state.activeUser}`, state.conversationId);
const historyKey = () => `kim.history.${state.activeUser}`;
const loadMessages = () => { try { return JSON.parse(localStorage.getItem(historyKey()) || "{}")[state.conversationId] || []; } catch { return []; } };
const saveMessage = (message: { role: string; text: string; attachmentName?: string; toolsUsed?: string[] }) => {
  state.messages.push(message);
  try { const all = JSON.parse(localStorage.getItem(historyKey()) || "{}"); all[state.conversationId] = state.messages.slice(-100); localStorage.setItem(historyKey(), JSON.stringify(all)); } catch { /* best effort */ }
};
state.messages = loadMessages();

const esc = (value: unknown) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]!));
const api = async (path: string, init: RequestInit = {}) => {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  headers.set("X-Kim-Client", "kim-desktop");
  if (state.pin) headers.set("X-Kim-Pin", state.pin);
  const response = await fetch(`${state.base}${path}`, { ...init, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.message || `Request failed (${response.status})`);
  return data;
};

const resizeWindow = async () => {
  try {
    if (state.compact) {
      await win.setMinSize(new LogicalSize(360, 118));
      await win.setSize(new LogicalSize(420, 148));
    } else {
      await win.setMinSize(new LogicalSize(900, 600));
      await win.setSize(new LogicalSize(1180, 760));
    }
  } catch {
    // Browser preview does not expose Tauri window controls.
  }
};

const syncInitialWindowMode = async () => {
  try {
    const size = await win.innerSize();
    // If the desktop window was maximized or restored large, show the workspace
    // instead of leaving the compact player floating in a large blank canvas.
    if (state.compact && size.width > 700) {
      state.compact = false;
      localStorage.setItem("kim.view", "full");
    }
  } catch {
    // Browser preview does not expose Tauri window dimensions.
  }
  await resizeWindow();
  render();
};

const toggleView = async (compact = !state.compact) => {
  state.compact = compact;
  localStorage.setItem("kim.view", compact ? "compact" : "full");
  document.body.classList.toggle("compact-mode", compact);
  await resizeWindow();
  render();
};

function shell(content: string) {
  document.body.classList.toggle("compact-mode", state.compact);
  if (state.compact) {
    mini();
    return;
  }
  app.innerHTML = `<div class="shell">
    <aside class="rail">
      <div class="brand"><span class="mark"><i></i><i></i></span><span>Kim</span></div>
      <div class="eyebrow">PERSONAL AGENT</div>
      <nav>${nav("chat", "⌁", "Talk to Kim")}${nav("browser", "◉", "Browser")}${nav("approvals", "✓", "Approvals")}</nav><button id="new-chat" class="rail-action">＋ <span>New chat</span></button>
      <div class="rail-bottom"><button id="propai-connect" class="rail-action">◈ <span>Checking PropAI…</span></button><button id="settings" class="rail-action">⚙ <span>Settings</span></button><div class="connection"><span id="connection-dot" class="dot"></span><span id="connection-label">Checking laptop relay…</span></div></div>
    </aside>
    <main class="main"><header><div><div class="kicker">KIM WORKSPACE</div><h1>${title()}</h1></div><div class="header-actions"><button id="user-switch" class="user-switch">${esc(state.activeUser)}</button><span class="pill"><span class="dot"></span> Online</span><button id="compact" class="icon-button" title="Collapse">▾</button><button id="refresh" class="icon-button" title="Refresh">↻</button></div></header>${content}</main>
  </div>`;
  document.querySelectorAll<HTMLElement>("[data-page]").forEach((el) => el.onclick = () => { state.page = el.dataset.page as Page; render(); });
  document.querySelector("#settings")?.addEventListener("click", settings);
  document.querySelector("#new-chat")?.addEventListener("click", () => { state.conversationId = crypto.randomUUID(); localStorage.setItem(`kim.conversation.${state.activeUser}`, state.conversationId); state.messages = []; state.attachment = null; state.page = "chat"; render(); });
  document.querySelector("#user-switch")?.addEventListener("click", () => { state.activeUser = state.activeUser === "Vishal" ? "Kapil" : "Vishal"; localStorage.setItem("kim.user", state.activeUser); state.conversationId = localStorage.getItem(`kim.conversation.${state.activeUser}`) || crypto.randomUUID(); localStorage.setItem(`kim.conversation.${state.activeUser}`, state.conversationId); state.messages = loadMessages(); render(); });
  document.querySelector("#propai-connect")?.addEventListener("click", () => {
    const button = document.querySelector<HTMLButtonElement>("#propai-connect");
    if (button?.dataset.connected === "true") disconnectPropAI(); else connectPropAI();
  });
  document.querySelector("#compact")?.addEventListener("click", () => toggleView(true));
  updateConnection();
  updatePropAIStatus();
}
function nav(page: Page, icon: string, label: string) { return `<button data-page="${page}" class="nav-item ${state.page === page ? "active" : ""}"><b>${icon}</b><span>${label}</span></button>`; }
function title() { return ({ chat: "What should we do?", browser: "Kim Browser", approvals: "Review requests" }[state.page]); }

function mini() {
  const last = state.messages[state.messages.length - 1];
  const subtitle = last ? last.text : "Ready when you are.";
  app.innerHTML = `<div class="mini-shell">
    <button id="expand" class="mini-orb" title="Expand Kim"><span class="orb mini"><i></i><i></i></span></button>
    <button id="wake" class="mini-control" title="Wake Kim">▶</button>
    <div class="mini-copy"><strong>Kim</strong><span>${esc(subtitle)}</span></div>
    <button id="mini-chat" class="mini-control" title="Open chat">⌁</button>
    <button id="mini-pause" class="mini-control" title="Pause listening">■</button>
  </div>`;
  document.querySelector("#expand")?.addEventListener("click", () => toggleView(false));
  document.querySelector("#mini-chat")?.addEventListener("click", () => toggleView(false));
  document.querySelector("#wake")?.addEventListener("click", () => api("/v1/control", { method: "POST", body: JSON.stringify({ action: "wake" }) }).catch(() => {}));
  document.querySelector("#mini-pause")?.addEventListener("click", () => api("/v1/control", { method: "POST", body: JSON.stringify({ action: "pause" }) }).catch(() => {}));
}

function render() {
  if (state.page === "chat") return chat();
  if (state.page === "browser") return browser();
  if (state.page === "approvals") return approvals();
  return chat();
}
function browser() {
  shell(`<section class="panel-page browser-page"><div class="intro">Open websites in Kim's own browser window. Your Kim chat stays open while you browse.</div><form id="browser-form" class="browser-form"><input id="browser-url" autocomplete="off" placeholder="https://example.com or search the web" /><button>Open browser</button></form><div class="browser-card"><strong>Browser actions</strong><p>Ask Kim to navigate, click, type, press keys, scroll, read pages, or capture a screenshot in the active browser.</p></div></section>`);
  document.querySelector<HTMLFormElement>("#browser-form")!.onsubmit = (event) => {
    event.preventDefault();
    const input = document.querySelector<HTMLInputElement>("#browser-url")!;
    const raw = input.value.trim();
    if (!raw) return;
    const url = /^(https?:\/\/)/i.test(raw) ? raw : `https://www.google.com/search?q=${encodeURIComponent(raw)}`;
    const browserWindow = new WebviewWindow(`kim-browser-${Date.now()}`, { url, title: "Kim Browser", width: 1280, height: 820, resizable: true });
    browserWindow.once("tauri://error", (event) => alert(`Browser could not open: ${String(event.payload)}`));
  };
}
function chat() {
  const renderMessage = (m: { role: string; text: string; attachmentName?: string; toolsUsed?: string[] }) => `<article class="message ${m.role}"><small>${m.role === "user" ? "YOU" : "KIM"}</small><p>${esc(m.text)}</p>${m.toolsUsed?.length ? `<details class="tool-card"><summary><span class="tool-spark">✦</span> Tool use <span class="tool-count">${m.toolsUsed.length}</span></summary><div class="tool-list">${m.toolsUsed.map((tool) => `<span>${esc(tool)}</span>`).join("")}</div></details>` : ""}${m.attachmentName ? `<small>Attached: ${esc(m.attachmentName)}</small>` : ""}</article>`;
  shell(`<section class="chat-page"><div class="hero-orb"><span class="orb"><i></i><i></i></span><div><strong>Kim is ready</strong><small>Ask Kim to act on your laptop, phone, or connected services.</small></div></div><div id="messages" class="messages">${state.messages.length ? state.messages.map(renderMessage).join("") : `<div class="empty"><span class="spark">✦</span><p>Your workspace is quiet.</p><small>Ask Kim anything, attach a file, or use a connected device.</small></div>`}${state.pending ? `<div class="agent-status" aria-live="polite"><span class="spinner"></span>${esc(state.pending)}</div>` : ""}</div>${state.attachment ? `<div class="attachment-chip">Attached: ${esc(state.attachment.name)} <button type="button" id="clear-attachment">×</button></div>` : ""}<form id="chat-form" class="composer"><button type="button" id="attach" class="composer-icon">＋</button><input id="chat-input" autocomplete="off" placeholder="Message Kim…" ${state.pending ? "disabled" : ""} /><button type="button" id="mic" class="composer-icon" ${state.pending ? "disabled" : ""}>♩</button><button type="submit" ${state.pending ? "disabled" : ""}>↑</button></form><input id="file-picker" type="file" hidden /><div class="suggestions"><button data-prompt="Prepare my day">Prepare my day</button><button data-prompt="Show my calendar">Show my calendar</button></div></section>`);
  document.querySelector<HTMLFormElement>("#chat-form")!.onsubmit = async (e) => {
    e.preventDefault();
    const input = document.querySelector<HTMLInputElement>("#chat-input")!;
    const text = input.value.trim();
    if (!text) return;
    if (!state.pin) { saveMessage({ role: "assistant", text: "Open Settings and add your Kim PIN before chatting." }); render(); return; }
    input.value = "";
    const attachment = state.attachment;
    state.attachment = null;
    saveMessage({ role: "user", text, attachmentName: attachment?.name });
    state.pending = "Kim is thinking…";
    render();
    try {
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), 120_000);
      const out = await api("/v1/chat", { method: "POST", signal: controller.signal, body: JSON.stringify({ message: text, conversation_id: state.conversationId, client: "desktop", device_context: "Linux desktop with browser, apps, files, and connected laptop relay", attachments: attachment ? [{ name: attachment.name, text: attachment.text }] : [] }) });
      window.clearTimeout(timer);
      saveMessage({ role: "assistant", text: out.reply || out.message || JSON.stringify(out), toolsUsed: Array.isArray(out.tools_used) ? out.tools_used : [] });
    } catch (error) {
      const message = (error as Error).name === "AbortError" ? "Kim timed out while waiting for the AI service." : (error as Error).message;
      saveMessage({ role: "assistant", text: `I couldn't complete that: ${message}` });
    } finally { state.pending = ""; render(); }
  };
  document.querySelector("#attach")?.addEventListener("click", () => document.querySelector<HTMLInputElement>("#file-picker")?.click());
  document.querySelector<HTMLInputElement>("#file-picker")?.addEventListener("change", (event) => { const file = (event.target as HTMLInputElement).files?.[0]; if (!file) return; const reader = new FileReader(); reader.onload = () => { state.attachment = { name: file.name, text: String(reader.result || "").slice(0, 120000) }; render(); }; reader.readAsText(file); });
  document.querySelector("#clear-attachment")?.addEventListener("click", () => { state.attachment = null; render(); });
  document.querySelector("#mic")?.addEventListener("click", () => {
    const Recognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!Recognition) { alert("Voice input is available on Android; this desktop WebView does not provide speech recognition."); return; }
    const recognition = new Recognition(); recognition.lang = "en-IN";
    recognition.onresult = (event: any) => { const input = document.querySelector<HTMLInputElement>("#chat-input"); if (input) input.value = event.results[0][0].transcript; };
    recognition.start();
  });
  document.querySelectorAll<HTMLButtonElement>("[data-prompt]").forEach((b) => b.onclick = () => { document.querySelector<HTMLInputElement>("#chat-input")!.value = b.dataset.prompt!; document.querySelector<HTMLFormElement>("#chat-form")!.requestSubmit(); });
}
function research() {
  shell(`<section class="panel-page"><div class="intro">Give Kim a question. It will search multiple sources, keep the evidence, and return a readable brief.</div><form id="research-form" class="research-form"><textarea id="research-input" placeholder="What should Kim investigate?"></textarea><button>Start research</button></form><div id="research-result" class="result"><div class="empty"><span class="spark">⌕</span><p>No research running.</p><small>Evidence and source links will appear here.</small></div></div></section>`);
  document.querySelector<HTMLFormElement>("#research-form")!.onsubmit = async (e) => { e.preventDefault(); const input = document.querySelector<HTMLTextAreaElement>("#research-input")!; const result = document.querySelector<HTMLDivElement>("#research-result")!; if (!input.value.trim()) return; result.innerHTML = `<div class="loading"><span class="spinner"></span> Kim is researching…</div>`; try { const job = await api("/v1/research", { method: "POST", body: JSON.stringify({ question: input.value.trim() }) }); let data; for (let i = 0; i < 60; i++) { await new Promise((r) => setTimeout(r, 1000)); data = await api(`/v1/research/${job.id}`); if (["completed", "failed"].includes(data.status)) break; } result.innerHTML = data.status === "completed" ? `<h2>Research brief</h2><pre>${esc(data.result || "No evidence returned")}</pre>` : `<div class="error">${esc(data.error || "Research failed")}</div>`; } catch (error) { result.innerHTML = `<div class="error">${esc((error as Error).message)}</div>`; } };
}
async function approvals() { shell(`<section class="panel-page"><div class="intro">Actions that can affect other people or external services wait here for your approval.</div><div id="approval-list" class="list"><div class="loading"><span class="spinner"></span> Loading approvals…</div></div></section>`); try { const data = await api("/v1/approvals"); const items = (data.approvals || data || []).filter((item: any) => item.status === "pending"); document.querySelector("#approval-list")!.innerHTML = items.length ? items.map((item: any) => `<article class="list-card"><div><strong>${esc(item.tool || item.action || item.name || "Requested action")}</strong><p>${esc(item.summary || JSON.stringify(item.args || item.payload || {}))}</p></div><div class="card-actions"><button data-approval="${esc(item.id)}" data-action="approve">Approve</button><button class="muted" data-approval="${esc(item.id)}" data-action="reject">Reject</button></div></article>`).join("") : `<div class="empty"><p>No requests waiting.</p></div>`; document.querySelectorAll<HTMLButtonElement>("[data-approval]").forEach((button) => button.onclick = async () => { await api(`/v1/approvals/${button.dataset.approval}/${button.dataset.action}`, { method: "POST", body: "{}" }); approvals(); }); } catch (error) { document.querySelector("#approval-list")!.innerHTML = `<div class="error">${esc((error as Error).message)}</div>`; } }
async function knowledge() { shell(`<section class="panel-page"><div class="intro">Private sources Kim can search across conversations and tasks.</div><div id="knowledge-list" class="list"><div class="loading"><span class="spinner"></span> Loading sources…</div></div></section>`); try { const data = await api("/v1/knowledge/sources"); const items = data.sources || data || []; document.querySelector("#knowledge-list")!.innerHTML = items.length ? items.map((item: any) => `<article class="list-card"><div><strong>${esc(item.title || item.name || "Source")}</strong><p>${esc(item.path || item.url || item.kind || "Knowledge source")}</p></div><span class="tag">${esc(item.kind || "indexed")}</span></article>`).join("") : `<div class="empty"><p>No sources indexed yet.</p><small>Ask Kim to remember a file or URL to begin.</small></div>`; } catch (error) { document.querySelector("#knowledge-list")!.innerHTML = `<div class="error">${esc((error as Error).message)}</div>`; } }
function settings() { const base = prompt("Kim server URL", state.base); if (base === null) return; const pin = prompt("Kim PIN", state.pin); if (pin === null) return; state.base = base.replace(/\/$/, ""); state.pin = pin; localStorage.setItem("kim.server", state.base); localStorage.setItem("kim.pin", state.pin); render(); }
async function connectPropAI() {
  try {
    let data;
    try {
      data = await api("/v1/propai/start");
    } catch (firstError) {
      // Older installs may still point at the laptop-only API. PropAI OAuth
      // belongs to the hosted control plane, so transparently migrate them.
      if (state.base === DEFAULT_SERVER) throw firstError;
      state.base = DEFAULT_SERVER;
      localStorage.setItem("kim.server", DEFAULT_SERVER);
      data = await api("/v1/propai/start");
    }
    // Keep Kim alive while PropAI/Supabase authentication runs in its own
    // WebView. The old implementation navigated the main window away, which
    // made an expired callback look like Kim had exited.
    const authWindow = new WebviewWindow("propai-auth", {
      url: data.auth_url,
      title: "Connect PropAI",
      width: 720,
      height: 820,
      resizable: true,
    });
    authWindow.once("tauri://error", (event) => {
      alert(`PropAI window could not open: ${String(event.payload)}`);
    });
  } catch (error) {
    alert(`PropAI connection could not start: ${(error as Error).message}`);
  }
}

async function updatePropAIStatus() {
  const button = document.querySelector<HTMLButtonElement>("#propai-connect");
  if (!button) return;
  try {
    const data = await api("/v1/integrations");
    const connected = Boolean(data.integrations?.propai_mcp?.connected);
    button.querySelector("span")!.textContent = connected ? "Disconnect PropAI" : "Connect PropAI";
    button.dataset.connected = connected ? "true" : "false";
    button.disabled = false;
    button.title = connected ? "PropAI MCP is connected" : "Connect your PropAI workspace";
  } catch {
    button.querySelector("span")!.textContent = "Connect PropAI";
    button.disabled = false;
  }
}

async function disconnectPropAI() {
  if (!confirm("Disconnect PropAI MCP from Kim?")) return;
  try { await api("/v1/propai/disconnect", { method: "POST", body: "{}" }); updatePropAIStatus(); }
  catch (error) { alert(`PropAI disconnect failed: ${(error as Error).message}`); }
}

async function updateConnection() {
  const label = document.querySelector<HTMLElement>("#connection-label");
  const dot = document.querySelector<HTMLElement>("#connection-dot");
  if (!label || !dot) return;
  try {
    const data = await api("/v1/device/status");
    const laptop = data.devices?.laptop;
    const online = laptop && (Date.now() / 1000 - Number(laptop.last_seen || 0)) < 15;
    label.textContent = online ? "Laptop relay connected" : "Laptop relay offline";
    dot.style.background = online ? "#63e6a1" : "#e06b75";
  } catch {
    label.textContent = "Relay status unavailable";
    dot.style.background = "#e06b75";
  }
}

window.setInterval(updateConnection, 10_000);

syncInitialWindowMode();
