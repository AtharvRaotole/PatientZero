// Curated product screenshots for docs/screenshots at 1440x900, deviceScaleFactor 2.
// Needs the app (APP_URL, default http://localhost:3001) and API (API_URL, default http://localhost:8000) running.
// Run: NODE_PATH=/path/to/node_modules node scripts/screenshots.cjs   (requires puppeteer-core and Chrome)
const path = require("path");
const fs = require("fs");
const puppeteer = require("puppeteer-core");

const APP = process.env.APP_URL || "http://localhost:3001";
const API = process.env.API_URL || "http://localhost:8000";
const OUT = path.join(__dirname, "..", "docs", "screenshots");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function ask(page, q) {
  const sel = "input[aria-label='Ask PatientZero']";
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.focus(sel);
  await page.keyboard.down("Control"); await page.keyboard.press("A"); await page.keyboard.up("Control");
  await page.keyboard.press("Backspace");
  await page.type(sel, q);
  await page.keyboard.press("Escape"); // close the suggestions; Enter then runs the typed text
  await page.keyboard.press("Enter");
}
const waitText = (page, re, timeout = 30000) =>
  page.waitForFunction((src) => new RegExp(src).test(document.body.innerText), { timeout }, re.source);
async function click(page, text, exact = true) {
  const ok = await page.evaluate((t, ex) => {
    const b = [...document.querySelectorAll("button")].find((x) => (ex ? x.innerText.trim() === t : x.innerText.includes(t)) && !x.disabled);
    if (b) b.click();
    return !!b;
  }, text, exact);
  if (!ok) throw new Error(`button not found: ${text}`);
}
// Scroll so the report section (h2) or card (h3) titled `text` sits just under the sticky top bar.
async function scrollTo(page, text, offset = 88) {
  const ok = await page.evaluate((t, off) => {
    const el = [...document.querySelectorAll("h2,h3,p,span,button")].find((e) => e.innerText && e.innerText.trim() === t);
    if (!el) return false;
    const box = el.tagName === "H3" ? el.closest(".surface") || el : el.closest("section") || el;
    window.scrollTo(0, box.getBoundingClientRect().top + window.scrollY - off);
    return true;
  }, text, offset);
  if (!ok) throw new Error(`text not found: ${text}`);
  await sleep(900);
}
async function scrollToId(page, id, offset = 72) {
  await page.evaluate((i, off) => {
    const el = document.getElementById(i);
    window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - off);
  }, id, offset);
  await sleep(900);
}
async function settle(page) {
  await sleep(3200); // results auto-scroll to the graph, then exposed paths draw in hop by hop
}
const shot = async (page, name) => {
  await page.screenshot({ path: path.join(OUT, name) });
  console.log("wrote docs/screenshots/" + name);
};

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  for (const f of fs.readdirSync(OUT)) if (/^\d.*\.png$/.test(f)) fs.unlinkSync(path.join(OUT, f)); // keeps logo-variants.png
  await fetch(`${API}/api/memory/reset`, { method: "POST" });
  await fetch(`${API}/api/playbooks`, { method: "DELETE" }).catch(() => {});

  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME || "/usr/bin/google-chrome",
    args: ["--no-sandbox", "--hide-scrollbars"],
    defaultViewport: { width: 1440, height: 900, deviceScaleFactor: 2 },
  });
  const page = await browser.newPage();
  await page.goto(APP, { waitUntil: "load", timeout: 90000 });
  await sleep(3500);
  await shot(page, "01-home-fleet-graph.png");

  await ask(page, "maintainer qix was phished");
  await waitText(page, /services can install/);
  await settle(page);
  await page.mouse.move(0, 0);
  await shot(page, "02-qix-blast-radius-graph.png");

  await scrollTo(page, "Exposure");
  await shot(page, "03-qix-results-exposure.png");

  await scrollTo(page, "Insights", 72);
  await sleep(1200);
  await shot(page, "04-insight-blast-rings.png");
  await click(page, "Exposure matrix");
  await sleep(1400);
  await shot(page, "05-insight-exposure-matrix.png");

  await scrollTo(page, "Exposure");
  await click(page, "Excluded by range", false);
  await sleep(900);
  await shot(page, "06-honest-negatives.png");

  await scrollTo(page, "Who to wake");
  await shot(page, "07-who-to-wake.png");

  await scrollTo(page, "Fix");
  await click(page, "Generate fix", false);
  await waitText(page, /written to the graph/);
  await sleep(1200);
  await click(page, "Approve", true);
  await waitText(page, /Approved and remembered/);
  await sleep(800);
  await scrollTo(page, "Fix");
  await shot(page, "08-fix-and-approve-playbook.png");

  await scrollTo(page, "Trace");
  await shot(page, "09-agent-trace.png");

  // Memory: a brand-new session asks again
  const p2 = await browser.newPage();
  await p2.goto(APP, { waitUntil: "load", timeout: 90000 });
  await sleep(1500);
  await ask(p2, "qix"); // a bare recall of a stored trigger is answered from memory
  await waitText(p2, /Answered from graph memory/);
  await settle(p2);
  await scrollToId(p2, "report");
  await shot(p2, "10-memory-recall.png");

  // The same trigger again: always a fresh traversal, with graph memory alongside as "Seen before".
  await ask(p2, "maintainer qix was phished");
  await waitText(p2, /Seen before/);
  await settle(p2);
  await scrollToId(p2, "report");
  await shot(p2, "10b-seen-before.png");

  await scrollTo(p2, "From compromise to remediation", 72);
  await sleep(800);
  await shot(p2, "11-incident-timeline.png");

  await ask(p2, "who is our single point of failure?");
  await waitText(p2, /Reach against PageRank/i);
  await settle(p2);
  await scrollTo(p2, "Single points of failure", 72);
  await shot(p2, "12-single-point-of-failure.png");

  await ask(p2, "why is img-service connected to is-arrayish?");
  await waitText(p2, /reaches is-arrayish in \d+ hops/);
  await settle(p2);
  await shot(p2, "13-path-between.png");

  await scrollTo(p2, "Would you have been hit on Sept 8, 2025?", 72);
  await click(p2, "axios/axios");
  await waitText(p2, /Yes\. A fresh install could have pulled malware/, 120000);
  await sleep(6000);
  await scrollTo(p2, "Yes. A fresh install could have pulled malware.", 120);
  await p2.mouse.move(0, 0);
  await shot(p2, "14-byo-axios-exposed.png");
  await scrollTo(p2, "Would you have been hit on Sept 8, 2025?", 72);
  await click(p2, "expressjs/express@4.x (runtime only)");
  await waitText(p2, /No\. Every range excluded the malicious versions/, 120000);
  await sleep(4500);
  await scrollTo(p2, "No. Every range excluded the malicious versions.", 120);
  await shot(p2, "15-byo-express-clean.png");

  await ask(p2, "left-pad@1.3.0");
  await waitText(p2, /not in the graph/);
  await settle(p2);
  await scrollToId(p2, "report");
  await shot(p2, "16-unknown-entity.png");

  await browser.close();
  await fetch(`${API}/api/memory/reset`, { method: "POST" });
  await fetch(`${API}/api/playbooks`, { method: "DELETE" }).catch(() => {});
})().catch((e) => {
  console.error("SCREENSHOTS ERROR", e);
  process.exit(1);
});
