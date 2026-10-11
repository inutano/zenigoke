/* zenigoke portal mockup: UI layer. Hash-routed single page, no build step.
 *
 *   #/            search: facets + results table (state in the hash query)
 *   #/sample/ACC  experiment detail
 *   #/cart        selection, download manifests, IGV session
 *   #/studies     study (BioProject) list
 *   #/igv         genome files + IGV set-up help
 *   #/tools       analysis tools (enrichment, matrix overview)
 *   #/about       about / methods / processing log (demoted work-log pages)
 */
(function () {
  "use strict";
  const Z = window.ZCore;
  const LIVE = "https://inutano.github.io/zenigoke/";
  const IGVJS_SRC = "https://cdn.jsdelivr.net/npm/igv@3.8.9/dist/igv.min.js";  // not on cdnjs
  const CART_KEY = "zenigoke.cart.v1";
  const MAX_IGV_TRACKS = 60;

  let CAT = null;
  const BYACC = new Map();
  let state = { q: "", filters: {}, sort: "assay" };
  let cart = loadCart();
  const selected = new Set();
  const facetOpen = {};
  const facetMore = {};
  const facetFind = {};
  const cartOpts = {
    cats: new Set(Z.FILE_CATS.filter(c => c.def).map(c => c.cat)),
    q: "10", naming: "original", tab: "files",
  };
  let igvCtx = null;

  // ------------------------------------------------------------ helpers
  const $ = (sel, el) => (el || document).querySelector(sel);
  const $$ = (sel, el) => Array.from((el || document).querySelectorAll(sel));
  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function assayCls(a) { return a.toLowerCase(); }
  function qcBadge(s) {
    const tip = s.qc.reasons.length ? s.qc.reasons.join("; ") : "no issues found by current rules";
    return `<span class="qc ${s.qc.flag}" title="${esc(tip + (s.qc.source === "provisional" ? " (provisional rules)" : ""))}">${s.qc.flag}</span>`;
  }
  function studyLabel(acc) {
    const st = CAT.studies[acc];
    return st ? st.label : (acc || "unknown");
  }
  function displayValue(key, v) {
    if (key === "study") {
      const st = CAT.studies[v];
      return st ? `${st.label} (${(st.first_public || "").slice(0, 4)})` : v;
    }
    return v;
  }
  function headline(s) {
    const what = s.assay === "ChIP-seq" ? `${s.assay} of ${s.target}` : s.assay;
    const who = s.genotype !== "not curated" ? s.genotype : "genotype not curated";
    const where = [s.stage, s.tissue].filter(x => x && x !== "not curated");
    return `${what} in ${who}${where.length ? ", " + [...new Set(where)].join(" / ") : ""}`;
  }
  function keyMetric(s) {
    if (s.status !== "ok") return "–";
    if (s.metrics.peaks) return Z.num(s.metrics.peaks["10"]);
    if (s.metrics.meth) return Z.frac(s.metrics.meth.CpG) + " mCG";
    return "–";
  }
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.hidden = true; }, 2200);
  }
  function download(name, text, type) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: type || "text/plain" }));
    a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); toast("Copied to clipboard"); }
    catch (e) {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); toast("Copied to clipboard"); } catch (e2) { toast("Copy failed: select and copy manually"); }
      ta.remove();
    }
  }

  // ---------------------------------------------------------------- cart
  function loadCart() {
    try { return JSON.parse(localStorage.getItem(CART_KEY) || "[]"); } catch (e) { return []; }
  }
  function saveCart() {
    try { localStorage.setItem(CART_KEY, JSON.stringify(cart)); } catch (e) { /* private mode */ }
    updateCartUi();
  }
  function inCart(acc) { return cart.includes(acc); }
  function addToCart(accs) {
    let n = 0;
    for (const a of accs) if (!inCart(a) && BYACC.has(a)) { cart.push(a); n++; }
    saveCart();
    toast(n ? `Added ${n} experiment${n > 1 ? "s" : ""} to cart` : "Already in cart");
  }
  function removeFromCart(accs) {
    const rm = new Set(accs);
    cart = cart.filter(a => !rm.has(a));
    saveCart();
  }
  function cartSamples() { return cart.map(a => BYACC.get(a)).filter(Boolean); }
  function updateCartUi() {
    $("#cart-count").textContent = cart.length;
    const bar = $("#cartbar");
    const route = currentRoute().name;
    const show = cart.length > 0 && route !== "cart";
    bar.hidden = !show;
    document.body.classList.toggle("has-cartbar", show);
    if (show) {
      const files = Z.selectFiles(cartSamples(), { cats: cartOpts.cats, q: cartOpts.q });
      $("#cartbar-text").innerHTML =
        `<b>${cart.length}</b> experiment${cart.length > 1 ? "s" : ""} in cart &middot; ${files.length} files &middot; ${Z.formatBytes(Z.sumBytes(files))}`;
    }
  }

  // -------------------------------------------------------------- router
  function currentRoute() {
    const h = location.hash.replace(/^#/, "") || "/";
    const [path, query] = h.split("?");
    const parts = path.split("/").filter(Boolean);
    if (parts.length === 0) return { name: "search", query: query || "" };
    if (parts[0] === "sample" && parts[1]) return { name: "sample", acc: decodeURIComponent(parts[1]) };
    return { name: parts[0], query: query || "" };
  }

  function route() {
    const r = currentRoute();
    $$(".mainnav a, .cart-link").forEach(a => a.classList.toggle("active", a.dataset.nav === r.name));
    const view = $("#view");
    $$("dialog[open]").forEach(d => d.close());
    switch (r.name) {
      case "search": state = Z.decodeState(r.query); renderSearch(view); break;
      case "sample": renderSample(view, r.acc); break;
      case "cart": {
        // Shareable selection: #/cart?add=SRX1,SRX2 merges into the local cart.
        const add = new URLSearchParams(r.query).get("add");
        if (add) { addToCart(add.split(",").map(x => x.trim()).filter(Boolean)); history.replaceState(null, "", "#/cart"); }
        renderCart(view); break;
      }
      case "studies": renderStudies(view); break;
      case "igv": renderIgvHelp(view); break;
      case "tools": renderTools(view); break;
      case "about": renderAbout(view); break;
      default: view.innerHTML = `<div class="card"><h1>Not found</h1><p><a href="#/">Back to data</a></p></div>`;
    }
    updateCartUi();
    if (r.name !== "search") window.scrollTo(0, 0);
  }

  function pushState(replace) {
    const h = "#/" + Z.encodeState(state);
    if (replace) history.replaceState(null, "", h); else history.pushState(null, "", h);
  }

  // ------------------------------------------------------------- search
  function renderSearch(view) {
    const n = CAT.samples.length;
    const byAssay = {};
    for (const s of CAT.samples) byAssay[s.assay] = (byAssay[s.assay] || 0) + 1;
    const quick = [
      ["assay", "ChIP-seq"], ["assay", "ATAC-seq"], ["assay", "BS-seq"],
      ["target", "H3K27me3"], ["target", "H3K4me3"], ["target", "H2Aub"],
      ["genotype_class", "wild type"], ["qc", "PASS"],
    ];
    view.innerHTML = `
      <section class="hero">
        <div>
          <h1>Marchantia polymorpha epigenome data, uniformly reprocessed</h1>
          <p>Public ChIP-seq, ATAC-seq and bisulfite-seq experiments from SRA, re-analysed on one reference genome (MpTak v7.1). Filter, add to cart, then download or open in IGV.</p>
        </div>
        <div class="stats" aria-label="Catalog size">
          <div class="stat"><b>${n}</b><span>experiments</span></div>
          <div class="stat"><b>${Object.keys(CAT.studies).length}</b><span>studies</span></div>
          <div class="stat"><b>${byAssay["ChIP-seq"] || 0}/${byAssay["ATAC-seq"] || 0}/${byAssay["BS-seq"] || 0}</b><span>ChIP / ATAC / BS</span></div>
          <div class="stat"><b>v7.1</b><span>MpTak genome</span></div>
        </div>
        <div class="searchrow">
          <label class="search">
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M10 2a8 8 0 0 1 6.32 12.9l5.39 5.4-1.41 1.4-5.4-5.39A8 8 0 1 1 10 2Zm0 2a6 6 0 1 0 0 12 6 6 0 0 0 0-12Z"/></svg>
            <span class="skip">Search</span>
            <input id="q" type="search" placeholder="Search accession, antibody, genotype, study, PMID…  e.g.  H3K27me3 bmi1" value="${esc(state.q)}" autocomplete="off">
          </label>
          <div class="quick" aria-label="Quick filters">
            ${quick.map(([k, v]) => `<button type="button" class="chip${(state.filters[k] || []).includes(v) ? " on" : ""}" data-quick="${esc(k)}" data-v="${esc(v)}">${esc(v)}</button>`).join("")}
          </div>
        </div>
      </section>
      <div class="browse">
        <aside class="facets" id="facets" aria-label="Filters"></aside>
        <section class="results" id="results" aria-label="Results"></section>
      </div>`;
    const q = $("#q");
    let t = null;
    q.addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(() => { state.q = q.value.trim(); selected.clear(); pushState(true); refreshSearch(); }, 140);
    });
    $$("[data-quick]", view).forEach(b => b.addEventListener("click", () => {
      toggleFilter(b.dataset.quick, b.dataset.v);
      b.classList.toggle("on", (state.filters[b.dataset.quick] || []).includes(b.dataset.v));
    }));
    refreshSearch();
  }

  function toggleFilter(key, v) {
    const cur = new Set(state.filters[key] || []);
    if (cur.has(v)) cur.delete(v); else cur.add(v);
    if (cur.size) state.filters[key] = [...cur]; else delete state.filters[key];
    selected.clear();
    pushState(true);
    refreshSearch();
  }

  function refreshSearch() {
    renderFacets();
    renderResults();
    $$("[data-quick]").forEach(b => b.classList.toggle("on", (state.filters[b.dataset.quick] || []).includes(b.dataset.v)));
  }

  function renderFacets() {
    const el = $("#facets");
    if (!el) return;
    const counts = Z.facetCounts(CAT.samples, state);
    const nActive = Object.values(state.filters).reduce((a, v) => a + v.length, 0);
    let html = `<div class="facets-head"><h2>Filters</h2>${nActive ? `<button class="more" data-act="clear">Clear all (${nActive})</button>` : ""}</div>`;
    for (const f of Z.FACETS) {
      let entries = counts[f.key];
      if (!entries || entries.length === 0) continue;
      const sel = new Set(state.filters[f.key] || []);
      const open = facetOpen[f.key] !== undefined ? facetOpen[f.key] : (f.open || sel.size > 0);
      const find = (facetFind[f.key] || "").toLowerCase();
      if (find) entries = entries.filter(([v]) => String(displayValue(f.key, v)).toLowerCase().includes(find));
      const limit = facetMore[f.key] ? Infinity : 7;
      const shown = entries.filter(([v], i) => i < limit || sel.has(v));
      html += `<details class="facet" data-key="${f.key}"${open ? " open" : ""}>
        <summary>${esc(f.label)}${sel.size ? `<span class="sel-n">${sel.size}</span>` : ""}</summary>
        <div class="facet-body">
          ${f.search && counts[f.key].length > 8 ? `<input class="facet-filter" data-find="${f.key}" placeholder="Find ${esc(f.label.toLowerCase())}…" value="${esc(facetFind[f.key] || "")}">` : ""}
          ${shown.map(([v, c]) => `
            <label class="fv${c === 0 ? " zero" : ""}" title="${esc(f.key === "study" ? (CAT.studies[v] || {}).title || v : v)}">
              <input type="checkbox" data-facet="${f.key}" value="${esc(v)}"${sel.has(v) ? " checked" : ""}>
              <span class="v">${esc(displayValue(f.key, v))}</span><span class="n">${c}</span>
            </label>`).join("")}
          ${entries.length > 7 ? `<button class="more" data-more="${f.key}">${facetMore[f.key] ? "Show fewer" : `Show all ${entries.length}`}</button>` : ""}
        </div></details>`;
    }
    el.innerHTML = html;
    $$("input[data-facet]", el).forEach(i => i.addEventListener("change", () => toggleFilter(i.dataset.facet, i.value)));
    $$("details.facet", el).forEach(d => d.addEventListener("toggle", () => { facetOpen[d.dataset.key] = d.open; }));
    $$("[data-more]", el).forEach(b => b.addEventListener("click", () => { facetMore[b.dataset.more] = !facetMore[b.dataset.more]; renderFacets(); }));
    $$("[data-find]", el).forEach(i => i.addEventListener("input", () => {
      facetFind[i.dataset.find] = i.value; renderFacets();
      const again = $(`[data-find="${i.dataset.find}"]`); again.focus(); again.setSelectionRange(i.value.length, i.value.length);
    }));
    const clear = $("[data-act=clear]", el);
    if (clear) clear.addEventListener("click", () => { state.filters = {}; selected.clear(); pushState(true); refreshSearch(); });
  }

  function renderResults() {
    const el = $("#results");
    if (!el) return;
    const rows = Z.filterSamples(CAT.samples, state).sort(Z.SORTS[state.sort] || Z.SORTS.assay);
    const chips = [];
    if (state.q) chips.push(`<button class="chip on" data-rmq="1">“${esc(state.q)}”</button>`);
    for (const [k, vals] of Object.entries(state.filters)) {
      for (const v of vals) chips.push(`<button class="chip on" data-rmk="${esc(k)}" data-rmv="${esc(v)}" title="${esc(Z.FACET_BY_KEY[k].label)}">${esc(displayValue(k, v))}</button>`);
    }
    const allSel = rows.length > 0 && rows.every(s => selected.has(s.acc));
    const nSel = [...selected].filter(a => rows.some(s => s.acc === a)).length;
    el.innerHTML = `
      <div class="toolbar">
        <button class="btn filters-toggle" data-act="filters">Filters</button>
        <span class="count">${rows.length} of ${CAT.samples.length} experiments</span>
        <span class="activechips">${chips.join("")}</span>
        <span class="spacer"></span>
        <label class="small muted">Sort
          <select id="sort">
            ${[["assay", "Assay / target"], ["acc", "Accession"], ["study", "Study"], ["qc", "QC status"], ["mapping", "Mapping rate"], ["newest", "Newest first"]]
              .map(([v, l]) => `<option value="${v}"${state.sort === v ? " selected" : ""}>${l}</option>`).join("")}
          </select></label>
      </div>
      <div class="toolbar">
        <button class="btn primary" data-act="add-sel"${nSel ? "" : " disabled"}>Add ${nSel || ""} selected to cart</button>
        <button class="btn" data-act="add-all"${rows.length ? "" : " disabled"}>Add all ${rows.length} results</button>
        <button class="btn" data-act="igv-sel"${nSel ? "" : " disabled"}>Open selected in IGV</button>
      </div>
      <div class="tablewrap">
        ${rows.length === 0 ? `<div class="empty">No experiments match. <button class="more" data-act="clear2">Clear filters</button></div>` : `
        <table class="data">
          <thead><tr>
            <th class="cb"><input type="checkbox" id="selall" aria-label="Select all results"${allSel ? " checked" : ""}></th>
            <th>Experiment</th><th>Assay / target</th><th>Genotype</th><th>Stage</th><th>Study</th>
            <th>QC</th><th class="num" title="Peaks at q<1e-10 (ChIP/ATAC) or mean CpG methylation (BS)">Peaks / mCG</th>
            <th class="num" title="Reads aligned to MpTak v7.1 (ChIP/ATAC: after trimming; BS-seq: concordant pairs or reads)">Aligned</th><th></th>
          </tr></thead>
          <tbody>
            ${rows.map(s => `
            <tr class="${inCart(s.acc) ? "incart" : ""}">
              <td class="cb"><input type="checkbox" data-sel="${s.acc}" aria-label="Select ${s.acc}"${selected.has(s.acc) ? " checked" : ""}></td>
              <td><a class="acc" href="#/sample/${s.acc}">${s.acc}</a><span class="sub" title="${esc(s.title || "")}">${esc(s.title || "")}</span></td>
              <td data-label="Assay"><span class="assay ${assayCls(s.assay)}">${s.assay}</span> ${s.target ? `<b>${esc(s.target)}</b>` : ""}</td>
              <td data-label="Genotype">${esc(s.genotype)}<span class="sub">${esc(s.genotype_class)}</span></td>
              <td data-label="Stage">${esc(s.stage)}<span class="sub">${s.tissue !== "not curated" && s.tissue !== s.stage ? esc(s.tissue) : ""}</span></td>
              <td data-label="Study"><span class="one" title="${esc((CAT.studies[s.study] || {}).title || "")}">${esc(studyLabel(s.study))}</span><span class="sub">${esc(s.study || "")}</span></td>
              <td data-label="QC">${qcBadge(s)}</td>
              <td class="num" data-label="Peaks / mCG">${keyMetric(s)}</td>
              <td class="num" data-label="Aligned">${Z.pct(s.metrics.mapping_rate)}</td>
              <td class="rowact">
                <button class="btn sm" data-cart="${s.acc}">${inCart(s.acc) ? "&#10003; In cart" : "+ Cart"}</button>
                <button class="btn sm" data-igv="${s.acc}"${s.status === "ok" ? "" : " disabled"} title="Open signal + peaks in IGV desktop">IGV</button>
              </td>
            </tr>`).join("")}
          </tbody>
        </table>`}
      </div>
      <p class="small muted">${CAT.samples.some(x => x.qc.source === "provisional") ? `QC flags marked provisional use these rules: ${esc(CAT.qc_rules.join(" · "))}. ` : ""}QC flags come from the 2026-10-01 QC investigation. Hover a badge for the reason.</p>`;

    $("#sort", el).addEventListener("change", e => { state.sort = e.target.value; pushState(true); renderResults(); });
    const sa = $("#selall", el);
    if (sa) sa.addEventListener("change", () => {
      for (const s of rows) { if (sa.checked) selected.add(s.acc); else selected.delete(s.acc); }
      renderResults();
    });
    $$("[data-sel]", el).forEach(c => c.addEventListener("change", () => {
      if (c.checked) selected.add(c.dataset.sel); else selected.delete(c.dataset.sel);
      renderResults();
    }));
    $$("[data-cart]", el).forEach(b => b.addEventListener("click", () => {
      const a = b.dataset.cart;
      if (inCart(a)) removeFromCart([a]); else addToCart([a]);
      renderResults();
    }));
    $$("[data-igv]", el).forEach(b => b.addEventListener("click", () => openIgvDialog([BYACC.get(b.dataset.igv)], b.dataset.igv)));
    $$("[data-rmk]", el).forEach(b => b.addEventListener("click", () => toggleFilter(b.dataset.rmk, b.dataset.rmv)));
    const rq = $("[data-rmq]", el);
    if (rq) rq.addEventListener("click", () => { state.q = ""; $("#q").value = ""; pushState(true); refreshSearch(); });
    const selRows = () => rows.filter(s => selected.has(s.acc));
    $("[data-act=add-sel]", el).addEventListener("click", () => { addToCart(selRows().map(s => s.acc)); selected.clear(); renderResults(); });
    $("[data-act=add-all]", el).addEventListener("click", () => { addToCart(rows.map(s => s.acc)); renderResults(); });
    $("[data-act=igv-sel]", el).addEventListener("click", () => openIgvDialog(selRows(), `${selRows().length} selected experiments`));
    $("[data-act=filters]", el).addEventListener("click", () => $("#facets").classList.toggle("open"));
    const c2 = $("[data-act=clear2]", el);
    if (c2) c2.addEventListener("click", () => { state = { q: "", filters: {}, sort: state.sort }; $("#q").value = ""; pushState(true); refreshSearch(); });
  }

  // ------------------------------------------------------------- detail
  function renderSample(view, acc) {
    const s = BYACC.get(acc);
    if (!s) { view.innerHTML = `<div class="card"><h1>${esc(acc)} not found</h1><p><a href="#/">Back to data</a></p></div>`; return; }
    const st = CAT.studies[s.study] || {};
    const crumbFilter = (k, v) => `#/${Z.encodeState({ q: "", filters: { [k]: [v] } })}`;
    const sameStudy = CAT.samples.filter(x => x.study === s.study && x.acc !== s.acc);
    const sameTarget = s.target && s.target !== "not curated"
      ? CAT.samples.filter(x => x.target === s.target && x.acc !== s.acc && x.study !== s.study) : [];
    const m = s.metrics;
    const qcBox = s.qc.flag === "PASS" ? "" : `
      <div class="callout ${s.qc.flag === "FAIL" ? "fail" : "warn"}">
        <b>QC ${s.qc.flag}</b>${s.qc.source === "provisional" ? " (provisional rules)" : ""}: use with care.
        <ul class="reasons">${s.qc.reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul>
      </div>`;
    const fileRows = s.files.map(f => `
      <tr>
        <td>${esc(f.label)}</td><td>${esc(f.fmt)}</td>
        <td class="num">${Z.formatBytes(f.size)}</td>
        <td class="rowact"><a class="btn sm" href="${esc(Z.fileUrl(CAT, f))}" download>Download</a>
          <button class="btn sm ghost" data-copy="${esc(Z.fileUrl(CAT, f))}">Copy URL</button></td>
      </tr>`).join("");
    const results = s.status !== "ok" ? `<p>The pipeline did not complete for this experiment; no processed files are available.</p>`
      : m.peaks ? `
        <dl class="kv">
          <dt>Peaks, q &lt; 1e-5</dt><dd>${Z.num(m.peaks["05"])}</dd>
          <dt>Peaks, q &lt; 1e-10</dt><dd>${Z.num(m.peaks["10"])}</dd>
          <dt>Peaks, q &lt; 1e-20</dt><dd>${Z.num(m.peaks["20"])}</dd>
        </dl><p class="small muted">MACS3 narrow peaks; thresholds are MACS3 q-values (−log10 q ≥ 5, 10, 20).</p>`
      : `<dl class="kv">
          <dt>Mean mCpG</dt><dd>${Z.frac(m.meth.CpG)}</dd>
          <dt>Mean mCHG</dt><dd>${Z.frac(m.meth.CHG)}</dd>
          <dt>Mean mCHH</dt><dd>${Z.frac(m.meth.CHH)}</dd>
          <dt>CpG HMRs</dt><dd>${Z.num(m.meth.hmr)}</dd>
          <dt>CpG PMDs</dt><dd>${Z.num(m.meth.pmd)}</dd>
        </dl>`;
    view.innerHTML = `
      <div class="crumbs"><a href="#/">Data</a> › <a href="${crumbFilter("assay", s.assay)}">${s.assay}</a>${s.target ? ` › <a href="${crumbFilter("target", s.target)}">${esc(s.target)}</a>` : ""} › ${s.acc}</div>
      <div class="card">
        <div class="detail-head">
          <div>
            <h1>${s.acc} ${qcBadge(s)}</h1>
            <p class="lead">${esc(headline(s))}</p>
            <p class="small muted">${esc(s.title || "")} · ${esc(st.label || s.study || "")}</p>
          </div>
          <div class="btnrow">
            <button class="btn primary" data-act="cart">${inCart(s.acc) ? "&#10003; In cart" : "+ Add to cart"}</button>
            <button class="btn" data-act="igv"${s.status === "ok" ? "" : " disabled"}>Open in IGV</button>
            <button class="btn" data-act="preview"${s.status === "ok" ? "" : " disabled"}>Preview</button>
            <button class="btn ghost" data-act="session"${s.status === "ok" ? "" : " disabled"}>IGV session</button>
          </div>
        </div>
        ${qcBox}
      </div>
      <div class="detail-grid">
        <section class="card"><h2>Sample</h2>
          <dl class="kv">
            <dt>Assay</dt><dd>${s.assay}</dd>
            ${s.target ? `<dt>ChIP target</dt><dd>${esc(s.target)} <span class="muted">(${esc(s.target_class)})</span></dd>` : ""}
            <dt>Genotype</dt><dd>${esc(s.genotype)} <span class="muted">(${esc(s.genotype_class)})</span></dd>
            <dt>Developmental stage</dt><dd>${esc(s.stage)}</dd>
            <dt>Tissue</dt><dd>${esc(s.tissue)}</dd>
            ${s.cell_type ? `<dt>Cell type</dt><dd>${esc(s.cell_type)}</dd>` : ""}
            <dt>Treatment</dt><dd>${esc(s.treatment)}</dd>
            <dt>Sample title</dt><dd>${esc(s.title || "–")}</dd>
            <dt>BioSample</dt><dd><a href="https://www.ncbi.nlm.nih.gov/biosample/${esc(s.biosample)}" target="_blank" rel="noopener">${esc(s.biosample)}</a></dd>
            ${s.gsm ? `<dt>GEO sample</dt><dd><a href="https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=${esc(s.gsm)}" target="_blank" rel="noopener">${esc(s.gsm)}</a></dd>` : ""}
          </dl>
          <p class="small muted">Curated by LLM-assisted extraction from BioSample / SRA records; see <a href="#/about">About</a>.</p>
        </section>
        <section class="card"><h2>Study</h2>
          <dl class="kv">
            <dt>Title</dt><dd>${esc(st.title || "–")}</dd>
            <dt>BioProject</dt><dd><a href="https://www.ncbi.nlm.nih.gov/bioproject/${esc(s.study)}" target="_blank" rel="noopener">${esc(s.study)}</a> <span class="muted">${esc(s.srp || "")}</span></dd>
            <dt>Submitter</dt><dd>${esc(st.center || "–")}</dd>
            <dt>Publication</dt><dd>${st.pmid ? `<a href="https://pubmed.ncbi.nlm.nih.gov/${esc(st.pmid)}/" target="_blank" rel="noopener">PMID ${esc(st.pmid)}</a><br><span class="small">${esc(st.citation || "")}</span>` : `<span class="muted">not linked</span>`}</dd>
            <dt>In this catalog</dt><dd><a href="${crumbFilter("study", s.study)}">${st.n || 1} experiments from this study</a></dd>
          </dl>
        </section>
        <section class="card"><h2>Sequencing &amp; processing</h2>
          <dl class="kv">
            <dt>SRA experiment / run</dt><dd><a href="https://www.ncbi.nlm.nih.gov/sra/${s.acc}" target="_blank" rel="noopener">${s.acc}</a> / ${esc(s.run || "–")}</dd>
            <dt>Instrument</dt><dd>${esc(s.instrument || "–")} · ${esc(s.layout || "–")}</dd>
            <dt>Reads after trimming</dt><dd>${Z.num(m.reads)}</dd>
            <dt>Alignment rate</dt><dd>${Z.pct(m.mapping_rate)}</dd>
            ${s.assay !== "BS-seq" ? `<dt>Duplication rate</dt><dd>${Z.pct(m.duplication_rate)}</dd>` : ""}
            <dt>Reference</dt><dd>${esc(CAT.genome.id)} (<a href="#/igv">genome files</a>)</dd>
            <dt>Pipeline</dt><dd>${s.assay === "BS-seq" ? "chip-atlas-pipeline-v2-bs v1.1.0 (plant fork)" : "chip-atlas-pipeline-v2 v1.0.0"} · <a href="#/about">methods</a></dd>
          </dl>
        </section>
        <section class="card"><h2>Results</h2>${results}</section>
      </div>
      ${s.files.length ? `
      <section class="card"><h2>Files</h2>
        <div class="btnrow">
          <button class="btn sm" data-act="urls">Copy all URLs</button>
          <button class="btn sm" data-act="sh">Download script (.sh)</button>
          <span class="small muted">${s.files.length} files · ${Z.formatBytes(s.files.reduce((a, f) => a + (f.size || 0), 0))}</span>
        </div>
        <div class="tablewrap"><table class="data"><thead><tr><th>Content</th><th>Format</th><th class="num">Size</th><th></th></tr></thead>
        <tbody>${fileRows}</tbody></table></div>
      </section>` : ""}
      <section class="card"><h2>How to cite</h2>
        <p class="small">Please cite the original study${st.pmid ? ` (${esc(st.citation ? st.citation.split(".")[0] : "")}, PMID ${esc(st.pmid)})` : ""} and the zenigoke catalog (release ${esc(CAT.release)}). Reference genome: MarpolBase MpTak v7.1.</p>
      </section>
      ${sameStudy.length || sameTarget.length ? `
      <section class="card"><h2>Related experiments</h2>
        ${sameStudy.length ? `<p class="small"><b>Same study:</b> ${sameStudy.slice(0, 14).map(x => `<a href="#/sample/${x.acc}" title="${esc(headline(x))}">${x.acc}</a> <span class="muted">${esc(x.target || x.assay)}</span>`).join(" · ")}${sameStudy.length > 14 ? " …" : ""}</p>` : ""}
        ${sameTarget.length ? `<p class="small"><b>${esc(s.target)} in other studies:</b> ${sameTarget.slice(0, 14).map(x => `<a href="#/sample/${x.acc}" title="${esc(headline(x))}">${x.acc}</a> <span class="muted">${esc(x.genotype)}</span>`).join(" · ")}</p>` : ""}
      </section>` : ""}`;

    const one = [s];
    $("[data-act=cart]", view).addEventListener("click", e => {
      if (inCart(s.acc)) removeFromCart([s.acc]); else addToCart([s.acc]);
      e.target.innerHTML = inCart(s.acc) ? "&#10003; In cart" : "+ Add to cart";
    });
    $("[data-act=igv]", view).addEventListener("click", () => openIgvDialog(one, s.acc));
    $("[data-act=preview]", view).addEventListener("click", () => { igvCtx = makeIgvCtx(one, s.acc); openPreview(); });
    $("[data-act=session]", view).addEventListener("click", () => { igvCtx = makeIgvCtx(one, s.acc); downloadSession(); });
    $$("[data-copy]", view).forEach(b => b.addEventListener("click", () => copyText(b.dataset.copy)));
    const all = s.files.map(f => ({ s, f }));
    const u = $("[data-act=urls]", view);
    if (u) u.addEventListener("click", () => copyText(Z.filesTxt(CAT, all)));
    const sh = $("[data-act=sh]", view);
    if (sh) sh.addEventListener("click", () => download(`zenigoke_${s.acc}_download.sh`, Z.downloadSh(CAT, all, {}), "text/x-shellscript"));
  }

  // --------------------------------------------------------------- cart
  function renderCart(view) {
    const samples = cartSamples();
    if (samples.length === 0) {
      view.innerHTML = `<div class="card"><h1>Your cart is empty</h1>
        <p>Select experiments on the <a href="#/">Data</a> page (checkboxes or “+ Cart”), then come back here to download them or open them in IGV.</p>
        <p class="small muted">The cart is kept in this browser only (localStorage).</p></div>`;
      return;
    }
    const nFail = samples.filter(s => s.qc.flag === "FAIL").length;
    const assays = new Set(samples.map(s => s.assay));
    const catRows = Z.FILE_CATS.filter(c => c.assays.some(a => assays.has(a))).map(c => {
      const n = Z.selectFiles(samples, { cats: [c.cat], q: cartOpts.q }).length;
      return `<label class="check"><input type="checkbox" data-cat="${c.cat}"${cartOpts.cats.has(c.cat) ? " checked" : ""}> ${esc(c.label)} <span class="n">${n} files</span></label>`;
    }).join("");
    const hasPeaks = assays.has("ChIP-seq") || assays.has("ATAC-seq");
    view.innerHTML = `
      <h1>Cart</h1>
      <div class="cart-grid">
        <section class="card">
          <div class="toolbar"><span class="count">${samples.length} experiments</span><span class="spacer"></span>
            ${nFail ? `<button class="btn sm" data-act="rmfail">Remove ${nFail} QC FAIL</button>` : ""}
            <button class="btn sm ghost" data-act="share" title="Link that recreates this selection in any browser">Copy share link</button>
            <button class="btn sm ghost" data-act="clear">Empty cart</button></div>
          ${nFail ? `<div class="callout fail small">${nFail} experiment${nFail > 1 ? "s are" : " is"} flagged QC FAIL. They are still included unless you remove them.</div>` : ""}
          <div class="tablewrap" style="margin-top:.5rem"><table class="data">
            <thead><tr><th>Experiment</th><th>Assay / target</th><th>Genotype</th><th>QC</th><th></th></tr></thead>
            <tbody>${samples.map(s => `<tr>
              <td><a class="acc" href="#/sample/${s.acc}">${s.acc}</a><span class="sub">${esc(studyLabel(s.study))}</span></td>
              <td data-label="Assay"><span class="assay ${assayCls(s.assay)}">${s.assay}</span> ${esc(s.target || "")}</td>
              <td data-label="Genotype">${esc(s.genotype)}<span class="sub">${esc(s.stage)}</span></td>
              <td data-label="QC">${qcBadge(s)}</td>
              <td><button class="btn sm ghost" data-rm="${s.acc}" aria-label="Remove ${s.acc}">Remove</button></td></tr>`).join("")}
            </tbody></table></div>
          <div class="btnrow">
            <button class="btn primary" data-act="igv">Open in IGV</button>
            <button class="btn" data-act="preview">Preview in browser</button>
            <span class="small muted" id="igv-count"></span>
          </div>
        </section>
        <section class="card opts">
          <h2>Download</h2>
          <fieldset><legend>Files to include</legend>${catRows}</fieldset>
          ${hasPeaks ? `<fieldset><legend>Peak threshold (MACS3 q-value)</legend>
            ${["05", "10", "20"].map(k => [k, Z.Q_LABEL[k]]).map(([k, l]) => `<label class="check"><input type="radio" name="q" value="${k}"${cartOpts.q === k ? " checked" : ""}> ${l}${k === "10" ? " <span class='n'>(default)</span>" : ""}</label>`).join("")}
          </fieldset>` : ""}
          <fieldset><legend>Local file names</legend>
            <label class="check"><input type="radio" name="naming" value="original"${cartOpts.naming === "original" ? " checked" : ""}> Original: <code>ChIP-seq/SRX9800305/SRX9800305.bw</code></label>
            <label class="check"><input type="radio" name="naming" value="descriptive"${cartOpts.naming === "descriptive" ? " checked" : ""}> Descriptive: <code>…/SRX9800305_ChIP-seq_H3K27me3_Mpbmi1-1l_thallus.bw</code></label>
          </fieldset>
          <p class="summary-line" id="dl-summary"></p>
          <div class="tabs" role="tablist">
            ${[["files", "URL list"], ["sh", "Bash script"], ["meta", "Metadata TSV"], ["manifest", "File manifest TSV"], ["session", "IGV session XML"]]
              .map(([k, l]) => `<button role="tab" data-tab="${k}" aria-selected="${cartOpts.tab === k}">${l}</button>`).join("")}
          </div>
          <p class="small muted" id="tab-hint"></p>
          <textarea class="out" id="out" readonly spellcheck="false" aria-label="Generated output"></textarea>
          <div class="btnrow">
            <button class="btn primary" data-act="dl">Download file</button>
            <button class="btn" data-act="copy">Copy</button>
          </div>
        </section>
      </div>`;

    const refreshOut = () => {
      const list = Z.selectFiles(samples, { cats: cartOpts.cats, q: cartOpts.q });
      const tracks = Z.igvTracks(CAT, samples, { q: cartOpts.q });
      $("#dl-summary").innerHTML = `<b>${list.length}</b> files · <b>${Z.formatBytes(Z.sumBytes(list))}</b> from ${samples.length} experiments`;
      $("#igv-count").textContent = `${tracks.length} IGV tracks${tracks.length > MAX_IGV_TRACKS ? " (large: IGV may be slow)" : ""}`;
      const out = outputFor(cartOpts.tab, samples, list, tracks);
      $("#out").value = out.text;
      $("#tab-hint").innerHTML = out.hint;
      updateCartUi();
    };
    $$("[data-cat]", view).forEach(c => c.addEventListener("change", () => {
      if (c.checked) cartOpts.cats.add(c.dataset.cat); else cartOpts.cats.delete(c.dataset.cat);
      refreshOut();
    }));
    $$("input[name=q]", view).forEach(r => r.addEventListener("change", () => { cartOpts.q = r.value; renderCart(view); }));
    $$("input[name=naming]", view).forEach(r => r.addEventListener("change", () => { cartOpts.naming = r.value; refreshOut(); }));
    $$("[data-tab]", view).forEach(b => b.addEventListener("click", () => {
      cartOpts.tab = b.dataset.tab;
      $$("[data-tab]", view).forEach(x => x.setAttribute("aria-selected", String(x === b)));
      refreshOut();
    }));
    $$("[data-rm]", view).forEach(b => b.addEventListener("click", () => { removeFromCart([b.dataset.rm]); renderCart(view); }));
    const rf = $("[data-act=rmfail]", view);
    if (rf) rf.addEventListener("click", () => { removeFromCart(samples.filter(s => s.qc.flag === "FAIL").map(s => s.acc)); renderCart(view); });
    $("[data-act=clear]", view).addEventListener("click", () => { removeFromCart(cart.slice()); renderCart(view); });
    $("[data-act=share]", view).addEventListener("click", () => copyText(location.href.split("#")[0] + "#/cart?add=" + cart.join(",")));
    $("[data-act=igv]", view).addEventListener("click", () => openIgvDialog(samples, `cart (${samples.length} experiments)`));
    $("[data-act=preview]", view).addEventListener("click", () => { igvCtx = makeIgvCtx(samples, "cart"); openPreview(); });
    $("[data-act=copy]", view).addEventListener("click", () => copyText($("#out").value));
    $("[data-act=dl]", view).addEventListener("click", () => {
      const out = outputFor(cartOpts.tab, samples, Z.selectFiles(samples, { cats: cartOpts.cats, q: cartOpts.q }), Z.igvTracks(CAT, samples, { q: cartOpts.q }));
      download(out.name, out.text, out.type);
    });
    refreshOut();
  }

  function outputFor(tab, samples, list, tracks) {
    const stamp = new Date().toISOString().slice(0, 10);
    switch (tab) {
      case "sh": return {
        name: `zenigoke_download_${stamp}.sh`, type: "text/x-shellscript",
        text: Z.downloadSh(CAT, list, { naming: cartOpts.naming }),
        hint: "Run <code>bash zenigoke_download_*.sh [dir]</code>. Needs only curl; re-running resumes partial downloads.",
      };
      case "meta": return {
        name: `zenigoke_metadata_${stamp}.tsv`, type: "text/tab-separated-values",
        text: Z.metadataTsv(CAT, samples),
        hint: "One row per experiment: curated sample attributes, study / PMID, QC metrics and flag. Opens in Excel or R (<code>read.delim</code>).",
      };
      case "manifest": return {
        name: `zenigoke_files_${stamp}.tsv`, type: "text/tab-separated-values",
        text: Z.manifestTsv(CAT, list, { naming: cartOpts.naming }),
        hint: "One row per file: type, format, size, local path and URL. Join with the metadata TSV on <code>accession</code>.",
      };
      case "session": return {
        name: `zenigoke_igv_session_${stamp}.xml`, type: "application/xml",
        text: Z.igvSessionXml(CAT, tracks, { locus: CAT.genome.default_locus }),
        hint: "In IGV desktop: <i>File › Open Session…</i> and pick this file. Works without the IGV port and in any browser.",
      };
      default: return {
        name: `zenigoke_files_${stamp}.txt`, type: "text/plain",
        text: Z.filesTxt(CAT, list),
        hint: "One URL per line (same convention as ENCODE). E.g. <code>xargs -n 1 -P 4 curl -fLO &lt; files.txt</code> or <code>wget -i files.txt</code>.",
      };
    }
  }

  // ------------------------------------------------------------ studies
  function renderStudies(view) {
    const studies = Object.entries(CAT.studies).sort((a, b) => String(b[1].first_public).localeCompare(String(a[1].first_public)));
    view.innerHTML = `<h1>Studies</h1>
      <p class="muted">${studies.length} BioProjects contribute experiments to this release. Click a study to filter the data table.</p>
      <div class="tablewrap"><table class="data">
        <thead><tr><th>Study</th><th>BioProject</th><th>Publication</th><th class="num">Experiments</th><th>Assays</th></tr></thead>
        <tbody>${studies.map(([acc, st]) => {
          const ss = CAT.samples.filter(s => s.study === acc);
          const by = {};
          for (const s of ss) by[s.assay] = (by[s.assay] || 0) + 1;
          return `<tr>
            <td><a href="#/${Z.encodeState({ q: "", filters: { study: [acc] } })}">${esc(st.title)}</a><span class="sub">${esc(st.center || "")}</span></td>
            <td data-label="BioProject"><a href="https://www.ncbi.nlm.nih.gov/bioproject/${esc(acc)}" target="_blank" rel="noopener">${esc(acc)}</a><span class="sub">${esc(st.srp || "")} · ${esc((st.first_public || "").slice(0, 4))}</span></td>
            <td data-label="Publication">${st.pmid ? `<a href="https://pubmed.ncbi.nlm.nih.gov/${esc(st.pmid)}/" target="_blank" rel="noopener">PMID ${esc(st.pmid)}</a><span class="sub" title="${esc(st.citation || "")}">${esc(st.citation || "")}</span>` : `<span class="muted">not linked</span>`}</td>
            <td class="num" data-label="Experiments">${ss.length}</td>
            <td data-label="Assays">${Object.entries(by).map(([a, n]) => `<span class="assay ${assayCls(a)}">${a} ${n}</span>`).join(" ")}</td></tr>`;
        }).join("")}</tbody></table></div>`;
  }

  // ------------------------------------------------------- genome & IGV
  function renderIgvHelp(view) {
    const g = CAT.genome;
    const row = (label, key, url) => {
      const h = g.hosted[key];
      return `<tr><td>${label}</td><td><code class="small">${esc(url.replace(CAT.data_base, ""))}</code></td>
        <td>${h === true ? `<span class="qc PASS">hosted</span>` : h === false ? `<span class="qc FAIL" title="Not on S3 yet: Phase 1 of the proposal">missing</span>` : `<span class="muted">not checked</span>`}</td></tr>`;
    };
    view.innerHTML = `<h1>Genome &amp; IGV</h1>
      <div class="detail-grid">
        <section class="card"><h2>Open data in IGV desktop</h2>
          <ol>
            <li><b>One click</b>: press <i>Open in IGV</i> on any experiment or on the cart. Needs IGV running with <i>View › Preferences › Advanced › Enable port 60151</i>. Chrome/Edge will ask once to allow access to apps on this device.</li>
            <li><b>Session file</b> (works everywhere, incl. Safari): download the IGV session XML from the cart, then <i>File › Open Session…</i>.</li>
            <li><b>By hand</b>: <i>Genomes › Load Genome from URL…</i> with the genome JSON below, then <i>File › Load from URL…</i> with any file URL.</li>
          </ol>
          <div class="btnrow">
            <button class="btn" data-act="ping">Test connection to IGV</button>
            <button class="btn" data-act="genome">Load genome only</button>
            <span class="status" id="ping-status" role="status" aria-live="polite"></span>
          </div>
        </section>
        <section class="card"><h2>Reference genome: ${esc(g.id)}</h2>
          <p class="small">${esc(g.name)}. Source: <a href="${esc(g.source)}" target="_blank" rel="noopener">MarpolBase</a>. MpTak v7.1 is not an IGV-hosted genome, so the catalog serves its own copy.</p>
          <div class="tablewrap"><table class="data"><thead><tr><th>File</th><th>Path on S3</th><th>Status</th></tr></thead><tbody>
            ${row("IGV genome JSON", "json", g.json)}${row("FASTA", "fasta", g.fasta)}${row("FASTA index", "fai", g.fai)}
            ${row("Gene models (GFF3)", "gff", g.gff)}${row("chrom.sizes", "chrom_sizes", g.chrom_sizes)}
          </tbody></table></div>
          <p class="small muted">Genome JSON URL for IGV: <code>${esc(g.json)}</code> (<a href="data/${esc(g.id)}.igv.json">proposed content</a>)</p>
          <p class="small muted">Interim alternative: the same assembly is on UCSC GenArk as <a href="https://genome.ucsc.edu/h/${esc(g.genark.accession)}" target="_blank" rel="noopener">${esc(g.genark.accession)}</a> (hub: <code>${esc(g.genark.hub)}</code>). Chromosome lengths match chrom.sizes exactly; names chr1–chr8, chrU, chrV are aliases.</p>
        </section>
      </div>`;
    $("[data-act=ping]", view).addEventListener("click", async () => {
      const el = $("#ping-status");
      el.className = "status busy"; el.textContent = "Looking for IGV on port 60151…";
      const perm = await lnaState();
      const r = perm === "denied" ? { ok: false } : await igvProbe(perm);
      el.className = "status " + (r.ok ? "ok" : "err");
      el.textContent = r.ok ? `IGV ${r.version || ""} is reachable (browser permission: ${perm}).`
        : perm === "denied" ? "Blocked by the browser's local-network permission for this site."
        : "No answer. Is IGV running with the port enabled? Safari blocks this check; use the session file.";
    });
    $("[data-act=genome]", view).addEventListener("click", () => {
      openIgvDialog([], "genome only");
    });
  }

  function renderTools(view) {
    view.innerHTML = `<h1>Tools</h1>
      <div class="detail-grid">
        <section class="card"><h2>Enrichment analysis</h2>
          <p>Upload a BED of regions and rank all catalog experiments by peak overlap (ChIP-Atlas-style “in silico ChIP”).</p>
          <a class="btn primary" href="${LIVE}enrichment.html" target="_blank" rel="noopener">Open enrichment tool</a></section>
        <section class="card"><h2>Overview matrix</h2>
          <p>Cross-tabulate experiments by two attributes (the current site’s landing page), kept as an exploratory view.</p>
          <a class="btn" href="${LIVE}index.html" target="_blank" rel="noopener">Open matrix (current site)</a></section>
        <section class="card"><h2>Programmatic access</h2>
          <p>The whole catalog is one JSON file, plus the SQLite DB:</p>
          <pre class="code">curl -s ${location.href.split("#")[0]}data/catalog.json | jq '.samples[] | select(.target=="H3K27me3") | .acc'</pre></section>
      </div>`;
  }

  function renderAbout(view) {
    view.innerHTML = `<h1>About</h1>
      <div class="detail-grid">
        <section class="card"><h2>What is zenigoke?</h2>
          <p>A catalog of public <i>Marchantia polymorpha</i> ChIP-seq, ATAC-seq and bisulfite-seq data from SRA, re-processed with one pipeline against MpTak v7.1, in the spirit of ChIP-Atlas.</p>
          <p class="small">Release ${esc(CAT.release)} · ${CAT.samples.length} experiments · ${Object.keys(CAT.studies).length} studies.</p></section>
        <section class="card"><h2>How to cite &amp; data use</h2>
          <p class="small">Cite the original study of each experiment (PMIDs are in the metadata TSV), the catalog, and MarpolBase for the genome. Processed files are free to reuse; raw data terms follow INSDC.</p></section>
        <section class="card"><h2>Methods</h2>
          <ul class="small">
            <li>ChIP/ATAC: chip-atlas-pipeline-v2 v1.0.0 (fastp, bwa-mem2, samtools, MACS3 q &lt; 1e-5 / 1e-10 / 1e-20, bigWig).</li>
            <li>BS-seq: chip-atlas-pipeline-v2-bs v1.1.0 + plant fork (DNMTools; CpG/CHG/CHH).</li>
            <li>Metadata: bsllmner-mk2 LLM-assisted curation of BioSample / SRA records.</li>
          </ul>
          <a href="${LIVE}methods.html" target="_blank" rel="noopener">Full methods &amp; versions</a></section>
        <section class="card"><h2>Project records</h2>
          <p class="small">Pipeline run summary and processing log (for maintainers): <a href="${LIVE}summary.html" target="_blank" rel="noopener">processing summary</a>.</p></section>
      </div>`;
  }

  // ---------------------------------------------------------------- IGV
  function makeIgvCtx(samples, label) {
    const q = cartOpts.q;
    return { samples, tracks: Z.igvTracks(CAT, samples.filter(s => s.status === "ok"), { q }), label };
  }

  /** Local-network permission state (Chrome/Edge 142+, Firefox LNA).
   *  "loopback-network" since Chrome 145, "local-network-access" before.
   *  Returns "granted" | "denied" | "prompt" | "unknown" (Safari, older browsers). */
  async function lnaState() {
    if (!navigator.permissions || !navigator.permissions.query) return "unknown";
    for (const name of ["loopback-network", "local-network-access"]) {
      try { return (await navigator.permissions.query({ name })).state; } catch (e) { /* unknown name */ }
    }
    return "unknown";
  }

  /** GET to IGV's port. IGV answers with Access-Control-Allow-Origin: *, so a
   *  normal CORS fetch can read the reply: 204 = OK, 200 + "ERROR..." = IGV error.
   *  A TypeError means: nothing listening, port disabled, blocked as mixed
   *  content (Safari) or local-network permission denied. timeoutMs = 0: none. */
  async function igvFetch(url, timeoutMs, mode) {
    const ctrl = new AbortController();
    const timer = timeoutMs ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
    try {
      const r = await fetch(url, { mode: mode || "cors", cache: "no-store", signal: ctrl.signal });
      const text = r.type === "opaque" ? "" : await r.text();
      return { ok: true, opaque: r.type === "opaque", status: r.status, text: text.trim() };
    } catch (e) {
      return { ok: false, timeout: e.name === "AbortError", error: String(e) };
    } finally { if (timer) clearTimeout(timer); }
  }

  /** Is IGV there? /execute?command=version answers immediately (unlike /load,
   *  which replies only after loading). Falls back to no-cors for IGV builds
   *  that do not send CORS headers. */
  async function igvProbe(perm) {
    const url = Z.IGV_PORT_BASE + "/execute?command=version";
    // While a permission prompt is open the request just waits: no short timer.
    const r = await igvFetch(url, perm === "granted" ? 4000 : 120000);
    if (r.ok) return { ok: true, version: /^\d/.test(r.text) ? r.text : "", opaque: false };
    if (r.timeout) return r;
    const r2 = await igvFetch(url, 4000, "no-cors");
    return r2.ok ? { ok: true, version: "", opaque: true } : r;
  }

  function igvFail(msg) {
    const st = $("#igv-status");
    st.className = "status err";
    st.innerHTML = msg;
    $("#igv-fallback").classList.add("highlight");
    $("#igv-fallback details").open = true;
  }

  function openIgvDialog(samples, label) {
    igvCtx = makeIgvCtx(samples, label);
    const d = $("#igv-dialog");
    const n = igvCtx.tracks.length;
    $("#igv-summary").innerHTML = n
      ? `${n} track${n > 1 ? "s" : ""} from ${esc(label)}${n > MAX_IGV_TRACKS ? ` <b>(many tracks: IGV may be slow)</b>` : ""}.`
      : `Load the ${esc(CAT.genome.id)} genome and gene models into IGV.`;
    $("#igv-locus").value = CAT.genome.default_locus;
    $("#igv-status").textContent = ""; $("#igv-status").className = "status";
    $("#igv-fallback").classList.remove("highlight");
    $("#igv-fallback details").open = false;
    // Until the genome JSON is on S3 (proposal Phase 1) the mockup defaults to
    // the UCSC GenArk copy of the same assembly.
    const sel = $("#igv-genome-src");
    sel.querySelector("option[value=hosted]").disabled = !CAT.genome.hosted.json;
    sel.value = Z.defaultGenomeSrc(CAT);
    updateGenomeNote();
    updateIgvLink();
    if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", "");
  }

  function currentLoadUrl() {
    return Z.igvLoadUrl(CAT, igvCtx.tracks, {
      locus: $("#igv-locus").value.trim(), genomeSrc: $("#igv-genome-src").value,
    });
  }

  function updateGenomeNote() {
    const v = $("#igv-genome-src").value;
    const note = $("#igv-genome-note");
    const msg = {
      hosted: "",
      genark: (CAT.genome.hosted.json ? "" : "The zenigoke genome files are not on S3 yet (proposal Phase 1). ") +
        "GenArk serves the same MpTak_v7.1 assembly (chromosome lengths match; chr1–chr8, chrU, chrV are aliases); its gene track is NCBI's annotation, not MarpolBase Mp*g IDs.",
      none: "Tracks go onto whatever genome IGV has open. If that is not MpTak v7.1 they will not line up.",
    }[v];
    note.textContent = msg; note.hidden = !msg;
  }

  function updateIgvLink() { $("#igv-link").href = currentLoadUrl(); }

  async function sendToIgv() {
    const st = $("#igv-status");
    const btn = $("#igv-send");
    btn.disabled = true;
    try {
      const perm = await lnaState();
      if (perm === "denied") {
        igvFail("This browser is blocking this site from talking to apps on your computer. Re-allow it in the site settings (padlock icon › Site settings › Local network / Apps on device), or use a fallback below.");
        return;
      }
      st.className = "status busy";
      st.textContent = perm === "prompt"
        ? "Your browser will ask to let this site access apps on this device. Choose Allow."
        : "Looking for IGV on port 60151…";
      const probe = await igvProbe(perm);
      if (!probe.ok) {
        igvFail(probe.timeout
          ? "No answer from IGV (the permission prompt may have been dismissed)."
          : "Could not reach IGV. It is not running, its port is disabled, or this browser blocks page-to-localhost calls (Safari). Try <b>Send via link</b> or the session file below.");
        return;
      }
      st.textContent = `IGV ${probe.version || ""} found. Loading ${igvCtx.tracks.length} tracks… (IGV replies when loading has finished)`;
      // No timeout: IGV answers /load only after the genome and tracks are loaded.
      const r = await igvFetch(currentLoadUrl(), 0, probe.opaque ? "no-cors" : "cors");
      if (!r.ok) { igvFail("IGV stopped answering while loading. Check the IGV window."); return; }
      if (!r.opaque && /^error/i.test(r.text)) { igvFail("IGV reported: " + esc(r.text.slice(0, 300))); return; }
      st.className = "status ok";
      st.textContent = r.opaque ? "Sent to IGV (this IGV version cannot confirm). Switch to the IGV window." : "Loaded in IGV. Switch to the IGV window.";
    } finally { btn.disabled = false; }
  }

  function downloadSession() {
    if (!igvCtx || igvCtx.tracks.length === 0) { toast("Nothing to put in a session"); return; }
    const open = $("#igv-dialog").open;
    const locus = (open && $("#igv-locus").value.trim()) || CAT.genome.default_locus;
    const genomeSrc = open ? $("#igv-genome-src").value : Z.defaultGenomeSrc(CAT);
    download(`zenigoke_igv_session_${new Date().toISOString().slice(0, 10)}.xml`,
      Z.igvSessionXml(CAT, igvCtx.tracks, { locus, genomeSrc }), "application/xml");
  }

  // ------------------------------------------------- igv.js preview
  let igvJsLoading = null;
  function loadIgvJs() {
    if (window.igv) return Promise.resolve(window.igv);
    if (!igvJsLoading) {
      igvJsLoading = new Promise((resolve, reject) => {
        const sc = document.createElement("script");
        sc.src = IGVJS_SRC; sc.async = true;
        sc.onload = () => resolve(window.igv);
        sc.onerror = () => { igvJsLoading = null; reject(new Error("could not load igv.js from CDN")); };
        document.head.appendChild(sc);
      });
    }
    return igvJsLoading;
  }

  async function openPreview() {
    if (!igvCtx || igvCtx.tracks.length === 0) { toast("No tracks to preview"); return; }
    const d = $("#preview-dialog");
    if ($("#igv-dialog").open) $("#igv-dialog").close();
    if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", "");
    const note = $("#preview-note");
    const cont = $("#igv-js-container");
    const maxTracks = 16;
    const tracks = igvCtx.tracks.slice(0, maxTracks);
    const corsOk = /\.github\.io$/.test(location.hostname) || location.origin === "http://localhost:8088";
    note.innerHTML = `Quick look with <a href="https://github.com/igvteam/igv.js" target="_blank" rel="noopener">igv.js</a>: ${tracks.length} of ${igvCtx.tracks.length} tracks streamed from S3.` +
      (CAT.genome.hosted.fasta ? "" : ` Genome: UCSC GenArk ${esc(CAT.genome.genark.accession)} (same assembly; zenigoke FASTA/GFF not hosted yet).`) +
      (corsOk ? "" : ` <b>S3 CORS currently allows only https://*.github.io and http://localhost:8088; serve this mockup on port 8088 to see tracks.</b>`);
    cont.innerHTML = `<p class="muted" style="padding:1rem">Loading igv.js…</p>`;
    try {
      const igv = await loadIgvJs();
      if (igv.removeAllBrowsers) igv.removeAllBrowsers();
      cont.innerHTML = "";
      const opts = Z.igvJsOptions(CAT, tracks, { locus: CAT.genome.default_locus });
      const trackConfigs = opts.tracks;
      // Create the browser first, then add tracks, so a track that fails (CORS,
      // network) does not prevent the genome view from appearing.
      const browser = await igv.createBrowser(cont, Object.assign({}, opts, { tracks: [] }));
      // A GenArk genome starts at the hub's defaultPos; chr6 etc. resolve via its alias table.
      if (opts.genome) await browser.search(opts.locus);
      try { await browser.loadTrackList(trackConfigs); }
      catch (e) { note.innerHTML += ` <span class="status err">Some tracks failed to load: ${esc(e.message || e)}</span>`; }
    } catch (e) {
      cont.innerHTML = `<p class="status err" style="padding:1rem">Preview failed: ${esc(e.message || e)}</p>`;
    }
  }

  // ---------------------------------------------------------------- boot
  function wireStatic() {
    $("#igv-send").addEventListener("click", sendToIgv);
    $("#igv-session").addEventListener("click", downloadSession);
    $("#igv-preview").addEventListener("click", openPreview);
    $("#igv-copy").addEventListener("click", () => copyText(currentLoadUrl()));
    $("#igv-locus").addEventListener("input", updateIgvLink);
    $("#igv-genome-src").addEventListener("change", () => { updateGenomeNote(); updateIgvLink(); });
    $("[data-action=igv-cart]").addEventListener("click", () => openIgvDialog(cartSamples(), `cart (${cart.length} experiments)`));
    window.addEventListener("hashchange", route);
    window.addEventListener("storage", e => { if (e.key === CART_KEY) { cart = loadCart(); updateCartUi(); } });
  }

  async function boot() {
    wireStatic();
    try {
      const r = await fetch("data/catalog.json", { cache: "no-cache" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      CAT = Z.prepare(await r.json());
    } catch (e) {
      $("#view").innerHTML = `<div class="card"><h1>Could not load data/catalog.json</h1><p>${esc(e.message)}. Run <code>python3 make_index.py</code> and serve this folder over HTTP (<code>python3 -m http.server</code>).</p></div>`;
      return;
    }
    for (const s of CAT.samples) BYACC.set(s.acc, s);
    cart = cart.filter(a => BYACC.has(a));
    route();
  }

  boot();
})();
