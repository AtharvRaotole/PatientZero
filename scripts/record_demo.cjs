// Records the 2:00 demo against the live site, in the exact beat order of docs/DEMO.md, holding each beat at least as
// long as its narration clip so the voiceover never runs into the next beat.
//
//   CLEAR_REPLAYS=1 sh scripts/reset-live.sh && TENANT=globex CLEAR_REPLAYS=1 sh scripts/reset-live.sh   # empty memory, no reseed
//   python3 scripts/voiceover.py --clips        # narration clips + demo/vo/durations.json (cached, cents)
//   NODE_PATH=/workspace/tools/node_modules node scripts/record_demo.cjs
//   python3 scripts/voiceover.py                # captions, demo/patientzero-demo.mp4 (silent) and -voiced.mp4
//   then reset both tenants again with CLEAR_REPLAYS=1 (no reseed), so judges get a clean first run.
//
// Output: demo/raw-demo.mp4 (1920x996 H.264, no captions, no audio; voiceover.py pads it to 1080 and burns captions in)
// and demo/beats.json. Env: APP_URL, CHROME (/usr/bin/google-chrome), KEEP_RAW=1.
// Every input is answered by the deterministic router: no LLM calls. Approve happens on camera; the watch Replay
// button is clicked once, at 1:28. The page is loaded once, unrecorded, first (warm-up), and the header counters
// are left to settle before the first beat.
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const APP = process.env.APP_URL || "https://patientzero-production-2cb3.up.railway.app";
const ROOT = path.join(__dirname, "..");
const OUT = path.join(ROOT, "demo");
const RAW = path.join(OUT, "raw");
const W = 1600, H = 830; // recorded viewport, scaled to 1920x996 on encode
const LEAD = 0.3, TAIL = 0.3; // voice starts LEAD s into a beat; the beat ends TAIL s after the voice does
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const DUR = JSON.parse(fs.readFileSync(path.join(OUT, "vo", "durations.json"), "utf8"));

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
  for (const ch of text) { await page.keyboard.type(ch); await sleep(38 + Math.random() * 34); }
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

// Hold the current beat until its narration has finished.
let cur = null;
async function hold() {
  if (!cur) return;
  const until = cur.t + LEAD + (DUR[cur.name] || 0) + TAIL;
  const wait = until - now();
  if (wait > 0) await sleep(wait * 1000);
  else console.log(`  (beat ${cur.name} ran ${(-wait).toFixed(1)} s past its narration)`);
}
async function startBeat(name) { await hold(); beat(name); cur = beats[beats.length - 1]; }

const INPUT = "input[aria-label='Ask PatientZero']";
async function ask(q, ms = 600) {
  await scrollToY(0, ms);
  await click(page.locator(INPUT), 500);
  await page.keyboard.press("Control+A");
  await page.keyboard.press("Backspace");
  await typeHuman(q);
  await page.keyboard.press("Escape");
  await sleep(120);
  await page.keyboard.press("Enter");
}
async function switchTenant(id) {
  await scrollToY(0, 500);
  await click(page.locator("[data-testid=tenant-switcher] button"), 450);
  await sleep(200);
  await click(page.locator(`[data-tenant-option=${id}]`), 400);
  await page.waitForFunction((t) => document.querySelector("[data-testid=graph-name]")?.textContent === `fleet:${t}`, id, { timeout: 30000 });
}

// ---------------------------------------------------------------- run
(async () => {
  fs.mkdirSync(RAW, { recursive: true });
  for (const f of fs.readdirSync(RAW)) fs.unlinkSync(path.join(RAW, f));
  const browser = await chromium.launch({ executablePath: process.env.CHROME || "/usr/bin/google-chrome", headless: true, args: ["--no-sandbox", "--hide-scrollbars"] });

  // Warm-up, not recorded: load the page once, wait for the fleet graph, find where the hovered service sits.
  {
    const pre = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
    const pg = await pre.newPage();
    await pg.goto(APP, { waitUntil: "load", timeout: 90000 });
    await pg.waitForFunction(() => document.body.innerText.includes("package versions"), null, { timeout: 90000 });
    await pg.evaluate(() => document.getElementById("graph").scrollIntoView());
    await sleep(2500);
    found["img-service"] = await searchNode(pg, "img-service", 0.133, 0.386);
    console.log(`warm-up done; img-service at ${found["img-service"] ? found["img-service"].map((v) => v.toFixed(3)).join(", ") : "not found"}`);
    await pre.close();
  }

  const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1, recordVideo: { dir: RAW, size: { width: W, height: H } } });
  await ctx.addInitScript(CURSOR);
  page = await ctx.newPage();
  const consoleErrors = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("response", (r) => { if (r.status() >= 400) consoleErrors.push(`${r.status()} ${r.url()}`); });
  t0 = Date.now();
  await page.goto(APP, { waitUntil: "load", timeout: 90000 });
  await waitText(/package versions/);
  await page.mouse.move(mouse.x, mouse.y);
  await sleep(2600); // the header counters count up from 0: let them settle before the first frame
  const trim = now();

  // 0:00 Hook
  beat("hook"); cur = beats[0]; cur.t = trim;
  await sleep(700);
  await hover(page.getByText("Sept 8, 2025. One phished maintainer", { exact: false }), 900);
  await sleep(2400);
  const g0 = await box(page.locator("[data-graph]"));
  await moveTo(g0.b.x + g0.b.width * 0.5, g0.b.y + g0.b.height * 0.35, 1200);

  // 0:08 Multi-hop answer
  await startBeat("replay");
  await click(page.getByRole("button", { name: "Replay the qix compromise", exact: true }), 700);
  await waitText(/13 of 22 services can install a malicious version/);
  await sleep(1900); // auto-scroll to the graph, chains draw in hop by hop
  await hoverNode("img-service", 0.133, 0.386, 800); // one red chain lights up
  await sleep(1200);
  await scrollToEl(page.locator("h2", { hasText: /^Exposure$/ }), 90, 800);
  await click(page.getByRole("tab", { name: /Excluded by range/ }).first(), 600);
  await sleep(200);
  await hover(page.getByText("checkout-api", { exact: true }).first(), 600);
  await sleep(1500);
  await scrollToEl(page.locator("#graph"), 70, 700);
  await dragDepth(4, 1, 300, 400);
  await waitText(/(^|\D)1 of 22 services can install/);
  await sleep(1100);
  await dragDepth(1, 4, 300, 450);
  await waitText(/13 of 22 services can install/);

  // 0:25 Crew
  await startBeat("crew");
  await scrollToY(0, 600);
  await click(page.locator("[data-testid=hero-crew]"), 700);
  await page.locator("[data-testid=crew-view]").waitFor({ timeout: 30000 });
  await sleep(700);
  await scrollToEl(page.locator("[data-testid=crew-view]"), 72, 500);
  await sleep(3000); // lanes fill at reading speed; the handoffs fan out to Investigator and Responder in parallel
  const chip = page.getByText("Read from the graph", { exact: true });
  if (await chip.count()) await hover(chip.first(), 600);
  await sleep(1000);
  const skip = page.getByRole("button", { name: /Skip to end/ });
  if (await skip.count()) await click(skip.first(), 600);
  await page.locator("[data-testid=crew-view][data-status=done]").waitFor({ timeout: 60000 });
  await sleep(400);
  await scrollToEl(page.locator("[data-testid=crew-summary]"), 90, 900);
  await hover(page.getByText("Not sent", { exact: false }).first(), 600);
  await sleep(1000);
  const share = page.getByRole("button", { name: /Share replay/ });
  if (await share.count()) await click(share.first(), 600);
  await sleep(500);

  // 0:50 Follow-ups: "and at depth 6?" first, then "who should I wake for that?"
  await startBeat("followups");
  await scrollToY(0, 500);
  await click(page.locator("[aria-label='Close the crew view']"), 450); // the session rail stays; the crew run is a turn in it
  await page.locator("[data-testid=crew-view]").waitFor({ state: "detached", timeout: 5000 }).catch(() => console.log("  (crew view still open)"));
  await ask("and at depth 6?", 200);
  await page.waitForFunction(() => /at depth 6/.test(document.querySelector("[data-testid=followup-line]")?.innerText || ""), null, { timeout: 30000 });
  await sleep(300);
  await scrollToEl(page.locator("#report"), 80, 600);
  await hover(page.locator("[data-testid=followup-line]"), 450);
  await sleep(500);
  await ask("who should I wake for that?", 450);
  await page.locator("[data-testid=wake-headline]").waitFor({ timeout: 30000 });
  await sleep(300);
  await scrollToEl(page.locator("#report"), 80, 600);
  await sleep(700);

  // 1:05 Act and remember: Depth back to 4, Generate fix, Approve, then ask "qix"
  await startBeat("fix");
  await scrollToEl(page.locator("#graph"), 70, 500);
  await dragDepth(6, 4, 250, 350);
  await waitText(/13 of 22 services can install/);
  await scrollToEl(page.locator("h2", { hasText: /^Fix$/ }), 90, 550);
  await click(page.getByRole("button", { name: /Generate fix/ }), 450);
  await waitText(/written to the graph/);
  await sleep(200);
  await scrollToEl(page.locator("h2", { hasText: /^Fix$/ }), 90, 300);
  await click(page.getByRole("button", { name: /^Approve$/ }), 500);
  await waitText(/Approved and remembered/);
  await sleep(350);
  await ask("qix", 500);
  await waitText(/Answered from graph memory/);
  await sleep(400);
  await scrollToEl(page.locator("#report"), 80, 500);
  await sleep(300);

  // 1:15 Tenants
  await startBeat("tenants");
  await switchTenant("globex");
  await ask("were we hit by Shai-Hulud?", 200);
  await waitText(/of 12 services can install a malicious version/);
  await sleep(700); // let the app's own scroll to the graph finish first
  await scrollToEl(page.locator("#report"), 80, 450);
  await waitText(/4 of 12 services can install a malicious version/);
  await sleep(1300);
  await switchTenant("acme");
  await ask("were we hit by Shai-Hulud?", 200);
  await page.locator("[data-testid=not-hit]").waitFor({ timeout: 30000 });
  await sleep(300);
  await scrollToEl(page.locator("#report"), 80, 400);
  await sleep(300);

  // 1:28 Live watch, one Replay click
  await startBeat("watch");
  await scrollToEl(page.locator("#watch"), 70, 900);
  await page.locator("[data-testid=live-watch]").waitFor({ timeout: 30000 });
  await sleep(300);
  await hover(page.locator("[data-testid=watch-headline]"), 700);
  await sleep(1400);
  await hover(page.locator("[data-testid=watch-verdicts]"), 500);
  await sleep(600);
  await click(page.locator("[data-testid=watch-replay]"), 700);
  await page.locator("[data-testid=watch-replay-result]").waitFor({ timeout: 30000 });
  await sleep(500);
  await hover(page.locator("[data-testid=watch-replay-result]"), 500);
  await sleep(800);
  const rrow = page.locator("[data-testid=watch-feed] li[data-replay=true]");
  if (await rrow.count()) await hover(rrow.first(), 600);

  // 1:40 Under the hood: an exposure answer's 5-call MCP chain, one Cypher step, then the bench card
  await startBeat("hood");
  await ask("debug@4.4.2", 400);
  await page.waitForFunction(() => +(document.querySelector("[data-testid=mcp-chain]")?.dataset.calls || 0) >= 5, null, { timeout: 30000 });
  await sleep(300);
  await scrollToEl(page.locator("[data-testid=mcp-chain]"), 120, 800);
  await hover(page.locator("[data-testid=mcp-chain]"), 450);
  await sleep(300);
  await click(page.locator("[data-testid=trace-mcp] button").first(), 450);
  await sleep(400);
  const steps = page.locator("button[aria-expanded]").filter({ hasText: /rows?/ });
  if (await steps.count()) await click(steps.first(), 500);
  await sleep(900);
  await scrollToEl(page.locator("#performance"), 70, 800);
  await page.locator("[data-testid=bench]").waitFor({ timeout: 30000 });
  await sleep(200);
  await hover(page.locator("[data-testid=bench-http]"), 600);
  await sleep(1000);
  await hover(page.locator("[data-testid=bench-scope]"), 600);

  // 1:52 Close: same screen
  await startBeat("close");
  await moveTo(W - 60, H - 60, 900);
  await hold();
  const end = now();

  const video = page.video();
  await ctx.close();
  await browser.close();
  const raw = await video.path();
  fs.writeFileSync(path.join(OUT, "beats.json"), JSON.stringify(beats.map((b) => ({ ...b, t: +(b.t - trim).toFixed(2) })), null, 1));
  const mp4 = path.join(OUT, "raw-demo.mp4");
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-ss", String(trim), "-i", raw, "-t", String(end - trim),
    "-vf", `fps=30,scale=1920:${Math.round(H * 1920 / W)}:flags=lanczos`,
    "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-an", mp4], { stdio: "inherit" });
  if (!process.env.KEEP_RAW) fs.rmSync(RAW, { recursive: true, force: true });
  console.log(`console errors / 4xx-5xx during the run: ${consoleErrors.length ? consoleErrors.join(" | ") : "none"}`);
  console.log(`wrote ${path.relative(ROOT, mp4)} (${(end - trim).toFixed(1)} s) and demo/beats.json`);
})().catch((e) => { console.error("RECORD ERROR", e); process.exit(1); });
