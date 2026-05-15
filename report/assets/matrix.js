"use strict";

// Static-mode matrix UI. Reads pre-generated JSON from `data/` (built by
// scripts/build_static_data.py) instead of /api/* — works on GitHub Pages
// with no backend.
//
// Per-sample track URLs point at ZENIGOKE_DATA_BASE (configured at build
// time via window.ZENIGOKE_DATA_BASE injected into index.html). When unset
// (local dev served by FastAPI), URLs are relative to the catalog server.

const state = {
  axes: null,
  matrix: null,
  selectedCells: new Map(),  // key "x|y" -> {x, y, n, accessions}
  groupColors: ["#3060a0","#a04030","#308050","#a0a030","#603090","#308090","#a07030","#7060a0"],
};

const DATA_BASE = (window.ZENIGOKE_DATA_BASE || "").replace(/\/$/, "");

function dataUrl(path) {
  return DATA_BASE ? `${DATA_BASE}${path}` : path;
}

async function init() {
  const r = await fetch("data/axes.json");
  state.axes = await r.json();

  const xsel = document.getElementById("x-axis-select");
  const ysel = document.getElementById("y-axis-select");
  for (const ax of state.axes.axes) {
    xsel.appendChild(opt(ax.key, ax.label));
    ysel.appendChild(opt(ax.key, ax.label));
  }
  xsel.value = "experiment_type";
  ysel.value = "genotype_class";
  xsel.addEventListener("change", refreshMatrix);
  ysel.addEventListener("change", refreshMatrix);
  document.getElementById("include-unknown").addEventListener("change", refreshMatrix);
  refreshMatrix();
}

function opt(value, text) {
  const o = document.createElement("option");
  o.value = value; o.textContent = text;
  return o;
}

async function refreshMatrix() {
  const x = document.getElementById("x-axis-select").value;
  const y = document.getElementById("y-axis-select").value;
  const inc = document.getElementById("include-unknown").checked ? 1 : 0;
  if (x === y) {
    document.getElementById("matrix-grid").innerHTML =
      "<p class='subtitle'>Pick two different axes.</p>";
    state.matrix = null;
    state.selectedCells.clear();
    renderSelection();
    return;
  }
  const r = await fetch(`data/matrix-${x}-${y}-${inc}.json`);
  state.matrix = await r.json();
  state.selectedCells.clear();
  renderSelection();
  renderMatrix();
}

function renderMatrix() {
  const m = state.matrix;
  if (!m) return;
  const cells = new Map(m.cells.map(c => [`${c.x}|${c.y}`, c]));
  const grid = document.getElementById("matrix-grid");
  let html = "<table><thead><tr><th></th>";
  for (const xv of m.x_values) html += `<th>${escapeHtml(xv)}</th>`;
  html += "</tr></thead><tbody>";
  for (const yv of m.y_values) {
    html += `<tr><th>${escapeHtml(yv)}</th>`;
    for (const xv of m.x_values) {
      const c = cells.get(`${xv}|${yv}`);
      if (c) {
        const sel = state.selectedCells.has(`${xv}|${yv}`) ? " selected" : "";
        html += `<td class="cell${sel}" data-x="${escapeAttr(xv)}" data-y="${escapeAttr(yv)}">${c.n}</td>`;
      } else {
        html += `<td class="cell empty"></td>`;
      }
    }
    html += "</tr>";
  }
  html += "</tbody></table>";
  grid.innerHTML = html;
  for (const td of grid.querySelectorAll("td.cell:not(.empty)")) {
    td.addEventListener("click", onCellClick);
  }
}

function onCellClick(e) {
  const td = e.currentTarget;
  const x = td.dataset.x, y = td.dataset.y;
  const key = `${x}|${y}`;
  if (state.selectedCells.has(key)) {
    state.selectedCells.delete(key);
  } else {
    const c = state.matrix.cells.find(c => c.x === x && c.y === y);
    state.selectedCells.set(key, c);
  }
  renderMatrix();
  renderSelection();
}

function renderSelection() {
  const panel = document.getElementById("selection-panel");
  if (state.selectedCells.size === 0) {
    panel.innerHTML = "<p class='subtitle'>Click a populated cell to begin.</p>";
    return;
  }
  let html = "";
  let total = 0;
  let i = 0;
  const groups = [];
  for (const [key, c] of state.selectedCells) {
    const color = state.groupColors[i % state.groupColors.length];
    total += c.accessions.length;
    groups.push({label: `${c.x} × ${c.y}`, accessions: c.accessions, color: color});
    html += `<div class="group">`;
    html += `<div class="group-header" style="color:${color}">${escapeHtml(c.x)} × ${escapeHtml(c.y)} (${c.n})</div>`;
    html += `<ul>${c.accessions.map(a => `<li>${a}</li>`).join("")}</ul>`;
    html += `</div>`;
    i += 1;
  }
  html += `<button id="send-igv-btn">&#9654; Send ${total} samples to IGV</button>`;
  html += `<a class="secondary" href="#" id="drilldown-link">Open detailed bundle page &#8599;</a>`;
  html += `<a class="secondary" href="#" id="clear-link">Clear selection</a>`;
  panel.innerHTML = html;
  document.getElementById("send-igv-btn").addEventListener("click", () => sendToIgv(groups));
  document.getElementById("drilldown-link").addEventListener("click", e => { e.preventDefault(); openDrilldown(groups); });
  document.getElementById("clear-link").addEventListener("click", e => {
    e.preventDefault();
    state.selectedCells.clear();
    renderMatrix(); renderSelection();
  });
}

// === Track-list construction (client-side) ===
//
// Per Phase 4 (static-only) we build the IGV track list in the browser
// instead of asking a server. No consensus track — IGV displays per-sample
// tracks side by side, which is enough for the comparison workflows the
// catalog targets.

const Q_LABEL = {"1e-5": "05", "1e-10": "10", "1e-20": "20"};

function tracksForAccession(acc, sample, color) {
  // `sample` is optional metadata pulled from the cell entry. We don't have
  // library_strategy in the matrix payload, so we infer it from the axis
  // value when possible, otherwise probe by URL pattern by trying ChIP first.
  // Simplest robust approach: assume ChIP if the cell's x or y is "ChIP:*",
  // ATAC if "ATAC-Seq", BS-seq if "Bisulfite-Seq". Caller passes the resolved
  // strategy as `sample.strategy`.
  const strat = sample.strategy;
  const q = sample.q_cutoff || "1e-10";
  if (strat === "ChIP-Seq" || strat === "ATAC-Seq") {
    const sub = strat === "ChIP-Seq" ? "chipseq" : "atacseq";
    const qLabel = Q_LABEL[q] || "10";
    return [
      {name: `${acc} bigwig`,
       url: dataUrl(`output/${sub}/${acc}/${acc}.bw`),
       type: "wig", color: color},
      {name: `${acc} peaks q≤${q}`,
       url: dataUrl(`output/${sub}/${acc}/${acc}.${qLabel}_peaks.narrowPeak`),
       type: "annotation", color: color},
    ];
  }
  if (strat === "Bisulfite-Seq") {
    return [
      {name: `${acc} CpG methyl`,
       url: dataUrl(`output/bsseq/${acc}/${acc}.CpG.methyl.bw`),
       type: "wig", color: color},
      {name: `${acc} CHG methyl`,
       url: dataUrl(`output/bsseq/${acc}/${acc}.CHG.methyl.bw`),
       type: "wig", color: color},
      {name: `${acc} CHH methyl`,
       url: dataUrl(`output/bsseq/${acc}/${acc}.CHH.methyl.bw`),
       type: "wig", color: color},
    ];
  }
  return [];
}

function resolveStrategy(cell) {
  // The matrix payload has cell.x / cell.y; one or the other carries the
  // experiment_type when that axis is in use. Otherwise we fall back to the
  // accession-prefix heuristic via the sample lookup map — but for simplicity
  // we encode strategy in the cell when the axis is experiment_type, else
  // mark "unknown" and skip those tracks.
  const isType = v => /^(ATAC-Seq|Bisulfite-Seq|ChIP:)/.test(v);
  if (isType(cell.x)) return cell.x.startsWith("ChIP:") ? "ChIP-Seq" : cell.x;
  if (isType(cell.y)) return cell.y.startsWith("ChIP:") ? "ChIP-Seq" : cell.y;
  return null;
}

function buildTracks(groups, qCutoff) {
  const tracks = [];
  for (let i = 0; i < groups.length; i++) {
    const g = groups[i];
    const color = state.groupColors[i % state.groupColors.length];
    const cellLike = {x: g.label.split(" × ")[0], y: g.label.split(" × ")[1] || ""};
    const strat = resolveStrategy(cellLike);
    if (!strat) continue;
    for (const acc of g.accessions) {
      tracks.push(...tracksForAccession(acc, {strategy: strat, q_cutoff: qCutoff}, color));
    }
  }
  return tracks;
}

async function sendToIgv(groups) {
  const btn = document.getElementById("send-igv-btn");
  btn.disabled = true;
  const tracks = buildTracks(groups, "1e-10");
  if (tracks.length === 0) {
    btn.textContent = "No track URLs (pick a cell with experiment_type on either axis)";
    return;
  }
  btn.textContent = "Loading into IGV…";
  const param = tracks.map(t => t.url + "|" + t.name).join(",");
  try {
    await fetch("http://localhost:60151/load?file=" + encodeURIComponent(param));
    btn.textContent = "✔ Sent to IGV";
  } catch (e) {
    btn.textContent = "Could not reach IGV (port :60151 not enabled?)";
    btn.disabled = false;
  }
}

function openDrilldown(groups) {
  const accessions = [...new Set(groups.flatMap(g => g.accessions))];
  const labels = groups.map(g => `${g.label}:${g.accessions.join('+')}`).join(';');
  const params = new URLSearchParams({
    acc: accessions.join(","),
    q: "1e-10",
    g: labels,
  });
  window.open("bundle.html?" + params.toString(), "_blank");
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

init();
