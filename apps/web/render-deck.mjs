/**
 * Renders the SIH deck from HTML to PNG slides + a vector PDF, and refuses to
 * pass if anything overflows its box or escapes the slide.
 *
 * Run from apps/web (playwright lives here):
 *   node render-deck.mjs
 */
import { chromium } from "playwright";
import { mkdirSync, rmSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const DECK = resolve("../../PPT/deck/index.html");
const OUT = resolve("../../PPT/out");
const SCALE = 2;

const audit = () => {
  const problems = [];
  const slides = [...document.querySelectorAll(".slide")];

  slides.forEach((slide, i) => {
    const name = slide.dataset.name || `slide${i + 1}`;
    const sr = slide.getBoundingClientRect();

    if (Math.abs(slide.offsetWidth - 1280) > 0.5 || Math.abs(slide.offsetHeight - 720) > 0.5) {
      problems.push(`${name}: slide is ${slide.offsetWidth}x${slide.offsetHeight}, expected 1280x720`);
    }

    for (const el of slide.querySelectorAll("*")) {
      if (el.tagName === "svg" || el.closest("svg")) continue;

      const overY = el.scrollHeight - el.clientHeight;
      const overX = el.scrollWidth - el.clientWidth;
      if (el.clientHeight > 0 && overY > 1) {
        problems.push(
          `${name}: <${el.tagName.toLowerCase()}${el.className ? "." + String(el.className).split(" ").join(".") : ""}> ` +
          `content overflows by ${overY}px vertically (${el.scrollHeight} in ${el.clientHeight}) — "${(el.textContent || "").trim().slice(0, 60)}"`
        );
      }
      if (el.clientWidth > 0 && overX > 1) {
        problems.push(
          `${name}: <${el.tagName.toLowerCase()}${el.className ? "." + String(el.className).split(" ").join(".") : ""}> ` +
          `content overflows by ${overX}px horizontally — "${(el.textContent || "").trim().slice(0, 60)}"`
        );
      }

      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      const pad = 1.5;
      if (r.left < sr.left - pad || r.right > sr.right + pad ||
          r.top < sr.top - pad || r.bottom > sr.bottom + pad) {
        problems.push(
          `${name}: <${el.tagName.toLowerCase()}${el.className ? "." + String(el.className).split(" ").join(".") : ""}> ` +
          `escapes the slide (l${(r.left - sr.left).toFixed(0)} t${(r.top - sr.top).toFixed(0)} ` +
          `r${(r.right - sr.right).toFixed(0)} b${(r.bottom - sr.bottom).toFixed(0)}) — ` +
          `"${(el.textContent || "").trim().slice(0, 50)}"`
        );
      }
    }
  });

  // Overlap check between sibling blocks that should never touch. On the title
  // slide the blocks are absolutely positioned, so name them explicitly — the
  // decorative hexagon and hero are meant to overlap and stay out of it.
  slides.forEach((slide, i) => {
    const name = slide.dataset.name || `slide${i + 1}`;
    const blocks = [
      ...slide.querySelectorAll(".bd > *"),
      ...slide.querySelectorAll(".lockup, .psrows, .tsum, .tfoot"),
    ];
    for (let a = 0; a < blocks.length; a++) {
      for (let b = a + 1; b < blocks.length; b++) {
        const ra = blocks[a].getBoundingClientRect();
        const rb = blocks[b].getBoundingClientRect();
        const ox = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
        const oy = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
        if (ox > 1 && oy > 1) {
          problems.push(`${name}: top-level blocks ${a} and ${b} overlap by ${oy.toFixed(0)}px`);
        }
      }
    }
  });

  return problems;
};

const run = async () => {
  // Clear the slide PNGs only. Wiping all of OUT fails with EBUSY whenever the
  // exported deck is open in PowerPoint, which is exactly when you re-render.
  rmSync(`${OUT}/slides`, { recursive: true, force: true });
  mkdirSync(`${OUT}/slides`, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1280, height: 720 },
    deviceScaleFactor: SCALE,
  });

  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(pathToFileURL(DECK).href, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(600);

  const problems = await page.evaluate(audit);

  console.log(`\nLAYOUT AUDIT — ${problems.length ? problems.length + " problem(s)" : "clean"}`);
  console.log("=".repeat(70));
  problems.forEach((p) => console.log("  ✕ " + p));
  if (errors.length) {
    console.log("\nPAGE ERRORS");
    errors.forEach((e) => console.log("  ! " + e));
  }

  const slides = await page.$$(".slide");
  const names = await page.$$eval(".slide", (els) =>
    els.map((e, i) => e.dataset.name || `slide${i + 1}`));

  for (let i = 0; i < slides.length; i++) {
    await slides[i].screenshot({ path: `${OUT}/slides/${names[i]}.png` });
    console.log(`  → ${names[i]}.png`);
  }

  await page.pdf({
    path: `${OUT}/SIH2026-HackShastra-SthiraRoute.pdf`,
    width: "1280px",
    height: "720px",
    printBackground: true,
    preferCSSPageSize: true,
  });
  console.log(`  → SIH2026-HackShastra-SthiraRoute.pdf`);

  await browser.close();
  console.log(problems.length ? "\nFIX THE ABOVE.\n" : "\nAll slides fit.\n");
  process.exitCode = problems.length ? 2 : 0;
};

run().catch((e) => { console.error(e); process.exit(1); });
