"use strict";

const API = (window.ZENIGOKE_API_BASE || "").replace(/\/$/, "");

const ui = {
  bed:    () => document.getElementById("bed-input"),
  file:   () => document.getElementById("bed-file"),
  q:      () => document.getElementById("q-cutoff"),
  strats: () => Array.from(document.querySelectorAll(".strat:checked")).map(e => e.value),
  runBtn: () => document.getElementById("run-btn"),
  status: () => document.getElementById("run-status"),
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

function render(data) {
  lastResults = data;
  ui.card().style.display = "";
  ui.summary().textContent =
    `${data.n_user_regions} input regions × ${data.n_experiments_tested} experiments`;
  const tbody = ui.tbody();
  tbody.innerHTML = "";
  data.results.forEach((r, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${i+1}</td>
      <td><a href="samples/${r.accession}.html">${r.accession}</a></td>
      <td>${r.antibody_target || "&mdash;"}</td>
      <td>${r.genotype_strain || "&mdash;"}</td>
      <td>${r.developmental_stage || "&mdash;"}</td>
      <td>${r.overlap_count}</td>
      <td>${r.fold_enrichment.toFixed(2)}</td>
      <td>${r.p_value.toExponential(2)}</td>
      <td>${r.q_value.toExponential(2)}</td>
      <td><a href="#" data-acc="${r.accession}" data-strat="${r.library_strategy}" class="row-igv">&#9654; IGV</a></td>`;
    tbody.appendChild(tr);
  });
  for (const a of tbody.querySelectorAll("a.row-igv")) {
    a.addEventListener("click", e => {
      e.preventDefault();
      const acc = a.dataset.acc, strat = a.dataset.strat;
      sendOneToIGV(acc, strat);
    });
  }
}

function sendOneToIGV(acc, strat) {
  const tracks = window.tracksForAccession(acc, {strategy: strat, q_cutoff: ui.q().value}, "#3060a0");
  const param = tracks.map(t => t.url + "|" + t.name).join(",");
  fetch("http://localhost:60151/load?file=" + encodeURIComponent(param))
    .catch(e => alert("Could not reach IGV at :60151."));
}

function sendTopToIGV(n) {
  if (!lastResults) return;
  const palette = ["#3060a0","#a04030","#308050","#a0a030","#603090","#308090","#a07030","#7060a0"];
  const all = [];
  for (let i = 0; i < Math.min(n, lastResults.results.length); i++) {
    const r = lastResults.results[i];
    const tracks = window.tracksForAccession(r.accession,
      {strategy: r.library_strategy, q_cutoff: ui.q().value},
      palette[i % palette.length]);
    all.push(...tracks);
  }
  const param = all.map(t => t.url + "|" + t.name).join(",");
  fetch("http://localhost:60151/load?file=" + encodeURIComponent(param))
    .catch(e => alert("Could not reach IGV at :60151."));
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

init();
