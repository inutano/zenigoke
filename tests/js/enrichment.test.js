"use strict";
// Unit tests for report/assets/enrichment.js (pure helpers; no DOM needed).
const assert = require("assert");
const E = require("../../report/assets/enrichment.js");

const base = {accession: "SRX1", library_strategy: "ChIP-Seq", antibody_target: "H3K27me3",
              genotype_strain: "Tak-1", developmental_stage: null, overlap_count: 2,
              fold_enrichment: 1.5, p_value: 0.01, q_value: 0.02};

// curated metadata (LLM-extracted from third-party records) is escaped, not parsed as HTML
let row = E.rowHtml({...base, antibody_target: "<img src=x onerror=alert(1)>", genotype_strain: 'a"b&c'}, 0);
assert.ok(!row.includes("<img"), row);
assert.ok(row.includes("&lt;img src=x onerror=alert(1)&gt;"), row);
assert.ok(row.includes("a&quot;b&amp;c"), row);
assert.ok(row.includes("<td>&mdash;</td>"), row);            // empty stage
assert.ok(row.includes('href="samples/SRX1.html"'), row);

row = E.rowHtml({...base, accession: '"><script>x()</script>'}, 1);
assert.ok(!row.includes("<script>"), row);
assert.ok(!row.includes('"><'), row);

// summary reports regions excluded by the organelle-like blacklist
assert.strictEqual(E.summaryText({n_user_regions: 10, n_user_regions_excluded: 3, n_experiments_tested: 5}),
                   "10 input regions (3 excluded: organelle-like) × 5 experiments");
assert.strictEqual(E.summaryText({n_user_regions: 10, n_user_regions_excluded: 0, n_experiments_tested: 5}),
                   "10 input regions × 5 experiments");
assert.strictEqual(E.summaryText({n_user_regions: 10, n_experiments_tested: 5}),
                   "10 input regions × 5 experiments");
// CSV export quotes every field (RFC 4180): curated values contain commas and quotes
assert.strictEqual(E.csvField("H3K27me3"), '"H3K27me3"');
assert.strictEqual(E.csvField("a,b"), '"a,b"');
assert.strictEqual(E.csvField('say "hi"'), '"say ""hi"""');
assert.strictEqual(E.csvField("line1\nline2"), '"line1\nline2"');
assert.strictEqual(E.csvField(3), '"3"');
assert.strictEqual(E.csvField(null), '""');
assert.strictEqual(E.csvField(undefined), '""');
const csv = E.resultsCsv([
  {...base, antibody_target: "H3K4me3, H3K27ac", genotype_strain: 'Tak-1 "wild type"'},
  {...base, accession: "SRX2", antibody_target: null, genotype_strain: null},
]);
const lines = csv.split("\n");
assert.strictEqual(lines.length, 3, csv);
assert.strictEqual(lines[0], '"rank","accession","library_strategy","antibody_target","genotype_strain",' +
                             '"developmental_stage","overlap_count","fold_enrichment","p_value","q_value"');
assert.strictEqual(lines[1], '"1","SRX1","ChIP-Seq","H3K4me3, H3K27ac","Tak-1 ""wild type""","",' +
                             '"2","1.5000","1.0000e-2","2.0000e-2"');
assert.strictEqual(lines[2], '"2","SRX2","ChIP-Seq","","","","2","1.5000","1.0000e-2","2.0000e-2"');
console.log("enrichment.js: all tests passed");
