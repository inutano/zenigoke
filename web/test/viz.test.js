// node test/viz.test.js   (from web/; needs data/catalog.json from make_index.py)
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const Z = require("../js/core.js");
const V = require("../js/viz.js");

const cat = Z.prepare(JSON.parse(fs.readFileSync(path.join(__dirname, "../data/catalog.json"), "utf8")));
const bm = JSON.parse(fs.readFileSync(path.join(__dirname, "../data/basemap.json"), "utf8"));
const S = cat.samples;
let n = 0;
function t(name, fn) { fn(); n++; console.log("ok -", name); }

const cellSum = svg => [...svg.matchAll(/<g class="mx-cell[^"]*"[^>]*>[\s\S]*?<text[^>]*>(\d+)<\/text><\/g>/g)]
  .reduce((a, m) => a + Number(m[1]), 0);
const fset = svg => [...svg.matchAll(/data-f="([^"]+)"/g)].map(m => JSON.parse(m[1].replace(/&quot;/g, '"')));

t("every preset counts each experiment exactly once (full and compact)", () => {
  for (const p of V.PRESETS) {
    for (const compact of [false, true]) {
      const svg = V.matrixSvg(S, S, cat.studies, p.id, { compact });
      assert.strictEqual(cellSum(svg), S.length, `${p.id} compact=${compact}`);
    }
  }
});

t("matrix counts follow the filtered rows, cells filter to exactly themselves", () => {
  const rows = Z.filterSamples(S, { q: "", filters: { target: ["H3K27me3"] } });
  const svg = V.matrixSvg(rows, S, cat.studies, "genotype", {});
  assert.strictEqual(cellSum(svg), rows.length);
  for (const f of fset(svg).filter(f => f.target && f.genotype_class)) {
    const hit = Z.filterSamples(S, { q: "", filters: f });
    assert.ok(hit.every(s => s.target === f.target[0] && s.genotype_class === f.genotype_class[0]));
  }
});

t("folded compact rows filter to the same experiments they count", () => {
  const svg = V.matrixSvg(S, S, cat.studies, "genotype", { compact: true });
  const tf = fset(svg).find(f => f.target_class && f.target_class.includes("transcription factor") && !f.genotype_class);
  assert.ok(tf, "a folded transcription-factor row exists");
  const hit = Z.filterSamples(S, { q: "", filters: tf });
  assert.strictEqual(hit.length, S.filter(s => s.target_class === "transcription factor").length);
});

t("trend: per-year bars sum to the catalog, cumulative ends at the total", () => {
  const vals = svg => [...svg.matchAll(/<text class="val"[^>]*>(\d+)<\/text>/g)].map(m => Number(m[1]));
  assert.strictEqual(vals(V.trendSvg(S, S, "year")).reduce((a, b) => a + b, 0), S.length);
  const cum = vals(V.trendSvg(S, S, "cumulative"));
  assert.strictEqual(cum[cum.length - 1], S.length);
  assert.deepStrictEqual(cum, [...cum].sort((a, b) => a - b), "cumulative never decreases");
});

t("map: places cover every experiment and sit inside the basemap", () => {
  const places = V.sourcePlaces(S, S, cat.studies);
  assert.strictEqual(places.reduce((a, p) => a + p.rows.length, 0), S.length);
  const svg = V.mapSvg(S, S, cat.studies, bm);
  for (const m of svg.matchAll(/<circle cx="([\d.]+)" cy="([\d.]+)"/g)) {
    assert.ok(+m[1] >= 0 && +m[1] <= bm.width && +m[2] >= 0 && +m[2] <= bm.height, `dot ${m[1]},${m[2]}`);
  }
});

console.log(`\n${n} tests passed`);
