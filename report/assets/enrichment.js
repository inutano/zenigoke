"use strict";

const API = ((typeof window !== "undefined" && window.ZENIGOKE_API_BASE) || "").replace(/\/$/, "");

const ui = {
  bed:    () => document.getElementById("bed-input"),
  file:   () => document.getElementById("bed-file"),
  q:      () => document.getElementById("q-cutoff"),
  strats: () => Array.from(document.querySelectorAll(".strat:checked")).map(e => e.value),
  runBtn: () => document.getElementById("run-btn"),
  status: () => document.getElementById("run-status"),
  igvStatus: () => document.getElementById("igv-status"),
  card:   () => document.getElementById("results-card"),
  summary: () => document.getElementById("results-summary"),
  tbody:  () => document.querySelector("#results-table tbody"),
  top10:  () => document.getElementById("top10-igv"),
  csv:    () => document.getElementById("download-csv"),
};

let lastResults = null;

function init() {
  ui.runBtn().addEventListener("click", run);
  ui.file().addEventListener("change", async e => {
    const f = e.target.files[0]; if (!f) return;
    ui.bed().value = await f.text();
  });
  ui.top10().addEventListener("click", () => sendTopToIGV(10));
  ui.csv().addEventListener("click", downloadCsv);
}

async function run() {
  const body = {
    regions_bed: ui.bed().value,
    q_cutoff: ui.q().value,
    filter: {strategy: ui.strats()},
  };
  if (!body.regions_bed.trim()) {
    ui.status().textContent = " · paste or load a BED first";
    return;
  }
  ui.runBtn().disabled = true;
  ui.status().textContent = " · running…";
  try {
    const r = await fetch(`${API}/api/enrichment`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({detail: r.status}));
      throw new Error(err.detail || `${r.status}`);
    }
    const data = await r.json();
    render(data);
    ui.status().textContent = ` · ${data.n_experiments_tested} experiments tested`;
  } catch (e) {
    ui.status().textContent = ` · failed: ${e.message}`;
  } finally {
    ui.runBtn().disabled = false;
  }
}

// Accession and curated metadata (LLM-extracted from third-party records) go
// into innerHTML, so they are escaped.
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}

function cell(v) { return v == null || v === "" ? "&mdash;" : escapeHtml(v); }

function rowHtml(r, i) {
  const acc = escapeHtml(r.accession);
  return `
      <td>${i+1}</td>
      <td><a href="samples/${escapeHtml(encodeURIComponent(r.accession))}.html">${acc}</a></td>
      <td>${cell(r.antibody_target)}</td>
      <td>${cell(r.genotype_strain)}</td>
      <td>${cell(r.developmental_stage)}</td>
      <td>${Number(r.overlap_count)}</td>
      <td>${Number(r.fold_enrichment).toFixed(2)}</td>
      <td>${Number(r.p_value).toExponential(2)}</td>
      <td>${Number(r.q_value).toExponential(2)}</td>
      <td><a href="#" data-idx="${i}" class="row-igv">&#9654; IGV</a></td>`;
}

function summaryText(data) {
  const ex = Number(data.n_user_regions_excluded) || 0;
  const excluded = ex > 0 ? ` (${ex} excluded: organelle-like)` : "";
  return `${data.n_user_regions} input regions${excluded} × ${data.n_experiments_tested} experiments`;
}

function render(data) {
  lastResults = data;
  ui.card().style.display = "";
  ui.summary().textContent = summaryText(data);
  const tbody = ui.tbody();
  tbody.innerHTML = "";
  data.results.forEach((r, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = rowHtml(r, i);
    tbody.appendChild(tr);
  });
  for (const a of tbody.querySelectorAll("a.row-igv")) {
    a.addEventListener("click", e => {
      e.preventDefault();
      sendOneToIGV(data.results[Number(a.dataset.idx)], a);
    });
  }
}

const IGV_PALETTE = ["#3060a0","#a04030","#308050","#a0a030","#603090","#308090","#a07030","#7060a0"];

function igvTracksFor(r, color) {
  return ZenigokeIGV.tracksForAccession(
    r.accession,
    {strategy: r.library_strategy, q_cutoff: ui.q().value, label: r.antibody_target || ""},
    color, window.ZENIGOKE_DATA_BASE || "");
}

async function sendToIGV(tracks) {
  const el = ui.igvStatus();
  if (tracks.length === 0) {
    el.textContent = "No IGV tracks for these results.";
    return {ok: false};
  }
  el.textContent = "Loading into IGV…";
  const r = await ZenigokeIGV.send(tracks);
  el.textContent = (r.ok ? "✔ " : "✖ ") + r.message;
  return r;
}

async function sendOneToIGV(r, link) {
  const res = await sendToIGV(igvTracksFor(r, IGV_PALETTE[0]));
  link.innerHTML = (res.ok ? "&#10004;" : "&#10006;") + " IGV";
}

function sendTopToIGV(n) {
  if (!lastResults) return;
  const all = [];
  for (let i = 0; i < Math.min(n, lastResults.results.length); i++) {
    all.push(...igvTracksFor(lastResults.results[i], IGV_PALETTE[i % IGV_PALETTE.length]));
  }
  return sendToIGV(all);
}

function downloadCsv() {
  if (!lastResults) return;
  const hdr = ["rank","accession","library_strategy","antibody_target","genotype_strain",
               "developmental_stage","overlap_count","fold_enrichment","p_value","q_value"];
  const rows = lastResults.results.map((r, i) => [
    i+1, r.accession, r.library_strategy, r.antibody_target || "",
    r.genotype_strain || "", r.developmental_stage || "",
    r.overlap_count, r.fold_enrichment.toFixed(4),
    r.p_value.toExponential(4), r.q_value.toExponential(4),
  ]);
  const csv = [hdr.join(","), ...rows.map(r => r.join(","))].join("\n");
  const blob = new Blob([csv], {type: "text/csv"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "enrichment.csv";
  a.click();
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {escapeHtml, rowHtml, summaryText};   // node unit tests
} else {
  init();
}
