# P0 constrained reference workload

This scratch-only harness measures the fork at application baseline
`075fe58895c3336eb6dda6855a636c75cfe69225`. It changes no application behavior.
Canonical intent and verdicts belong to `ROADMAP.md` and `WALKTHROUGH.md` in
`/home/noor/dev/apps/plane-bun`, not copied agent reports.

## Current contract

- Reference budget: **2 vCPU / 4 GiB total**, swap disabled, shared by the entire
  stack under the temporary `plane-bench-reference.slice`. This is a container
  budget on the local host, not a VM or proof that a physical 4-GiB machine fits
  the operating system, Docker, and application together.
- 20 authenticated synthetic users; five persistent editor connections share
  one page so changes cross client connections. Other users exercise API work.
- Default arrival recipe: 6 operations/s for 300s, with 20/s during seconds
  60–90: exactly 2,220 slots. The 20-slot cycle has 10 reads (three boards,
  five issue lists, two page lists), two creates, four patches, three page
  edits (one reconnect), and one file round trip.
- Every slot is dispatched or explicitly rejected at the 256-in-flight bound.
  Any rejection, missing completion, operation error, or dispatch lag over
  100ms fails the run. Latencies include delay from scheduled arrival, including
  per-issue/editor serialization. No transient-error tolerance.
- Read back every acknowledged issue's exact final name and **every patch's**
  unique `IssueActivity.new_value` through issue history. This proves the
  selected real API → RabbitMQ → Celery activity path, not every background task.
- Decode persisted Yjs binaries and require every acknowledged edit marker;
  rebuild each of the five editor connections and check the same markers.
  Non-empty content and binary growth are not persistence evidence.
- Uploaded 32-KiB payloads must download byte-exact. No browser UI verification
  is implied; browser checks remain user-driven.
- Sample whole-vhost queue depth every two seconds through load and recovery.
  Missing observations fail. Recovery requires persisted outcomes and multiple
  fresh zero-depth samples; queue emptiness alone is not job-completion proof.
- Recovery allowance: 240s. Report cold idle, load, and post-load separately.
  A failed reference load is a capacity result, not permission to reduce it.
- Current batch stops after harness repair and the reference baseline. Candidate
  work and paid model calls are outside its authorization. Later comparisons
  require ≥3 samples per condition with alternating order, ≥50% whole-stack RAM
  reduction as the overall target, and ≤5% CPU/p95 regression. Expensive retained
  features need separate first-use/post-use measurements before promotion.

## Isolation and image provenance

Project `plane-bench` owns 13 defined services (12 steady-state containers and
one exited migrator). All names are `plane-bench-*`; volumes are exclusively
`plane-bench_bench_{pgdata,redisdata,rabbitmq,uploads}`. Published ports are
loopback-only 8080/8443 (proxy) and 15672 (RabbitMQ management). Stop if occupied;
never restart Docker or stop another stack to recover a port.

`seed/setup.sh` refuses canonical-checkout env generation. Synthetic root/API
`.env` files and `seed/user.json` are private, ignored, and generated locally.
Never copy canonical credentials or include snapshots/credentials in evidence.

Setup reuses the retained locally built fork images, with no build or pull.
`seed/state.sh` records image IDs and refuses restore if tags resolve differently.
Application images include the historical bench-only baked prune-lockfile hooks
for Web/Admin/Space/Live (`deploy/pruned-lockfiles/*.bun.lock`), a deviation from
a pure baseline build. Root `bun.lock` and application code remain unchanged.
The images were built with Bun 1.4.4; record actual IDs per collection.

The control pool is explicitly **two Celery prefork children**, matching the
intended two-vCPU deployment rather than the host's eight-CPU discovery. Quota
alone does not change Python's CPU count. This is reference sizing, not a new C1
win; old eight-child exploratory controls are not comparable.
Record normal worker processes before collection; never start a second worker
inside its measured container to inspect a banner.

## Commands

Run from `benchmarks/p0-workload/`, after explicit local execution approval:

```sh
bash seed/setup.sh                 # new synthetic seed, retained images only
bash seed/state.sh save            # stop cleanly; archive all four volumes once
bash seed/state.sh restore         # validate hashes/images; recreate exact state
bash bench-run.sh --label reference-smoke --duration 20 --burst-after 5 --burst-duration 5 --recovery 60
python3 reference.py               # three restored baseline samples; no candidates
```

Seed shape is 400 issues, 12 pages, 20 users. Content/IDs are random on first
creation; **reproducibility comes from restoring the same stopped-volume
snapshot**, not reseeding Faker. Private `seed/reference-state/` holds archives,
image IDs, hashes, and credentials; it is ignored. Restores remove only this
project's disposable containers/volumes. Keep the snapshot for subsequent runs.

The runner writes `bench-results/p0-workload/<label>-<timestamp>.json`, schema
`p0-workload/3`, including operation records and timestamped queue observations.
Exit 0 means its outcome gates passed; exit 2 means failure with a report.
Configuration/seed/import errors before the run can exit 1 without a report.
A runner PASS is not a CPU, memory, latency-regression, or feature-coverage verdict.

Use the existing `../p0/collector.py` with host cgroup access, two-second cadence,
and fixed collection windows. Its instrument is `memory.current` including page
cache, not RSS/PSS. Whole-stack peak is the maximum simultaneous round sum, not
the sum of independent service peaks. Keep old exploratory results separate.

```sh
bun test contract.test.ts
bunx --no-install tsc -p tsconfig.json
python3 ../p0/collector.py --self-check
bash seed/reset.sh                 # project-scoped teardown, removes user.json
bash seed/budget.sh stop            # after teardown, remove experiment-only limits
```

No global pruning, host-daemon restart, remote work, canonical source promotion,
commit, push, or deployment is part of these commands.
