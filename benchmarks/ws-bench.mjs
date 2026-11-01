// WebSocket handshake bench against the live server. Uses the repo's own
// `ws` package from node_modules. Run with `node` (baseline) or `bun` (after).
// usage: ws-bench.mjs <url> [connections=200]
import { createRequire } from "module";
import path from "path";
// resolve `ws` from apps/live/node_modules (pnpm isolated layout)
const require = createRequire(path.join(process.cwd(), "package.json"));
const WebSocket = require("ws");

const url = process.argv[2] ?? "ws://127.0.0.1:3100/live";
const N = parseInt(process.argv[3] ?? "200", 10);
const lat = [];
let ok = 0,
  fail = 0;

function once() {
  return new Promise((resolve) => {
    const t0 = performance.now();
    const ws = new WebSocket(url, { handshakeTimeout: 5000 });
    ws.on("open", () => {
      lat.push(performance.now() - t0);
      ok++;
      ws.close();
      resolve();
    });
    ws.on("error", () => {
      fail++;
      resolve();
    });
  });
}

// sequential: measures per-connection server cost, not client concurrency
// oxlint-disable-next-line no-await-in-loop
for (let i = 0; i < N; i++) await once();
lat.sort((a, b) => a - b);
const q = (p) => Math.round(lat[Math.min(lat.length - 1, Math.floor(lat.length * p))]);
console.log(
  JSON.stringify({
    url,
    attempted: N,
    ok,
    fail,
    handshake_ms: { p50: q(0.5), p95: q(0.95), max: Math.round(lat.at(-1) ?? 0) },
    conns_per_s: Math.round(ok / (lat.reduce((a, b) => a + b, 0) / 1000 || 1)),
  })
);
