# Plane Optimization Walkthrough and Session Handoff

## Read this first

This is the durable record of work, decisions, measurements, failures, and resume instructions. **Append to this file after every milestone.** Keep priorities, candidate comparisons, migration outlines, sources, and deferred security/release checks in [ROADMAP.md](ROADMAP.md); do not duplicate that changing plan here.

User intent: build an independent, general-purpose Plane fork with radical footprint reduction and preserved capabilities. The confirmed target, reference deployment, regression gates, and current batch boundary live in [ROADMAP's confirmed intent contract](ROADMAP.md#confirmed-intent-and-current-batch--2026-11-04). Existing protections remain in place; the target is not a promised result.

**Current outcome:** the earlier Bun migration is pushed on its feature branch. C1 remains **INCONCLUSIVE and unpromoted** after M13. **M15 completed the repair/reference experiment: all three constrained trials failed, so no successful equal-work baseline or new optimization win is established.** M15's batch is closed; **M16 (2026-11-07) is authorized and is now BLOCKED (2026-11-08)** — the local diagnosis, implementation and verification were carried through, but the reference recipe remains capacity-blocked at 2 vCPU and the ≥50% target is unreachable at this revision, with no release claim. Source/harness remain in `/home/noor/repos/plane-bun-scratch-20261102/lead-integration`; reviewed raw results are in `bench-results/p0-reference-v3/`. Reuse this work rather than repeating preflight, research, or the failed trials.

## Current repository and delivery state

Verified at handoff:

| Item                               | State                                                                                                                                                                                                                                                                                                                                                  |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Working checkout                   | `feat/bun-migration` at the finalization base `21237d3a8` plus later documentation commits; `git log` is authoritative (not re-pinned in prose)                                                                                                                                                                                                        |
| Fetched migration remote           | `origin/feat/bun-migration` matches the local branch (push `075fe5889..21237d3a8`); `git log origin/feat/bun-migration` is authoritative                                                                                                                                                                                                               |
| Fetched historical playbook remote | `origin/chore/bun-migration-playbook` at `cbcde5b56`                                                                                                                                                                                                                                                                                                   |
| Baseline revision                  | `44fdd8c47` (historical Bun-migration baseline; the P0 experiment baseline is pinned `075fe58895c3336eb6dda6855a636c75cfe69225` in the scratch clone)                                                                                                                                                                                                  |
| Checkout drift                     | Resolved 2026-11-09 by merging `origin/feat/bun-migration` (`6c6fb4ecb` isolated linker, `075fe5889` bunfig installers) into the local branch; no pull/reset/force.                                                                                                                                                                                    |
| New local deliverables             | Promoted commits `5a4f4cc4f` (Live store recovery), `95b5f4058` (grouped distinct count), `dec9f038c` (P0 harness/comparator), `af7caae8c` (`/bench-results/` ignore), `e920c3924` (M16 docs), plus the merge commit and this final state commit. Pushed to `origin` (`noor-latif/plane-bun`) only. Raw benchmark evidence stays local and gitignored. |
| Existing local material            | `.serena/` was already untracked; leave it alone.                                                                                                                                                                                                                                                                                                      |
| Upstream delivery                  | No upstream (`makeplane/plane`) push, PR, or merge. No merge into `preview`.                                                                                                                                                                                                                                                                           |

**M16 status (2026-11-07, updated 2026-11-09):** local diagnosis, implementation and verification are complete; the milestone is **BLOCKED** on capacity and on the ≥50% whole-stack RAM target (see the M16 results section), not complete. On 2026-11-09 the user explicitly authorised local commits, a non-destructive merge of `origin/feat/bun-migration`, a push to `origin` only, and a personal laptop deployment; all of those were performed. Nothing was pushed to `upstream`, no image was published, and no remote host was touched. The laptop app runs as compose project `plane-local` (loopback `http://localhost:8090`), and the benchmark stacks are stopped. Raw benchmark evidence lives in the gitignored `bench-results/` tree, so a fresh clone does not include it.

The historical playbook branch remains separate from this feature branch. The newly tracked P0 measurement harness is **present here**: `benchmarks/p0/` (collector) and `benchmarks/p0-workload/` (runner, probes, seeds, deploy override, patches, comparator) were promoted in `dec9f038c`; they are benchmark tooling, not shipped application code, and the raw results they produced live in the gitignored `bench-results/` tree.

## Evidence saved with this handoff

All available numerical raw result files were copied from the temporary benchmark directory into [bench-results/session-history/](bench-results/session-history/). This prevents a temporary-directory cleanup from destroying the before/after evidence.

- `baseline-install-cold-{1,2,3}.json`, `baseline-install-warm-{1,2,3}.json`.
- `baseline-build-{1,2}.json`, `baseline-test.json`.
- `baseline-live.json`, `baseline-webdev.json`.
- `baseline-lockfile-bytes.txt`, `baseline-nm-size.json.raw`, `baseline-store-size.txt`.
- `after-live-boot.json`, `after-webdev-boot.json`: rejected/superseded hoisted-layout samples.
- `after-live-boot-iso.json`, `after-webdev-boot-iso.json`: isolated-layout samples.
- `playbook-cbcde5b56.tar`: a local `git archive` of the recorded playbook, harness, and committed after-results from the playbook branch. Read members directly; extraction is not required.

**Format trap:** several `baseline-*.json` files contain command output followed by a final one-line JSON measurement. They are not valid whole-file JSON documents. Read the final record or use the reader's raw line ranges; do not repeatedly retry a whole-file JSON parser. The boot/idle measurement files are ordinary JSON.

The saved archive contains `BUN-MIGRATION-PLAYBOOK.md`, `benchmarks/README.md`, the existing Python/Node harness, and the playbook branch's `bench-results/` files. The original branch snapshot is also linked at [cbcde5b56](https://github.com/noor-latif/plane-bun/tree/cbcde5b56).

Server `.json.log` files, environment files, secrets, and agent transcripts were not added to the handoff artifacts. Research findings were consolidated into ROADMAP.md; raw model claims are not a second source of truth. Some historical after-build/install numbers survive only in the playbook/session record; that limitation is stated below.

## Milestone ledger

### M0 — Baseline and existing harness

**Revision:** `44fdd8c47`, pnpm package management and Node runtime. Prior work isolated the benchmark checkout; the harness already exists on the playbook branch.

**Before:** no measured comparison. **After:** baseline samples established; see the numerical table below. This is an evidence milestone, not an optimization.

- Cold and warm installs, forced builds, forced tests, Live boot/idle/WebSocket handshakes, and Web development boot/idle were recorded.
- Test concurrency was Turbo's default, not increased to hide the known failing case.
- Baseline forced builds completed all 15 tasks without cache hits.
- Baseline full tests failed the blocks select keyboard-walk case. Preserve the failure in any comparison.

### M1 — Bun package-manager, CI, Docker, and documentation cutover

**Revisions:** `9218ff9d7` (manifests), `abefc0d41` (CI), `fa5d2e802` (Docker), `ac977c7e6` (cutover/Flox lock), `9a19abb9a` (docs/dev containers).

**Before:** M0 pnpm/Node baseline. **After:** Bun package management and Live running on Bun; the first install layout was hoisted. Aggregate measurements are in the table; individual manifest/CI/docs commits were not benchmarked independently.

Changes recorded by the earlier implementation pass:

- Bun workspace/catalog configuration and lockfile replaced pnpm files; package manager pinned to Bun 1.4.4.
- CI retained pinned Node alongside Bun because build/test tools still launch Node via their shebangs.
- Docker installers use Bun; Live's per-service runner uses Bun. Web/Admin production images remain static Caddy; Space remains Node SSR.
- Dependency trust policy left sharp untrusted; install trust/pin/React-resolution checks were reported passing.
- Flox catalog supplied Bun 1.4.2 rather than the 1.4.4 CI/Docker pin; the deviation and local activation prerequisite were documented.
- Earlier check/build gates passed. Tests did not become green.

The initial hoisted-layout build and development-memory claims were superseded by M2. Do not present M1-only values as results for the shipped isolated configuration.

### M2 — Restore isolated layout and carry install policy into Docker

**Revisions:** `6c6fb4ecb` (Bun isolated linker), `075fe5889` (four production installers copy bunfig).

**Before:** Bun's hoisted install context. **After:** `[install] linker = "isolated"`, matching the former pnpm policy; `bunfig.toml` explicitly copied into pruned Docker installers. Numerical before/after values are below.

- Earlier verification checked that bare `bun install` honored the linker policy and that the Bun lockfile remained byte-identical.
- Forced build/check paths were re-exercised on isolated layout. The Live image was rebuilt and WebSocket-smoked on that layout.
- Web/Admin/Space production images were not all rebuilt after this change. Do not imply complete image coverage.
- The observed build difference is layout-associated. Samples were not interleaved and were collected in different scratch trees/times; the entire timing difference is not a rigorously isolated causal estimate.

### M3 — Close the missing test observation on the shipped layout

**Code change:** none. **Before:** isolated layout had check/build/runtime/image observations, but no full/package/file test observations. **After:** the missing test runs were performed in a fresh scratch clone of the shipped feature branch.

The results are in the test table below. The package-solo difference persisted under both Bun linkers; the file passed alone. This establishes an unresolved comparison outcome, not a Bun VM bug. Vitest ran on Node in these ordinary script invocations.

A previous handback said gates were green including tests. **That was wrong:** tests were exercised but remained red. The earlier file-solo acceptance rule was a recorded experiment decision, not proof of full behavioral equivalence or release readiness.

### M4 — Correct the historical playbook and preserve evidence

**Playbook head:** `cbcde5b56` on `chore/bun-migration-playbook`.

**Before:** hoisted/isolated measurements and runtime attribution had been conflated. **After:** linker facts, Docker bunfig-copy requirement, isolated boot/idle JSONs, and the unresolved package-solo finding were recorded and pushed. This milestone changed documentation, not performance.

Known historical-document imperfections remain in the archived source: the test row combines both layouts but retains a hoisted-only percentage delta, and some method prose still mentions the hoisted default. Use this walkthrough's explicitly labeled comparisons rather than silently treating every archived sentence as current.

### M5 — Full-stack replacement research and Pareto roadmap

**Code revision:** unchanged working checkout at `9a19abb9a`. **Before benchmark:** not taken. **After benchmark:** not taken. No proposed new optimization was executed.

The user approved four read-only research agents for this pass. Their slices covered backend/database; scheduling/broker/cache/storage; browser/editor/state/toolchain; and Live/runtime/deployment. Findings and primary-source references were consolidated into [ROADMAP.md](ROADMAP.md#full-stack-replacement-research), including corrections to overconfident draft claims.

Important conclusions to carry forward:

- Proven source-level coupling: server document codecs reach emoji suggestions that import React UI. Split schema/serialization from browser-only behavior before replacing the editor.
- Live eagerly registers a heavy PDF endpoint; no in-repo caller was found, but external callers are unknown. Lazy loading is a smaller experiment than endpoint deletion. Preserve the API-called `/convert-document/` path.
- Hocuspocus 4 offers Bun-native integration while retaining documented v3/v4 wire compatibility. The fork pins 3.4.4; custom hooks, Redis integration, and shutdown draining still need a bounded major-version migration.
- RabbitMQ removal via existing Valkey is a plausible topology change, not proven delivery/durability equivalence. Scope includes templates, dependencies, startup behavior, cache/broker databases, and crash recovery.
- Sessions are already PostgreSQL-backed. Valkey also supports caches/authentication counters/locks/Live broadcasts; deleting it needs a real replacement for those roles.
- PostgreSQL-specific arrays/queries/migrations make SQLite-class replacements architectural, not drop-in. Django's psycopg pool is a smaller experiment. Under ASGI use bounded pooling with `CONN_MAX_AGE=0`, not positive persistent-connection lifetime, and install the pool extra.
- Native TypeScript 7 is a released tooling candidate; existing tsdown declarations still consume the older TypeScript compiler API. Trial checking separately instead of blindly replacing the catalog pin.
- Valkey 9.1's vendor-measured per-key overhead reductions are not Plane RSS savings. Vite 8 already uses Rolldown.
- An editor ESM barrel does not by itself prove failed tree-shaking. Font-face declarations do not imply that every declared weight downloads in the browser.
- AIO assembles older upstream published images and runs multiple supervised programs. It does not inherit this fork's Bun runtime by changing one supervisor command.
- Default-off APM is an operator capability, not automatically dead code; source-unused package declarations are deletion leads, not proof of deployment-wide non-use.

The user asked whether to disable advisories. Recommendation was off/blocker-only for exploratory work and a focused review before retaining changes. **No advisory setting was changed.** Repeated stale advisories must not restart settled work or cause endless documentation edits.

### M6 — Durable walkthrough and evidence handoff

**Code change:** none. Existing measurements were recovered from local raw files and an immutable playbook snapshot, then persisted in this checkout. This file establishes the logging protocol below. No benchmark/check/build/test/deployment ran during wrap-up.

### M7 — Execution-readiness review

**Before: not measured; after: not measured; no runtime change.** Read-only inspection confirmed the checkout remains at `9a19abb9a`, two commits behind the fetched `origin/feat/bun-migration`; those commits add isolated linking and copy bunfig into four production installers. The saved harness README covers command/process-tree measurements, boot/idle, and sequential WebSocket handshakes, not the complete P0 application workload.

**Verdict:** proceed with isolated discovery after execution approval, not a rollout or blanket replacement program. No additional research agents, tests, benchmarks, installs, or services were run.

Before the next run:

- Use an explicitly selected scratch baseline at `075fe5889`, not the stale working checkout or historical pnpm baseline. Preserve this checkout and its uncommitted evidence.
- Resolve the queue ambiguity in favor of P0 first, then one measured candidate. ROADMAP's P1/P2/P3 order and its PDF-first shortlist are hypotheses, not two mandatory implementation queues.
- Declare numerical latency/queue-delay tolerances and the minimum useful memory saving before examining candidate results. Fix job arrival rate and mix, include a burst and recovery observation, and count durable outcomes; a self-throttling producer can hide lost capacity.
- For lazy PDF/schema experiments, measure cold idle, first use, and post-use steady state separately. Lazy imports can remain resident after first use; a cold-idle win alone does not prove lower long-running deployment memory. Keep PDF laziness and schema/UI separation as separate changes.
- Before broker consolidation, specify eviction behavior as well as persistence and restart recovery. Separate logical Valkey databases do not isolate instance-wide memory pressure or eviction policy; queued jobs must not be exposed to cache eviction.
- Reuse the existing measurement primitives, but add only the missing fixed application workload and simultaneous stack accounting needed for P0. Boot/handshake success is not an application-outcome check.

**Next bounded action:** approval-gated P0 on a host selected with the user at execution time, using synthetic data, followed by P1 only if effective worker concurrency and measured service cost justify it. Otherwise select the largest actionable measured cost; a bounded Live import experiment remains a valid separate hypothesis. The known blocks failure remains unresolved and was not rerun. The original remote-host assumption is superseded by M8.

### M8 — Prepare the lead-steered GLM swarm plan

**Before: not measured; after: not measured; no runtime change.** Updated ROADMAP's measurement contract, execution order, and [OMP swarm launch contract](ROADMAP.md#omp-swarm-launch-contract). User intent is a subsequent OMP session with lead-steered GLM 5.3 Flash subagents; this preparation did not launch them.

Verified advisory claims against source: root Compose builds fork images, loads the API environment file, hard-codes container names, and publishes proxy ports; Community Compose pulls published application images and separately pins one API worker. The committed API environment example specifies two workers, not proof of the running count. The dummy-data command requires an existing user and invokes its seed function directly, so it is not itself queue-load evidence. The historical harness README documents process/boot/handshake measurements, not the missing P0 stack collector.

Plan corrections: pinned scratch baseline `075fe5889`; clean scratch clone with colocated jj and one workspace per writer; three initial GLM agents for measurement, workload/isolation, and a read-only Live contract check; lead-only shared-file integration; concurrent independent implementation but serialized measurement. P0 uses explicitly labeled cgroup memory rather than relabeling historical PSS. Numerical gates precede candidate measurements; lazy imports include post-use memory; broker changes include eviction behavior. Completed replacement research is retained, not repeated.

Host availability was not verified. Neither the advisory's laptop-only claim nor the earlier remote-host assumption is an execution fact. Select the host and obtain a bounded execution/model-spend approval at launch; documentation is not permission to run heavy work on either machine.

**Verification:** inspected targeted source/configuration ranges and checkout metadata; checked the revised documents for stale execution instructions. No model calls, application edits, builds, tests, benchmarks, service starts, commits, pushes, or deployments. **Next:** follow ROADMAP's launch preflight and Wave A; record every completed milestone here, including explicit "not measured" preparation results.

### M9 — Empty-session execution entry point

**Before: not measured; after: not measured; no runtime change.** The user wants to start a fresh session with only "execute roadmap". ROADMAP now opens with that entry point: read this ledger, assume the integration-lead role, recover state, obtain only missing execution authorization, and continue through the swarm workflow without another planning pass.

Canonical documents and evidence remain in this checkout, lead-write-only. Launch-input copies are read-only agent snapshots; agents return workspace-local results for the lead to consolidate. No swarm, services, tests, benchmarks, commits, pushes, or deployments were launched by this documentation change.

### M10 — Local launch preflight and staging

**Before: not measured; after: not measured; no runtime change.** Lead-delegated preflight pass for the [OMP swarm launch contract](ROADMAP.md#omp-swarm-launch-contract), executed in this session with explicit user approval: local-only host (nixlab and all other remote hosts forbidden), local scratch clone, installs/builds/tests/temporary isolated containers/benchmarks, and paid `surplus/glm-5.3-flash:high` calls with 3 concurrent agents (max 4 for genuinely independent slices). Full command-level evidence in [bench-results/m10-local-launch/preflight.md](bench-results/m10-local-launch/preflight.md).

Verified facts: pinned baseline `075fe5889` resolves to full SHA `075fe58895c3336eb6dda6855a636c75cfe69225`, present locally and on the real GitHub origin (`refs/heads/feat/bun-migration`, exact ls-remote match). Canonical source/branch/history untouched at `9a19abb9a` (behind 2; no pull/switch/commit; the only canonical changes are this evidence record: M10 below plus the new preflight report). Launch-input staging at `/home/noor/repos/plane-bun-launch-inputs-20261102/` — ROADMAP.md, WALKTHROUGH.md, playbook-cbcde5b56.tar only, SHA256 source==copy verified (no secrets, no `.serena`). Clean scratch clone from the real origin at the pinned SHA, colocated jj 0.46.0 initialized while clean, four workspaces created and verified: `…/plane-bun-scratch-20261102/{lead-integration,measurement,workload,live-contract}`; agents use `jj new -m` per unit in their own workspace (jj 0.46 `workspace add` requires an explicit destination path). Wave A's three slices dispatched from these workspaces with the confirmed model; dispatches pass `:high` explicitly because the model's default effort is `max`. Approved paid model calls were used by this preflight and by the running Wave A agents; no application installs, builds, tests, services, or benchmarks have run yet.

Host findings: cgroup v2 confirmed (P0 `memory.current` instrument available), docker 29.9.2 + Compose v5.6.1 usable, 8 CPU / 62 GiB RAM (~30 available) / 494 GiB disk free, zero Plane containers/images. Host bun 1.4.4 matches the repo pin (`.bun-version`/`packageManager`) and CI installs that exact version; all eight app Dockerfiles use the **floating `oven/bun:1.4-alpine` tag**, so the actual image Bun version remains to be recorded at image build time, not claimed from source. uv 0.12.21 matches `apps/api` exactly. **Flox was not active** during preflight and stays untouched: its manifest toolchain resolves bun 1.4.2 (deviates from the 1.4.4 pin — not for repo builds), its dev-stack services (valkey 9.0.3, rabbitmq 4.2.5, minio 2025) are not the Compose benchmark stack (valkey 7.2.11, rabbitmq 3.13.6, pgsty/silo), and the hook-written `.flox/data/*` plus `apps/*/.env` from a prior activation are preserved as-is — no wipe or relocation performed, none needed, because no Flox-stack run is planned; never activate Flox blindly. Port accounting corrected: host epmd on 4369 is not a conflict for root Compose, which publishes no broker ports; only an experiment override publishing broker ports would need to avoid 4369. Proxy ports come from `LISTEN_HTTP_PORT`/`LISTEN_HTTPS_PORT` and must pick free values (tailscale interface already holds :443). Root Compose hard-codes 12 container names — the workload slice owns name/port/volume isolation in its override.

Not done this milestone: no application installs, builds, tests, services, benchmarks, or commits; approved paid model calls were used (this preflight and the Wave A agents); the known unrelated blocks package-solo failure was not rerun; ROADMAP unchanged. Next: integrate Wave A returns in the lead workspace, then approved verification and P0 under the recorded envelope.

### M11 — P0 whole-stack baseline (measured)

**Before: no whole-stack baseline existed; after: baseline B and C rounds measured, runner gate PASS zero failures both; verdict: baseline measured, contract NOT complete (superseded by M13).** Question: whole-stack container-accounted memory under a fixed synthetic workload at baseline `075fe5889`. Single changed variable: none (this is the baseline). Instrument: collector schema `p0-cgroup-v2/2` (cgroup v2 `memory.current` per experiment container, host mode, 2s cadence, 311/311 rounds valid per round; includes page cache — not PSS).

Stack deviations from pure `075fe5889`, logged explicitly: bench-only Dockerfile hooks COPY baked pruned lockfiles into the four pruned installers (turbo prune's Bun-lockfile reduction drops `once`/`wrappy` and further transitive entries reachable via the root override `@react-router/serve → express ^5.2.1`); baked with `oven/bun:1.4-alpine` resolving to bun **1.4.4**, digest `sha256:4cf82f5686b1…70a` (2026-11-02). Source revision otherwise exact. Seeding uses the patched `setup.sh`: `create_dummy_data` as an in-process direct call with `members=[admin]`, `module_count=6` (upstream task crashes on empty project-member/module populations — upstream baseline bug, worked around, not fixed); queue evidence comes only from the runner's real `issue_activity.delay` traffic.

**Numbers (B / C, fresh volumes per round):**

- Whole-stack observed peak: **2.536 GB / 2.492 GB**; peak anon: **2.037 GB / 2.091 GB**.
- Per-service peaks (B / C bytes): worker 819,974,144 / 839,589,888; api 401,039,360 / 417,202,176; live 343,564,288 / 325,926,912; db 292,859,904 / 263,942,144; mq 233,181,184 / 196,419,584; beat 156,549,120 / 155,041,792; space 123,121,664 / 110,055,424; minio 152,342,528 / 130,416,640; proxy 82,345,984 / 79,544,320; admin 20,959,232 / 16,785,408; web 16,195,584 / 22,327,296; redis 9,658,368 / 12,120,064.
- Workload: ~6/s offered (burst 20/s, 60–90s), ~2084 / 2068 ops achieved, **0 errors**; p50/p95 by class (ms): board 252/1772, 246/1892; list 159/1645, 153/1751; create 77/1031, 74/969; patch 72/1008, 69/995; edit 61/626, 59/611; file 107/1526, 103/1255. "Queue drained in 2.003s" is not measured task/queue latency: the runner samples queue depth only after the run and reads one zero, then sleeps a fixed 2s before recording `drain_s` ≈ 2.00s.

**Gates for candidates** (from observed B↔C variability: worker ≈2.4%, whole stack ≈1.7%; other services varied more, so per-service claims rest on that service's own pair): minimum useful whole-stack peak saving **≥5%** (>2× variability); a per-service claim is valid if that service saves **≥5% of its own peak** with whole-stack regression **≤2%** and no queue-delay regression (drain ≤2.5s, backlog back to 0 within the 240s recovery window); durable outcomes must match baseline (issues created/patched, files byte-exact, pages flushed per-op, reconnects); no new error classes; exactly one changed variable per candidate; keep/reject/inconclusive verdicts mandatory. M11 declared no numerical p95 regression gate (ROADMAP 51–52) — a contract gap carried into C1. Only B and C are eligible fresh-volume controls: 2 samples per condition, fewer than the ≥3 required, with no baseline/candidate alternation.

**Limitations:** page-binary gate carries the round-A caveat (pre-rotation-fix runner artifacts kept under `bench-results/p0-baseline/` for provenance; A2–A4 were gate-validation iterations); laptop-class host; cgroup `memory.current` includes page cache; docker engine 29.9.2; Flox untouched; canonical checkout remains uncommitted/unpushed; known blocks test failure unresolved and not rerun.

**Evidence** (all under `bench-results/p0-baseline/`): `collector-b.json`, `collector-c.json`, `runner-p0-baseline-a3.json`, `runner-p0-baseline-b.json`, `runner-p0-baseline-c.json`, `collector-smoke-idle2.json` (+ smoke/provenance artifacts from the gate-validation iterations). Scratch jj refs: `rqwzoumw` (B), `uwvuwous` (C), `4d9fcf05` (gate tolerance), `48cc1cde` (A3 evidence), plus the runner-fix chain (`lwkrnquk`, `txoylzux`, `pqtrnyuu`, `uqvpqrtl`, `oylounwu`, `vrlrnymq`, `wrmnklmo`, `orwypxwm`, `zoryssuk`, `svmytqyz`, `nkqotmps`, `xptlznzv`, `vsxrumoy`, `luqksqnz`, `zvznmsts`).

**Next bounded action:** superseded — see M13.

### M12 — Candidate C1: bounded Celery prefork concurrency (measured, INCONCLUSIVE per M13)

**Before: worker prefork default 8 children, whole-stack peak 2.536/2.492 GB; after: configured concurrency 2, whole-stack peak 2.285/2.245 GB (actual per-round worker process census unknown); verdict: original KEEP withdrawn by M13 — C1 is INCONCLUSIVE and not promoted.** Question: does an explicit small worker pool (concurrency 2 vs default 8 = host CPU count) cut resident memory at equal work?

Intended changed variable: worker-only env `CELERY_WORKER_CONCURRENCY=2` via the bench override (`BENCH_WORKER_CONCURRENCY=2` in `deploy/plane-bench.env`; compose worker `environment:`); NO application source change; api/beat untouched. Mechanism: the entrypoint passes no `-c`; no settings file sets concurrency; prefork default = `cpu_count` (8); a separate diagnostic worker banner showed concurrency 2; the normal worker environment was set to 2, but actual per-round worker process/concurrency evidence was not archived. See M13 for diagnostic-worker lifecycle uncertainty.

Two candidate rounds, fresh volumes each, same nominal workload recipe and collector instrument as baseline (M11); delivered operation counts differed. Gate PASS, zero failures, no tolerance, both rounds. Ops 2090 / 2127 (baseline 2084 / 2068). Worker peak 641,839,104 / 641,089,536 B vs baseline B 819,974,144 / C 839,589,888 → **worker −22.7% in the FIRST TWO C1 rounds vs the B/C pair** (not an all-three mean, not statistical significance). Whole-stack peak 2,285,228,032 / 2,245,283,840 B vs 2,536,050,688 / 2,492,260,352 → **−9.9% vs mean ≈ 5× observed variability; memory-gate arithmetic supports a signal, NOT durable-parity proof or capacity headroom.** Quoted p50s, some increased: list 161/159 vs 159/153 ms; create 75/72 vs 77/74; patch 73/72 vs 72/69; edit 62/60 vs 61/59; file 104/105 vs 107/103. Queue: one post-load zero reading + fixed 2s sleep — "no backlog under burst" is withdrawn; burst-window depth was never sampled. Created/patched counts record accepted HTTP operations (210/214 created, 419/425 patched); the 10 issue reads check non-empty `name` only; `pageFlushes` count heuristic provider confirms (312/318), not persisted markers. Withdrawn claim: "every edit flushed". Files byte-exact 104/104 and 106/106 is valid. Reconnect counts 104/106.

M11 gates as scored by the then-runner: whole-stack ≥5% PASS; per-service ≥5% of own peak PASS (22.7%); whole-stack regression ≤2% PASS (improved); drain ≤2.5s PASS; durable match PASS; no new error classes PASS. Gate PASS means the current flawed runner accepted these reports; it is not durable-outcome proof. **VERDICT (M13): INCONCLUSIVE — original KEEP withdrawn; C1 is not promoted.**

Limitations: single host, one task mix, no p95 gate. Worker window CPU (collector 2s in-window delta sums, correct instrument, not lifetime counters): B 74.062376s / C 71.631400s vs C1 247.883940s / repeat 242.054734s / third 244.082237s. Whole-stack window CPU B 514.704994s / C 500.800788s vs C1 651.405938s / 645.598744s / 637.197641s; per reported op (total window CPU / ops) B 247.0 / C 242.2 ms vs C1 311.7 / 303.5 / 299.9 ms — arithmetic mean ~25% higher. These ~620s windows include recovery/idle time; not isolated task CPU. The A3 60.7M figure is removed (no canonical artifact pair); the "fewer children absorb the same work" explanation is withdrawn as unsupported. No 3-sigma or money-cost inference is made. C1 rounds are the only rounds with this env set; the baseline remains the 8-children default.

Evidence: `bench-results/p0-candidate-c1/{collector-c1.json, collector-c1-r2.json, runner-p0-candidate-c1.json, runner-p0-candidate-c1-r2.json}`. Scratch jj refs: `snpoluyy` (wiring), `qmwxtxwu` (C1 r1), `lrmkzvuy` (C1 r2).

Next bounded action: superseded — see M13.

Combined verification round (fresh volumes, `stack candidate-c1-combined`, label `p0-candidate-c1-combined`): gate PASS, zero failures; ops 2125; worker peak 683,720,704 B (0.64 GiB); whole-stack peak 2,308,571,136 B (2.15 GiB); one post-run zero queue reading, fixed 2s sleep; durable counters 213 created / 424 patched / 10-10 reads / 318 heuristic page confirms / 106-106 files byte-exact — raw figures retained for provenance. This third round is merely another same-single-candidate repeat, not a combined-winner verification; "Verdict stands" is corrected to inconclusive. Evidence: `collector-c1-combined.json`, `runner-p0-candidate-c1-combined.json`.

### M13 — Harness sanity review (docs-only)

**Before: not measured; after: not measured; no runtime change.** Read-only review of existing P0 artifacts and the scratch harness at `/home/noor/repos/plane-bun-scratch-20261102/lead-integration` (`benchmarks/p0-workload/run.ts`, `seed/setup.sh`, `deploy/docker-compose.bench.yml`). Supersedes M11 completion and M12 KEEP: **C1 INCONCLUSIVE, not promoted.** Overall outcome: continue the research direction; pause candidate progress pending measurement repair and intent confirmation. Do not move to API/Live/P2 or promote C1 until the repairs below hold; no demand for all roadmap menu items.

**Runner false-pass paths** (actual code, scratch `run.ts`; no data loss asserted — these are false-pass paths):

- `run.ts:313-356, 721-723`: superseded page-edit generations confirm with `before = 0` (any non-empty body passes); no HTTP/content check; the binary post-check gates only the first 3 of 12 pages. `pageFlushes` counts heuristic confirms, not persisted markers — "every edit flushed" withdrawn.
- `run.ts:694-705`: no expected-patch-name comparison; patches verified by HTTP acceptance only.
- `run.ts:675-687`: queue depth sampled only post-run; one `[0]` reading plus a fixed 2s sleep — no burst-window depth, no job-completion proof.
- `run.ts:638-666`: 64-promise batch waits + skip-on-lag mean the schedule is not a fixed delivered arrival rate. Nominal recipe: 6×270 + 20×30 = 2,220 slots; skipped arrivals B 134 / C 150, C1 128 / 91 / 93 — skipped arrivals are not gated. No error was reported in the five selected runs; the transient-tolerance branch (≤ `max-op-failures`) can PASS with `pass_with_tolerance: false` because its marking branch is unreachable (not consumed in these five runs).

**Experiment-control gaps:** `setup.sh:104-105` rebuilds all images every round — app image IDs differ per round (recorded in collector `containers[].image`); a different ID does not prove executable/dependency drift, but a fixed-same-image comparison is unestablished. `setup.sh:174-210` recreates random dummy data with no seed/content-identity proof — same 400/12 shape, not identical content. The compose worker override `CELERY_WORKER_CONCURRENCY: ${BENCH_WORKER_CONCURRENCY:-2}` defaults to the candidate value: a future run labelled "baseline" silently runs concurrency 2 unless the knob is explicitly set (source not changed now). Extra diagnostic celery workers were launched by a compound reset+setup command to read the concurrency banner each C1 round; their actual start/termination times are NOT known, no PID census or cleanup was recorded, `timeout 12` is not proof of death, and the command start timestamp is not the inner worker start time. Actual process concurrency per round is not archived. No attribution of the CPU increase to orphans is made as fact.

**Operational breach (recorded, not repeated):** original-session tool call `call_35faae31a28f4f4590ecca26` at 2026-11-02T14:22:09.599Z executed `sudo -n systemctl restart docker` (host-wide daemon) as part of proxy troubleshooting; tool result 14:22:35.750Z (`isError: false`) showed the bench containers exited. This exceeded the isolated-bench-stack scope; impact on unrelated host containers is UNESTABLISHED (no claim of no harm). Source: `history://local-launch-preflight` (transcript/secrets not copied here). No host-wide restart or prune in resumed work without specific authorization.

**Next bounded action (after intent confirmation): smallest harness repair —** prove persisted edit markers / expected patch values / background-job side effects; sample queue depth through the load window; do not silently omit arrivals; freeze image + dataset and record actual worker processes without launching new workers; predeclare p95 and CPU-tradeoff gates; run ≥3 alternating fresh samples per condition with the same fixed recipe. With a changed harness, do not pool old controls as if identical.

### M14 — Intent alignment confirmed (docs-only)

**Before: not measured; after: not measured; no runtime change.** User confirmed the [current intent contract](ROADMAP.md#confirmed-intent-and-current-batch--2026-11-04): independent product, preserved capabilities with lossless migration allowed, at least 50% RAM reduction, and a 5% latency/CPU regression ceiling on the agreed reference workload. Current batch is harness repair and a trustworthy baseline, then reassessment—not candidate implementation. This confirmation settled intent only; the subsequent local execution approval is recorded in M15.

Resume from M13 and the existing scratch workspace. Resolve missing execution permissions once for this bounded batch; do not reopen the settled product questions or treat historical broad execution instructions as permission to advance candidates.

### M15 — Reference harness repair and failed constrained trials

**Before:** old exploratory controls did not satisfy M13. **After:** repaired outcome checks and three new reference measurements, **all workload FAIL**; no successful equal-work baseline, retained optimization, or 50% reduction claim. This closes the approved repair/reference batch for reassessment, not the product goal.

**Scope and source:** user authorized local scratch edits, necessary installs/builds/tests, temporary isolated containers/snapshots, and baseline runs, with no additional paid agents. Reused the existing lead-integration workspace and locally retained images; no application source change, image rebuild/install, remote execution, canonical source promotion, push, or deployment in this batch. Scratch change `pkmpzmqk`, snapshot `61bca71d`, holds `benchmarks/p0-workload/` repairs and raw scratch results. The known unrelated blocks failure was not rerun.

**Controls:** application baseline `075fe58895c3336eb6dda6855a636c75cfe69225`, with the previously disclosed baked prune-lockfile image deviation. One stopped-volume snapshot restored before every sample; all four archive hashes and image IDs checked on restore. Independent artifact analysis confirms equal per-service image maps across all three collectors. Synthetic shape: 400 issues, 12 pages, 20 users. Only the collaboration fixture page was made public so five users could legitimately edit it; application authorization was not weakened. RabbitMQ hostname is fixed so its persisted node directory survives container recreation.

The experiment-only systemd slice shared **2 CPU equivalents / 4 GiB total, swap disabled**, across all containers. Actual limits were `cpu.max=200000 100000`, `memory.max=4294967296`, `memory.swap.max=0`. Reference worker concurrency was explicitly **two**, matching the intended two-vCPU deployment rather than Python's eight-CPU host discovery; archived normal-process inventories verify one Celery master plus two children in each sample. An eight-child preparation was cancelled before collection; its excluded restore/process-inventory artifacts remain only in scratch `bench-results/p0-reference-v3-aborted-eight-child/`. Reference sizing is not a C1 win. Host OS/Docker/load-generator overhead is outside the slice: these are not physical 4-GiB-machine fit measurements.

**Repaired harness:** exact persisted Yjs edit markers and reconnect checks replace binary-growth heuristics; all acknowledged issue final names and every unique patch activity are read back. Background evidence uses the actual API → RabbitMQ → Celery `IssueActivity.new_value` path. The scheduler accounts for every planned slot and rejects overflow explicitly, failing on any operation error, rejected/missing completion, or dispatch lag over 100ms. Twenty actors rotate roles; five persistent editors share one page. Queue depth is timestamped during load and recovery; missing observations fail. Private snapshots, credentials, and server logs stay out of canonical evidence.

**Recipe/instrument:** same 60s quiet warm-up and fixed 620s collector window per restored stack, with 30s pre-run idle. Runner authenticates users/opens editors before its 300s load clock; 6 operations/s with 20/s during seconds 60–90 gives **2,220 planned operations**; recovery allowance 240s. Collector schema `p0-cgroup-v2/2`, host mode, 2s cadence, **12 containers and 311/311 valid rounds in every run**. Stack peak is the maximum simultaneous sum of `memory.current` including cache, not RSS/PSS or the sum of independent service peaks.

| Observed metric                          | Reference 1   | Reference 2   | Reference 3   |
| ---------------------------------------- | ------------- | ------------- | ------------- |
| Whole-stack peak, bytes                  | 2,292,084,736 | 2,235,506,688 | 2,246,397,952 |
| Finished attempts / planned              | 1,994 / 2,220 | 2,042 / 2,220 | 2,042 / 2,220 |
| Successful attempts                      | 1,433         | 1,844         | 1,831         |
| Operation errors                         | 561           | 198           | 211           |
| Rejected arrivals                        | 226           | 178           | 178           |
| Persisted / acknowledged edit markers    | 220 / 248     | 71 / 261      | 274 / 276     |
| Byte-exact downloads / uploads           | 76 / 94       | 97 / 102      | 98 / 105      |
| Maximum observed queue depth             | 249           | 78            | 119           |
| Whole-stack CPU, seconds per 620s window | 728.651608    | 637.019959    | 659.918546    |
| Workload verdict                         | FAIL          | FAIL          | FAIL          |

All 20 actor IDs appear in every run. Dispatch lag maxima were 33.3/7.7/5.0ms, but the 256-in-flight bound rejected arrivals under the burst. Errors included 30s HTTP timeouts, 502s, editor sync timeouts, and failed reconnect-marker checks. All acknowledged patch activity records were verified (308/382/376); that does not make the failed operations successful. Final queue observations were zero in all runs, but missing durable outcomes prevented full recovery. The persistence counter stays zero unless _all_ markers pass; the table independently compares the saved expected marker sets with persisted XML and exposes the partial counts.

**Resource observation, not a causal diagnosis:** the sampled 28s inside each burst consumed 55.955174/55.969880/55.973380 CPU seconds, approximately the entire two-CPU allowance. API plus PostgreSQL dominated total window CPU. No parent `oom_kill` increment or container OOM flag was recorded; all 12 containers were running at each post-run check. The exact causes of timeouts and missing markers were not isolated. Reported p95 includes failed/queued attempts (board/list approximately 30s; issue patches approximately 90–130s), so it is not a successful-work latency control. Do not normalize CPU by unequal finished work or compare these peaks with old B/C/C1 as optimization savings.

**Verification and fault checks:** scoped TypeScript check passed; four contract tests passed; collector self-check passed 19 checks; shell syntax and Python compilation checks passed. The first strict private-page smoke rejected all 27 missing markers; inspection showed Live saves returning 403 for non-owner edits of the private fixture. After making the shared fixture public, all 27 markers persisted and survived reconnect, but that smoke still correctly failed on two 502 operation errors. A separate real fault check paused only the experiment worker: all 88 operations recorded zero operation errors, while missing activity records and queue depth 152 correctly failed the durable/recovery gates; the worker was unpaused in `finally`. This is targeted workflow/fault evidence, not full application or browser coverage.

**Evidence:** canonical `bench-results/p0-reference-v3/` contains three `reference-v3-{1,2,3}-{collector,runner}.json` pairs, three worker process inventories, `provenance.json`, independently recomputed `summary.json`, and the three smoke/fault reports. All **14 copied files (5,492,569 bytes)** were source/copy SHA256-checked; `manifest.json` records each hash and size. No private snapshots, environment files, credentials, server logs, or transcripts were copied. Collector peaks and CPU last-minus-first totals were independently recomputed from raw rounds. Scratch jj's 1-MiB new-file limit initially omitted the collectors; a one-command 2-MiB override included them without changing persistent settings.

**Teardown observed:** project-scoped containers, network, and all four volumes removed; temporary slice stopped/reverted. Final project volume listing was empty; unrelated `omarchy-windows` and healthy `gatwy` were up at inspection. This is not a claim of uninterrupted unrelated-service uptime. Images and private reproducible snapshots remain in scratch; no global prune or Docker-daemon restart occurred in this batch.

**Verdict and next boundary:** retain the harness repairs in scratch; **reject these trials as a successful equal-work baseline**, and keep C1 inconclusive. The agreed workload/reference pair failed; lowering load or relaxing gates is not an authorized repair. Before any footprint candidate, agree on a bounded diagnostic batch to isolate API/database burst cost and the missing-marker path, retaining the same correctness contract. Active database connection counts, a successful reference control, true cold-idle/expensive-feature measurements, and full capability coverage remain unestablished. No further execution or paid-agent approval is inferred from this completed batch.

### M16 — Local diagnosis and completion (BLOCKED, started 2026-11-07; blocked 2026-11-08)

**Before:** M15 closed with three failed reference trials and no passing equal-work control; C1 inconclusive. **After: not measured.** No application source change, benchmark sample, or verdict exists for this milestone at the time of this entry.

**Approval (2026-11-07):** complete the project locally with two coding workers — local installs, heavy builds, tests, isolated containers, and repeated benchmarks. Still excluded: remote hosts, host-wide restarts, paid models beyond those workers, canonical source promotion, canonical commits, pushes, image publishing, deployment, and real credential rotation. Browser verification stays user-driven. The settled contract carries forward unchanged (workload, 2 vCPU / 4 GiB envelope, ≥50% whole-stack RAM target, ≤5% per-class p95 / whole-stack CPU ceiling); M15's failures and C1's verdict remain as recorded.

**Stop boundary:** finished authorized local implementation and verification with the recorded contract evaluated, or a demonstrated unreachable prerequisite recorded as **BLOCKED and incomplete**. The old "stop and reassess after the reference batch" boundary no longer applies.

**Starting evidence:** a read-only protocol review of the harness editor path against installed Hocuspocus 3.4.4 and the Live persistence hooks found three issues to settle before any candidate comparison. (a) A failed reconnect leaves a destroyed provider and doc in the editor map, so later edits on that editor are recorded as acknowledged with no transport at all. (b) The server acknowledges updates in memory and stores them on a 10 s debounce, so per-op edit success and edit latency are acknowledgement metrics, not durability; the final database read is the only persistence signal. (c) Each reconnect spends part of its 15 s budget on an API authentication round-trip, while the measured API p95 of 30 s belongs to board/list calls; whether the auth call shares that tail is a **hypothesis** until reconnect timestamps exist, so some `editor sync timeout` / `reconnect lost acknowledged edit` results may be deadline artifacts rather than data loss. That remains possible, not observed. The recorded run-2 loss (190 of 261 markers with only 2 reconnect failures) is a persistence-path signal rather than a client acknowledgement bug; the run-3 two-marker loss matches the dead-editor path. All of this is static source analysis; the runtime proof is still outstanding.

**Next bounded action:** capture Live store-hook and document load/unload counters in the same run as the reference workload, fix the dead-editor path, and obtain one passing reference control before selecting any candidate.

### M16 — results (2026-11-08)

**Before:** three failed reference trials (M15), no passing equal-work control, C1 inconclusive. **After:** measured; the contract is evaluated below and the 50% target remains unreachable at this revision.

**Harness validity first.** The editor reconnect path was fixed (`benchmarks/p0-workload/editor-reconnect.ts`, transactional replacement; `assertConnected` guard) and the workload runner gained two instrumentation-only fields (`offered.rejected[]`, per-op read payload `items`/`outcome.readItems`); schema is now `p0-workload/5`. A recipe-identity proof shows the unchanged recipe still produces 2220 slots (360 steady + 600 burst + 1260 tail); CYCLE, rates, durations, seed, and images are untouched. I1 classification: a failed file round trip is a _reported_ operation error (`fileRoundTrip` returns `{ok: match}`, `op()` records `ok:false`, it counts in `achieved.errors` and the gate), so it is an integrity failure, never silent loss; v4-1 stays harness-valid.

**Store-durability fix (Live, baseline dependency).** A page store that timed out used to leave the pristine Hocuspocus debouncer's rejected execution retained, permanently stopping later stores for that document. The fix lives in `apps/live/src/extensions/database.ts` (+ narrow `core.service.ts`, `handler.ts`, `broadcast-error.ts` changes), with `@hocuspocus/server@3.4.4` **unmodified**: retries are awaited inside the store hook (saveMutex held, unload is a no-op), 2 attempts within a 15 s budget, 1 s backoff, 8 s final unload attempt (worst case 23 s, under the 25 s shutdown timeout), no retry for 4xx/413, one broadcast per failure episode, per-page unsaved tracking capped at 500 without bulk clearing, and only the five retry-needed context fields retained (dropped on success, non-retryable failure, force-close, unload, `onDestroy`). Prepared against a locally rebuilt Live image `sha256:08035905…`; proofs cover later-edit, no-edit, close, close-with-API-down, two concurrent editors, drain with an in-flight retry, drain with the API down, 413 force-close, 400 PAGE_LOCKED, and the pristine-app wedge control. Residual stated plainly: if the API is unavailable for the whole budget and the final attempt, the acknowledged edit is lost, logged as `page_store_loss`, counted and broadcast once; the durability gate detects it. (An observed force-close log contradiction — "unloaded successfully" then "still in memory" — is pre-existing `force-close-handler.ts` behaviour, `git blame 64781be7d`, and was not fixed here.)

**Control trio (fixed app, unchanged recipe/gates/limits).** `bench-results/p0-reference-v6/` labels `reference-v6-{1,2,3}`: gates **FAIL** on capacity as predicted — completed 2170/2168/2152 of 2220, errors 22/27/41, rejected 50/52/68 — but harness-valid: **0 missing page markers** in all three, `page_store_loss` 0, activities 427/426/424, files 108/108, 110/111 (111 uploaded; this sample's gate carries `file-roundtrip-mismatch`), 107/107. Cores pre 0.776–0.798 (load-window averages), **30 s burst window 2.001/1.999/1.999** (the earlier 1.956–1.999 figures averaged a 100 s window), post 0.904–0.930; peak current 2297–2364 MB; API inventory one gunicorn master + two uvicorn workers per run. **CPU metric (definition):** whole-stack CPU over the **load window only** — the first collector round at or after `load_started_at` through `load_completed_at + queue.drain_s` — divided by **successful** operations (`total_ops − errors`). The collector window is 620 s and is _not_ the denominator basis; an earlier revision of this record divided the whole window by dispatched ops (diluted by the idle tail and crediting fast-failing runs) and is corrected here. Corrected control values: 0.186557 / 0.194024 / 0.193253 core-s per successful op (median 0.193253). **Superseded:** the earlier 0.190/0.198/0.196 figures used the diluted metric.

**C+D2 screen.** `bench-results/p0-reference-v7/reference-v7-screen-cd2b-1` (corrected combined patch, sha256 `2c095bcb…`; the 8-case parity matrix is hash-bound but covers only FK group-bys — `state__group`, `priority` — and the ungrouped list, so it proves those paths unchanged and is **silent on D2's membership-vs-distinct change**; that change is covered by the new pytest class below): gate FAIL — completed 2199/2220, 15 errors, 21 rejections, burst **dispatched 19.30/s** (579 slots / 30 s; 18.80/s successful) vs 20/s; per-successful-op 0.174301 core-s on the corrected load-window metric (ratio 0.9019, **−9.8%** vs the 0.193253 control median; still **directional only** — one sample, and see the limitations), peak current 2232 MB / anon 1945 MB / file 157 MB, durability clean. **Superseded:** the earlier −8.3% (0.1797) figure used the diluted whole-window / dispatched-op metric. (0/330 markers missing, 110/110 files, 434/434 activities, `page_store_loss` 0). Per Step B this is a **capacity FAIL**: candidate measurement stops and no C-alone/D2-alone/C+D2 series is run. Limitations recorded: (1) the v6 control predates the instrumentation (`p0-workload/3`), so read-payload equivalence with the control is unknown and a clean candidate claim would need a `/4`-or-later control series this batch does not fund; (2) one sample of a combined patch is not attribution; the first screen attempt (`reference-v7-screen-cd2-1`) is preserved as a patch-assembly defect (554 list reads returned 500 from `length=page_count` inserted into `OffsetPaginator`'s return), never a capacity result; (3) per-kind p95 was not compared against the control, so no latency win is claimed. The v7 screen's read-pages payload is uninstrumented (bare-array response was only instrumented after that run), so payload-equivalence statements are limited to the board and list kinds.

**≥50% RAM unreachability.** From the memory-composition analysis (§4/§6, verified): v4-1 leaves 1,077,116,928 B anon against an 872,908,800 B allowance, and v4-2 1,072,455,680 B against 986,488,832 B. Those v4 peak-round artifacts live only in the scratch tree (`bench-results/p0-reference-v4/`), not in the canonical checkout. The whole-stack target is therefore unreachable at this revision even before any candidate; the remaining lever is the ungrouped issue-list endpoint (about a third of burst work, untouched by C/D2), which cannot be sized from existing evidence.

**Retention tests (new; promoted locally, unpushed).** Canonical now contains the test class (`apps/api/plane/tests/unit/utils/test_paginator.py::TestGroupedCountUsesDistinctIssues`), the grouped-paginator hunk, and `apps/live/tests/extensions/database.test.ts`. (A) `apps/api/plane/tests/unit/utils/test_paginator.py::TestGroupedCountUsesDistinctIssues` — five DB-backed tests driving `BasePaginator().paginate(..., group_by="labels__id")` against real PostgreSQL: pre-patch red run showed `total_count` 3 (project), 3 (sub-grouped), 4 (zero-label case) where distinct-issue truth is 2/2/3; with D2 applied the file passes 23/23. Observed semantics: V1 = LEFT OUTER (the unlabeled issue survives under the `None` group, count 3 == visible 3); V3 = surfaces without `.distinct()` stay unchanged-wrong: with both lookups the real filter path applies (`labels__id__in` plus `label_issue__deleted_at__isnull`) the observed count is **5** joined rows for 2 distinct issues. (B) `apps/live/tests/extensions/database.test.ts` — six vitest cases through the real Hocuspocus debouncer/saveMutex with fake timers and a stubbed page service: retry-then-success, exhausted budget resolves + loss logged once, 413 and 403 never retried and never pinned, once-per-episode broadcast, cap refusal without bulk clearing, context dropped and never logged. (C) Real gates: `docker compose -f docker-compose-test.yml run --rm --build api-tests pytest -m unit` → **568 passed, 923 deselected**; `bun run test` in `apps/live` → 46 tests passed, with two pre-existing file-level resolution failures (`@plane/decorators`, `@plane/logger` entry resolution) unrelated to this change. **CI finding:** no workflow runs pytest or vitest (workflows are build-branch, check-version, codeql, copyright-check, feature-deployment, i18n-sync-check, migration-check, two lint jobs, react-doctor), and `hocuspocus-v3.test.ts` is skipped unless `TEST_REDIS_URL` is set — so these suites only run locally and the existing integration file has been silently skipped. Not fixed here (out of scope/unauthorized). Live-suite breakdown from the final run: 5 files / **47 tests passed** — `wait-for-redis` 2, `drain-server` 6, `extensions/database` 7, `pdf-export/effect-utils` 10, `lib/pdf/pdf-rendering` 22 — plus 2 files failing at collection with 0 tests (pre-existing package-entry resolution: `server-destroy` via `src/server.ts:16`, `integration/hocuspocus-v3` via `src/extensions/database.ts:15` → `@plane/logger`; the integration file's `skipIf(!REDIS_URL)` never runs because the import fails first).

**Metric correction worked example.** The invalid first screen (`reference-v7-screen-cd2-1`, 556 errors — patch-assembly defect, never a capacity result) scores **−20.69%** on the old diluted metric (median basis) and **+4.32% worse** on the corrected load-window-per-successful-op metric. That inversion is why the metric was corrected: counting errored ops as completed and diluting with the idle collector tail rewards fast-failing runs.

**RAM against the target.** The screen's peak current is 2232 MB vs the control median 2343 MB (ratio 0.9522, **−4.8%**; the anon ratio is the same). That is nowhere near the ≥50% target and is consistent with the unreachability arithmetic above — a reader must not mistake −4.8% for progress toward 50%.

**p95 observation (not a latency claim, not a comparator verdict).** Recomputed directly from the two artifacts on a control-median basis, per-class p95 ratios are **0.7526–0.9656** (`write-issue-create` 0.9656; the earlier 0.7526–0.9265 range was wrong). This is a **single-sample observation on a capacity-FAILING candidate**, explicitly not a latency win; the comparator run itself is inconclusive (it needs ≥3 samples per side) and never evaluated p95 gates. The plausible cause is a shorter burst queue because the screen completed more work with fewer rejections.

**Gate-run scope.** `bun run check` is real, not a no-op: turbo plans 85 task nodes (56 with real commands) and completes. A bounded JS check is `bunx turbo run check --filter=live` (16 nodes, 13 real tasks). Deferred-check V4's wording stands.

**Promotion (2026-11-09, local commits only, unpushed at the time of this record).** `feat/bun-migration`: `5a4f4cc4f` fix(live): retry page stores without wedging the document (extension + narrowed service/context types + `apps/live/tests/extensions/database.test.ts`); `95b5f4058` fix(api): count distinct issues in grouped pagination (grouped paginator + `TestGroupedCountUsesDistinctIssues`); `dec9f038c` chore(bench): add the P0 harness, probes and acceptance comparator (including `benchmarks/**` lint exemption in `.oxlintrc.json`); `af7caae8c` chore: ignore local bench-results evidence; plus this docs commit. Gates at promotion: `bunx turbo run check --filter=live` 13/13 successful, `bun run test` in `apps/live` 49 passed / 3 skipped, `docker compose -f docker-compose-test.yml run --rm --build api-tests pytest -m unit` 568 passed / 923 deselected, harness tsc + contract 4/4 + editor-protocol 4/4 + comparator 67 passed. The laptop deployment reuses retained application images for `web`/`admin`/`space`/`proxy`/`live` whose app source matches HEAD (the API image was rebuilt from HEAD and carries the grouped-count change), but they are not byte-identical HEAD builds: a clean rebuild of those services currently hits the pre-existing turbo-prune lockfile defect, which remains a documented follow-up rather than something this batch rebuilt. The scratch workspaces remain the source of the measured images and the private snapshots.

**C1 verdict:** withdrawn/inconclusive as recorded in M13/M15 and not revived by this batch.

**Evidence:** `bench-results/p0-reference-v6/` and `bench-results/p0-reference-v7/` (runner, collector, provenance, logs, API/worker process inventories, loss logs), and `bench-results/m16-live-store-recovery/` — the canonical copy of the ephemeral proof set: the current-schema store-wedge proofs, the wedge control, the runner-instrumentation diff, both hash-bound parity captures, the inconclusive comparator run, the C/D2 candidate sources, and `completion-diagnosis.md` (the narrative record every section number above refers to), with `EVIDENCE.md` mapping each proof to the behaviour it demonstrates and listing superseded artifacts. All 24 copied files were sha256-verified against their `/tmp` originals (0 mismatches); the originals were left in place. `bench-results/` is now gitignored (`/bench-results/`, commit `af7caae8c`), so the evidence stays local and is not pushed.

**Verdict:** BLOCKED on capacity and on the 50% target — the reference recipe still cannot clear its zero-tolerance burst gate at 2 vCPU with the measured P3 work removed, and no performance candidate is promoted (the D2 change is promoted as a correctness fix only). The durability fix, harness instrumentation and D2 fix are promoted locally in unpushed commits (`5a4f4cc4f`, `dec9f038c`, `95b5f4058`); D2 remains a separate correctness candidate with no performance claim.

## Numerical before/after record

Units: MB/GB below are decimal approximations from PSS byte samples where raw evidence exists. PSS is shared-page-aware process-tree memory, not container RSS. All samples came from the same laptop but were not controlled interleaved production-stack experiments.

| Metric                                    | M0: pnpm/Node                                | M1: Bun hoisted                  | M2: Bun isolated                                   | Interpretation / source                                                                                                                                 |
| ----------------------------------------- | -------------------------------------------- | -------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Cold install wall                         | 10.55, 9.22, 9.09 s; median 9.22 s           | 19.1 s recorded single after-run | No separate isolated cold-install sample recovered | Network/cache-sensitive; after value from historical playbook, not a fresh wrap-up run.                                                                 |
| Warm no-change install wall               | 1.75, 1.73, 1.75 s; median 1.75 s            | 0.05 s recorded                  | No separate isolated timing recovered              | Local no-change install, not a fresh CI-install claim; after raw install log unavailable here.                                                          |
| `node_modules`, apparent bytes            | 815,976,003 B                                | About 847 MB recorded            | About 847 MB recorded                              | About +4%; baseline raw `du -sb` output saved; after values recorded in playbook.                                                                       |
| Lockfile bytes                            | 496,954 B                                    | 370,658 B                        | Same Bun lockfile in linker probe                  | About -25%; not a runtime memory measurement.                                                                                                           |
| Forced build wall                         | 49.23 and 55.04 s                            | 67.9 s recorded                  | 39.0 s recorded                                    | Isolated sample is about 21-29% below baseline range; single after-samples, not interleaved. After build raw logs not recovered.                        |
| Forced build peak tree PSS                | 4,341,354,496 and 4,292,932,608 B            | Not measured/recovered           | Not measured/recovered                             | Do not invent an after peak-memory result.                                                                                                              |
| Live boot readiness                       | 2.41 s                                       | 0.91 s                           | 0.92 s                                             | About -62%; readiness harness accepted an HTTP response, including Live's intentional root 404. It is not proof that an application workflow completed. |
| Live mean idle tree PSS, ~12 s            | 251,673,821 B                                | 206,801,457 B                    | 206,998,098 B                                      | About -18% for this process tree only; not whole-stack savings.                                                                                         |
| Host WS handshake success                 | 200/200                                      | 200/200                          | 200/200                                            | Node client on both sides; handshake probe, not document collaboration/persistence load.                                                                |
| Host WS p50 / p95 / max                   | 1 / 1 / 6 ms                                 | 0 / 1 / 7 ms                     | 0 / 1 / 8 ms                                       | One short run per configuration.                                                                                                                        |
| Host sequential WS handshakes/s           | 1,597                                        | 2,417                            | 2,153                                              | Isolated about +35% vs baseline in these samples; not sustained multi-user throughput.                                                                  |
| In-image isolated WS                      | No corresponding baseline image sample saved | Earlier image results superseded | 200/200; 2,186 handshakes/s recorded               | Playbook/session record; raw in-image JSON not recovered in this handoff.                                                                               |
| Web development boot                      | 4.98 s                                       | 3.52 s                           | 5.39 s                                             | Shipped-layout observation is roughly parity, not the old hoisted startup win.                                                                          |
| Web development mean idle tree PSS, ~12 s | 2,240,679,643 B                              | 528,613,177 B                    | 1,860,630,198 B                                    | Isolated about -17%; the earlier -76% hoisted claim is superseded and showed high run/configuration variance.                                           |

Additional raw idle CPU observations were saved in the boot JSONs: Live 2.5% / 0.5% / 0.6%, Web development 0.6% / 0.3% / 2.0% for baseline/hoisted/isolated respectively. These short idle samples do not establish CPU efficiency under completed work.

Store/cache-size accounting is not reconciled: the saved baseline store file reports `974M` via human-readable disk accounting, while the playbook contains a different apparent-byte comparison. Do not use these as equivalent instruments or headline a store-size improvement until remeasured consistently.

### Test and validation observations

| Condition                             | pnpm baseline                             | Bun hoisted                                  | Bun isolated                                                 |
| ------------------------------------- | ----------------------------------------- | -------------------------------------------- | ------------------------------------------------------------ |
| Full tests, default Turbo concurrency | Exit 1, 15/16 tasks; 77.67 s harness wall | Exit 1; 66.5 s historical record             | Exit 1, 15/16 tasks; 77.77 s tool wall                       |
| Blocks package alone                  | Pass; 44.3 s historical record            | Fail                                         | Fail; 60.37 s tool wall                                      |
| Select test file alone                | Pass                                      | 77/77 pass, about 34 s                       | 77/77 pass; 32.15 s tool wall                                |
| Check/build gates                     | Baseline recorded                         | Earlier implementation pass reported passing | Earlier isolated pass reported check passing and build 15/15 |
| Live image/start/WS                   | Baseline host runtime observed            | Earlier image observations recorded          | Isolated Live image built and WS 200/200                     |

Failure: `packages/blocks/src/select/select.test.tsx`, case `selects a row the keyboard walked past the rendered window` (baseline log points to line 110). Baseline package report: one failing test and 276 passing. Cause remains undetermined. Fake timers/rAF timing is a hypothesis class, not a proved root cause; neither the linker nor the Bun VM alone was isolated as cause.

Test failures are not an optimization success. The archived -14% test-wall figure describes only the hoisted sample; isolated full-suite wall was approximately baseline and still failed. No full application regression suite or full production-stack before/after benchmark was run in this research pass.

## Protocol for every future milestone

Before modifying runtime behavior, append an entry with its bounded question, baseline revision, actual runtime, linker, dataset/workload, concurrency/pools, resource limits, and measurement command/instrument. Reuse the saved harness where appropriate; do not create competing measurement tools by default.

After the milestone, append:

1. Changed files/revision and the feature contract retained, changed, or removed.
2. Before and after samples, units, sample count, completed-work counts/errors, and raw artifact paths.
3. Memory scope (process/tree/container/whole-stack), CPU/workload, p95, backlog, and first-use costs where relevant.
4. Observed verification and exact failures; distinguish source inspection, command execution, and real workflow evidence.
5. Keep/reject/inconclusive verdict, confounders, unresolved risks, and the next bounded action.

For a research/docs-only milestone write **before: not measured; after: not measured; no runtime change**. For historical work without an independent sample, label it **not measured independently**. Never backfill measurements from estimates or attribute the same aggregate gain to every commit.

Update this walkthrough immediately after each milestone, before moving on. Put raw result artifacts under a clearly named milestone directory within `bench-results/`; keep runtime/server logs and sensitive data out unless reviewed and explicitly needed. This logging requirement is not authorization to run tests, models, installs, services, or benchmarks.

## Resume checklist

1. Read this file, then ROADMAP.md. Preserve both and the intentionally saved benchmark evidence. Do not re-run the completed four-agent survey or repeat the Bun migration.
2. Canonical `feat/bun-migration` now carries the promoted changes (Live store recovery, grouped distinct count, P0 harness/comparator, docs) and the merge with origin. The scratch `lead-integration` workspace remains the home of the measured images and private snapshots; its harness is not the shipped application. Do not reset or force-push the canonical branch.
3. Read M15 and `bench-results/p0-reference-v3/summary.json`: three failed constrained trials exist, not a successful equal-work baseline. Preserve raw evidence and private scratch snapshots; do not re-run the failed trials to reconfirm them, pool old controls, or treat the two-child reference setting as a new C1 win. Their causes were diagnosed during M16 (harness editor reconnect; Live store wedge; burst capacity) — do not repeat the trials.
4. M16's local diagnosis and implementation are complete and the milestone is **BLOCKED** on burst capacity and on the ≥50% whole-stack RAM target; no further optimisation is scheduled. The next step is the user's own first-run setup (`http://localhost:8090`, sign-up) and browser verification of the `plane-local` app; benchmark work resumes only under a new explicit approval.
5. Reuse approvals only inside their explicit batch. M16 (2026-11-07) authorised local installs, heavy builds, tests, isolated containers, and repeated benchmarks. The user's 2026-11-09 approval additionally authorised local commits, a non-destructive merge of `origin/feat/bun-migration`, a push to `origin` only, and a personal laptop deployment. Still excluded: force-push, upstream (`makeplane/plane`) pushes/PRs, image publishing, remote hosts, host-wide restarts, and real credential rotation.
6. Record baseline and numerical acceptance gates before candidate measurements, exercise retained work, and append actual results here after every milestone. Keep security/release follow-ups in ROADMAP; discovery does not establish release readiness.
7. The 2026-11-09 approvals above cover canonical commits, the origin merge, the origin push, and the plane-local laptop deployment; they do not cover force-push, upstream pushes, image publication, remote hosts, host-wide restarts, or production credential changes. Raw benchmark evidence is gitignored and stays local; scratch jj snapshots remain experiment state.

## Traps and corrections not to repeat

- Web/Admin are already static Caddy production images. Their development-server measurements do not describe production frontend RAM.
- Ordinary Bun scripts can still launch Node through shebangs. Record the executable actually running the workload.
- PSS, RSS, cgroup memory, `du -sh`, and `du -sb` are different measurements. Shared pages and filesystem layout matter.
- No `/usr/bin/time` existed in the earlier environment; reuse the existing harness or shell timing instead of assuming it is installed.
- Keep the workload client runtime/transport fixed across any server-runtime comparison. Historical handshake probes used Node; M15's application workload uses Bun and its native WebSocket. These are separate series.
- The boot harness's readiness rule is looser than the README wording suggests; an HTTP response including 404 may count. Confirm its actual behavior before reusing readiness claims.
- The WS harness historically expected a helper at `/tmp/bench/ws-bench.mjs`; inspect/fix the invocation path in an isolated experiment rather than assuming saved scripts are portable unchanged.
- Turbo-pruned Docker installers need an explicit copy of root `bunfig.toml`; otherwise host and image install policies can differ.
- Bun's Alpine binary path is `/usr/local/bin/bun`; the bunx symlink must be available in the stage using it.
- AIO imports upstream images. It does not automatically ship fork changes, and putting programs in one container does not remove their memory.
- Celery's inspected webhook decorator does not prove retries beyond one hour: the default retry-backoff ceiling is 600 seconds. Do not invent a 12-hour visibility setting.
- `worker -B` normally embeds beat as another process, not a free thread-based scheduler. Same process count is not a memory win.
- Source-only absence of a caller/import is not proof of external non-use; default-off operator instrumentation is not automatically dead functionality.
- A historical baseline operation removed the user's global pnpm store; this was disclosed. Do not repeat it. Cold caches must be isolated disposable caches, never the user's shared store.
- A runner PASS is only as strong as its persisted-outcome assertions: verify raw outcomes against the contract before writing KEEP, and never launch a diagnostic consumer (e.g. a second celery worker) inside a measured worker round.

## Processes and cleanup

Latest runtime state (2026-11-09): the personal app runs as compose project `plane-local` on loopback `http://localhost:8090` (13 containers, `unless-stopped`, fresh named volumes, secrets in `/home/noor/.config/plane-local/plane-local.env`, mode 0600). The benchmark projects `plane-bench` and the harness test project were stopped and their containers removed; `plane-bench-reference.slice` was reverted. Durable benchmark evidence is preserved under `bench-results/` (gitignored, local-only) and the private synthetic snapshots stay in the scratch workspace. Unrelated `omarchy-windows`/`gatwy` were not touched.

The archive in `/tmp/plane-bun-wrap-evidence.tar` was created only to recover the existing git snapshot; its durable copy is in `bench-results/session-history/`. The original `/tmp/bench-results/` files may remain, but the preserved evidence no longer depends on them.
