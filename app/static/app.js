"use strict";

const $ = (s) => document.querySelector(s);
const state = {
  models: [],
  conversations: [],
  current: null,        // conversation object with messages
  pending: [],          // attachments waiting to be sent: {id, filename, kind, mime, uploading}
  abort: null,
};

/* ------------------------------------------------------------------ helpers */

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {},
    ...opts,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

function renderMarkdown(text) {
  if (window.marked && window.DOMPurify) {
    return DOMPurify.sanitize(marked.parse(text, { breaks: true, gfm: true }));
  }
  const div = document.createElement("div");
  div.textContent = text;
  return `<p style="white-space:pre-wrap">${div.innerHTML}</p>`;
}

function addCopyButtons(root) {
  root.querySelectorAll("pre").forEach((pre) => {
    if (pre.querySelector(".copy-code")) return;
    const b = document.createElement("button");
    b.className = "copy-code";
    b.type = "button";
    b.textContent = "Copy";
    b.onclick = () => {
      navigator.clipboard.writeText(pre.querySelector("code")?.innerText ?? pre.innerText);
      b.textContent = "Copied";
      setTimeout(() => (b.textContent = "Copy"), 1200);
    };
    pre.appendChild(b);
  });
}

function showNotice(text) {
  const n = $("#notice");
  n.textContent = text || "";
  n.hidden = !text;
}

function fmtSize(n) {
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}

function selectedModel() {
  return state.models.find((m) => m.id === $("#model-select").value);
}

const LAST_MODEL_KEY = "lehmus.lastModel";
function rememberModel(id) { try { localStorage.setItem(LAST_MODEL_KEY, id); } catch {} }
function recallModel() { try { return localStorage.getItem(LAST_MODEL_KEY); } catch { return null; } }

/* ------------------------------------------------------------------ models */

async function loadModels(refresh = false) {
  try {
    const data = await api(`/api/models${refresh ? "?refresh=true" : ""}`);
    state.models = data.models;
    const sel = $("#model-select");
    const prev = sel.value || state.current?.model || recallModel() || data.default_model;
    sel.innerHTML = "";
    for (const m of data.models) {
      const o = document.createElement("option");
      o.value = m.id;
      o.textContent = m.display_name + (m.vision ? "  🖼" : "");
      sel.appendChild(o);
    }
    if (prev && data.models.some((m) => m.id === prev)) sel.value = prev;
    updateModelBadge();
    const problems = [...data.errors];
    if (!data.models.length) problems.unshift("No models available. Check the API keys in config.json.");
    showNotice(problems.join("\n"));
  } catch (e) {
    showNotice("Could not load models: " + e.message);
  }
}

function updateModelBadge() {
  $("#model-badge").hidden = !selectedModel()?.vision;
}

/* ------------------------------------------------------------------ conversations */

async function loadConversations() {
  const q = $("#search").value.trim();
  state.conversations = await api("/api/conversations" + (q ? `?q=${encodeURIComponent(q)}` : ""));
  renderConversationList();
}

function dayGroup(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const days = Math.floor((new Date(now.toDateString()) - new Date(d.toDateString())) / 86400000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "Previous 7 days";
  if (days < 30) return "Previous 30 days";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "long" });
}

function renderConversationList() {
  const list = $("#conv-list");
  list.innerHTML = "";
  let lastGroup = null;
  for (const c of state.conversations) {
    const g = dayGroup(c.updated_at);
    if (g !== lastGroup) {
      const l = document.createElement("div");
      l.className = "group-label";
      l.textContent = g;
      list.appendChild(l);
      lastGroup = g;
    }
    const row = document.createElement("div");
    row.className = "conv" + (state.current?.id === c.id ? " active" : "");
    row.innerHTML = `<span class="title"></span><span class="actions">
      <button type="button" data-act="rename" title="Rename">✎</button>
      <button type="button" data-act="delete" title="Delete">🗑</button></span>`;
    row.querySelector(".title").textContent = c.title;
    row.querySelector(".title").title = c.title;
    row.onclick = async (ev) => {
      const act = ev.target.dataset?.act;
      if (act === "rename") {
        const t = prompt("Rename chat", c.title);
        if (t) { await api(`/api/conversations/${c.id}`, { method: "PATCH", body: JSON.stringify({ title: t }) }); loadConversations(); }
      } else if (act === "delete") {
        if (confirm(`Delete "${c.title}"? This cannot be undone.`)) {
          await api(`/api/conversations/${c.id}`, { method: "DELETE" });
          if (state.current?.id === c.id) newChat();
          loadConversations();
        }
      } else {
        openConversation(c.id);
      }
    };
    list.appendChild(row);
  }
  if (!state.conversations.length) {
    list.innerHTML = `<div class="group-label">${$("#search").value ? "No matches" : "No chats yet"}</div>`;
  }
}

async function openConversation(id) {
  if (state.abort) return;
  state.current = await api(`/api/conversations/${id}`);
  if (state.current.model && state.models.some((m) => m.id === state.current.model)) {
    $("#model-select").value = state.current.model;
    updateModelBadge();
  }
  location.hash = id;
  renderMessages();
  renderConversationList();
  document.body.classList.remove("sidebar-open");
  $("#input").focus();
}

function newChat() {
  if (state.abort) return;
  state.current = null;
  history.replaceState(null, "", location.pathname);
  renderMessages();
  renderConversationList();
  document.body.classList.remove("sidebar-open");
  $("#input").focus();
}

/* ------------------------------------------------------------------ messages */

function attachmentsEl(files) {
  const wrap = document.createElement("div");
  wrap.className = "attachments";
  for (const f of files) {
    if (f.kind === "image") {
      const a = document.createElement("a");
      a.href = `/api/uploads/${f.id}`;
      a.target = "_blank";
      a.innerHTML = `<img alt="">`;
      a.querySelector("img").src = `/api/uploads/${f.id}`;
      a.querySelector("img").alt = f.filename;
      wrap.appendChild(a);
    } else {
      const a = document.createElement("a");
      a.className = "chip";
      a.href = `/api/uploads/${f.id}`;
      a.target = "_blank";
      a.textContent = `📄 ${f.filename} · ${fmtSize(f.size)}`;
      wrap.appendChild(a);
    }
  }
  return wrap;
}

function messageEl(m) {
  const el = document.createElement("div");
  el.className = `msg ${m.role}`;
  if (m.attachments?.length) el.appendChild(attachmentsEl(m.attachments));
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (m.role === "user") bubble.textContent = m.content;
  else { bubble.innerHTML = renderMarkdown(m.content); addCopyButtons(bubble); }
  el.appendChild(bubble);
  const meta = document.createElement("div");
  meta.className = "meta";
  if (m.role === "assistant") {
    const modelName = state.models.find((x) => x.id === m.model)?.display_name || m.model || "";
    meta.innerHTML = `<span></span><button type="button" data-act="copy">Copy</button>`;
    meta.querySelector("span").textContent = modelName;
    meta.querySelector('[data-act="copy"]').onclick = (e) => {
      navigator.clipboard.writeText(m.content);
      e.target.textContent = "Copied";
      setTimeout(() => (e.target.textContent = "Copy"), 1200);
    };
  }
  el.appendChild(meta);
  return el;
}

function renderMessages() {
  const box = $("#messages");
  box.innerHTML = "";
  const msgs = state.current?.messages || [];
  if (!msgs.length) {
    box.innerHTML = `<div class="empty"><h1>Lehmus Chat</h1>
      <p>Pick a model above and start typing. You can attach images, PDFs, Word documents and text or code files.</p></div>`;
    return;
  }
  msgs.forEach((m) => box.appendChild(messageEl(m)));
  const last = box.lastElementChild;
  if (msgs.at(-1).role === "assistant" && last) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = "Regenerate";
    b.onclick = () => send({ regenerate: true });
    last.querySelector(".meta").appendChild(b);
  }
  box.scrollTop = box.scrollHeight;
}

function nearBottom(el) { return el.scrollHeight - el.scrollTop - el.clientHeight < 120; }

/* ------------------------------------------------------------------ sending */

function setBusy(busy) {
  $("#send").hidden = busy;
  $("#stop").hidden = !busy;
  $("#model-select").disabled = busy;
  $("#new-chat").disabled = busy;
}

async function send({ regenerate = false } = {}) {
  if (state.abort) return;
  const input = $("#input");
  const text = input.value.trim();
  const model = selectedModel();
  if (!model) { showNotice("Select a model first."); return; }
  if (!regenerate && !text && !state.pending.length) return;
  if (state.pending.some((p) => p.uploading)) { showNotice("Wait for the uploads to finish."); return; }
  showNotice("");

  if (!state.current) {
    const c = await api("/api/conversations", { method: "POST", body: JSON.stringify({ model: model.id }) });
    state.current = { ...c, messages: [] };
    location.hash = c.id;
  }
  const conv = state.current;
  const attachments = regenerate ? [] : state.pending.map((p) => p.id);

  if (regenerate) {
    if (conv.messages.at(-1)?.role === "assistant") conv.messages.pop();
  } else {
    conv.messages.push({ role: "user", content: text, attachments: state.pending.map((p) => ({ ...p, size: p.size || 0 })) });
    input.value = "";
    autoGrow();
    state.pending = [];
    renderPending();
  }
  renderMessages();

  // Live assistant message
  const box = $("#messages");
  const el = document.createElement("div");
  el.className = "msg assistant";
  const bubble = document.createElement("div");
  bubble.className = "bubble cursor";
  el.appendChild(bubble);
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;

  let answer = "", reasoning = "", reasoningEl = null, errored = null;
  state.abort = new AbortController();
  setBusy(true);
  let raf = 0;
  const paint = () => {
    raf = 0;
    const stick = nearBottom(box);
    bubble.innerHTML = renderMarkdown(answer);
    if (stick) box.scrollTop = box.scrollHeight;
  };

  try {
    const res = await fetch(`/api/conversations/${conv.id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: text, model: model.id, attachments, regenerate }),
      signal: state.abort.signal,
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch {}
      throw new Error(msg);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, i);
        buf = buf.slice(i + 2);
        const ev = /^event: (.*)$/m.exec(raw)?.[1];
        const dataLine = /^data: (.*)$/m.exec(raw)?.[1];
        if (!ev || dataLine === undefined) continue;
        const data = JSON.parse(dataLine);
        if (ev === "delta") {
          answer += data;
          if (!raf) raf = requestAnimationFrame(paint);
        } else if (ev === "reasoning") {
          reasoning += data;
          if (!reasoningEl) {
            reasoningEl = document.createElement("details");
            reasoningEl.className = "reasoning";
            reasoningEl.innerHTML = "<summary>Thinking…</summary><div></div>";
            el.insertBefore(reasoningEl, bubble);
          }
          reasoningEl.querySelector("div").textContent = reasoning;
        } else if (ev === "error") {
          errored = data;
        }
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") errored = e.message;
  } finally {
    state.abort = null;
    setBusy(false);
    if (raf) cancelAnimationFrame(raf);
  }

  if (answer) conv.messages.push({ role: "assistant", content: answer, model: model.id });
  renderMessages();
  if (reasoningEl) {
    reasoningEl.querySelector("summary").textContent = "Show thinking";
    const lastMsg = $("#messages").lastElementChild;
    lastMsg?.insertBefore(reasoningEl, lastMsg.querySelector(".bubble"));
  }
  if (errored) {
    const err = messageEl({ role: "assistant", content: "" });
    err.classList.add("error");
    err.querySelector(".bubble").textContent = errored;
    err.querySelector(".meta").innerHTML = "";
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = "Retry";
    retry.onclick = () => send({ regenerate: true });
    err.querySelector(".meta").appendChild(retry);
    $("#messages").appendChild(err);
    $("#messages").scrollTop = $("#messages").scrollHeight;
  }
  loadConversations();
}

/* ------------------------------------------------------------------ attachments */

function renderPending() {
  const list = $("#attach-list");
  list.innerHTML = "";
  state.pending.forEach((p, idx) => {
    const c = document.createElement("span");
    c.className = "chip" + (p.uploading ? " uploading" : "");
    c.innerHTML = `<span></span><button type="button" aria-label="Remove">✕</button>`;
    c.querySelector("span").textContent = `${p.kind === "image" ? "🖼" : "📄"} ${p.filename}${p.uploading ? " (uploading…)" : ""}`;
    c.querySelector("button").onclick = () => { state.pending.splice(idx, 1); renderPending(); };
    list.appendChild(c);
  });
  const imgs = state.pending.some((p) => p.kind === "image");
  if (imgs && selectedModel() && !selectedModel().vision) {
    showNotice("The selected model cannot view images. Choose a model marked 🖼 to send images.");
  }
}

async function uploadFiles(files) {
  for (const file of files) {
    const placeholder = {
      filename: file.name, uploading: true, size: file.size,
      kind: file.type.startsWith("image/") ? "image" : "text",
    };
    state.pending.push(placeholder);
    renderPending();
    const fd = new FormData();
    fd.append("file", file);
    try {
      const info = await api("/api/uploads", { method: "POST", body: fd });
      Object.assign(placeholder, info, { uploading: false });
    } catch (e) {
      state.pending.splice(state.pending.indexOf(placeholder), 1);
      showNotice(e.message);
    }
    renderPending();
  }
}

/* ------------------------------------------------------------------ wiring */

function autoGrow() {
  const t = $("#input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, window.innerHeight * 0.4) + "px";
}

$("#composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
});
$("#input").addEventListener("input", autoGrow);
$("#input").addEventListener("paste", (e) => {
  const files = [...(e.clipboardData?.files || [])];
  if (files.length) { e.preventDefault(); uploadFiles(files); }
});
$("#stop").onclick = () => state.abort?.abort();
$("#new-chat").onclick = newChat;
$("#file-input").onchange = (e) => { uploadFiles([...e.target.files]); e.target.value = ""; };
$("#model-select").onchange = async () => {
  updateModelBadge();
  rememberModel($("#model-select").value);
  renderPending();
  if (state.current) {
    await api(`/api/conversations/${state.current.id}`, { method: "PATCH", body: JSON.stringify({ model: $("#model-select").value }) });
  }
};
$("#refresh-models").onclick = () => loadModels(true);
let searchTimer;
$("#search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadConversations, 200); });
$("#open-sidebar").onclick = () => document.body.classList.add("sidebar-open");
$("#close-sidebar").onclick = () => document.body.classList.remove("sidebar-open");

let dragDepth = 0;
window.addEventListener("dragenter", (e) => { if (e.dataTransfer?.types.includes("Files")) { dragDepth++; $("#drop-overlay").hidden = false; } });
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#drop-overlay").hidden = true; } });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  $("#drop-overlay").hidden = true;
  if (e.dataTransfer?.files.length) uploadFiles([...e.dataTransfer.files]);
});

(async function init() {
  await loadModels();
  await loadConversations();
  const id = location.hash.slice(1);
  if (id && state.conversations.some((c) => c.id === id)) openConversation(id);
  else $("#input").focus();
})();
