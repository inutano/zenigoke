"use strict";

const state = {
  axes: null,
  matrix: null,
  selectedCells: new Map(),  // key "x|y" -> {x, y, n, accessions}
  groupColors: ["#3060a0","#a04030","#308050","#a0a030","#603090","#308090","#a07030","#7060a0"],
};

async function init() {
  const r = await fetch("/api/axes");
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
  const r = await fetch(`/api/matrix?x=${x}&y=${y}&include_unknown=${inc}`);
  state.matrix = await r.json();
  state.selectedCells.clear();
  renderSelection();
  renderMatrix();
}

function renderMatrix() {
  const m = state.matrix;
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

async function postBundle(groups) {
  const accessions = [...new Set(groups.flatMap(g => g.accessions))];
  const r = await fetch("/api/bundle", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({accessions: accessions, q_cutoff: "1e-10",
                          groups: groups.map(g => ({label: g.label, accessions: g.accessions}))}),
  });
  if (!r.ok) throw new Error(`bundle failed: ${r.status}`);
  return await r.json();
}

async function sendToIgv(groups) {
  const btn = document.getElementById("send-igv-btn");
  btn.disabled = true; btn.textContent = "Building bundle…";
  try {
    const bundle = await postBundle(groups);
    const param = bundle.tracks.map(t => t.url + "|" + t.name).join(",");
    btn.textContent = "Loading into IGV…";
    try {
      await fetch("http://localhost:60151/load?file=" + encodeURIComponent(param));
      btn.textContent = "✔ Sent to IGV";
    } catch (e) {
      btn.textContent = "Could not reach IGV (port :60151 not enabled?)";
      btn.disabled = false;
    }
  } catch (e) {
    btn.textContent = "Bundle failed: " + e.message;
    btn.disabled = false;
  }
}

async function openDrilldown(groups) {
  const bundle = await postBundle(groups);
  window.open("/bundle.html#" + bundle.hash, "_blank");
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

init();
