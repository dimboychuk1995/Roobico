/**
 * Оформление скриншотов для App Store: сырой скриншот -> витринная карточка
 * (зелёный градиент, заголовок, скруглённый скриншот с тенью) в точном
 * размере, который требует Apple.
 *
 * Использование:
 *   npm install            # один раз (ставит sharp)
 *   node make_screenshots.mjs
 *
 * Кладёт результат в out/. Что рендерить — описано в captions.json:
 *   [{ "file": "raw/iphone/01-dashboard.png", "device": "iphone69",
 *      "caption": "Your whole shop\nin your pocket" }, ...]
 */
import fs from "node:fs";
import path from "node:path";
import sharp from "sharp";

const DEVICES = {
  // App Store Connect: iPhone 6.9" и iPad Pro 13" (portrait)
  iphone69: { w: 1320, h: 2868 },
  ipad13: { w: 2064, h: 2752 },
};

const BG_TOP = "#15803d";
const BG_BOTTOM = "#16a34a";
const MARGIN = 96; // поля слева/справа вокруг скриншота
const CAPTION_TOP = 140; // отступ заголовка от верха
const LINE_HEIGHT = 1.18;

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function backgroundSvg(w, h) {
  return Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">
    <defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${BG_TOP}"/><stop offset="1" stop-color="${BG_BOTTOM}"/>
    </linearGradient></defs>
    <rect width="${w}" height="${h}" fill="url(#g)"/>
  </svg>`);
}

function captionSvg(w, lines, fontSize) {
  const totalH = Math.ceil(fontSize * LINE_HEIGHT * lines.length) + 20;
  const spans = lines
    .map(
      (line, i) =>
        `<text x="50%" y="${Math.round(fontSize + i * fontSize * LINE_HEIGHT)}"
           text-anchor="middle" font-family="Segoe UI, Arial, sans-serif"
           font-size="${fontSize}" font-weight="700" fill="#ffffff">${esc(line)}</text>`
    )
    .join("\n");
  return { svg: Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${totalH}">${spans}</svg>`), height: totalH };
}

function roundedMask(w, h, r) {
  return Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">
       <rect width="${w}" height="${h}" rx="${r}" ry="${r}" fill="#fff"/>
     </svg>`
  );
}

async function makeOne({ file, device, caption }) {
  const spec = DEVICES[device];
  if (!spec) throw new Error(`Unknown device "${device}" for ${file}`);
  const { w, h } = spec;

  const fontSize = device === "iphone69" ? 88 : 96;
  const lines = caption.split("\n");
  const cap = captionSvg(w, lines, fontSize);

  // Скриншот: масштабируем на ширину карточки, скругляем, обрезаем по низу
  const shotW = w - MARGIN * 2;
  const radius = Math.round(shotW * 0.05);
  const shotTop = CAPTION_TOP + cap.height + 70;
  const availH = h - shotTop; // скриншот "уходит" за нижний край — так и задумано

  const scaled = await sharp(file).resize({ width: shotW }).png().toBuffer();
  const meta = await sharp(scaled).metadata();
  const cropH = Math.min(meta.height, availH);
  const cropped = await sharp(scaled)
    .extract({ left: 0, top: 0, width: shotW, height: cropH })
    .composite([{ input: roundedMask(shotW, cropH, radius), blend: "dest-in" }])
    .png()
    .toBuffer();

  const outName = `${device}-${path.basename(file)}`;
  await sharp(backgroundSvg(w, h))
    .composite([
      { input: cap.svg, top: CAPTION_TOP, left: 0 },
      { input: cropped, top: shotTop, left: MARGIN },
    ])
    .png()
    .toFile(path.join("out", outName));
  console.log(`ok ${outName} (${w}x${h})`);
}

const jobs = JSON.parse(fs.readFileSync("captions.json", "utf8"));
fs.mkdirSync("out", { recursive: true });
for (const job of jobs) {
  if (!fs.existsSync(job.file)) {
    console.warn(`SKIP ${job.file} — файла нет`);
    continue;
  }
  await makeOne(job);
}
console.log("Done. Результат в out/");
