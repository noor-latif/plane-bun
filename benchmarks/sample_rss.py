#!/usr/bin/env python3
"""Sample a live process tree's RSS/CPU for N seconds; emit JSON summary.

usage: sample_rss.py <root_pid> <seconds>
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from benchrun import proc_snapshot, tree_stats


def sample_tree(root, seconds, proc):
    """Sample a running process tree for N seconds; return (idle_stats, None).

    Aborts early (returning partial data) if the watched process exits.
    """
    t0 = time.monotonic()
    samples = []
    first_cpu = None
    while time.monotonic() - t0 < seconds:
        if proc is not None and proc.poll() is not None:
            break
        rss, cpu = tree_stats(root, proc_snapshot())
        if first_cpu is None:
            first_cpu = cpu
        samples.append((rss, cpu))
        time.sleep(0.25)
    elapsed = max(time.monotonic() - t0, 0.25)
    if not samples:
        return {"error": "no samples; pid gone"}, None
    rss_values = sorted(r for r, _ in samples)
    cpu = samples[-1][1] - (samples[0][1])
    return {
        "seconds": round(elapsed, 2),
        "avg_tree_pss_bytes": sum(rss_values) // len(rss_values),
        "p95_tree_pss_bytes": rss_values[max(0, int(len(rss_values) * 0.95) - 1)],
        "cpu_percent": round(cpu * 100 / elapsed, 1),
    }, None


def main():
    root, seconds = int(sys.argv[1]), float(sys.argv[2])
    stats, _ = sample_tree(root, seconds, None)
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
