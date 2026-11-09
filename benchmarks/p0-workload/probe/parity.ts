/**
 * Consumer-visible parity matrix for the grouped issue list endpoint.
 *
 * Captures status + response digest for cursor/offset/group/empty-group cases so
 * a paginator change can be compared before and after on the real endpoint.
 * Scratch diagnostic only; no application behavior of its own.
 */
import { parseArgs } from "node:util";
import { createHash } from "node:crypto";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE_URL = "http://127.0.0.1:8080";
const vals = parseArgs({
  args: process.argv.slice(2),
  options: {
    label: { type: "string" },
    out: { type: "string" },
    "seed-file": { type: "string" },
  },
  strict: true,
}).values as Record<string, string | undefined>;
const seed = JSON.parse(readFileSync(vals["seed-file"] ?? join(HERE, "../seed/user.json"), "utf8")) as {
  users: Array<{ email: string; password: string }>;
  dataset: { workspace_slug: string; project_id: string };
};

async function signIn(user: { email: string; password: string }) {
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
  return { cookie, csrf };
}

const cases: Array<{ name: string; query: string }> = [
  { name: "board-default-perpage", query: "group_by=state__group&order_by=created_at" },
  { name: "board-priority", query: "group_by=priority&order_by=created_at" },
  { name: "board-perpage-50", query: "group_by=state__group&order_by=created_at&per_page=50" },
  { name: "board-page-2", query: "group_by=state__group&order_by=created_at&per_page=50&cursor=50:1:0" },
  { name: "board-page-3", query: "group_by=priority&order_by=created_at&per_page=5&cursor=5:2:0" },
  { name: "board-past-end", query: "group_by=state__group&order_by=created_at&per_page=50&cursor=50:999:0" },
  { name: "board-subgroup", query: "group_by=state__group&sub_group_by=priority&order_by=created_at&per_page=10" },
  { name: "plain-page-2", query: "order_by=created_at&per_page=50&cursor=50:1:0" },
];

function summary(body: unknown) {
  if (Array.isArray(body)) return { list_length: body.length };
  if (!body || typeof body !== "object") return { raw: body };
  const b = body as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const key of [
    "total_count",
    "total_results",
    "count",
    "total_pages",
    "grouped_by",
    "sub_grouped_by",
    "next_cursor",
    "prev_cursor",
    "next_page_results",
    "prev_page_results",
    "error",
  ]) {
    if (key in b) out[key] = b[key];
  }
  const results = b.results;
  if (Array.isArray(results)) out.list_length = results.length;
  else if (results && typeof results === "object") {
    const groups = results as Record<string, { results?: unknown[]; total_results?: unknown }>;
    out.groups = Object.fromEntries(
      Object.entries(groups).map(([key, value]) => [
        key,
        { n: Array.isArray(value?.results) ? value.results.length : null, total_results: value?.total_results },
      ])
    );
  }
  return out;
}

async function main() {
  const session = await signIn(seed.users[0]);
  const base = `${BASE_URL}/api/workspaces/${seed.dataset.workspace_slug}/projects/${seed.dataset.project_id}/issues/`;
  const headers = { Cookie: session.cookie, Referer: BASE_URL + "/", Origin: BASE_URL, "X-CSRFToken": session.csrf };
  const report: Record<string, unknown> = { label: vals.label ?? "parity", cases: {} };
  for (const testCase of cases) {
    const rows: unknown[] = [];
    for (let round = 0; round < 2; round++) {
      const res = await fetch(`${base}?${testCase.query}`, { headers });
      const text = await res.text();
      let body: unknown;
      try {
        body = JSON.parse(text);
      } catch {
        body = text.slice(0, 200);
      }
      rows.push({
        status: res.status,
        sha256: createHash("sha256").update(text).digest("hex"),
        bytes: text.length,
        summary: summary(body),
      });
    }
    (report.cases as Record<string, unknown>)[testCase.name] = { query: testCase.query, rounds: rows };
  }
  mkdirSync("/tmp/plane-probe", { recursive: true });
  const out = vals.out ?? join("/tmp/plane-probe", `${vals.label ?? "parity"}-${Date.now()}.json`);
  writeFileSync(out, JSON.stringify(report, null, 1));
  for (const [name, value] of Object.entries(
    report.cases as Record<string, { rounds: Array<{ status: number; summary: Record<string, unknown> }> }>
  )) {
    const r = value.rounds[0];
    console.log(`${name.padEnd(20)} status=${r.status} ${JSON.stringify(r.summary).slice(0, 200)}`);
  }
  console.log(`parity report: ${out}`);
}

void main().catch((e) => {
  console.error(String(e));
  process.exit(1);
});
