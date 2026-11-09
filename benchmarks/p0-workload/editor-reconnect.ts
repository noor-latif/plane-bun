/**
 * Editor connection lifecycle for the P0 workload.
 *
 * Kept out of `run.ts` so the reconnect policy can be exercised with real Yjs
 * documents and `@hocuspocus/provider` instances without starting the whole
 * workload. Scratch-only benchmark code; it changes no application behavior.
 */
import type { HocuspocusProvider } from "@hocuspocus/provider";
import type { Doc } from "yjs";

export type EditorConnection<U> = {
  pageId: string;
  doc: Doc;
  provider: HocuspocusProvider;
  user: U;
};

/**
 * A detached provider stops counting unsynced updates, so an insert into its
 * document looks acknowledged although nothing was sent; edits must be attached.
 */
export function assertAttached<U>(editor: EditorConnection<U>): void {
  if (!editor.provider.isAttached) throw new Error("editor connection detached");
}

/**
 * Swap in a replacement only after it connects, passes `validate` and is still
 * attached; a failed or detached replacement is destroyed and the previous live
 * connection is kept, so one failed reconnect cannot silence later edits. The
 * failure still propagates. This does not make later disconnects impossible.
 */
export async function replaceEditor<U>(
  previous: EditorConnection<U>,
  connect: () => Promise<EditorConnection<U>>,
  validate: (replacement: EditorConnection<U>) => Promise<void>
): Promise<EditorConnection<U>> {
  let replacement: EditorConnection<U> | undefined;
  try {
    replacement = await connect();
    await validate(replacement);
    // Validation can outlive the socket (server restart, auth expiry). Checking
    // attachment here means a connection that died during validation is
    // discarded with the same guarantees as a failed connect.
    assertAttached(replacement);
  } catch (error) {
    replacement?.provider.destroy();
    replacement?.doc.destroy();
    throw error;
  }
  previous.provider.destroy();
  previous.doc.destroy();
  return replacement;
}

/**
 * Reconnect one map entry through `replaceEditor`. The map is only updated after
 * the swap succeeded, so a failure leaves the caller on a live connection.
 */
export async function reconnectEditorInto<U>(
  editors: Map<string, EditorConnection<U>>,
  editorId: string,
  connect: () => Promise<EditorConnection<U>>,
  validate: (replacement: EditorConnection<U>) => Promise<void>
): Promise<EditorConnection<U>> {
  const previous = editors.get(editorId);
  if (!previous) throw new Error(`no editor connection for ${editorId}`);
  const replacement = await replaceEditor(previous, connect, validate);
  editors.set(editorId, replacement);
  return replacement;
}
