#!/usr/bin/env python3
"""P0 cgroup v2 per-container memory/CPU sampler + round aggregation.

Samples cgroup v2 `memory.current` (bytes) and `cpu.stat` `usage_usec` for
each running Docker container on a fixed cadence, then aggregates
simultaneous per-round sums and the observed peak of those sums.

Access modes:
  host  default. One `docker inspect` at startup maps each container to its
        host cgroup v2 directory via the container init PID
        (/proc/<pid>/cgroup, line `0::<path>`). Each round then reads
        /sys/fs/cgroup/... directly: zero docker calls while sampling, so
        the instrument does not perturb the measured stack.
  exec  fallback when the host cgroup tree is not readable (e.g. rootless
        docker): one `docker exec` per container per round. Slower and
        slightly contaminating (the exec process runs inside the measured
        container); recorded in the output, so numbers are only comparable
        within one access mode.

This is the container-accounted series (memory.current includes page
cache). Historical PSS (playbook branch benchmarks/*.py) is a different
instrument in a separate series; never merge the two.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

SCHEMA = "p0-cgroup-v2/2"
BASELINE_REVISION = "075fe58895c3336eb6dda6855a636c75cfe69225"
IN_CONTAINER_CGROUP = "/sys/fs/cgroup"

INSPECT_FMT = (
    "{{range .}}{{.ID}}\t{{.Name}}\t{{.State.Pid}}\t"
    '{{index .Config.Labels "com.docker.compose.service"}}\t'
    '{{index .Config.Labels "com.docker.compose.project"}}\t'
    "{{.Image}}\t{{.ImageID}}\n{{end}}"
)

# Same fields without the range wrapper (docker 29 array-range incompatibility;
# ImageID does not exist on container inspect — Config.Image is the image NAME).
INSPECT_FMT_SINGLE = (
    "{{.ID}}\t{{.Name}}\t{{.State.Pid}}\t"
    '{{index .Config.Labels "com.docker.compose.service"}}\t'
    '{{index .Config.Labels "com.docker.compose.project"}}\t'
    "{{.Image}}\t{{.Config.Image}}"
)

# One exec round-trip returns both instruments plus the memory.stat
# breakdown and swap; optional fields fail soft (skipped if the
# container lacks them). Used only in exec mode.
EXEC_SH = ("cat /sys/fs/cgroup/memory.current; "
           "grep -m1 '^usage_usec' /sys/fs/cgroup/cpu.stat; "
           "grep -E '^(anon|file|sock) ' /sys/fs/cgroup/memory.stat; "
           "cat /sys/fs/cgroup/memory.swap.current 2>/dev/null || true")


def now_iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


def bun_version():
    r = subprocess.run(["bun", "--version"], capture_output=True, text=True, check=False)
    return r.stdout.strip() or None if r.returncode == 0 else None


def docker_version():
    r = docker("version", "-f", "{{.Client.Version}} server={{.Server.Version}}")
    return r.stdout.strip() or None if r.returncode == 0 else None


def cgroup2_mount():
    """Mount point of the cgroup2 hierarchy, or None."""
    try:
        with open("/proc/self/mountinfo") as f:
            for line in f:
                pre, _, post = line.partition(" - ")
                fs = post.split()
                if fs and fs[0] == "cgroup2":
                    return pre.split()[4]
    except OSError:
        pass
    return None


def discover(only_names, project):
    """One-shot docker ps + inspect for identity; no per-round docker calls."""
    r = docker("ps", "-q")
    if r.returncode != 0:
        raise RuntimeError(f"docker ps failed: {r.stderr.strip()}")
    ids = r.stdout.split()
    if not ids:
        return []
    # docker 29 rejects `{{range .}}` over a multi-id inspect array (element
    # type degrades to interface{}); inspect each id individually instead.
    lines: list[str] = []
    for cid in ids:
        r = docker("inspect", "--format", INSPECT_FMT_SINGLE, cid)
        if r.returncode != 0:
            raise RuntimeError(f"docker inspect failed: {r.stderr.strip()}")
        lines.append(r.stdout.strip("\n"))
    out = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        cid, name, pid, service, proj, image, image_id = parts
        name = name.lstrip("/")
        if only_names and name not in only_names:
            continue
        if project and proj != project:
            continue
        out.append({
            "id": cid,
            "name": name,
            "service": service or None,
            "project": proj or None,
            "init_pid": int(pid) if pid.isdigit() else None,
            "image": image or None,
            "image_id": image_id or None,
        })
    return out


def host_cgroup_path(mount, init_pid):
    """Host cgroup v2 directory of a container via its init PID."""
    if init_pid is None:
        raise RuntimeError("container has no running PID")
    with open(f"/proc/{init_pid}/cgroup") as f:
        for line in f:
            # v2 line is "0::<path>" — empty controller field, so split
            # on at most 2 colons; partition() would leave one behind
            fields = line.strip().split(":", 2)
            if fields[0] == "0":
                return mount + fields[2]
    raise RuntimeError(f"no cgroup v2 line for pid {init_pid}")


def read_host(cg):
    # membership re-checked per round: a dead container whose cgroup dir
    # persists must surface as missing, not stale-but-valid samples
    with open(os.path.join(cg["cgroup_path"], "cgroup.procs")) as f:
        if not f.read().split():
            raise OSError("cgroup has no member processes")
    with open(os.path.join(cg["cgroup_path"], "memory.current")) as f:
        mem = int(f.read().strip())
    stat = {}
    with open(os.path.join(cg["cgroup_path"], "memory.stat")) as f:
        for line in f:
            key, _, val = line.partition(" ")
            if key in ("anon", "file", "sock"):
                stat[key] = int(val)
    usage = None
    try:
        with open(os.path.join(cg["cgroup_path"], "cpu.stat")) as f:
            for line in f:
                if line.startswith("usage_usec"):
                    usage = int(line.split()[1])
                    break
    except OSError:
        pass
    swap = None
    try:
        with open(os.path.join(cg["cgroup_path"], "memory.swap.current")) as f:
            swap = int(f.read().strip())
    except OSError:
        pass  # swap accounting not enabled for this cgroup
    return mem, usage, stat, swap


def read_exec(cg):
    r = docker("exec", cg["id"], "sh", "-c", EXEC_SH)
    if r.returncode != 0:
        raise OSError(f"docker exec failed: {r.stderr.strip()}")
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    if not lines:
        raise OSError("docker exec returned no data")
    usage = int(lines[1].split()[1]) if len(lines) > 1 else None
    swap = int(lines[-1]) if lines[-1].isdigit() else None
    return int(lines[0]), usage, parse_exec_stat(lines[2:]), swap


def parse_exec_stat(lines):
    """Parse optional trailing 'KEY VALUE' lines (anon/file/sock) from exec output."""
    stat = {}
    for line in lines:
        key, _, val = line.strip().partition(" ")
        if key in ("anon", "file", "sock") and val.strip().isdigit():
            stat[key] = int(val)
    return stat


def resolve_access(containers, mount, mode):
    """Decide host vs exec for the whole run; probe every container once."""
    if mode == "exec":
        for cg in containers:
            cg["cgroup_path"] = IN_CONTAINER_CGROUP
        return "exec"
    if mount is None:
        if mode == "host":
            raise RuntimeError("no cgroup v2 mount found on host")
        for cg in containers:
            cg["cgroup_path"] = IN_CONTAINER_CGROUP
        return "exec"
    for cg in containers:
        try:
            cg["cgroup_path"] = host_cgroup_path(mount, cg["init_pid"])
            with open(os.path.join(cg["cgroup_path"], "cgroup.procs")) as f:
                pids = f.read().split()
            if not pids:
                raise RuntimeError("cgroup has no member processes")
            read_host(cg)  # probe both instruments
        except (OSError, RuntimeError, ValueError) as e:
            if mode == "host":
                raise RuntimeError(f"host cgroup access failed for {cg['name']}: {e}")
            for c in containers:
                c["cgroup_path"] = IN_CONTAINER_CGROUP
            return "exec"
    return "host"


def collect(interval, duration, containers, access_mode, interrupted_flag):
    """Sample rounds on a drift-free fixed cadence. Raw rounds, no aggregation."""
    reader = read_host if access_mode == "host" else read_exec
    rounds = []
    t0 = time.time()
    i = 0
    try:
        while time.time() - t0 < duration:
            target = t0 + i * interval
            if time.time() < target:
                time.sleep(target - time.time())
            t = time.time()
            samples, missing = {}, []
            for cg in containers:
                try:
                    mem, usage, stat, swap = reader(cg)
                    samples[cg["name"]] = {
                        "memory_current_bytes": mem,
                        "cpu_usage_usec": usage,
                        "anon_bytes": stat.get("anon"),
                        "file_bytes": stat.get("file"),
                        "sock_bytes": stat.get("sock"),
                        "swap_current_bytes": swap,
                    }
                except (OSError, ValueError) as e:
                    missing.append(cg["name"])
                    print(f"warn: sample {cg['name']}: {e}", file=sys.stderr)
            rounds.append({"i": i, "t": t, "iso": now_iso(t),
                           "samples": samples, "missing": missing})
            i += 1
    except KeyboardInterrupt:
        interrupted_flag.append(True)
    return rounds


def aggregate(rounds, names):
    """Pure aggregation over raw rounds; the part the self-check exercises.

    A round with any missing sample is invalid and gets round_sum_bytes=None:
    a partial sum is never reported and a missing sample is never zero.
    observed_peak_bytes is the max of valid simultaneous round sums — never
    the sum of per-container independent peaks.
    """
    for r in rounds:
        r["valid"] = not r.get("missing")
        r["round_sum_bytes"] = (
            sum(s["memory_current_bytes"] for s in r["samples"].values())
            if r["valid"] else None
        )
        # informational sums, same validity rules; the contract metric
        # stays round_sum_bytes (memory.current). A round where no
        # sample carries the field sums to null, never 0.
        for key, field in (("anon_sum_bytes", "anon_bytes"),
                           ("file_sum_bytes", "file_bytes")):
            vals = [s[field] for s in r["samples"].values()
                    if s[field] is not None] if r["valid"] else []
            r[key] = sum(vals) if vals else None
    prev = {}
    for r in rounds:
        for n in names:
            s = r["samples"].get(n)
            delta = None
            if s is not None and s["cpu_usage_usec"] is not None:
                p = prev.get(n)
                if p is not None:
                    delta = max(s["cpu_usage_usec"] - p, 0)
                prev[n] = s["cpu_usage_usec"]
            if s is not None:
                s["cpu_delta_usec"] = delta
    valid_sums = [r["round_sum_bytes"] for r in rounds if r["valid"]]
    peak = max(valid_sums) if valid_sums else None
    peak_i = next((r["i"] for r in rounds
                   if r["valid"] and r["round_sum_bytes"] == peak), None)
    peak_anon = max((r["anon_sum_bytes"] for r in rounds if r["valid"]
                     and r["anon_sum_bytes"] is not None), default=None)
    peak_file = max((r["file_sum_bytes"] for r in rounds if r["valid"]
                     and r["file_sum_bytes"] is not None), default=None)
    per = {}
    for n in names:
        p_peak, a_peak, f_peak, cpu_total, miss = None, None, None, 0, 0
        for r in rounds:
            s = r["samples"].get(n)
            if s is None:
                miss += 1
                continue
            if p_peak is None or s["memory_current_bytes"] > p_peak:
                p_peak = s["memory_current_bytes"]
            if s["anon_bytes"] is not None and (a_peak is None or s["anon_bytes"] > a_peak):
                a_peak = s["anon_bytes"]
            if s["file_bytes"] is not None and (f_peak is None or s["file_bytes"] > f_peak):
                f_peak = s["file_bytes"]
            if s["cpu_delta_usec"]:
                cpu_total += s["cpu_delta_usec"]
        per[n] = {"peak_memory_current_bytes": p_peak,
                  "peak_anon_bytes": a_peak,
                  "peak_file_bytes": f_peak,
                  "cpu_total_usec": cpu_total,
                  "missing_samples": miss}
    invalid = [r["i"] for r in rounds if not r["valid"]]
    reasons = []
    if not rounds:
        reasons.append("no rounds collected")
    if invalid:
        reasons.append(f"missing samples in rounds {invalid}")
    return {
        "rounds_total": len(rounds),
        "rounds_valid": len(rounds) - len(invalid),
        "observed_peak_bytes": peak,
        "peak_round_i": peak_i,
        "peak_anon_bytes": peak_anon,
        "peak_file_bytes": peak_file,
        "run_valid": bool(rounds) and not invalid,
        "invalid_reasons": reasons,
        "per_container": per,
    }


def self_check():
    """Deterministic checks of aggregate() over fixture rounds. No docker."""
    failures = []

    def check(name, cond):
        print(f"{'PASS' if cond else 'FAIL'}: {name}")
        if not cond:
            failures.append(name)

    def rnd(i, samples, missing=()):
        return {"i": i, "t": float(i), "iso": "",
                "samples": {k: dict(v) for k, v in samples.items()},
                "missing": list(missing)}

    mem = lambda b, u=None: {"memory_current_bytes": b, "cpu_usage_usec": u,
                             "anon_bytes": None, "file_bytes": None,
                             "sock_bytes": None, "swap_current_bytes": None}

    # Case 1: sums and simultaneous peak vs sum-of-independent-peaks.
    rounds = [
        rnd(0, {"a": mem(100, 1000), "b": mem(200, 2000)}),
        rnd(1, {"a": mem(900, 3000), "b": mem(100, 2500)}),
        rnd(2, {"a": mem(100, 3000), "b": mem(900, 6000)}),
    ]
    s = aggregate(rounds, ["a", "b"])
    check("round sums", [r["round_sum_bytes"] for r in rounds] == [300, 1000, 1000])
    check("peak is max round sum (1000), not sum of per-container peaks (1800)",
          s["observed_peak_bytes"] == 1000 and s["peak_round_i"] == 1)
    check("run valid", s["run_valid"] and s["rounds_valid"] == 3)
    check("cpu deltas", rounds[1]["samples"]["a"]["cpu_delta_usec"] == 2000
          and rounds[0]["samples"]["a"]["cpu_delta_usec"] is None
          and s["per_container"]["b"]["cpu_total_usec"] == 4000)

    # Case 2: missing sample invalidates the round; never coerced to zero.
    rounds = [
        rnd(0, {"a": mem(100, 1000), "b": mem(200, 2000)}),
        rnd(1, {"a": mem(900, 3000)}, missing=["b"]),
        rnd(2, {"a": mem(100, 4000), "b": mem(500, 6000)}),
    ]
    s = aggregate(rounds, ["a", "b"])
    check("missing round invalid, sum None (not partial, not 0)",
          rounds[1]["valid"] is False and rounds[1]["round_sum_bytes"] is None)
    check("b not zero-filled in invalid round", "b" not in rounds[1]["samples"])
    check("run invalidated by missing sample", not s["run_valid"]
          and s["invalid_reasons"] == ["missing samples in rounds [1]"])
    check("peak uses valid rounds only (600)",
          s["observed_peak_bytes"] == 600 and s["rounds_valid"] == 2)

    # Case 3: cpu counter gap (None between samples) is not a zero delta.
    rounds = [
        rnd(0, {"a": mem(100, 1000)}),
        rnd(1, {"a": mem(100, None)}),
        rnd(2, {"a": mem(100, 1500)}),
    ]
    s = aggregate(rounds, ["a"])
    check("gap produces no delta, next delta spans the gap",
          rounds[1]["samples"]["a"]["cpu_delta_usec"] is None
          and rounds[2]["samples"]["a"]["cpu_delta_usec"] == 500
          and s["per_container"]["a"]["cpu_total_usec"] == 500)

    # Case 4: no rounds at all.
    s = aggregate([], ["a"])
    check("empty run invalid", not s["run_valid"]
          and s["observed_peak_bytes"] is None
          and s["invalid_reasons"] == ["no rounds collected"])

    # Case 5 (v2): anon/file per-round sums, peaks, per-container peaks.
    rounds = [
        rnd(0, {"a": {**mem(1000, 100), "anon_bytes": 300, "file_bytes": 500},
                "b": {**mem(2000, 200), "anon_bytes": 700, "file_bytes": 900}}),
        rnd(1, {"a": {**mem(1500, 300), "anon_bytes": 800, "file_bytes": 400},
                "b": {**mem(2500, 400), "anon_bytes": 600, "file_bytes": 1200}}),
    ]
    s = aggregate(rounds, ["a", "b"])
    check("anon/file round sums", rounds[0]["anon_sum_bytes"] == 1000
          and rounds[0]["file_sum_bytes"] == 1400
          and rounds[1]["anon_sum_bytes"] == 1400
          and rounds[1]["file_sum_bytes"] == 1600)
    check("anon/file peaks are max valid-round sums",
          s["peak_anon_bytes"] == 1400 and s["peak_file_bytes"] == 1600)
    check("contract sum unchanged (memory.current)",
          s["observed_peak_bytes"] == 4000 and rounds[0]["round_sum_bytes"] == 3000)
    check("per-container anon/file peaks",
          s["per_container"]["a"]["peak_anon_bytes"] == 800
          and s["per_container"]["b"]["peak_file_bytes"] == 1200)

    # Case 6 (v2): missing round nulls the informational sums too.
    rounds = [
        rnd(0, {"a": {**mem(1000, 100), "anon_bytes": 400}}),
        rnd(1, {}, missing=["a"]),
    ]
    s = aggregate(rounds, ["a"])
    check("invalid round: informational sums null, not partial",
          rounds[1]["round_sum_bytes"] is None
          and rounds[1]["anon_sum_bytes"] is None
          and rounds[1]["file_sum_bytes"] is None)
    check("peaks use valid rounds only",
          s["peak_anon_bytes"] == 400 and s["peak_file_bytes"] is None)

    # Case 7 (v2): swap null when absent, present when readable — pure
    # reader-level check, no aggregation semantics involved.
    tmp = tempfile.mkdtemp()
    try:
        def wf(name, content):
            with open(os.path.join(tmp, name), "w") as f:
                f.write(content)
        wf("cgroup.procs", "123\n")
        wf("memory.current", "8192\n")
        wf("memory.stat", "anon 4096\nfile 2048\nsock 128\nother 99\n")
        wf("cpu.stat", "usage_usec 1000\n")
        mem, usage, stat, swap = read_host({"cgroup_path": tmp})
        check("host reader: stat fields parsed",
              (mem, usage, stat) == (8192, 1000,
                                     {"anon": 4096, "file": 2048, "sock": 128}))
        check("swap null when memory.swap.current absent", swap is None)
        wf("memory.swap.current", "512\n")
        _, _, _, swap = read_host({"cgroup_path": tmp})
        check("swap read when present", swap == 512)
    finally:
        shutil.rmtree(tmp)

    print(f"self-check: {len(failures)} failure(s)")
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--interval", type=float, default=1.0,
                    help="sampling cadence seconds (default 1.0)")
    ap.add_argument("--duration", type=float, default=60.0,
                    help="total sampling seconds (default 60)")
    ap.add_argument("--project", default=None,
                    help="docker compose project label filter")
    ap.add_argument("--container", action="append", default=None,
                    help="container name filter; repeatable")
    ap.add_argument("--stack", default=None,
                    help="stack label recorded in run.stack (e.g. baseline|candidate)")
    ap.add_argument("--access", choices=("auto", "host", "exec"), default="auto",
                    help="host=/sys/fs/cgroup reads; exec=docker exec fallback")
    ap.add_argument("--out", default=None, help="write JSON here (default stdout)")
    ap.add_argument("--self-check", action="store_true",
                    help="run deterministic aggregation checks and exit")
    cfg = ap.parse_args()

    if cfg.self_check:
        sys.exit(self_check())
    if cfg.interval <= 0:
        ap.error("--interval must be > 0")

    try:
        containers = discover(set(cfg.container or []), cfg.project)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    if not containers:
        print("error: no matching running containers", file=sys.stderr)
        sys.exit(2)
    names = [c["name"] for c in containers]

    mount = cgroup2_mount()
    try:
        access_mode = resolve_access(containers, mount, cfg.access)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    print(f"collector: mode={access_mode} interval={cfg.interval}s "
          f"duration={cfg.duration}s containers={names}", file=sys.stderr)

    interrupted = []
    rounds = collect(cfg.interval, cfg.duration, containers, access_mode, interrupted)
    summary = aggregate(rounds, names)
    summary["interrupted"] = bool(interrupted)
    if interrupted:
        summary["invalid_reasons"].append("interrupted before duration")

    run = {
        "schema": SCHEMA,
        "collector": {"name": "benchmarks/p0/collector.py", "version": 2},
        "instrument": {"metric": "memory.current", "unit": "bytes",
                       "cpu_metric": "cpu.stat usage_usec", "cpu_unit": "microseconds",
                       "source": "cgroup v2",
                       "breakdown": "memory.stat anon/file/sock",
                       "swap": "memory.swap.current (null when absent)"},
        "provenance": {
            "baseline_revision": BASELINE_REVISION,
            "stack": cfg.stack,
            "collector_bun_version": bun_version(),
            "docker_version": docker_version(),
        },
        "access_mode": access_mode,
        "host": {"cgroup2_mount": mount},
        "interval_s": cfg.interval,
        "duration_s": cfg.duration,
        "started_at": now_iso(rounds[0]["t"]) if rounds else now_iso(time.time()),
        "ended_at": now_iso(time.time()),
        "containers": [{k: c.get(k) for k in
                        ("id", "name", "service", "project", "image",
                         "image_id", "cgroup_path")}
                       for c in containers],
        "rounds": rounds,
        "summary": summary,
    }
    text = json.dumps(run, indent=2)
    if cfg.out:
        with open(cfg.out, "w") as f:
            f.write(text + "\n")
        print(f"wrote {cfg.out}", file=sys.stderr)
    else:
        print(text)
    sys.exit(0 if summary["run_valid"] else 3)


if __name__ == "__main__":
    main()
