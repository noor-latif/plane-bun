# P0 container measurement — collector

cgroup v2 per-container sampler for the P0 measurement contract
(ROADMAP §"First: a small measurement contract"). Container-accounted
`memory.current` (bytes, includes page cache) plus `cpu.stat usage_usec`
deltas, with container/service identity, membership, and UTC timestamps
on a fixed cadence. This is **not** PSS: the historical playbook PSS
harness (`benchmarks/*.py` on the playbook branch) remains a separate
series and is not merged with these numbers.

## Invocation

```sh
# deterministic self-check (aggregation + missing-sample cases; no docker)
python3 benchmarks/p0/collector.py --self-check

# sample all running containers for 60s at 1s cadence, write JSON
python3 benchmarks/p0/collector.py --duration 60 --interval 1.0 --out run.json

# one compose project only, labeled for provenance
python3 benchmarks/p0/collector.py --project <project> --stack baseline --out run.json

# force an access mode (default auto)
python3 benchmarks/p0/collector.py --access host|exec --out run.json
```

Exit codes: `0` complete and every round valid; `2` setup error (docker
unreachable, no matching containers, cgroup access refused in `host`
mode); `3` sampling finished but the run is invalid (missing samples or
interrupted); `1` self-check failure. Warnings go to stderr, JSON to
`--out` (default stdout).

## Access modes

- `host` (default via `auto`): one `docker ps` + one `docker inspect` at
  startup resolves each container's init PID → `/proc/<pid>/cgroup` →
  host cgroup v2 directory; membership is verified via `cgroup.procs`.
  Rounds then read `/sys/fs/cgroup` directly — no docker calls while
  sampling, so the instrument does not contaminate the measured stack.
- `exec`: fallback when host cgroup paths are unreadable or empty
  (rootless docker). One `docker exec` per container per round; that
  process runs inside the measured container, so this mode is
  slightly contaminating. The mode is recorded in the output; compare
  numbers only within the same access mode.

`--access host` fails loudly instead of silently falling back; `auto`
falls back to `exec` only when host access fails.

The container set is fixed at startup: containers started or restarted
under a new ID afterwards are not sampled. A container that dies
mid-run produces missing samples (host mode re-checks `cgroup.procs`
every round, so a persisted but dead cgroup does not yield
stale-but-valid readings), which invalidate the run. Always
sample an explicitly bounded set — pass `--project <name>` (or
repeatable `--container <name>`); with no filter the collector samples
every running container on the host, which is wrong for a P0 run.

## Output schema (`p0-cgroup-v2/2`)

```
schema                 "p0-cgroup-v2/2"
collector              {name, version: 2}
instrument             {metric: memory.current [bytes], cpu_metric: cpu.stat
                        usage_usec [microseconds], source: cgroup v2,
                        breakdown: memory.stat anon/file/sock,
                        swap: memory.swap.current (null when absent)}
provenance             {baseline_revision (075fe5889 full SHA),
                        stack (--stack, null unless set),
                        collector_bun_version (bun --version at startup,
                        null if bun absent),
                        docker_version ("client=X server=Y", null if
                        docker query fails)}
access_mode            host | exec
host.cgroup2_mount     host cgroup2 mount point (null in exec mode)
interval_s, duration_s cadence and requested window
started_at, ended_at   UTC ISO-8601 of first round / write time
containers[]           {id, name, service, project, image, image_id,
                       cgroup_path} — service/project from compose
                       labels; image = repo:tag (.Image), image_id =
                       config digest (.ImageID), both from inspect
rounds[]               {i, t (epoch s), iso (UTC), samples{}, missing[],
                       round_sum_bytes, anon_sum_bytes, file_sum_bytes}
rounds[].samples.<name> {memory_current_bytes, cpu_usage_usec,
                         cpu_delta_usec, anon_bytes, file_bytes,
                         sock_bytes, swap_current_bytes}
                       anon/file/sock are the matching memory.stat
                       fields in bytes; swap_current_bytes is null when
                       the cgroup has no swap accounting. cpu_delta_usec
                       is null for the first round and across any
                       usage_usec gap; deltas are never negative.
rounds[].round_sum_bytes     sum of memory.current over the round; null
                       when the round is invalid
rounds[].anon_sum_bytes /    informational sums over valid rounds
rounds[].file_sum_bytes      (same validity rules); null when the round
                       is invalid OR no sample carries the field
summary.observed_peak_bytes  max over simultaneous valid round sums —
                       never the sum of per-container independent peaks
summary.peak_anon_bytes /    max of anon_sum/file_sum over valid rounds;
summary.peak_file_bytes      null when no valid round carries the field
summary.per_container  {peak_memory_current_bytes, peak_anon_bytes,
                        peak_file_bytes, cpu_total_usec,
                        missing_samples} per container (informational)
summary.run_valid      false if any round had a missing sample or the
                       run was interrupted
summary.invalid_reasons  machine-readable reasons when invalid
```

Round validity is unchanged from v1: solely missing-sample based. The
contract metric remains `round_sum_bytes` (memory.current); the anon/file
sums and peaks are informational decompositions under the same rules.

## Missing-sample rule

A round where any expected container could not be sampled is invalid: it
gets `round_sum_bytes: null` — never a partial sum, never zero. Any
invalid round makes `summary.run_valid: false` and the process exits 3;
the comparison is invalidated per the P0 contract. A `usage_usec` gap
yields no delta (null), and the next valid delta spans the gap.
