#!/usr/bin/env python3
"""Run a command; emit JSON {cmd, wall_s, exit, peak_tree_rss_bytes, cpu_s}.

Peak RSS and CPU are summed over the whole descendant process tree, sampled
every 150ms from /proc. No external tools needed.
"""
import json, os, subprocess, sys, time


def proc_snapshot():
    """Return {pid: (ppid, pss_bytes, cpu_ticks)}.

    Memory is PSS from smaps_rollup: shared pages (node text, hardlinked
    store files, mmap'd native libs) are divided among sharers, so a
    multi-process tree is not double-counted the way summed RSS would be.
    """
    snap = {}
    pagesize = os.sysconf("SC_PAGE_SIZE")
    clk = os.sysconf("SC_CLK_TCK")
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/stat") as f:
                st = f.read()
            rp = st.rfind(")")
            fields = st[rp + 2:].split()
            ppid = int(fields[1])
            utime, stime = int(fields[11]), int(fields[12])
            pss = 0
            try:
                with open(f"/proc/{pid}/smaps_rollup") as f:
                    for line in f:
                        if line.startswith("Pss:"):
                            pss = int(line.split()[1]) * 1024
                            break
            except OSError:
                # no smaps access: resident pages (over-counts shared pages)
                with open(f"/proc/{pid}/statm") as f:
                    pss = int(f.read().split()[1]) * pagesize
            snap[int(pid)] = (ppid, pss, (utime + stime) / clk)
        except (OSError, IndexError, ValueError):
            continue
    return snap


def tree_stats(root, snap):
    kids = {ppid: [] for ppid, _, _ in snap.values()}
    for pid, (ppid, _, _) in snap.items():
        kids.setdefault(ppid, []).append(pid)
    total_rss, total_cpu, seen = 0, 0.0, set()
    stack = [root]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        entry = snap.get(pid)
        if not entry:
            continue
        total_rss += entry[1]
        total_cpu += entry[2]
        stack.extend(kids.get(pid, []))
    return total_rss, total_cpu


def main():
    cmd = sys.argv[1:]
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd)
    peak_rss, peak_cpu = 0, 0.0
    while proc.poll() is None:
        rss, cpu = tree_stats(proc.pid, proc_snapshot())
        peak_rss = max(peak_rss, rss)
        peak_cpu = max(peak_cpu, cpu)
        time.sleep(0.15)
    wall = time.monotonic() - t0
    print(json.dumps({
        "cmd": " ".join(cmd),
        "wall_s": round(wall, 2),
        "exit": proc.returncode,
        # PSS: proportional set size summed over the tree (kB from smaps_rollup)
        "peak_tree_pss_bytes": peak_rss,
        # cumulative utime+stime of the tree at the last sample ≈ total CPU used
        "cpu_s_total": round(peak_cpu, 2),
    }))


if __name__ == "__main__":
    main()
