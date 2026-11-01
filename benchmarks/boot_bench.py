#!/usr/bin/env python3
"""Boot a server, time to first HTTP 200, sample idle RSS/CPU, optionally run
the ws handshake bench against it, then kill the whole tree.

usage: boot_bench.py <out.json> <port> <workdir> <cmd...> [--ws ws://url]
"""
import json, os, signal, subprocess, sys, time, urllib.error, urllib.request

sys.path.insert(0, "/tmp/bench")
from sample_rss import sample_tree


def main():
    args = sys.argv[1:]
    out_path, port, workdir = args[0], int(args[1]), args[2]
    cmd, ws_url = [], None
    it = iter(args[3:])
    for a in it:
        if a == "--ws":
            ws_url = next(it)
        else:
            cmd.append(a)

    t0 = time.monotonic()
    log = open(out_path + ".log", "w")
    proc = subprocess.Popen(cmd, cwd=workdir, stdout=log, stderr=log,
                            start_new_session=True)
    boot_s = None
    while time.monotonic() - t0 < 45:
        if proc.poll() is not None:
            break
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as r:
                if r.status == 200:
                    boot_s = time.monotonic() - t0
                    break
        except urllib.error.HTTPError:
            # 404 etc. still proves the HTTP server is accepting connections
            boot_s = time.monotonic() - t0
            break
        except Exception:
            time.sleep(0.3)
    if boot_s is None:
        proc.terminate()
        proc.wait(timeout=10)
        print(json.dumps({"boot": None, "cmd": " ".join(cmd), "exit": proc.returncode}))
        sys.exit(1)

    # idle RSS/CPU over 12s after boot
    rss, cpu = sample_tree(proc.pid, 12, proc)
    ws_result = None
    if ws_url:
        runtime = os.environ.get("WSBENCH_CMD", "node")
        r = subprocess.run([runtime, "/tmp/bench/ws-bench.mjs", ws_url, "200"],
                           capture_output=True, text=True, cwd=workdir)
        try:
            ws_result = json.loads(r.stdout)
        except json.JSONDecodeError:
            ws_result = {"error": r.stdout + r.stderr}

    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    proc.wait(timeout=10)
    json.dump({"boot_s": round(boot_s, 2), "idle": rss, "ws": ws_result,
               "cmd": " ".join(cmd)},
              open(out_path, "w"), indent=1)


if __name__ == "__main__":
    main()
