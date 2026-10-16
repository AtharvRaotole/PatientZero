// Runs every input from the README "Try it yourself" tables in a real browser and screenshots what renders.
// Run: NODE_PATH=/workspace/tools/node_modules APP_URL=<app> OUT=<dir> node scripts/tryit_screenshots.cjs
// Each query must render a meaningful report headline (and the expected words); each BYO input its verdict.
// Read-only against memory: no Generate fix, no approve, no reset.
const puppeteer = require("puppeteer-core");
const fs = require("fs");

const APP = process.env.APP_URL || "http://localhost:3001";
const OUT = process.env.OUT || "/tmp/pz-tryit";
fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const QUERIES = [
  ["qix", /Answered from graph memory|13 of 22 services/],
  ["debug@4.4.2", /10 of 22 services can install/],
  ["chalk@5.6.1", /1 of 22 services can install/],
  ["GHSA-4x49-vf9v-38px", /10 of 22 services can install/],
  ["Shai-Hulud", /Not hit\./, "[data-testid='clean-grid']"],
  ["who to wake for auth-service", /Page Amara Okafor for auth-service/, "[data-testid='responder']"],
  ["path from img-service to simple-swizzle", /4 hops/, null, "Shortest path"],
  ["single points of failure", /npm accounts/, null, "Single points of failure"],
];
const BYO = [
  ["https://github.com/axios/axios", true, /Runtime exposed/],
  ["https://github.com/sindresorhus/ora", true, /Runtime exposed/],
  ["https://github.com/expressjs/express/tree/4.x", false, /Runtime clean/],
  ["https://github.com/vercel/ms", true, /Runtime clean/, /Dev tooling/],
  ['{"dependencies":{"chalk":"^5.3.0"}}', true, /Runtime exposed/],
  ['{"dependencies":{"chalk":"5.3.0"}}', true, /Runtime clean/],
];

const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  " + detail : ""}`); };
const slug = (s) => s.replace(/^https:\/\/github.com\//, "").replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 48);

(async () => {
  const browser = await puppeteer.launch({ executablePath: process.env.CHROME || "/usr/bin/google-chrome", headless: "new",
    args: ["--no-sandbox", "--disable-dev-shm-usage"], defaultViewport: { width: 1440, height: 900 } });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(APP, { waitUntil: "networkidle2", timeout: 90000 });
  await page.waitForFunction(() => document.body.innerText.includes("package versions"), { timeout: 60000 });
  await sleep(2500);

  let i = 0;
  for (const [q, re, sel, extra] of QUERIES) {
    i++;
    await page.evaluate(() => window.scrollTo(0, 0));
    const before = await page.evaluate(() => document.querySelector("#report")?.innerText ?? "");
    const inp = "input[aria-label='Ask PatientZero']";
    await page.focus(inp);
    await page.keyboard.down("Control"); await page.keyboard.press("A"); await page.keyboard.up("Control");
    await page.keyboard.press("Backspace");
    await page.type(inp, q, { delay: 20 });
    const typed = await page.$eval(inp, (e) => e.value);
    if (typed !== q) throw new Error(`input holds ${JSON.stringify(typed)}, expected ${JSON.stringify(q)}`);
    await page.keyboard.press("Escape");
    const t0 = Date.now();
    await page.keyboard.press("Enter");
    // a loading state appears (button spinner, graph pill or dimmed report), then a new answer lands
    const sawLoading = await page.waitForFunction(() => /Traversing the graph/.test(document.body.innerText), { timeout: 3000, polling: 20 }).then(() => true, () => false);
    await page.waitForFunction((b) => {
      const r = document.querySelector("#report");
      return r && r.innerText !== b && !/Traversing the graph/.test(document.body.innerText) && r.querySelector("h2");
    }, { timeout: 30000 }, before);
    if (sel) await page.waitForSelector(sel, { timeout: 10000 });
    await sleep(1800);
    const st = await page.evaluate(() => {
      const h2 = document.querySelector("#report h2");
      const rep = document.querySelector("#report").innerText;
      return { h2: h2?.innerText.replace(/\s+/g, " ") ?? "", rep, y: window.scrollY, h2top: h2?.getBoundingClientRect().top ?? -1, vh: innerHeight };
    });
    const visible = st.h2top > -400 && st.h2top < st.vh; // the answer headline (or the graph above it) is on screen
    const ok = re.test(st.rep) && (!extra || st.rep.includes(extra)) && st.h2.length > 3;
    const f = `${OUT}/q${String(i).padStart(2, "0")}-${slug(q)}.png`;
    await page.screenshot({ path: f });
    await page.evaluate(() => document.getElementById("graph").scrollIntoView());
    await sleep(900);
    await page.screenshot({ path: f.replace(".png", "-graph.png") });
    check(`query ${JSON.stringify(q)}`, ok && visible, `"${st.h2}" (${Date.now() - t0} ms, scrolled to ${st.y}, loading ${sawLoading ? "seen" : "too fast to see"}) -> ${f}`);
  }

  i = 0;
  for (const [input, dev, re, extra] of BYO) {
    i++;
    await page.evaluate(() => document.getElementById("byo").scrollIntoView());
    await sleep(400);
    const isJson = input.startsWith("{");
    await page.evaluate((tab) => {
      const b = [...document.querySelectorAll("#byo [role='tab'], #byo button")].find((x) => x.innerText.trim() === tab);
      b?.click();
    }, isJson ? "package.json" : "GitHub URL");
    await sleep(200);
    const field = isJson ? "#byo textarea[aria-label='package.json']" : "#byo input[aria-label='GitHub URL']";
    await page.click(field, { clickCount: 3 });
    await page.keyboard.down("Control"); await page.keyboard.press("A"); await page.keyboard.up("Control");
    await page.keyboard.press("Backspace");
    await page.type(field, input, { delay: 5 });
    await page.evaluate((want) => {
      const cb = document.querySelector("#byo input[type='checkbox']");
      if (cb && cb.checked !== want) cb.click();
    }, dev);
    const before = await page.evaluate(() => document.querySelector("#byo").innerText);
    await page.evaluate(() => [...document.querySelectorAll("#byo button")].find((b) => b.innerText.trim() === "Scan")?.click());
    await page.waitForFunction((b) => { const t = document.querySelector("#byo").innerText; return t !== b && /Runtime (exposed|clean)/.test(t) && !/Fetching package/.test(t); }, { timeout: 120000 }, before);
    await sleep(1500);
    const txt = await page.evaluate(() => document.querySelector("#byo").innerText);
    const head = (txt.match(/Runtime (exposed|clean)[^\n]*/) || [""])[0];
    const ok = re.test(txt) && (!extra || extra.test(txt));
    const f = `${OUT}/b${String(i).padStart(2, "0")}-${slug(input)}.png`;
    await page.evaluate(() => { const h = [...document.querySelectorAll("#byo p, #byo h3")].find((x) => /^Runtime (exposed|clean)/.test(x.innerText)); (h || document.getElementById("byo")).scrollIntoView({ block: "center" }); });
    await sleep(500);
    await page.screenshot({ path: f });
    check(`BYO ${input}${dev ? "" : " (runtime only)"}`, ok, `"${head}" -> ${f}`);
    await sleep(6500); // stay under the scan rate limit
  }
  check("no page errors", errors.length === 0, errors.slice(0, 2).join(" | "));
  await browser.close();
  const failed = results.filter((x) => !x).length;
  console.log(`\n${results.length - failed}/${results.length} try-it checks passed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error("TRYIT ERROR", e); process.exit(2); });
