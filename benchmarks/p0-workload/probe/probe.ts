/**
 * Scratch-only diagnostic probe for the P0 reference stack.
 *
 * Not a benchmark and not evidence: it exists to isolate the M15 reference
 * failures (latency collapse after the burst, page-save marker loss) with
 * small, single-variable load shapes. It reuses the production API paths of
 * run.ts but changes no application behavior.
 *
 * Modes:
 *   read-seq      N sequential reads through one session; latency + status.
 *   read-arrival  fixed arrival schedule of reads only (steady/burst/steady).
 *   pages         five editors edit one page; per-edit ack latency and
 *                 persisted-marker verification with a configurable wait.
 *
 * Writes JSON to --out (default /tmp/plane-probe/<label>-<ts>.json).
 */
import { parseArgs } from "node:util";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { arrivals } from "../contract";
import type { Doc } from "yjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const reqEditor = createRequire(join(HERE, "../../../packages/editor/package.json"));
const Y = reqEditor("yjs") as typeof import("yjs");
const { HocuspocusProvider } = reqEditor("@hocuspocus/provider") as typeof import("@hocuspocus/provider");

const BASE_URL = "http://127.0.0.1:8080";
const rawFetch = globalThis.fetch;
const fetch: typeof globalThis.fetch = (input, init) =>
  rawFetch(input, { ...init, signal: AbortSignal.timeout(30000) });

const parsed = parseArgs({
  args: process.argv.slice(2),
  options: {
    label: { type: "string" },
    mode: { type: "string" },
    "seed-file": { type: "string" },
    out: { type: "string" },
    count: { type: "string" },
    rate: { type: "string" },
    duration: { type: "string" },
    "burst-after": { type: "string" },
    "burst-rate": { type: "string" },
    "burst-duration": { type: "string" },
    "max-inflight": { type: "string" },
    editors: { type: "string" },
    edits: { type: "string" },
    "persist-wait": { type: "string" },
    kind: { type: "string" },
    users: { type: "string" },
  },
  strict: true,
});
const vals = parsed.values as Record<string, string | undefined>;
const a = (n: string, d?: number) => {
  const v = vals[n];
  if (v === undefined) {
    if (d === undefined) throw new Error(`missing --${n}`);
    return d;
  }
  return Number(v);
};
const label = vals.label ?? "probe";
const mode = vals.mode ?? "read-seq";
const seedFile = vals["seed-file"] ?? join(HERE, "../seed/user.json");
const seed = JSON.parse(readFileSync(seedFile, "utf8")) as {
  users: Array<{ email: string; password: string }>;
  dataset: { workspace_slug: string; project_id: string; page_ids: string[]; issue_ids: string[] };
};

type Session = { cookie: string; csrf: string; id: string };
type Rec = { kind: string; actor_id: string; t: number; ms: number; ok: boolean; status?: number; error?: string };

async function signIn(user: { email: string; password: string }): Promise<Session> {
  const first = await fetch(`${BASE_URL}/auth/get-csrf-token/`);
  const csrf = first.headers
    .getSetCookie()
    .find((c) => c.startsWith("csrftoken="))
    ?.split(";")[0]
    .slice(10);
  if (!csrf) throw new Error("no CSRF cookie");
  const res = await fetch(`${BASE_URL}/auth/sign-in/`, {
    method: "POST",
    redirect: "manual",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Origin: BASE_URL,
      Cookie: `csrftoken=${csrf}`,
      "X-CSRFToken": csrf,
    },
    body: new URLSearchParams(user),
  });
  const cookie = res.headers
    .getSetCookie()
    .find((c) => c.startsWith("session-id="))
    ?.split(";")[0];
  if (res.status !== 302 || !cookie) throw new Error(`sign-in failed: HTTP ${res.status}`);
  const me = await fetch(`${BASE_URL}/api/users/me/`, { headers: { Cookie: cookie } });
  const data = (await me.json()) as { id?: string };
  if (!me.ok || typeof data.id !== "string") throw new Error(`user lookup: HTTP ${me.status}`);
  return { cookie, csrf, id: data.id };
}
const WS_SLUG = seed.dataset.workspace_slug;
const PROJECT_ID = seed.dataset.project_id;
const apiBase = `${BASE_URL}/api/workspaces/${WS_SLUG}/projects/${PROJECT_ID}`;
const issuesPath = `${apiBase}/issues/`;

const appHeaders = (s: Session) => ({
  Cookie: s.cookie,
  Referer: BASE_URL + "/",
  Origin: BASE_URL,
  "X-CSRFToken": s.csrf,
});

type ReadKind = "issue-list" | "board-status" | "board-priority" | "pages" | "me";
async function readOp(kind: ReadKind, s: Session, index: number): Promise<{ ok: boolean; status?: number }> {
  const url =
    kind === "me"
      ? `${BASE_URL}/api/users/me/`
      : kind === "issue-list"
        ? `${issuesPath}?order_by=created_at&per_page=50&page=${(index % 3) + 1}`
        : kind === "board-status"
          ? `${issuesPath}?group_by=state__group&order_by=created_at`
          : kind === "board-priority"
            ? `${issuesPath}?group_by=priority&order_by=created_at`
            : `${apiBase}/pages/?per_page=20`;
  const res = await fetch(url, { headers: appHeaders(s) });
  await res.json();
  return { ok: res.ok, status: res.status };
}
const READ_CYCLE: ReadKind[] = [
  "board-status",
  "issue-list",
  "issue-list",
  "board-priority",
  "issue-list",
  "pages",
  "me",
];

let runStart = 0;
const ops: Rec[] = [];
async function op(kind: string, s: Session, t: number, fn: () => Promise<{ ok: boolean; status?: number }>) {
  const arrival = runStart + t * 1000;
  let rec: Rec;
  try {
    const r = await fn();
    rec = { kind, actor_id: s.id, t, ms: Date.now() - arrival, ok: r.ok, status: r.status };
  } catch (e) {
    rec = { kind, actor_id: s.id, t, ms: Date.now() - arrival, ok: false, error: String(e).slice(0, 160) };
  }
  ops.push(rec);
  if (!rec.ok) console.warn(`[fail] ${kind} ${rec.status ?? rec.error}`);
}
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

// --------------------------------------------------------------- pages mode
type Editor = { pageId: string; doc: Doc; provider: InstanceType<typeof HocuspocusProvider>; user: Session };
const LIVE_WS = BASE_URL.replace("http", "ws") + "/live/collaboration";
async function connectEditor(pageId: string, user: Session): Promise<Editor> {
  const doc = new Y.Doc();
  const params = new URLSearchParams({ documentType: "project_page", workspaceSlug: WS_SLUG, projectId: PROJECT_ID });
  const provider = new HocuspocusProvider({
    url: `${LIVE_WS}?${params}`,
    name: pageId,
    document: doc,
    token: JSON.stringify({ id: user.id, cookie: user.cookie }),
  });
  const synced = Promise.withResolvers<void>();
  const timer = setTimeout(() => {
    provider.destroy();
    synced.reject(new Error("editor sync timeout"));
  }, 15000);
  provider.on("synced", () => {
    clearTimeout(timer);
    synced.resolve();
  });
  await synced.promise;
  return { pageId, doc, provider, user };
}
function xml(e: Editor): string {
  return e.doc.getXmlFragment("default").toString();
}
async function persistedXml(pageId: string, s: Session): Promise<string | null> {
  const res = await fetch(`${apiBase}/pages/${pageId}/description/`, { headers: appHeaders(s) });
  if (!res.ok) return null;
  const doc = new Y.Doc();
  try {
    Y.applyUpdate(doc, new Uint8Array(await res.arrayBuffer()));
    return doc.getXmlFragment("default").toString();
  } finally {
    doc.destroy();
  }
}

async function pagesMode(editorsN: number, edits: number, persistWait: number) {
  const sessions: Session[] = [];
  for (let i = 0; i < editorsN; i++) sessions.push(await signIn(seed.users[i]));
  const pageId = seed.dataset.page_ids[0];
  const editors: Editor[] = [];
  for (let i = 0; i < editorsN; i++) editors.push(await connectEditor(pageId, sessions[i]));
  const expected: string[] = [];
  const syncTimes: Array<{ e: number; k: number; ms: number; t: number }> = [];
  runStart = Date.now();
  for (let k = 0; k < edits; k++) {
    for (let e = 0; e < editorsN; e++) {
      const editor = editors[e];
      const marker = `bench-${label}-e${e}-k${k}-end`;
      const t = (Date.now() - runStart) / 1000;
      const started = Date.now();
      const para = new Y.XmlElement("paragraph");
      para.insert(0, [new Y.XmlText(marker)]);
      const body = editor.doc.getXmlFragment("default");
      body.insert(body.length, [para]);
      const deadline = Date.now() + 15000;
      while (editor.provider.hasUnsyncedChanges) {
        if (Date.now() >= deadline) throw new Error(`ack timeout e${e} k${k}`);
        await sleep(10);
      }
      expected.push(marker);
      syncTimes.push({ e, k, ms: Date.now() - started, t });
    }
  }
  // First persisted read immediately after the last ack, then poll.
  const first = await persistedXml(pageId, sessions[0]);
  const t0 = Date.now();
  let xmlNow = first;
  let waited = 0;
  while (Date.now() - t0 < persistWait * 1000) {
    const missing = expected.filter((m) => !(xmlNow ?? "").includes(m));
    if (missing.length === 0) break;
    await sleep(500);
    xmlNow = await persistedXml(pageId, sessions[0]);
  }
  waited = (Date.now() - t0) / 1000;
  const missing = expected.filter((m) => !(xmlNow ?? "").includes(m));
  const missingIdx = missing.map((m) => {
    const mm = /-e(\d+)-k(\d+)-end$/.exec(m);
    return mm ? `e${mm[1]}k${mm[2]}` : m;
  });
  // Reconnect check on a fresh provider for editor 0.
  editors[0].provider.destroy();
  editors[0].doc.destroy();
  const rebuilt = await connectEditor(pageId, sessions[0]);
  const afterReconnect = expected.filter((m) => !xml(rebuilt).includes(m));
  for (const ed of [rebuilt, ...editors.slice(1)]) {
    ed.provider.destroy();
    ed.doc.destroy();
  }
  return {
    mode: "pages",
    label,
    page_id: pageId,
    editors: editorsN,
    edits_per_editor: edits,
    acknowledged: expected.length,
    persisted_ok: expected.length - missing.length,
    missing_count: missing.length,
    missing_first: missingIdx.slice(0, 30),
    persisted_wait_s: waited,
    persisted_found_immediately: (first ?? "") === "" ? 0 : expected.filter((m) => (first ?? "").includes(m)).length,
    reconnect_missing_count: afterReconnect.length,
    ack_ms: summarizeMs(syncTimes.map((x) => x.ms)),
    ack_timeline: syncTimes.map((x) => ({ t: Number(x.t.toFixed(2)), e: x.e, ms: Math.round(x.ms) })),
  };
}

function summarizeMs(v: number[]): { n: number; p50: number; p95: number; max: number } {
  if (!v.length) return { n: 0, p50: 0, p95: 0, max: 0 };
  const s = [...v].sort((x, y) => x - y);
  const p = (q: number) => s[Math.min(s.length - 1, Math.ceil((q / 100) * s.length) - 1)];
  return { n: s.length, p50: Math.round(p(50)), p95: Math.round(p(95)), max: Math.round(s[s.length - 1]) };
}

async function main() {
  let report: Record<string, unknown>;
  if (mode === "pages") {
    report = await pagesMode(a("editors", 5), a("edits", 1), a("persist-wait", 30));
  } else {
    const users = seed.users.slice(0, a("users", 20));
    const sessions: Session[] = [];
    for (const user of users) sessions.push(await signIn(user));
    if (mode === "read-seq") {
      runStart = Date.now();
      const count = a("count", 30);
      const kinds: ReadKind[] = vals.kind ? [vals.kind as ReadKind] : READ_CYCLE;
      for (let i = 0; i < count; i++) {
        const s = sessions[i % sessions.length];
        const kind = kinds[i % kinds.length];
        const t = (Date.now() - runStart) / 1000;
        await op(kind, s, t, () => readOp(kind, s, i));
      }
      report = { mode, label, count, latencies: summarizeMs(ops.map((o) => o.ms)), by_kind: byKind() };
    } else if (mode === "read-arrival") {
      const schedule = arrivals({
        duration: a("duration", 120),
        rate: a("rate", 6),
        burstAfter: a("burst-after", 60),
        burstRate: a("burst-rate", 20),
        burstDuration: a("burst-duration", 30),
      });
      const maxInflight = a("max-inflight", 256);
      runStart = Date.now();
      const pending = new Set<Promise<unknown>>();
      let rejected = 0;
      let maxLag = 0;
      for (const [index, t] of schedule.entries()) {
        await sleep(Math.max(0, runStart + t * 1000 - Date.now()));
        maxLag = Math.max(maxLag, Date.now() - runStart - t * 1000);
        if (pending.size >= maxInflight) {
          rejected++;
          continue;
        }
        const s = sessions[(index + Math.floor(index / 6)) % sessions.length];
        const kind = READ_CYCLE[index % READ_CYCLE.length];
        const task = op(kind, s, t, () => readOp(kind, s, index));
        pending.add(task);
        void task.finally(() => pending.delete(task));
      }
      await Promise.all(pending);
      const end = Date.now();
      report = {
        mode,
        label,
        planned: schedule.length,
        rejected,
        max_dispatch_lag_ms: maxLag,
        elapsed_s: (end - runStart) / 1000,
        achieved_rate: ops.length / ((end - runStart) / 1000),
        latencies: summarizeMs(ops.map((o) => o.ms)),
        by_kind: byKind(),
        buckets: buckets(15),
        operations: ops,
      };
    } else {
      throw new Error(`unknown mode ${mode}`);
    }
  }
  const outDir = "/tmp/plane-probe";
  mkdirSync(outDir, { recursive: true });
  const out = vals.out ?? join(outDir, `${label}-${Date.now()}.json`);
  writeFileSync(out, JSON.stringify({ ...report, operations: mode === "pages" ? undefined : ops }, null, 1));
  console.log(`probe report: ${out}`);
}

function byKind() {
  const out: Record<string, { n: number; p50: number; p95: number; max: number; errors: number }> = {};
  for (const kind of new Set(ops.map((o) => o.kind))) {
    const list = ops.filter((o) => o.kind === kind);
    out[kind] = { ...summarizeMs(list.map((o) => o.ms)), errors: list.filter((o) => !o.ok).length };
  }
  return out;
}
function buckets(width: number) {
  const out: Array<{ t: number; n: number; errors: number; p50: number; max: number; ok_rate: number }> = [];
  for (let start = 0; start * width < 1e9; start++) {
    const list = ops.filter((o) => o.t >= start * width && o.t < (start + 1) * width);
    if (!list.length) break;
    const ms = summarizeMs(list.map((o) => o.ms));
    out.push({
      t: start * width,
      n: list.length,
      errors: list.filter((o) => !o.ok).length,
      p50: ms.p50,
      max: ms.max,
      ok_rate: Number((1 - list.filter((o) => !o.ok).length / list.length).toFixed(3)),
    });
  }
  return out;
}

void main().catch((e) => {
  console.error(String(e));
  process.exit(1);
});
