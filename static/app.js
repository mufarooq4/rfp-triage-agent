/* RFP Triage Agent -- front end (no framework, no build step). */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const BOARD_KEY = "rfp-triage-board-v1";
  const state = { current: null, pages: null, reqFilter: "all" };

  // ------------------------------------------------------------------ utils
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const NS = "Not stated";
  const isNS = (v) => !v || String(v).trim() === "" || String(v).trim().toLowerCase() === "not stated";
  const sc = (n) => (n == null || isNaN(n) ? "?" : Number(n).toFixed(1));
  const human = (s) => String(s || "").replace(/_/g, " ");
  const fmtDate = (iso) => {
    if (!iso) return null;
    const d = new Date(iso + "T00:00:00");
    if (isNaN(d)) return iso;
    return d.toLocaleDateString("en-US", { weekday: "short", year: "numeric", month: "short", day: "numeric" });
  };
  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2200);
  }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); toast("Copied to clipboard"); }
    catch { const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); toast("Copied"); }
  }
  function download(name, content, type) {
    const blob = new Blob([content], { type });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }
  const slug = (s) => String(s || "rfp").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 50) || "rfp";
  const storage = {
    get() { try { return JSON.parse(localStorage.getItem(BOARD_KEY) || "[]"); } catch { return []; } },
    set(v) { try { localStorage.setItem(BOARD_KEY, JSON.stringify(v)); } catch { /* storage full or blocked */ } },
  };

  // evidence chip: every claim links to its page
  let evRegistry = [];
  function ev(item, label) {
    if (!item || !item.verification || item.verification === "no_quote") return "";
    const id = evRegistry.push({ ...item, label }) - 1;
    const map = { verified: "✓", page_corrected: "↻", approximate: "≈", unverified: "⚠" };
    const tip = { verified: "Quote found word-for-word on this page", page_corrected: "Quote found, on a different page than the model cited (corrected)", approximate: "Near-exact match on this page (line-break noise)", unverified: "Quote NOT found in the PDF — verify manually" }[item.verification];
    const pg = item.page ? `p.${item.page}` : "p.?";
    return `<button type="button" class="ev ${esc(item.verification)}" data-ev="${id}" title="${esc(tip)}">${pg} ${map[item.verification] || ""}</button>`;
  }

  // ------------------------------------------------------------------ health
  let maxMb = 25;
  fetch("/api/health").then((r) => r.json()).then((h) => {
    if (h.max_upload_mb) { maxMb = h.max_upload_mb; const sub = document.querySelector(".dz-sub"); if (sub) sub.textContent = `or click to choose a file · max ${maxMb} MB`; }
    const el = $("#health");
    el.classList.add(h.llm_configured ? "ok" : "bad");
    el.title = h.llm_configured ? `Ready · models: ${h.models.join(", ")}` : "Server has no GEMINI_API_KEY set";
  }).catch(() => $("#health").classList.add("bad"));

  // ------------------------------------------------------------------ theme (dark by default)
  const themeBtn = $("#themeBtn");
  function applyTheme(t) {
    document.documentElement.dataset.theme = t;
    themeBtn.textContent = t === "dark" ? "☀ Light" : "☾ Dark";
    themeBtn.title = `Switch to ${t === "dark" ? "light" : "dark"} mode`;
  }
  applyTheme(document.documentElement.dataset.theme || "dark");
  themeBtn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("rfp-theme", next); } catch { /* storage blocked: still switches */ }
  });

  // ------------------------------------------------------------------ profile
  $("#profileBtn").addEventListener("click", async () => {
    const p = await fetch("/api/profile").then((r) => r.json());
    $("#profileBody").innerHTML = `
      <p><strong>${esc(p.name)}</strong> — ${esc(p.summary)}</p>
      <div class="sub-h">What we build</div><ul class="plain">${p.builds.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>
      <div class="sub-h">Treated as unknown (never assumed)</div><ul class="plain">${p.unknown.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>`;
    $("#profileDialog").showModal();
  });

  // ------------------------------------------------------------------ upload
  const dz = $("#dropzone"), input = $("#fileInput");
  ["dragenter", "dragover"].forEach((e) => dz.addEventListener(e, (ev) => { ev.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => dz.addEventListener(e, (ev) => { ev.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) analyse(f); });
  input.addEventListener("change", () => { if (input.files[0]) analyse(input.files[0]); input.value = ""; });

  function show(view) {
    ["uploadView", "progressView", "reportView"].forEach((v) => ($("#" + v).hidden = v !== view));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  let timer;
  function startProgress(name) {
    $("#progressFile").textContent = name;
    const steps = [...document.querySelectorAll("#steps li")];
    const t0 = Date.now();
    steps.forEach((s) => s.className = "");
    clearInterval(timer);
    timer = setInterval(() => {
      const s = (Date.now() - t0) / 1000;
      $("#elapsed").textContent = s.toFixed(0);
      // the server works in one request; the stepper tracks typical stage timings
      const active = s < 1.5 ? 0 : 1;   // after the single model call, steps 2-4 finish in milliseconds
      steps.forEach((el, i) => el.className = i < active ? "done" : i === active ? "active" : "");
    }, 250);
  }

  async function analyse(file) {
    $("#uploadError").hidden = true;
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") return fail("Please choose a PDF file.");
    if (file.size > maxMb * 1024 * 1024) return fail(`That file is larger than ${maxMb} MB, the upload limit of this free deployment. Run the app locally for bigger files.`);
    show("progressView"); startProgress(file.name);
    const fd = new FormData(); fd.append("file", file);
    try {
      const r = await fetch("/api/triage?include_pages=true", { method: "POST", body: fd });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `Server error (${r.status})`);
      clearInterval(timer);
      const pages = body.pages; delete body.pages;
      addToBoard(body);
      render(body, pages);
    } catch (e) {
      clearInterval(timer);
      show("uploadView"); fail(e.message || String(e));
    }
  }
  function fail(msg) { const el = $("#uploadError"); el.textContent = msg; el.hidden = false; }

  // ------------------------------------------------------------------ board
  function addToBoard(report) {
    const board = storage.get().filter((r) => r.document?.sha256 !== report.document?.sha256);
    board.push(report);
    storage.set(board.slice(-25));
    renderBoard();
  }
  function proposalDue(r) {
    const d = (r.key_dates || []).find((x) => x.category === "proposal_due" && x.date);
    return d ? d.date : null;
  }
  function renderBoard() {
    const list = $("#boardList");
    const board = storage.get().sort((a, b) => (b.decision === "BID") - (a.decision === "BID") || b.fit_score - a.fit_score);
    if (!board.length) { list.innerHTML = `<li class="board-empty">No RFPs yet. Upload one to start your ranked shortlist.</li>`; return; }
    const curr = state.current?.document?.sha256;
    list.innerHTML = board.map((r, i) => {
      const due = proposalDue(r);
      const days = due ? Math.round((new Date(due + "T00:00:00") - new Date(new Date().toDateString())) / 864e5) : null;
      const title = isNS(r.facts?.title?.value) ? r.document?.filename : r.facts.title.value;
      return `<li class="board-item ${r.document?.sha256 === curr ? "active" : ""}" data-sha="${esc(r.document?.sha256)}">
        <div class="bi-top"><span class="bi-rank">#${i + 1}</span>
          <span class="chip ${r.decision === "BID" ? "bid" : "nobid"}">${r.decision === "BID" ? "BID" : "NO-BID"} · ${sc(r.fit_score)}</span></div>
        <div class="bi-title">${esc(title)}</div>
        <div class="bi-meta">${due ? `Due ${esc(fmtDate(due))}${days !== null ? ` · ${days < 0 ? "passed" : days + "d left"}` : ""}` : "Due date not stated"}</div>
      </li>`;
    }).join("");
  }
  $("#boardList").addEventListener("click", (e) => {
    const li = e.target.closest(".board-item"); if (!li) return;
    const r = storage.get().find((x) => x.document?.sha256 === li.dataset.sha);
    if (r) render(r, null);
  });
  $("#clearBoard").addEventListener("click", () => { storage.set([]); renderBoard(); toast("Board cleared"); });
  $("#newBtn").addEventListener("click", () => { show("uploadView"); });

  // ------------------------------------------------------------------ report
  function ring(score) {
    const r = 46, c = 2 * Math.PI * r, pct = Math.max(0, Math.min(10, score)) / 10;
    const color = score >= 6 ? "var(--bid)" : score >= 4 ? "var(--warn)" : "var(--nobid)";
    return `<div class="ring"><svg width="112" height="112" viewBox="0 0 112 112">
      <circle cx="56" cy="56" r="${r}" fill="none" stroke="var(--track)" stroke-width="10"/>
      <circle cx="56" cy="56" r="${r}" fill="none" stroke="${color}" stroke-width="10" stroke-linecap="round"
        stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - pct)}"/></svg>
      <div class="ring-label"><div><div class="ring-score">${sc(score)}</div><div class="ring-sub">fit / 10</div></div></div></div>`;
  }

  function render(r, pages) {
    state.current = r; state.pages = pages; evRegistry = []; state.reqFilter = "all";
    const f = r.facts || {};
    const bid = r.decision === "BID";
    const title = isNS(f.title?.value) ? r.document?.filename : f.title.value;
    const evPct = r.evidence?.verified_pct;
    const tags = [
      r.fast_no_bid ? `<span class="chip nobid">Fast no-bid</span>` : "",
      r.borderline ? `<span class="chip warn">Borderline</span>` : "",
      `<span class="chip neutral">Confidence: ${esc(r.confidence)}</span>`,
      evPct != null ? `<span class="chip ${evPct >= 90 ? "bid" : evPct >= 70 ? "warn" : "nobid"}" title="Share of cited quotes found in the PDF">Evidence checked: ${r.evidence.verified}/${r.evidence.checked} (${evPct}%)</span>` : "",
      `<span class="chip info">${esc(human(r.work_category?.value))}</span>`,
    ].join("");

    const html = `
    <div class="card hero">
      <div class="decision-badge ${bid ? "bid" : "nobid"}">${bid ? "BID" : "NO-BID"}<small>recommendation</small></div>
      <div>
        <div class="hero-title">${esc(title)}</div>
        <div class="hero-meta">${esc(isNS(f.agency?.value) ? "Agency not stated" : f.agency.value)}${!isNS(f.solicitation_number?.value) ? " · " + esc(f.solicitation_number.value) : ""} · ${esc(r.document?.pages)} pages · ${esc(r.timing?.total_s ?? "?")} s${r.timing?.cache_hit ? " (cached)" : ""}</div>
        <div class="hero-verdict">${esc(r.verdict_line)}</div>
        <div class="hero-tags">${tags}</div>
      </div>
      ${ring(r.fit_score)}
    </div>

    <nav class="section-nav">
      <a href="#s-rec">Recommendation</a><a href="#s-sum">Summary</a><a href="#s-dates">Key dates</a>
      <a href="#s-req">Requirements</a><a href="#s-fit">Fit score</a><a href="#s-q">Questions &amp; flags</a><a href="#s-json">JSON</a>
    </nav>

    ${(r.warnings || []).map((w) => `<div class="callout warn">${esc(w)}</div>`).join("")}

    <section class="card" id="s-rec">
      <div class="sec-head"><h2>Why ${bid ? "bid" : "not bid"}</h2></div>
      <ol class="reasons">${(r.reasons || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol>
      <div class="grid-2">
        <div>
          <div class="sub-h">Deal-breakers</div>
          ${(r.deal_breakers || []).length ? `<ul class="plain">${r.deal_breakers.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<div class="ns">None identified.</div>`}
          ${(r.gates || []).length ? `<div class="sub-h">Score caps applied</div><ul class="plain">${r.gates.map((x) => `<li>${esc(x.replace(/^Deal-breaker -- /, "Deal-breaker: "))}</li>`).join("")}</ul>` : ""}
        </div>
        <div>
          <div class="sub-h">What would change our mind</div>
          ${(r.what_would_change_our_mind || []).length ? `<ul class="plain">${r.what_would_change_our_mind.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<div class="ns">Not stated.</div>`}
          ${otherSide(r, bid)}
        </div>
      </div>
    </section>

    <section class="card" id="s-sum">
      <div class="sec-head"><h2>What the client wants</h2></div>
      <p style="margin-top:0">${esc(r.summary)}</p>
      ${(r.scope_of_work || []).length ? `<div class="sub-h">Scope of work</div><ul class="plain">${r.scope_of_work.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      <div class="sub-h">Key facts</div>
      <div class="table-wrap"><table class="t"><tbody>
        ${[["agency", "Agency"], ["solicitation_number", "Solicitation no."], ["contract_type", "Contract type"], ["contract_value", "Contract value"], ["period_of_performance", "Period of performance"], ["place_of_performance", "Where the work happens"], ["submission_method", "How to submit"], ["evaluation_method", "Evaluation method"], ["point_of_contact", "Point of contact"]]
          .map(([k, label]) => { const x = f[k] || {}; return `<tr><td class="k">${label}</td><td>${isNS(x.value) ? `<span class="ns">${NS}</span>` : esc(x.value)} ${ev(x, label)}</td></tr>`; }).join("")}
        <tr><td class="k">Work category</td><td>${esc(human(r.work_category?.value))} — <span class="ns" style="font-style:normal">${esc(r.work_category?.reason)}</span></td></tr>
      </tbody></table></div>
    </section>

    <section class="card" id="s-dates">
      <div class="sec-head"><h2>Key dates</h2>
        <div class="sec-actions"><button class="ghost-btn" id="icsBtn" type="button" title="Download all calendar dates as an .ics file">Add to calendar (.ics)</button></div></div>
      ${datesTable(r)}
    </section>

    <section class="card" id="s-req">
      <div class="sec-head"><h2>Requirements &amp; must-haves</h2>
        <div class="sec-actions"><button class="ghost-btn" id="csvBtn" type="button">Export compliance checklist (CSV)</button></div></div>
      <div class="filters" id="reqFilters">
        ${["all", "mandatory", "gap", "unknown"].map((k) => `<button type="button" class="filter ${k === "all" ? "on" : ""}" data-f="${k}">${{ all: "All", mandatory: "Mandatory", gap: "Gaps for us", unknown: "Unknown for us" }[k]} (${countReq(r, k)})</button>`).join("")}
      </div>
      <div class="table-wrap" id="reqTable" style="margin-top:10px">${reqTable(r, "all")}</div>
      ${otherMusts(r)}
    </section>

    <section class="card" id="s-fit">
      <div class="sec-head"><h2>Fit score: ${sc(r.fit_score)} / 10</h2>
        <span class="chip neutral">raw ${esc(r.fit_breakdown?.raw_score)} before gates</span></div>
      ${(r.fit_breakdown?.factors || []).map(factorRow).join("")}
      <div class="method">Score = Σ weight × rating share (strong +1.0 · positive 0.75 · neutral/not stated 0.5 · negative 0.15 · deal-breaker 0). Out-of-domain work is capped at 2.0, any deal-breaker at 4.0. BID needs ≥ ${esc(r.fit_breakdown?.method?.bid_threshold)} and no deal-breaker. The model rates; the code scores.</div>
    </section>

    <section class="card" id="s-q">
      <div class="grid-2">
        <div>
          <div class="sec-head"><h2>Questions to ask the agency</h2>
            ${(r.open_questions || []).length ? `<button class="ghost-btn" id="qBtn" type="button">Copy as email</button>` : ""}</div>
          ${(r.open_questions || []).length ? `<ol class="plain">${r.open_questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ol>` : `<div class="ns">None.</div>`}
        </div>
        <div>
          <div class="sec-head"><h2>Flagged as ambiguous</h2></div>
          ${(r.ambiguities || []).length ? r.ambiguities.map((a) => `<div class="callout warn"><strong>${esc(a.issue)}</strong> ${ev(a, "Ambiguity")}
            ${(a.readings || []).length ? `<ul class="plain" style="margin-top:6px">${a.readings.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</div>`).join("") : `<div class="ns">Nothing ambiguous was flagged.</div>`}
        </div>
      </div>
    </section>

    <section class="card" id="s-json">
      <div class="sec-head"><h2>Machine-readable report</h2>
        <div class="sec-actions"><button class="ghost-btn" id="copyJson" type="button">Copy JSON</button><button class="ghost-btn" id="dlJson" type="button">Download JSON</button></div></div>
      <details><summary>Show JSON</summary><pre class="json">${esc(JSON.stringify(r, null, 2))}</pre></details>
      <p class="method">Same data via API: <code>POST /api/triage</code> with a multipart <code>file</code>. Model: ${esc(r.model)} · generated ${esc(r.generated_at)}</p>
    </section>
    <p class="footer-note">Uses only what is in the document. "Not stated" means the RFP does not say. Click any <span class="ev verified">p.N ✓</span> chip to see the source.</p>`;

    const view = $("#reportView");
    view.innerHTML = html;
    show("reportView");
    renderBoard();

    $("#icsBtn").addEventListener("click", () => exportICS(r));
    $("#csvBtn").addEventListener("click", () => exportCSV(r));
    $("#copyJson").addEventListener("click", () => copy(JSON.stringify(r, null, 2)));
    $("#dlJson").addEventListener("click", () => download(`triage-${slug(title)}.json`, JSON.stringify(r, null, 2), "application/json"));
    const qBtn = $("#qBtn"); if (qBtn) qBtn.addEventListener("click", () => copy(questionsEmail(r)));
    $("#reqFilters").addEventListener("click", (e) => {
      const b = e.target.closest(".filter"); if (!b) return;
      document.querySelectorAll("#reqFilters .filter").forEach((x) => x.classList.toggle("on", x === b));
      $("#reqTable").innerHTML = reqTable(r, b.dataset.f);
    });
  }

  function otherSide(r, bid) {
    const list = (r.reasons_considered || {})[bid ? "not_to_bid" : "to_bid"] || [];
    if (!list.length) return "";
    return `<div class="sub-h">The case ${bid ? "against" : "for"} bidding</div><ul class="plain">${list.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
  }

  function datesTable(r) {
    const ds = r.key_dates || [];
    if (!ds.length) return `<div class="ns">No dates found in the document.</div>`;
    return `<div class="table-wrap"><table class="t"><thead><tr><th>What</th><th>Date</th><th>As written in the RFP</th><th>Source</th></tr></thead><tbody>
      ${ds.map((d) => {
        const days = d.days_until;
        const badge = days == null ? "" : days < 0 ? `<span class="chip neutral">passed</span>` : days <= 7 ? `<span class="chip nobid">${days}d left</span>` : days <= 21 ? `<span class="chip warn">${days}d left</span>` : `<span class="chip bid">${days}d left</span>`;
        return `<tr><td><strong>${esc(d.what)}</strong><div class="factor-rating">${esc(human(d.category))}</div></td>
          <td><div class="date-big">${d.date ? esc(fmtDate(d.date)) : `<span class="ns">No calendar date</span>`}</div>
            <div class="factor-rating" style="text-transform:none">${[d.time, d.timezone].filter(Boolean).map(esc).join(" ")}</div>${badge}</td>
          <td><span class="as-written">${esc(d.as_written)}</span>${(d.issues || []).map((i) => `<div class="issue">${esc(i)}</div>`).join("")}</td>
          <td>${ev(d, d.what)}</td></tr>`;
      }).join("")}</tbody></table></div>`;
  }

  // Safety net (code scan): every "must / shall / required / [M]" sentence in the PDF
  // that the requirement list above does not already cover.
  function otherMusts(r) {
    const list = r.other_must_statements || [];
    const cov = r.coverage || {};
    if (!cov.mandatory_statements) return "";
    return `<details class="musts" style="margin-top:14px">
      <summary>Safety net: ${list.length} more “must” statement${list.length === 1 ? "" : "s"} found by a code scan of every page
        <span class="factor-rating" style="text-transform:none">(${cov.mandatory_statements} mandatory sentences in the PDF; ${cov.covered_by_requirements} already covered above)</span></summary>
      <p class="method" style="margin-top:8px">Not every one is a bid blocker. They are listed so nothing mandatory is missed. Click a page to see it in context.</p>
      <table class="t"><tbody>${list.map((m) => `<tr><td style="width:70px">${ev({ page: m.page, quote: m.text, verification: "verified", cited_page: m.page, match_score: 100 }, "Mandatory statement")}</td><td>${esc(m.text)}</td></tr>`).join("")}</tbody></table>
    </details>`;
  }

  function countReq(r, k) {
    const rs = r.requirements || [];
    return k === "all" ? rs.length : k === "mandatory" ? rs.filter((x) => x.mandatory).length : rs.filter((x) => x.idiaz_status === k).length;
  }
  function reqTable(r, k) {
    let rs = r.requirements || [];
    if (k === "mandatory") rs = rs.filter((x) => x.mandatory);
    else if (k !== "all") rs = rs.filter((x) => x.idiaz_status === k);
    if (!rs.length) return `<div class="ns">None.</div>`;
    const st = { meets: ["bid", "We meet it"], gap: ["nobid", "Gap"], unknown: ["warn", "Unknown"], not_applicable: ["neutral", "N/A"] };
    return `<table class="t"><thead><tr><th>Type</th><th>Requirement</th><th>Must?</th><th>idiaz.io</th><th>Source</th></tr></thead><tbody>
      ${rs.map((x) => { const s = st[x.idiaz_status] || st.unknown; return `<tr>
        <td><span class="chip neutral">${esc(human(x.category))}</span></td>
        <td>${esc(x.text)}${x.status_note ? `<div class="factor-rating" style="text-transform:none;margin-top:3px">${esc(x.status_note)}</div>` : ""}</td>
        <td>${x.mandatory ? "<strong>Required</strong>" : "Preferred"}</td>
        <td><span class="chip ${s[0]}">${s[1]}</span></td>
        <td>${ev(x, x.text.slice(0, 60))}</td></tr>`; }).join("")}</tbody></table>`;
  }

  function factorRow(fc) {
    const share = fc.weight ? fc.points / fc.weight : 0;
    const color = share >= 0.75 ? "var(--bid)" : share >= 0.5 ? "var(--neutral-bar)" : share > 0 ? "var(--warn)" : "var(--nobid)";
    return `<div class="factor">
      <div><div class="factor-name">${esc(fc.label)}</div><div class="factor-rating">${esc(human(fc.rating))} · weight ${esc(fc.weight)}</div></div>
      <div><div class="bar"><span style="width:${Math.max(3, share * 100)}%;background:${color}"></span></div>
        <div class="factor-reason">${esc(fc.reason)} ${ev(fc, fc.label)}</div></div>
      <div class="factor-pts">+${Number(fc.points).toFixed(2)}</div></div>`;
  }

  // ------------------------------------------------------------------ exports
  function icsEscape(s) { return String(s || "").replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/([,;])/g, "\\$1"); }
  function exportICS(r) {
    const title = isNS(r.facts?.title?.value) ? r.document?.filename : r.facts.title.value;
    const sol = isNS(r.facts?.solicitation_number?.value) ? "" : r.facts.solicitation_number.value;
    const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
    const events = (r.key_dates || []).filter((d) => d.date).map((d, i) => {
      const ymd = d.date.replace(/-/g, "");
      let when;
      if (d.time && /^\d{1,2}:\d{2}$/.test(d.time)) {
        const [h, m] = d.time.split(":").map(Number);
        const end = new Date(2000, 0, 1, h, m + 60);
        const endT = `${String(end.getHours()).padStart(2, "0")}${String(end.getMinutes()).padStart(2, "0")}00`;
        when = `DTSTART:${ymd}T${String(h).padStart(2, "0")}${String(m).padStart(2, "0")}00\r\nDTEND:${ymd}T${endT}`;
      } else {
        const [yy, mm, dd] = d.date.split("-").map(Number);
        const next = new Date(Date.UTC(yy, mm - 1, dd + 1)); // UTC avoids time-zone day shifts
        when = `DTSTART;VALUE=DATE:${ymd}\r\nDTEND;VALUE=DATE:${next.toISOString().slice(0, 10).replace(/-/g, "")}`;
      }
      const alarm = ["proposal_due", "questions_due"].includes(d.category)
        ? `\r\nBEGIN:VALARM\r\nTRIGGER:-P2D\r\nACTION:DISPLAY\r\nDESCRIPTION:${icsEscape(d.what)} in 2 days\r\nEND:VALARM` : "";
      return `BEGIN:VEVENT\r\nUID:${r.document.sha256.slice(0, 16)}-${i}@rfp-triage\r\nDTSTAMP:${stamp}\r\n${when}\r\nSUMMARY:${icsEscape(`${d.what} — ${title}`)}\r\nDESCRIPTION:${icsEscape(`${sol ? sol + "\n" : ""}As written: ${d.as_written}${d.timezone ? "\nTime zone: " + d.timezone : ""}\nSource: PDF page ${d.page ?? "?"}\nDecision: ${r.decision} (${sc(r.fit_score)}/10)`)}${alarm}\r\nEND:VEVENT`;
    });
    if (!events.length) return toast("No calendar dates to export");
    const ics = `BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//RFP Triage Agent//EN\r\nCALSCALE:GREGORIAN\r\n${events.join("\r\n")}\r\nEND:VCALENDAR\r\n`;
    download(`deadlines-${slug(title)}.ics`, ics, "text/calendar");
  }
  function exportCSV(r) {
    const rows = [["Category", "Requirement", "Mandatory", "idiaz.io status", "Note", "PDF page", "Evidence check", "Quote"]];
    (r.requirements || []).forEach((x) => rows.push([human(x.category), x.text, x.mandatory ? "Yes" : "No", x.idiaz_status, x.status_note || "", x.page ?? "", x.verification, x.quote || ""]));
    const csv = rows.map((row) => row.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\r\n");
    const title = isNS(r.facts?.title?.value) ? r.document?.filename : r.facts.title.value;
    download(`compliance-checklist-${slug(title)}.csv`, "\ufeff" + csv, "text/csv");
  }
  function questionsEmail(r) {
    const f = r.facts || {};
    const sol = isNS(f.solicitation_number?.value) ? "" : f.solicitation_number.value;
    const title = isNS(f.title?.value) ? "" : f.title.value;
    const qDue = (r.key_dates || []).find((d) => d.category === "questions_due");
    return `Subject: Questions${sol ? " – " + sol : ""}${title ? " – " + title : ""}\n\n` +
      `Dear Contracting Officer,\n\nidiaz.io respectfully submits the following questions regarding ${sol || "the above solicitation"}` +
      `${qDue ? ` ahead of the question deadline (${qDue.as_written})` : ""}:\n\n` +
      (r.open_questions || []).map((q, i) => `${i + 1}. ${q}`).join("\n") +
      `\n\nThank you for your time.\n\nKind regards,\nidiaz.io`;
  }

  // ------------------------------------------------------------------ evidence drawer
  function openDrawer(item) {
    const d = $("#drawer");
    $("#drawerTitle").textContent = item.label || "Evidence";
    const msg = {
      verified: `<span class="chip bid">Verified</span> Quote found word-for-word on PDF page ${item.page}.`,
      page_corrected: `<span class="chip info">Page corrected</span> The model cited page ${item.cited_page ?? "?"}; the quote is actually on page ${item.page}.`,
      approximate: `<span class="chip info">Near-exact</span> ${item.match_score}% match on page ${item.page} (usually line-break noise).`,
      unverified: `<span class="chip nobid">Not found</span> This quote could not be located in the PDF. Check the claim manually.`,
    }[item.verification] || "";
    $("#drawerStatus").innerHTML = msg;
    $("#drawerQuote").textContent = item.quote ? `“${item.quote}”` : "";
    const page = state.pages && item.page ? state.pages[item.page - 1] : null;
    $("#drawerPageLabel").textContent = page ? `PDF page ${item.page} — full text` : "";
    $("#drawerPage").innerHTML = page ? highlight(page, item.quote) : `<span class="ns">${state.pages ? "No page to show." : "Page text is kept only for the RFP analysed in this visit. Re-upload the PDF to view the source page."}</span>`;
    $("#drawerBackdrop").hidden = false; d.classList.add("open"); d.setAttribute("aria-hidden", "false");
    const m = $("#drawerPage mark"); if (m) m.scrollIntoView({ block: "center" });
  }
  function closeDrawer() { $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); $("#drawerBackdrop").hidden = true; }
  function highlight(text, quote) {
    const safe = esc(text);
    if (!quote) return safe;
    const words = quote.replace(/[\u2018\u2019]/g, "'").replace(/[\u201c\u201d]/g, '"').split(/\s+/).filter(Boolean).slice(0, 40)
      .map((w) => esc(w).replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/['"’‘“”]/g, ".?"));
    if (!words.length) return safe;
    try {
      const re = new RegExp(words.join("[\\s\\-]*\\s*"), "i");
      return safe.replace(re, (m) => `<mark>${m}</mark>`);
    } catch { return safe; }
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-ev]"); if (b) openDrawer(evRegistry[+b.dataset.ev]);
  });
  $("#drawerClose").addEventListener("click", closeDrawer);
  $("#drawerBackdrop").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

  renderBoard();
})();
