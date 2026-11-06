// Screenshots for the user manual and the system documentation.
//
//   1. backend with demo data:  DATABASE_URL=sqlite:///./demo.db python scripts/demo_data.py
//                               DATABASE_URL=sqlite:///./demo.db uvicorn app.main:app
//   2. frontend:                pnpm dev
//   3. node scripts/screenshots.mjs        (needs Playwright with Chromium)
//
// Writes PNGs into backend/app/handbuch/img/. Only invented demo data may appear on them.
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const here = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(here, "../../backend/app/handbuch/img");
const BASE = process.env.APP_URL ?? "http://127.0.0.1:5173";
const SAMPLE = resolve(here, "../../samples/beispielrechnung.pdf");
const PASSWORD = "demo-passwort-2026";
const only = process.argv.slice(2);
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ args: ["--lang=de-DE"] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, locale: "de-DE", colorScheme: "light" });
const page = await ctx.newPage();

async function shot(name, opts = {}) {
  if (only.length && !only.includes(name)) return;
  await page.waitForTimeout(opts.wait ?? 500);
  const target = opts.selector ? page.locator(opts.selector).first() : page;
  await target.screenshot({ path: `${OUT}/${name}.png`, fullPage: opts.full ?? false, animations: "disabled" });
  console.log("✓", name);
}

async function login(user) {
  await page.goto(`${BASE}/#/start`);
  await page.getByLabel("Benutzername").fill(user);
  await page.getByLabel("Passwort").fill(PASSWORD);
  if (user === "anna") await shot("anmeldung", { wait: 200, selector: "form" });
  await page.getByRole("button", { name: "Anmelden" }).click();
  await page.waitForSelector(".sidebar");
}

// --- user manual -----------------------------------------------------------
await login("anna");
await page.waitForSelector(".charts");
await shot("startseite", { wait: 1200 });
await shot("startseite-kennzahlen", { selector: ".charts", wait: 300 });

await page.goto(`${BASE}/#/umwandeln`);
await shot("umwandeln-start");
await page.locator(".workspace input[type=file]").first().setInputFiles(SAMPLE);
await page.waitForSelector(".editor", { timeout: 20000 });
await shot("umwandeln-pruefen", { wait: 2500 });

await page.goto(`${BASE}/#/pruefen`);
await page.waitForTimeout(800);
await shot("zu-pruefen-liste");
const firstItem = page.locator("table tbody tr.clickable").first();
if (await firstItem.count()) {
  await firstItem.click();
  await page.waitForTimeout(2500);
  await shot("zu-pruefen-bearbeiten");
}

await page.goto(`${BASE}/#/archiv`);
await page.waitForSelector(".archive table");
await shot("archiv");

await page.goto(`${BASE}/#/hilfe`);
await page.waitForSelector(".doc h1");
await shot("hilfe");

await page.goto(`${BASE}/#/passwort`);
await shot("passwort");

// --- system documentation (admin screens) ---------------------------------
await page.getByRole("button", { name: "Abmelden" }).click();
await login("admin");
await page.goto(`${BASE}/#/verwaltung`);
await page.waitForTimeout(800);
await shot("admin-benutzer");
for (const [tab, name] of [
  ["Eingänge & Automatik", "admin-eingaenge"],
  ["Kundenstamm (SAP)", "admin-kundenstamm"],
  ["Einstellungen", "admin-einstellungen"],
  ["Protokoll", "admin-protokoll"],
]) {
  const btn = page.locator(".tabs button", { hasText: tab });
  if (await btn.count()) {
    await btn.first().click();
    await shot(name, { wait: 900 });
  }
}

await browser.close();
