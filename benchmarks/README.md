# Benchmark harness

Measures the metrics in `BUN-MIGRATION-PLAYBOOK.md` §0/§9. Python stdlib +
Node/Bun only; samples `/proc` for PSS (shared pages divided among sharers —
do not "simplify" to RSS, it double-counts the isolated-linker layout).

| File            | Measures                                                                | Usage                                                                                                   |
| --------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `benchrun.py`   | wall time + peak tree PSS + total CPU of a finishing command            | `python3 benchmarks/benchrun.py <cmd…> > result.json`                                                   |
| `boot_bench.py` | server boot→HTTP 200, idle PSS/CPU, optional WS bench, kills tree after | `python3 benchmarks/boot_bench.py out.json <port> <workdir> <cmd…> [--ws ws://…]`                       |
| `sample_rss.py` | idle PSS/CPU of a running tree                                          | imported by boot_bench; standalone `python3 benchmarks/sample_rss.py <pid> <seconds>`                   |
| `ws-bench.mjs`  | sequential WS handshakes ×N                                             | `node benchmarks/ws-bench.mjs ws://127.0.0.1:3100/live 200` (resolve `ws` from the repo's node_modules) |

Baseline numbers for commit `44fdd8c47` (pnpm/node): see playbook §0 table.
Cold-install runs are network-bound: 3 runs, quote median, keep the store in
`/tmp/bench/pnpm-store-cold` (pnpm) and `--cache-dir /tmp/bench/bun-cache`
(bun) so both sides are measured cold-to-cold. The live server needs a
valkey on 6379: `docker run -d --rm -p 6379:6379 valkey/valkey:7.2.11-alpine`.
