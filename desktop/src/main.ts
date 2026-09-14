import "./styles.css";

type Page = "chat" | "research" | "approvals" | "knowledge";
const app = document.querySelector<HTMLDivElement>("#app")!;
const state = {
  page: "chat" as Page,
  base: localStorage.getItem("kim.server") || "https://app.vishalojha.me",
  pin: localStorage.getItem("kim.pin") || "",
  messages: [] as { role: string; text: string }[],
};

const esc = (value: unknown) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]!));
const api = async (path: string, init: RequestInit = {}) => {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (state.pin) headers.set("X-Kim-Pin", state.pin);
  const response = await fetch(`${state.base}${path}`, { ...init, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.message || `Request failed (${response.status})`);
  return data;
};

function shell(content: string) {
  app.innerHTML = `<div class="shell">
    <aside class="rail">
      <div class="brand"><span class="mark"><i></i><i></i></span><span>Kim</span></div>
      <div class="eyebrow">PERSONAL AGENT</div>
      <nav>${nav("chat", "⌁", "Talk to Kim")}${nav("research", "⌕", "Research")}${nav("approvals", "✓", "Approvals")}${nav("knowledge", "▣", "Knowledge")}</nav>
      <div class="rail-bottom"><button id="settings" class="rail-action">⚙ <span>Settings</span></button><div class="connection"><span class="dot"></span><span>Cloud connected</span></div></div>
    </aside>
    <main class="main"><header><div><div class="kicker">KIM WORKSPACE</div><h1>${title()}</h1></div><div class="header-actions"><span class="pill"><span class="dot"></span> Online</span><button id="refresh" class="icon-button" title="Refresh">↻</button></div></header>${content}</main>
  </div>`;
  document.querySelectorAll<HTMLElement>("[data-page]").forEach((el) => el.onclick = () => { state.page = el.dataset.page as Page; render(); });
  document.querySelector("#settings")?.addEventListener("click", settings);
}
function nav(page: Page, icon: string, label: string) { return `<button data-page="${page}" class="nav-item ${state.page === page ? "active" : ""}"><b>${icon}</b><span>${label}</span></button>`; }
function title() { return ({ chat: "What should we do?", research: "Research with Kim", approvals: "Review requests", knowledge: "Your knowledge" }[state.page]); }

function render() {
  if (state.page === "chat") return chat();
  if (state.page === "research") return research();
  if (state.page === "approvals") return approvals();
  return knowledge();
}
function chat() {
  shell(`<section class="chat-page"><div class="hero-orb"><span class="orb"><i></i><i></i></span><div><strong>Kim is ready</strong><small>Ask, plan, research, or get something done.</small></div></div><div id="messages" class="messages">${state.messages.length ? state.messages.map((m) => `<article class="message ${m.role}"><small>${m.role === "user" ? "YOU" : "KIM"}</small><p>${esc(m.text)}</p></article>`).join("") : `<div class="empty"><span class="spark">✦</span><p>Your workspace is quiet.</p><small>Try asking Kim to prepare your day, investigate a topic, or draft an email.</small></div>`}</div><form id="chat-form" class="composer"><input id="chat-input" autocomplete="off" placeholder="Ask Kim anything…" /><button>↑</button></form><div class="suggestions"><button data-prompt="Prepare my day">Prepare my day</button><button data-prompt="Research the latest AI news">Research AI news</button><button data-prompt="Show my calendar">Show my calendar</button></div></section>`);
  document.querySelector<HTMLFormElement>("#chat-form")!.onsubmit = async (e) => { e.preventDefault(); const input = document.querySelector<HTMLInputElement>("#chat-input")!; const text = input.value.trim(); if (!text) return; input.value = ""; state.messages.push({ role: "user", text }); render(); try { const out = await api("/v1/chat", { method: "POST", body: JSON.stringify({ message: text }) }); state.messages.push({ role: "assistant", text: out.reply || out.message || JSON.stringify(out) }); } catch (error) { state.messages.push({ role: "assistant", text: `I couldn't complete that: ${(error as Error).message}` }); } render(); };
  document.querySelectorAll<HTMLButtonElement>("[data-prompt]").forEach((b) => b.onclick = () => { document.querySelector<HTMLInputElement>("#chat-input")!.value = b.dataset.prompt!; document.querySelector<HTMLFormElement>("#chat-form")!.requestSubmit(); });
}
function research() {
  shell(`<section class="panel-page"><div class="intro">Give Kim a question. It will search multiple sources, keep the evidence, and return a readable brief.</div><form id="research-form" class="research-form"><textarea id="research-input" placeholder="What should Kim investigate?"></textarea><button>Start research</button></form><div id="research-result" class="result"><div class="empty"><span class="spark">⌕</span><p>No research running.</p><small>Evidence and source links will appear here.</small></div></div></section>`);
  document.querySelector<HTMLFormElement>("#research-form")!.onsubmit = async (e) => { e.preventDefault(); const input = document.querySelector<HTMLTextAreaElement>("#research-input")!; const result = document.querySelector<HTMLDivElement>("#research-result")!; if (!input.value.trim()) return; result.innerHTML = `<div class="loading"><span class="spinner"></span> Kim is researching…</div>`; try { const job = await api("/v1/research", { method: "POST", body: JSON.stringify({ question: input.value.trim() }) }); let data; for (let i = 0; i < 60; i++) { await new Promise((r) => setTimeout(r, 1000)); data = await api(`/v1/research/${job.id}`); if (["completed", "failed"].includes(data.status)) break; } result.innerHTML = data.status === "completed" ? `<h2>Research brief</h2><pre>${esc(data.result || "No evidence returned")}</pre>` : `<div class="error">${esc(data.error || "Research failed")}</div>`; } catch (error) { result.innerHTML = `<div class="error">${esc((error as Error).message)}</div>`; } };
}
async function approvals() { shell(`<section class="panel-page"><div class="intro">Actions that can affect other people or external services wait here for your approval.</div><div id="approval-list" class="list"><div class="loading"><span class="spinner"></span> Loading approvals…</div></div></section>`); try { const data = await api("/v1/approvals"); const items = data.approvals || data || []; document.querySelector("#approval-list")!.innerHTML = items.length ? items.map((item: any) => `<article class="list-card"><div><strong>${esc(item.tool || item.action || "Requested action")}</strong><p>${esc(JSON.stringify(item.args || item.payload || {}))}</p></div><div class="card-actions"><button data-approval="${esc(item.id)}" data-action="approve">Approve</button><button class="muted" data-approval="${esc(item.id)}" data-action="reject">Reject</button></div></article>`).join("") : `<div class="empty"><p>No requests waiting.</p></div>`; } catch (error) { document.querySelector("#approval-list")!.innerHTML = `<div class="error">${esc((error as Error).message)}</div>`; } }
async function knowledge() { shell(`<section class="panel-page"><div class="intro">Private sources Kim can search across conversations and tasks.</div><div id="knowledge-list" class="list"><div class="loading"><span class="spinner"></span> Loading sources…</div></div></section>`); try { const data = await api("/v1/knowledge/sources"); const items = data.sources || data || []; document.querySelector("#knowledge-list")!.innerHTML = items.length ? items.map((item: any) => `<article class="list-card"><div><strong>${esc(item.title || item.name || "Source")}</strong><p>${esc(item.path || item.url || item.kind || "Knowledge source")}</p></div><span class="tag">${esc(item.kind || "indexed")}</span></article>`).join("") : `<div class="empty"><p>No sources indexed yet.</p><small>Ask Kim to remember a file or URL to begin.</small></div>`; } catch (error) { document.querySelector("#knowledge-list")!.innerHTML = `<div class="error">${esc((error as Error).message)}</div>`; } }
function settings() { const base = prompt("Kim server URL", state.base); if (base === null) return; const pin = prompt("Kim PIN", state.pin); if (pin === null) return; state.base = base.replace(/\/$/, ""); state.pin = pin; localStorage.setItem("kim.server", state.base); localStorage.setItem("kim.pin", state.pin); render(); }

render();
