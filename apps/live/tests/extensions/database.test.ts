/**
 * Store-recovery behaviour of the Live database extension, driven through the
 * real @hocuspocus/server 3.4.4 debouncer and saveMutex.
 *
 * Only the HTTP page service, the error broadcast, the force-close handler and
 * the logger are replaced. Fake timers cover the debounce/backoff budgets, so
 * the suite is deterministic and needs no Docker or live stack.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as Y from "yjs";
import { Hocuspocus } from "@hocuspocus/server";

const mocks = vi.hoisted(() => ({
  updateDescriptionBinary: vi.fn(),
  fetchDescriptionBinary: vi.fn(),
  broadcastError: vi.fn(),
  forceClose: vi.fn(),
  loggerError: vi.fn(),
  loggerInfo: vi.fn(),
}));

vi.mock("@/services/page/handler", () => ({
  getPageService: () => ({
    updateDescriptionBinary: mocks.updateDescriptionBinary,
    fetchDescriptionBinary: mocks.fetchDescriptionBinary,
    fetchDetails: async () => ({ name: "page", description_html: "<p></p>" }),
  }),
}));
vi.mock("@/utils/broadcast-error", () => ({ broadcastError: mocks.broadcastError }));
vi.mock("@/extensions/force-close-handler", () => ({ forceCloseDocumentAcrossServers: mocks.forceClose }));
vi.mock("@plane/logger", () => ({
  logger: { error: mocks.loggerError, info: mocks.loggerInfo, warn: vi.fn() },
}));
vi.mock("@plane/editor/server", async () => {
  const Yjs = await import("yjs");
  return {
    getAllDocumentFormatsFromDocumentEditorBinaryData: (state: Uint8Array) => {
      const doc = new Yjs.Doc();
      Yjs.applyUpdate(doc, state);
      const text = doc.getXmlFragment("default").toString();
      return {
        contentBinaryEncoded: Buffer.from(state).toString("base64"),
        contentHTML: text,
        contentJSON: { text },
      };
    },
    getBinaryDataFromDocumentEditorHTMLString: () => null,
  };
});

import { AppError } from "@/lib/errors";
import { Database, storeTrackingSizes } from "@/extensions/database";

const context = {
  documentType: "project_page" as const,
  workspaceSlug: "ws",
  projectId: "project",
  cookie: "session-id=secret-cookie",
  userId: "user-1",
};
const instance = {} as never;
const DEBOUNCE_MS = 10_000;
const BACKOFF_MS = 1_000;

function makeServer() {
  const db = new Database();
  const server = new Hocuspocus({
    debounce: DEBOUNCE_MS,
    maxDebounce: DEBOUNCE_MS,
    quiet: true,
    extensions: [db],
  });
  return { server, db };
}

function edit(doc: Y.Doc, marker: string) {
  const body = doc.getXmlFragment("default");
  const paragraph = new Y.XmlElement("paragraph");
  paragraph.insert(0, [new Y.XmlText(marker)]);
  body.insert(body.length, [paragraph]);
}

async function openAndEdit(server: Hocuspocus, pageId: string, markers: string[]) {
  const connection = await server.openDirectConnection(pageId, context as never);
  // transact() awaits the store hook (immediately=true); drive the fake
  // debounce/backoff timers while it is pending instead of awaiting it first.
  const transact = connection.transact((doc) => {
    for (const marker of markers) edit(doc, marker);
  });
  await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(1_000);
  await transact;
  const document = server.documents.get(pageId);
  if (!document) throw new Error("document not created");
  return { connection, document };
}

function expectNoCookieLogged() {
  const logged = JSON.stringify([...mocks.loggerError.mock.calls, ...mocks.loggerInfo.mock.calls]);
  expect(logged).not.toContain("session-id=secret-cookie");
}

const transient = () => new AppError("timeout of 10000ms exceeded");
const status = (code: number) => new AppError("request failed", { statusCode: code });

describe("Live store recovery", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.updateDescriptionBinary.mockReset();
    mocks.fetchDescriptionBinary.mockReset().mockResolvedValue(Buffer.from(Y.encodeStateAsUpdate(new Y.Doc())));
    mocks.broadcastError.mockReset().mockResolvedValue(undefined);
    mocks.forceClose.mockReset().mockResolvedValue(undefined);
    mocks.loggerError.mockReset();
    mocks.loggerInfo.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("retries a transient failure through the real debouncer and persists the whole document", async () => {
    const { server, db } = makeServer();
    mocks.updateDescriptionBinary.mockRejectedValueOnce(transient()).mockResolvedValueOnce({});
    const { document } = await openAndEdit(server, "page-1", ["marker-a", "marker-b"]);

    expect(mocks.updateDescriptionBinary).toHaveBeenCalledTimes(2);
    const payload = mocks.updateDescriptionBinary.mock.calls[1][1] as { description_html: string };
    expect(payload.description_html).toContain("marker-a");
    expect(payload.description_html).toContain("marker-b");
    expect(mocks.broadcastError).not.toHaveBeenCalled();

    // Success empties the unsaved set and the retained context.
    expect(storeTrackingSizes()).toEqual({ unsaved: 0, contexts: 0 });
    mocks.updateDescriptionBinary.mockClear();
    await db.beforeUnloadDocument({ document, documentName: "page-1", instance } as never);
    expect(mocks.updateDescriptionBinary).not.toHaveBeenCalled();
  });

  it("resolves after the budget is exhausted and records the loss once", async () => {
    const { server, db } = makeServer();
    mocks.updateDescriptionBinary.mockRejectedValue(transient());
    const { document } = await openAndEdit(server, "page-2", ["marker-a"]);

    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    await vi.advanceTimersByTimeAsync(BACKOFF_MS);
    expect(mocks.updateDescriptionBinary).toHaveBeenCalledTimes(2);
    expect(mocks.broadcastError).toHaveBeenCalledTimes(1);
    expect(mocks.loggerError.mock.calls.some((call) => String(call[0]).includes("retry budget"))).toBe(true);

    // The page is still tracked: the unload flush makes its own bounded attempt.
    mocks.updateDescriptionBinary.mockClear();
    const unloadPending = db.beforeUnloadDocument({ document, documentName: "page-2", instance } as never);
    await vi.advanceTimersByTimeAsync(BACKOFF_MS);
    await unloadPending;
    expect(mocks.updateDescriptionBinary).toHaveBeenCalledTimes(2);
    expect(mocks.loggerError.mock.calls.some((call) => String(call[0]).includes("page_store_loss"))).toBe(true);
    expect(mocks.broadcastError).toHaveBeenCalledTimes(2);

    // The hook resolves, so the server's unload path is never refused; the page
    // is forgotten, so a second unload attempt performs no store at all.
    expect(server.documents.has("page-2")).toBe(true);
    mocks.updateDescriptionBinary.mockClear();
    await db.beforeUnloadDocument({ document, documentName: "page-2", instance } as never);
    expect(mocks.updateDescriptionBinary).not.toHaveBeenCalled();
  });

  it("never retries a 413, force-closes, and does not pin the document", async () => {
    const { server, db } = makeServer();
    mocks.updateDescriptionBinary.mockRejectedValue(status(413));
    const { connection, document } = await openAndEdit(server, "page-3", ["marker-a"]);

    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    expect(mocks.updateDescriptionBinary).toHaveBeenCalledTimes(1);
    expect(mocks.forceClose).toHaveBeenCalledTimes(1);
    expect(mocks.broadcastError).toHaveBeenCalledTimes(1);

    expect(storeTrackingSizes()).toEqual({ unsaved: 0, contexts: 0 });
    mocks.updateDescriptionBinary.mockClear();
    await db.beforeUnloadDocument({ document, documentName: "page-3", instance } as never);
    expect(mocks.updateDescriptionBinary).not.toHaveBeenCalled();
    await connection.disconnect();
  });

  it("never retries a non-413 4xx and forgets the page", async () => {
    const { server, db } = makeServer();
    mocks.updateDescriptionBinary.mockRejectedValue(status(403));
    const { connection, document } = await openAndEdit(server, "page-4", ["marker-a"]);

    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    expect(mocks.updateDescriptionBinary).toHaveBeenCalledTimes(1);
    expect(mocks.forceClose).not.toHaveBeenCalled();
    expect(mocks.broadcastError).toHaveBeenCalledTimes(1);

    expect(storeTrackingSizes()).toEqual({ unsaved: 0, contexts: 0 });
    mocks.updateDescriptionBinary.mockClear();
    await db.beforeUnloadDocument({ document, documentName: "page-4", instance } as never);
    expect(mocks.updateDescriptionBinary).not.toHaveBeenCalled();
    await connection.disconnect();
  });

  it("bounds each attempt by the remaining store budget", async () => {
    const { server, db } = makeServer();
    // The stub consumes fake time, so the second attempt sees the remaining
    // budget rather than the full request cap.
    mocks.updateDescriptionBinary.mockImplementation(
      () => new Promise((_resolve, reject) => setTimeout(() => reject(transient()), 9_000))
    );
    const connection = await server.openDirectConnection("page-8", context as never);
    const transact = connection.transact((doc) => edit(doc, "marker-a"));
    await vi.advanceTimersByTimeAsync(9_000);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(mocks.updateDescriptionBinary.mock.calls[0][2]).toBe(10_000);
    expect(mocks.updateDescriptionBinary.mock.calls[1][2]).toBe(5_000);
    await vi.advanceTimersByTimeAsync(9_000);
    await transact;
    const document = server.documents.get("page-8");
    if (!document) throw new Error("document not created");

    mocks.updateDescriptionBinary.mockClear();
    const unloadPending = db.beforeUnloadDocument({ document, documentName: "page-8", instance } as never);
    await vi.advanceTimersByTimeAsync(9_000);
    await unloadPending;
    expect(mocks.updateDescriptionBinary.mock.calls[0][2]).toBe(8_000);
    expect(storeTrackingSizes()).toEqual({ unsaved: 0, contexts: 0 });
  });

  it("caps tracking at the limit, logs the refusal and keeps existing entries", { timeout: 30_000 }, async () => {
    const { db } = makeServer();
    mocks.updateDescriptionBinary.mockRejectedValue(transient());
    const doc = new Y.Doc();
    edit(doc, "marker");
    const pending = Array.from({ length: 500 }, (_, index) =>
      db.onStoreDocument({ document: doc, documentName: `page-cap-${index}`, context, instance } as never)
    );
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(BACKOFF_MS);
    await Promise.all(pending);
    const overflow = db.onStoreDocument({
      document: doc,
      documentName: "page-cap-overflow",
      context,
      instance,
    } as never);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(BACKOFF_MS);
    await overflow;
    expect(mocks.loggerError.mock.calls.some((call) => String(call[0]).includes("tracking at capacity"))).toBe(true);

    mocks.updateDescriptionBinary.mockClear();
    const unloadPending = db.beforeUnloadDocument({ document: doc, documentName: "page-cap-0", instance } as never);
    await vi.advanceTimersByTimeAsync(BACKOFF_MS);
    await unloadPending;
    expect(mocks.updateDescriptionBinary).toHaveBeenCalledTimes(2);
  });

  it("drops the retained context on destroy and never logs the cookie", async () => {
    const { server, db } = makeServer();
    mocks.updateDescriptionBinary.mockRejectedValue(transient());
    const { document } = await openAndEdit(server, "page-5", ["marker-a"]);
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    await vi.advanceTimersByTimeAsync(BACKOFF_MS);

    db.onDestroy();
    mocks.updateDescriptionBinary.mockClear();
    await db.beforeUnloadDocument({ document, documentName: "page-5", instance } as never);
    expect(mocks.updateDescriptionBinary).not.toHaveBeenCalled();

    expectNoCookieLogged();
    expect(storeTrackingSizes()).toEqual({ unsaved: 0, contexts: 0 });
  });
});
