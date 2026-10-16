// Renders docs/screenshots/logo-variants.png: NODE_PATH=/path/to/node_modules node scripts/logo/render.cjs docs/screenshots/logo-variants.png
const puppeteer = require("puppeteer-core");
const V = require("./variants.js");
const svg = (k, s, ink = "#0C0A09", paper = "#FAFAF9") => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none">${V[k](ink, paper)}</svg>`;
const names = { A: "A. Ring and edge (current)", B: "B. Zero with edge", C: "C. Orbit" };
const notes = {
  A: "Hairline edge and 1.25 stroke vanish at 16 px; ring reads light next to the semibold wordmark.",
  B: "A true zero: upright oval at 2.5 stroke matches the wordmark weight; larger node and 1.75 edge survive at 16 px.",
  C: "Strongest at 16 px, but the center dot reads as a target or radio button and the graph idea (an edge) is gone.",
};
const card = (k, pick) => `
<div class="card ${pick ? "pick" : ""}">
  <div class="head"><span>${names[k]}</span>${pick ? '<span class="tag">Chosen</span>' : ""}</div>
  <div class="row big">${svg(k, 96)}</div>
  <div class="row sizes">
    <div>${svg(k, 48)}<small>48</small></div>
    <div>${svg(k, 32)}<small>32</small></div>
    <div>${svg(k, 24)}<small>24</small></div>
    <div>${svg(k, 16)}<small>16</small></div>
    <div class="dark">${svg(k, 16, "#FAFAF9", "#0C0A09")}<small>16 dark</small></div>
  </div>
  <div class="row tab"><div class="chrome">${svg(k, 16)}<span>PatientZero</span></div></div>
  <div class="row lockup">${svg(k, 22)}<b>PatientZero</b></div>
  <p>${notes[k]}</p>
</div>`;
const html = `<!doctype html><html><head><style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
body{margin:0;background:#FAFAF9;font-family:Inter,system-ui,sans-serif;color:#0C0A09;padding:40px 44px}
h1{font-size:20px;font-weight:600;letter-spacing:-0.02em;margin:0 0 4px}
.sub{font-size:13px;color:#57534E;margin:0 0 24px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}
.card{background:#fff;border:1px solid #E7E5E4;border-radius:12px;padding:20px}
.card.pick{border-color:#0C0A09}
.head{display:flex;justify-content:space-between;font-size:14px;font-weight:600;letter-spacing:-0.01em}
.tag{font-size:12px;font-weight:500;color:#C4282D}
.row{display:flex;align-items:center;justify-content:center;border-top:1px solid #E7E5E4;padding:16px 0}
.row:first-of-type{border-top:0}
.big{padding:24px 0}
.sizes{gap:22px;align-items:flex-end}
.sizes div{display:flex;flex-direction:column;align-items:center;gap:6px}
.sizes .dark{background:#0C0A09;padding:8px 10px;border-radius:6px}
.sizes .dark small{color:#A8A29E}
small{font-size:11px;color:#A8A29E}
.chrome{display:flex;align-items:center;gap:8px;background:#EFEDEB;border-radius:8px 8px 0 0;padding:7px 14px;font-size:12px;color:#57534E;width:180px}
.lockup{gap:8px}.lockup b{font-size:15px;font-weight:600;letter-spacing:-0.02em}
p{font-size:12.5px;color:#57534E;line-height:1.5;margin:12px 0 0}
</style></head><body>
<h1>PatientZero mark: three variants</h1>
<p class="sub">Same 24-unit grid, ink #0C0A09 and one vermilion node #E5484D. Checked at 16 px (favicon), on dark, and next to the 15 px semibold wordmark.</p>
<div class="grid">${card("A")}${card("B", true)}${card("C")}</div>
</body></html>`;
(async () => {
  const b = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome", args: ["--no-sandbox"], defaultViewport: { width: 1440, height: 900, deviceScaleFactor: 2 } });
  const p = await b.newPage();
  await p.setContent(html, { waitUntil: "networkidle0" });
  const h = await p.evaluate(() => document.body.scrollHeight);
  await p.setViewport({ width: 1440, height: h, deviceScaleFactor: 2 });
  await p.screenshot({ path: process.argv[2] || "/tmp/rv/logo/variants.png" });
  await b.close();
})();
