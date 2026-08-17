/**
 * Visual audit: measures what is clipped, overflowing, overlapping or below the
 * fold at real viewport sizes. Run: node audit-ui.mjs
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const WEB = process.env.WEB_URL || "http://127.0.0.1:5173";
const SIZES = [
  [1920, 1080, "1920 desktop"],
  [1600, 950, "1600 laptop"],
  [1440, 900, "1440 macbook"],
  [1366, 768, "1366 projector"],
  [1280, 720, "1280 small"],
  [1024, 768, "1024 tablet"],
];

const findings = [];
const add = (size, sev, what) => {
  findings.push({ size, sev, what });
  console.log(`  [${sev}] ${size}: ${what}`);
};

const probe = async (page) =>
  page.evaluate(() => {
    const out = { clipped: [], overflowX: [], overlaps: [], offscreen: [], tiny: [] };
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;

    // Containers whose content is taller/wider than the box
    for (const el of document.querySelectorAll("*")) {
      const cs = getComputedStyle(el);
      if (cs.display === "none" || cs.visibility === "hidden") continue;
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) continue;
      const sel =
        el.className && typeof el.className === "string" && el.className.trim()
          ? `.${el.className.trim().split(/\s+/).slice(0, 2).join(".")}`
          : el.tagName.toLowerCase();

      const overY = el.scrollHeight - el.clientHeight;
      const overX = el.scrollWidth - el.clientWidth;
      const scrollsY = /auto|scroll/.test(cs.overflowY);
      const scrollsX = /auto|scroll/.test(cs.overflowX);

      if (overY > 4 && !scrollsY && cs.overflow !== "visible")
        out.clipped.push({ sel, hidden: overY, h: Math.round(r.height) });
      if (overX > 2 && !scrollsX)
        out.overflowX.push({ sel, over: overX });
      // Anything sticking out of the viewport horizontally. A panel that is
      // deliberately parked offscreen by a transform is a closed drawer, not a bug.
      const parked = el.closest(".inspector:not(.open)") || cs.transform.includes("matrix");
      if ((r.right > vw + 1 || r.left < -1) && !parked)
        out.offscreen.push({ sel, left: Math.round(r.left), right: Math.round(r.right) });
    }

    // Interactive controls pushed below the fold inside a scroller
    for (const el of document.querySelectorAll("button, input, select, a")) {
      const r = el.getBoundingClientRect();
      if (r.height < 2) continue;
      if (r.top > vh - 4 || r.bottom < 4)
        out.offscreen.push({
          sel: (el.innerText || el.getAttribute("aria-label") || el.tagName).slice(0, 42).replace(/\n/g, " "),
          kind: "control below fold",
          top: Math.round(r.top),
        });
      if (r.height < 22 && el.tagName === "BUTTON")
        out.tiny.push({ sel: (el.innerText || "").slice(0, 30), h: Math.round(r.height) });
    }

    // Overlapping siblings that should not overlap
    const boxes = [...document.querySelectorAll(".act, .gauge, .overlay-item, .panel, .route-item, .tl-lane")]
      .map((el) => ({ el, r: el.getBoundingClientRect() }))
      .filter((b) => b.r.width > 4 && b.r.height > 4);
    for (let i = 0; i < boxes.length; i++)
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i], b = boxes[j];
        if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
        const ox = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left);
        const oy = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
        if (ox > 3 && oy > 3)
          out.overlaps.push({
            a: a.el.className.split(/\s+/)[0],
            b: b.el.className.split(/\s+/)[0],
            area: Math.round(ox * oy),
          });
      }

    const rail = document.querySelector(".rail");
    const insp = document.querySelector(".inspector");
    return {
      ...out,
      railHidden: rail ? rail.scrollHeight - rail.clientHeight : null,
      inspectorHidden: insp ? insp.scrollHeight - insp.clientHeight : null,
      docOverflowX: document.documentElement.scrollWidth - vw,
    };
  });

const run = async () => {
  mkdirSync("shots/audit", { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
  await page.goto(WEB, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".nowbar", { timeout: 20000 });
  const start = page.locator(".nowbar .demo-start");
  if (await start.count()) {
    await start.click();
    await page.waitForSelector(".tl-lane", { timeout: 90000 });
  } else {
    await page.waitForSelector(".tl-lane", { timeout: 20000 });
  }
  await page.getByRole("button", { name: "Lab" }).click();
  await page.waitForSelector(".lab", { timeout: 8000 });
  await page.waitForSelector(".run-id", { timeout: 20000 });

  console.log(`\nVISUAL AUDIT @ ${WEB}\n${"=".repeat(64)}`);
  for (const [w, h, name] of SIZES) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(600);
    const r = await probe(page);
    await page.screenshot({ path: `shots/audit/${w}x${h}.png` });

    if (r.docOverflowX > 1) add(name, "OVERFLOW", `page scrolls horizontally by ${r.docOverflowX}px`);
    for (const c of r.clipped.slice(0, 6))
      add(name, "CLIPPED", `${c.sel} hides ${c.hidden}px of content with no scrollbar`);
    for (const c of r.overflowX.slice(0, 6))
      add(name, "OVERFLOW", `${c.sel} content is ${c.over}px wider than its box`);
    for (const c of r.offscreen.slice(0, 8))
      add(name, "HIDDEN", `${c.kind || "element"} "${c.sel}" ${c.top != null ? `at y=${c.top}` : `x ${c.left}..${c.right}`}`);
    for (const c of r.overlaps.slice(0, 6))
      add(name, "OVERLAP", `${c.a} overlaps ${c.b} (${c.area}px²)`);
    for (const c of r.tiny.slice(0, 4))
      add(name, "TOUCH", `button "${c.sel}" only ${c.h}px tall`);
    if (r.railHidden > 0) add(name, "SCROLL", `sidebar has ${r.railHidden}px below the fold`);
    if (r.inspectorHidden > 0) add(name, "SCROLL", `inspector has ${r.inspectorHidden}px below the fold`);
  }

  await browser.close();
  console.log("=".repeat(64));
  const bySev = {};
  for (const f of findings) bySev[f.sev] = (bySev[f.sev] || 0) + 1;
  console.log(findings.length ? `${findings.length} findings: ${JSON.stringify(bySev)}` : "clean");
};

run().catch((e) => {
  console.error("audit crashed:", e);
  process.exit(1);
});
