/**
 * Deterministic real-path repro: one failed page store followed by a later
 * acknowledged edit.
 *
 * Real Yjs document + real @hocuspocus/provider against the running Live
 * service; the store failure is forced by pausing the API container while the
 * debounced store request is in flight (Live's own axios timeout is 20 s).
 * Persisted state is read from GET /pages/{id}/description/ before and after.
 *
 * Usage: bun probe/store-wedge-repro.ts --label before --pause-seconds 25
 * Output: /tmp/plane-probe/store-wedge-<label>.json
 */
import { parseArgs } from "node:util";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { spawn, spawnSync } from "node:child_process";
import type { Doc } from "yjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const reqEditor = createRequire(join(HERE, "../../../packages/editor/package.json"));
const Y = reqEditor("yjs") as typeof import("yjs");
const { HocuspocusProvider } = reqEditor("@hocuspocus/provider") as typeof import("@hocuspocus/provider");

const BASE_URL = "http://127.0.0.1:8080";
const LIVE_WS = BASE_URL.replace("http", "ws") + "/live/collaboration";
const vals = parseArgs({
  args: process.argv.slice(2),
  options: {
    label: { type: "string" },
    "seed-file": { type: "string" },
    "pause-seconds": { type: "string" },
    "persist-wait": { type: "string" },
    mode: { type: "string" },
  },
  strict: true,
}).values as Record<string, string | undefined>;

const label = vals.label ?? "repro";
const mode = vals.mode ?? "later-edit";
const pauseSeconds = Number(vals["pause-seconds"] ?? 25);
const persistWait = Number(vals["persist-wait"] ?? 40);
const seed = JSON.parse(readFileSync(vals["seed-file"] ?? join(HERE, "../seed/user.json"), "utf8")) as {
  users: Array<{ email: string; password: string }>;
  dataset: { workspace_slug: string; project_id: string; page_ids: string[] };
};
const pageId = seed.dataset.page_ids[0];
const seed0 = seed.users[0];

type Session = { cookie: string; csrf: string; id: string };
const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

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

async function signOut(session: Session): Promise<number> {
  const res = await fetch(`${BASE_URL}/auth/sign-out/`, {
    method: "POST",
    redirect: "manual",
    headers: { Cookie: session.cookie, Referer: BASE_URL + "/", Origin: BASE_URL, "X-CSRFToken": session.csrf },
  });
  return res.status;
}

let doc: Doc;
let provider: InstanceType<typeof HocuspocusProvider>;

async function connect(user: Session): Promise<void> {
  doc = new Y.Doc();
  const params = new URLSearchParams({
    documentType: "project_page",
    workspaceSlug: seed.dataset.workspace_slug,
    projectId: seed.dataset.project_id,
  });
  provider = new HocuspocusProvider({
    url: `${LIVE_WS}?${params}`,
    name: pageId,
    document: doc,
    token: JSON.stringify({ id: user.id, cookie: user.cookie }),
  });
  const synced = Promise.withResolvers<void>();
  const timer = setTimeout(() => synced.reject(new Error("editor sync timeout")), 15000);
  provider.on("synced", () => {
    clearTimeout(timer);
    synced.resolve();
  });
  await synced.promise;
}

async function appendMarkerOn(
  target: Doc,
  targetProvider: InstanceType<typeof HocuspocusProvider>,
  marker: string
): Promise<number> {
  const started = Date.now();
  const para = new Y.XmlElement("paragraph");
  para.insert(0, [new Y.XmlText(marker)]);
  const body = target.getXmlFragment("default");
  body.insert(body.length, [para]);
  const deadline = Date.now() + 15000;
  while (targetProvider.hasUnsyncedChanges) {
    if (Date.now() >= deadline) throw new Error("ack timeout");
    await sleep(10);
  }
  return Date.now() - started;
}

async function appendMarker(marker: string): Promise<number> {
  return appendMarkerOn(doc, provider, marker);
}

function persistedText(session: Session): Promise<string | null> {
  const p = `/api/workspaces/${seed.dataset.workspace_slug}/projects/${seed.dataset.project_id}/pages/${pageId}/description/`;
  return fetch(BASE_URL + p, {
    headers: { Cookie: session.cookie, Referer: BASE_URL + "/", Origin: BASE_URL, "X-CSRFToken": session.csrf },
  }).then(async (res) => {
    if (!res.ok) return null;
    const d = new Y.Doc();
    try {
      Y.applyUpdate(d, new Uint8Array(await res.arrayBuffer()));
      return d.getXmlFragment("default").toString();
    } finally {
      d.destroy();
    }
  });
}

async function waitForMarkers(session: Session, markers: string[], timeoutMs: number) {
  const started = Date.now();
  const trace: Array<{ t: number; missing: string[] }> = [];
  for (;;) {
    const text = await persistedText(session);
    const missing = markers.filter((m) => !(text ?? "").includes(m));
    trace.push({ t: (Date.now() - started) / 1000, missing });
    if (missing.length === 0) return { ok: true, waited: (Date.now() - started) / 1000, trace };
    if (Date.now() - started >= timeoutMs) return { ok: false, waited: (Date.now() - started) / 1000, trace };
    await sleep(1000);
  }
}

function lastStoreEpochMs(lines: string[]): number | null {
  let last: number | null = null;
  for (const line of lines) {
    const m = /Store \\"|Store "/.test(line) ? /(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z)/.exec(line) : null;
    if (m) last = Date.parse(m[1]);
  }
  return last;
}

function dbHasMarker(pageId: string, marker: string): boolean {
  const sql = `select coalesce(description_html,'') from pages where id = '${pageId}'`;
  const res = docker(["exec", "plane-bench-db", "psql", "-U", "bench_plane", "-d", "bench_plane", "-tA", "-c", sql]);
  return res.out.includes(marker);
}

function docker(args: string[]) {
  const proc = spawnSync("docker", args, { encoding: "utf8" });
  return { code: proc.status, out: proc.stdout ?? "", err: proc.stderr ?? "" };
}

async function apiReady(timeoutMs = 60000): Promise<boolean> {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const res = await fetch(`${BASE_URL}/api/users/me/`, { signal: AbortSignal.timeout(3000) });
      if (res.status === 401 || res.status === 200) return true;
    } catch {}
    await sleep(1000);
  }
  return false;
}

async function forceFailure(
  markersBlocked: string
): Promise<{ paused: boolean; blockedAck: number; unpaused: boolean; pausedFor: number; inWindowPersisted: boolean }> {
  const paused = docker(["pause", "plane-bench-api"]).code === 0;
  const pausedAt = Date.now();
  await sleep(1500);
  const blockedAck = await appendMarker(markersBlocked);
  const checkAtMs = Math.max(0, (pauseSeconds - 2) * 1000);
  await sleep(checkAtMs);
  // Inside the failure window: the API is still frozen and the store attempt has
  // already failed, so this is the acknowledged-but-not-persisted state.
  const inWindowPersisted = dbHasMarker(pageId, markersBlocked);
  await sleep(Math.max(0, pauseSeconds * 1000 - checkAtMs));
  const unpaused = docker(["unpause", "plane-bench-api"]).code === 0;
  return { paused, blockedAck, unpaused, pausedFor: (Date.now() - pausedAt) / 1000, inWindowPersisted };
}

async function main() {
  const stamp = Date.now();
  const markers = {
    before: `repro-${label}-a-${stamp}`,
    blocked: `repro-${label}-b-${stamp}`,
    after: `repro-${label}-c-${stamp}`,
    second: `repro-${label}-d-${stamp}`,
  };
  const logStart = new Date().toISOString().replace(/\.\d+Z$/, "Z");

  const session = await signIn(seed0);
  await connect(session);
  let second: { doc: Doc; provider: InstanceType<typeof HocuspocusProvider>; userId: string } | null = null;
  if (mode === "concurrent") {
    const session2 = await signIn(seed.users[1]);
    const doc2 = new Y.Doc();
    const params = new URLSearchParams({
      documentType: "project_page",
      workspaceSlug: seed.dataset.workspace_slug,
      projectId: seed.dataset.project_id,
    });
    const provider2 = new HocuspocusProvider({
      url: `${LIVE_WS}?${params}`,
      name: pageId,
      document: doc2,
      token: JSON.stringify({ id: session2.id, cookie: session2.cookie }),
    });
    const synced2 = Promise.withResolvers<void>();
    const timer2 = setTimeout(() => synced2.reject(new Error("second editor sync timeout")), 15000);
    provider2.on("synced", () => {
      clearTimeout(timer2);
      synced2.resolve();
    });
    await synced2.promise;
    second = { doc: doc2, provider: provider2, userId: session2.id };
  }
  const ackBefore = await appendMarker(markers.before);
  const before = await waitForMarkers(session, [markers.before], persistWait * 1000);

  if (second) await appendMarkerOn(second.doc, second.provider, markers.second);
  if (mode === "close-api-down") {
    // Close while the API is still frozen: the final unload attempt must fail,
    // be logged/counted, and the unload must still proceed.
    const paused = docker(["pause", "plane-bench-api"]).code === 0;
    await sleep(1500);
    const ack = await appendMarker(markers.blocked);
    await sleep(pauseSeconds * 1000); // budget exhausted, API still down
    provider.destroy();
    doc.destroy();
    await sleep(12000); // final unload attempt + unload
    const persistedWhileDown = dbHasMarker(pageId, markers.blocked);
    const unpaused = docker(["unpause", "plane-bench-api"]).code === 0;
    await apiReady();
    await sleep(3000);
    const persistedAfterRecovery = dbHasMarker(pageId, markers.blocked);
    const logsDown = docker(["logs", "plane-bench-live", "--since", logStart])
      .out.split("\n")
      .filter((line: string) => /page_store_loss|Refusing|Document unloaded|still in memory|attempt 1\/2/i.test(line));
    // Real unload probe: a fresh connection syncs from the server's in-memory
    // document if it is still loaded, or from the DB if it was unloaded. The
    // marker is absent from the DB (the store failed), so its presence here
    // means the dirty document survived in memory.
    const observerDoc = new Y.Doc();
    const observerParams = new URLSearchParams({
      documentType: "project_page",
      workspaceSlug: seed.dataset.workspace_slug,
      projectId: seed.dataset.project_id,
    });
    const observerProvider = new HocuspocusProvider({
      url: `${LIVE_WS}?${observerParams}`,
      name: pageId,
      document: observerDoc,
      token: JSON.stringify({ id: session.id, cookie: session.cookie }),
    });
    const observerSynced = Promise.withResolvers<void>();
    const observerTimer = setTimeout(() => observerSynced.reject(new Error("observer sync timeout")), 15000);
    observerProvider.on("synced", () => {
      clearTimeout(observerTimer);
      observerSynced.resolve();
    });
    await observerSynced.promise;
    const serverDocHasMarker = observerDoc.getXmlFragment("default").toString().includes(markers.blocked);
    observerProvider.destroy();
    observerDoc.destroy();
    const reportDown = {
      label,
      mode,
      page_id: pageId,
      markers,
      paused,
      ack_ms: ack,
      unpaused,
      persisted_while_down: persistedWhileDown,
      loss_logged: logsDown.some((l: string) => l.includes("page_store_loss")),
      refusal_logged: logsDown.some((l: string) => l.includes("Refusing")),
      server_doc_has_marker_after_reload: serverDocHasMarker,
      persisted_after_recovery: persistedAfterRecovery,
      force_close_unload_success_logged: logsDown.some((l: string) => l.includes("Document unloaded")),
      force_close_still_in_memory_logged: logsDown.some((l: string) => l.includes("still in memory")),
      field_semantics: {
        persisted_while_down: "DB read while the API was frozen: the acknowledged edit is not persisted",
        loss_logged: "page_store_loss line emitted by the final unload attempt",
        refusal_logged: "the removed refuse-unload path must never appear",
        server_doc_has_marker_after_reload:
          "fresh connection after recovery; compare with persisted_after_recovery to tell a late server-side write from an in-memory pin",
        persisted_after_recovery:
          "DB read after recovery: true means an aborted request completed server-side and the edit is durable after all",
        force_close_unload_success_logged:
          "only the force-close handler logs this; the normal unload path does not, so false is not evidence of anything",
        force_close_still_in_memory_logged:
          "only the force-close handler logs this; false means the force-close handler did not report a pinned document",
      },
      logs: logsDown.slice(-20),
    };
    mkdirSync("/tmp/plane-probe", { recursive: true });
    const outDown = join("/tmp/plane-probe", `store-wedge-${label}.json`);
    writeFileSync(outDown, JSON.stringify(reportDown, null, 1));
    console.log(
      `repro ${label} mode=close-api-down persisted_while_down=${reportDown.persisted_while_down} ` +
        `loss_logged=${reportDown.loss_logged} refusal=${reportDown.refusal_logged} ` +
        `server_doc_has_marker_after_reload=${reportDown.server_doc_has_marker_after_reload} persisted_after_recovery=${reportDown.persisted_after_recovery}`
    );
    console.log(`report: ${outDown}`);
    return;
  }

  if (mode === "drain-api-down") {
    const paused = docker(["pause", "plane-bench-api"]).code === 0;
    await sleep(1500);
    const ack = await appendMarker(markers.blocked);
    await sleep(12000); // retry in flight, API stays down
    const stopStartedAt = Date.now();
    const stop = spawn("docker", ["stop", "-t", "70", "plane-bench-live"], { stdio: "ignore" });
    const exitCode = await new Promise<number | null>((resolve) => stop.on("exit", (code) => resolve(code)));
    const drainSeconds = (Date.now() - stopStartedAt) / 1000;
    const persistedWhileDown = dbHasMarker(pageId, markers.blocked);
    const logsDrainDown = docker(["logs", "plane-bench-live", "--since", logStart])
      .out.split("\n")
      .filter((line: string) =>
        /page_store_loss|Refusing|drain|Document unloaded|still in memory|attempt 1\/2/i.test(line)
      );
    const unpaused = docker(["unpause", "plane-bench-api"]).code === 0;
    docker(["start", "plane-bench-live"]);
    await apiReady();
    const reportDrainDown = {
      label,
      mode,
      page_id: pageId,
      markers,
      paused,
      ack_ms: ack,
      unpaused,
      stop_exit: exitCode,
      drain_seconds: drainSeconds,
      drain_deadline_s: 60,
      deadline_margin_s: 60 - drainSeconds,
      persisted_while_down: persistedWhileDown,
      loss_logged: logsDrainDown.some((l: string) => l.includes("page_store_loss")),
      logs: logsDrainDown.slice(-20),
    };
    mkdirSync("/tmp/plane-probe", { recursive: true });
    const outDrainDown = join("/tmp/plane-probe", `store-wedge-${label}.json`);
    writeFileSync(outDrainDown, JSON.stringify(reportDrainDown, null, 1));
    console.log(
      `repro ${label} mode=drain-api-down stop_exit=${exitCode} drain_s=${drainSeconds} ` +
        `persisted_while_down=${persistedWhileDown} loss_logged=${reportDrainDown.loss_logged}`
    );
    console.log(`report: ${outDrainDown}`);
    return;
  }

  if (mode === "locked-4xx") {
    const lockUrl = `${BASE_URL}/api/workspaces/${seed.dataset.workspace_slug}/projects/${seed.dataset.project_id}/pages/${pageId}/lock/`;
    const lock = await fetch(lockUrl, {
      method: "POST",
      headers: { Cookie: session.cookie, Referer: BASE_URL + "/", Origin: BASE_URL, "X-CSRFToken": session.csrf },
    });
    const ack = await appendMarker(markers.blocked);
    await sleep(25000);
    const text = await persistedText(session);
    const logs4xx = docker(["logs", "plane-bench-live", "--since", logStart])
      .out.split("\n")
      .filter((line: string) =>
        /attempt 1\/2|attempt 2\/2|beforeUnloadDocument|Refusing|PAGE_LOCKED|status code 400|page_store_loss/i.test(
          line
        )
      );
    const unlock = await fetch(lockUrl, {
      method: "DELETE",
      headers: { Cookie: session.cookie, Referer: BASE_URL + "/", Origin: BASE_URL, "X-CSRFToken": session.csrf },
    });
    const report4xx = {
      label,
      mode,
      page_id: pageId,
      markers,
      lock_status: lock.status,
      unlock_status: unlock.status,
      ack_ms: ack,
      persisted: { blocked: (text ?? "").includes(markers.blocked) },
      attempts_logged: logs4xx.filter((l: string) => l.includes("attempt ")).length,
      unload_attempt_logged: logs4xx.some((l: string) => l.includes("beforeUnloadDocument")),
      refusal_logged: logs4xx.some((l: string) => l.includes("Refusing")),
      loss_logged: logs4xx.some((l: string) => l.includes("page_store_loss")),
      logs: logs4xx.slice(-30),
    };
    mkdirSync("/tmp/plane-probe", { recursive: true });
    const out4xx = join("/tmp/plane-probe", `store-wedge-${label}.json`);
    writeFileSync(out4xx, JSON.stringify(report4xx, null, 1));
    console.log(
      `repro ${label} mode=locked-4xx lock=${lock.status} unlock=${unlock.status} attempts=${report4xx.attempts_logged} ` +
        `unload_attempt=${report4xx.unload_attempt_logged} refusal=${report4xx.refusal_logged} loss=${report4xx.loss_logged} persisted=${report4xx.persisted.blocked}`
    );
    console.log(`report: ${out4xx}`);
    provider.destroy();
    doc.destroy();
    return;
  }

  if (mode === "nonretryable-4xx") {
    const ack = await appendMarker(markers.blocked);
    const signOutStatus = await signOut(session);
    await sleep(25000);
    const text = await persistedText(session);
    const logs4xx = docker(["logs", "plane-bench-live", "--since", logStart])
      .out.split("\n")
      .filter((line: string) =>
        /attempt 1\/2|attempt 2\/2|beforeUnloadDocument|Refusing|status code|Discarding/i.test(line)
      );
    const report4xx = {
      label,
      mode,
      page_id: pageId,
      markers,
      ack_ms: ack,
      sign_out_status: signOutStatus,
      persisted: { blocked: (text ?? "").includes(markers.blocked) },
      attempts_logged: logs4xx.filter((l: string) => l.includes("attempt ")).length,
      unload_attempt_logged: logs4xx.some((l: string) => l.includes("beforeUnloadDocument")),
      refusal_logged: logs4xx.some((l: string) => l.includes("Refusing")),
      logs: logs4xx.slice(-30),
    };
    mkdirSync("/tmp/plane-probe", { recursive: true });
    const out4xx = join("/tmp/plane-probe", `store-wedge-${label}.json`);
    writeFileSync(out4xx, JSON.stringify(report4xx, null, 1));
    console.log(
      `repro ${label} mode=nonretryable-4xx signout=${signOutStatus} attempts=${report4xx.attempts_logged} ` +
        `unload_attempt=${report4xx.unload_attempt_logged} refusal=${report4xx.refusal_logged} persisted=${report4xx.persisted.blocked}`
    );
    console.log(`report: ${out4xx}`);
    provider.destroy();
    doc.destroy();
    return;
  }

  if (mode === "oversize") {
    const huge = "x".repeat(6_000_000);
    const started = Date.now();
    const body = doc.getXmlFragment("default");
    const para = new Y.XmlElement("paragraph");
    para.insert(0, [new Y.XmlText(huge)]);
    body.insert(body.length, [para]);
    const deadline = Date.now() + 60000;
    while (provider.hasUnsyncedChanges) {
      if (Date.now() >= deadline) break;
      await sleep(50);
    }
    await sleep(25000);
    const logs413 = docker(["logs", "plane-bench-live", "--since", logStart])
      .out.split("\n")
      .filter((line: string) => /413|too large|attempt 1\/2|attempt 2\/2|force|Force|content_too_large/i.test(line));
    const reportOversize = {
      label,
      mode,
      page_id: pageId,
      markers,
      ack_ms: Date.now() - started,
      provider_attached_after: provider.isAttached,
      logs: logs413.slice(-40),
    };
    mkdirSync("/tmp/plane-probe", { recursive: true });
    const outOversize = join("/tmp/plane-probe", `store-wedge-${label}.json`);
    writeFileSync(outOversize, JSON.stringify(reportOversize, null, 1));
    console.log(`repro ${label} mode=oversize attached_after=${provider.isAttached} log_lines=${logs413.length}`);
    console.log(`report: ${outOversize}`);
    provider.destroy();
    doc.destroy();
    return;
  }

  if (mode === "drain") {
    const paused = docker(["pause", "plane-bench-api"]).code === 0;
    await sleep(1500);
    const ack = await appendMarker(markers.blocked);
    await sleep(12000); // store is now in flight and will time out unless the API returns
    const stopStartedAt = Date.now();
    const stop = spawn("docker", ["stop", "-t", "70", "plane-bench-live"], { stdio: "ignore" });
    await sleep(5000);
    const unpaused = docker(["unpause", "plane-bench-api"]).code === 0;
    const exitCode = await new Promise<number | null>((resolve) => stop.on("exit", (code) => resolve(code)));
    const drainSeconds = (Date.now() - stopStartedAt) / 1000;
    const storeLines = docker(["logs", "plane-bench-live", "--since", logStart])
      .out.split("\n")
      .filter((line: string) => /Store |attempt 1\/2|attempt 2\/2|timeout/i.test(line));
    docker(["start", "plane-bench-live"]);
    // wait for Live to listen again
    const readyDeadline = Date.now() + 60000;
    while (Date.now() < readyDeadline) {
      const probe = docker(["logs", "--tail", "1", "plane-bench-live"]);
      if (probe.out.length > 0) break;
      await sleep(1000);
    }
    await sleep(6000);
    const persisted = ((await persistedText(session)) ?? "").includes(markers.blocked);
    const reportDrain = {
      label,
      mode,
      page_id: pageId,
      markers,
      paused,
      ack_ms: ack,
      unpaused,
      stop_exit: exitCode,
      drain_seconds: drainSeconds,
      drain_deadline_s: 60,
      deadline_margin_s: 60 - drainSeconds,
      shutdown_timeout_s: 25,
      stop_started_at: new Date(stopStartedAt).toISOString(),
      last_store_at: lastStoreEpochMs(storeLines) ? new Date(lastStoreEpochMs(storeLines)!).toISOString() : null,
      in_flight_at_stop: (() => {
        const last = lastStoreEpochMs(storeLines);
        return last !== null && stopStartedAt - last >= 0 && stopStartedAt - last < 20000;
      })(),
      persisted,
      store_lines: storeLines.slice(-20),
      logs: docker(["logs", "plane-bench-live", "--since", logStart])
        .out.split("\n")
        .filter((line: string) => /Store|timeout|drain|persist|Refusing|discard|attempt/i.test(line))
        .slice(-40),
    };
    mkdirSync("/tmp/plane-probe", { recursive: true });
    const outDrain = join("/tmp/plane-probe", `store-wedge-${label}.json`);
    writeFileSync(outDrain, JSON.stringify(reportDrain, null, 1));
    console.log(`repro ${label} mode=drain stop_exit=${exitCode} persisted=${persisted}`);
    console.log(`report: ${outDrain}`);
    provider.destroy();
    doc.destroy();
    return;
  }

  const fail1 = await forceFailure(markers.blocked);
  const apiReadyAfter = await apiReady();
  await sleep(2000);
  const blockedImmediately = ((await persistedText(session)) ?? "").includes(markers.blocked);

  let fail2: Awaited<ReturnType<typeof forceFailure>> | null = null;
  let ackAfter: number | null = null;
  if (mode === "repeat-failure") {
    ackAfter = await appendMarker(markers.after);
    fail2 = await forceFailure(markers.second);
    await apiReady();
    await sleep(2000);
  } else if (mode === "later-edit") {
    ackAfter = await appendMarker(markers.after);
  } else if (mode === "concurrent") {
    ackAfter = await appendMarkerOn(second!.doc, second!.provider, markers.after);
  }

  // Close the client exactly here in close/repeat modes (last disconnect).
  if (mode === "close" || mode === "repeat-failure" || mode === "exhaust-close") {
    provider.destroy();
    doc.destroy();
  }

  const watch =
    mode === "repeat-failure"
      ? [markers.blocked, markers.after, markers.second]
      : mode === "concurrent"
        ? [markers.before, markers.second, markers.blocked, markers.after]
        : mode === "later-edit"
          ? [markers.blocked, markers.after]
          : [markers.blocked];
  const recovered = await waitForMarkers(session, watch, (mode === "no-edit" ? 30 : persistWait) * 1000);
  const finalText = (await persistedText(session)) ?? "";
  const logs = docker(["logs", "plane-bench-live", "--since", logStart])
    .out.split("\n")
    .filter((line: string) => /Store|timeout of 20000ms|Error in updating document|UNHANDLED/.test(line))
    .slice(-40);

  if (mode !== "close" && mode !== "repeat-failure") {
    provider.destroy();
    doc.destroy();
  }
  if (second) {
    second.provider.destroy();
    second.doc.destroy();
  }
  const editors =
    mode === "concurrent"
      ? [
          { user_id: session.id, markers: [markers.before, markers.blocked, markers.after] },
          { user_id: (second as { userId?: string } | null)?.userId ?? "second", markers: [markers.second] },
        ]
      : [{ user_id: session.id, markers: [markers.before, markers.blocked, markers.after] }];
  const report = {
    label,
    mode,
    page_id: pageId,
    markers,
    editors,
    pause_seconds: pauseSeconds,
    ack_before_ms: ackBefore,
    before,
    fail1,
    api_ready_after_unpause: apiReadyAfter,
    blocked_persisted_immediately: blockedImmediately,
    ack_after_ms: ackAfter,
    fail2,
    recovered,
    persisted: Object.fromEntries(watch.map((m) => [m, finalText.includes(m)])),
    logs,
  };
  mkdirSync("/tmp/plane-probe", { recursive: true });
  const out = join("/tmp/plane-probe", `store-wedge-${label}.json`);
  writeFileSync(out, JSON.stringify(report, null, 1));
  console.log(
    `repro ${label} mode=${mode}: before_ok=${before.ok} recovered=${recovered.ok} ` +
      `waited=${recovered.waited}s persisted=${JSON.stringify(report.persisted)}`
  );
  console.log(`report: ${out}`);
}

void main().catch((e) => {
  console.error(String(e));
  process.exit(1);
});
