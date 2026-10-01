"use strict";
const assert = require("assert");
const I = require("../../report/assets/igv.js");
const BASE = "https://b.s3.ap-northeast-1.amazonaws.com/";

assert.strictEqual(I.dataUrl("output/x.bw", BASE), "https://b.s3.ap-northeast-1.amazonaws.com/output/x.bw");
assert.strictEqual(I.dataUrl("output/x.bw", "https://b.example"), "https://b.example/output/x.bw");
assert.strictEqual(I.dataUrl("output/x.bw", ""), "output/x.bw");

const chip = I.tracksForAccession("SRX1", {strategy: "ChIP-Seq", q_cutoff: "1e-10", label: "H3K27me3, Mpez1,Mpknox2"}, "#123456", BASE);
assert.deepStrictEqual(chip.map(t => t.format), ["bigwig", "bigbed"]);
assert.ok(chip[1].url.endsWith("/output/chipseq/SRX1/SRX1.10.bb"));
// names never contain commas
for (const t of chip) assert.ok(!t.name.includes(","), t.name);

const bs = I.tracksForAccession("SRX2", {strategy: "Bisulfite-Seq"}, "#000", BASE);
assert.deepStrictEqual(bs.map(t => t.format), ["bigwig", "bigwig", "bigwig", "bed"]);

const u = new URL(I.loadUrl(chip, {genome: I.DEFAULT_GENOME, locus: "chr6:1-100", port: 60151}));
assert.strictEqual(u.origin, "http://127.0.0.1:60151");
assert.strictEqual(u.pathname, "/load");
assert.strictEqual(u.searchParams.get("genome"), I.DEFAULT_GENOME);
assert.strictEqual(u.searchParams.get("file").split(",").length, 2);
assert.strictEqual(u.searchParams.get("name").split(",").length, 2);
assert.strictEqual(u.searchParams.get("format"), "bigwig,bigbed");
assert.strictEqual(u.searchParams.get("merge"), "true");

(async () => {
  // send result maps network error to "unreachable"
  const down = async () => { throw new TypeError("Failed to fetch"); };
  let r = await I.send(chip, {fetchImpl: down});
  assert.strictEqual(r.ok, false); assert.strictEqual(r.status, "unreachable");
  const ok = async (url) => ({status: url.includes("/execute") ? 200 : 204, text: async () => ""});
  r = await I.send(chip, {fetchImpl: ok});
  assert.strictEqual(r.status, "loaded");
  const err = async (url) => ({status: 200, text: async () => url.includes("/execute") ? "2.19.1" : "ERROR: genome not found"});
  r = await I.send(chip, {fetchImpl: err});
  assert.strictEqual(r.status, "igv-error"); assert.ok(r.message.includes("genome not found"));
  // a non-2xx /load reply is an error, not "loaded"
  const http500 = async (url) => ({status: url.includes("/execute") ? 200 : 500, text: async () => ""});
  r = await I.send(chip, {fetchImpl: http500});
  assert.strictEqual(r.ok, false); assert.strictEqual(r.status, "igv-error");
  console.log("igv.js: all tests passed");
})().catch(e => { console.error(e); process.exit(1); });
