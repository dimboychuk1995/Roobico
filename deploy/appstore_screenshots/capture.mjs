/**
 * Автосъёмка сырых скриншотов из веб-рендера приложения (react-native-web).
 * Требует поднятого стенда: Flask :5000 + same-origin прокси :7777 (см.
 * scratchpad/shots/proxy.mjs). Результат кладёт в raw/iphone и raw/ipad.
 *
 *   node capture.mjs         # светлая тема
 *   node capture.mjs dark    # тёмная тема -> raw/iphone-dark, raw/ipad-dark
 */
import fs from "node:fs";
import { chromium } from "playwright";

const BASE = "http://localhost:7777";
const EMAIL = "owner@roobico-test.com";
const PASSWORD = "RoobicoTest2026!";

const DEVICES = {
  iphone: { viewport: { width: 440, height: 956 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true },
  ipad: { viewport: { width: 1032, height: 1376 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
};

async function login(page) {
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.getByText(/^Server:/).click();
  await page.getByPlaceholder("https://app.roobico.com").fill(BASE);
  await page.getByPlaceholder("you@company.com").fill(EMAIL);
  await page.getByPlaceholder("••••••••").fill(PASSWORD);
  await page.getByText("Sign in", { exact: true }).click();
  await page.waitForTimeout(4000);
}

async function shoot(page, file, settle = 1500) {
  await page.waitForTimeout(settle);
  await page.screenshot({ path: file });
  console.log("shot", file);
}

const scheme = process.argv.includes("dark") ? "dark" : "light";

for (const [base, dev] of Object.entries(DEVICES)) {
  const name = scheme === "dark" ? `${base}-dark` : base;
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ...dev, colorScheme: scheme });
  const page = await ctx.newPage();
  fs.mkdirSync(`raw/${name}`, { recursive: true });

  await login(page);

  // 01 Dashboard — период Year, чтобы метрики были не нулевые
  await page.getByText("Year", { exact: true }).click().catch(() => {});
  await shoot(page, `raw/${name}/01-dashboard.png`);

  // 02 Work Orders: список
  await page.getByText("Work Orders", { exact: true }).last().click();
  await shoot(page, `raw/${name}/02-wo-list.png`, 2500);

  // 03 Редактор WO: тап по первой карточке (неоплаченный WO редиректит в форму)
  await page.getByText(/#\d+/).first().click();
  await shoot(page, `raw/${name}/03-wo-editor.png`, 3500);

  // 04 Parts
  await page.goto(`${BASE}/parts`, { waitUntil: "networkidle" });
  await shoot(page, `raw/${name}/04-parts.png`, 2500);

  // 05 Reports
  await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
  await shoot(page, `raw/${name}/05-reports.png`, 2500);

  // 06 Calendar
  await page.goto(`${BASE}/calendar`, { waitUntil: "networkidle" });
  await shoot(page, `raw/${name}/06-calendar.png`, 3000);

  await browser.close();
}
console.log("done");
