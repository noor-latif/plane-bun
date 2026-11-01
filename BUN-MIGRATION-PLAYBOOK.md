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
full-parallel turbo load; it passes when the package runs alone. Gate rule
(§8) accounts for it. Do not "fix" that test.

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
package.json `workspaces.catalog`. Then verify, in order:

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
"msgpackr-extract", "turbo", "sharp"]` (exact list; order irrelevant).
4. Workspaces: root becomes
   `"workspaces": ["apps/*", "packages/*"]`. The pnpm `!apps/api` /
   `!apps/proxy` exclusions are dropped — both are already workspace members
   with zero JS dependencies (apps/api/package.json has no dependencies
   field; apps/proxy has none), so `apps/*` membership is harmless. Verify
   after install: `grep -c apps/api bun.lock` → 0.
5. After the first `bun install` of Phase 1 succeeded, root package.json must
   also contain the migrated `"workspaces": { "packages": …, "catalog": {…} }`
   object Bun generated. Merge: keep Bun's object form and ensure its
   `packages` array is exactly `["apps/*", "packages/*"]` and its `catalog`
   matches pnpm's catalog audit from Phase 1.

### 3.2 `bunfig.toml` — new file, repo root, full content

```toml
# Bun configuration. Replaces pnpm-workspace.yaml policy settings.
[install]
# Keep bun.lock as the single source of truth for dependency resolution.
frozenLockfile = true

[install.minimumReleaseAgeExcludes]
"@storybook/addon-docs@10.4.6" = true
"@storybook/builder-vite@10.4.6" = true
"@storybook/csf-plugin@10.4.6" = true
"@storybook/react-dom-shim@10.4.6" = true
"@storybook/react-vite@10.4.6" = true
"@storybook/react@10.4.6" = true
"storybook@10.4.6" = true
"@makeplane/propel@0.7.3" = true
```

(The excludes are moved verbatim from pnpm-workspace.yaml
`minimumReleaseAgeExclude`; the table-of-true form is how bunfig expresses a
string list here. If `bun install` rejects the table form, switch to the
array form documented at https://bun.com/docs/bunfig — same entries, same
order — and record which form you used.)

### 3.3 Files to delete at the END of the migration (Phase 7 gate, not now)

- `pnpm-workspace.yaml` — ONLY after you confirm bunfig + root package.json
  carry everything Bun needs AND `pnpm-workspace.yaml` would confuse no tool
  anymore. Before deleting, `grep -rn "pnpm-workspace" .github apps packages`
  → must be empty.
- `.npmrc` — pnpm-only settings (`node-linker=isolated`,
  `public-hoist-pattern`, …). Bun ignores it; delete it (§1.10: default
  linker is the hoisted layout — this is the expected disk win).

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

apps/live scripts (exact final bodies):

```jsonc
"dev":   "bun run --hot --env-file=.env ./src/index.ts",
"start": "bun run --env-file=.env .",
```

`bun run --hot` is Bun's watch mode (replaces `tsdown --watch --onSuccess
"node --env-file=.env ."`); `bun run .` resolves the package `main`
(`./dist/start.mjs`). If `bun run --hot ./src/index.ts` fails on a TS/path
alias the tsdown build had handled, fall back to the built output for dev too:
`"dev": "tsdown --watch --no-clean --onSuccess \"bun run --env-file=.env .\""`
— record which variant you kept.

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

`[services.web]` command: `exec pnpm dev` → `exec bun run dev`.

Update the manifest `description` toolchain line: `Node 22, pnpm 11.10.0` →
`Node 22, Bun 1.4.4`.

Verification of this phase happens in Phase 7 step 6; do not invent flox
commands.

## 8. Phase 7 — Verification gate (all must pass, in order)

Run from a clean tree: `find . -name node_modules -type d -prune -exec rm -rf
{} +` then `bun install --frozen-lockfile`.

1. **Install**: `bun install --frozen-lockfile` exits 0, twice in a row
   (idempotence).
2. **Type/lint**: `bun run check` exits 0 (turbo → check:format/check:lint/
   check:types under oxlint/oxfmt/tsc on Node).
3. **Build**: `bun run build` exits 0; all 4 apps emit dist/build.
4. **Tests**: `bun run test` — every suite green EXCEPT the documented
   pre-existing blocks flake (§0). Decision rule: rerun only the failed
   package alone (`bun run turbo run test --filter=@plane/blocks --force`).
   Passes alone → record as known flake, proceed. Fails alone → real
   regression: STOP, report.
5. **live runtime gate**:
   ```bash
   docker run -d --rm --name gate-valkey -p 6379:6379 valkey/valkey:7.2.11-alpine
   cd apps/live && bun run start   # with .env from .env.example
   ```
   Then, from a second shell:
   `node benchmarks/ws-bench.mjs ws://127.0.0.1:3100/live 200`
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
that cold install is network-bound and node_modules size reflects Bun's
hoisted default linker vs pnpm's isolated linker.
