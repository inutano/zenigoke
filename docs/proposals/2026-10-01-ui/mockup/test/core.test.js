// node test/core.test.js   (from the mockup folder; no dependencies)
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const Z = require("../js/core.js");

const cat = Z.prepare(JSON.parse(fs.readFileSync(path.join(__dirname, "../data/catalog.json"), "utf8")));
const S = cat.samples;
let n = 0;
function t(name, fn) { fn(); n++; console.log("ok -", name); }

t("catalog has 157 experiments and study metadata", () => {
  assert.strictEqual(S.length, 157);
  assert.ok(Object.keys(cat.studies).length >= 10);
  assert.ok(S.every(s => s.study), "every experiment linked to a BioProject");
});

t("facet filter: OR within, AND across", () => {
  const st = { q: "", filters: { assay: ["ChIP-seq"], target: ["H3K27me3", "H3K4me3"] } };
  const rows = Z.filterSamples(S, st);
  assert.ok(rows.length > 0);
  assert.ok(rows.every(s => s.assay === "ChIP-seq" && ["H3K27me3", "H3K4me3"].includes(s.target)));
  const both = S.filter(s => s.target === "H3K27me3").length + S.filter(s => s.target === "H3K4me3").length;
  assert.strictEqual(rows.length, both);
});

t("disjunctive facet counts ignore the facet's own selection", () => {
  const st = { q: "", filters: { assay: ["ATAC-seq"] } };
  const c = Z.facetCounts(S, st);
  const assay = Object.fromEntries(c.assay);
  assert.strictEqual(assay["ChIP-seq"], 90);
  assert.strictEqual(assay["ATAC-seq"], 36);
  const qcTotal = c.qc.reduce((a, [, v]) => a + v, 0);
  assert.strictEqual(qcTotal, 36, "qc facet counts restricted to ATAC");
});

t("text search is AND over tokens and hits study titles / PMIDs", () => {
  const r1 = Z.filterSamples(S, { q: "h3k27me3 bmi1", filters: {} });
  assert.ok(r1.length > 0 && r1.every(s => s.target === "H3K27me3"));
  const pmid = Object.values(cat.studies).find(x => x.pmid).pmid;
  assert.ok(Z.filterSamples(S, { q: pmid, filters: {} }).length > 0);
});

t("hash state round-trips", () => {
  const st = { q: "heat stress", filters: { assay: ["ChIP-seq"], genotype: ["Mpez1,Mpknox2 double mutant"] }, sort: "qc" };
  const back = Z.decodeState(Z.encodeState(st).slice(1));
  assert.deepStrictEqual(back, st);
});

t("file selection honours categories and peak threshold", () => {
  const chip = S.find(s => s.acc === "SRX9800305");
  const l = Z.selectFiles([chip], { cats: ["signal", "peaks_bb"], q: "20" });
  assert.deepStrictEqual(l.map(x => Z.fileName(x.f)), ["SRX9800305.bw", "SRX9800305.20.bb"]);
  const failed = S.find(s => s.status !== "ok");
  assert.strictEqual(Z.selectFiles([failed], {}).length, 0, "failed run has no files");
});

t("files.txt / download.sh / manifest use the S3 base", () => {
  const sel = S.filter(s => s.assay === "BS-seq").slice(0, 2);
  const l = Z.selectFiles(sel, {});
  const txt = Z.filesTxt(cat, l).trim().split("\n");
  assert.strictEqual(txt.length, l.length);
  assert.ok(txt.every(u => u.startsWith("https://zenigoke-catalog-dsc.s3.ap-northeast-1.amazonaws.com/output/bsseq/")));
  const sh = Z.downloadSh(cat, l, { naming: "descriptive" });
  assert.ok(sh.startsWith("#!/usr/bin/env bash"));
  assert.ok(/fetch 'https:\/\/[^']+\.CpG\.methyl\.bw' 'BS-seq\/[A-Z]+\d+\/[A-Z]+\d+_BS-seq_[^']+\.CpG\.methyl\.bw' \d+\n/.test(sh));
  fs.writeFileSync(path.join(require("os").tmpdir(), "zenigoke_test_download.sh"), sh);
  const man = Z.manifestTsv(cat, l, {}).trim().split("\n");
  assert.strictEqual(man.length, l.length + 1);
  assert.ok(man.every(r => r.split("\t").length === 8));
});

t("metadata TSV is rectangular", () => {
  const tsv = Z.metadataTsv(cat, S).trim().split("\n");
  const w = tsv[0].split("\t").length;
  assert.strictEqual(tsv.length, 158);
  assert.ok(tsv.every(r => r.split("\t").length === w));
});

t("IGV tracks + /load URL", () => {
  const chip = S.find(s => s.acc === "SRX9800305");
  const bs = S.find(s => s.assay === "BS-seq" && s.status === "ok");
  const tr = Z.igvTracks(cat, [chip, bs], { q: "10" });
  assert.deepStrictEqual(tr.map(x => x.format), ["bigwig", "bigbed", "bigwig", "bigwig", "bigwig", "bed"]);
  assert.ok(tr.every(x => !x.name.includes(",")), "no commas in track names");
  const url = Z.igvLoadUrl(cat, tr, { locus: "chr1:1-1000" });
  const u = new URL(url);
  assert.strictEqual(u.origin + u.pathname, "http://127.0.0.1:60151/load");
  assert.strictEqual(u.searchParams.get("file").split(",").length, tr.length);
  assert.strictEqual(u.searchParams.get("name").split(",").length, tr.length);
  assert.strictEqual(u.searchParams.get("genome"), Z.genomeRef(cat, Z.defaultGenomeSrc(cat)));
  assert.strictEqual(new URL(Z.igvLoadUrl(cat, tr, { genomeSrc: "hosted" })).searchParams.get("genome"), cat.genome.json);
  assert.strictEqual(new URL(Z.igvLoadUrl(cat, tr, { genomeSrc: "none" })).searchParams.get("genome"), null);
  assert.strictEqual(u.searchParams.get("locus"), "chr1:1-1000");
  assert.strictEqual(u.searchParams.get("format").split(",").length, tr.length);
  assert.ok(!url.includes("%2C"), "items joined with literal commas");
  const g = new URL(Z.igvLoadUrl(cat, [], { genomeSrc: "hosted" }));
  assert.strictEqual(g.searchParams.get("file"), null, "genome-only URL has no file=");
  assert.strictEqual(g.searchParams.get("genome"), cat.genome.json);
});

t("IGV session XML", () => {
  const tr = Z.igvTracks(cat, S.filter(s => s.target === "H3K27me3"), {});
  const xml = Z.igvSessionXml(cat, tr, {});
  assert.ok(Z.igvSessionXml(cat, tr, { genomeSrc: "hosted" }).includes(`genome="${cat.genome.json}"`));
  assert.strictEqual((xml.match(/<Resource /g) || []).length, tr.length);
  fs.writeFileSync(path.join(require("os").tmpdir(), "zenigoke_test_session.xml"), xml);
});

t("igv.js options fall back to UCSC GenArk while FASTA is not hosted", () => {
  const o = Z.igvJsOptions(cat, Z.igvTracks(cat, [S.find(s => s.acc === "SRX9800305")], {}), {});
  if (cat.genome.hosted.fasta) assert.ok(o.reference.fastaURL);
  else assert.strictEqual(o.genome, "GCA_039105155.1");
  assert.strictEqual(o.tracks.length, 2);
});

console.log(`\n${n} tests passed`);
