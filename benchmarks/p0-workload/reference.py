#!/usr/bin/env python3
"""Restore one private snapshot and collect three identical baseline windows."""
import hashlib
import json
import os
import platform
from pathlib import Path
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
# A new series must never overwrite retained evidence: the directory and label
# prefix are explicit, and the host token is a random experiment identity (not
# a hostname) persisted privately so samples from the same host can be grouped.
OUT = ROOT / "bench-results" / os.environ.get("P0_OUT_DIR", "p0-reference-v4")
LABEL_PREFIX = os.environ.get("P0_LABEL_PREFIX", "reference-v4")
RUNS = int(os.environ.get("P0_RUNS", "3"))
START_INDEX = int(os.environ.get("P0_START_INDEX", "1"))
CGROUP = Path("/sys/fs/cgroup/plane.slice/plane-bench.slice/plane-bench-reference.slice")


def host_token():
    path = HERE / "seed/reference-state/host-token"
    if not path.exists():
        path.write_text(hashlib.sha256(os.urandom(32)).hexdigest()[:32] + "\n")
        path.chmod(0o600)
    return path.read_text().strip()


def command(*args, timeout=300):
    return subprocess.run(args, cwd=HERE, text=True, capture_output=True, timeout=timeout, check=True).stdout


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    snapshot = Path(os.environ.get("P0_STATE_DIR", str(HERE / "seed/reference-state")))
    # A new invocation appends to an existing series file instead of replacing
    # earlier samples' provenance.
    prior = json.loads((OUT / "provenance.json").read_text()) if (OUT / "provenance.json").exists() else {}
    provenance = {
        "schema": "p0-reference/3",
        "host_token": host_token(),
        "host_facts": {"kernel": platform.release(), "cpu_count": os.cpu_count(),
                       "collector": "benchmarks/p0/collector.py p0-cgroup-v2/2"},
        "harness_revision": command("jj", "log", "-r", "@", "--no-graph", "-T", "commit_id").strip(),
        "workload_recipe": {"steady_rate": 6, "burst_rate": 20, "burst_after_s": 60,
                            "burst_duration_s": 30, "duration_s": 300, "recovery_s": 240,
                            "max_inflight": 256, "actors": 20, "editors": 5},
        "cpu_quota": "2 CPUs shared", "memory_max": 4294967296,
        "memory_swap_max": 0, "baseline_worker_concurrency": 2,
        "snapshot_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshot.glob("*.tar.gz")},
        "image_ids": (snapshot / "images.ids").read_text().splitlines(),
        "limitations": ["Shared local host, not a VM; host OS/Docker overhead outside the 4-GiB stack budget",
                        "Core mixed workload only; expensive-feature and full capability coverage not established",
                        "No candidate in this batch; old exploratory controls are a separate series"],
        "runs": prior.get("runs", []),
        "api_patch": prior.get("api_patch", {}),
        "api_source_sha256": prior.get("api_source_sha256", {}),
    }
    for index in range(START_INDEX, RUNS + 1):
        label = f"{LABEL_PREFIX}-{index}"
        print(f"{label}: restoring identical volumes", flush=True)
        restore = command("bash", "seed/state.sh", "restore", timeout=600)
        (OUT / f"{label}-restore.txt").write_text(restore)
        # Optional candidate code for a diagnostic run. The patch is applied to
        # the recreated container only (image unchanged) and its hashes are
        # recorded, so the run cannot be mistaken for the untouched baseline.
        patch = os.environ.get("P0_API_PATCH")
        if patch:
            target = "/code/plane/utils/paginator.py"
            command("docker", "cp", patch, f"plane-bench-api:{target}")
            proven = {
                "path": patch,
                "sha256": hashlib.sha256(Path(patch).read_bytes()).hexdigest(),
                "container_file_sha256": command(
                    "docker", "exec", "plane-bench-api", "sha256sum", target).split()[0],
            }
            provenance.setdefault("api_patch", {})[label] = proven
            command("docker", "restart", "plane-bench-api", timeout=120)
            for _ in range(90):
                try:
                    subprocess.run(
                        ["curl", "-fsS", "-o", "/dev/null", "--max-time", "3",
                         "http://127.0.0.1:8080/auth/get-csrf-token/"],
                        check=True, capture_output=True)
                    break
                except subprocess.CalledProcessError:
                    time.sleep(2)
            else:
                raise RuntimeError("API readiness after candidate patch failed")
            (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        command("docker", "exec", "plane-bench-worker", "celery", "-A", "plane", "inspect", "ping", timeout=60)
        inventory = command("docker", "ps", "--filter", "label=com.docker.compose.project=plane-bench", "--format", "{{.Names}}")
        names = inventory.splitlines()
        if len(names) != 12:
            raise RuntimeError(f"expected 12 steady containers, got {len(names)}")
        controls = json.loads(command("docker", "inspect", "--format", "{{json .HostConfig.CgroupParent}}", names[0]))
        if controls != "plane-bench-reference.slice":
            raise RuntimeError("reference container outside shared parent")
        for name in names:
            if command("docker", "inspect", "--format", "{{.HostConfig.CgroupParent}}", name).strip() != controls:
                raise RuntimeError(f"{name}: inconsistent shared parent")
        limits = {field: (CGROUP / field).read_text().strip() for field in ("cpu.max", "memory.max", "memory.swap.max")}
        quota, period = map(int, limits["cpu.max"].split())
        if quota != 2 * period or limits["memory.max"] != "4294967296" or limits["memory.swap.max"] != "0":
            raise RuntimeError(f"wrong parent limits: {limits}")
        provenance.setdefault("api_source_sha256", {})[label] = command(
            "docker", "exec", "plane-bench-api", "sha256sum", "/code/plane/utils/paginator.py").split()[0]
        api_processes = command("docker", "top", "plane-bench-api", "-eo", "pid,ppid,args")
        (OUT / f"{label}-api-processes.txt").write_text(api_processes)
        api_processes_summary = [line for line in api_processes.splitlines()[1:]]
        worker_processes = command("docker", "top", "plane-bench-worker", "-eo", "pid,ppid,args")
        (OUT / f"{label}-worker-processes.txt").write_text(worker_processes)
        rows = [line.split(None, 2) for line in worker_processes.splitlines()[1:]]
        celery = [(pid, ppid) for pid, ppid, args in rows if "celery -A plane worker" in args]
        worker_ids = {pid for pid, _ in celery}
        masters = [pid for pid, ppid in celery if ppid not in worker_ids]
        if len(masters) != 1 or sum(ppid == masters[0] for _, ppid in celery) != 2:
            raise RuntimeError("normal worker process inventory is not one master plus two children")
        before_events = (CGROUP / "memory.events").read_text()
        time.sleep(60)  # same quiet warm-up for every restored stack
        collector_path = OUT / f"{label}-collector.json"
        with (OUT / f"{label}-collector.log").open("w") as log:
            collector = subprocess.Popen([
                "python3", str(HERE.parent / "p0/collector.py"), "--project", "plane-bench",
                "--access", "host", "--interval", "2", "--duration", "620", "--stack", label,
                "--out", str(collector_path),
            ], cwd=HERE, stdout=log, stderr=log)
            try:
                time.sleep(30)  # pre-load idle section, within the fixed collector window
                print(f"{label}: measuring mixed load", flush=True)
                runner = subprocess.run(["bash", "bench-run.sh", "--label", label], cwd=HERE,
                                        text=True, capture_output=True, timeout=580)
                (OUT / f"{label}-runner.log").write_text(runner.stdout + runner.stderr)
                collector_code = collector.wait(timeout=650)
            finally:
                if collector.poll() is None:
                    collector.terminate()
                    collector.wait(timeout=10)
        reports = sorted((ROOT / "bench-results/p0-workload").glob(f"{label}-*.json"))
        if len(reports) != 1:
            raise RuntimeError(f"{label}: expected unique runner report, got {len(reports)}")
        runner_data = json.loads(reports[0].read_text())
        (OUT / f"{label}-runner.json").write_text(json.dumps(runner_data, indent=2) + "\n")
        live_logs = command("docker", "logs", "plane-bench-live", timeout=180)
        loss_lines = [line for line in live_logs.splitlines() if "page_store_loss" in line]
        (OUT / f"{label}-live-loss.log").write_text("\n".join(loss_lines) + ("\n" if loss_lines else ""))
        states = [json.loads(command("docker", "inspect", "--format", "{{json .State}}", name)) for name in names]
        after_events = (CGROUP / "memory.events").read_text()
        before_oom = int(dict(line.split() for line in before_events.splitlines())["oom_kill"])
        after_oom = int(dict(line.split() for line in after_events.splitlines())["oom_kill"])
        result = {"label": label, "runner_exit": runner.returncode, "collector_exit": collector_code,
                  "harness_revision": command("jj", "log", "-r", "@", "--no-graph", "-T", "commit_id").strip(),
                  "runner_schema": runner_data.get("schema"),
                  "api_processes": api_processes_summary,
                  "page_store_loss_count": len(loss_lines),
                  "limits": limits, "memory_events_before": before_events, "memory_events_after": after_events,
                  "container_states": [{k: state[k] for k in ("Status", "OOMKilled", "ExitCode")} for state in states]}
        provenance["runs"].append(result)
        (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        print(f"{label}: runner={runner.returncode}, collector={collector_code}, gates={runner_data['gate']}", flush=True)
        if collector_code != 0 or after_oom != before_oom or any(state["Status"] != "running" or state["OOMKilled"] for state in states):
            print("Reference instability: stopping further samples for reassessment", flush=True)
            break


if __name__ == "__main__":
    main()
