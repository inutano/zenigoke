/* Zenigoke Catalog web UI: pure logic (no DOM).
 *
 * Loaded by index.html as a classic script (exposes window.ZCore) and by
 * test/core.test.js under node (module.exports). Everything that turns the
 * catalog index into facets, filtered rows, download manifests and IGV
 * hand-off payloads lives here so it can be tested without a browser.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ZCore = factory();
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ---------------------------------------------------------------- facets
  // OR within one facet, AND across facets. Counts for a facet are computed
  // with every *other* active facet applied (disjunctive faceting), so the
  // user always sees how many rows a click would add.
  const FACETS = [
    { key: "assay", label: "Assay", get: s => s.assay, open: true },
    { key: "target", label: "ChIP target", get: s => s.target, open: true, search: true },
    { key: "target_class", label: "Target class", get: s => s.target_class },
    { key: "genotype_class", label: "Genotype class", get: s => s.genotype_class, open: true },
    { key: "genotype", label: "Genotype / strain", get: s => s.genotype, search: true },
    { key: "stage", label: "Developmental stage", get: s => s.stage, open: true },
    { key: "tissue", label: "Tissue", get: s => s.tissue },
    { key: "treatment", label: "Treatment", get: s => s.treatment },
    { key: "study", label: "Study (BioProject)", get: s => s.study, open: true },
    { key: "country", label: "Country / region", get: s => s.country },
    { key: "year", label: "Year public", get: s => s.year, byValueDesc: true },
    { key: "qc", label: "QC status", get: s => s.qc.flag, open: true, order: ["PASS", "WARN", "FAIL"] },
  ];
  const FACET_BY_KEY = Object.fromEntries(FACETS.map(f => [f.key, f]));

  function haystack(s, studies) {
    const st = (studies && studies[s.study]) || {};
    return [s.acc, s.run, s.gsm, s.biosample, s.title, s.assay, s.target, s.target_class,
      s.genotype, s.stage, s.tissue, s.cell_type, s.treatment, s.study, s.srp,
      st.title, st.center, st.institution, st.city, st.country, st.pmid, st.citation].filter(Boolean).join(" ").toLowerCase();
  }

  function prepare(catalog) {
    for (const s of catalog.samples) {
      const st = catalog.studies[s.study] || {};
      s.year = (s.first_public || "").slice(0, 4) || null;
      s.country = st.country || null;
      s._hay = haystack(s, catalog.studies);
    }
    return catalog;
  }

  function matchesText(s, q) {
    if (!q) return true;
    const toks = q.toLowerCase().split(/\s+/).filter(Boolean);
    return toks.every(t => s._hay.includes(t));
  }

  function matchesFacets(s, filters, skipKey) {
    for (const [key, vals] of Object.entries(filters)) {
      if (key === skipKey || !vals || vals.length === 0) continue;
      const f = FACET_BY_KEY[key];
      if (!f) continue;
      if (!vals.includes(f.get(s))) return false;
    }
    return true;
  }

  function filterSamples(samples, state) {
    return samples.filter(s => matchesText(s, state.q) && matchesFacets(s, state.filters || {}, null));
  }

  function facetCounts(samples, state) {
    const out = {};
    const textHits = samples.filter(s => matchesText(s, state.q));
    for (const f of FACETS) {
      const counts = new Map();
      for (const s of textHits) {
        if (!matchesFacets(s, state.filters || {}, f.key)) continue;
        const v = f.get(s);
        if (v === null || v === undefined) continue;
        counts.set(v, (counts.get(v) || 0) + 1);
      }
      // keep selected values visible even when their count drops to 0
      for (const v of (state.filters || {})[f.key] || []) if (!counts.has(v)) counts.set(v, 0);
      let entries = [...counts.entries()];
      if (f.order) entries.sort((a, b) => f.order.indexOf(a[0]) - f.order.indexOf(b[0]));
      else if (f.byValueDesc) entries.sort((a, b) => String(b[0]).localeCompare(String(a[0])));
      else entries.sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
      out[f.key] = entries;
    }
    return out;
  }

  const SORTS = {
    acc: (a, b) => a.acc.localeCompare(b.acc),
    assay: (a, b) => a.assay.localeCompare(b.assay) || String(a.target || "").localeCompare(String(b.target || "")) || a.acc.localeCompare(b.acc),
    study: (a, b) => String(a.study).localeCompare(String(b.study)) || a.acc.localeCompare(b.acc),
    qc: (a, b) => ["PASS", "WARN", "FAIL"].indexOf(a.qc.flag) - ["PASS", "WARN", "FAIL"].indexOf(b.qc.flag) || a.acc.localeCompare(b.acc),
    mapping: (a, b) => (b.metrics.mapping_rate || 0) - (a.metrics.mapping_rate || 0),
    newest: (a, b) => String(b.first_public || "").localeCompare(String(a.first_public || "")),
  };

  // ------------------------------------------------- URL hash <-> state
  function encodeState(state) {
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    for (const f of FACETS) {
      const v = (state.filters || {})[f.key];
      if (v && v.length) p.set(f.key, v.join("|"));
    }
    if (state.sort && state.sort !== "assay") p.set("sort", state.sort);
    const s = p.toString();
    return s ? "?" + s : "";
  }

  function decodeState(query) {
    const p = new URLSearchParams(query || "");
    const filters = {};
    for (const f of FACETS) if (p.get(f.key)) filters[f.key] = p.get(f.key).split("|");
    return { q: p.get("q") || "", filters, sort: p.get("sort") || "assay" };
  }

  // ------------------------------------------------------------ files
  const FILE_CATS = [
    { cat: "signal", label: "Signal track (bigWig)", assays: ["ChIP-seq", "ATAC-seq"], def: true },
    { cat: "peaks_bb", label: "Peaks (bigBed, for genome browsers)", assays: ["ChIP-seq", "ATAC-seq"], def: true, q: true },
    { cat: "peaks_np", label: "Peaks (narrowPeak text, for analysis)", assays: ["ChIP-seq", "ATAC-seq"], def: false, q: true },
    { cat: "meth", label: "Methylation level CpG/CHG/CHH (bigWig)", assays: ["BS-seq"], def: true },
    { cat: "meth_cov", label: "Methylation read coverage (bigWig)", assays: ["BS-seq"], def: false },
    { cat: "meth_regions", label: "HMR / hyperMR / PMD regions (BED)", assays: ["BS-seq"], def: true },
    { cat: "qc", label: "QC reports (fastp JSON, stats)", assays: ["ChIP-seq", "ATAC-seq", "BS-seq"], def: false },
  ];
  const Q_LABEL = { "05": "q < 1e-5", "10": "q < 1e-10", "20": "q < 1e-20" };

  function fileUrl(catalog, f) { return catalog.data_base + f.key; }

  function fileName(f) { return f.key.split("/").pop(); }

  function safeName(x) { return String(x || "NA").replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, ""); }

  function descriptiveName(s, f) {
    // SRX9800305.10.bb -> SRX9800305_ChIP-seq_H3K27me3_Mpbmi1-1l_thallus.10.bb
    const orig = fileName(f);
    const rest = orig.slice(s.acc.length); // ".10.bb", "_fastp.json", ...
    const parts = [s.acc, s.assay, s.target, s.genotype, s.stage]
      .filter(x => x && x !== "not curated").map(safeName);
    return parts.join("_") + rest;
  }

  /** Files of `samples` matching the chosen categories and peak threshold. */
  function selectFiles(samples, opts) {
    const cats = new Set(opts.cats || FILE_CATS.filter(c => c.def).map(c => c.cat));
    const q = opts.q || "10";
    const out = [];
    for (const s of samples) {
      for (const f of s.files || []) {
        if (!cats.has(f.cat)) continue;
        if (f.q && f.q !== q) continue;
        out.push({ s, f });
      }
    }
    return out;
  }

  function sumBytes(list) { return list.reduce((a, x) => a + (x.f.size || 0), 0); }

  function formatBytes(n) {
    if (n === null || n === undefined) return "–";
    if (n >= 1e9) return (n / 1e9).toFixed(1) + " GB";
    if (n >= 1e6) return (n / 1e6).toFixed(1) + " MB";
    if (n >= 1e3) return (n / 1e3).toFixed(0) + " kB";
    return n + " B";
  }

  // --------------------------------------------------------- manifests
  function filesTxt(catalog, list) {
    return list.map(x => fileUrl(catalog, x.f)).join("\n") + (list.length ? "\n" : "");
  }

  function shQuote(s) { return "'" + String(s).replace(/'/g, "'\\''") + "'"; }

  function downloadSh(catalog, list, opts) {
    const naming = (opts && opts.naming) || "original";
    const total = formatBytes(sumBytes(list));
    const nExp = new Set(list.map(x => x.s.acc)).size;
    const lines = [
      "#!/usr/bin/env bash",
      `# Zenigoke Catalog download script: ${list.length} files from ${nExp} experiments (${total})`,
      `# Release ${catalog.release}; genome ${catalog.genome.id}. Generated ${new Date().toISOString()}`,
      "# Usage: bash download.sh [target_dir]   (re-run to resume; curl -C - continues partial files)",
      "set -euo pipefail",
      'DEST="${1:-zenigoke_' + safeName(catalog.genome.id) + '}"',
      'mkdir -p "$DEST" && cd "$DEST"',
      "fetch() {  # fetch <url> <relative path> <expected bytes | ->",
      '  if [ -f "$2" ] && [ "$3" != "-" ] && [ "$(wc -c < "$2" | tr -d \' \')" = "$3" ]; then echo "skip $2 (complete)"; return 0; fi',
      '  mkdir -p "$(dirname "$2")"',
      '  echo "get  $2"',
      '  curl -fLsS --retry 3 --retry-delay 2 -C - -o "$2" "$1"',
      "}",
    ];
    for (const { s, f } of list) {
      const name = naming === "descriptive" ? descriptiveName(s, f) : fileName(f);
      lines.push(`fetch ${shQuote(fileUrl(catalog, f))} ${shQuote(s.assay + "/" + s.acc + "/" + name)} ${f.size == null ? "-" : f.size}`);
    }
    lines.push('echo "done: ' + list.length + ' files in $PWD"');
    return lines.join("\n") + "\n";
  }

  function tsvCell(v) {
    if (v === null || v === undefined) return "";
    return String(v).replace(/[\t\r\n]+/g, " ");
  }

  const META_COLS = [
    ["accession", s => s.acc], ["assay", s => s.assay], ["target", s => s.target],
    ["target_class", s => s.target_class], ["genotype", s => s.genotype],
    ["genotype_class", s => s.genotype_class], ["developmental_stage", s => s.stage],
    ["tissue", s => s.tissue], ["cell_type", s => s.cell_type], ["treatment", s => s.treatment],
    ["sample_title", s => s.title], ["bioproject", s => s.study], ["sra_study", s => s.srp],
    ["run", s => s.run], ["geo_sample", s => s.gsm], ["biosample", s => s.biosample],
    ["pmid", (s, st) => st.pmid], ["study_title", (s, st) => st.title],
    ["layout", s => s.layout], ["instrument", s => s.instrument],
    ["reads_after_trimming", s => s.metrics.reads], ["mapping_rate_pct", s => s.metrics.mapping_rate],
    ["duplication_rate_pct", s => s.metrics.duplication_rate],
    ["peaks_q1e-5", s => s.metrics.peaks && s.metrics.peaks["05"]],
    ["peaks_q1e-10", s => s.metrics.peaks && s.metrics.peaks["10"]],
    ["peaks_q1e-20", s => s.metrics.peaks && s.metrics.peaks["20"]],
    ["mean_mCpG", s => s.metrics.meth && s.metrics.meth.CpG],
    ["mean_mCHG", s => s.metrics.meth && s.metrics.meth.CHG],
    ["mean_mCHH", s => s.metrics.meth && s.metrics.meth.CHH],
    ["qc_flag", s => s.qc.flag], ["qc_reasons", s => s.qc.reasons.join("; ")],
  ];

  function metadataTsv(catalog, samples) {
    const head = META_COLS.map(c => c[0]).concat(["genome", "catalog_release"]);
    const rows = samples.map(s => {
      const st = catalog.studies[s.study] || {};
      return META_COLS.map(c => tsvCell(c[1](s, st))).concat([catalog.genome.id, catalog.release]);
    });
    return [head].concat(rows).map(r => r.join("\t")).join("\n") + "\n";
  }

  function manifestTsv(catalog, list, opts) {
    const naming = (opts && opts.naming) || "original";
    const head = ["accession", "assay", "file_type", "format", "threshold", "size_bytes", "local_path", "url"];
    const rows = list.map(({ s, f }) => [
      s.acc, s.assay, f.label, f.fmt, f.q ? Q_LABEL[f.q] : "", f.size == null ? "" : f.size,
      s.assay + "/" + s.acc + "/" + (naming === "descriptive" ? descriptiveName(s, f) : fileName(f)),
      fileUrl(catalog, f),
    ].map(tsvCell));
    return [head].concat(rows).map(r => r.join("\t")).join("\n") + "\n";
  }

  // ---------------------------------------------------------------- IGV
  // 127.0.0.1 rather than localhost: the IGV launcher prefers IPv4, and Firefox
  // has exempted 127.0.0.1 from mixed-content blocking longer than "localhost".
  const IGV_PORT_BASE = "http://127.0.0.1:60151";
  const COLORS = {
    "histone modification": "#2f5fa7", "histone / variant": "#4f8fc0",
    "transcription factor": "#2e8b57", "control (input / IgG)": "#888888",
    "tag only (control)": "#888888", "ATAC-seq": "#7b4fa0",
    CpG: "#c0392b", CHG: "#d68910", CHH: "#7e5109", regions: "#555555",
  };

  function trackLabel(s) {
    return [s.acc, s.target || s.assay, s.genotype !== "not curated" ? s.genotype : null,
      s.stage !== "not curated" ? s.stage : null].filter(Boolean).join(" ")
      .replace(/,/g, ";");   // commas would break IGV's comma-separated name= list
  }

  /** IGV track list for samples: signal + peaks (ChIP/ATAC), methylation + HMR (BS). */
  function igvTracks(catalog, samples, opts) {
    const q = (opts && opts.q) || "10";
    const tracks = [];
    for (const s of samples) {
      const base = trackLabel(s);
      for (const f of s.files || []) {
        if (!f.igv) continue;
        if (f.igv === "peaks" && f.q !== q) continue;
        let name, format, type, color;
        if (f.igv === "signal") {
          name = base + " | signal"; format = "bigwig"; type = "wig";
          color = s.assay === "ATAC-seq" ? COLORS["ATAC-seq"] : (COLORS[s.target_class] || "#2f5fa7");
        } else if (f.igv === "peaks") {
          name = base + " | peaks " + Q_LABEL[f.q]; format = "bigbed"; type = "annotation";
          color = s.assay === "ATAC-seq" ? COLORS["ATAC-seq"] : (COLORS[s.target_class] || "#2f5fa7");
        } else if (f.igv === "meth") {
          name = base + " | m" + f.ctx; format = "bigwig"; type = "wig"; color = COLORS[f.ctx];
        } else {
          name = base + " | CpG HMR"; format = "bed"; type = "annotation"; color = COLORS.regions;
        }
        tracks.push({ name, url: fileUrl(catalog, f), format, type, color, acc: s.acc });
      }
    }
    return tracks;
  }

  /** Genome argument for IGV desktop: "hosted" = our genome JSON on S3,
   *  "genark" = UCSC GenArk hub (interim), "none" = use what IGV has open. */
  function genomeRef(catalog, src) {
    if (src === "none") return null;
    if (src === "genark") return catalog.genome.genark.hub;
    return catalog.genome.json;
  }
  function defaultGenomeSrc(catalog) { return catalog.genome.hosted && catalog.genome.hosted.json ? "hosted" : "genark"; }

  /** GET URL for IGV desktop's batch port (CommandListener /load).
   *  file/name/format are parallel comma lists; each item is encoded on its
   *  own and joined with literal commas. genome= takes the hosted genome JSON
   *  URL (its "id" equals that URL, so IGV does not reload it on every call). */
  function igvLoadUrl(catalog, tracks, opts) {
    const o = opts || {};
    const enc = encodeURIComponent;
    const p = [];
    const g = genomeRef(catalog, o.genomeSrc || defaultGenomeSrc(catalog));
    if (g) p.push("genome=" + enc(g));
    if (tracks.length) {
      p.push("file=" + tracks.map(t => enc(t.url)).join(","));
      p.push("name=" + tracks.map(t => enc(t.name.replace(/,/g, ";"))).join(","));
      p.push("format=" + tracks.map(t => enc(t.format)).join(","));
    }
    if (o.locus) p.push("locus=" + enc(o.locus));
    if (tracks.length) p.push("merge=" + (o.merge === false ? "false" : "true"));
    return IGV_PORT_BASE + "/load?" + p.join("&");
  }

  function xmlEsc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function hexToRgb(h) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(h || "");
    return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)].join(",") : "0,0,178";
  }

  /** IGV desktop session file (File > Open Session...). Track format is
   *  inferred by IGV from the file extension (.bw/.bb/.bed). */
  function igvSessionXml(catalog, tracks, opts) {
    const locus = (opts && opts.locus) || catalog.genome.default_locus;
    const L = [
      '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
      `<!-- Zenigoke Catalog ${xmlEsc(catalog.release)}: ${tracks.length} tracks. Open in IGV desktop: File > Open Session... -->`,
      `<Session genome="${xmlEsc(genomeRef(catalog, (opts && opts.genomeSrc) || defaultGenomeSrc(catalog)) || catalog.genome.json)}" locus="${xmlEsc(locus)}" version="8">`,
      "  <Resources>",
    ];
    for (const t of tracks) {
      L.push(`    <Resource name="${xmlEsc(t.name)}" path="${xmlEsc(t.url)}" color="${hexToRgb(t.color)}"/>`);
    }
    L.push("  </Resources>");
    L.push("</Session>");
    return L.join("\n") + "\n";
  }

  /** igv.js browser options for the in-page preview. Uses our hosted FASTA
   *  when available, else the UCSC GenArk assembly (igv.js needs a sequence
   *  source; chrom.sizes alone is not enough). With a GenArk genome igv.js
   *  starts at the hub's default position, so callers should search(locus). */
  function igvJsOptions(catalog, tracks, opts) {
    const g = catalog.genome;
    const o = {
      locus: (opts && opts.locus) || g.default_locus,
      tracks: tracks.map(t => ({
        name: t.name, url: t.url, format: t.format, type: t.type, color: t.color,
        height: t.type === "wig" ? 50 : 25, autoscale: t.type === "wig",
        displayMode: t.type === "annotation" ? "COLLAPSED" : undefined,
      })),
    };
    if (g.hosted && g.hosted.fasta) {
      o.reference = { id: g.json, name: g.id, fastaURL: g.fasta, indexURL: g.fai,
        tracks: g.hosted.gff ? [{ name: "genes (MarpolBase)", format: "gff3", url: g.gff, indexed: false, searchable: true, displayMode: "EXPANDED" }] : [] };
    } else {
      o.genome = g.genark.accession;
    }
    return o;
  }

  // ---------------------------------------------------------------- misc
  function pct(x, d) { return x === null || x === undefined ? "–" : Number(x).toFixed(d === undefined ? 1 : d) + "%"; }
  function frac(x) { return x === null || x === undefined ? "–" : (x * 100).toFixed(1) + "%"; }
  function num(x) { return x === null || x === undefined ? "–" : Number(x).toLocaleString("en-US"); }

  return {
    FACETS, FACET_BY_KEY, FILE_CATS, Q_LABEL, SORTS, IGV_PORT_BASE,
    prepare, filterSamples, facetCounts, encodeState, decodeState,
    selectFiles, sumBytes, formatBytes, fileUrl, fileName, descriptiveName,
    filesTxt, downloadSh, metadataTsv, manifestTsv,
    igvTracks, igvLoadUrl, igvSessionXml, igvJsOptions, trackLabel, genomeRef, defaultGenomeSrc,
    pct, frac, num,
  };
}));
