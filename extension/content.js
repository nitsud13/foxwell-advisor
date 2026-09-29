// Foxwell Advisor content script.
// Works both as an unpacked Chrome extension and as a plain <script> on the mock page.
// It watches edit fields, debounces, POSTs one change event to the local server,
// and renders the verdict in a docked panel. Only the change event leaves the page.

(() => {
  // Run once per page even if both the unpacked extension and the mock page's inline
  // <script> load this file. The DOM is shared between the page and the extension's
  // isolated world, so a marker on <html> works where a window flag would not.
  if (document.documentElement.dataset.fxAdvisor) return;
  document.documentElement.dataset.fxAdvisor = "1";

  const VERSION = "0.3.3"; // shown in the panel header so a stale extension build is obvious
  const SERVER = "http://localhost:8877";
  const DEBOUNCE_MS = 150;
  let timer = null;

  // --- field discovery -------------------------------------------------
  // Mock page: elements with data-fx-field. Real Ads Manager: generic capture.
  // Meta changes the DOM often, so instead of per-field selectors we watch every
  // editable control, derive a label from the nearest aria-label / <label> /
  // heading text, and let Jev classify the change from that label text.
  const IS_MOCK = !!document.querySelector("[data-fx-field]");
  // Verified on the real Ads Manager campaign and ad set panes (2026-09-21): inputs, comboboxes,
  // switches, radios (schedule), and listbox-style menu buttons (bid strategy, budget mode).
  // The bid strategy dropdown on the campaign pane is role=button + aria-haspopup=menu (verified 2026-09-21).
  const EDITABLE = 'input:not([type=hidden]):not([type=search]), select, textarea, [role="combobox"], [role="switch"], [role="checkbox"], [role="radio"], [role="button"][aria-haspopup="listbox"], [role="button"][aria-haspopup="menu"], [role="button"][aria-haspopup="true"]';
  // Tooltip and nav buttons also carry aria-haspopup; skip anything without a visible value or labelled as help.
  const isMenuButton = (el) => el.getAttribute("role") === "button" && el.hasAttribute("aria-haspopup");
  const SKIP_VALUE = /^(action menu|(more)+|view ads|preview|apply now|open|close|create template)$/i;
  const skipButton = (el) => isMenuButton(el) && (!clean(el.textContent) || SKIP_VALUE.test(clean(el.textContent)) || /learn more|help|info/i.test(el.getAttribute("aria-label") || ""));

  // Row selection checkboxes in the campaigns table are not settings. Their label is the
  // row text, so a campaign called "CBO ..." was read as a campaign budget toggle.
  const isRowSelector = (el) => (el.type === "checkbox" || el.getAttribute("role") === "checkbox") && el.getAttribute("role") !== "switch"
    && (!!el.closest('[role="row"], [role="gridcell"], [role="rowheader"], td, th, ._4lg0') || /select all/i.test(el.getAttribute("aria-label") || ""));

  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };

  function fields() {
    if (IS_MOCK) return [...document.querySelectorAll("[data-fx-field]")].map((el) => ({ el, name: el.dataset.fxField }));
    const out = [], seen = new Map();
    for (const el of document.querySelectorAll(EDITABLE)) {
      if (skipButton(el) || isRowSelector(el)) continue;
      if (!visible(el)) continue; // hidden duplicates (virtualized grid, hover layers) must not compete with the visible control
      const name = gridSwitchName(el) || labelFor(el);
      if (!name || name === "__skip__") continue;
      // One control per label. Two elements sharing a label with different values would
      // otherwise read as a change on every rescan.
      if (seen.has(name)) continue;
      seen.set(name, el);
      out.push({ el, name });
    }
    return out;
  }

  // The on/off switch inside a table row: its nearby text is the row's hover action bar
  // ("Edit Duplicate ..."), so name it by level and row instead.
  function gridSwitchName(el) {
    const isSwitch = el.getAttribute("role") === "switch" || (el.type === "checkbox" && el.getAttribute("role") !== "checkbox");
    if (!isSwitch || !el.closest("._4lg0")) return "";
    const row = rowOfElement(el);
    const level = LEVEL_NAME[pageInfo().level] || "Campaign";
    // No row match means we cannot say which ad this switch belongs to. A shared generic
    // label would make every switch on the page fight over one value, so do not track it.
    return row ? `${level} on/off: ${row.name}` : "__skip__";
  }

  const clean = (t) => (t || "").replace(/[\u200b-\u200d\ufeff]/g, "").replace(/\s+/g, " ").trim().slice(0, 80);

  // Collect label candidates from the control outward, then take the first one that is
  // not just the control's own value. Verified on the real Ads Manager campaign pane:
  // "Budget", "Budget mode", "Buying type", "A/B test", "On/off" all resolve.
  // First short text inside a node: a wrapper that holds "Performance goal" plus a long
  // description should yield "Performance goal", not the whole paragraph.
  function shortText(node) {
    const t = clean(node.textContent);
    if (t.length <= 60) return t;
    const w = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    let x;
    while ((x = w.nextNode())) { const s = clean(x.textContent); if (s.length >= 3 && s.length <= 60) return s; }
    return "";
  }

  function labelFor(el) {
    const val = clean(valueOf(el)).toLowerCase();
    const c = [];
    const push = (t) => { t = clean(t); if (t) c.push(t); };
    push(el.getAttribute("aria-label"));
    const byId = el.getAttribute("aria-labelledby");
    if (byId) push(byId.split(" ").map((id) => document.getElementById(id)?.textContent).join(" "));
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) push(l.textContent); }
    const wrap = el.closest("label"); if (wrap) push(wrap.textContent);
    push(el.placeholder);
    let node = el.parentElement;
    for (let i = 0; i < 10 && node; i++, node = node.parentElement) {
      // Text just above the control (e.g. "Campaign objective" above its dropdown).
      let prev = node.previousElementSibling, k = 0;
      while (prev && k < 3) { const t = shortText(prev); if (t) { push(t); break; } prev = prev.previousElementSibling; k++; }
      // First short text inside the ancestor (e.g. "How we'll bid in ad auctions." then "Campaign bid strategy").
      push(shortText(node));
      const h = node.querySelector("h1,h2,h3,h4,h5,h6,[role=heading],legend");
      if (h) push(h.textContent);
      push([...node.childNodes].filter((n) => n.nodeType === 3).map((n) => clean(n.textContent)).find(Boolean));
    }
    const good = [];
    for (let t of c) {
      // Drop the control's own value from the candidate so the label is stable across changes
      // ("Budget mode Daily budget" -> "Budget mode", "On/off Active" -> "On/off").
      if (val) t = clean(t.replace(new RegExp(val.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"), ""));
      if (!t) continue;
      const l = t.toLowerCase();
      if (/^(on|off|yes|no)$/.test(l)) continue;
      if (good.some((g) => g.toLowerCase() === l || l.includes(g.toLowerCase()) || g.toLowerCase().includes(l))) continue;
      good.push(t);
      if (good.length === 2) break;
    }
    return good.join(" / ");
  }

  // Ignore controls that are clearly not campaign settings (search boxes, filters, nav).
  const IGNORE = /search|filter|date range|column|comment|name your|campaign name|ad set name|ad name|enter your .* name|action menu|^more$|date picker|^hours$|^minutes$|^meridiem$|advertiser|payer/i;

  function valueOf(el) {
    const role = el.getAttribute("role");
    if (role === "button") return clean(el.textContent);
    if (el.type === "checkbox" || el.type === "radio" || role === "switch" || role === "checkbox" || role === "radio") {
      const on = el.checked ?? (el.getAttribute("aria-checked") === "true");
      return on ? "on" : "off";
    }
    if (el.tagName === "SELECT") return el.options[el.selectedIndex]?.text || el.value;
    if (el.getAttribute("role") === "combobox") return el.value || el.textContent.trim();
    return el.value;
  }

  function campaignContext() {
    const ctx = {};
    if (!IS_MOCK) {
      // Best effort on the real page: title of the edit pane and any learning-phase badge.
      const h = document.querySelector('[role="dialog"] h1, [role="dialog"] h2, [data-pagelet*="Edit"] h1, h1');
      if (h) ctx.name = h.textContent.trim().slice(0, 120);
      const txt = document.body.innerText.slice(0, 20000);
      if (/learning phase|learning limited/i.test(txt)) ctx.in_learning = true;
      return ctx;
    }
    document.querySelectorAll("[data-fx-ctx]").forEach((el) => {
      let v = el.type === "checkbox" ? el.checked : el.value;
      if (v === "true") v = true; if (v === "false") v = false;
      if (typeof v === "string" && v !== "" && !isNaN(v)) v = Number(v);
      ctx[el.dataset.fxCtx] = v;
    });
    return ctx;
  }

  // --- performance readers ---------------------------------------------------
  // Everything Jev learns about performance comes from what is already on the page.
  // Table: Ads Manager renders a virtualized grid whose cells are positioned by geometry,
  // not nested in rows. Match a cell to a row by vertical position and to a column by
  // overlapping the column header's horizontal span. Verified 2026-09-28.
  function pageInfo() {
    const q = new URLSearchParams(location.search);
    const level = (location.pathname.match(/\/manage\/(campaigns|adsets|ads)/) || [])[1] || "";
    const sel = q.get("selected_ad_ids") || q.get("selected_adset_ids") || q.get("selected_campaign_ids") || "";
    return { account: q.get("act") || "", level, selected: sel.split(",").filter(Boolean), date_range: q.get("date") || "" };
  }

  function tableGeometry() {
    const headers = [...document.querySelectorAll('[role="columnheader"]')].map((h) => {
      const r = h.getBoundingClientRect(); return { name: clean(h.innerText), x1: r.left, x2: r.right };
    }).filter((h) => h.name);
    const nameCol = headers.find((h) => /^(campaign|ad set|ad)$/i.test(h.name));
    if (headers.length < 3 || !nameCol) return null;
    const cells = [...document.querySelectorAll("._4lg0")].map((c) => {
      const r = c.getBoundingClientRect();
      return { text: clean(c.innerText), y: (r.top + r.bottom) / 2, x1: r.left, x2: r.right, h: r.height };
    }).filter((c) => c.h > 0 && c.text);
    return { headers, nameCol, cells };
  }

  function rowFromCells(g, y) {
    const metrics = {};
    let name = "";
    for (const c of g.cells.filter((c) => Math.abs(c.y - y) < 6)) {
      const h = g.headers.find((h) => c.x1 < h.x2 - 2 && c.x2 > h.x1 + 2);
      if (!h) continue;
      if (h === g.nameCol) name = c.text.split("\n")[0];
      else if (!/off \/ on/i.test(h.name)) metrics[h.name] = c.text;
    }
    if (!name || name === g.nameCol.name || /^results from \d+/i.test(name)) return null; // header and totals rows are cells too
    return { name, metrics };
  }

  function readTableRows() {
    const g = tableGeometry();
    if (!g) return [];
    const rows = [];
    for (const a of g.cells.filter((c) => c.x1 < g.nameCol.x2 - 2 && c.x2 > g.nameCol.x1 + 2)) {
      const row = rowFromCells(g, a.y);
      if (row && Object.keys(row.metrics).length) rows.push(row);
    }
    return rows;
  }

  // Row that contains a control (the on/off switch, a row checkbox), by vertical position.
  function rowOfElement(el) {
    const g = tableGeometry();
    if (!g || !el) return null;
    const r = el.getBoundingClientRect();
    return rowFromCells(g, (r.top + r.bottom) / 2);
  }

  // Row currently selected by its checkbox (the inline budget popover edits the selected row).
  function selectedRow() {
    const g = tableGeometry();
    if (!g) return null;
    // Row boxes only: not the on/off switches (role=switch) and not the select-all box.
    const boxes = [...document.querySelectorAll('input[type="checkbox"]')].filter((b) => b.checked && b.getAttribute("role") !== "switch"
      && !/select all/i.test(b.getAttribute("aria-label") || "") && !b.closest('[role="columnheader"]') && b.closest("._4lg0, [role=\"row\"]"));
    if (boxes.length !== 1) return null;
    const r = boxes[0].getBoundingClientRect();
    return rowFromCells(g, (r.top + r.bottom) / 2);
  }

  // Editor summary card (top right of the campaign/ad set editor): "Amount spent $248.60" etc.
  const CARD_LABELS = /^(amount spent|cost per result|website purchases?|purchases|results|purchase roas.*|roas|frequency|reach|impressions)$/i;
  function readEditorCard() {
    const out = {};
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let x;
    while ((x = w.nextNode())) {
      const t = clean(x.textContent).replace(/\s*(\.\.\.|\u2026)$/, "");
      if (!t || !CARD_LABELS.test(t)) continue;
      let box = x.parentElement;
      for (let i = 0; i < 5 && box; i++, box = box.parentElement) {
        if (box.id === "fx-advisor" || box.closest("#fx-advisor")) break;
        const leaf = [...box.querySelectorAll("*")].map((e) => (e.childNodes.length === 1 && e.firstChild.nodeType === 3) ? clean(e.textContent) : "")
          .find((v) => v && v !== t && /^[$\d\u2014\-][\d,.$%x\u2014\-]*$/.test(v));
        if (leaf) { out[t] = leaf; break; }
      }
    }
    return out;
  }

  // Name of the entity being edited: the breadcrumb's highlighted item or the editor title.
  function editorEntityName() {
    const h = document.querySelector('[role="dialog"] h1, [role="dialog"] h2, h1');
    const crumb = [...document.querySelectorAll('a, span')].find((e) => e.getAttribute("aria-current") === "page");
    return clean((crumb && crumb.innerText) || (h && h.innerText) || "").slice(0, 120);
  }

  const LEVEL_NAME = { campaigns: "Campaign", adsets: "Ad set", ads: "Ad" };
  function entityContext(el) {
    if (IS_MOCK) {
      const m = {};
      document.querySelectorAll("[data-fx-metric]").forEach((x) => { m[x.dataset.fxMetric] = clean(x.textContent); });
      return { account: "mock", level: "campaigns", id: "mock-1", name: campaignContext().name || "", date_range: "last 7 days", metrics: m };
    }
    const info = pageInfo();
    const inEditor = /\/edit\//.test(location.pathname);
    let metrics = {}, name = "";
    if (inEditor) { metrics = readEditorCard(); name = editorEntityName(); }
    if (!Object.keys(metrics).length) {
      // Table view: the row the control sits in (on/off switch), else the checked row
      // (inline budget popover edits the selected row), else the only row on screen.
      const rows = readTableRows();
      const row = (el && rowOfElement(el)) || selectedRow() || (rows.length === 1 ? rows[0] : null);
      if (row) { metrics = row.metrics; name = name || row.name; }
    }
    return { account: info.account, level: info.level, id: info.selected.length === 1 ? info.selected[0] : "", name, date_range: info.date_range, metrics };
  }

  // Publish / discard tracking. Advised changes are "pending" until the buyer clicks
  // Publish (-> published) or Discard / Cancel (-> discarded). Ads Manager has several
  // publish surfaces: the editor footer, the inline popover, the "Review and publish"
  // drawer, and the "Discard drafts" dialog. All are buttons whose visible text says so.
  const pendingEventIds = new Set();
  const PUBLISH_RE = /^(publish|publish all|publish \d+ changes?)$/i;
  const DISCARD_RE = /^(discard|discard drafts?|discard changes?|cancel)$/i;
  async function setStatus(status) {
    const ids = [...pendingEventIds];
    if (!ids.length) return;
    pendingEventIds.clear();
    try {
      await fetch(`${SERVER}/events/status`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ event_ids: ids, status }) });
      log("status", status, ids);
    } catch (e) { log("status failed", e.message); }
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest('button, [role="button"], a');
    if (!b || b.closest("#fx-advisor")) return;
    const t = clean(b.innerText || b.getAttribute("aria-label") || "");
    if (PUBLISH_RE.test(t)) setStatus("published");
    else if (DISCARD_RE.test(t)) {
      // "Cancel" only counts inside an edit surface (popover, dialog, editor footer), not in nav.
      if (/^cancel$/i.test(t) && !b.closest('[role="dialog"], form, [aria-modal="true"]') && !/\/edit\//.test(location.pathname)) return;
      setStatus("discarded");
    }
  }, true);

  // Snapshot: post every visible row so outcomes can be judged later. On load, on level
  // change, and every 5 minutes. Only what is on screen leaves the browser.
  let lastSnapshotKey = "";
  async function postSnapshot(force) {
    if (IS_MOCK) return;
    const info = pageInfo();
    const rows = readTableRows();
    if (!rows.length) return;
    const key = `${info.account}|${info.level}|${info.date_range}|${rows.map((r) => r.name).join(",")}`;
    if (!force && key === lastSnapshotKey) return;
    lastSnapshotKey = key;
    try {
      await fetch(`${SERVER}/snapshot`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account: info.account, level: info.level, date_range: info.date_range, rows }) });
      log("snapshot", rows.length, "rows", info.level, info.date_range);
    } catch (e) { log("snapshot failed", e.message); }
  }

  // --- panel -------------------------------------------------------------
  function panel() {
    let p = document.getElementById("fx-advisor");
    if (p) return p;
    p = document.createElement("div");
    p.id = "fx-advisor";
    p.className = "idle";
    p.innerHTML = `<header title="Drag to move"><span>Foxwell Advisor <small class="muted">v${VERSION}</small></span><span class="right"><small id="fx-mode"></small><button id="fx-collapse" title="Collapse">–</button></span></header>
      <div class="body"><div class="muted">Waiting for a change.</div></div>`;
    document.body.appendChild(p);
    restorePosition(p);
    makeDraggable(p);
    p.querySelector("#fx-collapse").addEventListener("click", (e) => { e.stopPropagation(); toggleCollapse(p); });
    return p;
  }

  // --- position and collapse (per viewer, kept in localStorage) ---------
  const POS_KEY = "fx-advisor-pos";
  function savePosition(p) {
    try { localStorage.setItem(POS_KEY, JSON.stringify({ left: p.style.left, top: p.style.top, collapsed: p.classList.contains("collapsed") })); } catch (e) {}
  }
  function restorePosition(p) {
    try {
      const s = JSON.parse(localStorage.getItem(POS_KEY) || "null");
      if (s && s.left && s.top) { p.style.left = s.left; p.style.top = s.top; p.style.right = "auto"; }
      if (s && s.collapsed) p.classList.add("collapsed");
    } catch (e) {}
  }
  // Keep the whole panel inside the viewport (after expand, drag, or window resize).
  function clampIntoView(p) {
    const r = p.getBoundingClientRect();
    let left = r.left, top = r.top;
    if (r.right > window.innerWidth - 8) left = Math.max(8, window.innerWidth - r.width - 8);
    if (r.bottom > window.innerHeight - 8) top = Math.max(8, window.innerHeight - r.height - 8);
    if (left !== r.left || top !== r.top) { p.style.left = left + "px"; p.style.top = top + "px"; p.style.right = "auto"; savePosition(p); }
  }
  window.addEventListener("resize", () => { const p = document.getElementById("fx-advisor"); if (p) clampIntoView(p); });

  function toggleCollapse(p) {
    p.classList.toggle("collapsed");
    p.querySelector("#fx-collapse").textContent = p.classList.contains("collapsed") ? "+" : "–";
    savePosition(p);
    clampIntoView(p);
  }
  function makeDraggable(p) {
    const h = p.querySelector("header");
    let sx, sy, ox, oy, moved;
    h.addEventListener("mousedown", (e) => {
      if (e.target.id === "fx-collapse") return;
      const r = p.getBoundingClientRect();
      sx = e.clientX; sy = e.clientY; ox = r.left; oy = r.top; moved = false;
      const onMove = (ev) => {
        const dx = ev.clientX - sx, dy = ev.clientY - sy;
        if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
        p.style.left = Math.max(0, Math.min(window.innerWidth - 60, ox + dx)) + "px";
        p.style.top = Math.max(0, Math.min(window.innerHeight - 40, oy + dy)) + "px";
        p.style.right = "auto";
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove); document.removeEventListener("mouseup", onUp);
        if (moved) savePosition(p); else toggleCollapse(p); // plain click on the header also collapses
      };
      document.addEventListener("mousemove", onMove); document.addEventListener("mouseup", onUp);
      e.preventDefault();
    });
  }

  function pct(x) { return Math.round((x || 0) * 100); }

  const INTERRUPT_MIN = 0.4; // below this, Jev says the change is not worth advice: stay quiet

  function render(res, ev) {
    const p = panel();
    if ((res.interrupt ?? 1) < INTERRUPT_MIN) {
      // Low interrupt: one quiet line. Green if the community lead is "supports", gray otherwise.
      const v = res.verdict;
      const lead = v && v.lead && v.n_relevant ? `Community: ${pct(v.share?.[v.lead])}% ${v.lead}` : "no community entry";
      p.className = v && v.lead === "supports" ? "low" : "idle";
      document.getElementById("fx-mode").textContent = `jev: ${res.jev_mode} · ${res.total_latency_ms} ms`;
      p.querySelector(".body").innerHTML = `<div><b>${res.topic_label}</b> · routine change. ${lead}. <span class="muted">interrupt ${pct(res.interrupt)}%</span></div>`;
      return;
    }
    p.classList.remove("collapsed"); p.querySelector("#fx-collapse").textContent = "–";
    p.className = (res.risk || "idle");
    document.getElementById("fx-mode").textContent = `jev: ${res.jev_mode} · ${res.total_latency_ms} ms`;
    const v = res.verdict;
    let html = `<div class="topic">${res.topic_label} <span class="muted">(${pct(res.topic_confidence)}% sure)</span></div>`;
    html += `<div>Risk: <b>${res.risk}</b> <span class="muted">(${pct(res.risk_confidence)}%)</span>`;
    if (res.delta_pct !== null && res.delta_pct !== undefined) html += ` · ${res.delta_pct > 0 ? "+" : ""}${res.delta_pct}%`;
    html += `</div>`;
    const pf = res.performance;
    if (pf && Object.keys(pf).length) {
      const parts = [];
      if (pf.spend !== undefined) parts.push(`spend $${pf.spend.toLocaleString()}`);
      if (pf.purchases !== undefined) parts.push(`${pf.purchases} purchases`);
      if (pf.roas !== undefined) parts.push(`ROAS ${pf.roas}`);
      if (pf.cpa !== undefined) parts.push(`CPA $${pf.cpa}`);
      if (pf.frequency !== undefined) parts.push(`freq ${pf.frequency}`);
      if (pf.delivery) parts.push(pf.delivery);
      html += `<div class="perf">Seen on page: ${parts.join(" · ")}</div>`;
    }
    if (v) {
      const s = v.share || {};
      html += `<div class="meter"><span class="s" style="width:${pct(s.supports)}%"></span><span class="d" style="width:${pct(s.depends)}%"></span><span class="c" style="width:${pct(s.cautions)}%"></span></div>`;
      html += `<div class="legend"><span>● ${pct(s.supports)}% support</span><span>● ${pct(s.depends)}% depends</span><span>● ${pct(s.cautions)}% caution</span></div>`;
      html += `<div class="summary">${v.summary}</div>`;
      if (v.deciding_factor && v.deciding_factor_share >= 0.4) {
        const F = { spend_level: "daily spend level", account_size: "account maturity and pixel data", creative: "creative volume and quality", product_or_offer: "product, price, or offer", seasonality: "timing and seasonality" };
        html += `<div class="factor">Deciding factor: <b>${F[v.deciding_factor] || v.deciding_factor}</b> <span class="muted">(${pct(v.deciding_factor_share)}% of the caution and depends sources)</span></div>`;
      }
      for (const src of (v.sources || []).slice(0, 3)) {
        html += `<div class="src"><a href="${src.link}" target="_blank" rel="noopener">${src.source}</a> <span class="muted">${src.date} · ${src.stance}</span><div class="ex">${src.excerpt}</div></div>`;
      }
      html += `<div class="stats">${v.n_relevant} community sources · built ${v.built_at}</div>`;
    } else {
      html += `<div class="summary muted">No playbook entry for this topic yet. Run the playbook builder.</div>`;
    }
    html += `<div class="stats">Sent: ${ev.field} ${ev.old} → ${ev.new} · interrupt ${pct(res.interrupt)}%</div>`;
    p.querySelector(".body").innerHTML = html;
    clampIntoView(p);
  }

  function renderError(err) {
    const p = panel();
    p.className = "idle";
    p.querySelector(".body").innerHTML = `<div class="muted">Advisor server not reachable at ${SERVER}. ${err}</div>`;
  }

  // --- wiring --------------------------------------------------------------
  async function advise(name, el, oldVal, newVal) {
    const ev = { field: name, old: oldVal, new: newVal, campaign: campaignContext(), entity: entityContext(el) };
    try {
      const r = await fetch(`${SERVER}/advise`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(ev) });
      const res = await r.json();
      if (res.event_id) pendingEventIds.add(res.event_id);
      render(res, ev);
    } catch (e) { renderError(e.message); }
  }

  // --- change detection ----------------------------------------------------
  // Keyed by label, not by element. Ads Manager re-renders controls (especially
  // menu-style dropdowns) as new DOM nodes after a pick, so an observer on the old
  // node never fires. Rescanning by label catches replaced nodes and reopened sections.
  const DEBUG = (() => { try { return localStorage.getItem("fx-advisor-debug") === "1"; } catch (e) { return false; } })();
  const log = (...a) => { if (DEBUG) console.debug("[fx-advisor]", ...a); };
  const known = new Map();     // label -> last committed value
  const editStart = new Map(); // label -> value when the current text edit began
  const reported = new Map();  // label -> last value sent to the server
  const lastInputAt = new Map(); // label -> time of the last keystroke
  const TYPE_DEBOUNCE_MS = 600; // wait for the buyer to finish typing the number
  let typeTimer = null;
  if (DEBUG) window.__fxState = { known, editStart };

  function isTextEntry(el) {
    return (el.tagName === "INPUT" && !["checkbox", "radio"].includes(el.type)) || el.tagName === "TEXTAREA";
  }

  const lastChange = new Map(); // name -> { from, to, at }
  const adviseTimes = [];       // timestamps of recent advise calls (rate cap)
  const RATE_CAP = 12, RATE_WINDOW_MS = 60000, FLAP_WINDOW_MS = 4000;

  function noteChange(name, el, oldVal, newVal) {
    const prev = lastChange.get(name);
    known.set(name, newVal);
    // A value that flips straight back to what it was a moment ago is the page re-rendering,
    // not the buyer. Record it, say nothing.
    if (prev && prev.from === newVal && prev.to === oldVal && Date.now() - prev.at < FLAP_WINDOW_MS) {
      lastChange.set(name, { from: oldVal, to: newVal, at: Date.now() });
      log("flap ignored", name, oldVal, "->", newVal);
      return;
    }
    lastChange.set(name, { from: oldVal, to: newVal, at: Date.now() });
    const now = Date.now();
    while (adviseTimes.length && now - adviseTimes[0] > RATE_WINDOW_MS) adviseTimes.shift();
    if (adviseTimes.length >= RATE_CAP) { log("rate cap, skipped", name); return; }
    adviseTimes.push(now);
    reported.set(name, newVal);
    log("change", name, oldVal, "->", newVal);
    clearTimeout(timer);
    timer = setTimeout(() => advise(name, el, oldVal, newVal), DEBOUNCE_MS);
  }

  // Label for a control, cached on the node. Mock page: the data attribute.
  function nameOf(el) {
    if (IS_MOCK) return el.dataset.fxField || "";
    if (el.dataset.fxName === undefined) el.dataset.fxName = labelFor(el);
    return el.dataset.fxName;
  }
  const ignored = (name) => !name || (!IS_MOCK && IGNORE.test(name));
  const midEdit = (name) => Date.now() - (lastInputAt.get(name) || 0) < 1500;

  function rescan() {
    if (DEBUG) { window.__fxState.ticks = (window.__fxState.ticks || 0) + 1; window.__fxState.rescan = rescan; }
    for (const { el, name } of fields()) {
      if (ignored(name)) continue;
      const v = valueOf(el);
      if (!el.dataset.fxBound) {
        el.dataset.fxBound = "1";
        if (isTextEntry(el)) {
          // Ads Manager re-creates the input node on every keystroke. A node that shows up
          // right after a keystroke is the same edit: keep the start value. A node that
          // appears with no recent typing is a fresh field: its value is the start value.
          const fresh = !midEdit(name) || !editStart.has(name);
          log("text node bound", name, "value", v, fresh ? "fresh -> start reset" : "mid-edit -> start kept");
          if (fresh) { editStart.set(name, v); known.set(name, v); }
        } else {
          el.addEventListener("change", () => setTimeout(rescan, 0));
          el.addEventListener("click", () => {
            if (isMenuButton(el)) lastMenu = { name, value: valueOf(el), t: Date.now(), fired: false };
            setTimeout(rescan, 0);
          });
        }
      }
      if (!known.has(name)) { known.set(name, v); log("bound", name, "=", v); continue; }
      if (known.get(name) === v) continue;
      if (isTextEntry(el)) {
        if (document.activeElement === el) continue; // mid-edit: the typing listener owns it
        // Unfocused text field with a new value: this is the commit. Report it only if the
        // typing listener did not already send this exact value.
        const already = reported.get(name) === v;
        const ov = known.get(name);
        known.set(name, v); editStart.set(name, v);
        if (!already) noteChange(name, el, ov, v);
        continue;
      }
      if (reported.get(name) === v) { known.set(name, v); continue; } // already sent (menu pick)
      if (reverted && reverted.name === name && v === reverted.value && Date.now() < reverted.until) {
        // The pick is parked behind a dialog: the button keeps its old text until the dialog
        // is confirmed. Only treat "still the old value" as a cancel once no dialog is open.
        if (document.querySelector('[role="dialog"]')) continue;
        known.set(name, v); reverted = null; log("revert (dialog cancelled)", name); continue;
      }
      noteChange(name, el, known.get(name), v);
    }
    watchDialogs();
  }

  // Text entry is handled with delegated listeners so it survives node replacement.
  // "old" is pinned to the value the field held when the edit started and stays pinned
  // through every keystroke. It only moves when the edit is committed (change/blur/rescan).
  document.addEventListener("focusin", (e) => {
    const el = e.target; if (!isTextEntry(el)) return;
    const name = nameOf(el); if (ignored(name)) return;
    // Defer one tick: a node swap focuses the new node inside the keystroke's own input
    // event, before the typing listener below has recorded that keystroke.
    setTimeout(() => { if (!midEdit(name)) editStart.set(name, valueOf(el)); }, 0);
  }, true);
  document.addEventListener("input", (e) => {
    const el = e.target; if (!isTextEntry(el)) return;
    const name = nameOf(el); if (ignored(name)) return;
    if (!editStart.has(name)) editStart.set(name, known.get(name) ?? valueOf(el));
    lastInputAt.set(name, Date.now());
    clearTimeout(typeTimer);
    typeTimer = setTimeout(() => {
      // Read the value from whatever node currently represents this field.
      const cur = [...document.querySelectorAll(IS_MOCK ? "[data-fx-field]" : EDITABLE)].find((x) => isTextEntry(x) && nameOf(x) === name) || el;
      const nv = valueOf(cur), ov = editStart.get(name);
      if (nv !== ov && nv !== "") { log("typing", name, ov, "->", nv); reported.set(name, nv); advise(name, cur, ov, nv); }
    }, TYPE_DEBOUNCE_MS);
  }, true);
  // No commit on blur/change. A blur can arrive mid-edit (node swaps, popovers), and a
  // commit at the wrong moment turns "200 -> 600" into "6 -> 600". The rescan commits any
  // unfocused text field within a second, silently if the value was already reported.

  // --- menu picks and dialogs -------------------------------------------------
  // Ads Manager commits some picks only after a dialog (e.g. "ROAS goal for 1 campaign")
  // is confirmed, so the dropdown text does not change at pick time. The pick itself
  // is the intent, so fire on the menu item click and attribute it to the open menu.
  let lastMenu = null;
  let reverted = null; // suppress the "change back" when the user cancels the dialog
  const MENU_WINDOW_MS = 8000;

  function menuPick(newVal, el) {
    if (!lastMenu || Date.now() - lastMenu.t > MENU_WINDOW_MS || lastMenu.fired) return;
    newVal = clean(newVal);
    if (!newVal || newVal === clean(lastMenu.value)) return;
    lastMenu.fired = true;
    reverted = { name: lastMenu.name, value: lastMenu.value, until: Date.now() + 60000 };
    noteChange(lastMenu.name, el, lastMenu.value, newVal);
  }

  document.addEventListener("click", (e) => {
    const item = e.target.closest('[role="menuitem"],[role="menuitemradio"],[role="option"]');
    if (item) menuPick(shortText(item), item);
  }, true);

  const seenDialogs = new WeakSet();
  function watchDialogs() {
    for (const d of document.querySelectorAll('[role="dialog"]')) {
      if (seenDialogs.has(d)) continue;
      seenDialogs.add(d);
      const h = d.querySelector('h1,h2,h3,[role="heading"]');
      const title = h ? shortText(h) : shortText(d);
      log("dialog", title);
      if (title) menuPick(title, d);
    }
  }

  let scanTimer = null;
  const scheduleRescan = () => { clearTimeout(scanTimer); scanTimer = setTimeout(rescan, 120); };

  panel();
  rescan();
  setTimeout(() => postSnapshot(false), 2500);
  setInterval(() => postSnapshot(false), 5 * 60 * 1000);
  let lastHref = location.href;
  setInterval(() => { if (location.href !== lastHref) { lastHref = location.href; setTimeout(() => postSnapshot(false), 2500); } }, 1000);
  new MutationObserver(scheduleRescan).observe(document.body, { childList: true, subtree: true, characterData: true });
  setInterval(rescan, 1000); // safety net for re-renders the observer coalesced away
})();
