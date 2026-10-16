// End-to-end browser check of the main journey + screenshots (headless Chrome via puppeteer-core).
// Run: npm i puppeteer-core (anywhere) and
//   NODE_PATH=<dir>/node_modules node scripts/e2e_screenshots.cjs
// Env: APP_URL (http://localhost:3001), API_URL (http://localhost:8000), CHROME (/usr/bin/google-chrome)
const puppeteer = require("puppeteer-core");
const path = require("path");

const APP = process.env.APP_URL || "http://localhost:3001";
const API = process.env.API_URL || "http://localhost:8000";
// Test captures go to a scratch dir; the curated README set is made by scripts/screenshots.cjs.
const OUT = process.env.E2E_OUT || "/tmp/pz-e2e";
require("fs").mkdirSync(OUT, { recursive: true });
const results = [];
const errors = [];

function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  " + detail : ""}`);
}

async function waitOutcome(page, outcome, timeout = 15000) {
  await page.waitForFunction((o) => document.body.innerText.includes(o), { timeout }, outcome);
  await new Promise((r) => setTimeout(r, 1800)); // let the graph camera + animations settle
}

async function clickText(page, text, selector = "button") {
  const ok = await page.evaluate((t, sel) => {
    const b = [...document.querySelectorAll(sel)].find((x) => x.innerText.trim().startsWith(t));
    if (b) b.click();
    return !!b;
  }, text, selector);
  if (!ok) throw new Error(`button not found: ${text}`);
}

async function ask(page, q) {
  const sel = "input[aria-label='Ask PatientZero']";
  await page.focus(sel);
  await page.keyboard.down("Control"); await page.keyboard.press("A"); await page.keyboard.up("Control");
  await page.keyboard.press("Backspace");
  await page.type("input[aria-label='Ask PatientZero']", q);
  await page.keyboard.press("Escape"); // close the suggestions; Enter runs the typed text
  await page.keyboard.press("Enter");
}

async function setDepth(page, d) {
  await page.evaluate((v) => {
    const el = document.querySelector("input[aria-label='graph depth']");
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(el, String(v));
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }, d);
}

const exposedCount = (page) =>
  page.evaluate(() => {
    const m = document.body.innerText.match(/Exposed\s+(\d+)/);
    return m ? Number(m[1]) : -1;
  });

(async () => {
  await fetch(`${API}/api/memory/reset`, { method: "POST" });
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME || "/usr/bin/google-chrome",
    headless: "new",
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--window-size=1600,1000"],
    defaultViewport: { width: 1600, height: 1000, deviceScaleFactor: 1 },
  });
  const page = await browser.newPage();
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(APP, { waitUntil: "networkidle2", timeout: 60000 });
  await page.waitForFunction(() => document.body.innerText.includes("versions"), { timeout: 30000 });
  await new Promise((r) => setTimeout(r, 4000));
  await page.screenshot({ path: `${OUT}/01-home-fleet-graph.png` });
  check("home renders fleet graph + empty state", await page.evaluate(() => document.body.innerText.includes("Know your blast radius")));

  // AC4: qix
  await ask(page, "maintainer qix was phished");
  await waitOutcome(page, "services can install");
  await page.evaluate(() => window.scrollTo(0, 0));
  await new Promise((r) => setTimeout(r, 1500));
  await page.screenshot({ path: `${OUT}/02-qix-blast-radius-graph.png` });
  const n4 = await exposedCount(page);
  check("AC4 qix answered_with_path, >=5 exposed", n4 >= 5, `exposed=${n4}`);
  const traceOk = await page.evaluate(() => ["resolve_entity", "incident_scope", "exposure", "who_to_wake"].every((t) => document.body.innerText.includes(t)));
  check("AC4 trace lists resolve_entity, incident_scope, exposure, who_to_wake", traceOk);
  await page.evaluate(() => window.scrollTo(0, 700));
  await new Promise((r) => setTimeout(r, 800));
  await page.screenshot({ path: `${OUT}/03-qix-results-trace-wake.png` });

  // AC5: depth 1 vs 4
  await setDepth(page, 1);
  await page.waitForFunction(() => document.body.innerText.includes("services at depth 1"), { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 1500));
  const n1 = await exposedCount(page);
  const exposedNames = (pg) => pg.evaluate(() => [...document.querySelectorAll("button span.truncate")].map((x) => x.innerText));
  const img1 = (await exposedNames(page)).includes("img-service");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${OUT}/04-depth-1.png` });
  await setDepth(page, 4);
  await page.waitForFunction(() => document.body.innerText.includes("services at depth 4"), { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 1500));
  const n4b = await exposedCount(page);
  const img4 = (await exposedNames(page)).includes("img-service");
  check("AC5 depth 4 > depth 1, img-service only at depth >= 3", n4b > n1 && !img1 && img4, `depth1=${n1} depth4=${n4b}`);

  // P0-2 regression: re-trace 5 times at different depths with the pointer resting on the graph and a wheel
  // scroll over it. The graph must stay fitted (zoom 1), keep every node, name its labels, and hold no stale hover.
  const graphState = () => page.evaluate(() => ({ ...document.querySelector("[data-graph]").dataset }));
  const box = await page.evaluate(() => { const r = document.querySelector("[data-graph]").getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  const base = await graphState();
  const retrace = [];
  for (const d of [1, 3, 6, 2, 5]) {
    await page.mouse.move(box.x + box.w * 0.5, box.y + box.h * 0.25);
    await page.mouse.wheel({ deltaY: 500 });
    await setDepth(page, d);
    await page.waitForFunction((v) => document.body.innerText.includes(`services at depth ${v}`), { timeout: 15000 }, d);
    await new Promise((r) => setTimeout(r, 1800));
    await page.mouse.move(5, 5);
    await new Promise((r) => setTimeout(r, 300));
    const st = await graphState();
    const exposed = await exposedCount(page);
    retrace.push({ d, exposed, ...st });
  }
  const stable = retrace.every((s) => s.nodes === base.nodes && s.zoom === "1.00" && !s.hover && Number(s.labels) >= Math.max(1, s.exposed) + 1);
  check("P0-2 five re-traces keep the graph fitted and labelled", stable, retrace.map((s) => `d${s.d}:${s.labels}lbl/${s.nodes}n/z${s.zoom}${s.hover ? "/hover" : ""}`).join(" "));
  // Dragging the slider fires one change per step; only the last depth may land, whatever order answers arrive in.
  for (const v of [6, 5, 4, 3, 2, 1, 2, 3, 4]) { await setDepth(page, v); await new Promise((r) => setTimeout(r, 35)); }
  await new Promise((r) => setTimeout(r, 2500));
  const depthState = () => page.evaluate(() => ({
    slider: document.querySelector("input[aria-label='graph depth']").value,
    label: document.querySelector("input[aria-label='graph depth']")?.nextElementSibling?.textContent ?? null,
    answer: document.querySelector("[data-testid=answer]")?.getAttribute("data-depth") ?? null,
  }));
  const settled = await depthState();
  check("P0-2 slider drag lands on the last depth", settled.slider === "4" && settled.answer === "4" && settled.label === "4", JSON.stringify(settled));

  // Out-of-order answers: hold the depth-2 answer for 3 s so it arrives after the depth-5 one. The answer must stay at 5.
  let heldAsk = false;
  const holdDepth2 = (req) => {
    let body = null;
    try { body = req.method() === "POST" && /\/api\/ask/.test(req.url()) ? JSON.parse(req.postData() || "{}") : null; } catch { body = null; }
    if (body && body.depth === 2 && !heldAsk) { heldAsk = true; setTimeout(() => req.continue().catch(() => {}), 3000); }
    else req.continue().catch(() => {});
  };
  await page.setRequestInterception(true);
  page.on("request", holdDepth2);
  await setDepth(page, 2);
  await new Promise((r) => setTimeout(r, 500)); // the depth-2 request is out and held
  await setDepth(page, 5);
  await page.waitForFunction(() => document.querySelector("[data-testid=answer]")?.getAttribute("data-depth") === "5", { timeout: 15000 }).catch(() => {});
  await new Promise((r) => setTimeout(r, 3500)); // the held depth-2 answer lands now, last
  const late = await depthState();
  page.off("request", holdDepth2);
  await page.setRequestInterception(false);
  check("depth: an earlier answer arriving last never overrides the slider", heldAsk && late.slider === "5" && late.answer === "5" && late.label === "5", JSON.stringify({ heldAsk, ...late }));
  await setDepth(page, 4);
  await page.waitForFunction(() => document.querySelector("[data-testid=answer]")?.getAttribute("data-depth") === "4", { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 1200));

  // AC9: generate fix
  await clickText(page, "Generate fix");
  await page.waitForFunction(() => document.body.innerText.includes("written to the graph"), { timeout: 20000 });
  await new Promise((r) => setTimeout(r, 1200));
  await page.evaluate(() => {
    const el = [...document.querySelectorAll("h3")].find((h) => h.innerText === "Remediation");
    el && el.scrollIntoView({ block: "start" });
  });
  await new Promise((r) => setTimeout(r, 600));
  await page.screenshot({ path: `${OUT}/05-generate-fix.png` });
  const fixOk = await page.evaluate(() => /"debug(@[^"]*)?": "4\.4\.3"/.test(document.body.innerText));
  check("AC9 overrides JSON with clean versions + incident written", fixOk);

  // AC6: debug@4.4.2 honest negative (fresh session to avoid memory: use the Run fresh button if recalled)
  await page.evaluate(() => window.scrollTo(0, 0));
  await ask(page, "debug@4.4.2");
  await page.waitForFunction(() => /services can install|Answered from graph memory/.test(document.body.innerText), { timeout: 15000 });
  if (await page.evaluate(() => document.body.innerText.includes("Run a fresh traversal"))) {
    await clickText(page, "Run a fresh traversal");
    await waitOutcome(page, "Excluded by range");
  }
  await clickText(page, "Excluded by range");
  await new Promise((r) => setTimeout(r, 800));
  const ac6 = await page.evaluate(() => document.body.innerText.includes("debug 2.6.9 (exact) does not satisfy 4.4.2"));
  check("AC6 checkout-api safe with reason", ac6);
  await page.evaluate(() => window.scrollTo(0, 650));
  await new Promise((r) => setTimeout(r, 600));
  await page.screenshot({ path: `${OUT}/06-debug-honest-negatives.png` });

  // AC10 (memory, new session)
  const p2 = await browser.newPage();
  globalThis.__page = p2;
  p2.on("pageerror", (e) => errors.push(String(e)));
  await p2.goto(APP, { waitUntil: "networkidle2" });
  await p2.waitForFunction(() => document.body.innerText.includes("versions"), { timeout: 30000 });
  // Plain typing then Enter, suggestions open: the bar must ask exactly "qix", not swap in an example.
  await p2.focus("input[aria-label='Ask PatientZero']");
  await p2.type("input[aria-label='Ask PatientZero']", "qix", { delay: 40 });
  await new Promise((r) => setTimeout(r, 300));
  await p2.keyboard.press("Enter");
  await waitOutcome(p2, "Answered from graph memory");
  const kept = await p2.evaluate(() => ({ v: document.querySelector("input[aria-label='Ask PatientZero']").value, i: document.querySelector("[data-testid='interpreted']")?.innerText ?? "" }));
  check("bare qix keeps the typed text, shows the interpretation", kept.v === "qix" && /maintainer qix was phished/.test(kept.i), JSON.stringify(kept));
  const memLine = await p2.evaluate(() => document.querySelector("[data-testid='memory-line']")?.innerText ?? "");
  check("AC10 new session: qix answered from graph memory, with incident id and date", /INC-\S+/.test(memLine) && /\d{4}|\d:\d\d/.test(memLine), memLine.replace(/\s+/g, " "));
  await p2.screenshot({ path: `${OUT}/07-memory-recall.png`, fullPage: true });

  // AC11 SPOF
  await ask(p2, "who is our single point of failure?");
  await waitOutcome(p2, "Single points of failure");
  check("AC11 SPOF ranking shown", await p2.evaluate(() => document.body.innerText.includes("qix")));
  await p2.evaluate(() => window.scrollTo(0, 650));
  await new Promise((r) => setTimeout(r, 900));
  await p2.screenshot({ path: `${OUT}/08-single-point-of-failure.png` });

  // AC12 path
  await p2.evaluate(() => window.scrollTo(0, 0));
  await ask(p2, "why is img-service connected to is-arrayish?");
  await waitOutcome(p2, "Shortest path with algo.SPpaths");
  await p2.screenshot({ path: `${OUT}/09-path-between.png` });
  check("AC12 path_between drawn", await p2.evaluate(() => document.body.innerText.includes("simple-swizzle")));

  // Who to wake: a responder card (team, Slack, pager, on-call, ownership graph), scrolled into view
  await p2.evaluate(() => window.scrollTo(0, 0));
  await ask(p2, "who to wake for auth-service");
  await p2.waitForSelector("[data-testid='responder']", { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 1400));
  const wk = await p2.evaluate(() => {
    const el = document.querySelector("[data-testid='responder']");
    const h2 = [...document.querySelectorAll("#report h2")].map((x) => x.innerText).join(" | ");
    return { text: el.innerText, top: el.getBoundingClientRect().top, vh: innerHeight, svg: !!el.querySelector("svg[aria-label*='owns']"), h2 };
  });
  check("who to wake: responder card with team, Slack, pager, on-call and graph, in view",
    /Identity & Access/.test(wk.text) && /#team-identity/.test(wk.text) && /acme-identity/.test(wk.text) && /Amara Okafor/.test(wk.text) && wk.svg && wk.top < wk.vh && /Page Amara Okafor for auth-service/.test(wk.h2),
    `top=${Math.round(wk.top)} h2=${wk.h2}`);
  await p2.screenshot({ path: `${OUT}/11-who-to-wake.png` });

  // Campaign not hit: a confident Not hit state with stats, the 22 clean services and the trace
  await p2.evaluate(() => window.scrollTo(0, 0));
  await ask(p2, "Shai-Hulud");
  await p2.waitForSelector("[data-testid='not-hit']", { timeout: 20000 });
  await new Promise((r) => setTimeout(r, 1400));
  const nh = await p2.evaluate(() => ({
    clean: document.querySelectorAll("[data-testid='clean-grid'] > *").length,
    stats: document.querySelector("[data-testid='not-hit-stats']")?.innerText.replace(/\s+/g, " ") ?? "",
    top: document.querySelector("[data-testid='not-hit']").getBoundingClientRect().top, vh: innerHeight,
    trace: document.body.innerText.includes("incident_scope"),
    labels: Number(document.querySelector("[data-graph]")?.getAttribute("data-labels") ?? 0),
  }));
  check("Shai-Hulud: Not hit with 203 packages, 589 versions, 22 clean services and the trace, in view",
    nh.clean === 22 && /203/.test(nh.stats) && /589/.test(nh.stats) && nh.trace && nh.top >= 0 && nh.top < nh.vh, JSON.stringify(nh));
  await p2.screenshot({ path: `${OUT}/12-shai-hulud-not-hit.png` });

  // AC7 unknown
  await ask(p2, "left-pad@1.3.0");
  await waitOutcome(p2, "Not in the graph.");
  await p2.evaluate(() => window.scrollTo(0, 650));
  await new Promise((r) => setTimeout(r, 600));
  await p2.screenshot({ path: `${OUT}/10-unknown-entity.png` });
  check("AC7 unknown_entity", true);

  // AC8 not_in_graph
  await p2.evaluate(() => window.scrollTo(0, 0));
  await ask(p2, "ansi-regex@6.2.1");
  // Previous screen (unknown_entity) shows neither sentence, so either one means the new answer has rendered.
  await p2.waitForFunction(() => /No fleet path admits this version|Answered from graph memory/.test(document.body.innerText), { timeout: 20000 });
  if (await p2.evaluate(() => document.body.innerText.includes("Run a fresh traversal"))) {
    await clickText(p2, "Run a fresh traversal"); // the qix incident in memory covers ansi-regex; re-traverse
  }
  await waitOutcome(p2, "No fleet path admits this version at depth 4");
  check("AC8 not_in_graph sentence", true);

  check("no browser console errors", errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
  await fetch(`${API}/api/memory/reset`, { method: "POST" });
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} browser checks passed`);
  process.exit(failed.length ? 1 : 0);
})().catch(async (e) => {
  console.error("E2E ERROR", e);
  if (globalThis.__page) await globalThis.__page.screenshot({ path: "/tmp/e2e-fail.png" }).catch(() => {});
  process.exit(2);
});
