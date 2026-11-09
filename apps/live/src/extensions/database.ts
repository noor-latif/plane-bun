/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Database as HocuspocusDatabase } from "@hocuspocus/extension-database";
import * as Y from "yjs";
// plane imports
import {
  getAllDocumentFormatsFromDocumentEditorBinaryData,
  getBinaryDataFromDocumentEditorHTMLString,
} from "@plane/editor/server";
import type { TDocumentPayload } from "@plane/types";
import { logger } from "@plane/logger";
// lib
import { AppError } from "@/lib/errors";
// services
import { getPageService } from "@/services/page/handler";
// type
import type { FetchPayloadWithContext, HocusPocusServerContext, StorePayloadWithContext } from "@/types";
import { ForceCloseReason, CloseCode } from "@/types/admin-commands";
import { broadcastError } from "@/utils/broadcast-error";
// force close utility
import { forceCloseDocumentAcrossServers } from "./force-close-handler";

const fetchDocument = async ({ context, documentName: pageId, instance }: FetchPayloadWithContext) => {
  try {
    const service = getPageService(context.documentType, context);
    // fetch details
    const response = (await service.fetchDescriptionBinary(pageId)) as Buffer;
    const binaryData = new Uint8Array(response);
    // if binary data is empty, convert HTML to binary data
    if (binaryData.byteLength === 0) {
      const pageDetails = await service.fetchDetails(pageId);
      const convertedBinaryData = getBinaryDataFromDocumentEditorHTMLString(
        pageDetails.description_html ?? "<p></p>",
        pageDetails.name
      );
      if (convertedBinaryData) {
        // save the converted binary data back to the database
        try {
          const { contentBinaryEncoded, contentHTML, contentJSON } = getAllDocumentFormatsFromDocumentEditorBinaryData(
            convertedBinaryData,
            true
          );
          const payload: TDocumentPayload = {
            description_binary: contentBinaryEncoded,
            description_html: contentHTML,
            description_json: contentJSON,
          };
          await service.updateDescriptionBinary(pageId, payload);
        } catch (e) {
          const error = new AppError(e);
          logger.error("Failed to save binary after first conversion from html:", error);
        }
        return convertedBinaryData;
      }
    }
    // return binary data
    return binaryData;
  } catch (error) {
    const appError = new AppError(error, { context: { pageId } });
    logger.error("Error in fetching document", appError);

    // Broadcast error to frontend for user document types
    await broadcastError(instance, pageId, "Unable to load the page. Please try refreshing.", "fetch", context);

    throw appError;
  }
};

// Two attempts inside a 15 s budget, plus an 8 s final attempt on unload: the
// worst case (23 s) fits below server.ts's 25 s shutdown timeout, which is
// itself below the 30 s container stop grace.
const STORE_ATTEMPT_LIMIT = 2;
const STORE_TOTAL_BUDGET_MS = 15_000;
const STORE_REQUEST_TIMEOUT_MS = 10_000;
const STORE_BACKOFF_MS = [1_000];
const FINAL_STORE_BUDGET_MS = 8_000;
const TRACKED_PAGE_LIMIT = 500;

// Pages whose last store attempt failed transiently. Presence is all that is
// read; entries are removed on a successful store, on a non-retryable failure,
// on force-close and after the final unload attempt. Only the fields the retry
// call needs are retained, never the whole server context.
type StoreContext = Pick<HocusPocusServerContext, "documentType" | "workspaceSlug" | "projectId" | "cookie" | "userId">;
const unsavedPages = new Set<string>();
const pageContexts = new Map<string, StoreContext>();
let storeLossCount = 0;

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Diagnostic/test seam: current retry-tracking sizes (no per-page identity). */
export const storeTrackingSizes = () => ({ unsaved: unsavedPages.size, contexts: pageContexts.size });

const isRetryable = (error: AppError): boolean => {
  const status = error.statusCode;
  if (status === 413) return false;
  if (typeof status === "number" && status >= 400 && status < 500 && status !== 429) return false;
  return true;
};

const buildPayload = (state: Uint8Array): TDocumentPayload => {
  const { contentBinaryEncoded, contentHTML, contentJSON } = getAllDocumentFormatsFromDocumentEditorBinaryData(
    state,
    true
  );
  return {
    description_binary: contentBinaryEncoded,
    description_html: contentHTML,
    description_json: contentJSON,
  };
};

const rememberUnsaved = (pageId: string, context: StoreContext): boolean => {
  if (!unsavedPages.has(pageId) && unsavedPages.size >= TRACKED_PAGE_LIMIT) {
    // Never bulk-drop other pages: an untracked page only loses its unload
    // flush, and that is reported here.
    logger.error(
      "Page store tracking at capacity; page left untracked for the unload flush",
      new AppError("store tracking capacity", { context: { pageId } })
    );
    return false;
  }
  unsavedPages.add(pageId);
  pageContexts.set(pageId, {
    documentType: context.documentType,
    workspaceSlug: context.workspaceSlug,
    projectId: context.projectId,
    cookie: context.cookie,
    userId: context.userId,
  });
  return true;
};

const forgetUnsaved = (pageId: string) => {
  unsavedPages.delete(pageId);
  pageContexts.delete(pageId);
};

/**
 * Bounded retry loop for one page store. Returns null on success and the last
 * error otherwise. Every attempt is logged; the caller decides how to report.
 */
const savePage = async (params: {
  context: StoreContext;
  pageId: string;
  payload: TDocumentPayload;
  budgetMs: number;
}): Promise<AppError | null> => {
  const { context, pageId, payload, budgetMs } = params;
  const service = getPageService(context.documentType, context);
  const startedAt = Date.now();
  let lastError: AppError | null = null;
  for (let attempt = 1; attempt <= STORE_ATTEMPT_LIMIT; attempt++) {
    const remaining = budgetMs - (Date.now() - startedAt);
    if (remaining <= 0) break;
    try {
      // oxlint-disable-next-line no-await-in-loop -- retries are sequential by design
      await service.updateDescriptionBinary(pageId, payload, Math.min(STORE_REQUEST_TIMEOUT_MS, remaining));
      forgetUnsaved(pageId);
      return null;
    } catch (error) {
      lastError = new AppError(error, { context: { operation: "storeDocument", pageId, attempt } });
      logger.error(`Error in updating document (attempt ${attempt}/${STORE_ATTEMPT_LIMIT})`, lastError);
      if (!isRetryable(lastError)) break;
      const wait = STORE_BACKOFF_MS[attempt - 1] ?? 0;
      if (Date.now() - startedAt + wait >= budgetMs) break;
      // oxlint-disable-next-line no-await-in-loop -- the backoff must finish before the next attempt
      if (attempt < STORE_ATTEMPT_LIMIT) await sleep(wait);
    }
  }
  const error =
    lastError ?? new AppError("Page store budget exhausted", { context: { operation: "storeDocument", pageId } });
  if (isRetryable(error)) {
    rememberUnsaved(pageId, context);
  } else {
    // A non-retryable failure (413 or 4xx other than 429) can never succeed on
    // retry: it must not enter the unload flush and must not pin the document.
    forgetUnsaved(pageId);
  }
  return error;
};

const storeDocument = async ({
  context,
  state: pageBinaryData,
  documentName: pageId,
  instance,
}: StorePayloadWithContext) => {
  let payload: TDocumentPayload;
  try {
    payload = buildPayload(pageBinaryData);
  } catch (error) {
    const appError = new AppError(error, { context: { operation: "storeDocument", pageId } });
    logger.error("Failed to convert document for storage", appError);
    await broadcastError(instance, pageId, "Unable to save the page. Please try again.", "store", context);
    return;
  }

  const failure = await savePage({ context, pageId, payload, budgetMs: STORE_TOTAL_BUDGET_MS });
  if (!failure) return;

  const isContentTooLarge = failure.statusCode === 413;
  const errorMessage = isContentTooLarge
    ? "Document is too large to save. Please reduce the content."
    : "Unable to save the page. Please try again.";
  const errorCode = isContentTooLarge ? "content_too_large" : undefined;

  await broadcastError(instance, pageId, errorMessage, "store", context, errorCode, isContentTooLarge);

  if (isContentTooLarge) {
    await forceCloseDocumentAcrossServers(
      instance,
      pageId,
      ForceCloseReason.DOCUMENT_TOO_LARGE,
      CloseCode.DOCUMENT_TOO_LARGE
    );
    return;
  }

  // Resolve instead of rejecting: a rejected store leaves the pristine
  // hocuspocus debouncer's rejected execution retained, which stops every later
  // store for this document. The page stays tracked as unsaved and is retried on
  // the next edit or on the last disconnect.
  logger.error("Page store failed within its retry budget; the edit remains unsaved", failure);
};

type UnloadPayload = {
  document: Y.Doc;
  documentName: string;
  instance: StorePayloadWithContext["instance"];
};

export class Database extends HocuspocusDatabase {
  constructor() {
    super({ fetch: fetchDocument, store: storeDocument });
  }

  /**
   * Last client gone: give an unsaved page one final bounded store attempt
   * before hocuspocus unloads the document. Without this, a store that failed
   * while the API was unavailable is dropped at unload.
   */
  /**
   * Unload is the last chance to persist: an unsaved page gets one final
   * bounded attempt. A failure is logged, counted and broadcast, then the unload
   * proceeds -- refusing the unload would not save the data (the process still
   * exits) and would turn every graceful stop during an API outage into a
   * failed restart.
   */
  async beforeUnloadDocument({ document, documentName: pageId, instance }: UnloadPayload) {
    if (!unsavedPages.has(pageId)) return;
    const retryContext = pageContexts.get(pageId);
    if (!retryContext) {
      forgetUnsaved(pageId);
      return;
    }
    try {
      const payload = buildPayload(Buffer.from(Y.encodeStateAsUpdate(document)));
      const failure = await savePage({ context: retryContext, pageId, payload, budgetMs: FINAL_STORE_BUDGET_MS });
      if (failure) {
        storeLossCount += 1;
        logger.error(
          "page_store_loss: final store attempt failed; unload proceeds and the acknowledged edit is lost",
          failure
        );
        await broadcastError(instance, pageId, "Unable to save the page. Please try again.", "store", retryContext);
      }
    } catch (error) {
      storeLossCount += 1;
      logger.error(
        "page_store_loss: final store attempt threw; unload proceeds and the acknowledged edit is lost",
        new AppError(error, { context: { operation: "beforeUnloadDocument", pageId } })
      );
    } finally {
      forgetUnsaved(pageId);
    }
  }

  /** Drop all retained retry context at shutdown. */
  async onDestroy() {
    unsavedPages.clear();
    pageContexts.clear();
  }
}
