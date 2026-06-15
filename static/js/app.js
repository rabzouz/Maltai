// Maltai - logique front (vanilla JS, zero dependance)
const $ = (s) => document.querySelector(s);
const api = (p, opts) => fetch(p, opts).then((r) => {
  if (r.status === 401) { location.href = "/login"; throw new Error("401"); }
  return r.json();
});

const state = {
  user: null,
  sessions: [],
  providers: [],
  ollamaModels: [],
  currentSession: null,
  providerId: null,
  model: null,
  streaming: false,
  attachments: [],   // {id, filename, kind}
  disabledTools: new Set(JSON.parse(localStorage.getItem("maltai_disabled_tools") || "[]")),
  allTools: [],      // noms de tous les outils connus
  abort: null,       // AbortController du stream en cours
};

function effectivePlan() {
  if (state.user?.is_admin) return "admin";
  return state.user?.plan || "basic";
}

function canUseAgentTools() {
  return ["premium", "admin"].includes(effectivePlan());
}

function planLabel(plan = effectivePlan()) {
  return plan === "admin" ? "Admin" : plan === "premium" ? "Premium" : "Basic";
}

function formatCredits(value) {
  if (value == null) return "∞ crédits";
  const n = Number(value) || 0;
  return `${n.toLocaleString("fr-FR")} crédits`;
}

function updateSubscriptionUI() {
  const plan = effectivePlan();
  const badge = $("#plan-badge");
  if (badge) {
    badge.textContent = planLabel(plan);
    badge.className = `plan-badge ${plan}`;
  }
  const creditBadge = $("#credit-badge");
  if (creditBadge) {
    creditBadge.textContent = formatCredits(state.user?.credit_balance);
    creditBadge.classList.toggle("unlimited", state.user?.credit_balance == null);
  }
  const agentToggle = $("#agent-mode");
  if (agentToggle) {
    agentToggle.disabled = !canUseAgentTools();
    if (!canUseAgentTools()) agentToggle.checked = false;
  }
  const status = $("#subscription-status");
  if (status) {
    status.textContent = canUseAgentTools()
      ? `Plan ${planLabel(plan)} : outils agent actifs · solde ${formatCredits(state.user?.credit_balance)}.`
      : `Plan Basic : chat actif, outils agent reserves au plan Premium · solde ${formatCredits(state.user?.credit_balance)}.`;
  }
  const adminBox = $("#subscription-admin");
  if (adminBox) adminBox.classList.toggle("hidden", !state.user?.is_admin);
  const topbarAdmin = $("#topbar-admin");
  if (topbarAdmin) topbarAdmin.classList.toggle("hidden", !state.user?.is_admin);
}

async function loadMe() {
  try {
    state.user = await api("/api/auth/me");
  } catch {
    state.user = null;
  }
  updateSubscriptionUI();
}

async function loadCredits() {
  const box = $("#credit-ledger");
  if (!box || state.user?.is_admin) {
    if (box) box.innerHTML = "";
    return;
  }
  try {
    const data = await api("/api/auth/credits");
    if (state.user) state.user.credit_balance = data.credit_balance;
    updateSubscriptionUI();
    const rows = data.ledger || [];
    box.innerHTML = rows.length ? "<h3 class=\"sub\">Dépenses récentes</h3>" : "";
    rows.slice(0, 8).forEach((r) => {
      const row = document.createElement("div");
      row.className = "credit-row";
      const when = new Date((r.created_at || 0) * 1000).toLocaleString("fr-FR");
      row.innerHTML = `<span>${esc(r.reason || "usage")} · ${esc(when)}</span>
        <strong>${Number(r.delta).toLocaleString("fr-FR")}</strong>`;
      box.appendChild(row);
    });
  } catch {}
}

async function loadSubscriptionUsers() {
  const box = $("#subscription-users");
  if (!box || !state.user?.is_admin) return;
  box.innerHTML = '<p class="hint">chargement…</p>';
  try {
    const users = await api("/api/auth/users");
    box.innerHTML = "";
    users.forEach((u) => {
      const row = document.createElement("div");
      row.className = "provider-row";
      const effective = u.is_admin ? "admin" : (u.plan || "basic");
      row.innerHTML = `<div><strong>${esc(u.username)}</strong>
        <div class="meta">${esc(planLabel(effective))} · ${esc(formatCredits(u.credit_balance))}</div></div>`;
      const actions = document.createElement("div");
      actions.className = "credit-actions";
      const sel = document.createElement("select");
      sel.className = "plan-select";
      sel.disabled = !!u.is_admin;
      ["basic", "premium"].forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p;
        opt.textContent = planLabel(p);
        opt.selected = effective === p;
        sel.appendChild(opt);
      });
      sel.onchange = async () => {
        await fetch(`/api/auth/users/${u.id}/plan`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ plan: sel.value }),
        });
        await loadSubscriptionUsers();
      };
      actions.appendChild(sel);
      if (!u.is_admin) {
        const amount = document.createElement("input");
        amount.className = "credit-input";
        amount.type = "number";
        amount.min = "0";
        amount.step = "1000";
        amount.placeholder = "crédits";
        const add = document.createElement("button");
        add.className = "section-mini-btn";
        add.textContent = "+";
        add.title = "Ajouter des crédits";
        add.onclick = async () => {
          const credits = parseInt(amount.value || "0", 10);
          if (!credits) return;
          await fetch(`/api/auth/users/${u.id}/credits`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: "add", credits }),
          });
          await loadSubscriptionUsers();
        };
        const set = document.createElement("button");
        set.className = "section-mini-btn";
        set.textContent = "=";
        set.title = "Fixer le solde";
        set.onclick = async () => {
          const credits = parseInt(amount.value || "0", 10);
          await fetch(`/api/auth/users/${u.id}/credits`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: "set", credits }),
          });
          await loadSubscriptionUsers();
        };
        actions.append(amount, add, set);
      }
      row.appendChild(actions);
      box.appendChild(row);
    });
  } catch {
    box.innerHTML = '<p class="hint">Impossible de charger les utilisateurs.</p>';
  }
}

// --- Providers -----------------------------------------------------------
async function loadProviders() {
  state.providers = await api("/api/providers");
  const sel = $("#provider-select");
  sel.innerHTML = "";
  state.providers.forEach((p) => {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = p.name;
    sel.appendChild(o);
  });
  if (state.providers.length) {
    state.providerId = state.providers[0].id;
    sel.value = state.providerId;
    await loadModels(state.providerId);
  }
  renderProviderRows();
}

async function loadModels(pid) {
  const sel = $("#model-select");
  sel.innerHTML = '<option>…</option>';
  const prov = state.providers.find((p) => p.id === pid);
  try {
    const res = await api(`/api/providers/${pid}/models`);
    sel.innerHTML = "";
    const models = res.models || [];
    if (prov && prov.model && !models.includes(prov.model)) models.unshift(prov.model);
    models.forEach((m) => {
      const o = document.createElement("option");
      o.value = m; o.textContent = m;
      sel.appendChild(o);
    });
    state.model = prov?.model && models.includes(prov.model) ? prov.model : models[0] || null;
    if (state.model) {
      sel.value = state.model;
      const lbl = $("#composer-model-name");
      if (lbl) lbl.textContent = state.model;
    }
  } catch {
    sel.innerHTML = "";
    if (prov?.model) {
      const o = document.createElement("option");
      o.value = prov.model; o.textContent = prov.model;
      sel.appendChild(o);
      state.model = prov.model;
    }
  }
}

async function refreshActiveProviderModels() {
  if (state.providerId) await loadModels(state.providerId);
}

function renderProviderRows() {
  const box = $("#provider-rows");
  box.innerHTML = "";
  if (!state.providers.length) {
    box.innerHTML = '<p class="hint">Aucun provider. Ajoutes-en un ci-dessous.</p>';
    return;
  }
  state.providers.forEach((p) => {
    const row = document.createElement("div");
    row.className = "provider-row";
    const memTag = p.embed_model ? ` · 🧠 ${esc(p.embed_model)}` : " · 🧠 off";
    row.innerHTML = `<div><strong>${esc(p.name)}</strong>
      <div class="meta">${esc(p.base_url)} · ${esc(p.model || "—")}${memTag}</div></div>`;
    const del = document.createElement("button");
    del.className = "icon-btn"; del.textContent = "🗑";
    del.onclick = async () => {
      await fetch(`/api/providers/${p.id}`, { method: "DELETE" });
      await loadProviders();
    };
    row.appendChild(del);
    box.appendChild(row);
  });
}

// --- Modeles Ollama ------------------------------------------------------
function bytes(n) {
  if (!Number.isFinite(n) || n <= 0) return "";
  const units = ["o", "Ko", "Mo", "Go", "To"];
  let v = n, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function setOllamaProgress(value, text) {
  const box = $("#ollama-progress");
  const bar = box?.querySelector("div");
  const status = $("#ollama-status");
  if (!box || !bar || !status) return;
  if (value == null) {
    box.classList.add("hidden");
    bar.style.width = "0%";
  } else {
    box.classList.remove("hidden");
    bar.style.width = `${Math.max(0, Math.min(100, value))}%`;
  }
  if (text != null) status.textContent = text;
}

async function loadOllamaModels() {
  const box = $("#ollama-rows");
  const status = $("#ollama-status");
  if (!box || !status) return;
  status.textContent = "chargement…";
  try {
    const resp = await fetch("/api/ollama/models");
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || "Ollama injoignable");
    state.ollamaModels = data.models || [];
    box.innerHTML = state.ollamaModels.length ? "" : '<p class="hint">Aucun modèle Ollama local.</p>';
    state.ollamaModels.forEach((m) => {
      const row = document.createElement("div");
      row.className = "provider-row";
      const family = m.details?.family ? ` · ${esc(m.details.family)}` : "";
      row.innerHTML = `<div><strong>${esc(m.name)}</strong>
        <div class="meta">${bytes(m.size)}${family}</div></div>`;
      const del = document.createElement("button");
      del.className = "icon-btn"; del.textContent = "🗑"; del.title = "Supprimer";
      del.onclick = async () => {
        if (!confirm(`Supprimer ${m.name} d'Ollama ?`)) return;
        const r = await fetch(`/api/ollama/models/${encodeURIComponent(m.name)}`, { method: "DELETE" });
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          alert(d.detail || "Suppression impossible");
          return;
        }
        await loadOllamaModels();
        await refreshActiveProviderModels();
      };
      row.appendChild(del);
      box.appendChild(row);
    });
    status.textContent = `Ollama : ${data.base_url} · ${state.ollamaModels.length} modèle(s)`;
  } catch (e) {
    box.innerHTML = "";
    status.textContent = `Ollama injoignable : ${e.message}`;
  }
}

async function pullOllamaModel() {
  const input = $("#ollama-model");
  const btn = $("#ollama-pull");
  const name = input.value.trim();
  if (!name) { alert("Nom du modèle requis."); return; }
  btn.disabled = true;
  setOllamaProgress(0, `Téléchargement de ${name}…`);
  try {
    const resp = await fetch("/api/ollama/pull", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!resp.ok || !resp.body) {
      const d = await resp.json().catch(() => ({}));
      throw new Error(d.detail || "Téléchargement impossible");
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const ev of events) {
        const type = ev.split("\n").find((l) => l.startsWith("event:"));
        const line = ev.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        const data = JSON.parse(line.slice(5).trim());
        if (type && type.includes("error")) throw new Error(data.message || "Erreur Ollama");
        const status = data.status || "en cours";
        const doneBytes = data.completed ?? data.current;
        if (data.total && doneBytes != null) {
          const pct = (doneBytes / data.total) * 100;
          setOllamaProgress(pct, `${status} · ${bytes(doneBytes)} / ${bytes(data.total)}`);
        } else {
          setOllamaProgress(5, status);
        }
      }
    }
    setOllamaProgress(100, `✓ ${name} téléchargé`);
    input.value = "";
    await loadOllamaModels();
    await refreshActiveProviderModels();
  } catch (e) {
    setOllamaProgress(null, `✗ ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

// --- Sessions ------------------------------------------------------------
async function loadSessions() {
  state.sessions = await api("/api/sessions");
  renderSessions();
}

function renderSessions() {
  const list = $("#session-list");
  list.innerHTML = "";
  state.sessions.forEach((s) => {
    const item = document.createElement("div");
    item.className = "session-item" + (s.id === state.currentSession ? " active" : "");
    const title = document.createElement("span");
    title.className = "title"; title.textContent = s.title;
    title.onclick = () => openSession(s.id);
    const del = document.createElement("span");
    del.className = "del"; del.textContent = "✕";
    del.onclick = async (e) => {
      e.stopPropagation();
      await fetch(`/api/sessions/${s.id}`, { method: "DELETE" });
      if (state.currentSession === s.id) { state.currentSession = null; clearMessages(); }
      await loadSessions();
    };
    item.append(title, del);
    list.appendChild(item);
  });
}

async function newSession() {
  const s = await api("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider_id: state.providerId, model: state.model }),
  });
  state.currentSession = s.id;
  await loadSessions();
  clearMessages();
  if (isMobile()) closeSidebar();
}

async function openSession(sid) {
  state.currentSession = sid;
  renderSessions();
  if (isMobile()) closeSidebar();
  const msgs = await api(`/api/sessions/${sid}/messages`);
  clearMessages();
  const rows = [];
  msgs.forEach((m) => {
    const b = addMessage(m.role, m.content);
    if (m.role === "assistant") addCopyAction(b);
    rows.push(b.closest(".msg-row"));
    if (rows[rows.length - 1]) rows[rows.length - 1].dataset.mid = m.id;
  });
  refreshRowActions();
}

// --- Messages ------------------------------------------------------------
function clearMessages() { $("#messages").innerHTML = ""; }

function addMessage(role, content) {
  const empty = $(".empty-state");
  if (empty) empty.remove();
  const row = document.createElement("div");
  row.className = `msg-row ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (role === "assistant") {
    const tag = document.createElement("div");
    tag.className = "role-tag"; tag.textContent = "Maltai";
    row.appendChild(wrap(tag, bubble));
  } else {
    row.appendChild(bubble);
  }
  if (role === "assistant") renderMarkdown(bubble, content);
  else bubble.textContent = content;
  $("#messages").appendChild(row);
  scrollDown();
  return bubble;
}

function wrap(tag, bubble) {
  const d = document.createElement("div");
  d.appendChild(tag); d.appendChild(bubble);
  return d;
}

function scrollDown() {
  const m = $("#messages");
  m.scrollTop = m.scrollHeight;
}

// --- Helpers UI ---
function setStatus(msg) {
  const bar = document.getElementById("status-bar");
  if (bar) { bar.textContent = msg; bar.style.opacity = msg ? "1" : "0"; }
}

// --- Chat streaming ------------------------------------------------------
async function send(contentOverride) {
  const input = $("#input");
  const content = (typeof contentOverride === "string" ? contentOverride : input.value).trim();
  if (!content || state.streaming) return;
  if (!state.providerId || !state.model) { alert("Configure un provider et un modèle."); return; }
  if ($("#agent-mode").checked && !canUseAgentTools()) {
    alert("Plan premium requis pour utiliser les outils de l'agent.");
    return;
  }
  if (!state.currentSession) await newSession();

  const attachedNames = state.attachments.filter((a) => a.id).map((a) => a.filename);
  input.value = ""; input.style.height = "auto";
  addMessage("user", content + (attachedNames.length ? `\n📎 ${attachedNames.join(", ")}` : ""));
  const bubble = addMessage("assistant", "");
  bubble.innerHTML = '<span class="thinking-dots"><span></span><span></span><span></span></span>';
  bubble.classList.add("typing");
  setStatus("Maltai réfléchit…");
  state.streaming = true;
  const sendBtn = $("#send");
  sendBtn.classList.add("stop"); sendBtn.textContent = "■"; sendBtn.title = "Arrêter";
  state.abort = new AbortController();

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      signal: state.abort.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: state.currentSession,
        provider_id: state.providerId,
        model: state.model,
        content,
        agent: $("#agent-mode").checked,
        attachment_ids: state.attachments.filter((a) => a.id).map((a) => a.id),
        enabled_tools: $("#agent-mode").checked ? enabledToolsParam() : null,
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `Erreur ${resp.status}`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "", acc = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const ev of events) {
        const line = ev.split("\n").find((l) => l.startsWith("data:"));
        const type = ev.split("\n").find((l) => l.startsWith("event:"));
        if (!line) continue;
        const data = JSON.parse(line.slice(5).trim());
        if (type && type.includes("memory")) {
          addToolLine(`🧠 ${data.count} souvenir(s) rappelé(s) du contexte passé`, bubble);
        } else if (type && type.includes("tool_result")) {
          setStatus("Maltai répond…");
          finishToolCard(data.name, String(data.result));
        } else if (type && type.includes("tool")) {
          setStatus(`⚙ Outil : ${data.name}…`);
          const card = document.createElement("div");
          // inserer la carte AVANT la bulle de reponse en cours
          const row = bubble.closest(".msg-row");
          addToolCard(data.name, String(data.arguments));
          row.parentNode.insertBefore(row.parentNode.lastChild, row);
        } else if (type && type.includes("error")) {
          acc += `\n\n⚠ ${data.message}`;
          renderMarkdown(bubble, acc);
        } else if (type && type.includes("done")) {
          if (data.usage && state.user && data.usage.balance !== undefined) {
            state.user.credit_balance = data.usage.balance;
            updateSubscriptionUI();
            loadCredits();
          }
        } else if (data.content) {
          if (!acc) { bubble.innerHTML = ""; setStatus("Maltai répond…"); }
          acc += data.content;
          renderMarkdown(bubble, acc);
        }
        scrollDown();
      }
    }
    bubble.classList.remove("typing"); setStatus("");
  } catch (e) {
    bubble.classList.remove("typing"); setStatus("");
    if (e.name === "AbortError") {
      renderMarkdown(bubble, (bubble.textContent || "") + "\n\n⏹ *Génération interrompue*");
    } else {
      bubble.textContent += `\n\n⚠ Erreur réseau : ${e.message}`;
    }
  } finally {
    state.streaming = false; state.abort = null;
    const sb = $("#send");
    sb.classList.remove("stop"); sb.textContent = "↑"; sb.title = "Envoyer"; sb.disabled = false;
    state.attachments = []; renderChips();
    addCopyAction(bubble);
    tagMessageRows();
    loadSessions();
  }
}

// --- Helpers / events ----------------------------------------------------
function esc(s) { const d = document.createElement("div"); d.textContent = s ?? ""; return d.innerHTML; }

function autoGrow(el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 200) + "px"; }

function escHtml(s) { const d = document.createElement("div"); d.textContent = s ?? ""; return d.innerHTML; }

function addToolLine(html, beforeBubble, asHtml) {
  const line = document.createElement("div");
  line.className = "tool-line";
  if (asHtml) line.innerHTML = html; else line.textContent = html;
  const row = beforeBubble.closest(".msg-row");
  row.parentNode.insertBefore(line, row);
  scrollDown();
}

// --- Rendu Markdown -------------------------------------------------------
marked.setOptions({ breaks: true, gfm: true });

function renderMarkdown(el, text) {
  const raw = marked.parse(text || "");
  el.innerHTML = DOMPurify.sanitize(raw, { ADD_ATTR: ["target"] });
  el.querySelectorAll("a").forEach((a) => { a.target = "_blank"; a.rel = "noopener"; });
  el.querySelectorAll("pre code").forEach((c) => {
    try { hljs.highlightElement(c); } catch {}
  });
  el.querySelectorAll("pre").forEach((pre) => {
    if (pre.querySelector(".code-copy")) return;
    const btn = document.createElement("button");
    btn.className = "code-copy"; btn.textContent = "copier";
    btn.onclick = () => {
      navigator.clipboard.writeText(pre.querySelector("code")?.innerText || "");
      btn.textContent = "✓"; setTimeout(() => (btn.textContent = "copier"), 1200);
    };
    pre.appendChild(btn);
  });
}

// --- Drawer mobile -------------------------------------------------------
function isMobile() { return window.matchMedia("(max-width: 760px)").matches; }

function syncViewportHeight() {
  const h = window.visualViewport?.height || window.innerHeight;
  document.documentElement.style.setProperty("--vvh", `${Math.round(h)}px`);
}

function openSidebar() {
  $("#sidebar").classList.remove("collapsed");
  $("#overlay").classList.add("show");
}
function closeSidebar() {
  $("#sidebar").classList.add("collapsed");
  $("#overlay").classList.remove("show");
}
function toggleSidebar() {
  if ($("#sidebar").classList.contains("collapsed")) openSidebar();
  else closeSidebar();
}

// --- Panneau d'outils -------------------------------------------------------
function saveToolPrefs() {
  localStorage.setItem("maltai_disabled_tools", JSON.stringify([...state.disabledTools]));
}

async function loadToolsPanel(sel) {
  const list = $(sel || "#tools-list");
  list.innerHTML = '<p class="hint">chargement…</p>';
  try {
    const d = await api("/api/tools");
    if (!d.can_use_tools) {
      state.allTools = [];
      list.innerHTML = `<p class="hint">${esc(d.upgrade_message || "Plan premium requis.")}</p>`;
      return;
    }
    state.allTools = [...d.native.map((t) => t.name), ...d.mcp.map((t) => t.name)];
    list.innerHTML = "";
    const addOpt = (t, badge) => {
      const lbl = document.createElement("label");
      lbl.className = "tool-opt";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !state.disabledTools.has(t.name);
      cb.onchange = () => {
        if (cb.checked) state.disabledTools.delete(t.name);
        else state.disabledTools.add(t.name);
        saveToolPrefs();
      };
      const info = document.createElement("div");
      info.innerHTML = `<div class="t-name">${esc(t.name)}${badge}</div>
        <div class="t-desc">${esc(t.description || "")}</div>`;
      lbl.append(cb, info);
      list.appendChild(lbl);
    };
    const sep1 = document.createElement("div");
    sep1.className = "tools-sep"; sep1.textContent = "Outils natifs";
    list.appendChild(sep1);
    d.native.forEach((t) => addOpt(t, t.admin_only ? ' <span class="badge">admin</span>' : ""));
    if (d.mcp.length) {
      const sep2 = document.createElement("div");
      sep2.className = "tools-sep"; sep2.textContent = "Serveurs MCP";
      list.appendChild(sep2);
      d.mcp.forEach((t) => addOpt(t, ' <span class="badge mcp">mcp</span>'));
    }
  } catch {
    list.innerHTML = '<p class="hint">Impossible de charger les outils.</p>';
  }
}

function enabledToolsParam() {
  if (!state.disabledTools.size) return null;        // tout actif -> pas de filtre
  return state.allTools.filter((n) => !state.disabledTools.has(n));
}

// --- Cartes d'appels d'outils -------------------------------------------------
const runningCards = [];

async function tagMessageRows() {
  if (!state.currentSession) return;
  try {
    const msgs = await api(`/api/sessions/${state.currentSession}/messages`);
    const rows = [...document.querySelectorAll(".msg-row")];
    msgs.forEach((m, i) => { if (rows[i]) rows[i].dataset.mid = m.id; });
    refreshRowActions();
  } catch {}
}

function refreshRowActions() {
  const rows = [...document.querySelectorAll(".msg-row")];
  // ✎ modifier sur les messages user
  rows.filter((r) => r.classList.contains("user") && r.dataset.mid).forEach((r) => {
    if (r.querySelector(".msg-actions")) return;
    const bar = document.createElement("div");
    bar.className = "msg-actions edit-bar";
    const ed = document.createElement("button");
    ed.textContent = "✎ modifier";
    ed.onclick = () => editMessage(r);
    bar.appendChild(ed);
    r.querySelector(".bubble").after(bar);
  });
  // ↻ regenerer uniquement sur la DERNIERE reponse assistant
  document.querySelectorAll(".regen-btn").forEach((b) => b.remove());
  const lastAssistant = rows.filter((r) => r.classList.contains("assistant")).pop();
  if (lastAssistant) {
    const bar = lastAssistant.querySelector(".msg-actions");
    if (bar && !bar.querySelector(".regen-btn")) {
      const rg = document.createElement("button");
      rg.className = "regen-btn"; rg.textContent = "↻ régénérer";
      rg.onclick = regenerate;
      bar.appendChild(rg);
    }
  }
}

async function regenerate() {
  if (state.streaming || !state.currentSession) return;
  try {
    const r = await fetch(`/api/sessions/${state.currentSession}/regenerate-prep`, { method: "POST" });
    if (!r.ok) return;
    const d = await r.json();
    await openSession(state.currentSession);   // re-rend l'historique tronque
    send(d.content);
  } catch {}
}

async function editMessage(row) {
  if (state.streaming) return;
  const mid = row.dataset.mid;
  const text = row.querySelector(".bubble").innerText.replace(/\n📎 .*$/s, "").replace(/\n\[fichiers joints : [^\]]*\]$/, "");
  if (!confirm("Modifier ce message supprimera la suite de la conversation. Continuer ?")) return;
  const r = await fetch(`/api/sessions/${state.currentSession}/truncate`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: mid }),
  });
  if (!r.ok) return;
  await openSession(state.currentSession);
  const input = $("#input");
  input.value = text;
  autoGrow(input);
  input.focus();
}

function addCopyAction(bubble) {
  if (!bubble || bubble.closest(".msg-row.user")) return;
  const wrap = bubble.parentNode;
  if (!wrap || wrap.querySelector(".msg-actions")) return;
  const bar = document.createElement("div");
  bar.className = "msg-actions";
  const copy = document.createElement("button");
  copy.textContent = "copier";
  copy.onclick = () => {
    navigator.clipboard.writeText(bubble.innerText || "");
    copy.textContent = "✓"; setTimeout(() => (copy.textContent = "copier"), 1200);
  };
  bar.appendChild(copy);
  wrap.appendChild(bar);
}

function addToolCard(name, argsPreview) {
  const wrap = document.createElement("div");
  wrap.className = "tool-card";
  wrap.innerHTML = `
    <div class="tool-card-inner">
      <div class="tool-card-head">
        <span class="t-status spin">◌</span>
        <span class="t-tool">${esc(name)}</span>
        <span class="t-preview">${esc(argsPreview)}</span>
        <span class="t-chev">▸</span>
      </div>
      <div class="tool-card-body">
        <div class="lbl">Arguments</div><pre class="t-args">${esc(argsPreview)}</pre>
        <div class="lbl">Résultat</div><pre class="t-result">en cours…</pre>
      </div>
    </div>`;
  wrap.querySelector(".tool-card-head").onclick = () => wrap.classList.toggle("open");
  $("#messages").appendChild(wrap);
  runningCards.push({ name, el: wrap });
  scrollDown();
}

function finishToolCard(name, result) {
  const idx = runningCards.findIndex((c) => c.name === name);
  if (idx === -1) return;
  const { el } = runningCards.splice(idx, 1)[0];
  const st = el.querySelector(".t-status");
  st.classList.remove("spin");
  st.textContent = result.startsWith("Erreur") || result.startsWith("[erreur") ? "✗" : "✓";
  el.querySelector(".t-result").textContent = result;
}

// --- Pieces jointes --------------------------------------------------------
function renderChips() {
  const box = $("#attach-chips");
  box.innerHTML = "";
  state.attachments.forEach((a, i) => {
    const chip = document.createElement("span");
    chip.className = "chip" + (a.uploading ? " uploading" : "");
    const icon = a.kind === "image" ? "🖼" : a.kind === "pdf" ? "📄" : "📝";
    chip.innerHTML = `${icon} ${esc(a.filename)} `;
    const x = document.createElement("span");
    x.className = "x"; x.textContent = "✕";
    x.onclick = () => { state.attachments.splice(i, 1); renderChips(); };
    chip.appendChild(x);
    box.appendChild(chip);
  });
}

async function uploadFiles(files) {
  for (const f of files) {
    const placeholder = { filename: f.name, kind: "", uploading: true };
    state.attachments.push(placeholder); renderChips();
    const form = new FormData();
    form.append("file", f);
    try {
      const r = await fetch("/api/upload", { method: "POST", body: form });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "upload");
      Object.assign(placeholder, d, { uploading: false });
    } catch (e) {
      state.attachments = state.attachments.filter((a) => a !== placeholder);
      alert(`Échec upload ${f.name} : ${e.message}`);
    }
    renderChips();
  }
}

async function loadWorkspace() {
  const box = $("#ws-rows");
  if (!box) return;
  try {
    const files = await api("/api/workspace");
    box.innerHTML = files.length ? "" : '<p class="hint">Workspace vide.</p>';
    files.forEach((f) => {
      const row = document.createElement("div");
      row.className = "provider-row";
      const kb = (f.size / 1024).toFixed(1);
      row.innerHTML = `<a class="ws-link" href="/api/workspace/download?path=${encodeURIComponent(f.path)}" download>${esc(f.path)}</a><span class="meta">${kb} Ko</span>`;
      box.appendChild(row);
    });
  } catch {}
}

// --- Connecteurs (Telegram + cles API) ------------------------------------
async function loadConnectors() {
  try {
    const cfg = await api("/api/telegram/config");
    $("#tg-chats").value = (cfg.allowed_chat_ids || []).join(", ");
    $("#tg-agent").checked = !!cfg.agent;
    $("#tg-enabled").checked = !!cfg.enabled;
    $("#tg-token").placeholder = cfg.has_token
      ? "Jeton enregistré (laisser vide pour conserver)"
      : "Jeton du bot (@BotFather)";
  } catch {}
  try {
    const keys = await api("/api/external/keys");
    const box = $("#key-rows");
    box.innerHTML = keys.length ? "" : '<p class="hint">Aucune clé.</p>';
    keys.forEach((k) => {
      const row = document.createElement("div");
      row.className = "provider-row";
      row.innerHTML = `<div><strong>${esc(k.name)}</strong> <span class="meta">${esc(k.preview)}</span></div>`;
      const del = document.createElement("button");
      del.className = "icon-btn"; del.textContent = "🗑";
      del.onclick = async () => {
        await fetch(`/api/external/keys/${encodeURIComponent(k.name)}`, { method: "DELETE" });
        await loadConnectors();
      };
      row.appendChild(del);
      box.appendChild(row);
    });
  } catch {}
}

// --- Serveurs MCP --------------------------------------------------------
async function loadMcpServers() {
  const box = $("#mcp-rows");
  if (!box) return;
  const servers = await api("/api/mcp");
  box.innerHTML = "";
  if (!servers.length) {
    box.innerHTML = '<p class="hint">Aucun serveur MCP configuré.</p>';
    return;
  }
  servers.forEach((s) => {
    const row = document.createElement("div");
    row.className = "provider-row";
    const info = document.createElement("div");
    info.innerHTML = `<strong>${esc(s.name)}</strong> ${s.enabled ? "" : "· <em>désactivé</em>"}
      <div class="meta">${esc(s.url)}${s.has_token ? " · 🔑" : ""}</div>
      <div class="meta mcp-status" id="mcp-status-${s.id}"></div>`;
    const btns = document.createElement("div");
    btns.style.display = "flex";

    const test = document.createElement("button");
    test.className = "icon-btn"; test.textContent = "🔌"; test.title = "Tester la connexion";
    test.onclick = async () => {
      const st = document.getElementById(`mcp-status-${s.id}`);
      st.textContent = "test…";
      try {
        const r = await fetch(`/api/mcp/${s.id}/tools`);
        const d = await r.json();
        if (!r.ok) { st.textContent = `✗ ${d.detail || "erreur"}`; return; }
        st.textContent = `✓ ${d.tools.length} outil(s) : ` + d.tools.map((t) => t.name).join(", ").slice(0, 120);
      } catch { st.textContent = "✗ injoignable"; }
    };

    const toggle = document.createElement("button");
    toggle.className = "icon-btn"; toggle.textContent = s.enabled ? "⏸" : "▶";
    toggle.title = s.enabled ? "Désactiver" : "Activer";
    toggle.onclick = async () => {
      await fetch(`/api/mcp/${s.id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !s.enabled }),
      });
      await loadMcpServers();
    };

    const del = document.createElement("button");
    del.className = "icon-btn"; del.textContent = "🗑";
    del.onclick = async () => {
      await fetch(`/api/mcp/${s.id}`, { method: "DELETE" });
      await loadMcpServers();
    };

    btns.append(test, toggle, del);
    row.append(info, btns);
    box.appendChild(row);
  });
}

async function refreshMemoryStatus() {
  try {
    const m = await fetch("/api/memory").then((r) => r.json());
    const el = $("#mem-status");
    if (!m.enabled) { el.textContent = "Mémoire désactivée (MEMORY_ENABLED=false)."; return; }
    el.textContent = `${m.count} souvenir(s) mémorisé(s) · top-${m.top_k} rappelés par message. ` +
      "Nécessite un modèle d'embeddings configuré sur le provider.";
  } catch { /* ignore */ }
}

function bindEvents() {
  $("#new-chat").onclick = newSession;
  $("#send").onclick = () => {
    if (state.streaming && state.abort) { state.abort.abort(); return; }
    send();
  };
  $("#tools-btn").onclick = (e) => {
    e.stopPropagation();
    const panel = $("#tools-panel");
    const opening = panel.classList.contains("hidden");
    panel.classList.toggle("hidden");
    if (opening) loadToolsPanel();
  };
  document.addEventListener("click", (e) => {
    const panel = $("#tools-panel");
    if (!panel.classList.contains("hidden") && !panel.contains(e.target) && e.target.id !== "tools-btn") {
      panel.classList.add("hidden");
    }
  });
  $("#tools-all").onclick = () => { state.disabledTools.clear(); saveToolPrefs(); loadToolsPanel(); };
  $("#tools-none").onclick = () => { state.allTools.forEach((n) => state.disabledTools.add(n)); saveToolPrefs(); loadToolsPanel(); };


  // --- Deep Research ---------------------------------------------------------
  let drInitialized = false;
  function initDeepResearch() {
    if (drInitialized) return;
    drInitialized = true;
    const btn    = $("#dr-start-btn");
    const input  = $("#dr-input");
    const status = $("#dr-status");
    const result = $("#dr-result");
    if (!btn || !input) return;

    async function runResearch() {
      const topic = input.value.trim();
      if (!topic) return;
      btn.disabled = true;
      status.className = "dr-status";
      status.innerHTML = '<span class="dr-spinner"></span> Analyse du sujet…';
      result.className = "dr-result hidden";
      result.innerHTML = "";

      try {
        // Call via agent tool endpoint
        const r = await fetch("/api/tool/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            tool: "deep_research",
            args: { topic },
            model: state.model,
            provider: state.provider,
          }),
        });
        if (!r.ok) throw new Error(await r.text());
        const d = await r.json();
        status.innerHTML = "✓ Rapport prêt";
        result.className = "dr-result";
        result.innerHTML = markdownToHtml(d.result || d.output || "");
      } catch(e) {
        status.innerHTML = "⚠ Erreur : " + e.message;
      } finally {
        btn.disabled = false;
      }
    }

    btn.onclick = runResearch;
    input.onkeydown = (e) => { if (e.key === "Enter") runResearch(); };
  }

  function markdownToHtml(md) {
    return md
      .replace(/^## (.+)/gm, "<h3>$1</h3>")
      .replace(/^# (.+)/gm, "<h2>$1</h2>")
      .replace(/^### (.+)/gm, "<h4>$1</h4>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      .replace(/^- (.+)/gm, "<li>$1</li>")
      .replace(/(<li>.*<\/li>)/gs, "<ul>$1</ul>")
      .replace(/\n{2,}/g, "</p><p>")
      .replace(/^(?!<[h|u|l])/gm, "")
      .trim();
  }

  // --- Notes & Taches --------------------------------------------------------
  async function loadNotesPanel(kind) {
    const list = $(kind === "todo" ? "#todos-list" : "#notes-list");
    list.innerHTML = '<p class="hint">chargement…</p>';
    try {
      const items = await api(`/api/notes?kind=${kind}`);
      list.innerHTML = items.length ? "" : '<p class="hint">— vide —</p>';
      items.forEach((n) => {
        const row = document.createElement("div");
        row.className = "panel-item" + (n.done ? " done" : "");
        if (kind === "todo") {
          const cb = document.createElement("input");
          cb.type = "checkbox";
          cb.checked = !!n.done;
          cb.onchange = async () => { await api(`/api/notes/${n.id}/toggle`, { method: "PATCH" }); loadNotesPanel("todo"); };
          row.appendChild(cb);
        }
        const txt = document.createElement("span");
        txt.className = "p-text";
        txt.textContent = n.content;
        const del = document.createElement("button");
        del.className = "p-del";
        del.textContent = "\u2715";
        del.title = "Supprimer";
        del.onclick = async () => { await api(`/api/notes/${n.id}`, { method: "DELETE" }); loadNotesPanel(kind); };
        row.append(txt, del);
        list.appendChild(row);
      });
    } catch {
      list.innerHTML = '<p class="hint">Erreur de chargement.</p>';
    }
  }
  async function addNoteFromInput(kind) {
    const input = $(kind === "todo" ? "#todo-input" : "#note-input");
    const content = input.value.trim();
    if (!content) return;
    await api("/api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, kind }),
    });
    input.value = "";
    loadNotesPanel(kind);
  }
  // notes/todos now wired via nav items
  $("#note-add-btn").onclick = () => addNoteFromInput("note");
  $("#todo-add-btn").onclick = () => addNoteFromInput("todo");
  $("#note-input").addEventListener("keydown", (e) => { if (e.key === "Enter") addNoteFromInput("note"); });
  $("#todo-input").addEventListener("keydown", (e) => { if (e.key === "Enter") addNoteFromInput("todo"); });

  // --- Terminal admin -------------------------------------------------------
  let terminalInitialized = false;
  let filesInitialized = false;
  let currentFilesPath = ".";
  let filesParentPath = "";
  const terminalHistory = [];
  let terminalHistoryIndex = 0;

  async function terminalApi(path, opts = {}) {
    const r = await fetch(path, opts);
    if (r.status === 401) {
      location.href = "/login";
      throw new Error("401");
    }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `Erreur ${r.status}`);
    return d;
  }

  function setConsoleTab(tab) {
    document.querySelectorAll("[data-console-tab]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.consoleTab === tab);
    });
    $("#console-terminal-pane")?.classList.toggle("hidden", tab !== "terminal");
    $("#console-files-pane")?.classList.toggle("hidden", tab !== "files");
    if (tab === "files") initFilesPanel();
    if (tab === "terminal") setTimeout(() => $("#terminal-input")?.focus(), 0);
  }

  function appendTerminal(text) {
    const out = $("#terminal-output");
    if (!out) return;
    out.textContent += text;
    out.scrollTop = out.scrollHeight;
  }

  async function runTerminalCommand(command) {
    const input = $("#terminal-input");
    const cmd = (command || input?.value || "").trim();
    if (!cmd) return;
    if (input) input.value = "";
    terminalHistory.push(cmd);
    terminalHistoryIndex = terminalHistory.length;
    appendTerminal(`$ ${cmd}\n`);
    try {
      const d = await terminalApi("/api/terminal/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: cmd }),
      });
      appendTerminal(`[exit ${d.exit_code ?? "timeout"}]\n${d.output || ""}\n`);
      if (d.expanded && d.expanded !== d.command) appendTerminal(`# ${d.expanded}\n`);
      appendTerminal("\n");
    } catch (e) {
      appendTerminal(`Erreur: ${e.message}\n\n`);
    }
  }

  function fileDirname(path) {
    const clean = (path || "").replace(/\\/g, "/").replace(/^\/+/, "");
    const idx = clean.lastIndexOf("/");
    return idx > 0 ? clean.slice(0, idx) : ".";
  }

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes)) return "";
    if (bytes < 1024) return `${bytes} o`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} Ko`;
    return `${(bytes / 1024 / 1024).toFixed(1)} Mo`;
  }

  function setFileStatus(text, kind = "") {
    const el = $("#file-status");
    if (!el) return;
    el.textContent = text || "";
    el.className = `file-status ${kind}`.trim();
  }

  function renderFiles(items) {
    const list = $("#files-list");
    list.innerHTML = "";
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "hint";
      empty.textContent = "Dossier vide.";
      list.appendChild(empty);
      return;
    }
    items.forEach((item) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = `file-row ${item.type}`;
      const badge = document.createElement("span");
      badge.className = "file-badge";
      badge.textContent = item.type === "dir" ? "DIR" : "FILE";
      const name = document.createElement("span");
      name.className = "file-name";
      name.textContent = item.name;
      const meta = document.createElement("span");
      meta.className = "file-meta";
      meta.textContent = item.type === "dir" ? "dossier" : formatBytes(item.size);
      row.append(badge, name, meta);
      row.onclick = () => {
        if (item.type === "dir") loadFiles(item.path);
        else openWorkspaceFile(item.path);
      };
      list.appendChild(row);
    });
  }

  async function loadFiles(path = currentFilesPath) {
    setFileStatus("Chargement...");
    try {
      const d = await terminalApi(`/api/terminal/files?path=${encodeURIComponent(path || ".")}`);
      currentFilesPath = d.path || ".";
      filesParentPath = d.parent || ".";
      $("#files-path").textContent = currentFilesPath === "." ? "/" : `/${currentFilesPath}`;
      renderFiles(d.items || []);
      setFileStatus("");
    } catch (e) {
      setFileStatus(e.message, "error");
    }
  }

  async function openWorkspaceFile(path) {
    setFileStatus("Lecture...");
    try {
      const d = await terminalApi(`/api/terminal/file?path=${encodeURIComponent(path)}`);
      $("#file-path").value = d.path;
      $("#file-content").value = d.content || "";
      setFileStatus(`${d.path} ouvert (${formatBytes(d.size)})`, "ok");
    } catch (e) {
      setFileStatus(e.message, "error");
    }
  }

  async function saveWorkspaceFile() {
    const path = $("#file-path").value.trim();
    if (!path) {
      setFileStatus("Chemin de fichier requis.", "error");
      return;
    }
    setFileStatus("Sauvegarde...");
    try {
      const d = await terminalApi("/api/terminal/file", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, content: $("#file-content").value }),
      });
      setFileStatus(`${d.path} sauvegarde (${formatBytes(d.size)})`, "ok");
      await loadFiles(fileDirname(d.path));
    } catch (e) {
      setFileStatus(e.message, "error");
    }
  }

  function initFilesPanel() {
    if (filesInitialized) return;
    filesInitialized = true;
    $("#files-refresh").onclick = () => loadFiles(currentFilesPath);
    $("#files-up").onclick = () => loadFiles(filesParentPath || ".");
    $("#file-new").onclick = () => {
      $("#file-path").value = "";
      $("#file-content").value = "";
      setFileStatus("Nouveau fichier pret.");
      $("#file-path").focus();
    };
    $("#file-save").onclick = saveWorkspaceFile;
    $("#file-content").addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        saveWorkspaceFile();
      }
    });
    loadFiles(".");
  }

  function initTerminal() {
    if (terminalInitialized) return;
    terminalInitialized = true;
    $("#terminal-output").textContent = "";
    document.querySelectorAll("[data-console-tab]").forEach((btn) => {
      btn.onclick = () => setConsoleTab(btn.dataset.consoleTab);
    });
    $("#terminal-run").onclick = () => runTerminalCommand();
    document.querySelectorAll("[data-terminal-cmd]").forEach((btn) => {
      btn.onclick = () => runTerminalCommand(btn.dataset.terminalCmd);
    });
    $("#terminal-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        runTerminalCommand();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        terminalHistoryIndex = Math.max(0, terminalHistoryIndex - 1);
        $("#terminal-input").value = terminalHistory[terminalHistoryIndex] || "";
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        terminalHistoryIndex = Math.min(terminalHistory.length, terminalHistoryIndex + 1);
        $("#terminal-input").value = terminalHistory[terminalHistoryIndex] || "";
      }
    });
    runTerminalCommand("version");
  }

  function openTerminalWindow() {
    $("#terminal-window").classList.remove("hidden");
    initTerminal();
    const activeTab = document.querySelector("[data-console-tab].active")?.dataset.consoleTab || "terminal";
    setConsoleTab(activeTab);
  }

  function openTerminalFilesWindow() {
    $("#terminal-window").classList.remove("hidden");
    initTerminal();
    setConsoleTab("files");
  }

  function closeTerminalWindow() {
    $("#terminal-window").classList.add("hidden");
  }

  function openSettingsModal() {
    $("#settings-modal").classList.remove("hidden");
    updateSubscriptionUI();
    loadCredits();
    loadSubscriptionUsers();
    refreshMemoryStatus();
    loadOllamaModels();
    loadMcpServers();
    loadConnectors();
    loadWorkspace();
  }

  function openAdminPanel() {
    openSettingsModal();
    setTimeout(() => $("#subscription-status")?.scrollIntoView({ block: "start" }), 0);
  }

  async function refreshBrainPanel() {
    const status = $("#brain-memory-status");
    if (status) status.textContent = "chargement...";
    try {
      const m = await fetch("/api/memory").then((r) => r.json());
      if (status) {
        status.textContent = m.enabled
          ? `${m.count} souvenir(s) memorise(s) · top-${m.top_k} rappeles par message.`
          : "Memoire desactivee (MEMORY_ENABLED=false).";
      }
    } catch {
      if (status) status.textContent = "Memoire indisponible.";
    }
    loadToolsPanel("#brain-tools-preview");
  }

  function refreshModelsPanel() {
    const status = $("#models-status");
    const list = $("#models-list");
    if (!status || !list) return;
    const providerCount = state.providers.length;
    const modelCount = state.ollamaModels.length;
    status.textContent = `${providerCount} provider(s) · ${modelCount} modele(s) Ollama local(aux).`;
    list.innerHTML = "";
    state.providers.forEach((p) => {
      const row = document.createElement("div");
      row.className = "panel-item";
      row.innerHTML = `<span>${esc(p.name)}<br><small>${esc(p.model || "modele non defini")}</small></span>`;
      list.appendChild(row);
    });
    if (!state.providers.length) list.innerHTML = '<p class="hint">Aucun provider configure.</p>';
  }

  async function refreshModelsPanelData() {
    try { await loadOllamaModels(); } catch {}
    refreshModelsPanel();
  }

  function openComposerTools() {
    const panel = $("#tools-panel");
    const opening = panel.classList.contains("hidden");
    panel.classList.toggle("hidden");
    if (opening) loadToolsPanel();
  }

  // --- Recherche dans les discussions ---------------------------------------
  $("#session-search").addEventListener("input", (e) => {
    const q = e.target.value.toLowerCase();
    document.querySelectorAll("#session-list .session-item").forEach((el) => {
      const t = el.querySelector(".title");
      el.style.display = !q || (t && t.textContent.toLowerCase().includes(q)) ? "" : "none";
    });
  });
  // --- Sidebar toggle -------------------------------------------------------
  const sideToggle = $("#sidebar-toggle");
  if (sideToggle) sideToggle.onclick = toggleSidebar;
  const topbarMenu = $("#topbar-menu");
  if (topbarMenu) topbarMenu.onclick = openSidebar;
  const topbarAdmin = $("#topbar-admin");
  if (topbarAdmin) topbarAdmin.onclick = openAdminPanel;
  $("#overlay").onclick = closeSidebar;

  // --- Nouveau Chat ---------------------------------------------------------
  $("#new-chat").onclick = newSession;

  // --- Nav items ------------------------------------------------------------
  const SUBPANELS = {
    discussions: { el: "#subpanel-discussions", load: () => {} },
    brain:       { el: "#subpanel-brain",       load: () => refreshBrainPanel() },
    email:       { el: "#subpanel-email",       load: () => {} },
    models:      { el: "#subpanel-models",      load: () => refreshModelsPanelData() },
    tools:       { el: "#subpanel-tools",       load: () => loadToolsPanel("#side-tools-list") },
    notes:       { el: "#subpanel-notes",        load: () => loadNotesPanel("note") },
    tasks:       { el: "#subpanel-tasks",        load: () => loadNotesPanel("todo") },
    research:    { el: "#subpanel-research",     load: () => initDeepResearch() },
  };

  function activateNav(id) {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
    const btn = $(`#nav-${id}`);
    if (btn) btn.classList.add("active");
    // Hide all subpanels
    document.querySelectorAll(".subpanel").forEach((p) => p.classList.add("hidden"));
    // Show relevant subpanel
    if (SUBPANELS[id]) {
      const panel = $(SUBPANELS[id].el);
      if (panel) {
        panel.classList.remove("hidden");
        SUBPANELS[id].load();
      }
    }
    if (isMobile() && id !== "chat") openSidebar();
  }

  $("#nav-chat").onclick        = () => activateNav("chat");
  $("#nav-discussions").onclick = () => activateNav("discussions");
  $("#nav-brain").onclick       = () => activateNav("brain");
  $("#nav-email").onclick       = () => activateNav("email");
  $("#nav-models").onclick      = () => activateNav("models");
  $("#nav-tools").onclick       = () => activateNav("tools");
  $("#nav-terminal").onclick    = openTerminalWindow;
  $("#nav-notes").onclick       = () => activateNav("notes");
  $("#nav-tasks").onclick       = () => activateNav("tasks");
  $("#nav-research").onclick    = () => activateNav("research");
  $("#nav-skills").onclick      = () => {
    activateNav("chat");
    const inp = $("#input");
    if (inp) { inp.value = ""; }
    // Could show skills list — placeholder for now
  };
  $("#nav-theme").onclick       = () => {
    activateNav("chat");
    openSettingsModal();
  };

  const brainSettings = $("#brain-settings");
  if (brainSettings) brainSettings.onclick = openSettingsModal;
  const brainRefresh = $("#brain-refresh");
  if (brainRefresh) brainRefresh.onclick = refreshBrainPanel;
  const emailSettings = $("#email-settings");
  if (emailSettings) emailSettings.onclick = openSettingsModal;
  const modelsSettings = $("#models-settings");
  if (modelsSettings) modelsSettings.onclick = openSettingsModal;
  const modelsRefresh = $("#models-refresh");
  if (modelsRefresh) modelsRefresh.onclick = refreshModelsPanelData;

  const composerSearch = $("#composer-search");
  if (composerSearch) composerSearch.onclick = () => activateNav("research");
  const composerTerminal = $("#composer-terminal");
  if (composerTerminal) composerTerminal.onclick = openTerminalWindow;
  const composerFiles = $("#composer-files");
  if (composerFiles) composerFiles.onclick = openTerminalFilesWindow;
  const composerTools = $("#composer-tools");
  if (composerTools) composerTools.onclick = (e) => { e.stopPropagation(); openComposerTools(); };
  const composerModels = $("#composer-models");
  if (composerModels) composerModels.onclick = () => activateNav("models");

  // --- Footer buttons -------------------------------------------------------
  const footNotes = $("#foot-notes");
  if (footNotes) footNotes.onclick = () => activateNav("notes");
  const footTasks = $("#foot-tasks");
  if (footTasks) footTasks.onclick = () => activateNav("tasks");

  const railLogout = $("#rail-logout");
  if (railLogout) railLogout.onclick = async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    location.href = "/login";
  };

  // --- Sidebar resize handle ------------------------------------------------
  const resizeHandle = $("#sidebar-resize-handle");
  if (resizeHandle) {
    let resizing = false, startX = 0, startW = 0;
    resizeHandle.addEventListener("mousedown", (e) => {
      resizing = true; startX = e.clientX; startW = $("#sidebar").offsetWidth;
      document.body.style.userSelect = "none"; e.preventDefault();
    });
    document.addEventListener("mousemove", (e) => {
      if (!resizing) return;
      const newW = Math.max(160, Math.min(420, startW + (e.clientX - startX)));
      $("#sidebar").style.width = newW + "px";
    });
    document.addEventListener("mouseup", () => {
      if (resizing) { resizing = false; document.body.style.userSelect = ""; }
    });
  }

  // --- Settings & misc ------------------------------------------------------
  // open-settings now in sidebar footer
  const openSettingsBtn = $("#open-settings");
  if (openSettingsBtn) openSettingsBtn.onclick = openSettingsModal;
  $("#attach-btn").onclick = () => $("#file-input").click();
  $("#file-input").addEventListener("change", (e) => {
    uploadFiles([...e.target.files]); e.target.value = "";
  });
  $("#input").addEventListener("focus", () => {
    syncViewportHeight();
    setTimeout(() => $("#input").scrollIntoView({ block: "nearest" }), 120);
  });
  const _logout = $("#logout");
  if (_logout) _logout.onclick = async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    location.href = "/login";
  };
  $("#tg-save").onclick = async () => {
    const msg = $("#tg-msg");
    msg.textContent = "enregistrement…";
    const r = await fetch("/api/telegram/config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: $("#tg-token").value.trim(),
        public_url: $("#tg-url").value.trim(),
        allowed_chat_ids: $("#tg-chats").value.split(",").map((s) => s.trim()).filter(Boolean),
        agent: $("#tg-agent").checked,
        enabled: $("#tg-enabled").checked,
      }),
    });
    const d = await r.json().catch(() => ({}));
    msg.textContent = r.ok
      ? ($("#tg-enabled").checked ? "✓ Bot activé, webhook configuré" : "✓ Enregistré (bot désactivé)")
      : `✗ ${d.detail || "erreur"}`;
    if (r.ok) $("#tg-token").value = "";
  };
  $("#key-add").onclick = async () => {
    const name = $("#key-name").value.trim();
    if (!name) { alert("Donne un nom à la clé."); return; }
    const r = await fetch("/api/external/keys", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { alert(d.detail || "Erreur"); return; }
    $("#key-msg").textContent = `Clé "${d.name}" (copie-la, elle ne sera plus montrée) : ${d.key}`;
    $("#key-name").value = "";
    await loadConnectors();
  };
  $("#m-add").onclick = async () => {
    const name = $("#m-name").value.trim();
    const url = $("#m-url").value.trim();
    if (!name || !url) { alert("Nom et URL requis."); return; }
    const r = await fetch("/api/mcp", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, url, auth_token: $("#m-token").value.trim() }),
    });
    if (!r.ok) { const d = await r.json().catch(() => ({})); alert(d.detail || "Erreur"); return; }
    ["#m-name", "#m-url", "#m-token"].forEach((s) => ($(s).value = ""));
    await loadMcpServers();
  };
  $("#ollama-refresh").onclick = loadOllamaModels;
  $("#ollama-pull").onclick = pullOllamaModel;
  $("#ollama-model").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); pullOllamaModel(); }
  });
  $("#mem-clear").onclick = async () => {
    if (!confirm("Effacer toute la mémoire vectorielle ? Cette action est irréversible.")) return;
    const r = await fetch("/api/memory", { method: "DELETE" }).then((x) => x.json());
    await refreshMemoryStatus();
    $("#mem-status").textContent = `${r.removed} souvenir(s) effacé(s).`;
  };
  $("#cp-save").onclick = async () => {
    const msg = $("#cp-msg");
    msg.textContent = "";
    const r = await fetch("/api/auth/change-password", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_password: $("#cp-current").value,
        new_password: $("#cp-new").value,
      }),
    });
    const d = await r.json().catch(() => ({}));
    msg.textContent = r.ok ? "✓ Mot de passe modifié" : (d.detail || "Erreur");
    if (r.ok) { $("#cp-current").value = ""; $("#cp-new").value = ""; }
  };
  $("#close-settings").onclick = () => $("#settings-modal").classList.add("hidden");
  $("#terminal-close").onclick = closeTerminalWindow;
  // Click hors du modal-box pour fermer
  $("#settings-modal").addEventListener("click", (e) => {
    if (e.target === $("#settings-modal")) $("#settings-modal").classList.add("hidden");
  });
  // Echap pour fermer
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#settings-modal").classList.contains("hidden")) {
      $("#settings-modal").classList.add("hidden");
    } else if (e.key === "Escape" && !$("#terminal-window").classList.contains("hidden")) {
      closeTerminalWindow();
    }
  });

  $("#input").addEventListener("input", (e) => autoGrow(e.target));
  const inputEl = $("#input");
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  inputEl.addEventListener("input", () => autoGrow(inputEl));
  // hauteur initiale
  autoGrow(inputEl);

  $("#provider-select").onchange = async (e) => {
    state.providerId = e.target.value;
    await loadModels(state.providerId);
  };
  $("#model-select").onchange = (e) => {
    state.model = e.target.value;
    const lbl = $("#composer-model-name");
    if (lbl) lbl.textContent = e.target.value || "—";
  };

  $("#p-add").onclick = async () => {
    const name = $("#p-name").value.trim();
    const base_url = $("#p-url").value.trim();
    if (!name || !base_url) { alert("Nom et Base URL requis."); return; }
    await fetch("/api/providers", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name, base_url,
        api_key: $("#p-key").value.trim(),
        model: $("#p-model").value.trim(),
        embed_model: $("#p-embed").value.trim(),
      }),
    });
    ["#p-name", "#p-url", "#p-key", "#p-model", "#p-embed"].forEach((s) => ($(s).value = ""));
    await loadProviders();
  };
}

// --- Init ----------------------------------------------------------------
function applyInitialLayout() {
  syncViewportHeight();
  // Sur mobile le drawer demarre ferme ; sur desktop il reste ouvert.
  if (isMobile()) closeSidebar();
  else { $("#sidebar").classList.remove("collapsed"); $("#overlay").classList.remove("show"); }
}

window.addEventListener("resize", syncViewportHeight);
window.addEventListener("orientationchange", () => setTimeout(syncViewportHeight, 250));
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", syncViewportHeight);
  window.visualViewport.addEventListener("scroll", syncViewportHeight);
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/static/sw.js").catch(() => {});
  });
}

(async function init() {
  try { bindEvents(); } catch(e) { console.error('bindEvents error:', e); }
  applyInitialLayout();
  window.addEventListener("resize", applyInitialLayout);
  await loadMe();
  await loadCredits();
  await loadProviders();
  await loadSessions();
})();
