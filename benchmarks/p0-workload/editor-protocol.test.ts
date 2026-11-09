// Real-runtime proof for the editor reconnect lifecycle (F1). Needs the
// plane-bench stack and a private seed file; skipped by default so collection
// never touches fixtures. Enable with P0_LIVE_TEST=1.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { describe, test } from "node:test";
import type { Doc } from "yjs";
import { assertAttached, reconnectEditorInto, type EditorConnection } from "./editor-reconnect";

const HERE = dirname(fileURLToPath(import.meta.url));
const reqEditor = createRequire(join(HERE, "../../packages/editor/package.json"));
const Y = reqEditor("yjs") as typeof import("yjs");
const { HocuspocusProvider } = reqEditor("@hocuspocus/provider") as typeof import("@hocuspocus/provider");

const BASE_URL = "http://127.0.0.1:8080";
const LIVE_WS = BASE_URL.replace("http", "ws") + "/live/collaboration";
const live = process.env.P0_LIVE_TEST === "1";
const opts = { skip: live ? false : "set P0_LIVE_TEST=1 with the plane-bench stack up", timeout: 60_000 };

type Session = { cookie: string; csrf: string; id: string };
type Editor = EditorConnection<Session>;
type Seed = {
  users: Array<{ email: string; password: string }>;
  dataset: { workspace_slug: string; project_id: string; page_ids: string[] };
};

function seed(): Seed {
  return JSON.parse(readFileSync(process.env.P0_SEED_FILE ?? join(HERE, "seed/user.json"), "utf8")) as Seed;
}

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

async function connect(url: string, pageId: string, user: Session, timeoutMs = 15_000): Promise<Editor> {
  const data = seed();
  const doc = new Y.Doc();
  const params = new URLSearchParams({
    documentType: "project_page",
    workspaceSlug: data.dataset.workspace_slug,
    projectId: data.dataset.project_id,
  });
  const provider = new HocuspocusProvider({
    url: `${url}?${params}`,
    name: pageId,
    document: doc,
    token: JSON.stringify({ id: user.id, cookie: user.cookie }),
  });
  const synced = Promise.withResolvers<void>();
  const timer = setTimeout(() => synced.reject(new Error("editor sync timeout")), timeoutMs);
  provider.on("synced", () => {
    clearTimeout(timer);
    synced.resolve();
  });
  try {
    await synced.promise;
  } catch (error) {
    provider.destroy();
    doc.destroy();
    throw error;
  }
  return { pageId, doc, provider, user };
}

async function appendMarker(editor: Editor, marker: string): Promise<void> {
  const para = new Y.XmlElement("paragraph");
  para.insert(0, [new Y.XmlText(marker)]);
  const body = editor.doc.getXmlFragment("default");
  body.insert(body.length, [para]);
  const deadline = Date.now() + 15_000;
  while (editor.provider.hasUnsyncedChanges) {
    if (Date.now() >= deadline) throw new Error("edit acknowledgement timeout");
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

/** Page text as the server currently holds it, read by a fresh connection. */
async function serverPageText(pageId: string, user: Session): Promise<string> {
  const observer = await connect(LIVE_WS, pageId, user);
  try {
    return observer.doc.getXmlFragment("default").toString();
  } finally {
    observer.provider.destroy();
    observer.doc.destroy();
  }
}

async function eventuallyPersisted(pageId: string, user: Session, marker: string, timeoutMs = 45_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!(await persistedPageText(pageId, user)).includes(marker)) {
    if (Date.now() > deadline) throw new Error(`marker not persisted: ${marker}`);
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

async function persistedPageText(pageId: string, user: Session): Promise<string> {
  const data = seed();
  const res = await fetch(
    `${BASE_URL}/api/workspaces/${data.dataset.workspace_slug}/projects/${data.dataset.project_id}/pages/${pageId}/description/`,
    { headers: { Cookie: user.cookie, Referer: BASE_URL + "/", Origin: BASE_URL, "X-CSRFToken": user.csrf } }
  );
  if (!res.ok) throw new Error(`persisted page read: HTTP ${res.status}`);
  const doc = new Y.Doc();
  try {
    Y.applyUpdate(doc, new Uint8Array(await res.arrayBuffer()));
    return doc.getXmlFragment("default").toString();
  } finally {
    doc.destroy();
  }
}

describe("editor reconnect lifecycle", () => {
  const cleanup = (editor: Editor) => {
    editor.provider.destroy();
    editor.doc.destroy();
  };

  test("legacy order (reproduced manually): an edit accepted through a detached provider is lost", opts, async () => {
    const data = seed();
    const pageId = data.dataset.page_ids[0];
    const user = await signIn(data.users[0]);
    const stamp = Date.now();
    const editor = await connect(LIVE_WS, pageId, user);
    try {
      await appendMarker(editor, `legacy-before-${stamp}`);
      // This reproduces the pre-fix call order by hand (destroy, then connect);
      // it does not execute the old runner binary.
      editor.provider.destroy();
      editor.doc.destroy();
      await assert.rejects(connect("ws://127.0.0.1:9", pageId, user, 2000));
      // The surviving map entry is detached; its ack loop counts nothing.
      const marker = `legacy-lost-${stamp}`;
      const body = editor.doc.getXmlFragment("default");
      body.insert(body.length, [new Y.XmlText(marker)]);
      assert.equal(editor.provider.isAttached, false);
      assert.equal(editor.provider.hasUnsyncedChanges, false);
      assert.throws(() => assertAttached(editor), /editor connection detached/);
      // The "accepted" marker never reaches the server document.
      assert.equal((await serverPageText(pageId, user)).includes(marker), false);
    } finally {
      cleanup(editor);
    }
  });

  test("failed replacement keeps the map's live connection and its next edit is durable", opts, async () => {
    const data = seed();
    const pageId = data.dataset.page_ids[0];
    const user = await signIn(data.users[1]);
    const stamp = Date.now();
    const editors = new Map<string, Editor>();
    const editor = await connect(LIVE_WS, pageId, user);
    editors.set("editor-0", editor);
    try {
      await appendMarker(editor, `retained-before-${stamp}`);
      await assert.rejects(
        reconnectEditorInto(
          editors,
          "editor-0",
          () => connect("ws://127.0.0.1:9", pageId, user, 2000),
          async () => {}
        )
      );
      assert.equal(editors.get("editor-0"), editor);
      assert.equal(editor.provider.isAttached, true);
      const marker = `retained-after-${stamp}`;
      await appendMarker(editor, marker);
      assert.equal((await serverPageText(pageId, user)).includes(marker), true);
      await eventuallyPersisted(pageId, user, marker);
    } finally {
      cleanup(editor);
    }
  });

  test(
    "a validated replacement updates the map, destroys the old connection and persists its next edit",
    opts,
    async () => {
      const data = seed();
      const pageId = data.dataset.page_ids[0];
      const user = await signIn(data.users[2]);
      const stamp = Date.now();
      const carried = `carried-${stamp}`;
      const editors = new Map<string, Editor>();
      const previous = await connect(LIVE_WS, pageId, user);
      editors.set("editor-0", previous);
      let replacement: Editor | undefined;
      try {
        await appendMarker(previous, carried);
        replacement = await reconnectEditorInto(
          editors,
          "editor-0",
          () => connect(LIVE_WS, pageId, user),
          async (next) => {
            assert.equal(next.doc.getXmlFragment("default").toString().includes(carried), true);
          }
        );
        assert.equal(editors.get("editor-0"), replacement);
        assert.equal(previous.provider.isAttached, false);
        assert.equal(replacement.provider.isAttached, true);
        const marker = `replacement-after-${stamp}`;
        await appendMarker(replacement, marker);
        await eventuallyPersisted(pageId, user, marker);
      } finally {
        cleanup(previous);
        if (replacement) cleanup(replacement);
      }
    }
  );

  test(
    "a replacement that detaches during validation is discarded and the map keeps the live connection",
    opts,
    async () => {
      const data = seed();
      const pageId = data.dataset.page_ids[0];
      const user = await signIn(data.users[3]);
      const editors = new Map<string, Editor>();
      const previous = await connect(LIVE_WS, pageId, user);
      editors.set("editor-0", previous);
      let replacement: Editor | undefined;
      try {
        await assert.rejects(
          reconnectEditorInto(
            editors,
            "editor-0",
            async () => (replacement = await connect(LIVE_WS, pageId, user)),
            async (next) => {
              // Simulate a socket that dies while validation is still running.
              next.provider.destroy();
              next.doc.destroy();
            }
          ),
          /editor connection detached/
        );
        assert.equal(editors.get("editor-0"), previous);
        assert.equal(previous.provider.isAttached, true);
        assert.equal(replacement?.provider.isAttached, false);
        await appendMarker(previous, `after-detached-validation-${Date.now()}`);
      } finally {
        cleanup(previous);
      }
    }
  );
});
