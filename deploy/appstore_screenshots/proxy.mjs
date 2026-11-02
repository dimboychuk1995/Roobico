/**
 * Same-origin стенд для веб-рендера мобилки (react-native-web): отдаёт
 * статику `expo export --platform web` и проксирует всё остальное во Flask
 * :5000, чтобы cookie сессии были first-party. Маршруты приложения
 * резолвятся в <route>.html (static output), включая сегменты вида [id].
 * Слушает только 127.0.0.1.
 *
 *   cd mobile && npx expo export --platform web --output-dir <dist>
 *   node proxy.mjs <dist> [7777]
 *   node capture.mjs            # см. шапку capture.mjs
 */
import fs from "node:fs";
import http from "node:http";
import path from "node:path";

const DIST = process.argv[2];
const PORT = Number(process.argv[3] || 7777);
const UPSTREAM = { host: "127.0.0.1", port: 5000 };

if (!DIST) {
  console.error("usage: node proxy.mjs <expo web dist dir> [port]");
  process.exit(1);
}

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".svg": "image/svg+xml",
  ".ttf": "font/ttf",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".map": "application/json",
};

function resolveRoute(urlPath) {
  const clean = decodeURIComponent(urlPath.split("?")[0]);
  const direct = path.join(DIST, clean);
  if (clean !== "/" && fs.existsSync(direct) && fs.statSync(direct).isFile()) return direct;
  const segs = clean.split("/").filter(Boolean);
  if (!segs.length) return path.join(DIST, "index.html");
  let dir = DIST;
  for (let i = 0; i < segs.length; i++) {
    const last = i === segs.length - 1;
    const entries = fs.existsSync(dir) ? fs.readdirSync(dir) : [];
    if (last) {
      if (entries.includes(segs[i] + ".html")) return path.join(dir, segs[i] + ".html");
      const dyn = entries.find((e) => /^\[.+\]\.html$/.test(e));
      if (dyn) return path.join(dir, dyn);
      if (entries.includes(segs[i]) && fs.existsSync(path.join(dir, segs[i], "index.html")))
        return path.join(dir, segs[i], "index.html");
      return null;
    }
    if (entries.includes(segs[i])) dir = path.join(dir, segs[i]);
    else {
      const dynDir = entries.find((e) => /^\[.+\]$/.test(e) && fs.statSync(path.join(dir, e)).isDirectory());
      if (!dynDir) return null;
      dir = path.join(dir, dynDir);
    }
  }
  return null;
}

http
  .createServer((req, res) => {
    const file = req.method === "GET" ? resolveRoute(req.url) : null;
    if (file) {
      res.writeHead(200, { "content-type": MIME[path.extname(file)] || "application/octet-stream" });
      fs.createReadStream(file).pipe(res);
      return;
    }
    const headers = { ...req.headers, host: `${UPSTREAM.host}:${UPSTREAM.port}` };
    const up = http.request({ ...UPSTREAM, path: req.url, method: req.method, headers }, (ur) => {
      res.writeHead(ur.statusCode, ur.headers);
      ur.pipe(res);
    });
    up.on("error", (e) => {
      res.writeHead(502);
      res.end("upstream error: " + e.message);
    });
    req.pipe(up);
  })
  .listen(PORT, "127.0.0.1", () =>
    console.log(`proxy on http://127.0.0.1:${PORT} -> dist ${DIST}, upstream :${UPSTREAM.port}`)
  );
