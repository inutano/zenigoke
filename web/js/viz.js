/* kkmm web UI: overview visualisations (matrix presets, year trend,
 * source map). Plain SVG strings, no libraries.
 *
 * Every clickable mark carries data-f='{"facet":["value",...]}' (a filter
 * set in the search-state vocabulary of core.js) and data-tip (tooltip
 * text). The caller decides what a click does; V.bindTips() wires hover.
 * Loaded as a classic script (window.ZViz) and by test/viz.test.js (node).
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ZViz = factory();
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const ASSAYS = ["ChIP-seq", "ATAC-seq", "BS-seq"];
  const ASSAY_CLS = { "ChIP-seq": "a-chip", "ATAC-seq": "a-atac", "BS-seq": "a-bs" };
  const CHIP_GROUPS = [
    ["histone modification", "Histone marks"],
    ["histone / variant", "Histones & variants"],
    ["transcription factor", "Transcription factors"],
    ["control", "Controls (input, IgG, tag)"],
    ["not curated", "Target not curated"],
  ];

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  const fattr = f => esc(JSON.stringify(f));
  const plural = n => `${n} experiment${n === 1 ? "" : "s"}`;
  function qcLine(list) {
    const c = { PASS: 0, WARN: 0, FAIL: 0 };
    for (const s of list) c[s.qc.flag] = (c[s.qc.flag] || 0) + 1;
    return ["PASS", "WARN", "FAIL"].filter(k => c[k]).map(k => `${k} ${c[k]}`).join(" · ");
  }

  // ------------------------------------------------------------ matrix
  // Rows are "data types" (what was measured); columns come from a preset.
  // Structure is always built from the whole catalog so it does not jump
  // around when filters change; counts come from the filtered rows.
  // compact (hero) mode keeps histone rows but folds TFs, controls and
  // not-curated targets into one row per group, so the card stays short.
  const FOLD = { "transcription factor": ["transcription factor"], control: ["control (input / IgG)", "tag only (control)"], "not curated": ["not curated"] };
  function dataTypes(all, compact) {
    const groups = [];
    const atac = all.filter(s => s.assay === "ATAC-seq").length;
    const bs = all.filter(s => s.assay === "BS-seq").length;
    if (atac) groups.push({ label: "Chromatin accessibility", rows: [{ label: "ATAC-seq", assay: "ATAC-seq", f: { assay: ["ATAC-seq"] }, m: s => s.assay === "ATAC-seq" }] });
    if (bs) groups.push({ label: "DNA methylation", rows: [{ label: "BS-seq (CpG/CHG/CHH)", assay: "BS-seq", f: { assay: ["BS-seq"] }, m: s => s.assay === "BS-seq" }] });
    const chip = all.filter(s => s.assay === "ChIP-seq");
    const groupOf = s => {
      const c = s.target_class || "not curated";
      return c.startsWith("control") || c.startsWith("tag only") ? "control" : c;
    };
    for (const [cls, label] of CHIP_GROUPS) {
      const counts = new Map();
      for (const s of chip) if (groupOf(s) === cls) counts.set(s.target, (counts.get(s.target) || 0) + 1);
      if (!counts.size) continue;
      if (compact && FOLD[cls]) {
        const tc = FOLD[cls];
        groups.push({ label: "ChIP-seq · " + label, rows: [{ label: `${counts.size} target${counts.size > 1 ? "s" : ""}: ${[...counts.keys()].slice(0, 3).join(", ")}${counts.size > 3 ? "…" : ""}`, assay: "ChIP-seq", f: { assay: ["ChIP-seq"], target_class: tc }, m: s => s.assay === "ChIP-seq" && tc.includes(s.target_class || "not curated") }] });
        continue;
      }
      const rows = [...counts.entries()].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])))
        .map(([t]) => ({ label: t, assay: "ChIP-seq", f: { assay: ["ChIP-seq"], target: [t] }, m: s => s.assay === "ChIP-seq" && s.target === t }));
      groups.push({ label: "ChIP-seq · " + label, rows });
    }
    return groups;
  }

  const PRESETS = [
    { id: "genotype", label: "Genotype", facet: "genotype_class", get: s => s.genotype_class, order: ["wild type", "mutant", "overexpression"] },
    { id: "stage", label: "Developmental stage", facet: "stage", get: s => s.stage, last: ["not curated"] },
    { id: "treatment", label: "Treatment", facet: "treatment", get: s => s.treatment, order: ["none / not stated", "control", "heat stress", "ABA"] },
    { id: "study", label: "Study (lab, year)", facet: "study", get: s => s.study, byStudyDate: true },
  ];
  const PRESET_BY_ID = Object.fromEntries(PRESETS.map(p => [p.id, p]));

  function presetColumns(preset, all, studies) {
    const counts = new Map();
    for (const s of all) { const v = preset.get(s); if (v) counts.set(v, (counts.get(v) || 0) + 1); }
    let vals = [...counts.keys()];
    if (preset.order) vals.sort((a, b) => idx(preset.order, a) - idx(preset.order, b) || counts.get(b) - counts.get(a));
    else if (preset.byStudyDate) vals.sort((a, b) => String((studies[a] || {}).first_public).localeCompare(String((studies[b] || {}).first_public)));
    else vals.sort((a, b) => (preset.last || []).includes(a) - (preset.last || []).includes(b) || counts.get(b) - counts.get(a));
    return vals.map(v => {
      let label = v, sub = "";
      if (preset.byStudyDate) {
        const st = studies[v] || {};
        label = st.inst_short || v;
        sub = (st.first_public || "").slice(0, 4);
      }
      return { v, label, sub, f: { [preset.facet]: [v] } };
    });
  }
  function idx(arr, v) { const i = arr.indexOf(v); return i < 0 ? 999 : i; }

  // fixed bins so a colour means the same count in every preset and view
  const BINS = [[1, 1, "1"], [2, 3, "2–3"], [4, 7, "4–7"], [8, 15, "8–15"], [16, Infinity, "16+"]];
  function bin(n) { if (n <= 0) return 0; for (let i = 0; i < BINS.length; i++) if (n <= BINS[i][1]) return i + 1; return 5; }

  // opts: { compact, selected: "rowLabel|colValue" }
  function matrixSvg(rows, all, studies, presetId, opts) {
    opts = opts || {};
    const preset = PRESET_BY_ID[presetId] || PRESETS[0];
    const groups = dataTypes(all, opts.compact);
    const cols = presetColumns(preset, all, studies);
    const C = opts.compact
      ? { rowH: 19, gapG: 16, labW: 196, font: 11 }
      : { rowH: 26, gapG: 22, labW: 220, font: 12.5 };
    const avail = (opts.width || (opts.compact ? 600 : 900)) - C.labW - 36;
    C.colW = Math.max(opts.compact ? 40 : 56, Math.min(opts.compact ? 104 : 120, Math.floor(avail / cols.length)));
    // column labels: horizontal when they fit, rotated otherwise (rotated
    // labels lean right past the last column, so make room for them)
    const longest = Math.max(...cols.map(c => c.label.length + (c.sub ? 4 : 0)));
    const rotate = longest * C.font * 0.58 > C.colW - 4;
    const lean = rotate ? Math.round(longest * C.font * 0.45) : 0;
    if (rotate) C.colW = Math.max(opts.compact ? 36 : 48, Math.min(C.colW, Math.floor((avail - lean) / cols.length)));
    const headH = rotate ? Math.min(120, 18 + longest * C.font * 0.45) : (preset.byStudyDate ? 34 : 22);
    const totW = 36;
    const W = C.labW + cols.length * C.colW + totW + lean;
    let y = headH + 4;
    const parts = [];
    // header
    cols.forEach((c, i) => {
      const cx = C.labW + i * C.colW + C.colW / 2;
      const tip = preset.byStudyDate ? `${(studies[c.v] || {}).institution || c.v} · ${c.v} (${c.sub})` : `${preset.label}: ${c.v}`;
      if (rotate) {
        parts.push(`<g class="mx-col" data-f="${fattr(c.f)}" data-tip="${esc(tip)}" tabindex="0" role="button"><text transform="translate(${cx - 2},${headH - 2}) rotate(-40)" font-size="${C.font}">${esc(c.label)}${c.sub ? ` <tspan class="sub">’${esc(c.sub.slice(2))}</tspan>` : ""}</text></g>`);
      } else {
        parts.push(`<g class="mx-col" data-f="${fattr(c.f)}" data-tip="${esc(tip)}" tabindex="0" role="button"><text x="${cx}" y="${preset.byStudyDate ? headH - 16 : headH - 4}" text-anchor="middle" font-size="${C.font}">${esc(c.label)}</text>${c.sub ? `<text class="sub" x="${cx}" y="${headH - 3}" text-anchor="middle" font-size="${C.font - 1.5}">${esc(c.sub)}</text>` : ""}</g>`);
      }
    });
    const totX = C.labW + cols.length * C.colW + totW / 2;
    parts.push(`<text class="mx-tot-h" x="${totX}" y="${headH - 4}" text-anchor="middle" font-size="${C.font - 1}">all</text>`);
    for (const g of groups) {
      y += 2;
      parts.push(`<text class="mx-group" x="0" y="${y + C.gapG - 5}" font-size="${C.font - 1}">${esc(g.label)}</text>`);
      y += C.gapG;
      for (const r of g.rows) {
        const inRow = rows.filter(r.m);
        parts.push(`<g class="mx-row" data-f="${fattr(r.f)}" data-tip="${esc(`${r.label}: ${plural(inRow.length)}${inRow.length ? " (" + qcLine(inRow) + ")" : ""}`)}" tabindex="0" role="button">
          <rect class="mx-assay ${ASSAY_CLS[r.assay]}" x="0" y="${y + 3}" width="4" height="${C.rowH - 6}" rx="1"/>
          <text x="10" y="${y + C.rowH / 2 + C.font * 0.36}" font-size="${C.font}">${esc(r.label)}</text></g>`);
        cols.forEach((c, i) => {
          const cell = inRow.filter(s => preset.get(s) === c.v);
          const n = cell.length;
          const x = C.labW + i * C.colW;
          const key = `${r.label}|${c.v}`;
          const sel = opts.selected === key ? " sel" : "";
          if (!n) {
            parts.push(`<rect class="mx-empty" x="${x + 1}" y="${y + 1}" width="${C.colW - 2}" height="${C.rowH - 2}" rx="3"/>`);
            return;
          }
          const b = bin(n);
          const colLab = preset.byStudyDate ? `${c.label} ${c.sub}` : c.v;
          parts.push(`<g class="mx-cell b${b}${sel}" data-key="${esc(key)}" data-f="${fattr(Object.assign({}, r.f, c.f))}" data-tip="${esc(`${r.label} × ${colLab}: ${plural(n)} (${qcLine(cell)})`)}" tabindex="0" role="button">
            <rect x="${x + 1}" y="${y + 1}" width="${C.colW - 2}" height="${C.rowH - 2}" rx="3"/>
            <text x="${x + C.colW / 2}" y="${y + C.rowH / 2 + C.font * 0.36}" text-anchor="middle" font-size="${C.font}">${n}</text></g>`);
        });
        parts.push(`<text class="mx-tot" x="${totX}" y="${y + C.rowH / 2 + C.font * 0.36}" text-anchor="middle" font-size="${C.font - 1}">${inRow.length || ""}</text>`);
        y += C.rowH;
      }
    }
    const H = y + 4;
    const legend = `<div class="mx-legend small muted" aria-hidden="true">experiments per cell ${BINS.map((b, i) => `<span><i class="b${i + 1}"></i>${b[2]}</span>`).join("")}</div>`;
    return `<svg class="mx" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Experiments by data type and ${esc(preset.label.toLowerCase())}">${parts.join("")}</svg>${legend}`;
  }

  // ------------------------------------------------------------- trend
  function roundTop(x, y, w, h, r) {
    if (h <= 0) return "";
    r = Math.min(r, w / 2, h);
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }

  // mode: "year" | "cumulative"
  function trendSvg(rows, all, mode, opts) {
    opts = opts || {};
    const W = opts.width || 460, H = opts.height || 210;
    const years = all.map(s => +s.year).filter(Boolean);
    const y0 = Math.min(...years), y1 = Math.max(...years);
    const span = [];
    for (let y = y0; y <= y1; y++) span.push(y);
    const per = span.map(y => {
      const o = { year: y };
      for (const a of ASSAYS) o[a] = rows.filter(s => +s.year === y && s.assay === a).length;
      return o;
    });
    const data = mode === "cumulative"
      ? per.map((d, i) => { const o = { year: d.year }; for (const a of ASSAYS) o[a] = per.slice(0, i + 1).reduce((t, x) => t + x[a], 0); return o; })
      : per;
    const tot = d => ASSAYS.reduce((t, a) => t + d[a], 0);
    const maxV = Math.max(1, ...data.map(tot));
    const step = maxV > 100 ? 50 : maxV > 40 ? 20 : maxV > 16 ? 10 : maxV > 8 ? 4 : 2;
    const top = Math.ceil(maxV / step) * step;
    const L = 30, R = 6, T = 16, B = 22;
    const pw = W - L - R, ph = H - T - B;
    const bw = pw / data.length;
    const barW = Math.max(6, Math.min(34, bw * 0.66));
    const sy = v => T + ph - (v / top) * ph;
    const parts = [];
    for (let v = 0; v <= top; v += step) {
      parts.push(`<line class="grid" x1="${L}" x2="${W - R}" y1="${sy(v)}" y2="${sy(v)}"/><text class="axis" x="${L - 5}" y="${sy(v) + 3.5}" text-anchor="end">${v}</text>`);
    }
    data.forEach((d, i) => {
      const x = L + i * bw + (bw - barW) / 2;
      let base = 0;
      const segs = ASSAYS.filter(a => d[a] > 0);
      const t = tot(d);
      segs.forEach((a, j) => {
        const yTop = sy(base + d[a]), yBot = sy(base);
        const gap = j > 0 ? 2 : 0;  // 2px surface gap between stacked fills
        const h = Math.max(0, yBot - yTop - gap);
        const path = j === segs.length - 1 ? roundTop(x, yTop, barW, h, 4) : `M${x},${yTop}h${barW}v${h}h${-barW}Z`;
        parts.push(`<path class="bar ${ASSAY_CLS[a]}" d="${path}"/>`);
        base += d[a];
      });
      const lab = String(d.year);
      const xl = L + i * bw + bw / 2;
      parts.push(`<text class="axis" x="${xl}" y="${H - 6}" text-anchor="middle">${bw < 34 ? "’" + lab.slice(2) : lab}</text>`);
      if (t) parts.push(`<text class="val" x="${xl}" y="${sy(t) - 4}" text-anchor="middle">${t}</text>`);
      const yearsIn = mode === "cumulative" ? span.slice(0, i + 1).map(String) : [lab];
      const f = { year: yearsIn.filter(y => all.some(s => s.year === y)) };
      const breakdown = ASSAYS.map(a => `${a.replace("-seq", "")} ${d[a]}`).join(" · ");
      const tip = `${mode === "cumulative" ? "Up to " + lab : lab}: ${plural(t)} (${breakdown})`;
      // hit target: the whole column, bigger than the bar
      parts.push(`<rect class="hit" x="${L + i * bw}" y="${T}" width="${bw}" height="${ph + B}" data-f="${fattr(f)}" data-tip="${esc(tip)}" tabindex="0" role="button"/>`);
    });
    return `<svg class="trend" width="100%" viewBox="0 0 ${W} ${H}" role="img" aria-label="Experiments by year made public">${parts.join("")}</svg>`;
  }

  function assayLegend() {
    return `<div class="legend small">${ASSAYS.map(a => `<span><i class="sw ${ASSAY_CLS[a]}"></i>${a}</span>`).join("")}</div>`;
  }

  // --------------------------------------------------------------- map
  const A1 = 1.340264, A2 = -0.081106, A3 = 0.000893, A4 = 0.003796, M = Math.sqrt(3) / 2;
  function equalEarth(lon, lat, bm) {
    const l = lon * Math.PI / 180, p = lat * Math.PI / 180;
    const t = Math.asin(M * Math.sin(p)), t2 = t * t, t6 = t2 * t2 * t2;
    const x = l * Math.cos(t) / (M * (A1 + 3 * A2 * t2 + t6 * (7 * A3 + 9 * A4 * t2)));
    const y = t * (A1 + A2 * t2 + t6 * (A3 + A4 * t2));
    return [(x + bm.xmax) * bm.k, (bm.ymax - y) * bm.k];
  }

  // Sources are the submitting institutions (SRA center_name, hand-curated
  // to coordinates), aggregated per country/region so nearby labs do not
  // stack on top of each other at world scale.
  function sourcePlaces(rows, all, studies) {
    const by = new Map();
    for (const [acc, st] of Object.entries(studies)) {
      if (!st.country) continue;
      if (!by.has(st.country)) by.set(st.country, { country: st.country, insts: new Map(), lat: 0, lon: 0, k: 0, rows: [], total: 0 });
      const p = by.get(st.country);
      if (!p.insts.has(st.institution)) { p.insts.set(st.institution, { name: st.institution, city: st.city, studies: [] }); p.lat += st.lat; p.lon += st.lon; p.k++; }
      p.insts.get(st.institution).studies.push(acc);
    }
    for (const p of by.values()) { p.lat /= p.k; p.lon /= p.k; }
    for (const s of all) { const p = by.get(s.country); if (p) p.total++; }
    for (const s of rows) { const p = by.get(s.country); if (p) p.rows.push(s); }
    return [...by.values()].sort((a, b) => b.rows.length - a.rows.length || b.total - a.total);
  }

  function mapSvg(rows, all, studies, bm) {
    if (!bm) return `<div class="muted small" style="padding:2rem">Loading map…</div>`;
    const places = sourcePlaces(rows, all, studies);
    const parts = [`<path class="land" d="${bm.path}"/>`];
    // big first so small dots stay on top and clickable
    for (const p of [...places].sort((a, b) => b.rows.length - a.rows.length)) {
      const [x, y] = equalEarth(p.lon, p.lat, bm);
      const n = p.rows.length;
      const r = n ? 9 + 4.2 * Math.sqrt(n) : 7;
      const inst = [...p.insts.values()].map(i => `${i.name} (${i.city})`).join("; ");
      const tip = `${p.country}: ${plural(n)}${n ? " (" + ASSAYS.map(a => `${a.replace("-seq", "")} ${p.rows.filter(s => s.assay === a).length}`).join(" · ") + ")" : ""}. ${inst}`;
      parts.push(`<g class="place${n ? "" : " zero"}" data-f="${fattr({ country: [p.country] })}" data-tip="${esc(tip)}" tabindex="0" role="button"><circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r.toFixed(1)}"/></g>`);
    }
    const list = `<ol class="placelist small">${places.map(p => `<li><button type="button" class="linkish" data-f="${fattr({ country: [p.country] })}" data-tip="${esc([...p.insts.values()].map(i => i.name).join("; "))}">${esc(p.country)}</button> <span class="n">${p.rows.length}</span></li>`).join("")}</ol>`;
    return `<svg class="map" width="100%" viewBox="0 0 ${bm.width} ${bm.height}" role="img" aria-label="Where the data come from: submitting institutions by country or region">${parts.join("")}</svg>${list}`;
  }

  // ----------------------------------------------------------- tooltip
  let tipEl = null;
  function bindTips(root) {
    if (!tipEl) {
      tipEl = document.createElement("div");
      tipEl.className = "viztip"; tipEl.hidden = true; tipEl.setAttribute("role", "tooltip");
      document.body.appendChild(tipEl);
    }
    const show = (el, x, y) => {
      tipEl.textContent = el.getAttribute("data-tip");
      tipEl.hidden = false;
      const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
      tipEl.style.left = Math.min(window.innerWidth - w - 8, Math.max(8, x + 14)) + "px";
      tipEl.style.top = Math.max(8, y - h - 12) + "px";
    };
    root.querySelectorAll("[data-tip]").forEach(el => {
      el.addEventListener("mousemove", e => show(el, e.clientX, e.clientY));
      el.addEventListener("mouseleave", () => { tipEl.hidden = true; });
      el.addEventListener("focus", () => { const b = el.getBoundingClientRect(); show(el, b.left + b.width / 2, b.top); });
      el.addEventListener("blur", () => { tipEl.hidden = true; });
    });
  }
  function hideTip() { if (tipEl) tipEl.hidden = true; }

  // Rotated header labels are hard to measure as strings; once the SVG is in
  // the DOM, grow it to its real bounding box so nothing is clipped.
  function fitMatrix(root) {
    root.querySelectorAll("svg.mx").forEach(svg => {
      const bb = svg.getBBox();
      const w = Math.ceil(Math.max(+svg.getAttribute("width"), bb.x + bb.width + 4));
      const h = +svg.getAttribute("height");
      svg.setAttribute("width", w);
      svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    });
  }

  let basemap = null, basemapP = null;
  function loadBasemap() {
    if (!basemapP) basemapP = fetch("data/basemap.json").then(r => r.json()).then(j => (basemap = j)).catch(() => null);
    return basemapP;
  }

  return {
    PRESETS, PRESET_BY_ID, ASSAYS, matrixSvg, trendSvg, assayLegend, mapSvg, sourcePlaces,
    bindTips, hideTip, loadBasemap, fitMatrix, get basemap() { return basemap; },
  };
}));
