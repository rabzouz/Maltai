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
  toolMeta: {},       // metadata des outils natifs
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
  const providerForm = $("#provider-form");
  if (providerForm) providerForm.classList.toggle("hidden", !state.user?.is_admin);
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
      const planOptions = u.is_admin ? ["admin"] : ["basic", "premium"];
      sel.disabled = !!u.is_admin;
      if (u.is_admin) sel.title = "Compte administrateur : accès complet, usage non facturé.";
      planOptions.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p;
        opt.textContent = planLabel(p);
        opt.selected = effective === p;
        sel.appendChild(opt);
      });
      sel.onchange = async () => {
        const previous = effective;
        const r = await fetch(`/api/auth/users/${u.id}/plan`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ plan: sel.value }),
        });
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          alert(d.detail || `Erreur ${r.status}`);
          sel.value = previous;
          return;
        }
        if (state.user?.id === u.id) await loadMe();
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
          const r = await fetch(`/api/auth/users/${u.id}/credits`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: "add", credits }),
          });
          if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            alert(d.detail || `Erreur ${r.status}`);
            return;
          }
          await loadSubscriptionUsers();
        };
        const set = document.createElement("button");
        set.className = "section-mini-btn";
        set.textContent = "=";
        set.title = "Fixer le solde";
        set.onclick = async () => {
          const credits = parseInt(amount.value || "0", 10);
          const r = await fetch(`/api/auth/users/${u.id}/credits`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: "set", credits }),
          });
          if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            alert(d.detail || `Erreur ${r.status}`);
            return;
          }
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

// --- Pricing -------------------------------------------------------------
const PRICING_DISMISS_PREFIX = "maltai_pricing_dismissed_";

function pricingDismissKey() {
  return `${PRICING_DISMISS_PREFIX}${state.user?.id || "anonymous"}`;
}

function closePricingModal(remember = true) {
  const modal = $("#pricing-modal");
  if (!modal) return;
  modal.classList.add("hidden");
  if (remember && state.user?.id) localStorage.setItem(pricingDismissKey(), "1");
}

function defaultPricingOffers() {
  return [
    {
      id: "premium_monthly",
      name: "Premium mensuel",
      price: "9,99 EUR / mois",
      description: "Outils Agent, scraping web, browser, fichiers, PDF, code sandbox et crédits inclus.",
      mode: "subscription",
      credits: 100000,
      configured: true,
    },
    {
      id: "premium_yearly",
      name: "Premium annuel",
      price: "99 EUR / an",
      description: "Premium pendant un an avec tarif réduit et crédits annuels inclus.",
      mode: "subscription",
      credits: 1200000,
      configured: true,
    },
    {
      id: "credits_100k",
      name: "Pack 100 000 crédits",
      price: "5 EUR",
      description: "Recharge ponctuelle de crédits pour exécuter plus d'outils et d'agents.",
      mode: "payment",
      credits: 100000,
      configured: true,
    },
  ];
}

function renderPricingCards(offers) {
  const box = $("#pricing-cards");
  if (!box) return;
  const items = (offers && offers.length ? offers : defaultPricingOffers()).slice(0, 3);
  box.innerHTML = "";
  items.forEach((offer) => {
    const card = document.createElement("article");
    card.className = `pricing-card ${offer.id === "premium_monthly" ? "featured" : ""}`;
    const credits = Number(offer.credits || 0);
    const creditLabel = credits
      ? `${credits.toLocaleString("fr-FR")} crédits`
      : "Crédits suivis";
    const billingLabel = offer.mode === "subscription" ? "Abonnement" : "Achat ponctuel";
    card.innerHTML = `
      <div class="pricing-card-top">
        <span>${esc(billingLabel)}</span>
        ${offer.id === "premium_monthly" ? "<strong>Recommandé</strong>" : ""}
      </div>
      <h3>${esc(offer.name)}</h3>
      <div class="pricing-price">${esc(offer.price)}</div>
      <p>${esc(offer.description)}</p>
      <ul>
        <li>Outils Agent actifs</li>
        <li>Scraping, browser et fichiers</li>
        <li>${esc(creditLabel)}</li>
      </ul>
      <a class="btn-primary pricing-card-btn" href="/billing">Choisir</a>
    `;
    if (!offer.configured) {
      card.querySelector(".pricing-card-btn").classList.add("disabled");
      card.querySelector(".pricing-card-btn").textContent = "Bientôt";
      card.querySelector(".pricing-card-btn").removeAttribute("href");
    }
    box.appendChild(card);
  });
}

async function loadPricingCards() {
  try {
    const data = await api("/api/billing/plans");
    renderPricingCards(data.offers || []);
  } catch {
    renderPricingCards(defaultPricingOffers());
  }
}

async function maybeShowPricingModal() {
  if (!state.user || effectivePlan() !== "basic") return;
  if (localStorage.getItem(pricingDismissKey()) === "1") return;
  await loadPricingCards();
  $("#pricing-modal")?.classList.remove("hidden");
}

// --- Providers -----------------------------------------------------------
async function loadProviders() {
  state.providers = await api("/api/providers");
  const sel = $("#provider-select");
  sel.innerHTML = "";
  state.providers.forEach((p) => {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = p.premium_managed ? `${p.name} · Premium` : p.name;
    sel.appendChild(o);
  });
  if (state.providers.length) {
    state.providerId = state.providers[0].id;
    sel.value = state.providerId;
    await loadModels(state.providerId);
  } else {
    state.providerId = null;
    state.model = null;
    const modelSel = $("#model-select");
    if (modelSel) modelSel.innerHTML = "";
    const lbl = $("#composer-model-name");
    if (lbl) lbl.textContent = "modèle";
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
    const premiumTag = p.premium_managed ? " · Premium géré" : "";
    row.innerHTML = `<div><strong>${esc(p.name)}</strong>
      <div class="meta">${esc(p.base_url)} · ${esc(p.model || "—")}${memTag}${premiumTag}</div></div>`;
    const del = document.createElement("button");
    del.className = "icon-btn"; del.textContent = "🗑";
    if (p.premium_managed) {
      del.disabled = true;
      del.title = "Provider Premium configure par l'administrateur";
    } else {
      del.onclick = async () => {
        await fetch(`/api/providers/${p.id}`, { method: "DELETE" });
        await loadProviders();
      };
    }
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

function formatUsageNumber(value) {
  return Number(value || 0).toLocaleString("fr-FR");
}

function addUsageFooter(bubble, usage) {
  if (!bubble || !usage) return;
  const wrap = bubble.parentElement;
  if (!wrap) return;
  wrap.querySelector(".usage-footer")?.remove();
  const input = Number(usage.input_tokens || 0);
  const output = Number(usage.output_tokens || 0);
  const total = Number(usage.total_tokens || input + output);
  const spent = Number(usage.credits_spent || 0);
  const footer = document.createElement("div");
  footer.className = "usage-footer";
  const model = usage.model ? String(usage.model) : state.model || "modèle";
  const provider = usage.provider ? String(usage.provider) : "";
  const balance = usage.balance == null ? "∞" : formatUsageNumber(usage.balance);
  const billingBits = usage.balance == null
    ? "<span>Admin · usage non facturé</span>"
    : `<span>-${formatUsageNumber(spent)} crédits</span><span>solde ${esc(balance)}</span>`;
  footer.innerHTML = `
    <span>${esc(provider ? `${provider} · ${model}` : model)}</span>
    <span>${usage.agent ? "Agent" : "Chat"}</span>
    <span>${formatUsageNumber(input)} in</span>
    <span>${formatUsageNumber(output)} out</span>
    <span>${formatUsageNumber(total)} tokens</span>
    ${billingBits}
  `;
  bubble.after(footer);
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
  if (state.attachments.some((a) => a.uploading)) {
    setStatus("Upload de la pièce jointe en cours… attends la fin avant d'envoyer.");
    return;
  }
  if (!state.currentSession) await newSession();

  const readyAttachments = state.attachments.filter((a) => a.id);
  const attachedNames = readyAttachments.map((a) => a.filename);
  const imageAttachments = readyAttachments.filter((a) => a.kind === "image");
  if (imageAttachments.length && !modelLooksVisionCapable(state.model)) {
    setStatus("Photo jointe : choisis un modèle vision pour la décrire (gpt-4o-mini, llama3.2-vision, llava…).");
  }
  input.value = ""; input.style.height = "auto";
  const userBubble = addMessage("user", content + (attachedNames.length ? `\n📎 ${attachedNames.join(", ")}` : ""));
  renderAttachmentPreview(userBubble, readyAttachments);
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
        attachment_ids: readyAttachments.map((a) => a.id),
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
          if (data.usage) addUsageFooter(bubble, data.usage);
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
  const raw = marked.parse(richMarkdown(text || ""));
  el.innerHTML = DOMPurify.sanitize(raw, { ADD_ATTR: ["target"] });
  el.querySelectorAll("a").forEach((a) => {
    repairLinkHref(a);
    a.target = "_blank";
    a.rel = "noopener";
  });
  el.querySelectorAll("img").forEach((img) => {
    img.loading = "lazy";
    img.referrerPolicy = "no-referrer";
    if (!img.alt) img.alt = "Image";
  });
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
  renderWorkspaceDownloadsNear(el, text);
}

function richMarkdown(text) {
  return normalizeMalformedMarkdownLinks(String(text || ""))
    .split(/(```[\s\S]*?```|`[^`\n]+`)/g)
    .map((chunk) => {
      if (!chunk || chunk.startsWith("`")) return chunk;
      return chunk.replace(/(^|[\s(])((https?:\/\/[^\s<>()]+))/g, (m, lead, url, offset) => {
        if (lead === "(" && chunk[offset - 1] === "]") return m;
        const clean = url.replace(/[.,;:!?]+$/, "");
        const tail = url.slice(clean.length);
        if (/\.(png|jpe?g|webp|gif|avif)(\?.*)?$/i.test(clean)) {
          return `${lead}![Image](${clean})${tail}`;
        }
        return `${lead}[${clean}](${clean})${tail}`;
      });
    })
    .join("");
}

function normalizeMalformedMarkdownLinks(text) {
  return text
    // Corrige: [titre]([https://site](https://site))
    .replace(/\]\(\[?(https?:\/\/[^\]\s)]+)\]?\((https?:\/\/[^)\s\\]+)\\?\)\)/g, "]($2)")
    // Corrige: [titre]( [https://site](https://site) )
    .replace(/\]\(\s*\[?(https?:\/\/[^\]\s)]+)\]?\((https?:\/\/[^)\s\\]+)\\?\)\s*\)/g, "]($2)");
}

function repairLinkHref(a) {
  const attr = a.getAttribute("href") || "";
  let decoded = attr;
  try { decoded = decodeURIComponent(attr); } catch {}
  decoded = decoded.replace(/\\\)/g, ")").replace(/\\$/g, "");
  const urls = [...decoded.matchAll(/https?:\/\/[^\[\]\s)\\]+/g)].map((m) =>
    m[0].replace(/[.,;:!?]+$/, "")
  );
  if (!urls.length) return;
  const fixed = urls[urls.length - 1];
  if (fixed && fixed !== attr) a.setAttribute("href", fixed);
}

function renderWorkspaceDownloadsNear(el, text) {
  const links = workspaceDownloadLinks(text);
  el.parentElement?.querySelector(".tool-downloads.from-message")?.remove();
  if (!links) return;
  links.classList.add("from-message");
  el.after(links);
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

const DIRECT_TOOL_EXAMPLES = {
  read_file: { path: "video_ltx2_3_i2v.json" },
  list_files: { path: "." },
  write_file: { path: "notes/exemple.txt", content: "Bonjour depuis Maltai." },
  pdf_read: { path: "exports/rapport-demo.pdf", max_chars: 8000 },
  pdf_create: {
    path: "exports/rapport-demo.pdf",
    title: "Rapport Maltai",
    content: "# Rapport Maltai\n\nCe PDF a ete cree depuis l'outil pdf_create.\n\n## Points cles\n- Lecture PDF avec pdf_read\n- Creation PDF telechargeable\n- Workspace isole par utilisateur",
    author: "MaltaiAI",
    page_size: "A4"
  },
  docx_read: { path: "exports/rapport-demo.docx", max_chars: 8000 },
  docx_create: {
    path: "exports/rapport-demo.docx",
    title: "Rapport Word Maltai",
    content: "# Rapport Word Maltai\n\nDocument cree avec docx_create.\n\n## Points cles\n- Lecture DOCX\n- Creation DOCX\n- Export telechargeable",
    author: "MaltaiAI"
  },
  xlsx_read: { path: "exports/tableau-demo.xlsx", max_rows: 20, max_cols: 10 },
  xlsx_create: {
    path: "exports/tableau-demo.xlsx",
    sheet: "Demo",
    rows: [["Nom", "Prix", "Stock"], ["Jet ski", 120, 3], ["Bouee", 20, 8]]
  },
  zip_create: { path: "exports/demo.zip", files: ["exports/rapport-demo.docx", "exports/tableau-demo.xlsx"], max_files: 50 },
  zip_extract: { path: "exports/demo.zip", dest: "imports/demo", overwrite: true },
  context_compress: {
    mode: "auto",
    max_chars: 1200,
    text: "{\"url\":\"https://example.com\",\"title\":\"Example\",\"links\":[{\"text\":\"More information\",\"url\":\"https://iana.org/domains/example\"}],\"logs\":[\"INFO start\",\"WARNING large output\",\"ERROR demo line\"]}"
  },
  code_execute: { code: "print('Bonjour Maltai')\nprint(2 + 2)" },
  web_search: { query: "actualité intelligence artificielle France" },
  web_fetch: { url: "https://example.com" },
  web_scrape: {
    url: "https://example.com",
    fields: {
      title: "h1",
      links: { selector: "a", attr: "href", all: true }
    },
    include: { metadata: true, headings: true, links: true, json_ld: true },
    save_as: "scrape-example.json",
    format: "json",
    limit: 10
  },
  web_crawl: { url: "https://example.com", max_pages: 3, max_depth: 1, save_as: "crawl-example.json" },
  seo_audit: { url: "https://example.com", save_as: "seo-example.json" },
  browser_navigate: { url: "https://example.com" },
  browser_snapshot: { url: "https://maltai.fr" },
  browser_links: {},
  browser_form_list: { url: "https://example.com" },
  browser_submit: { index: 0, data: { q: "test" } },
  browser_open: { url: "https://example.com" },
  browser_click: { text: "More information" },
  browser_type: { selector: "input[name='q']", text: "test", clear: true },
  browser_screenshot: { path: "page-example.png", full_page: true },
  git_status: {},
  git_branch: {},
  git_log: { limit: 8 },
  git_diff: { stat: true },
  git_show: { ref: "HEAD", mode: "summary" },
  wikipedia: { query: "intelligence artificielle" },
  weather: { city: "Paris" },
  deep_research: { topic: "Outils agent IA pour développeurs" },
};

function directToolCostLabel(name) {
  const t = state.toolMeta[name];
  if (!t) return "—";
  const cost = Number(t.credit_cost || 0);
  return cost ? `${cost.toLocaleString("fr-FR")} crédits` : "admin";
}

function setDirectToolExample(name) {
  const select = $("#direct-tool-select");
  const args = $("#direct-tool-args");
  if (!select || !args) return;
  if (state.toolMeta[name]) select.value = name;
  const example = DIRECT_TOOL_EXAMPLES[name] || {};
  args.value = JSON.stringify(example, null, 2);
  updateDirectToolCost();
}

function updateDirectToolCost() {
  const select = $("#direct-tool-select");
  const cost = $("#direct-tool-cost");
  if (select && cost) cost.textContent = directToolCostLabel(select.value);
}

function renderDirectToolRunner(nativeTools) {
  const select = $("#direct-tool-select");
  if (!select) return;
  const previous = select.value;
  select.innerHTML = "";
  nativeTools.forEach((t) => {
    const opt = document.createElement("option");
    opt.value = t.name;
    opt.textContent = t.name;
    select.appendChild(opt);
  });
  if (previous && state.toolMeta[previous]) select.value = previous;
  else if (state.toolMeta.read_file) select.value = "read_file";
  updateDirectToolCost();
  const args = $("#direct-tool-args");
  if (args && !args.value.trim()) setDirectToolExample(select.value || "read_file");
}

async function runDirectTool() {
  const select = $("#direct-tool-select");
  const argsBox = $("#direct-tool-args");
  const result = $("#direct-tool-result");
  const btn = $("#direct-tool-run");
  if (!select || !argsBox || !result || !select.value) return;
  let args = {};
  try {
    args = argsBox.value.trim() ? JSON.parse(argsBox.value) : {};
  } catch {
    result.textContent = "JSON invalide dans les arguments.";
    result.classList.add("error");
    return;
  }
  btn.disabled = true;
  result.classList.remove("error");
  result.textContent = `Execution ${select.value}...`;
  try {
    const r = await fetch("/api/tool/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tool: select.value,
        args,
        provider: state.providerId,
        model: state.model,
      }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `Erreur ${r.status}`);
    if (d.usage && state.user && d.usage.balance !== undefined) {
      state.user.credit_balance = d.usage.balance;
      updateSubscriptionUI();
      loadCredits();
    }
    const spent = d.usage?.credits_spent ? `\n\n[credits: -${d.usage.credits_spent} | solde: ${formatCredits(d.usage.balance)}]` : "";
    const text = String(d.result || "") + spent;
    result.textContent = text;
    result.parentElement?.querySelector(".tool-downloads")?.remove();
    const links = workspaceDownloadLinks(text);
    if (links) result.after(links);
  } catch (e) {
    result.classList.add("error");
    result.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

async function loadToolsPanel(sel) {
  const list = $(sel || "#tools-list");
  list.innerHTML = '<p class="hint">chargement…</p>';
  try {
    const d = await api("/api/tools");
    if (!d.can_use_tools) {
      state.allTools = [];
      state.toolMeta = {};
      const toolsTotal = $("#tools-total");
      const toolsActive = $("#tools-active");
      const toolsState = $("#tools-state");
      if (toolsTotal) toolsTotal.textContent = "0";
      if (toolsActive) toolsActive.textContent = "0";
      if (toolsState) toolsState.textContent = "Basic";
      renderDirectToolRunner([]);
      const result = $("#direct-tool-result");
      const message = d.upgrade_message || "Plan premium requis.";
      if (result) result.textContent = message;
      list.innerHTML = `<div class="upgrade-card">
        <strong>Outils Agent réservés au Premium</strong>
        <p>${esc(message)}</p>
        <ul>
          <li>Connect : providers compatibles OpenAI, Ollama et OpenRouter.</li>
          <li>Remember : Brain, souvenirs et contexte rappelé.</li>
          <li>Search : recherche web, scraping, browser et captures.</li>
          <li>Experiment : fichiers, PDF, code sandbox et exports.</li>
        </ul>
        <a class="btn-primary" href="/billing">Voir les offres</a>
      </div>`;
      return;
    }
    state.toolMeta = {};
    d.native.forEach((t) => { state.toolMeta[t.name] = t; });
    renderDirectToolRunner(d.native);
    state.allTools = [...d.native.map((t) => t.name), ...d.mcp.map((t) => t.name)];
    const toolsTotal = $("#tools-total");
    const toolsActive = $("#tools-active");
    const toolsState = $("#tools-state");
    if (toolsTotal) toolsTotal.textContent = String(state.allTools.length);
    if (toolsActive) {
      const activeCount = state.allTools.filter((n) => !state.disabledTools.has(n)).length;
      toolsActive.textContent = String(activeCount);
    }
    if (toolsState) toolsState.textContent = effectivePlan() === "admin" ? "Admin" : "Premium";
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
        const toolsActive = $("#tools-active");
        if (toolsActive) {
          const activeCount = state.allTools.filter((n) => !state.disabledTools.has(n)).length;
          toolsActive.textContent = String(activeCount);
        }
      };
      const info = document.createElement("div");
      const cost = t.credit_cost ? ` · ${Number(t.credit_cost).toLocaleString("fr-FR")} crédits` : "";
      info.innerHTML = `<div class="t-name">${esc(t.name)}${badge}</div>
        <div class="t-desc">${esc(t.description || "")}</div>`;
      if (cost) info.querySelector(".t-desc").textContent += cost;
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

function workspaceDownloadLinks(text) {
  const paths = new Set();
  const raw = text || "";
  const pathRe = /(?:Screenshot sauvegarde\s*:\s*)?((?:browser_screenshots|notes|exports|files)\/[^\s"'<>]+\.(?:png|jpg|jpeg|webp|gif|txt|md|json|csv|html|pdf|docx|xlsx|zip))/gi;
  const apiRe = /(?:https?:\/\/[^/\s"'<>]+)?\/api\/workspace\/download\?path=([^\s"'<>)]*)/gi;
  const exportRe = /(?:https?:\/\/[^/\s"'<>]+)?\/exports\/([^\s"'<>]+\.(?:png|jpg|jpeg|webp|gif|txt|md|json|csv|html|pdf|docx|xlsx|zip))/gi;
  let m;
  while ((m = pathRe.exec(raw))) {
    paths.add(m[1].replace(/[).,;:]+$/, ""));
  }
  while ((m = apiRe.exec(raw))) {
    try {
      paths.add(decodeURIComponent(m[1]).replace(/[).,;:]+$/, ""));
    } catch {
      paths.add(m[1].replace(/[).,;:]+$/, ""));
    }
  }
  while ((m = exportRe.exec(raw))) {
    try {
      paths.add(`exports/${decodeURIComponent(m[1])}`.replace(/[).,;:]+$/, ""));
    } catch {
      paths.add(`exports/${m[1]}`.replace(/[).,;:]+$/, ""));
    }
  }
  if (!paths.size) return null;
  const wrap = document.createElement("div");
  wrap.className = "tool-downloads";
  const title = document.createElement("div");
  title.className = "lbl";
  title.textContent = "Fichiers";
  wrap.appendChild(title);
  paths.forEach((path) => {
    const a = document.createElement("a");
    a.className = "ws-link";
    const href = `/api/workspace/download?path=${encodeURIComponent(path)}`;
    a.href = href;
    a.download = path.split("/").pop();
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = `Ouvrir / télécharger ${path}`;
    a.onclick = (e) => {
      // Sur Android/PWA, l'attribut download est souvent ignore. Ouvrir le
      // fichier dans un nouvel onglet donne au navigateur son viewer PDF natif.
      if (/Android|iPhone|iPad|iPod/i.test(navigator.userAgent)) {
        e.preventDefault();
        window.open(new URL(href, location.origin).href, "_blank", "noopener");
      }
    };
    wrap.appendChild(a);
  });
  return wrap;
}

function finishToolCard(name, result) {
  const idx = runningCards.findIndex((c) => c.name === name);
  if (idx === -1) return;
  const { el } = runningCards.splice(idx, 1)[0];
  const st = el.querySelector(".t-status");
  st.classList.remove("spin");
  st.textContent = result.startsWith("Erreur") || result.startsWith("[erreur") ? "✗" : "✓";
  const resultEl = el.querySelector(".t-result");
  resultEl.textContent = result;
  const links = workspaceDownloadLinks(result);
  if (links) resultEl.after(links);
}

// --- Pieces jointes --------------------------------------------------------
function renderChips() {
  const box = $("#attach-chips");
  box.innerHTML = "";
  state.attachments.forEach((a, i) => {
    const chip = document.createElement("span");
    chip.className = "chip" + (a.uploading ? " uploading" : "") + (a.kind === "image" ? " image-chip" : "");
    const icon = a.kind === "image" ? "🖼" : a.kind === "pdf" ? "📄" : "📝";
    chip.innerHTML = `${a.previewUrl ? `<img src="${esc(a.previewUrl)}" alt="">` : icon} <span>${esc(a.filename)}</span> `;
    const x = document.createElement("span");
    x.className = "x"; x.textContent = "✕";
    x.onclick = () => { state.attachments.splice(i, 1); renderChips(); };
    chip.appendChild(x);
    box.appendChild(chip);
  });
}

async function uploadFiles(files) {
  for (const f of files) {
    const isImage = f.type && f.type.startsWith("image/");
    const placeholder = {
      filename: f.name,
      kind: isImage ? "image" : "",
      uploading: true,
      previewUrl: isImage ? URL.createObjectURL(f) : "",
    };
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

function renderAttachmentPreview(anchorEl, attachments) {
  const images = (attachments || []).filter((a) => a.kind === "image" && a.previewUrl);
  if (!images.length || !anchorEl) return;
  const wrap = document.createElement("div");
  wrap.className = "message-attachments";
  images.forEach((a) => {
    const item = document.createElement("a");
    item.className = "message-image-preview";
    item.href = a.previewUrl;
    item.target = "_blank";
    item.rel = "noopener";
    item.innerHTML = `<img src="${esc(a.previewUrl)}" alt="${esc(a.filename)}"><span>${esc(a.filename)}</span>`;
    wrap.appendChild(item);
  });
  anchorEl.after(wrap);
}

function modelLooksVisionCapable(model) {
  const m = String(model || "").toLowerCase();
  return [
    "gpt-4o", "gpt-4.1", "vision", "llava", "bakllava", "moondream",
    "minicpm-v", "qwen2-vl", "qwen2.5-vl", "gemini", "claude-3",
  ].some((x) => m.includes(x));
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
  const directToolSelect = $("#direct-tool-select");
  if (directToolSelect) directToolSelect.onchange = () => {
    updateDirectToolCost();
    setDirectToolExample(directToolSelect.value);
  };
  const directToolRun = $("#direct-tool-run");
  if (directToolRun) directToolRun.onclick = runDirectTool;
  document.querySelectorAll("[data-tool-example]").forEach((btn) => {
    btn.onclick = () => setDirectToolExample(btn.dataset.toolExample);
  });


  // --- Deep Research ---------------------------------------------------------
  let drInitialized = false;
  function initDeepResearch() {
    if (drInitialized) return;
    drInitialized = true;
    const btn    = $("#dr-start-btn");
    const input  = $("#dr-input");
    const status = $("#dr-status");
    const result = $("#dr-result");
    const actions = $("#dr-actions");
    const copyBtn = $("#dr-copy");
    const downloadBtn = $("#dr-download");
    const sendChatBtn = $("#dr-send-chat");
    let lastReport = "";
    let lastTopic = "";
    if (!btn || !input) return;
    document.querySelectorAll("[data-dr-topic]").forEach((example) => {
      example.onclick = () => {
        input.value = example.dataset.drTopic || "";
        input.focus();
      };
    });

    async function runResearch() {
      const topic = input.value.trim();
      if (!topic) return;
      btn.disabled = true;
      status.className = "dr-status";
      status.innerHTML = '<span class="dr-spinner"></span> Analyse du sujet…';
      result.className = "dr-result hidden";
      result.innerHTML = "";
      if (actions) actions.className = "dr-actions hidden";
      lastReport = "";
      lastTopic = topic;

      try {
        // Call via agent tool endpoint
        const r = await fetch("/api/tool/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            tool: "deep_research",
            args: { topic },
            model: state.model,
            provider: state.providerId,
          }),
        });
        if (!r.ok) throw new Error(await r.text());
        const d = await r.json();
        lastReport = String(d.result || d.output || "");
        status.innerHTML = "✓ Rapport prêt";
        result.className = "dr-result";
        renderMarkdown(result, lastReport);
        if (actions && lastReport) actions.className = "dr-actions";
      } catch(e) {
        status.innerHTML = "⚠ Erreur : " + e.message;
      } finally {
        btn.disabled = false;
      }
    }

    if (copyBtn) copyBtn.onclick = async () => {
      if (!lastReport) return;
      const original = copyBtn.textContent;
      try {
        await navigator.clipboard.writeText(lastReport);
        copyBtn.textContent = "Copié";
      } catch (_) {
        copyBtn.textContent = "Copie impossible";
      }
      setTimeout(() => { copyBtn.textContent = original; }, 1400);
    };

    if (downloadBtn) downloadBtn.onclick = () => {
      if (!lastReport) return;
      const slug = (lastTopic || "rapport")
        .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
        .toLowerCase().replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "").slice(0, 42) || "rapport";
      const blob = new Blob([lastReport], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `maltai-deep-research-${slug}.md`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };

    if (sendChatBtn) sendChatBtn.onclick = () => {
      if (!lastReport) return;
      fillComposerFromPanel(`Analyse ce rapport Deep Research et propose les prochaines actions :\n\n${lastReport}`);
    };

    btn.onclick = runResearch;
    input.onkeydown = (e) => { if (e.key === "Enter") runResearch(); };
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
  let processInitialized = false;
  let selectedProcessId = null;
  let processPollTimer = null;
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
    $("#console-process-pane")?.classList.toggle("hidden", tab !== "process");
    if (tab === "files") initFilesPanel();
    if (tab === "process") initProcessPanel();
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

  function isPreviewableImage(path) {
    return /\.(png|jpe?g|webp|gif)$/i.test(path || "");
  }

  function isTextLikeFile(path) {
    return /\.(txt|md|csv|json|py|js|ts|html|css|xml|ya?ml|sh|sql|log|kt|java|dart)$/i.test(path || "");
  }

  function setFileDownload(path, text = "Télécharger") {
    const status = $("#file-status");
    if (!status) return;
    status.innerHTML = "";
    status.className = "file-status ok";
    const a = document.createElement("a");
    a.className = "ws-link";
    a.href = `/api/workspace/download?path=${encodeURIComponent(path)}`;
    a.download = path.split("/").pop();
    a.textContent = `${text} ${path}`;
    status.appendChild(a);
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
    $("#file-path").value = path;
    if (isPreviewableImage(path)) {
      $("#file-content").value = "";
      setFileDownload(path, "Télécharger l'image");
      return;
    }
    if (!isTextLikeFile(path)) {
      $("#file-content").value = "";
      setFileDownload(path);
      return;
    }
    setFileStatus("Lecture...");
    try {
      const d = await terminalApi(`/api/terminal/file?path=${encodeURIComponent(path)}`);
      $("#file-path").value = d.path;
      $("#file-content").value = d.content || "";
      setFileStatus(`${d.path} ouvert (${formatBytes(d.size)})`, "ok");
    } catch (e) {
      if (String(e.message || "").includes("trop volumineux")) {
        $("#file-content").value = "";
        setFileDownload(path, "Télécharger le fichier volumineux");
      } else {
        setFileStatus(e.message, "error");
      }
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

  function processStatusLabel(p) {
    const code = p.exit_code === null || p.exit_code === undefined ? "" : ` · exit ${p.exit_code}`;
    return `${p.status || "unknown"}${code}`;
  }

  function renderProcesses(processes) {
    const list = $("#process-list");
    if (!list) return;
    list.innerHTML = "";
    if (!processes.length) {
      const empty = document.createElement("p");
      empty.className = "hint";
      empty.textContent = "Aucun process lance.";
      list.appendChild(empty);
      return;
    }
    processes.forEach((p) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = `process-row ${p.status || ""}` + (p.id === selectedProcessId ? " active" : "");
      row.innerHTML = `<span class="process-cmd">${esc(p.command || p.id)}</span>
        <span class="process-meta">pid ${esc(String(p.pid || "-"))} · ${esc(processStatusLabel(p))}</span>`;
      row.onclick = () => selectProcess(p.id);
      list.appendChild(row);
    });
  }

  async function loadProcesses() {
    try {
      const d = await terminalApi("/api/terminal/process");
      renderProcesses(d.processes || []);
    } catch (e) {
      const list = $("#process-list");
      if (list) list.innerHTML = `<p class="hint">${esc(e.message)}</p>`;
    }
  }

  async function selectProcess(id) {
    if (!id) return;
    selectedProcessId = id;
    try {
      const d = await terminalApi(`/api/terminal/process/${encodeURIComponent(id)}`);
      const title = $("#process-selected");
      const log = $("#process-log");
      if (title) title.textContent = `${d.id} · ${processStatusLabel(d)}`;
      if (log) {
        log.textContent = d.output || "";
        log.scrollTop = log.scrollHeight;
      }
      const kill = $("#process-kill");
      if (kill) kill.disabled = d.status !== "running";
      await loadProcesses();
    } catch (e) {
      const log = $("#process-log");
      if (log) log.textContent = e.message;
    }
  }

  async function startProcess() {
    const input = $("#process-command");
    const command = input?.value.trim();
    if (!command) return;
    try {
      const d = await terminalApi("/api/terminal/process/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command }),
      });
      input.value = "";
      await loadProcesses();
      await selectProcess(d.id);
    } catch (e) {
      const log = $("#process-log");
      if (log) log.textContent = e.message;
    }
  }

  function setProcessExample(name) {
    const input = $("#process-command");
    if (!input) return;
    const examples = {
      ticks: "python -c \"import time; [print('tick', i, flush=True) or time.sleep(1) for i in range(20)]\"",
      server: "python -m http.server 9000",
      ollama: "python -c \"import urllib.request; print(urllib.request.urlopen('http://10.0.1.1:11434/api/tags', timeout=10).read().decode())\"",
    };
    input.value = examples[name] || "";
    input.focus();
  }

  async function killSelectedProcess() {
    if (!selectedProcessId) return;
    try {
      const d = await terminalApi(`/api/terminal/process/${encodeURIComponent(selectedProcessId)}`, {
        method: "DELETE",
      });
      await loadProcesses();
      await selectProcess(d.id);
    } catch (e) {
      const log = $("#process-log");
      if (log) log.textContent = e.message;
    }
  }

  function initProcessPanel() {
    if (!processInitialized) {
      processInitialized = true;
      $("#process-start").onclick = startProcess;
      $("#process-refresh").onclick = async () => {
        await loadProcesses();
        if (selectedProcessId) await selectProcess(selectedProcessId);
      };
      $("#process-kill").onclick = killSelectedProcess;
      document.querySelectorAll("[data-process-example]").forEach((btn) => {
        btn.onclick = () => setProcessExample(btn.dataset.processExample);
      });
      $("#process-command").addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          startProcess();
        }
      });
    }
    loadProcesses();
    if (!processPollTimer) {
      processPollTimer = setInterval(() => {
        if ($("#terminal-window")?.classList.contains("hidden")) return;
        if (!$("#console-process-pane")?.classList.contains("hidden")) {
          loadProcesses();
          if (selectedProcessId) selectProcess(selectedProcessId);
        }
      }, 2500);
    }
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
    if (processPollTimer) {
      clearInterval(processPollTimer);
      processPollTimer = null;
    }
  }

  function closePanelWindow() {
    const win = $("#panel-window");
    if (win) win.classList.add("hidden");
    document.querySelectorAll(".subpanel").forEach((p) => p.classList.add("hidden"));
  }

  function openPanelWindow(id, title) {
    const cfg = SUBPANELS[id];
    if (!cfg) return;
    const win = $("#panel-window");
    const body = $("#panel-window-body");
    const titleEl = $("#panel-window-title");
    const panel = $(cfg.el);
    if (!win || !body || !panel) return;
    document.querySelectorAll(".subpanel").forEach((p) => {
      if (p !== panel) p.classList.add("hidden");
    });
    panel.classList.remove("hidden");
    body.appendChild(panel);
    if (titleEl) titleEl.textContent = title;
    win.dataset.panel = id;
    win.classList.remove("hidden");
    cfg.load();
    if (isMobile()) closeSidebar();
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
    const list = $("#brain-memory-list");
    const query = $("#brain-search")?.value.trim() || "";
    const role = $("#brain-role-filter")?.value || "";
    const pinnedOnly = $("#brain-pinned-filter")?.checked ? "1" : "";
    if (status) status.textContent = "chargement...";
    try {
      const m = await fetch("/api/memory").then((r) => r.json());
      const brainTotal = $("#brain-total");
      const brainTopk = $("#brain-topk");
      const brainState = $("#brain-state");
      if (brainTotal) brainTotal.textContent = m.enabled ? String(m.count || 0) : "0";
      if (brainTopk) brainTopk.textContent = m.enabled ? String(m.top_k || 0) : "0";
      if (brainState) brainState.textContent = m.enabled ? "Actif" : "Off";
      if (status) {
        status.textContent = m.enabled
          ? `${m.count} souvenir(s) memorise(s) · top-${m.top_k} rappeles par message.`
          : "Memoire desactivee (MEMORY_ENABLED=false).";
      }
      if (list) {
        const data = await api(`/api/memory/items?limit=80&q=${encodeURIComponent(query)}&role=${encodeURIComponent(role)}&pinned=${encodeURIComponent(pinnedOnly)}`);
        list.innerHTML = "";
        const items = data.items || [];
        const resultCount = $("#brain-result-count");
        if (resultCount) {
          const filterLabel = query || role || pinnedOnly ? "filtré(s)" : "chargé(s)";
          resultCount.textContent = `${items.length} souvenir(s) ${filterLabel}`;
        }
        if (!items.length) {
          list.innerHTML = '<p class="hint">Aucun souvenir.</p>';
        } else {
          items.forEach((item) => {
            const row = document.createElement("div");
            row.className = "memory-item";
            const when = new Date((item.created_at || 0) * 1000).toLocaleString("fr-FR");
            const content = String(item.content || "");
            const pinMark = item.pinned ? " épinglé ·" : "";
            row.innerHTML = `<div class="memory-meta">${esc(item.role || "memory")} ·${pinMark} ${esc(when)} · ${esc(item.id.slice(0, 8))}</div>
              <div class="memory-content">${esc(content.length > 420 ? content.slice(0, 420) + "…" : content)}</div>`;
            const pin = document.createElement("button");
            pin.className = "section-mini-btn";
            pin.textContent = item.pinned ? "désépingler" : "épingler";
            pin.onclick = async () => {
              await fetch(`/api/memory/${encodeURIComponent(item.id)}/pin`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ pinned: !item.pinned }),
              });
              await refreshBrainPanel();
            };
            const del = document.createElement("button");
            del.className = "section-mini-btn";
            del.textContent = "supprimer";
            del.onclick = async () => {
              if (!confirm("Supprimer ce souvenir ?")) return;
              await fetch(`/api/memory/${encodeURIComponent(item.id)}`, { method: "DELETE" });
              await refreshBrainPanel();
            };
            const actions = document.createElement("div");
            actions.className = "memory-actions";
            actions.append(pin, del);
            row.appendChild(actions);
            list.appendChild(row);
          });
        }
      }
    } catch {
      if (status) status.textContent = "Memoire indisponible.";
      if (list) list.innerHTML = '<p class="hint">Impossible de charger les souvenirs.</p>';
    }
    loadToolsPanel("#brain-tools-preview");
  }

  function initLibraryPanel() {
    document.querySelectorAll("[data-library-prompt]").forEach((btn) => {
      btn.onclick = () => {
        activateNav("chat");
        const inp = $("#input");
        if (!inp) return;
        inp.value = btn.dataset.libraryPrompt || "";
        inp.focus();
        autoGrow(inp);
      };
    });
  }

  function fillComposerFromPanel(prompt) {
    activateNav("chat");
    const inp = $("#input");
    if (!inp) return;
    inp.value = prompt || "";
    inp.focus();
    autoGrow(inp);
  }

  function initAgentOpsPanel() {
    document.querySelectorAll("[data-agentops-prompt]").forEach((btn) => {
      btn.onclick = () => fillComposerFromPanel(btn.dataset.agentopsPrompt || "");
    });
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
    discussions: { el: "#subpanel-discussions", title: "Discussions", load: () => {} },
    brain:       { el: "#subpanel-brain",       title: "Brain", load: () => refreshBrainPanel() },
    email:       { el: "#subpanel-email",       title: "Email", load: () => {} },
    models:      { el: "#subpanel-models",      title: "Models", load: () => refreshModelsPanelData() },
    tools:       { el: "#subpanel-tools",       title: "Tools", load: () => loadToolsPanel("#side-tools-list") },
    notes:       { el: "#subpanel-notes",       title: "Notes", load: () => loadNotesPanel("note") },
    tasks:       { el: "#subpanel-tasks",       title: "Tâches", load: () => loadNotesPanel("todo") },
    agentops:    { el: "#subpanel-agentops",    title: "AgentOps", load: () => initAgentOpsPanel() },
    research:    { el: "#subpanel-research",    title: "Deep Research", load: () => initDeepResearch() },
    skills:      { el: "#subpanel-skills",      title: "Librairie", load: () => initLibraryPanel() },
  };

  function activateNav(id) {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
    const btn = $(`#nav-${id}`);
    if (btn) btn.classList.add("active");
    if (id === "chat") {
      closePanelWindow();
      if (isMobile()) closeSidebar();
      return;
    }
    if (SUBPANELS[id]) {
      openPanelWindow(id, SUBPANELS[id].title);
    }
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
  $("#nav-agentops").onclick    = () => activateNav("agentops");
  $("#nav-research").onclick    = () => activateNav("research");
  $("#nav-skills").onclick      = () => activateNav("skills");
  $("#nav-theme").onclick       = () => {
    activateNav("chat");
    openSettingsModal();
  };

  const brainSettings = $("#brain-settings");
  if (brainSettings) brainSettings.onclick = openSettingsModal;
  const brainRefresh = $("#brain-refresh");
  if (brainRefresh) brainRefresh.onclick = refreshBrainPanel;
  const brainSearch = $("#brain-search");
  const brainSearchBtn = $("#brain-search-btn");
  if (brainSearchBtn) brainSearchBtn.onclick = refreshBrainPanel;
  if (brainSearch) brainSearch.addEventListener("keydown", (e) => {
    if (e.key === "Enter") refreshBrainPanel();
  });
  const brainRoleFilter = $("#brain-role-filter");
  if (brainRoleFilter) brainRoleFilter.onchange = refreshBrainPanel;
  const brainPinnedFilter = $("#brain-pinned-filter");
  if (brainPinnedFilter) brainPinnedFilter.onchange = refreshBrainPanel;
  const brainDeleteFiltered = $("#brain-delete-filtered");
  if (brainDeleteFiltered) brainDeleteFiltered.onclick = async () => {
    const query = $("#brain-search")?.value.trim() || "";
    const role = $("#brain-role-filter")?.value || "";
    if (!query && !role) {
      alert("Ajoute une recherche ou un filtre rôle avant de supprimer les résultats.");
      return;
    }
    if (!confirm("Supprimer tous les résultats filtrés non épinglés ?")) return;
    const d = await api(`/api/memory/filtered?q=${encodeURIComponent(query)}&role=${encodeURIComponent(role)}`, { method: "DELETE" });
    alert(`${d.removed || 0} souvenir(s) supprimé(s). Les souvenirs épinglés sont conservés.`);
    await refreshBrainPanel();
  };
  const brainClear = $("#brain-clear");
  if (brainClear) brainClear.onclick = async () => {
    if (!confirm("Effacer toute la mémoire vectorielle ? Cette action est irréversible.")) return;
    await fetch("/api/memory", { method: "DELETE" });
    await refreshBrainPanel();
  };
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
  const panelWindowClose = $("#panel-window-close");
  if (panelWindowClose) panelWindowClose.onclick = closePanelWindow;
  const pricingClose = $("#pricing-close");
  if (pricingClose) pricingClose.onclick = () => closePricingModal(true);
  const pricingLater = $("#pricing-later");
  if (pricingLater) pricingLater.onclick = () => closePricingModal(true);
  const pricingModal = $("#pricing-modal");
  if (pricingModal) {
    pricingModal.addEventListener("click", (e) => {
      if (e.target === pricingModal) closePricingModal(true);
    });
  }
  // Click hors du modal-box pour fermer
  $("#settings-modal").addEventListener("click", (e) => {
    if (e.target === $("#settings-modal")) $("#settings-modal").classList.add("hidden");
  });
  // Echap pour fermer
  document.addEventListener("keydown", (e) => {
    const pricingModalOpen = $("#pricing-modal") && !$("#pricing-modal").classList.contains("hidden");
    if (e.key === "Escape" && !$("#settings-modal").classList.contains("hidden")) {
      $("#settings-modal").classList.add("hidden");
    } else if (e.key === "Escape" && pricingModalOpen) {
      closePricingModal(true);
    } else if (e.key === "Escape" && !$("#terminal-window").classList.contains("hidden")) {
      closeTerminalWindow();
    } else if (e.key === "Escape" && !$("#panel-window").classList.contains("hidden")) {
      closePanelWindow();
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
  setTimeout(() => { maybeShowPricingModal().catch(() => {}); }, 350);
})();
