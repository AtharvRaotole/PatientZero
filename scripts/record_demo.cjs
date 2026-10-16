// Records the ~2 minute draft demo video headlessly, beat by beat per docs/DEMO.md, against the live site.
//
//   sh scripts/reset-live.sh && sh scripts/seed-demo-memory.sh       # memory seeded, as judges will see it
//   (cd backend && uv run python ../scripts/mcp_demo_client.py > ../demo/mcp-capture.txt)   # REAL MCP output
//   NODE_PATH=<dir with playwright-core>/node_modules node scripts/record_demo.cjs
//   sh scripts/reset-live.sh && sh scripts/seed-demo-memory.sh       # leave exactly one approved incident
//
// Output: demo/patientzero-demo.mp4 (NAME=patientzero-demo-draft for the draft) (1920x1080 H.264, captions burned in, no audio) and
// demo/patientzero-demo-draft.srt. Env: APP_URL, CHROME (/usr/bin/google-chrome), KEEP_RAW=1.
// The app runs in Auto mode, but every beat here is answered by the deterministic router: no LLM calls.
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const APP = process.env.APP_URL || "https://patientzero-production-2cb3.up.railway.app";
const ROOT = path.join(__dirname, "..");
const OUT = path.join(ROOT, "demo");
const RAW = path.join(OUT, "raw");
const W = 1600, H = 830; // recorded viewport, scaled to 1920x996 on encode
const STRIP = 84; // caption strip under the page (1080 - 996): captions never cover the UI
const NAME = process.env.NAME || "patientzero-demo";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- visible cursor (headless video has none)
const CURSOR = () => {
  const install = () => {
    if (document.getElementById("__pz_cursor")) return;
    const c = document.createElement("div");
    c.id = "__pz_cursor";
    c.innerHTML = `<svg width="22" height="22" viewBox="0 0 24 24"><path d="M4 2.5 L4 19.5 L8.6 15.3 L11.6 21.6 L14.6 20.2 L11.7 14.1 L18 14.1 Z" fill="#0C0A09" stroke="#FFFFFF" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
    Object.assign(c.style, { position: "fixed", left: "-40px", top: "-40px", zIndex: 2147483647, pointerEvents: "none", transform: "translate(-3px,-2px)", filter: "drop-shadow(0 1px 1.5px rgba(0,0,0,.25))" });
    document.documentElement.appendChild(c);
    addEventListener("mousemove", (e) => { c.style.left = e.clientX + "px"; c.style.top = e.clientY + "px"; }, true);
    addEventListener("mousedown", (e) => {
      const r = document.createElement("div");
      Object.assign(r.style, { position: "fixed", left: e.clientX - 16 + "px", top: e.clientY - 16 + "px", width: "32px", height: "32px", borderRadius: "50%", border: "2px solid rgba(229,72,77,.7)", zIndex: 2147483646, pointerEvents: "none", transition: "transform .45s ease-out, opacity .45s ease-out" });
      document.documentElement.appendChild(r);
      requestAnimationFrame(() => { r.style.transform = "scale(1.6)"; r.style.opacity = "0"; });
      setTimeout(() => r.remove(), 500);
    }, true);
    document.documentElement.style.scrollBehavior = "auto";
  };
  if (document.readyState === "loading") addEventListener("DOMContentLoaded", install); else install();
};

// ---------------------------------------------------------------- helpers
let page, t0 = 0, mouse = { x: W / 2, y: H / 2 };
const caps = []; // {start, end, text} in seconds from t0
const now = () => (Date.now() - t0) / 1000;
function caption(text, secs) { caps.push({ start: now(), end: now() + secs, text }); }
const beats = [];
function beat(name) { beats.push({ t: now(), name }); console.log(`[${now().toFixed(1)}s] ${name}`); }

async function moveTo(x, y, ms = 700) {
  const sx = mouse.x, sy = mouse.y, n = Math.max(8, Math.round(ms / 16));
  for (let i = 1; i <= n; i++) {
    const k = i / n, e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
    await page.mouse.move(sx + (x - sx) * e, sy + (y - sy) * e);
    await sleep(ms / n);
  }
  mouse = { x, y };
}
async function box(loc) {
  loc = loc.first();
  await loc.waitFor({ state: "visible", timeout: 60000 });
  const b = await loc.boundingBox();
  return { x: b.x + b.width / 2, y: b.y + b.height / 2, b };
}
async function hover(loc, ms = 700) { const p = await box(loc); await moveTo(p.x, p.y, ms); return p; }
async function click(loc, ms = 700) {
  const p = await hover(loc, ms);
  await sleep(180);
  await page.mouse.down(); await sleep(70); await page.mouse.up();
  return p;
}
async function scrollToY(y, ms = 1000) {
  await page.evaluate(([y, ms]) => new Promise((res) => {
    const s = scrollY, d = Math.max(0, y) - s, st = performance.now();
    const f = (t) => { const k = Math.min(1, (t - st) / ms), e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2; scrollTo(0, s + d * e); k < 1 ? requestAnimationFrame(f) : res(); };
    requestAnimationFrame(f);
  }), [y, ms]);
}
async function scrollToEl(loc, offset = 80, ms = 1000) {
  await loc.first().waitFor({ state: "attached", timeout: 60000 });
  const y = await loc.first().evaluate((el, off) => el.getBoundingClientRect().top + scrollY - off, offset);
  await scrollToY(y, ms);
}
async function typeHuman(text) {
  for (const ch of text) { await page.keyboard.type(ch); await sleep(70 + Math.random() * 90); }
}
const text = (t) => page.getByText(t, { exact: true });
const waitText = (re, timeout = 60000) => page.waitForFunction((src) => new RegExp(src).test(document.body.innerText), re.source, { timeout });

// Hover a graph node by id. The canvas has no DOM nodes, so search near an approximate spot (fractions of the
// graph frame) with the cursor hidden, using the canvas's data-hover attribute, then glide there visibly.
const found = {}; // id -> [fx, fy], from the unrecorded pre-pass
async function searchNode(pg, id, fx, fy) {
  const el = pg.locator("[data-graph]");
  const b = await el.boundingBox();
  const ax = b.x + b.width * fx, ay = b.y + b.height * fy;
  for (let r = 0; r <= 70; r += 4) {
    const n = Math.max(1, Math.round((2 * Math.PI * r) / 5));
    for (let k = 0; k < n; k++) {
      const x = ax + r * Math.cos((k / n) * 2 * Math.PI), y = ay + r * Math.sin((k / n) * 2 * Math.PI);
      await pg.mouse.move(x, y);
      await sleep(20);
      if ((await el.getAttribute("data-hover")) === id) return [(x - b.x) / b.width, (y - b.y) / b.height];
    }
  }
  return null;
}
async function hoverNode(id, fx, fy, ms = 1000) {
  if (found[id]) {
    const { b } = await box(page.locator("[data-graph]"));
    await moveTo(b.x + b.width * found[id][0], b.y + b.height * found[id][1], ms);
    return;
  }
  const { b } = await box(page.locator("[data-graph]"));
  const ax = b.x + b.width * fx, ay = b.y + b.height * fy;
  await page.evaluate(() => { const c = document.getElementById("__pz_cursor"); if (c) c.style.visibility = "hidden"; });
  let hit = null;
  outer: for (let r = 0; r <= 60; r += 5) {
    const n = Math.max(1, Math.round((2 * Math.PI * r) / 6));
    for (let k = 0; k < n; k++) {
      const x = ax + r * Math.cos((k / n) * 2 * Math.PI), y = ay + r * Math.sin((k / n) * 2 * Math.PI);
      await page.mouse.move(x, y);
      await sleep(18);
      if ((await page.locator("[data-graph]").getAttribute("data-hover")) === id) { hit = { x, y }; break outer; }
    }
  }
  await page.mouse.move(mouse.x, mouse.y);
  await sleep(30);
  await page.evaluate(() => { const c = document.getElementById("__pz_cursor"); if (c) c.style.visibility = "visible"; });
  if (!hit) { console.log(`  (node ${id} not found near ${fx},${fy})`); hit = { x: ax, y: ay }; }
  await moveTo(hit.x, hit.y, ms);
}

// Depth slider: drag the thumb like a person would.
async function dragDepth(from, to, t1 = 600, t2 = 700) {
  const s = page.locator("input[aria-label='graph depth']");
  const { b } = await box(s);
  const xAt = (v) => b.x + 8 + ((v - 1) / 5) * (b.width - 16);
  const y = b.y + b.height / 2;
  await moveTo(xAt(from), y, t1);
  await page.mouse.down();
  await moveTo(xAt(to), y, t2);
  await page.mouse.up();
}

// ---------------------------------------------------------------- the MCP beat (real captured output)
function mcpHtml(lines) {
  const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
  return `<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{margin:0;height:100%;background:#FAFAF9;font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:#0C0A09}
  .wrap{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px}
  .kicker{font-size:15px;color:#57534E;letter-spacing:-.01em}
  .kicker b{color:#0C0A09;font-weight:600}
  .term{width:1180px;border-radius:12px;background:#0C0A09;box-shadow:0 24px 48px -16px rgba(12,10,9,.25);overflow:hidden}
  .bar{height:34px;display:flex;align-items:center;gap:7px;padding:0 14px;border-bottom:1px solid #292524}
  .bar i{width:10px;height:10px;border-radius:50%;background:#44403C;display:block}
  .bar span{margin-left:10px;font:12px ui-monospace,monospace;color:#A8A29E}
  pre{margin:0;padding:22px 24px 26px;font:15.5px/1.65 "DejaVu Sans Mono",ui-monospace,monospace;color:#E7E5E4;white-space:pre-wrap;word-break:break-word;min-height:300px}
  .p{color:#A8A29E}.o{color:#FAFAF9}.r{color:#F2A7A9}
  .note{font-size:13px;color:#69625D}
  </style></head><body><div class="wrap">
  <div class="kicker"><b>The same tools over MCP.</b> Real output, replayed: an MCP client calling <code>remediation_plan</code>, the entry point behind the qix replay.</div>
  <div class="term"><div class="bar"><i></i><i></i><i></i><span>backend, local</span></div><pre id="t"></pre></div>
  <div class="note">Captured from <code>scripts/mcp_demo_client.py</code>: the MCP server (backend/app/mcp_server.py) over stdio, run locally against the same fleet graph. The incident it wrote went to the local dev graph, not the live site.</div>
  </div><script>
  const cmd = "uv run python ../scripts/mcp_demo_client.py";
  const lines = ${JSON.stringify(lines.map(esc))};
  const t = document.getElementById("t");
  let i = 0;
  function typeCmd(){ if(i<=cmd.length){ t.innerHTML='<span class="p">$ </span><span class="o">'+cmd.slice(0,i)+'</span>'; i++; setTimeout(typeCmd, 20); } else { setTimeout(()=>out(0), 300); } }
  function out(k){ if(k>=lines.length) return; const l=lines[k]; t.innerHTML += '\\n<span class="'+(l.startsWith('&lt;-')?'r':'o')+'">'+l+'</span>'; setTimeout(()=>out(k+1), 260); }
  setTimeout(typeCmd, 300);
  </script></body></html>`;
}

function srtTime(s) {
  s = Math.max(0, s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60), ms = Math.round((s - Math.floor(s)) * 1000);
  const p = (n, w = 2) => String(n).padStart(w, "0");
  return `${p(h)}:${p(m)}:${p(sec)},${p(Math.min(ms, 999), 3)}`;
}

// ---------------------------------------------------------------- run
(async () => {
  fs.mkdirSync(RAW, { recursive: true });
  for (const f of fs.readdirSync(RAW)) fs.unlinkSync(path.join(RAW, f));
  const mcpFile = path.join(OUT, "mcp-capture.txt");
  if (!fs.existsSync(mcpFile)) throw new Error("demo/mcp-capture.txt missing: run scripts/mcp_demo_client.py first (see header)");
  const mcpLines = fs.readFileSync(mcpFile, "utf8").trim().split("\n");

  const browser = await chromium.launch({ executablePath: process.env.CHROME || "/usr/bin/google-chrome", headless: true, args: ["--no-sandbox", "--hide-scrollbars"] });
  // Pre-pass, not recorded: find where the services we hover sit in the (fixed, radial) fleet layout.
  {
    const pre = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
    const pg = await pre.newPage();
    await pg.goto(APP, { waitUntil: "load", timeout: 90000 });
    await pg.waitForFunction(() => document.body.innerText.includes("package versions"), null, { timeout: 60000 });
    await pg.evaluate(() => document.getElementById("graph").scrollIntoView()); // the whole frame in view
    await sleep(2500);
    for (const [id, fx, fy] of [["web-frontend", 0.139, 0.297], ["img-service", 0.133, 0.386], ["auth-service", 0.732, 0.881]]) {
      found[id] = await searchNode(pg, id, fx, fy);
      console.log(`located ${id}: ${found[id] ? found[id].map((v) => v.toFixed(3)).join(", ") : "not found"}`);
    }
    await pre.close();
  }
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1, recordVideo: { dir: RAW, size: { width: W, height: H } } });
  await ctx.addInitScript(CURSOR);
  page = await ctx.newPage();
  t0 = Date.now();
  await page.goto(APP, { waitUntil: "load", timeout: 90000 });
  await waitText(/package versions/);
  await page.mouse.move(mouse.x, mouse.y);
  const trim = now(); // the video starts here, once the app has painted
  beat("home");
  await sleep(800);
  caption("Sept 8, 2025: one npm maintainer, qix, was phished.", 3.6);
  await hover(page.getByText("Sept 8, 2025. One phished maintainer", { exact: false }), 900);
  await sleep(2600);
  caption("19 packages, 2.71 billion downloads the week before. Are we hit?", 4.2);
  // a quiet look at the fleet graph: hover a service so its dependency chain lights up
  await hoverNode("web-frontend", 0.139, 0.297, 1000);
  await sleep(2600);

  // 0:12 Replay
  beat("replay");
  caption("PatientZero answers from a FalkorDB graph of our 22 services.", 3.6);
  await click(page.getByRole("button", { name: "Replay the qix compromise", exact: true }), 900);
  await waitText(/services can install a malicious version/);
  await sleep(3300); // auto-scroll to the graph, chains draw in hop by hop
  beat("graph lit");
  caption("Red means the range on the last edge admits the malicious version.", 4.4);
  await hoverNode("img-service", 0.133, 0.386, 1000); // a 4-hop chain lights up on its own
  await sleep(2600);
  await hoverNode("auth-service", 0.732, 0.881, 900);
  caption("Six hand-written Cypher queries, answered in milliseconds.", 3.4);
  await sleep(1500);
  const gg = await box(page.locator("[data-graph]"));
  await moveTo(gg.b.x + gg.b.width * 0.5, gg.b.y - 30, 800);
  await sleep(1500);

  // 0:28 Exposure
  beat("exposure");
  await scrollToEl(page.locator("h2", { hasText: /^Exposure$/ }), 90, 1100);
  caption("Every chain ends in an npm semver check on the last edge.", 4);
  await hover(page.getByText("auth-service", { exact: true }).first(), 900);
  await sleep(2500);
  beat("honest negatives");
  await click(page.getByRole("tab", { name: /Excluded by range/ }).first(), 800);
  caption("Honest negatives: checkout-api reaches debug, but its range excludes the bad version.", 4.6);
  await sleep(400);
  await hover(page.getByText("checkout-api", { exact: true }).first(), 900);
  await sleep(2900);

  // 0:44 Depth
  beat("depth");
  await scrollToEl(page.locator("#graph"), 70, 800);
  await dragDepth(4, 1, 350, 450);
  await waitText(/1 of 22 services can install/);
  caption("Direct dependencies only: 1 service. Transitive depth is the whole story.", 3.6);
  await sleep(1900);
  await dragDepth(1, 4, 300, 450);
  await waitText(/13 of 22 services can install/);
  await sleep(700);

  // 0:55 Insights
  beat("insights");
  await scrollToEl(page.locator("h3", { hasText: /^Insights$/ }), 120, 1200);
  caption("14 of the 19 compromised packages are reachable from this fleet.", 4.8);
  const rings = await box(page.locator("svg").filter({ has: page.locator("text", { hasText: /compromised packages/ }) }).first());
  await moveTo(rings.x, rings.y, 1000); // the core fans out on hover
  await sleep(2200);
  await click(page.getByRole("tab", { name: "Exposure matrix" }), 800);
  caption("Every cell in the matrix is a path in the graph.", 3.2);
  await moveTo(rings.b.x + 260, rings.b.y + 150, 800);
  await sleep(2200);

  // 1:07 Who to wake, then Fix
  beat("who to wake");
  await scrollToEl(page.locator("h2", { hasText: /^Who to wake$/ }), 90, 1100);
  caption("Owning team and on-call for every exposed service, from the graph.", 3.2);
  await sleep(2900);
  beat("fix");
  await scrollToEl(page.locator("h2", { hasText: /^Fix$/ }), 90, 1100);
  await click(page.getByRole("button", { name: /Generate fix/ }), 900);
  await waitText(/written to the graph/);
  caption("Overrides pinned to clean versions, one draft per team. Written back to FalkorDB.", 4.6);
  await sleep(1500);
  await scrollToEl(page.locator("h2", { hasText: /^Fix$/ }), 90, 600);
  await sleep(1200);
  beat("approve");
  await click(page.getByRole("button", { name: /^Approve$/ }), 900);
  await waitText(/Approved and remembered/);
  caption("A human approves the plan; it is remembered as a playbook.", 3.4);
  await sleep(2800);

  // 1:22 Recall
  beat("recall");
  await scrollToY(0, 1000);
  const input = page.locator("input[aria-label='Ask PatientZero']");
  await click(input, 900);
  await page.keyboard.press("Control+A");
  await sleep(250);
  caption("Ask again, in one word.", 2.6);
  await typeHuman("qix");
  await sleep(500);
  await page.keyboard.press("Enter");
  await waitText(/Answered from graph memory/);
  await sleep(500);
  await scrollToY(0, 400);
  await hover(page.locator("[data-testid='interpreted']"), 700); // the typed text stays; the reading shows below
  await sleep(1000);
  await scrollToEl(page.locator("#report"), 80, 1000);
  beat("memory answer");
  caption("Answered from graph memory, citing the incident and when.", 4.2);
  await hover(page.locator("[data-testid='memory-line']"), 900);
  await sleep(3000);

  // 1:32 Timeline
  beat("timeline");
  await scrollToEl(page.locator("#timeline"), 70, 1300);
  caption("Compromise, advisories, our incident and the approved playbook, as dated nodes.", 4.4);
  await sleep(4200);

  // 1:40 Bring your own repo
  beat("byo axios");
  await scrollToEl(page.locator("#byo"), 70, 900);
  caption("Bring your own repo: its real dependency graph, in a separate FalkorDB graph.", 2.6);
  await click(page.getByRole("button", { name: "axios/axios" }), 650);
  await waitText(/Runtime exposed\. A fresh install could have pulled malware/, 120000);
  await sleep(300);
  await scrollToEl(page.getByText("Runtime exposed. A fresh install could have pulled malware.", { exact: true }), 110, 800);
  caption("axios: exposed at runtime through https-proxy-agent, then debug.", 3.4);
  await hover(page.getByText("debug@4.4.2", { exact: true }).last(), 600);
  await sleep(1500);
  beat("byo express");
  await scrollToEl(page.locator("#byo"), 70, 1000);
  await click(page.getByRole("button", { name: "expressjs/express@4.x (runtime only)" }), 900);
  await waitText(/Runtime clean\./, 120000);
  await sleep(600);
  await scrollToEl(page.getByText("Runtime clean.", { exact: true }), 110, 1100);
  caption("Express 4 pins debug 2.6.9 exactly, so its runtime was clean.", 4.4);
  await hover(page.getByText(/Excluded by range: debug 2\.6\.9/).first(), 800);
  await sleep(2800);

  // MCP beat, then close
  beat("mcp");
  await page.setContent(mcpHtml(mcpLines), { waitUntil: "load" });
  await page.mouse.move(-50, -50);
  caption("The same tools over MCP: a real client gets the same 13 of 22.", 6.6);
  await sleep(7100);
  beat("close");
  await page.setContent(`<!doctype html><html><body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#FAFAF9;font-family:Inter,sans-serif">
    <div style="text-align:center"><div style="display:flex;align-items:center;gap:14px;justify-content:center">
    <svg width="44" height="44" viewBox="0 0 24 24" fill="none"><ellipse cx="10.5" cy="13.25" rx="6.1" ry="8.25" stroke="#0C0A09" stroke-width="2.5"/><path d="M15.6 7.4 L20.9 2.6" stroke="#0C0A09" stroke-width="1.75" stroke-linecap="round"/><circle cx="20.9" cy="2.6" r="1.6" fill="#0C0A09"/><circle cx="15.1" cy="7.75" r="3.5" fill="#E5484D" stroke="#FAFAF9" stroke-width="1.6"/></svg>
    <span style="font-size:44px;font-weight:600;letter-spacing:-.025em;color:#0C0A09">PatientZero</span></div>
    <p style="margin-top:18px;font-size:20px;color:#57534E">Know your blast radius before the postmortem.</p>
    <p style="margin-top:6px;font-size:15px;color:#69625D">FalkorDB graph traversal, npm semver on the last edge, graph memory.</p></div></body></html>`);
  caption("Deterministic first. The LLM only when needed, name-checked, with hard spend caps.", 4.6);
  await sleep(4800);
  const end = now();

  const video = page.video();
  await ctx.close();
  await browser.close();
  const raw = await video.path();

  // captions relative to the trimmed start
  const srt = caps.map((c, i) => `${i + 1}\n${srtTime(c.start - trim)} --> ${srtTime(Math.min(c.end, end) - trim)}\n${c.text}\n`).join("\n");
  const srtPath = path.join(OUT, `${NAME}.srt`);
  fs.writeFileSync(srtPath, srt);
  fs.writeFileSync(path.join(OUT, "beats.json"), JSON.stringify(beats.map((b) => ({ ...b, t: +(b.t - trim).toFixed(1) })), null, 1));

  const mp4 = path.join(OUT, `${NAME}.mp4`);
  const style = "FontName=Inter,FontSize=7.5,PrimaryColour=&H00FFFFFF,OutlineColour=&H30141210,BackColour=&H30141210,BorderStyle=3,Outline=4,Shadow=0,MarginV=${process.env.MARGINV || 7},Bold=0";
  const fontsdir = "/usr/share/fonts/truetype/sand-box/google/Inter";
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-ss", String(trim), "-i", raw, "-t", String(end - trim),
    "-vf", `fps=30,scale=1920:${Math.round(H * 1920 / W)}:flags=lanczos,pad=1920:1080:0:0:color=0xFAFAF9,subtitles=${srtPath}:fontsdir=${fontsdir}:force_style='${style}'`,
    "-c:v", "libx264", "-preset", "slow", "-crf", process.env.CRF || "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", mp4], { stdio: "inherit" });
  if (!process.env.KEEP_RAW) fs.rmSync(RAW, { recursive: true, force: true });
  console.log(`wrote ${path.relative(ROOT, mp4)} (${(fs.statSync(mp4).size / 1e6).toFixed(1)} MB, ${(end - trim).toFixed(1)} s) and ${path.relative(ROOT, srtPath)}`);
})().catch((e) => { console.error("RECORD ERROR", e); process.exit(1); });
