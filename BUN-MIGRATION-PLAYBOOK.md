# Playbook: Node/pnpm → Bun migration for plane-bun

Audience: an executor agent that follows instructions literally and does not
improvise. Every design decision is already made here. Where you must observe
something, the decision rule is given. If an instruction fails in a way this
document does not cover: STOP, record the exact command + error, do not
"fix" by deletion or substitution.

Base: `noor-latif/plane-bun`, default branch `preview` (must already contain
commit `44fdd8c47`, the flox environment). Work on one branch: `feat/bun-migration`.
The benchmarks/ directory contains the measurement harness; use it, do not
replace it.

---

## 0. Mission, scope, non-goals

Convert the JS monorepo (4 apps, 12 packages; pnpm 11.10.0 + turbo 2.10.11 +
vite 8.2.4 + vitest 4.1.11 + tsdown 0.16) to **Bun 1.4.x as package manager,
script runner, and production runtime for the `live` server**.

Out of scope — do not touch:

- `apps/api` (Django) and `apps/proxy` (Caddy).
- vitest → bun:test rewrites. vitest stays; its binary keeps executing on Node
  (shebang resolution — see §1.10).
- Any dependency version bump except where explicitly instructed.
- Forcing build tools onto the Bun VM (`bun --bun` for vite/tsdown is NOT
  required; their shebangs resolve Node and that stays). The measured wins do
  not need it.

Measured baseline (commit `44fdd8c47`, 8-core/62 GB Linux laptop, pnpm store
isolated at /tmp/bench/pnpm-store-cold; medians, 3 runs for installs):

| Metric                                 | node/pnpm baseline                         | Measured with                                                                                  |
| -------------------------------------- | ------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| cold install                           | 9.1 / 9.2 / 10.6 s                         | `benchmarks/benchrun.py pnpm install --frozen-lockfile --store-dir /tmp/bench/pnpm-store-cold` |
| warm install                           | 1.75 s (3×)                                | same, store intact                                                                             |
| node_modules                           | 816 MB                                     | `du -sb node_modules`                                                                          |
| install peak tree PSS                  | 1.24–1.33 GB                               | benchrun.py                                                                                    |
| full build (`turbo run build --force`) | 49.2 / 55.0 s, peak tree PSS 4.29–4.34 GB  | benchrun.py                                                                                    |
| live boot → HTTP 200                   | 2.41 s                                     | `benchmarks/boot_bench.py`                                                                     |
| live idle PSS (12 s avg)               | 252 MB                                     | `benchmarks/sample_rss.py`                                                                     |
| live WS handshake ×200                 | p50 1 ms, p95 1 ms, max 6 ms, 1597 conns/s | `benchmarks/ws-bench.mjs`                                                                      |
| web dev server idle PSS                | 2.24 GB                                    | boot_bench.py                                                                                  |

Known pre-existing flake (present in baseline, not caused by this migration):
`@plane/blocks` test `src/select/select.test.tsx > selects a row the keyboard
walked past the rendered window` times out when the whole suite runs under
full-parallel turbo load; it passes when the package runs alone. The baseline
recorded it at turbo's **default concurrency** — do not pin `--concurrency`
on either side of the comparison. Gate rule (§8) accounts for it. Do not
"fix" that test.

## 1. Verified compatibility facts (ground truth; do not re-litigate)

1. **Catalogs**: supported since Bun 1.2.14; definitions live in **root
   package.json** (`workspaces.catalog`), NOT pnpm-workspace.yaml; `catalog:`
   refs migrate on first install. https://bun.com/docs/pm/catalogs
2. **Lockfile**: `bun install` auto-migrates pnpm-lock.yaml → bun.lock since
   1.2.23, exact versions + integrity preserved.
   https://bun.com/blog/bun-v1.2.23
3. **Overrides**: pnpm-workspace.yaml `overrides:` → root package.json
   `"overrides"` (npm-style; supports `minimatch@3`-style selectors and
   `router>path-to-regexp` indirect form used here).
   https://bun.com/docs/pm/overrides
4. **minimumReleaseAge**: Bun 1.3+ reads it from **bunfig.toml** only. This
   repo has NO active minimumReleaseAge — only `minimumReleaseAgeExclude`
   (8 entries) → move to bunfig `[install] minimumReleaseAgeExcludes`
   (trailing **s**).
5. **allowBuilds → trustedDependencies**: Bun's analog is
   `trustedDependencies` in root package.json; defining it **REPLACES** Bun's
   curated default allowlist. pnpm's `sharp: false` deny-entry has no Bun
   equivalent; include `sharp` in the trusted list (its install script only
   downloads platform binaries) so behavior matches pnpm's baseline builds.
   https://bun.com/docs/pm/lifecycle
6. **Turbo**: `turbo prune --docker` emits bun.lock since 2.5, stable 2.6+
   (repo has 2.10.11). Prune regression existed in 2.8.12–2.8.15: never
   downgrade turbo. `turbo run` itself is PM-agnostic.
   https://turborepo.dev/blog/turbo-2-6
7. **Docker**: `oven/bun:1.4-alpine` exists (amd64+arm64); sharp ships musl
   prebuilds and officially supports Bun (README: Node-API v9 runtimes
   "including Node.js, Deno and Bun").
8. **ws/hocuspocus on Bun**: `ws` server `upgrade`/`unexpected-response`
   events implemented since Bun 1.4.0 — exactly what hocuspocus 3.4.4 + ws
   8.21.0 need. The WS gate in §8 verifies it on this repo's server.
9. **setup-bun v2**: input `bun-version-file` reads `.bun-version`; no
   node-version-file input. https://github.com/oven-sh/setup-bun
10. **engines**: Bun ignores `engines.node` (issue #5846). Vite/tsdown/oxlint
    binaries still execute on Node via their shebangs regardless of PM — so
    Node remains needed for builds/tests/lint. Keep `engines.node`.

## 2. Phase 1 — Branch, install migration

```bash
git checkout preview && git pull --ff-only
git checkout -b feat/bun-migration
bun --version            # must print 1.4.x; if not, install Bun 1.4.x first
rm -rf node_modules      # only the root + workspace dirs bun will rebuild
bun install
```

This writes `bun.lock` and rewrites catalog definitions into root
package.json `workspaces.catalog`. **Verified at the base commit** (executor:
your numbers should match within network jitter): 1174 packages, `bun.lock`
created, catalog migrated **179/179 entries with zero version drift**, the
markdown-it override resolves to the pinned 14.3.3, and install prints
`Blocked 1 postinstall` naming `@parcel/watcher@2.5.4`
(`node scripts/build-from-source.js`). That block is EXPECTED here — it is
what §3.1 step 3 (`trustedDependencies`) resolves; confirm with
`bun pm untrusted` (must list exactly that one package). Any OTHER blocked or
failing package = STOP and report.

```bash
test -f bun.lock && echo lockfile-ok
grep -c '"catalog' bun.lock          # >0: catalog refs preserved
grep -A5 '"workspaces"' package.json | grep -q catalog && echo catalog-migrated
```

Catalog audit (STOP on mismatch): diff every version in the migrated
`workspaces.catalog` against pnpm-workspace.yaml's `catalog:` block. Any
version that differs = silent dependency drift repo-wide = STOP and report.

Override errors: if `bun install` fails naming a specific override entry,
STOP and report that entry verbatim. Do not delete or weaken override entries
to make install pass — they are CVE pins.

## 3. Phase 2 — Manifest surgery

### 3.1 Root `package.json` — precise edits

1. `"packageManager": "pnpm@11.10.0+sha512.…" → "packageManager": "bun@1.4.4"`.
2. `"engines"`: keep `"node": ">=22.22.0"`, add `"bun": ">=1.4.4"`.
3. Add `"trustedDependencies": ["@parcel/watcher", "@swc/core", "esbuild",
"msgpackr-extract", "turbo"]` (exact list; order irrelevant). Note
   `sharp` is deliberately **excluded**: pnpm's `allowBuilds` denies it
   (`sharp: false`) and sharp ships its platform binaries as
   optionalDependencies prebuilds, so no lifecycle script is needed — keeping
   it out preserves the deny on both sides. Empirical anchor: the baseline
   install ran lifecycle scripts for `@parcel/watcher` and
   `msgpackr-extract` (pnpm applies built-in defaults on top of its
   allow-map); Bun blocks everything not in `trustedDependencies`, so after
   this edit `bun pm untrusted` must report **sharp only** (it did report
   `@parcel/watcher` alone before the edit — that block is what this list
   resolves). Any other package on either side of the run/blocked split that
   differs from the baseline install log = STOP and report.
4. Workspaces (single authoritative form — an array cannot carry `catalog`,
   so the object form wins). Use an **explicit package list**, not `apps/*`:
   ```jsonc
   "workspaces": {
     "packages": ["apps/admin", "apps/live", "apps/space", "apps/web", "packages/*"],
     "catalog": { …migrated from pnpm-workspace.yaml, audited in Phase 1… }
   }
   ```
   This reproduces pnpm's `!apps/api` / `!apps/proxy` exclusions without
   relying on negation support. Apply the same replacement to the `packages`
   array of the object Bun's Phase-1 install generated (step 5).
5. Bun's Phase-1 install already generated the `"workspaces"` object with a
   `catalog` block — keep it, with `packages` set to the explicit list above.
   Verify: `apps/api` (name `plane-api`) may appear in bun.lock as a
   workspace member — it must contribute **no dependencies** (its
   package.json declares none). `apps/proxy` must not appear at all.

### 3.2 `bunfig.toml` — new file, repo root, full content

```toml
# Bun configuration. Replaces pnpm-workspace.yaml policy settings.
[install]
# Key verified against bun 1.4.4 (install completes; treat lockfile as frozen
# by default so --frozen-lockfile in CI is a no-op guard, not the only guard).
frozenLockfile = true
```

This repo sets no active pnpm `minimumReleaseAge`, so nothing else migrates
here. (If you reintroduce a release-age policy later, Bun 1.3+ spells it
`minimumReleaseAge` in this file; the exclude key is
`minimumReleaseAgeExcludes`.)

### 3.3 Files to delete at the END of the migration (Phase 7 gate, not now)

- `pnpm-workspace.yaml` — ONLY after you confirm bunfig + root package.json
  carry everything Bun needs AND `pnpm-workspace.yaml` would confuse no tool
  anymore. Before deleting, `grep -rn "pnpm-workspace" .github apps packages`
  → must be empty.
- `.npmrc` — pnpm-only settings (`node-linker=isolated`,
  `public-hoist-pattern`, …). Bun ignores it; delete it (§1.10: default
  linker is the hoisted layout — this is the expected disk win).
  In the same edit remove `".npmrc"` from `turbo.json`'s
  `globalDependencies` array (it would fail turbo's hash inputs on a
  deleted file). Keep the other entries (`.oxfmtrc.json`, `.oxlintrc.json`).

Do not delete either file before Phase 7's gate passes.

## 4. Phase 3 — Scripts

```bash
grep -rn "pnpm" --include=package.json apps packages package.json
```

Rewrite rules (mechanical):
| Old | New |
|---|---|
| `pnpm exec turbo` (script bodies) | `turbo` |
| `pnpm run X` (script bodies) | `bun run X` |
| `pnpm dlx` | `bunx` |
| `pnpm install` (docs/scripts) | `bun install` |

Known sites: `packages/i18n/package.json` has
`"build": "pnpm run generate:types && tsdown"` → `"bun run generate:types && tsdown"`;
root scripts already call plain `turbo`/`pnpm exec` patterns per the grep.
Corepack note: `corepack enable pnpm` lines in docs/CI die in Phase 4/5.

apps/live scripts (exact final bodies; both invocation forms verified on
1.4.4 at the base commit — `bun .` and `bun run .` both resolve package
`main` (`./dist/start.mjs`) and boot Express + Redis against a real valkey;
root `prepare` also runs under `bun install`, so husky hooks survive):

```jsonc
"dev":   "tsdown --watch --no-clean --onSuccess \"bun run --env-file=.env .\"",
"start": "bun run --env-file=.env .",
```

`dev` keeps the repo's existing watch build and swaps only the serve
command (`node` → `bun`) — this exact form is verified. Do NOT switch `dev`
to `bun run --hot ./src/start.ts` (running the TS source directly): that
variant is unverified against this app's tsconfig paths, `@effect/*`
imports, and the generated-`dist` contract; treat it as an optional
experiment only if `dev` misbehaves, and record which variant you kept.

No other app runs a JS server process in production (web/admin are static
bundles behind Caddy; space is `react-router-serve` on Node inside Docker —
unchanged, §6).

## 5. Phase 4 — CI

Files: `.github/workflows/pull-request-build-lint-web-apps.yml`,
`i18n-sync-check.yml`, `check-version.yml`. (API/migration workflows: no
change.)

In each job of the web-apps workflow replace this block:

```yaml
- name: Set up Node.js
  uses: actions/setup-node@v6
  with:
    node-version-file: .node-version

- name: Enable Corepack and pnpm
  run: corepack enable pnpm

- name: Get pnpm store directory
  shell: bash
  run: echo "STORE_PATH=$(pnpm store path --silent)" >> $GITHUB_ENV

- name: Cache pnpm store
  uses: actions/cache@v5
  with:
    path: ${{ env.STORE_PATH }}
    key: pnpm-store-${{ runner.os }}-${{ hashFiles('**/pnpm-lock.yaml') }}
    restore-keys: |
      pnpm-store-${{ runner.os }}-

- name: Install dependencies
  run: pnpm install --frozen-lockfile
```

with:

```yaml
- name: Set up Bun
  uses: oven-sh/setup-bun@v2
  with:
    bun-version-file: .bun-version

- name: Install dependencies
  run: bun install --frozen-lockfile
```

(setup-bun has built-in cache; the manual store-cache dance is redundant.)

Also per workflow:

- `i18n-sync-check.yml`: same replacement; `pnpm dlx tsx …` → `bunx tsx …`;
  drop the corepack step.
- `check-version.yml`: it only needs to read package.json version — replace
  setup-node with setup-bun (bun-version-file: .bun-version) and
  `node -p "require('./package.json').version"` stays valid JS.

New file `.bun-version` at repo root, single line: `1.4.4`.

**Cutover-commit discipline:** `pnpm-lock.yaml` is referenced by the
Dockerfiles (§6), by CI caches (`hashFiles('**/pnpm-lock.yaml')`), and by
turbo's globalDependencies (§3.3). Do NOT delete the pnpm files in a
standalone early commit — the branch's CI and image builds would go red
mid-playbook and a half-migrated tree becomes indistinguishable from a real
regression. Land §4–§7 as a reviewable series, then make **one atomic
cutover commit** that deletes `pnpm-workspace.yaml` + `.npmrc` +
`pnpm-lock.yaml` and carries every Dockerfile, workflow, `turbo.json`, and
`.flox/env/manifest.toml` edit together; run the local gates in §8 steps
1–3 BEFORE that commit, and expect the fork's Actions to run against the
pushed result.

`check-version.yml` note: it runs `node -p "require('./package.json').version"`.
setup-bun installs no `node` binary — switch that line to
`bun -p "require('./package.json').version"` (verified: `bun -p` exists on
1.4.4 and prints the version), or keep a setup-node step just for this
workflow if you prefer zero risk there.

## 6. Phase 5 — Docker

Rule per file: builder/installer stages switch corepack-pnpm → bun
(`COPY --from=oven/bun:1.4-alpine /bin/bun /usr/local/bin/bun` into the
node:22-alpine builder is acceptable and keeps Node available for
vite/tsdown shebangs; alternatively use `oven/bun:1.4-alpine` as builder base
and add node only if a shebang needs it — do NOT add node unless a build
fails without it). `turbo prune` lines stay; turbo emits `out/bun.lock` now
(§1.6) — update the installer COPY path from `out/pnpm-lock.yaml` to
`out/bun.lock`, and replace
`pnpm fetch` + `pnpm install --offline --frozen-lockfile` cache-mount pairs
with a single `RUN bun install --frozen-lockfile` (drop the
`--mount=type=cache,id=pnpm-store` lines; Bun's global cache works with
`--cache-dir` if you want a cache mount, but plain layer-cached install is
fine at this repo's size).

Runtime stages:

- `apps/web/Dockerfile.web`, `apps/admin/Dockerfile.admin`: runtime is
  Caddy serving static files — nothing changes except the builder stages.
- `apps/space/Dockerfile.space`: runtime CMD stays
  `./node_modules/.bin/react-router-serve ./build/server/index.js` (Node
  process, node_modules copied from installer — installer layout change is
  hoisted-node_modules; verify the runtime COPY set still captures
  everything: run the built image and curl the port).
- `apps/live/Dockerfile.live`: runtime CMD becomes
  `CMD ["bun", "apps/live"]` and the runner base switches from `base`
  (node:22-alpine) to `FROM oven/bun:1.4-alpine AS runner`. Live needs no
  build tooling at runtime; keep the COPY set
  (packages/, apps/live/dist, apps/live/node_modules, node_modules,
  apps/live/package.json) minus the esbuild/tsgolint `find -delete` line
  (bun image carries no npm-era leftovers; keep the line harmless or drop it
  — dropping is preferred). Alpine musl + sharp: §1.7 confirms support.
- `deployments/aio/community/Dockerfile`: its node stage copies
  `/usr/local/{lib,include,bin}` from `node:22-alpine` to run
  react-router-serve for space/admin under supervisor. Out of scope to
  re-engineer: this file builds from `makeplane/*` upstream images, so
  change ONLY the comment noting the node stage follows upstream, and leave
  mechanics untouched.

## 7. Phase 6 — flox manifest

`.flox/env/manifest.toml`:

```toml
[install]
bun.pkg-path = "bun"
nodejs.pkg-path = "nodejs_22"      # keep: vite/oxlint/vitest shebangs need node
pnpm.pkg-path = "pnpm"             # DELETE these three lines in the same edit
pnpm.version = "11.10.0"
pnpm.pkg-group = "pnpm"
```

Version constraint: the flox catalog carries `bun` **1.4.2** (checked via
`flox show bun`), not the 1.4.4 used elsewhere. Pin the manifest to the
catalog's (`bun.version = "1.4.2"`) OR add a `.bun-version` respect — the
deviation between 1.4.2 (flox dev env) and 1.4.4 (CI/Docker) is acceptable
within 1.4.x, but record it in the PR description; do not silently let the
two float.

`[services.web]` command: `exec pnpm dev` → `exec bun run dev`.

Update the manifest `description` toolchain line: `Node 22, pnpm 11.10.0` →
`Node 22, Bun 1.4.4`.

Verification of this phase happens in Phase 7 step 6; do not invent flox
commands.

## 8. Phase 7 — Verification gate (all must pass, in order)

Run from a clean tree: `find . -name node_modules -type d -prune -exec rm -rf
{} +` then `bun install --frozen-lockfile`.

1. **Install**: `bun install --frozen-lockfile` exits 0, twice in a row
   (idempotence). Then **resolver integrity** — every CVE floor and override
   pin from the migrated `overrides` must still hold, or the perf PR ships a
   security regression. At minimum:
   ```bash
   for pair in "markdown-it:14.3.3" "prosemirror-view:1.42.6" \
     "prosemirror-model:1.25.12" "prosemirror-state:1.4.4" "path-to-regexp:0.1.13" \
     "follow-redirects:1.16.1" "body-parser:1.20.9" "fast-uri:3.1.8"; do
     n="${pair%%:*}"; v="${pair##*:}"
     grep -q "\"$n\": \"$v\"" bun.lock || { echo "OVERRIDE DRIFT: $pair"; exit 1; }
   done
   ```
   Also verify exactly one React version in bun.lock and that the
   range-form floors (`browserslist >=4.28.7`, `toml >=5.0.1 <6`,
   `tinypool@2 >=2.1.2`, `proxy-addr >=2.0.8`) resolve inside their ranges
   (`bun pm ls | grep <name>` for each). Any drift = STOP; the install-time
   benchmark is only a same-graph comparison when this passes.
2. **Type/lint**: `bun run check` exits 0 (turbo → check:format/check:lint/
   check:types under oxlint/oxfmt/tsc on Node).
3. **Build**: `bun run build` exits 0; all 4 apps emit dist/build.
4. **Tests**: the root has no `test` script — drive turbo directly, at
   default concurrency on both sides (the baseline flake was recorded at
   default; pinning would lighten the after-pass and fake a fix):
   `bunx turbo run test --force`
   — every suite green EXCEPT the documented pre-existing blocks flake (§0).
   Decision rule: rerun only the failed package alone
   (`bunx turbo run test --filter=@plane/blocks --force`).
   Passes alone → record as known flake, proceed. Fails alone → real
   regression: STOP, report. You may not raise `testTimeout`, skip, or
   delete that case; if it times out again under matched conditions, report
   it as a finding.
5. **live runtime gate**:
   ```bash
   docker run -d --rm --name gate-valkey -p 6379:6379 valkey/valkey:7.2.11-alpine
   cd apps/live && bun run start   # with .env from .env.example
   ```
   Then, from a second shell — run it **from `apps/live`** (ws-bench resolves
   the `ws` package from `process.cwd()`):
   `cd apps/live && node ../../benchmarks/ws-bench.mjs ws://127.0.0.1:3100/live 200`
   must report `ok: 200, fail: 0`. Any handshake failure = Bun runtime
   regression in the ws path = STOP.
6. **flox**: `flox activate -d . -c 'bun --version && node --version'`
   prints both.
7. **Docker**: `docker build -f apps/live/Dockerfile.live .` succeeds;
   run the image with valkey up; ws-bench against it: 200/200.
8. **Benchmarks**: rerun the Phase-0 harness with `bun` substituted for
   `pnpm`/`node` per table in §9; fill the results table; commit it with the
   branch.
9. **Cleanup**: delete `pnpm-workspace.yaml` + `.npmrc` (§3.3); rerun gate
   steps 1–3 to prove nothing referenced them.

Rollback: branch-only; `preview` untouched. If the gate fails irrecoverably,
stop and hand back the branch with the failure log.

## 9. Results table (fill after Phase 7)

| Metric                        | baseline (pnpm/node) | after (bun) | Δ   |
| ----------------------------- | -------------------- | ----------- | --- |
| cold install median (3×)      | 9.2 s                |             |     |
| warm install (3×)             | 1.75 s               |             |     |
| node_modules                  | 816 MB               |             |     |
| build --force median          | 55.0 s               |             |     |
| build peak tree PSS           | 4.29 GB              |             |     |
| live boot → 200               | 2.41 s               |             |     |
| live idle PSS (12 s)          | 252 MB               |             |     |
| WS handshake p50/p95, conns/s | 1/1 ms, 1597         |             |     |
| web dev idle PSS              | 2.24 GB              |             |     |

Method notes: same laptop, same harness (`benchmarks/`), bun installs use
`--cache-dir /tmp/bench/bun-cache` wiped for cold runs; quote medians; state
that cold install is network-bound (the baseline logged 27–39 KiB/s registry
warnings) and node_modules size reflects Bun's hoisted default linker vs
pnpm's isolated linker. Fill the after column **only** from harness runs —
there are no projections in this document.

Variant labels for the after column: the JS build/test tools (vite, tsdown,
oxlint, vitest) execute on **Node via their shebangs** under `bun run`
regardless of package manager — so label build/test/dev-server rows
`pm-only` (that is what `bun run dev` measures). If you additionally measure
`bun run --bun dev` (forces the Bun VM), label it `pm-plus-runtime` as a
separate row; probe the flag on 1.4.4 first
(`cd apps/web && bun --bun vite --version`) and STOP if it errors rather
than reporting a broken number. WS rows: the client stays **Node on both
sides** (`node benchmarks/ws-bench.mjs`, as in §8) — only the server engine
changes, or the handshake number attributes client speed to the migration.
