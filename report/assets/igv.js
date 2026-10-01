"use strict";
// Shared IGV helpers: track URLs, the IGV batch-port /load URL, and sending.
// Loaded as a plain <script> (window.ZenigokeIGV) and by node unit tests.
(function (root) {
  const DEFAULT_GENOME = "https://hgdownload.soe.ucsc.edu/hubs/GCA/039/105/155/GCA_039105155.1/hub.txt";
  const Q_LABEL = {"1e-5": "05", "1e-10": "10", "1e-20": "20"};

  function dataUrl(path, base) {
    const b = (base || "").replace(/\/+$/, "");
    return b ? `${b}/${path.replace(/^\/+/, "")}` : path;
  }

  function cleanName(s) { return String(s).replace(/,/g, ";"); }

  function tracksForAccession(acc, opts, color, base) {
    const strat = opts.strategy;
    const label = opts.label ? ` ${opts.label}` : "";
    if (strat === "ChIP-Seq" || strat === "ATAC-Seq") {
      const sub = strat === "ChIP-Seq" ? "chipseq" : "atacseq";
      const q = opts.q_cutoff || "1e-10";
      const ql = Q_LABEL[q] || "10";
      return [
        {name: cleanName(`${acc}${label} | signal`), url: dataUrl(`output/${sub}/${acc}/${acc}.bw`, base), format: "bigwig", color},
        {name: cleanName(`${acc}${label} | peaks q<${q}`), url: dataUrl(`output/${sub}/${acc}/${acc}.${ql}.bb`, base), format: "bigbed", color},
      ];
    }
    if (strat === "Bisulfite-Seq") {
      const t = (ctx) => ({name: cleanName(`${acc}${label} | ${ctx} methyl`),
                           url: dataUrl(`output/bsseq/${acc}/${acc}.${ctx}.methyl.bw`, base), format: "bigwig", color});
      return [t("CpG"), t("CHG"), t("CHH"),
              {name: cleanName(`${acc}${label} | CpG HMR`), url: dataUrl(`output/bsseq/${acc}/${acc}.CpG.hmr.bed`, base), format: "bed", color}];
    }
    return [];
  }

  function loadUrl(tracks, opts) {
    const o = opts || {};
    const port = o.port || 60151;
    const enc = (xs) => xs.map(encodeURIComponent).join(",");
    let u = `http://127.0.0.1:${port}/load?genome=${encodeURIComponent(o.genome || DEFAULT_GENOME)}` +
      `&file=${enc(tracks.map(t => t.url))}&name=${enc(tracks.map(t => t.name))}` +
      `&format=${enc(tracks.map(t => t.format))}&merge=true`;
    if (o.locus) u += `&locus=${encodeURIComponent(o.locus)}`;
    return u;
  }

  async function send(tracks, opts) {
    const o = opts || {};
    const f = o.fetchImpl || ((u) => fetch(u));
    const port = o.port || 60151;
    try {
      await f(`http://127.0.0.1:${port}/execute?command=version`);
    } catch (e) {
      return {ok: false, status: "unreachable",
              message: "Could not reach IGV on port " + port + ". Start IGV desktop and enable its port (View › Preferences › Advanced › Enable port)."};
    }
    let r;
    try {
      r = await f(loadUrl(tracks, {...o, genome: o.genome || (root && root.ZENIGOKE_IGV_GENOME) || DEFAULT_GENOME}));
    } catch (e) {
      return {ok: false, status: "unreachable", message: "IGV stopped responding while loading."};
    }
    if (r.status !== 200 && r.status !== 204) {
      return {ok: false, status: "igv-error", message: "IGV answered HTTP " + r.status + " to the load request."};
    }
    const body = r.status === 200 ? String(await r.text()) : "";
    if (body.startsWith("ERROR")) return {ok: false, status: "igv-error", message: body};
    return {ok: true, status: "loaded", message: `Loaded ${tracks.length} tracks in IGV`};
  }

  const api = {DEFAULT_GENOME, Q_LABEL, dataUrl, tracksForAccession, loadUrl, send};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.ZenigokeIGV = api;
})(typeof window !== "undefined" ? window : null);
