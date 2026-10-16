// End-to-end checks for tenants, the response crew, session follow-ups, isolation, the live watch, MCP and the bench.
// Run: NODE_PATH=<dir>/node_modules node scripts/e2e_crew_tenants.cjs
// Env: APP_URL (http://localhost:3001), API_URL (http://localhost:8000), CHROME (/usr/bin/google-chrome), E2E_OUT
// Deterministic inputs only (no LLM calls). Resets acme and globex memory before and after (local only).
const puppeteer = require("puppeteer-core");

const APP = process.env.APP_URL || "http://localhost:3001";
const API = process.env.API_URL || "http://localhost:8000";
const OUT = process.env.E2E_OUT || "/tmp/pz-e2e-crew";
require("fs").mkdirSync(OUT, { recursive: true });
const results = [];
const errors = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  " + detail : ""}`);
}
async function reset() {
  for (const t of ["acme", "globex"]) await fetch(`${API}/api/admin/reset?tenant=${t}`, { method: "POST" }).catch(() => {});
}
async function ask(page, q) {
  const sel = "input[aria-label='Ask PatientZero']";
  await page.focus(sel);
  await page.keyboard.down("Control"); await page.keyboard.press("A"); await page.keyboard.up("Control");
  await page.keyboard.press("Backspace");
  await page.type(sel, q);
  await page.keyboard.press("Escape");
  await page.keyboard.press("Enter");
}
const text = (page, sel) => page.$eval(sel, (e) => e.innerText).catch(() => "");
const waitText = (page, re, timeout = 20000) =>
  page.waitForFunction((s) => new RegExp(s).test(document.body.innerText), { timeout }, re.source);

(async () => {
  await reset();
  const browser = await puppeteer.launch({ executablePath: process.env.CHROME || "/usr/bin/google-chrome", args: ["--no-sandbox"], defaultViewport: { width: 1440, height: 900 } });
  const page = await browser.newPage();
  globalThis.__page = page;
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (r) => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
  await page.goto(APP, { waitUntil: "networkidle2" });
  await page.evaluate(() => localStorage.clear());
  await page.goto(APP, { waitUntil: "networkidle2" });
  await page.waitForSelector("[data-testid=tenant-switcher]", { timeout: 15000 });

  // 1. Tenant switch: URL, header graph, persistence across reload, Globex Shai-Hulud 4 of 12.
  check("header starts on fleet:acme", (await text(page, "[data-testid=graph-name]")) === "fleet:acme");
  await page.click("[data-testid=tenant-switcher] button");
  await page.waitForSelector("[data-tenant-option=globex]");
  await page.click("[data-tenant-option=globex]");
  await page.waitForFunction(() => document.querySelector("[data-testid=graph-name]")?.textContent === "fleet:globex", { timeout: 10000 });
  const url1 = new URL(page.url());
  check("tenant switch: ?tenant=globex and header fleet:globex", url1.searchParams.get("tenant") === "globex", page.url());
  await page.reload({ waitUntil: "networkidle2" });
  await page.waitForFunction(() => document.querySelector("[data-testid=graph-name]")?.textContent === "fleet:globex", { timeout: 10000 });
  check("tenant survives a reload", (await page.$eval("[data-testid=tenant-switcher]", (e) => e.dataset.tenant)) === "globex");
  await ask(page, "Shai-Hulud");
  await waitText(page, /4 of 12 services can install a malicious version/);
  await sleep(1800);
  await page.screenshot({ path: `${OUT}/01-globex-shai-hulud.png` });
  check("Globex Shai-Hulud: 4 of 12 exposed", true);

  // Isolation part 1: remember the Globex session id, then go back to Acme.
  const globexSession = await page.evaluate(() => localStorage.getItem("pz-session-globex"));
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.click("[data-testid=tenant-switcher] button");
  await page.click("[data-tenant-option=acme]");
  await page.waitForFunction(() => document.querySelector("[data-testid=graph-name]")?.textContent === "fleet:acme", { timeout: 10000 });
  check("switching back to Acme gives a clean URL", !new URL(page.url()).searchParams.has("tenant"), page.url());
  check("Acme Shai-Hulud answer is gone after the switch", !(await page.evaluate(() => document.body.innerText.includes("4 of 12 services"))));

  // 2. Follow-ups, end to end.
  await ask(page, "debug@4.4.2");
  await waitText(page, /services can install a malicious version/);
  await sleep(1200);
  await ask(page, "who should I wake for that?");
  await page.waitForFunction(() => /Resolved\s+that\s+to\s+debug@4\.4\.2\s+from turn 1/.test(document.querySelector("[data-testid=followup-line]")?.innerText || ""), { timeout: 20000 });
  await sleep(1500);
  const wakeTop = await page.evaluate(() => document.getElementById("who-to-wake")?.getBoundingClientRect().top ?? 9999);
  check("follow-up: 'who should I wake for that?' resolves to debug@4.4.2 from turn 1", true, (await text(page, "[data-testid=followup-line]")).replace(/\s+/g, " "));
  check("follow-up: who-to-wake section scrolled into view", wakeTop > -200 && wakeTop < 700, `top=${Math.round(wakeTop)}`);
  await (await page.$("[data-testid=followup-line]")).evaluate((e) => e.scrollIntoView({ block: "center" }));
  await sleep(400);
  await page.screenshot({ path: `${OUT}/02-followup-resolution.png` });
  await ask(page, "and at depth 6?");
  await page.waitForFunction(() => /at depth 6/.test(document.querySelector("[data-testid=followup-line]")?.innerText || ""), { timeout: 20000 });
  const slider = await page.$eval("input[aria-label='graph depth']", (e) => e.value);
  check("follow-up: 'and at depth 6?' reruns at depth 6 and moves the slider", slider === "6", `slider=${slider}`);
  // The rail refetches the session after each answer; on a remote deploy that GET lands a moment after the answer.
  await page.waitForFunction(() => /3 turns/.test(document.querySelector("[data-testid=session-rail]")?.innerText || ""), { timeout: 10000 }).catch(() => {});
  const rail = await text(page, "[data-testid=session-rail]");
  check("session rail lists 3 turns with the resolved follow-ups", /3 turns/.test(rail) && /from turn 1/.test(rail) && /from turn 2/.test(rail), rail.replace(/\n/g, " | ").slice(0, 160));

  // 3. Isolation: Acme memory and session never appear in Globex.
  const acmeSession = await page.evaluate(() => localStorage.getItem("pz-session-acme"));
  const cross = (await fetch(`${API}/api/sessions/${acmeSession}?tenant=globex`)).status; // from node, so the expected 404 is not a page error
  check("isolation: the Acme session is a 404 in Globex", cross === 404, `status=${cross}`);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.click("[data-testid=tenant-switcher] button");
  await page.click("[data-tenant-option=globex]");
  await page.waitForFunction(() => document.querySelector("[data-testid=graph-name]")?.textContent === "fleet:globex", { timeout: 10000 });
  await sleep(1200);
  const gRail = await text(page, "[data-testid=session-rail]");
  check("isolation: Globex rail shows only its own turn", !/debug@4\.4\.2|who should I wake/.test(gRail) && /Shai-Hulud/.test(gRail), gRail.replace(/\n/g, " | ").slice(0, 120));
  check("isolation: separate session ids per tenant", globexSession && acmeSession && globexSession !== acmeSession);
  await ask(page, "debug@4.4.2");
  await waitText(page, /services can install a malicious version/);
  await sleep(800);
  const fromMem = await page.evaluate(() => document.body.innerText.includes("Answered from graph memory"));
  check("isolation: Acme incident memory is not recalled in Globex", !fromMem);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.click("[data-testid=tenant-switcher] button");
  await page.click("[data-tenant-option=acme]");
  await page.waitForFunction(() => document.querySelector("[data-testid=graph-name]")?.textContent === "fleet:acme", { timeout: 10000 });

  // 4. Crew stream: 4 lanes done, handoffs drawn, Investigator and Responder overlapped, summary, shareable replay.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.click("[data-testid=hero-crew]");
  await page.waitForSelector("[data-testid=crew-view]");
  let overlap = false;
  for (let i = 0; i < 200 && !overlap; i++) {
    overlap = await page.evaluate(() => ["investigator", "responder"].every((a) => document.querySelector(`[data-testid=crew-lane-${a}]`)?.dataset.status === "running"));
    if (!overlap) await sleep(100);
  }
  check("crew: Investigator and Responder run at the same time", overlap);
  await page.screenshot({ path: `${OUT}/03-crew-midrun.png` });
  await page.waitForSelector("[data-testid=crew-view][data-status=done]", { timeout: 60000 });
  await sleep(900);
  const lanes = await page.$$eval("[data-testid^=crew-lane-]", (els) => els.map((e) => `${e.dataset.testid.replace("crew-lane-", "")}=${e.dataset.status}`));
  check("crew: all 4 agents done", lanes.length === 4 && lanes.every((l) => l.endsWith("=done")), lanes.join(","));
  const edges = await page.$$eval("[data-testid=crew-edge]", (els) => els.map((e) => `${e.dataset.from}>${e.dataset.to}`).sort());
  check("crew: 4 handoffs drawn", edges.join(",") === "investigator>fixer,responder>fixer,triage>investigator,triage>responder", edges.join(","));
  const summary = await text(page, "[data-testid=crew-summary]");
  check("crew: summary with exposed count, who to page, drafts not sent, patch plan, playbook, memory delta",
    /services exposed/.test(summary) && /Who to page/.test(summary) && /Not sent/.test(summary) && /Patch plan/.test(summary) && /Playbook/.test(summary) && /Memory delta/.test(summary));
  await (await page.$("[data-testid=crew-view]")).screenshot({ path: `${OUT}/04-crew-finished.png` });
  const runId = new URL(page.url()).searchParams.get("run");
  check("crew: shareable ?run= URL", /^CREW-/.test(runId || ""), runId);
  const p2 = await browser.newPage();
  p2.on("pageerror", (e) => errors.push(String(e)));
  await p2.goto(`${APP}/?run=${runId}`, { waitUntil: "networkidle2" });
  await p2.waitForSelector("[data-testid=crew-view]", { timeout: 15000 });
  await p2.evaluate(() => [...document.querySelectorAll("button")].find((b) => b.innerText.includes("Skip to end"))?.click());
  await p2.waitForSelector("[data-testid=crew-view][data-status=done]", { timeout: 30000 });
  const rEdges = (await p2.$$("[data-testid=crew-edge]")).length;
  check("crew: replay from the URL rebuilds all 4 lanes and handoffs", rEdges === 4 && /replayed from the graph/.test(await text(p2, "[data-testid=crew-view]")), `edges=${rEdges}`);
  await p2.close();
  await page.click("[aria-label='Close the crew view']");

  // 5. Live watch, MCP trace and bench.
  await page.$eval("#watch", (e) => e.scrollIntoView());
  await page.waitForSelector("[data-testid=live-watch]", { timeout: 15000 });
  await page.waitForFunction(() => document.querySelector("[data-testid=watch-live]")?.dataset.state === "open", { timeout: 15000 }).catch(() => {});
  const verdicts = await page.$$eval("[data-testid=watch-verdicts] [data-tenant]", (els) => els.map((e) => e.dataset.tenant));
  check("watch: a verdict for every tenant and an open stream", verdicts.includes("acme") && verdicts.includes("globex") && (await page.$eval("[data-testid=watch-live]", (e) => e.dataset.state)) === "open", verdicts.join(","));
  await page.click("[data-testid=watch-replay]");
  await page.waitForSelector("[data-testid=watch-replay-result]", { timeout: 20000 });
  const rep = await text(page, "[data-testid=watch-replay-result]");
  check("watch: replay of debug@4.4.2 is labelled and hits both tenants", /REPLAY/i.test(rep) && /Acme[\s\S]*of 22 exposed/.test(rep) && /Globex[\s\S]*of 12 exposed/.test(rep), rep.replace(/\n/g, " ").slice(0, 120));
  await page.evaluate(() => window.scrollTo(0, 0));
  await ask(page, "who to wake for auth-service");
  await page.waitForSelector("[data-testid=trace-mcp]", { timeout: 20000 });
  const mcp = await page.$eval("[data-testid=trace-mcp]", (e) => ({ via: e.dataset.via, t: e.innerText }));
  check("trace: first step is the MCP tools/call", mcp.via === "mcp" && /via MCP/.test(mcp.t) && /investigate/.test(mcp.t) && /tools listed/.test(mcp.t), mcp.t.replace(/\n/g, " ").slice(0, 100));
  await page.$eval("#performance", (e) => e.scrollIntoView());
  await page.waitForSelector("[data-testid=bench]", { timeout: 15000 });
  const bench = await text(page, "[data-testid=bench]");
  check("bench: throughput, p50/p95/p99, errors and which run it was", /queries per second/.test(bench) && /p95/.test(bench) && /p99/.test(bench) && /error/.test(bench) && /stored result/.test(bench));

  check("no browser console errors", errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
  await reset();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} crew/tenant checks passed`);
  process.exit(failed.length ? 1 : 0);
})().catch(async (e) => {
  console.error("E2E ERROR", e);
  if (globalThis.__page) await globalThis.__page.screenshot({ path: "/tmp/e2e-crew-fail.png" }).catch(() => {});
  await reset();
  process.exit(2);
});
