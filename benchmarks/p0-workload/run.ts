/**
 * P0 synthetic fixed-rate workload runner.
 *
 * Fixed arrival rate (steady -> burst -> steady -> recovery) against the
 * isolated plane-bench stack, with durable-outcome checks:
 *   - board/list reads via the real app API (session auth through the proxy),
 *   - issue creates/patches (API -> issue_activity.delay -> RabbitMQ -> worker),
 *   - five persistent collaborative editors with exact persisted edit markers,
 *   - all issue values and queued name-change activity read back after load,
 *   - file upload via presigned POST + is_uploaded patch, download round-trip.
 *
 * Usage: bun run.ts --label p0-baseline-a [--duration 300] [--rate 6]
 *                   [--burst-after 60] [--burst-rate 20] [--burst-duration 30]
 *                   [--recovery 240] [--seed-file seed/user.json]
 * Exit codes: 0 all gates passed, 2 gate failed (report written), 1 runner error.
 */

import { parseArgs } from "node:util";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { AsyncLocalStorage } from "node:async_hooks";
import { arrivals, missingMarkers, workloadFailures } from "./contract";
import { assertAttached, reconnectEditorInto } from "./editor-reconnect";
// Resolve dependencies from their owning workspaces under the isolated linker.
import type * as Yjs from "yjs";
import type * as ProviderModule from "@hocuspocus/provider";
import type * as Zod from "zod";

const HERE = dirname(fileURLToPath(import.meta.url));
const reqEditor = createRequire(join(HERE, "../../packages/editor/package.json"));
const reqLive = createRequire(join(HERE, "../../apps/live/package.json"));
const { z } = reqLive("zod") as typeof Zod;

const Y = reqEditor("yjs") as typeof Yjs;
const providerMod = reqEditor("@hocuspocus/provider") as typeof ProviderModule;
const { HocuspocusProvider } = providerMod;
const fetch: typeof globalThis.fetch = (input, init) =>
  globalThis.fetch(input, { ...init, signal: AbortSignal.timeout(30000) });

const BASE_URL = "http://127.0.0.1:8080";

const parsed = parseArgs({
  args: process.argv.slice(2),
  options: {
    label: { type: "string" },
    "seed-file": { type: "string" },
    duration: { type: "string" },
    rate: { type: "string" },
    "burst-after": { type: "string" },
    "burst-rate": { type: "string" },
    "burst-duration": { type: "string" },
    recovery: { type: "string" },
    "max-inflight": { type: "string" },
  },
  strict: true,
}) as {
  values: {
    label?: string;
    "seed-file"?: string;
    duration?: string;
    rate?: string;
    "burst-after"?: string;
    "burst-rate"?: string;
    "burst-duration"?: string;
    recovery?: string;
    "max-inflight"?: string;
  };
};

const args = {
  label: parsed.values.label ?? "p0-run",
  "seed-file": parsed.values["seed-file"] ?? join(HERE, "seed/user.json"),
  duration: Number(parsed.values.duration ?? 300),
  rate: Number(parsed.values.rate ?? 6),
  "burst-after": Number(parsed.values["burst-after"] ?? 60),
  "burst-rate": Number(parsed.values["burst-rate"] ?? 20),
  "burst-duration": Number(parsed.values["burst-duration"] ?? 30),
  recovery: Number(parsed.values.recovery ?? 240),
  "max-inflight": Number(parsed.values["max-inflight"] ?? 256),
};

const seed = z
  .object({
    email: z.string().email(),
    password: z.string().min(1),
    users: z.array(z.object({ email: z.string().email(), password: z.string().min(1) })).length(20),
    dataset: z
      .object({
        workspace_slug: z.string().min(1),
        project_id: z.string().uuid(),
        page_ids: z.array(z.string().uuid()).min(5),
        issue_ids: z.array(z.string().uuid()).min(1),
      })
      .passthrough(),
  })
  .parse(JSON.parse(readFileSync(args["seed-file"], "utf8")));
const WS_SLUG = seed.dataset.workspace_slug;
const PROJECT_ID = seed.dataset.project_id;
const PAGE_IDS = seed.dataset.page_ids;
if (
  !Number.isInteger(args["max-inflight"]) ||
  args["max-inflight"] < 1 ||
  !Number.isFinite(args.recovery) ||
  args.recovery < 1
)
  throw new Error("invalid runner bounds");
type Session = { cookie: string; csrf: string; id: string };
const sessions: Session[] = [];
const actor = new AsyncLocalStorage<Session>();
const session = () => actor.getStore() ?? sessions[0];
const expectedIssues = new Map<string, string>();
const expectedActivities = new Map<string, Set<string>>();
const issueChains = new Map<string, Promise<unknown>>();

// ---------------------------------------------------------------- results
type OpRecord = {
  kind: string;
  actor_id: string;
  t: number; // s since run start (arrival)
  ms: number; // latency
  ok: boolean;
  status?: number;
  error?: string;
  /** Read payload size observed by the harness (instrumentation only). */
  items?: number;
};
const ops: OpRecord[] = [];
const outcome = {
  issuesCreated: 0,
  issuesPatched: 0,
  issueReadsOk: 0,
  pagesEdited: 0,
  pageReconnects: 0,
  persistedMarkers: 0,
  reconnectMarkers: 0,
  activitiesVerified: 0,
  filesUploaded: 0,
  filesRoundTripOk: 0,
  wsSynced: 0,
  readItems: {} as Record<string, number>,
};
const report = {
  label: args.label,
  schema: "p0-workload/5",
  started_at: "",
  finished_at: "",
  load_started_at: "",
  load_completed_at: "",
  base_url: BASE_URL,
  dataset: seed.dataset,
  offered: {
    steady_rate: args.rate,
    burst_rate: args["burst-rate"],
    burst_after_s: args["burst-after"],
    burst_duration_s: args["burst-duration"],
    duration_s: args.duration,
    recovery_s: args.recovery,
    planned_ops: 0,
    rejected_ops: 0,
    rejected: [] as Array<{ kind: string; t: number; reason: string }>,
    max_dispatch_lag_ms: 0,
    users: 20,
    editors: 5,
  },
  achieved: { total_ops: 0, achieved_rate_per_s: 0, errors: 0 },
  latency: {} as Record<string, { p50: number; p95: number; n: number }>,
  outcome,
  durable: {
    pages: {} as Record<string, { expected: string[]; missing: string[]; persisted_xml: string }>,
    issues: {} as Record<
      string,
      { expected: string; actual: string; expected_activities: string[]; missing_activities: string[] }
    >,
  },
  queue: {
    sampled_depths: [] as number[],
    samples: [] as Array<{ t: number; depth: number | null }>,
    drained: false,
    drain_s: null as number | null,
    last_error: null as string | null,
  },
  gate: { pass: false, failures: [] as string[] },
};

function percentile(values: number[], p: number): number {
  if (values.length === 0) return Number.NaN;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return sorted[idx];
}
function summarize() {
  const by = new Map<string, number[]>();
  for (const op of ops) {
    const list = by.get(op.kind) ?? [];
    list.push(op.ms);
    by.set(op.kind, list);
  }
  for (const [kind, list] of by) {
    report.latency[kind] = { p50: percentile(list, 50), p95: percentile(list, 95), n: list.length };
  }
  report.achieved.total_ops = ops.length;
  const span = (runEnd - runStart) / 1000;
  report.achieved.achieved_rate_per_s = span > 0 ? ops.length / span : 0;
  report.achieved.errors = ops.filter((o) => !o.ok).length;
  report.outcome.readItems = {};
  for (const op of ops) {
    if (op.items !== undefined) {
      report.outcome.readItems[op.kind] = (report.outcome.readItems[op.kind] ?? 0) + op.items;
    }
  }
}

// ---------------------------------------------------------------- session
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
  const data = await me.json();
  if (!me.ok || !data || typeof data.id !== "string") throw new Error(`user lookup: HTTP ${me.status}`);
  return { cookie, csrf, id: data.id };
}

const appHeaders = (): Record<string, string> => ({
  Cookie: session().cookie,
  Referer: BASE_URL + "/",
  Origin: BASE_URL,
  "X-CSRFToken": session().csrf,
});

const jsonHeaders = (): Record<string, string> => ({
  ...appHeaders(),
  "Content-Type": "application/json",
});

// ---------------------------------------------------------------- op helpers
const apiBase = `${BASE_URL}/api/workspaces/${WS_SLUG}/projects/${PROJECT_ID}`;

async function op(
  kind: string,
  fn: () => Promise<{ ok: boolean; status?: number; items?: number }>,
  tStart: number
): Promise<boolean> {
  const arrival = runStart + tStart * 1000;
  let record: OpRecord;
  try {
    const r = await fn();
    record = {
      kind,
      actor_id: session().id,
      t: tStart,
      ms: Date.now() - arrival,
      ok: r.ok,
      status: r.status,
      items: r.items,
    };
  } catch (e) {
    record = {
      kind,
      actor_id: session().id,
      t: tStart,
      ms: Date.now() - arrival,
      ok: false,
      error: String(e).slice(0, 200),
    };
  }
  ops.push(record);
  if (!record.ok) console.warn(`[op-fail] ${kind}: ${record.status ?? record.error}`);
  return record.ok;
}

const issuesPath = `${apiBase}/issues/`;

async function createIssue(index: number): Promise<{ ok: boolean; status?: number; id?: string }> {
  const res = await fetch(issuesPath, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ name: `bench issue ${args.label} ${index}` }),
  });
  if (!res.ok) return { ok: false, status: res.status };
  const data = (await res.json()) as { id: string };
  expectedIssues.set(data.id, `bench issue ${args.label} ${index}`);
  outcome.issuesCreated += 1;
  return { ok: true, status: res.status, id: data.id };
}

const createdIssues: string[] = [];

async function patchIssue(id: string, n: number): Promise<{ ok: boolean; status?: number }> {
  const name = `bench-${args.label}-patch-${n}-end`;
  const res = await fetch(`${issuesPath}${id}/`, {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify({ name }),
  });
  if (res.ok) {
    expectedIssues.set(id, name);
    const values = expectedActivities.get(id) ?? new Set<string>();
    values.add(name);
    expectedActivities.set(id, values);
  }
  return { ok: res.ok, status: res.status };
}

async function readBoard(
  groupBy: "state__group" | "priority"
): Promise<{ ok: boolean; status?: number; items?: number }> {
  const res = await fetch(`${issuesPath}?group_by=${groupBy}&order_by=created_at`, { headers: appHeaders() });
  const data = (await res.json()) as { results?: unknown };
  const items = Array.isArray(data?.results)
    ? data.results.length
    : data?.results && typeof data.results === "object"
      ? Object.values(data.results as Record<string, { results?: unknown[] }>).reduce(
          (total, group) => total + (Array.isArray(group?.results) ? group.results.length : 0),
          0
        )
      : undefined;
  return { ok: res.ok, status: res.status, items };
}

async function readIssueList(page: number): Promise<{ ok: boolean; status?: number; items?: number }> {
  const res = await fetch(`${issuesPath}?order_by=created_at&per_page=50&page=${page + 1}`, {
    headers: appHeaders(),
  });
  const data = (await res.json()) as { results?: unknown[] };
  return { ok: res.ok, status: res.status, items: Array.isArray(data?.results) ? data.results.length : undefined };
}

async function readPages(): Promise<{ ok: boolean; status?: number; items?: number }> {
  const res = await fetch(`${apiBase}/pages/?per_page=20`, { headers: appHeaders() });
  // The pages list endpoint returns a bare array, unlike the issue endpoints.
  const data = (await res.json()) as unknown[] | { results?: unknown[] };
  const items = Array.isArray(data) ? data.length : Array.isArray(data?.results) ? data.results.length : undefined;
  return { ok: res.ok, status: res.status, items };
}

// ------------------------------------------------------- five live editors
const LIVE_WS = BASE_URL.replace("http", "ws") + "/live/collaboration";
type Editor = { pageId: string; doc: Yjs.Doc; provider: InstanceType<typeof HocuspocusProvider>; user: Session };
const editors = new Map<string, Editor>();
const markers = new Map<string, string[]>();
const editorChains = new Map<string, Promise<unknown>>();

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
    synced.reject(new Error("editor sync timeout"));
  }, 15000);
  provider.on("synced", () => {
    clearTimeout(timer);
    synced.resolve();
  });
  try {
    await synced.promise;
  } catch (error) {
    // Never leak a half-open connection or its document: this connection is
    // abandoned and the caller decides what happens next.
    provider.destroy();
    doc.destroy();
    throw error;
  }
  outcome.wsSynced++;
  return { pageId, doc, provider, user };
}

async function pageEditViaWebSocket(editorId: string, reconnect: boolean, index: number): Promise<{ ok: boolean }> {
  let editor = editors.get(editorId)!;
  const pageId = editor.pageId;
  if (reconnect) {
    // Transactional swap: the live connection survives a failed replacement so
    // later edits still reach the server instead of silently dying on a
    // destroyed provider. The failure itself still propagates (no retry).
    editor = await reconnectEditorInto(
      editors,
      editorId,
      () => connectEditor(pageId, editor.user),
      async (replacement) => {
        const deadline = Date.now() + 15000;
        while (missingMarkers(replacement.doc.getXmlFragment("default").toString(), markers.get(pageId) ?? []).length) {
          if (Date.now() >= deadline) throw new Error("reconnect lost acknowledged edit");
          await sleep(50);
        }
      }
    );
    outcome.pageReconnects++;
  }
  assertAttached(editor);
  const marker = `bench-${args.label}-edit-${index}-end`;
  const para = new Y.XmlElement("paragraph");
  para.insert(0, [new Y.XmlText(marker)]);
  const body = editor.doc.getXmlFragment("default");
  body.insert(body.length, [para]);
  const deadline = Date.now() + 15000;
  while (editor.provider.hasUnsyncedChanges) {
    if (Date.now() >= deadline) throw new Error("edit acknowledgement timeout");
    await sleep(10);
  }
  const expected = markers.get(pageId) ?? [];
  expected.push(marker);
  markers.set(pageId, expected);
  outcome.pagesEdited++;
  return { ok: true };
}

async function verifyPageBinaries(): Promise<boolean> {
  let count = 0;
  for (const [pageId, expected] of markers) {
    const res = await fetch(`${apiBase}/pages/${pageId}/description/`, { headers: appHeaders() });
    if (!res.ok) return false;
    const doc = new Y.Doc();
    try {
      Y.applyUpdate(doc, new Uint8Array(await res.arrayBuffer()));
      const content = doc.getXmlFragment("default").toString();
      const missing = missingMarkers(content, expected);
      report.durable.pages[pageId] = { expected: [...expected], missing, persisted_xml: content };
      if (missing.length) return false;
      count += expected.length;
    } finally {
      doc.destroy();
    }
  }
  outcome.persistedMarkers = count;
  return count === outcome.pagesEdited && count > 0;
}

async function verifyIssuesAndActivities(): Promise<boolean> {
  let values = 0;
  let activities = 0;
  for (const [id, expected] of expectedIssues) {
    const res = await fetch(`${issuesPath}${id}/`, { headers: appHeaders() });
    const data = z.object({ name: z.string() }).parse(await res.json());
    report.durable.issues[id] = { expected, actual: data.name, expected_activities: [], missing_activities: [] };
    if (!res.ok || data.name !== expected) return false;
    values++;
    const names = expectedActivities.get(id);
    if (!names) continue;
    const history = await fetch(`${issuesPath}${id}/history/?activity_type=issue-property`, { headers: appHeaders() });
    if (!history.ok) return false;
    const rows = z
      .array(z.object({ field: z.string().nullable(), new_value: z.string().nullable() }))
      .parse(await history.json());
    const persisted = new Set(rows.filter((row) => row.field === "name").map((row) => row.new_value));
    const missing = [...names].filter((name) => !persisted.has(name));
    report.durable.issues[id].expected_activities = [...names];
    report.durable.issues[id].missing_activities = missing;
    if (missing.length) return false;
    activities += names.size;
  }
  outcome.issueReadsOk = values;
  outcome.activitiesVerified = activities;
  return values === expectedIssues.size && values > 0 && activities === outcome.issuesPatched && activities > 0;
}

// ------------------------------------------------------- files
const FILE_BYTES = crypto.getRandomValues(new Uint8Array(32 * 1024)); // 32 KiB
async function fileRoundTrip(index: number): Promise<{ ok: boolean; status?: number }> {
  // 1) presigned POST — v2 project-asset endpoint with entity context;
  // uploads must be images per baseline policy (a 32 KiB random PNG).
  const presign = await fetch(`${BASE_URL}/api/assets/v2/workspaces/${WS_SLUG}/projects/${PROJECT_ID}/`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({
      name: `bench-${args.label}-${index}.png`,
      type: "image/png",
      size: FILE_BYTES.byteLength,
      entity_identifier: createdIssues[(index * 7) % createdIssues.length] ?? PROJECT_ID,
      entity_type: createdIssues.length > 0 ? "ISSUE_ATTACHMENT" : "PROJECT_COVER",
    }),
  });
  if (!presign.ok) return { ok: false, status: presign.status };
  const { upload_data: uploadData, asset_id: assetId } = (await presign.json()) as {
    upload_data: { url: string; fields: Record<string, string> };
    asset_id: string;
  };

  // 2) upload bytes to silo through the presigned POST (proxy-fronted bucket route)
  const form = new FormData();
  for (const [k, v] of Object.entries(uploadData.fields)) form.append(k, v);
  form.append(
    "file",
    new Blob([FILE_BYTES], { type: "image/png" }),
    uploadData.fields.key?.split("/").pop() ?? "bench.png"
  );
  const upload = await fetch(uploadData.url, { method: "POST", body: form });
  if (!upload.ok) return { ok: false, status: upload.status };
  outcome.filesUploaded += 1;

  // 3) mark uploaded via the app API (v2 project-asset path)
  const mark = await fetch(`${BASE_URL}/api/assets/v2/workspaces/${WS_SLUG}/projects/${PROJECT_ID}/${assetId}/`, {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify({ is_uploaded: true }),
  });
  if (!mark.ok) return { ok: false, status: mark.status };

  // 4) durable check: signed download via the app API (what the browser
  // uses) — the raw bucket route is not anonymous-readable at baseline.
  const dlReq = await fetch(
    `${BASE_URL}/api/assets/v2/workspaces/${WS_SLUG}/projects/${PROJECT_ID}/download/${assetId}/`,
    { headers: appHeaders(), redirect: "manual" }
  );
  const dlUrl = dlReq.headers.get("location") ?? "";
  if (!dlUrl) return { ok: false, status: dlReq.status };
  const dl = await fetch(dlUrl);
  if (!dl.ok) return { ok: false, status: dl.status };
  const bytes = new Uint8Array(await dl.arrayBuffer());
  const match = bytes.byteLength === FILE_BYTES.byteLength && bytes.every((b, i) => b === FILE_BYTES[i]);
  if (match) outcome.filesRoundTripOk += 1;
  return { ok: match, status: dl.status };
}

// ------------------------------------------------------- queue depth
async function rabbitDepth(): Promise<number | null> {
  // bench vhost bench_plane from deploy/mq.env — it has NO leading slash, so
  // the URL must NOT carry the %2F prefix. /api/queues/bench_plane returns the
  // vhost's queue list; depth is the sum of `messages` across all queues
  // (drain gate = whole vhost empty; avoids a 404 on an unconfirmed queue
  // name). last_error is diagnostic only and never gates by itself.
  try {
    const res = await fetch("http://127.0.0.1:15672/api/queues/bench_plane", {
      headers: {
        Authorization: "Basic " + Buffer.from("bench_plane:bench-plane-pass-2").toString("base64"),
      },
    });
    if (!res.ok) {
      report.queue.last_error = "http " + res.status;
      return null;
    }
    const data = z.array(z.object({ messages: z.number().int().nonnegative() })).parse(await res.json());
    return data.reduce((total, queue) => total + queue.messages, 0);
  } catch (e) {
    report.queue.last_error = String(e).slice(0, 200);
    return null;
  }
}

// ------------------------------------------------------- schedule
const CYCLE = [
  "read-board-status",
  "read-issue-list",
  "read-issue-list",
  "read-board-priority",
  "write-issue-create",
  "write-issue-create",
  "write-issue-patch",
  "write-issue-patch",
  "write-issue-patch",
  "read-pages",
  "read-issue-list",
  "edit-page",
  "read-issue-list",
  "edit-page-reconnect",
  "read-board-status",
  "write-issue-patch",
  "read-issue-list",
  "edit-page",
  "read-pages",
  "file-roundtrip",
] as const;

let runStart = 0;
let runEnd = 0;
let editCount = 0;
function sleep(ms: number): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>();
  setTimeout(resolve, ms);
  return promise;
}

async function performOp(kind: (typeof CYCLE)[number], t: number, index: number): Promise<boolean> {
  switch (kind) {
    case "read-board-status":
      return op(kind, () => readBoard("state__group"), t);
    case "read-board-priority":
      return op(kind, () => readBoard("priority"), t);
    case "read-issue-list":
      return op(kind, () => readIssueList(index % 3), t);
    case "read-pages":
      return op(kind, () => readPages(), t);
    case "write-issue-create": {
      let ok = false;
      await op(
        kind,
        async () => {
          const r = await createIssue(index);
          if (r.id) createdIssues.push(r.id);
          ok = r.ok;
          return { ok: r.ok, status: r.status };
        },
        t
      );
      return ok;
    }
    case "write-issue-patch": {
      const id = seed.dataset.issue_ids[index % seed.dataset.issue_ids.length];
      return op(
        kind,
        async () => {
          const prior = issueChains.get(id) ?? Promise.resolve();
          const next = prior.then(() => patchIssue(id, index));
          issueChains.set(
            id,
            next.catch(() => undefined)
          );
          const r = await next;
          if (r.ok) outcome.issuesPatched++;
          return r;
        },
        t
      );
    }
    case "edit-page":
    case "edit-page-reconnect": {
      const editorId = String(editCount++ % 5);
      return actor.run(editors.get(editorId)!.user, () =>
        op(
          kind,
          async () => {
            const prior = editorChains.get(editorId) ?? Promise.resolve();
            const next = prior.then(() => pageEditViaWebSocket(editorId, kind === "edit-page-reconnect", index));
            editorChains.set(
              editorId,
              next.catch(() => undefined)
            );
            return next;
          },
          t
        )
      );
    }
    case "file-roundtrip":
      return op(kind, () => fileRoundTrip(index), t);
  }
}

async function main(): Promise<void> {
  report.started_at = new Date().toISOString();
  const pending = new Set<Promise<unknown>>();
  let sampling = false;
  let sampler: Promise<void> | undefined;
  try {
    for (const user of seed.users) sessions.push(await signIn(user));
    for (let index = 0; index < 5; index++)
      editors.set(String(index), await connectEditor(PAGE_IDS[0], sessions[index]));
    const schedule = arrivals({
      duration: args.duration,
      rate: args.rate,
      burstAfter: args["burst-after"],
      burstRate: args["burst-rate"],
      burstDuration: args["burst-duration"],
    });
    report.offered.planned_ops = schedule.length;
    runStart = Date.now();
    report.load_started_at = new Date(runStart).toISOString();
    sampling = true;
    sampler = (async () => {
      while (sampling) {
        const t = (Date.now() - runStart) / 1000;
        const depth = await rabbitDepth();
        report.queue.samples.push({ t, depth });
        if (depth !== null) report.queue.sampled_depths.push(depth);
        await sleep(2000);
      }
    })();
    for (const [index, t] of schedule.entries()) {
      await sleep(Math.max(0, runStart + t * 1000 - Date.now()));
      report.offered.max_dispatch_lag_ms = Math.max(
        report.offered.max_dispatch_lag_ms,
        Date.now() - runStart - t * 1000
      );
      if (pending.size >= args["max-inflight"]) {
        report.offered.rejected_ops++;
        report.offered.rejected.push({ kind: CYCLE[index % CYCLE.length], t, reason: "max-inflight" });
        continue;
      }
      const userIndex = (index + Math.floor(index / CYCLE.length)) % sessions.length;
      const task = actor.run(sessions[userIndex], () => performOp(CYCLE[index % CYCLE.length], t, index));
      pending.add(task);
      void task.finally(() => pending.delete(task));
    }
    const deadline = Date.now() + args.recovery * 1000;
    await Promise.race([
      Promise.all(pending),
      sleep(args.recovery * 1000).then(() => {
        if (pending.size) throw new Error(`recovery expired with ${pending.size} operations pending`);
      }),
    ]);
    runEnd = Date.now();
    report.load_completed_at = new Date(runEnd).toISOString();
    let pagesOk = false;
    let issuesOk = false;
    let zeroSince: number | null = null;
    while (Date.now() < deadline) {
      if (!pagesOk) pagesOk = await verifyPageBinaries();
      if (!issuesOk) issuesOk = await verifyIssuesAndActivities();
      const last = report.queue.samples.at(-1);
      if (last?.depth === 0 && (Date.now() - runStart) / 1000 - last.t < 5) zeroSince ??= Date.now();
      else zeroSince = null;
      if (pagesOk && issuesOk && zeroSince !== null && Date.now() - zeroSince >= 4000) {
        report.queue.drained = true;
        report.queue.drain_s = Math.max(0, last!.t - args.duration);
        break;
      }
      await sleep(2000);
    }
    if (pagesOk) {
      for (const [editorId, editor] of editors) {
        editor.provider.destroy();
        editor.doc.destroy();
        const rebuilt = await connectEditor(editor.pageId, editor.user);
        editors.set(editorId, rebuilt);
        if (missingMarkers(rebuilt.doc.getXmlFragment("default").toString(), markers.get(editor.pageId)!).length)
          pagesOk = false;
      }
      if (pagesOk) outcome.reconnectMarkers = outcome.persistedMarkers;
    }
    summarize();
    const g = report.gate.failures;
    g.push(...workloadFailures(schedule.length, ops.length, report.achieved.errors, report.offered.rejected_ops));
    if (new Set(ops.map((record) => record.actor_id)).size !== 20) g.push("active-user-count-not-20");
    if (report.offered.max_dispatch_lag_ms > 100) g.push("arrival-dispatch-lag-over-100ms");
    if (!pagesOk) g.push("persisted-or-reconnected-edit-missing");
    if (!issuesOk) g.push("issue-value-or-background-activity-missing");
    if (!outcome.filesUploaded || outcome.filesRoundTripOk !== outcome.filesUploaded) g.push("file-roundtrip-mismatch");
    if (!report.queue.samples.some((s) => s.t < args.duration)) g.push("queue-load-unmeasured");
    if (report.queue.samples.some((s) => s.depth === null)) g.push("queue-observation-missing");
    if (!report.queue.drained) g.push("queue-or-durable-work-not-recovered");
    report.gate.pass = g.length === 0;
  } catch (error) {
    report.gate.failures.push(`aborted:${String(error).slice(0, 200)}`);
    report.gate.pass = false;
    runEnd = Date.now();
    if (runStart) summarize();
  } finally {
    sampling = false;
    await sampler;
    for (const editor of editors.values()) {
      editor.provider.destroy();
      editor.doc.destroy();
    }
    report.finished_at = new Date().toISOString();
    const outDir = join(HERE, "../../bench-results/p0-workload");
    mkdirSync(outDir, { recursive: true });
    const out = join(outDir, `${args.label}-${Date.now()}.json`);
    writeFileSync(out, JSON.stringify({ ...report, operations: ops }, null, 2));
    console.log(`report: ${out}`);
    console.log(`gate: ${report.gate.pass ? "PASS" : "FAIL"} ${report.gate.failures.join(", ")}`);
  }
  process.exit(report.gate.pass ? 0 : 2);
}

void main();
