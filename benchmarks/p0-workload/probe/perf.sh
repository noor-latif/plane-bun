#!/usr/bin/env bash
# Single-variable cost probe for one endpoint kind.
# Usage: bash probe/perf.sh <kind> <count> <label>
# Warm-up requests are excluded; CPU comes from the container cgroup counters,
# row/transaction counts from pg_stat_database, wall p50 from the probe report.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIND="${1:?kind}"; COUNT="${2:?count}"; LABEL="${3:?label}"
usage() { docker exec "plane-bench-$1" grep -m1 '^usage_usec' /sys/fs/cgroup/cpu.stat | awk '{print $2}'; }
dbstat() { docker exec plane-bench-db psql -U bench_plane -d bench_plane -tA -c \
  "select xact_commit,tup_returned,tup_fetched,blks_hit from pg_stat_database where datname='bench_plane'"; }

cd "$HERE/.."
for round in 1 2 3; do
  bun probe/probe.ts --mode read-seq --count 40 --kind "$KIND" --users 1 --label "$LABEL-warm$round" >/dev/null 2>&1
done
A0=$(usage api); D0=$(usage db); S0=$(dbstat); T0=$(date +%s%N)
bun probe/probe.ts --mode read-seq --count "$COUNT" --kind "$KIND" --users 1 --label "$LABEL" > "/tmp/plane-probe/perf-$LABEL.log" 2>&1
T1=$(date +%s%N); A1=$(usage api); D1=$(usage db); S1=$(dbstat)
python3 - "$LABEL" "$KIND" "$COUNT" "$A0" "$A1" "$D0" "$D1" "$T0" "$T1" "$S0" "$S1" <<'PY'
import json, sys, glob
label, kind, count, a0, a1, d0, d1, t0, t1, s0, s1 = sys.argv[1:]
report = json.load(open(sorted(glob.glob(f"/tmp/plane-probe/{label}-*.json"))[-1]))
lat = report["by_kind"].get(kind) or report["latencies"]
s0 = [int(x) for x in s0.strip().split("|")]
s1 = [int(x) for x in s1.strip().split("|")]
delta = [b - a for a, b in zip(s0, s1)]
print(json.dumps({
    "label": label, "kind": kind, "n": int(count),
    "wall_s": round((int(t1) - int(t0)) / 1e9, 3),
    "p50_ms": lat["p50"], "p95_ms": lat["p95"], "errors": lat.get("errors", 0),
    "cpu_api_ms_per_req": round((int(a1) - int(a0)) / 1e3 / int(count), 1),
    "cpu_db_ms_per_req": round((int(d1) - int(d0)) / 1e3 / int(count), 1),
    "pg_xact_per_req": round(delta[0] / int(count), 2),
    "pg_tup_returned_per_req": round(delta[1] / int(count), 1),
    "pg_tup_fetched_per_req": round(delta[2] / int(count), 1),
    "pg_blks_hit_per_req": round(delta[3] / int(count), 1),
}, indent=1))
PY