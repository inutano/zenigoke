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
console.log("enrichment.js: all tests passed");
