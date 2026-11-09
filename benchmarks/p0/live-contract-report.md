# Wave A — Live contract check (lazy PDF / schema-only extensions)

Workspace: `/home/noor/repos/plane-bun-scratch-20261102/live-contract`
Baseline verified: `git rev-parse HEAD` = `075fe58895c3336eb6dda6855a636c75cfe69225` (`fix(docker): copy bunfig into pruned installers`), clean except untracked `.serena/`. Read-only: no app edits, no builds/tests/services.
Model confirmed in use: `surplus/glm-5.3-flash:high`.
Scope: verification of the two already-identified boundaries from the completed survey (ROADMAP "Remove optional PDF and browser UI from Live's baseline import graph"), not a replacement survey.

---

## Experiment 1 — Lazy PDF implementation (endpoint preserved)

### Files in the boundary (exact paths, pinned baseline)

| File                                                                                                                   | Role                                                                                                                                                                                                                                                                                                      |
| ---------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/live/src/controllers/index.ts`                                                                                   | L9 imports `PdfExportController`; L11 `CONTROLLERS` array includes it. Sole registration path.                                                                                                                                                                                                            |
| `apps/live/src/controllers/pdf-export.controller.ts` (142 lines)                                                       | `@Controller("/pdf-export")`, `@Post("/")`. Parses cookie + Effect Schema body, maps tagged errors to HTTP, provides `PdfExportService.Default` (L118).                                                                                                                                                   |
| `apps/live/src/schema/pdf-export.ts`                                                                                   | `PdfExportRequestBody = Schema.Struct({...})` — Effect Schema, no heavy imports. Errors `PdfValidationError`, `PdfAuthenticationError`, `PdfTimeoutError`, `PdfContentFetchError`, `PdfGenerationError`, `PdfImageProcessingError`.                                                                       |
| `apps/live/src/services/pdf-export/index.ts` (barrel)                                                                  | Re-exports `PdfExportService`, `exportToPdf` from `pdf-export.service.ts`.                                                                                                                                                                                                                                |
| `apps/live/src/services/pdf-export/pdf-export.service.ts` (380 lines)                                                  | Effect `Service`; imports `sharp` (L7, only sharp import in `apps/live/src`), `getAllDocumentFormatsFromDocumentEditorBinaryData` from `@plane/editor/server` (L9), `renderPlaneDocToPdfBuffer` from `@/lib/pdf` (L10). Constants: `IMAGE_CONCURRENCY=4`, timeouts 8s/7s/15s, `IMAGE_MAX_DIMENSION=1200`. |
| `apps/live/src/services/pdf-export/effect-utils.ts`                                                                    | `withTimeoutAndRetry`, `recoverWithDefault`, `tryAsync`. Imports only `effect` + schema types — cheap, reusable.                                                                                                                                                                                          |
| `apps/live/src/services/pdf-export/types.ts`                                                                           | Type-only + `@/lib/pdf` type imports.                                                                                                                                                                                                                                                                     |
| `apps/live/src/lib/pdf/index.ts` (barrel)                                                                              | Re-exports `plane-pdf-exporter`, `node-renderers`, `mark-renderers`, `styles`, types.                                                                                                                                                                                                                     |
| `apps/live/src/lib/pdf/plane-pdf-exporter.tsx` (88 lines)                                                              | Imports `@react-pdf/renderer` (L9). **Module-level side effects:** `Font.register({...})` at import time with 6 Inter woff files resolved via `createRequire` from `@fontsource/inter` (400/600/700 × normal/italic).                                                                                     |
| `apps/live/src/lib/pdf/node-renderers.tsx`                                                                             | `@react-pdf/renderer` primitives + `CORE_EXTENSIONS` from `@plane/editor` (the lib entry, not `/server`).                                                                                                                                                                                                 |
| `apps/live/src/lib/pdf/mark-renderers.tsx`, `styles.ts` (React-pdf `StyleSheet`), `colors.ts`, `icons.tsx`, `types.ts` | Supporting; pulled by the barrel.                                                                                                                                                                                                                                                                         |

### Callers (verified)

- In-repo callers of the endpoint: **none**. `PdfExport|pdf-export` searches across `apps/web`, `apps/api`, and repo-wide code files return only the live implementation + `tests/services/pdf-export/effect-utils.test.ts` (which does not touch the controller or service, only `effect-utils` and `PdfTimeoutError`).
- Web exports PDFs client-side; **external callers remain unknown** (survey conclusion reconfirmed at this baseline).
- The registration chain is exactly: `server.ts` L18 `import { CONTROLLERS } from "@/controllers"` → `index.ts` L9 import → L11 array → `server.ts` L95 `CONTROLLERS.forEach(...registerController...)`. Test `tests/server-destroy.test.ts` L22 mocks `@/controllers` with `CONTROLLERS: []` — a lazy controller must stay compatible with that mock shape.
- Contrast: `/convert-document/` (`document.controller.ts` L25) is called by `apps/api/plane/bgtasks/copy_s3_object.py` L79 (`f"{live_url}/convert-document/"`, `variant: "rich"` for PAGE else `"document"`); must be preserved — it is a separate controller and not touched by lazy PDF.

### Dependencies / fonts / native assets reachable only via the PDF chain

- `@react-pdf/renderer` ^4.8.1, `@react-pdf/types` ^2.13.1 (root `package.json` catalog; transitive `pdfkit`, `fontkit`, `linebreak`, `bidi-js` class libs).
- `sharp` ^0.35.5 — native libvips binary. Image path (`processImages`, service L185+): fetch → `sharp().rotate().flatten({white bg}).resize(1200 fit inside).jpeg({q85}).toBuffer()` → base64 data URL.
- `@fontsource/inter` 5.2.8 — six `.woff` files read at module init by `Font.register`. Runtime image installs **no OS font package** (`Dockerfile.live`: only `libc6-compat`, apk upgrade, npm/esbuild removal); the woffs inside node_modules are the only font assets. Alpine musl + `sharp` prebuilt binaries work today (image builds and WS-smoked per WALKTHROUGH M2), so a lazy-import change does not alter native asset loading — it only defers it.
- `effect` 3.20.0 also lives outside the PDF chain in Live, but the controller/service use it heavily; lazy PDF defers `@react-pdf` + `sharp` + `@fontsource/inter` + the editor-codec import, not Effect.

### What lazy loading must preserve (retained contract)

- Endpoint `POST /pdf-export/` reachable after first use; request/response contract: body schema above; success streams `application/pdf` with sanitized `Content-Disposition` filename; errors return `{message}` JSON with mapped status.
- `getDocumentType` hardcodes `"project_page"`; `fetchPageContent` uses `pageService.fetchDescriptionBinary(pageId)` (`apps/live/src/services/page/core.service.ts` L40, GET `{basePath}/pages/{pageId}/description/` as arraybuffer) → `getAllDocumentFormatsFromDocumentEditorBinaryData(binary, true)` → `contentJSON` + `titleHTML`.
- `vi.mock("@/controllers")` shape in `server-destroy.test.ts` must keep resolving `CONTROLLERS`.

### Existing verification scenarios to reuse (EXISTING files only — not run here)

1. `apps/live/tests/lib/pdf/pdf-rendering.test.ts` (732 lines, 18 cases): renders through `renderPlaneDocToPdfBuffer` and asserts `%PDF-` header, extracted text for headings/lists/tables/marks/links/mentions/page-size/landscape/metadata/noAssets/mixed+nested docs. This is the behavioral contract for the lazily loaded renderer; it imports `@/lib/pdf` directly, so it exercises first-use of the deferred module.
2. `apps/live/tests/services/pdf-export/effect-utils.test.ts` (14 cases): timeout/retry/recover semantics around `PdfTimeoutError` — the error-mapping contract kept live when the controller wraps a lazy loader.
3. `apps/live/tests/server-destroy.test.ts`: boots the whole server with mocked `CONTROLLERS`/page service; the registration path (`server.ts` setupRoutes) is exercised by the real container registration; a lazy variant must keep this green.
4. `apps/live/tests/integration/hocuspocus-v3.test.ts` (skipIf no `TEST_REDIS_URL`; v2 Yjs fixtures incl. emoji shortcode `:tada:` in the title fixture): collaboration regression guard proving the lazy change left collaboration alone. It also demonstrates the API-boundary mock pattern (`fetchDescriptionBinary`/`updateDescriptionBinary`) usable for a controller-level PDF smoke.
5. No existing test covers the controller HTTP layer itself (`parseRequest`, error→HTTP mapping, filename sanitization) — **uncertainty**: parity of the endpoint wrapper after lazy-loading has no pre-built scenario; the lead would need the two PDF test files plus a manual/added controller-level request.

### Measurement hooks (cold / first-use / post-use), named in source

- **Cold idle:** process starts without `@react-pdf`/`sharp`/Inter woffs resident. Instrument: cgroup `memory.current` (P0 contract) or the playbook harness's process-tree PSS (`benchmarks/sample_rss.py` in `playbook-cbcde5b56.tar`); boot readiness via `boot_bench.py` (accepts any HTTP response — root 404 is intentional).
- **First-use:** first `POST /pdf-export/` triggers dynamic import → measure request latency and the memory delta (import cost + `Font.register` reading six woffs + sharp's libvips init). Observable completion: HTTP 200 + `%PDF-` header in response body (the existing test asserts this exact predicate).
- **Post-use steady state:** modules stay resident after first use; sample `memory.current`/PSS at fixed cadence for ≥12 s post-request and compare against a control that made the same request. The WALKTHROUGH M7 rule applies verbatim: a cold-idle win alone is a startup result, not deployment-memory proof.
- Server entry: `src/start.ts` → `Server` → `initialize()` → `listen()`; build is `tsdown` single ESM entry `src/start.ts` → `dist/start.mjs` (`CMD ["bun", "apps/live"]`). `exports: true` in tsdown config; a dynamic `import()` inside the controller stays a real runtime dynamic import in the artifact — but **unverified**: whether tsdown/rolldown keeps `@react-pdf/renderer` external in `dist/start.mjs` (single-file bundle would still defer execution but not parse; allocation deferral is what matters and must be measured, not assumed).

### Uncertainty (experiment 1)

- tsdown bundling behavior for the dynamic import (see above) — unverified; no build was run.
- External callers of `/pdf-export` — unknowable from source; retirement was already deferred, this experiment keeps the endpoint.
- Whether `getAllDocumentFormatsFromDocumentEditorBinaryData` (shared with the schema experiment) pulls UI code _through the PDF path too_ — see below; if the schema split lands first, the PDF chain's editor import becomes cheaper independently.
- Node 22 (`FROM node:22-alpine`) vs Bun runtime: Dockerfile CMD is Bun, but `Dockerfile.live` CMD comment says "plain node" in a comment about npm removal — CMD is `bun apps/live`; the historical trap "Bun scripts may still launch Node via shebangs" applies to any measurement invocation.

---

## Experiment 2 — Schema-only server extensions (persisted formats preserved)

### Proven chain at this baseline (re-verified, matches survey)

`packages/editor/src/server.ts` (L9) → `helpers/document-codecs.ts` (L14-17) → `extensions/core-without-props.ts` → `extensions/emoji/extension.ts` (L11 `.configure({ suggestion: emojiSuggestion, ... })`) → `extensions/emoji/suggestion.ts` (L4 `ReactRenderer` from `@tiptap/react`, L12 `EmojisListDropdown` from `./components/emojis-list`).

`@/extensions/core-without-props` is imported by exactly three files: `helpers/document-codecs.ts`, `lib.ts` (L6 barrel), `extensions/index.ts` (L17 barrel).

### The UI reachability map is wider than emoji (new at this baseline — survey named emoji only)

Also reached from `CoreEditorExtensionsWithoutProps` (all in `packages/editor/src/extensions/`):

- `image/extension.tsx` + `custom-image/extension.tsx`: `ReactNodeViewRenderer` + `CustomImageNodeView` (React node views).
- `table/table/table.ts` → `TableView` (`jsx-dom-cjs`, non-React but DOM-dependent) and `table/plugins/drag-handles/{column,row}/plugin.ts` → `ReactRenderer` + `ColumnDragHandle`/`RowDragHandle` (import `react`, `@makeplane/propel/icons`).
- `mentions/extension-config.ts` defines `renderComponent` option (handler-injected; the config file itself has no React import — mention UI arrives via `@plane/editor` consumers passing handlers, and `mention-node-view.tsx` exists for browser use).
- `code/without-props.tsx` → `lowlight`/`highlight.js` (no React, but real weight).
- Callout/work-item-embed `extension-config.ts` files are UI-free (constants + `@tiptap/core` only).

Consequence: an emoji-only schema split shrinks the reachability set but does not fully free `@plane/editor/server` of React/DOM; the experiment's first step should split the _extension configs_ into schema-pure variants, not only reroute emoji.

### Editor package shape

- Exports (`packages/editor/package.json`): `.` → `dist/index.js`, `./lib` → `dist/lib.js`, `./server` → `dist/server.js` (tsdown `platform: "neutral"`, entries `src/index.ts|lib.ts|server.ts`).
- `src/lib.ts` exports `core-without-props` + yjs-utils (browser `@tiptap/html` codecs); `src/server.ts` builds the same codec set on `@tiptap/html/server` (happy-dom). `yjs-utils.ts` duplicates `createDocumentCodecs` wiring for the browser entry — **both entries import the same `core-without-props`**, so a schema split must be importable from both.
- Live consumes `@plane/editor/server` in exactly 4 files: `controllers/document.controller.ts` (`convertHTMLDocumentToAllFormats`), `extensions/database.ts` (`getAllDocumentFormatsFromDocumentEditorBinaryData`, `getBinaryDataFromDocumentEditorHTMLString`), `extensions/title-sync.ts` (`TITLE_EDITOR_EXTENSIONS`, `createRealtimeEvent`, `extractTextFromHTML`, `generateTitleProsemirrorJson`), `services/pdf-export/pdf-export.service.ts` (overlaps experiment 1).
- Live also imports `@plane/editor` (lib entry) in `lib/auth.ts` (type-only), `lib/pdf/node-renderers.tsx` (value: `CORE_EXTENSIONS`), `utils/broadcast-*.ts` (value: `createRealtimeEvent`). `lib.ts` → `yjs-utils.ts` → `document-codecs.ts` → the same UI-reaching extensions; so even non-PDF Live lib-entry imports currently reach React. **Uncertainty**: whether any Live import path avoids the extensions entirely today — none found; every server/lib consumer transitively builds the full schema.

### Persisted formats and the schema names/attrs that must not change

- `createDocumentCodecs` builds `richTextEditorSchema`/`documentEditorSchema` via `getSchema(extensions)` and converts via `prosemirrorJSONToYDoc`/`yXmlFragmentToProseMirrorRootNode` (`@tiptap/y-tiptap`), Yjs fragments `"default"` (body) and `"title"`. `TitleExtensions` = Document(heading-only) + Heading(level 1) + Text — trivially schema-pure already.
- Django persisted columns (API side): `description_binary` (BinaryField, Yjs update bytes) on `Page`, `PageVersion`, `Issue`, `IssueDescription`, `Draft` (`apps/api/plane/db/models/{page,issue,draft,description}.py`); `description_html`, `description_json` alongside. Page retrieve endpoint streams `description_binary` as `application/octet-stream` (`apps/api/plane/app/views/page/base.py` L495-515); serializers carry all three formats (`app/serializers/page.py` L161-163). `PageVersion` additionally persists `description_stripped`.
- Schema-relevant node names/attrs that serialize into those bytes (from the without-props configs): `emoji` node attr `name` (plus `enableEmoticons` behavior); `mention` attrs `id`, `entity_identifier`, `entity_name` (`EMentionComponentAttributeNames`); `calloutComponent` attrs (`ECalloutAttributeNames`, `DEFAULT_CALLOUT_BLOCK_ATTRIBUTES`); `imageComponent` (`ECustomImageAttributeNames`, `DEFAULT_CUSTOM_IMAGE_ATTRIBUTES`); taskList/taskItem, tables, codeBlock(lowlight), quote, horizontalRule, text-align, text-style, work-item-embed. **UI separation must not touch `addAttributes`/`parseHTML`/`toMarkdown`/storage serializers — only `suggestion`, node views, and render handlers.**
- Emoji data: `EmojiExtension` filters `gitHubEmojis` (drops `fallbackImage` to avoid CDN calls) at configure time — data-only, no UI.

### Existing verification scenarios to reuse (EXISTING files only)

1. `apps/live/tests/integration/hocuspocus-v3.test.ts` — the single strongest persisted-format check: two recorded **v2 (Tiptap 2 / y-prosemirror) base64 Yjs documents** (`V2_PAGE` with body+title incl. taskList/taskItem/bold/italic, and `V2_PAGE_WITHOUT_TITLE`); asserts v2 body+title preservation, cross-server edit sync, title backfill for a title-less v2 page, and HTML→Yjs server-side conversion saved back. Requires `TEST_REDIS_URL` (skips otherwise) — the lead must supply Redis or the scenario silently skips.
2. `packages/editor/src/hooks/__tests__/use-yjs-setup.test.ts` and `extensions/__tests__/placeholder.test.ts` — existing editor-side checks (small; placeholder is schema-adjacent).
3. `apps/live/tests/server-destroy.test.ts` — server boot with the real `@plane/editor/server` import chain in place (only controllers mocked).
4. No existing test does round-trip `HTML → getSchema(extensions) → JSON/HTML` golden-file comparison, and none parses a persisted `description_binary` fixture against `documentEditorSchema` — **uncertainty**: schema-attribute parity after the split is _not_ directly covered by any existing scenario; the closest proxies are (1) and web's runtime behavior (not exercised here). The lead should treat v2-fixture round-trips as the available evidence and state the gap.

### Measurement hooks (schema experiment)

- Same three phases as experiment 1, on a separate change: cold idle (no `@tiptap/react`, `@floating-ui/*`, `lucide-react`, dropdown components, `jsx-dom-cjs` loaded), first-use (first `/convert-document/` request or first Hocuspocus document load — schema built on demand if factories are lazy), post-use steady state (V8/JSC module registry retention).
- First-use paths to exercise: `/convert-document/` (API-called, `copy_s3_object.py`), Hocuspocus `onLoadDocument`/`onStoreDocument` (`extensions/database.ts`), title sync (document events). Durable outcome: saved `description_binary` round-trips and Yjs convergence, matching the hocuspocus-v3 assertions.
- Dependency deferral targets (from `packages/editor/package.json` deps): `@tiptap/react`, `@floating-ui/dom`, `@floating-ui/react`, `lucide-react` (+ peer `react`/`react-dom` already in Live's deps for other reasons — removing them from the server graph is a Live-level win only if no other Live path loads them; PDF node-renderers imports `@plane/editor` lib entry, which is experiment-1-adjacent).
- Fonts/native: none in this boundary (editor-side); happy-dom is the HTML codec's DOM (imported by `@tiptap/html/server`), retained either way.

### Uncertainty (experiment 2)

- `getSchema(...)` runs at `createDocumentCodecs` call time (module init of `server.ts`), so today's server builds both schemas at boot even before any request; a lazy-split can defer this, but whether Hocuspocus accepts deferred schema construction per-document is unverified (hook timing in `extensions/database.ts` executes codec calls per load/save — plausibly fine, not proven).
- `mention` `renderComponent` is handler-injected from Live callers — grep did not find where Live passes it (`getPageService` wiring was not traced fully); if Live never passes a browser handler on the server, mention UI is already dead on the server and the split is purely mechanical there. **Unresolved detail.**
- Both `lib.ts` and `server.ts` wire codecs; web/space consume `@plane/editor` lib entry at scale (60+ files) — the split must remain source-compatible for the browser entry, which was not mapped beyond the Live consumers above.
- Whether bun's transpiler treats `.tsx` UI files inside the editor package as side-effectful (dropdown CSS, `@makeplane/propel/icons` subimport) — not examined; affects how much first-use actually defers.

---

## Cross-experiment notes

- The two changes touch one shared file surface (`pdf-export.service.ts` imports `@plane/editor/server`), confirming ROADMAP's treatment of them as **independent candidates on the same baseline, not stacked**.
- Shared existing regression guard for both: `hocuspocus-v3.test.ts` (Redis-gated) and `server-destroy.test.ts`.
- Playbook harness members for the lead's measurement phase: `benchmarks/README.md`, `benchrun.py`, `boot_bench.py`, `sample_rss.py`, `ws-bench.mjs` (inside `playbook-cbcde5b56.tar`, launch-input dir).

## Checks NOT performed (execution-gated)

- No install, build (`tsdown`/`tsc`), test run, service start, benchmark, formatter, or nixlab/host work — per assignment. The verification scenarios above are catalogued, not executed.
- No dynamic-import bundling probe of `dist/start.mjs`; no `bun build --external` experiment.
- No container/image inspection (`sharp`/`@fontsource/inter` presence verified from Dockerfile + lockfile/catalog, not from a built image).
- No Serena rename/refactor; onboarding was skipped (first-use prompt declined — out of scope for read-only work; `.serena/` project scaffolding is untracked and left in place).
- Web/space consumer mapping of the editor lib entry beyond Live: bounded out; schema split's browser-entry compatibility is stated as uncertainty, not proven.
