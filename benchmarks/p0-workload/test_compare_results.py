"""CLI tests for compare_results.py.

Each test builds tiny but schema-real runner/collector JSON files, runs the script
as a consumer would, and asserts the exit code and report. Only boundaries and
consumer-visible false passes are covered; there are no source-text or wiring tests.
"""
import copy
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "compare_results.py")

DEFAULT_KINDS = ("read-issue-list", "edit-page", "write-issue-patch", "write-issue-create")
DEFAULT_MS = {kind: 100.0 for kind in DEFAULT_KINDS}
DEFAULT_MS["file-roundtrip"] = 100.0
DEFAULT_MS["read-pages"] = 100.0
DEFAULT_MEMORY = {"a": (100, 150, 150), "b": (50, 50, 50)}      # simultaneous peak 200
WINNING_MEMORY = {"a": (50, 75, 75), "b": (25, 25, 25)}        # simultaneous peak 100
DEFAULT_CPU = {"a": (1000, 1500, 2000), "b": (1000, 1000, 1000)}  # 1000 usec of load-window CPU
EPOCH_2026 = 1767225600  # 2026-01-01T00:00:00Z


def p95_from(values):
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil((95 / 100) * len(ordered)) - 1)
    return float(ordered[index])


def build_runner(label="run", kinds=DEFAULT_KINDS, *, planned=None, rejected=0, errors=0, ms=None,
                 declared_p95_delta=None, page_missing=None, issue_actual=None,
                 durable_pages=True, durable_issues=True, outcome_overrides=None,
                 harness_failures=None, gate_pass=None, duration_s=None,
                 schema="p0-workload/5", read_item_count=5,
                 load_completed_at="2026-01-01T00:00:10Z"):
    planned = len(kinds) if planned is None else planned
    dispatched = planned - rejected
    if dispatched < 0 or errors > dispatched:
        raise ValueError("fixture cannot dispatch that many operations")
    ms = dict(DEFAULT_MS if ms is None else ms)

    operations = []
    for index in range(dispatched):
        kind = kinds[index % len(kinds)]
        ok = index >= errors
        record = {"kind": kind, "actor_id": "actor", "t": 0.0,
                  "ms": ms[kind] if ok else 30000.0, "ok": ok}
        if kind.startswith("read") and (
            schema == "p0-workload/5" or (schema == "p0-workload/4" and kind != "read-pages")
        ):
            record["items"] = read_item_count
        if ok:
            record["status"] = 200
        else:
            record["error"] = "TimeoutError: The operation timed out."
        operations.append(record)

    by_kind = {}
    for op in operations:
        by_kind.setdefault(op["kind"], []).append(op["ms"])
    latency = {}
    for kind, values in by_kind.items():
        computed = p95_from(values)
        latency[kind] = {"p50": values[0], "p95": computed + (declared_p95_delta or 0), "n": len(values)}

    ok_counts = {}
    for op in operations:
        if op["ok"]:
            ok_counts[op["kind"]] = ok_counts.get(op["kind"], 0) + 1

    edits = ok_counts.get("edit-page", 0) + ok_counts.get("edit-page-reconnect", 0)
    patches = ok_counts.get("write-issue-patch", 0)
    creates = ok_counts.get("write-issue-create", 0)
    file_ops = sum(1 for op in operations if op["kind"] == "file-roundtrip")
    file_ok = ok_counts.get("file-roundtrip", 0)
    uploaded = file_ops if file_ops else 1
    roundtripped = file_ok if file_ops else 1

    markers = [f"marker-{index + 1}" for index in range(edits)]
    activities = [f"activity-{index + 1}" for index in range(patches)]
    pages = {"page-1": {"expected": markers, "missing": list(page_missing or []),
                        "persisted_xml": "".join(f"<p>{marker}</p>" for marker in markers)}} \
        if durable_pages else {}
    issues = {"issue-1": {"expected": "final", "actual": "final" if issue_actual is None else issue_actual,
                          "expected_activities": activities, "missing_activities": []}} \
        if durable_issues else {}
    read_items = {}
    if schema in ("p0-workload/4", "p0-workload/5"):
        for op in operations:
            if "items" in op:
                read_items[op["kind"]] = read_items.get(op["kind"], 0) + op["items"]
    outcome = {"issuesCreated": creates, "issuesPatched": patches, "issueReadsOk": 1,
               "pagesEdited": edits, "pageReconnects": 1, "persistedMarkers": edits,
               "reconnectMarkers": edits, "activitiesVerified": patches,
               "filesUploaded": uploaded, "filesRoundTripOk": roundtripped, "wsSynced": 1}
    if read_items:
        outcome["readItems"] = read_items
    outcome.update(outcome_overrides or {})

    rejected_detail = [
        {"kind": kinds[index % len(kinds)], "t": 0.0, "reason": "max-inflight"}
        for index in range(rejected)
    ]

    failures = list(harness_failures or [])
    if dispatched != planned:
        failures.append(f"completed:{dispatched}/{planned}")
    if errors:
        failures.append(f"operation-errors:{errors}")
    if rejected:
        failures.append(f"rejected-arrivals:{rejected}")
    if gate_pass is None:
        gate_pass = not failures

    runner = {
        "schema": schema,
        "label": label,
        "base_url": "http://127.0.0.1:8080",
        "dataset": {"workspace_slug": "bench", "project_id": "p", "page_ids": ["page-1"],
                    "issue_ids": ["issue-1"]},
        "started_at": "2026-01-01T00:00:00Z",
        "load_started_at": "2026-01-01T00:00:00Z",
        "load_completed_at": load_completed_at,
        "finished_at": "2026-01-01T00:09:00Z",
        "offered": {"steady_rate": 1, "burst_rate": 1, "burst_after_s": 0, "burst_duration_s": 0,
                    "duration_s": planned if duration_s is None else duration_s,
                    "recovery_s": 10, "planned_ops": planned,
                    "rejected_ops": rejected,
                    "max_dispatch_lag_ms": 0, "users": 20, "editors": 5},
        "achieved": {"total_ops": dispatched, "achieved_rate_per_s": 1.0, "errors": errors},
        "outcome": outcome,
        "gate": {"pass": gate_pass, "failures": failures},
        "queue": {"sampled_depths": [0], "drain_s": 0},
        "latency": latency,
        "operations": operations,
        "durable": {"pages": pages, "issues": issues},
    }
    if schema in ("p0-workload/4", "p0-workload/5"):
        runner["offered"]["rejected"] = rejected_detail
    return runner


def build_collector(*, per_service=None, anon=None, file=None, cpu=None, duration_s=10,
                    interval_s=5, swap=0, host=None, images=None, image_ids=None):
    """Rounds tile the declared window; memory = anon + file (sock 0)."""
    anon = anon or per_service or DEFAULT_MEMORY
    file = file or {name: tuple(0 for _ in values) for name, values in anon.items()}
    cpu = cpu or DEFAULT_CPU
    images = images or {"a": "img-a", "b": "img-b"}
    image_ids = image_ids or {"a": "id-a", "b": "id-b"}
    count = int(duration_s // interval_s) + 1

    def value(series, index):
        return series[min(index, len(series) - 1)]

    rounds = []
    for index in range(count):
        samples = {}
        anon_sum = 0
        file_sum = 0
        for name, anon_values in anon.items():
            a = value(anon_values, index)
            f = value(file[name], index)
            anon_sum += a
            file_sum += f
            samples[name] = {
                "memory_current_bytes": a + f,
                "cpu_usage_usec": value(cpu[name], index),
                "anon_bytes": a,
                "file_bytes": f,
                "sock_bytes": 0,
                "swap_current_bytes": swap,
                "cpu_delta_usec": None,
            }
        rounds.append({"i": index, "t": EPOCH_2026 + index * interval_s,
                       "iso": "2026-01-01T00:00:00Z",
                       "samples": samples, "missing": [], "valid": True,
                       "round_sum_bytes": anon_sum + file_sum,
                       "anon_sum_bytes": anon_sum, "file_sum_bytes": file_sum})
    peak = max(round_["round_sum_bytes"] for round_ in rounds)
    peak_anon = max(round_["anon_sum_bytes"] for round_ in rounds)
    return {
        "schema": "p0-cgroup-v2/2",
        "collector": {"name": "collector"},
        "instrument": {"metric": "memory.current", "unit": "bytes", "cpu_metric": "cpu.stat usage_usec",
                       "cpu_unit": "microseconds", "source": "cgroup v2"},
        "provenance": {"baseline_revision": "rev"},
        "access_mode": "host",
        "host": host or {"cgroup2_mount": "/sys/fs/cgroup"},
        "interval_s": interval_s,
        "duration_s": duration_s,
        "started_at": "2026-01-01T00:00:00Z",
        "ended_at": "2026-01-01T00:10:00Z",
        "containers": [{"id": "1", "name": "a", "image": images["a"], "image_id": image_ids["a"]},
                       {"id": "2", "name": "b", "image": images["b"], "image_id": image_ids["b"]}],
        "rounds": rounds,
        "summary": {"rounds_total": count, "rounds_valid": count, "observed_peak_bytes": peak,
                    "peak_round_i": count - 1, "peak_anon_bytes": peak_anon, "peak_file_bytes": 0,
                    "run_valid": True, "invalid_reasons": [], "per_container": {}, "interrupted": False},
    }


def build_provenance(memory_max=4294967296, snapshot=None, concurrency: int | None = 2,
                     host: str | None = "host-a", host_token: str | None = None,
                     host_facts: dict | None = None, source: dict | None = None,
                     runs: list | None = None, harness_revision: str | None = "rev-a",
                     runner_schema: str | None = "p0-workload/4"):
    provenance: dict = {
        "schema": "p0-reference/2",
        "cpu_quota": "2 CPUs shared",
        "memory_max": memory_max,
        "memory_swap_max": 0,
        "snapshot_sha256": snapshot or {"uploads": "h1", "pgdata": "h2"},
        "api_source_sha256": source if source is not None else {"run-1": "sha-a"},
        "runs": runs if runs is not None else [
            {"label": "run-1", "runner_exit": 0, "collector_exit": 0}
        ],
    }
    if concurrency is not None:
        provenance["baseline_worker_concurrency"] = concurrency
    if host is not None:
        provenance["host"] = host
    if host_token is not None:
        provenance["host_token"] = host_token
    if host_facts is not None:
        provenance["host_facts"] = host_facts
    if harness_revision is not None:
        provenance["harness_revision"] = harness_revision
    if runner_schema is not None:
        provenance["runner_schema"] = runner_schema
    return provenance


class ComparatorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.control_prov = self.write_json("control-prov.json", build_provenance())
        self.candidate_prov = self.write_json("candidate-prov.json", build_provenance())

    def tearDown(self):
        self._tmp.cleanup()

    # ---------------------------------------------------------------- helpers
    def write_json(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return path

    def write_side(self, role, pairs):
        args = []
        for index, (runner, collector) in enumerate(pairs):
            args += ["--" + role, self.write_json(f"{role}-{index}-runner.json", runner),
                     self.write_json(f"{role}-{index}-collector.json", collector)]
        return args

    def run_cli(self, control, candidate, *,
                control_prov: str | None = "default",
                candidate_prov: str | None = "default") -> tuple[subprocess.CompletedProcess, dict]:
        report_path = os.path.join(self.tmp, "report.json")
        if os.path.exists(report_path):
            os.remove(report_path)
        argv = [sys.executable, SCRIPT, *self.write_side("control", control),
                *self.write_side("candidate", candidate), "--json", report_path]
        if control_prov == "default":
            control_prov = self.control_prov
        if candidate_prov == "default":
            candidate_prov = self.candidate_prov
        if control_prov:
            argv += ["--control-provenance", control_prov]
        if candidate_prov:
            argv += ["--candidate-provenance", candidate_prov]
        result = subprocess.run(argv, capture_output=True, text=True)
        self.assertTrue(os.path.exists(report_path), result.stdout + result.stderr)
        with open(report_path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
        return result, report

    def sides(self, runner=None, collector=None, count=3):
        runner = build_runner() if runner is None else runner
        collector = build_collector() if collector is None else collector
        return [(copy.deepcopy(runner), copy.deepcopy(collector)) for _ in range(count)]

    def winning_collector(self, **overrides):
        return build_collector(anon=WINNING_MEMORY, **overrides)

    def assertInvalid(self, result, report):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "invalid")

    def assertInconclusive(self, result, report, needle):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertIn(needle, report["reason"])

    # ---------------------------------------------------------------- gates
    def test_pass_at_exact_half_current(self):
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "pass")
        self.assertIn("loaded-workload", report["scope"])
        self.assertAlmostEqual(report["gates"]["ram"]["worst_ratio"], 0.5, places=9)
        self.assertTrue(report["gates"]["ram"]["pass"])
        self.assertTrue(report["gates"]["ram"]["corroboration"]["corroborated"])
        self.assertIn("peak simultaneous memory.current", report["gates"]["ram"]["metric"])

    def test_p95_exact_ceiling_passes_and_above_fails(self):
        control = self.sides()
        at_limit = self.sides(build_runner(ms={kind: 105.0 for kind in DEFAULT_KINDS}),
                              self.winning_collector())
        result, report = self.run_cli(control, at_limit)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(all(gate["pass"] for gate in report["gates"]["p95"].values()))

        above = self.sides(build_runner(ms={kind: 105.01 for kind in DEFAULT_KINDS}),
                           self.winning_collector())
        result, report = self.run_cli(control, above)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["verdict"], "fail")

    def test_zero_denominator_is_not_nan(self):
        control = self.sides(build_runner(ms={kind: 0.0 for kind in DEFAULT_KINDS}))
        zero_win = self.sides(build_runner(ms={kind: 0.0 for kind in DEFAULT_KINDS}),
                              self.winning_collector())
        result, report = self.run_cli(control, zero_win)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["gates"]["p95"]["edit-page"]["worst_ratio"], 1.0)

        positive = self.sides(build_runner(ms={kind: 5.0 for kind in DEFAULT_KINDS}),
                              self.winning_collector())
        result, report = self.run_cli(control, positive)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(report["gates"]["p95"]["edit-page"]["worst_ratio"] > 1.0)

    def test_cpu_uses_raw_last_minus_first_counters(self):
        # cpu_delta_usec is absent in every fixture; a delta-based reader would see no CPU.
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        expected = (1000 + 0) / 1e6 / 4
        self.assertAlmostEqual(report["samples"]["control"][0]["cpu_s_per_op"], expected, places=12)

    def test_longer_idle_tail_does_not_score_better(self):
        # Same load-window CPU, extra CPU accrued during the post-load tail only.
        control_cpu = {"a": (1000, 1500, 2000, 2000, 2000), "b": (1000, 1000, 1000, 1000, 1000)}
        candidate_cpu = {"a": (1000, 1500, 2000, 9000, 15000), "b": (1000, 1000, 1000, 1000, 1000)}
        control_collector = build_collector(cpu=control_cpu, duration_s=20)
        candidate_collector = build_collector(cpu=candidate_cpu, duration_s=20,
                                              anon=WINNING_MEMORY)
        result, report = self.run_cli(self.sides(collector=control_collector),
                                      self.sides(collector=candidate_collector))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertAlmostEqual(report["gates"]["cpu"]["worst_ratio"], 1.0, places=9)
        self.assertGreater(report["samples"]["candidate"][0]["cpu_s_collector_window"],
                           report["samples"]["control"][0]["cpu_s_collector_window"])

    def test_fast_failure_cannot_score_better(self):
        kinds = ("read-issue-list", "read-pages", "edit-page", "write-issue-patch",
                 "write-issue-create")
        control_runner = build_runner(kinds=kinds, planned=5)
        candidate_runner = build_runner(kinds=kinds, planned=5, errors=2)
        result, report = self.run_cli(self.sides(control_runner), self.sides(candidate_runner))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        # 1000 usec / 3 successful ops vs 1000 usec / 5 successful ops = 5/3.
        self.assertAlmostEqual(report["gates"]["cpu"]["worst_ratio"], 5 / 3, places=9)
        self.assertAlmostEqual(
            report["samples"]["candidate"][0]["cpu_s_per_dispatched_op"],
            report["samples"]["control"][0]["cpu_s_per_dispatched_op"], places=12)

    def test_absent_load_timestamps_is_invalid(self):
        runner = build_runner()
        del runner["load_started_at"]
        result, report = self.run_cli(self.sides(runner), self.sides())
        self.assertInvalid(result, report)
        self.assertIn("load_started_at", report["reason"])

    def test_read_items_must_cover_every_read_kind(self):
        kinds = ("read-issue-list", "read-pages", "edit-page", "write-issue-patch",
                 "write-issue-create")
        control_runner = build_runner(kinds=kinds, planned=5)
        runner = build_runner(kinds=kinds, planned=5)
        del control_runner["outcome"]["readItems"]["read-pages"]
        del runner["outcome"]["readItems"]["read-pages"]
        result, report = self.run_cli(self.sides(control_runner),
                                      self.sides(runner, self.winning_collector()))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertIn("readItems missing kinds: read-pages", report["reason"])

    def test_equal_simultaneous_peak_is_not_a_memory_win(self):
        # Control services peak in different rounds; candidate services peak together.
        control_collector = build_collector(per_service={"a": (200, 0, 0), "b": (0, 200, 0)})
        candidate_collector = build_collector(per_service={"a": (120, 120, 120), "b": (120, 120, 120)})
        result, report = self.run_cli(self.sides(collector=control_collector),
                                      self.sides(collector=candidate_collector))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertAlmostEqual(report["gates"]["ram"]["worst_ratio"], 1.2, places=9)

    def test_cache_only_50_percent_win_does_not_pass(self):
        # Control runs with a warm cache (anon 100, file 200); candidate has the same anon
        # but a cold cache (file 50). memory.current halves, anon does not move.
        control_collector = build_collector(
            anon={"a": (50, 50, 50), "b": (50, 50, 50)},
            file={"a": (100, 100, 100), "b": (100, 100, 100)},
        )
        candidate_collector = build_collector(
            anon={"a": (50, 50, 50), "b": (50, 50, 50)},
            file={"a": (25, 25, 25), "b": (25, 25, 25)},
        )
        result, report = self.run_cli(self.sides(collector=control_collector),
                                      self.sides(collector=candidate_collector))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertTrue(report["gates"]["ram"]["pass"])
        self.assertAlmostEqual(report["gates"]["ram"]["worst_ratio"], 0.5, places=9)
        self.assertFalse(report["gates"]["ram"]["corroboration"]["corroborated"])
        self.assertIn("not corroborated by anon", report["reason"])

    # ---------------------------------------------------------------- harness vs capacity
    def test_harness_valid_capacity_failing_control_is_accepted(self):
        control_runner = build_runner(planned=8, rejected=2, errors=1)
        candidate_runner = build_runner(planned=8)
        result, report = self.run_cli(self.sides(control_runner),
                                      self.sides(candidate_runner, self.winning_collector()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "pass")
        self.assertFalse(report["capacity"]["control"]["all_pass"])
        self.assertTrue(report["capacity"]["candidate"]["all_pass"])
        first = report["capacity"]["control"]["samples"][0]
        self.assertEqual((first["planned"], first["dispatched"], first["rejected"], first["errors"]),
                         (8, 6, 2, 1))

    def test_candidate_completing_all_work_is_reported_as_more_work(self):
        control_runner = build_runner(planned=8, rejected=2, errors=1)
        candidate_runner = build_runner(planned=8)
        result, report = self.run_cli(self.sides(control_runner),
                                      self.sides(candidate_runner, self.winning_collector()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["capacity"]["work_comparison"],
                         "candidate completed more work than control")
        self.assertEqual(report["capacity"]["candidate_completed_median"], 8)
        self.assertEqual(report["capacity"]["control_completed_median"], 6)

    def test_unknown_candidate_evidence_blocks_pass(self):
        other = self.write_json("concurrency-prov.json", build_provenance(concurrency=4))
        runner = build_runner()
        del runner["offered"]["rejected"]
        result, report = self.run_cli(self.sides(), self.sides(runner, self.winning_collector()),
                                      candidate_prov=other)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertIn("candidate evidence unknown: offered.rejected[]", report["reason"])
        self.assertTrue(report["gates"]["ram"]["pass"])

    def test_harness_durability_failure_is_rejected_by_the_harness_rule(self):
        # Capacity fields look fine; the gate says the run did not prove persistence.
        runner = build_runner(harness_failures=["persisted-or-reconnected-edit-missing"])
        result, report = self.run_cli(self.sides(runner), self.sides())
        self.assertInvalid(result, report)
        self.assertIn("harness-side gate failure", report["reason"])
        self.assertIn("persisted-or-reconnected-edit-missing", report["reason"])

    def test_capacity_failing_control_with_file_mismatch_is_accepted(self):
        # A failed file round trip is a reported operation error (capacity), not silent loss.
        kinds = ("file-roundtrip", "read-issue-list", "edit-page", "write-issue-patch",
                 "write-issue-create")
        control_runner = build_runner(kinds=kinds, planned=5, errors=1)
        candidate_runner = build_runner(kinds=kinds, planned=5)
        result, report = self.run_cli(self.sides(control_runner),
                                      self.sides(candidate_runner, self.winning_collector()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "pass")
        control = report["capacity"]["control"]["samples"][0]
        self.assertFalse(control["pass"])
        self.assertEqual(control["errors"], 1)
        integrity = report["samples"]["control"][0]["file_integrity"]
        self.assertEqual((integrity["uploaded"], integrity["roundtripped"], integrity["ok"]),
                         (1, 0, False))

    def test_marker_losing_sample_is_rejected_on_durability_not_capacity(self):
        # v5-d2-1 pattern: capacity fields look fine, but acknowledged edits were not persisted.
        runner = build_runner(page_missing=["marker-1"],
                              outcome_overrides={"persistedMarkers": 0, "reconnectMarkers": 0})
        result, report = self.run_cli(self.sides(runner), self.sides())
        self.assertInvalid(result, report)
        self.assertIn("missing persisted edits", report["reason"])
        self.assertNotIn("capacity", report["reason"])

    def test_schema3_control_cannot_support_a_schema5_candidate(self):
        control_runner = build_runner(schema="p0-workload/3")
        candidate_runner = build_runner()
        result, report = self.run_cli(self.sides(control_runner),
                                      self.sides(candidate_runner, self.winning_collector()))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertIn("control-side read payload counts not recorded", report["reason"])
        self.assertIn("p0-workload/3", report["reason"])
        self.assertEqual(report["payload_equivalence"]["control_payload_equivalence"], "unknown")

    def test_schema5_control_with_schema5_candidate_is_eligible(self):
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["payload_equivalence"]["candidate_payload_equivalence"], "recorded")

    def test_schema4_control_missing_pages_is_inconclusive_against_schema5(self):
        kinds = ("read-issue-list", "read-pages", "edit-page", "write-issue-patch",
                 "write-issue-create")
        control_runner = build_runner(kinds=kinds, planned=5, schema="p0-workload/4")
        candidate_runner = build_runner(kinds=kinds, planned=5)
        result, report = self.run_cli(self.sides(control_runner),
                                      self.sides(candidate_runner, self.winning_collector()))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertIn("control evidence unknown: readItems missing kinds: read-pages", report["reason"])
        self.assertEqual(report["payload_equivalence"]["control_payload_equivalence"], "unknown")

    def test_schema3_candidate_is_inconclusive(self):
        candidate_runner = build_runner(schema="p0-workload/3")
        result, report = self.run_cli(self.sides(),
                                      self.sides(candidate_runner, self.winning_collector()))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("candidate-side read payload counts not recorded", report["reason"])

    def test_same_harness_is_eligible(self):
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(report["metadata"]["harness"]["changed"])
        self.assertEqual(report["metadata"]["harness"]["evidence"], "top-level fallback (weaker)")

    def test_harness_difference_blocks_single_variable_claim(self):
        other = self.write_json("harness-prov.json", build_provenance(harness_revision="rev-b"))
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()),
                                      candidate_prov=other)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertIn("single changed variable not established", report["reason"])
        self.assertIn("harness_revision", report["metadata"]["changed_variables"])
        self.assertTrue(report["metadata"]["harness"]["changed"])

    def test_per_run_harness_fields_are_used(self):
        prov = build_provenance(
            harness_revision=None, runner_schema=None,
            runs=[{"label": "run", "harness_revision": "rev-a", "runner_schema": "p0-workload/4",
                   "runner_exit": 0, "collector_exit": 0}],
        )
        path = self.write_json("per-run-prov.json", prov)
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()),
                                      control_prov=path, candidate_prov=path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["metadata"]["harness"]["evidence"], "per-run")

    def test_top_level_harness_fallback_is_labelled_weaker(self):
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["metadata"]["harness"]["evidence"], "top-level fallback (weaker)")

    def test_missing_harness_identity_is_inconclusive(self):
        missing = self.write_json("no-harness-prov.json", build_provenance(
            harness_revision=None, runner_schema=None))
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()),
                                      candidate_prov=missing)
        self.assertEqual(result.returncode, 2)
        self.assertIn("harness identity not recorded", report["metadata"]["reason"])

    def test_harness_invalid_control_is_rejected_even_if_capacity_passes(self):
        runner = build_runner(page_missing=["marker-1"])
        result, report = self.run_cli(self.sides(runner), self.sides())
        self.assertInvalid(result, report)
        self.assertIn("missing persisted edits", report["reason"])

    def test_candidate_dropping_ops_cannot_pass(self):
        control_runner = build_runner(planned=8)
        candidate_runner = build_runner(planned=8, rejected=2)
        result, report = self.run_cli(self.sides(control_runner),
                                      self.sides(candidate_runner, self.winning_collector()))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("candidate capacity", report["reason"])
        self.assertFalse(report["capacity"]["candidate"]["all_pass"])

    def test_capacity_failing_candidate_fails_on_its_own_merits(self):
        control_runner = build_runner(planned=8)
        candidate_runner = build_runner(planned=8, errors=2)
        result, report = self.run_cli(self.sides(control_runner), self.sides(candidate_runner))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("candidate capacity", report["reason"])
        self.assertEqual(report["capacity"]["candidate"]["samples"][0]["errors"], 2)

    # ---------------------------------------------------------------- sample eligibility
    def test_fractional_counter_is_invalid(self):
        runner = build_runner()
        runner["offered"]["rejected_ops"] = 0.5
        result, report = self.run_cli(self.sides(runner), self.sides())
        self.assertInvalid(result, report)
        self.assertIn("integer", report["reason"])

    def test_incomplete_work_invalidates_sample(self):
        runner = build_runner()
        runner["achieved"]["total_ops"] = 3
        result, report = self.run_cli(self.sides(runner), self.sides())
        self.assertInvalid(result, report)

    def test_gate_pass_with_missing_durable_marker_is_invalid(self):
        result, report = self.run_cli(self.sides(build_runner(page_missing=["marker-1"])), self.sides())
        self.assertInvalid(result, report)
        self.assertIn("missing persisted edits", report["reason"])

    def test_empty_durable_sections_are_invalid(self):
        result, report = self.run_cli(self.sides(build_runner(durable_pages=False)), self.sides())
        self.assertInvalid(result, report)

    def test_issue_value_mismatch_is_invalid(self):
        result, report = self.run_cli(self.sides(build_runner(issue_actual="else")), self.sides())
        self.assertInvalid(result, report)

    def test_durability_count_without_matching_markers_is_invalid(self):
        overrides = {"pagesEdited": 2, "persistedMarkers": 2, "reconnectMarkers": 2}
        result, report = self.run_cli(self.sides(build_runner(outcome_overrides=overrides)), self.sides())
        self.assertInvalid(result, report)
        self.assertIn("expected markers", report["reason"])

    def test_counter_without_matching_operations_is_invalid(self):
        overrides = {"pagesEdited": 2, "persistedMarkers": 2, "reconnectMarkers": 2}
        runner = build_runner(outcome_overrides=overrides)
        runner["durable"]["pages"]["page-1"]["expected"] = ["marker-1", "marker-2"]
        runner["durable"]["pages"]["page-1"]["persisted_xml"] = "<p>marker-1</p><p>marker-2</p>"
        result, report = self.run_cli(self.sides(runner), self.sides())
        self.assertInvalid(result, report)
        self.assertIn("successful edit-page", report["reason"])

    def test_non_numeric_metric_is_invalid(self):
        collector = build_collector()
        collector["summary"]["observed_peak_bytes"] = None
        result, report = self.run_cli(self.sides(collector=collector), self.sides())
        self.assertInvalid(result, report)

    def test_tampered_reported_p95_is_invalid(self):
        result, report = self.run_cli(self.sides(build_runner(declared_p95_delta=5)), self.sides())
        self.assertInvalid(result, report)
        self.assertIn("recomputed", report["reason"])

    def test_swapped_conditions_are_invalid(self):
        result, report = self.run_cli(self.sides(collector=build_collector(swap=4096)), self.sides())
        self.assertInvalid(result, report)
        self.assertIn("swap", report["reason"])

    def test_tampered_round_sum_is_invalid(self):
        collector = build_collector()
        collector["rounds"][1]["round_sum_bytes"] += 1024
        result, report = self.run_cli(self.sides(collector=collector), self.sides())
        self.assertInvalid(result, report)
        self.assertIn("sum of container memory", report["reason"])

    def test_tampered_anon_sum_is_invalid(self):
        collector = build_collector()
        collector["rounds"][1]["anon_sum_bytes"] += 1024
        result, report = self.run_cli(self.sides(collector=collector), self.sides())
        self.assertInvalid(result, report)
        self.assertIn("anon sum", report["reason"])

    def test_truncated_window_is_invalid(self):
        collector = build_collector()
        collector["duration_s"] = 620
        collector["interval_s"] = 2
        collector["rounds"] = [
            {"i": 0, "t": 0.0, "iso": "2026-01-01T00:00:00Z",
             "samples": copy.deepcopy(collector["rounds"][0]["samples"]),
             "missing": [], "valid": True, "round_sum_bytes": collector["rounds"][0]["round_sum_bytes"],
             "anon_sum_bytes": collector["rounds"][0]["anon_sum_bytes"], "file_sum_bytes": 0},
            {"i": 1, "t": 2.0, "iso": "2026-01-01T00:00:02Z",
             "samples": copy.deepcopy(collector["rounds"][0]["samples"]),
             "missing": [], "valid": True, "round_sum_bytes": collector["rounds"][0]["round_sum_bytes"],
             "anon_sum_bytes": collector["rounds"][0]["anon_sum_bytes"], "file_sum_bytes": 0},
        ]
        collector["summary"]["rounds_total"] = 2
        collector["summary"]["rounds_valid"] = 2
        collector["summary"]["observed_peak_bytes"] = collector["rounds"][0]["round_sum_bytes"]
        result, report = self.run_cli(self.sides(collector=collector), self.sides())
        self.assertInvalid(result, report)
        self.assertIn("cannot cover the declared 620", report["reason"])

    def test_jittered_but_complete_window_is_accepted(self):
        collector = self.winning_collector()
        collector["rounds"][1]["t"] += 2.0  # 7s then 3s gaps around a 5s cadence
        result, report = self.run_cli(self.sides(), self.sides(collector=collector))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "pass")

    def test_long_sampling_gap_is_invalid(self):
        collector = self.winning_collector()
        collector["rounds"][1]["t"] += 20.0
        result, report = self.run_cli(self.sides(collector=collector), self.sides())
        self.assertInvalid(result, report)

    # ---------------------------------------------------------------- comparability
    def test_kind_count_mismatch_is_inconclusive_when_both_capacity_pass(self):
        control = self.sides(build_runner(kinds=("read-issue-list", "edit-page", "edit-page",
                                                 "write-issue-patch", "write-issue-create")))
        candidate = self.sides(build_runner(kinds=("read-issue-list", "read-issue-list", "edit-page",
                                                   "write-issue-patch", "write-issue-create")))
        result, report = self.run_cli(control, candidate)
        self.assertInconclusive(result, report, "count differs")

    def test_read_payload_count_mismatch_is_inconclusive(self):
        candidate = self.sides(build_runner(read_item_count=6), self.winning_collector())
        result, report = self.run_cli(self.sides(), candidate)
        self.assertInconclusive(result, report, "read payload counts differ")

    def test_different_window_is_inconclusive(self):
        result, report = self.run_cli(self.sides(), self.sides(collector=build_collector(duration_s=20)))
        self.assertInconclusive(result, report, "duration_s")

    def test_image_change_inside_one_condition_is_inconclusive(self):
        candidate = self.sides()
        candidate[0][1]["containers"][0]["image"] = "other-a"
        result, report = self.run_cli(self.sides(), candidate)
        self.assertInconclusive(result, report, "image map")

    def test_image_id_change_inside_one_condition_is_inconclusive(self):
        candidate = self.sides()
        candidate[0][1]["containers"][0]["image_id"] = "other-id"
        result, report = self.run_cli(self.sides(), candidate)
        self.assertInconclusive(result, report, "image map")

    def test_image_change_between_conditions_is_allowed(self):
        candidate = self.sides(collector=self.winning_collector(
            images={"a": "new-a", "b": "new-b"}, image_ids={"a": "new-id-a", "b": "new-id-b"}))
        result, report = self.run_cli(self.sides(), candidate)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "pass")
        self.assertIn("image_set", report["metadata"]["changed_variables"])

    def test_fewer_than_three_samples_is_inconclusive(self):
        result, report = self.run_cli(self.sides(count=2), self.sides(count=2))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertIn("need >= 3 samples", report["reason"])

    # ---------------------------------------------------------------- metadata
    def test_missing_provenance_is_inconclusive_even_when_gates_pass(self):
        candidate = self.sides(collector=self.winning_collector())
        result, report = self.run_cli(self.sides(), candidate, control_prov=None, candidate_prov=None)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertIn("provenance", report["metadata"]["reason"])

    def test_provenance_without_host_identity_is_inconclusive(self):
        no_host = self.write_json("no-host-prov.json", build_provenance(host=None))
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()),
                                      candidate_prov=no_host)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertIn("host identity not recorded", report["metadata"]["reason"])

    def test_host_token_is_experiment_local_identity(self):
        token_prov = self.write_json("token-prov.json", build_provenance(host=None, host_token="token-a"))
        control_token = self.write_json("control-token-prov.json", build_provenance(host=None, host_token="token-a"))
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()),
                                      control_prov=control_token, candidate_prov=token_prov)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["metadata"]["host_identity"], "experiment_token")
        self.assertIn("does not prove the same physical host", report["metadata"]["host_provenance"])

    def test_host_token_mismatch_is_inconclusive(self):
        token_prov = self.write_json("token-prov.json", build_provenance(host=None, host_token="token-b"))
        control_token = self.write_json("control-token-prov.json", build_provenance(host=None, host_token="token-a"))
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()),
                                      control_prov=control_token, candidate_prov=token_prov)
        self.assertInconclusive(result, report, "host token differs")

    def test_host_facts_mismatch_is_inconclusive(self):
        facts_prov = self.write_json("facts-prov.json", build_provenance(
            host=None, host_token="token-a", host_facts={"kernel": "other", "cpu_count": 8}))
        control_facts = self.write_json("control-facts-prov.json", build_provenance(
            host=None, host_token="token-a", host_facts={"kernel": "linux", "cpu_count": 2}))
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()),
                                      control_prov=control_facts, candidate_prov=facts_prov)
        self.assertInconclusive(result, report, "host facts differ")

    def test_missing_source_condition_is_inconclusive(self):
        no_source = build_provenance()
        del no_source["api_source_sha256"]
        path = self.write_json("no-source-prov.json", no_source)
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()),
                                      candidate_prov=path)
        self.assertInconclusive(result, report, "source condition not recorded")

    def test_missing_successful_execution_record_is_inconclusive(self):
        failed_run = build_provenance(runs=[{"label": "run-1", "runner_exit": 1, "collector_exit": 0}])
        path = self.write_json("failed-run-prov.json", failed_run)
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()),
                                      candidate_prov=path)
        self.assertInconclusive(result, report, "no successful execution record")

    def test_capacity_failing_execution_record_is_accepted(self):
        # runner_exit 2 means the workload gate failed (capacity), not that the run did not execute.
        prov = build_provenance(runs=[{"label": "run-1", "runner_exit": 2, "collector_exit": 0}])
        path = self.write_json("gate-failed-prov.json", prov)
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()),
                                      candidate_prov=path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["metadata"]["status"], "equal")

    def test_source_change_is_reported_not_rejected(self):
        other_source = build_provenance(source={"run-1": "sha-b"})
        path = self.write_json("other-source-prov.json", other_source)
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()),
                                      candidate_prov=path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(report["metadata"]["source_condition"]["changed"])

    def test_host_identity_mismatch_is_inconclusive(self):
        other = self.write_json("other-host-prov.json", build_provenance(host="host-b"))
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()),
                                      candidate_prov=other)
        self.assertEqual(result.returncode, 2)
        self.assertIn("host identity differs", report["metadata"]["reason"])

    def test_different_snapshot_is_inconclusive(self):
        other = self.write_json("other-prov.json", build_provenance(snapshot={"uploads": "x", "pgdata": "y"}))
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()),
                                      candidate_prov=other)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["metadata"]["status"], "unknown")
        self.assertIn("snapshot_sha256", report["metadata"]["reason"])

    def test_different_limits_are_inconclusive(self):
        other = self.write_json("limit-prov.json", build_provenance(memory_max=8589934592))
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()),
                                      candidate_prov=other)
        self.assertEqual(result.returncode, 2)
        self.assertIn("memory_max", report["metadata"]["reason"])

    def test_image_and_config_change_block_pass_as_unknown_variable(self):
        other = self.write_json("concurrency-prov.json", build_provenance(concurrency=4))
        candidate = self.sides(collector=self.winning_collector(
            images={"a": "new-a", "b": "new-b"}, image_ids={"a": "new-id-a", "b": "new-id-b"}))
        result, report = self.run_cli(self.sides(), candidate, candidate_prov=other)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertIn("single changed variable not established", report["reason"])
        self.assertEqual(report["metadata"]["single_variable"], "unknown")
        self.assertTrue(report["gates"]["ram"]["pass"])

    def test_declared_setting_presence_unknown_blocks_pass(self):
        partial = self.write_json("partial-prov.json", build_provenance(concurrency=None))
        result, report = self.run_cli(self.sides(), self.sides(collector=self.winning_collector()),
                                      candidate_prov=partial)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertIn("declared setting presence unknown", report["reason"])
        self.assertTrue(report["gates"]["ram"]["pass"])

    def test_unknown_variable_does_not_mask_gate_failure(self):
        other = self.write_json("concurrency-prov.json", build_provenance(concurrency=4))
        candidate = self.sides(collector=build_collector(
            images={"a": "new-a", "b": "new-b"}, image_ids={"a": "new-id-a", "b": "new-id-b"}))
        result, report = self.run_cli(self.sides(), candidate, candidate_prov=other)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "fail")
        self.assertFalse(report["gates"]["ram"]["pass"])

    def test_unknown_metadata_does_not_convert_failure_to_inconclusive(self):
        result, report = self.run_cli(self.sides(), self.sides(), control_prov=None, candidate_prov=None)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "fail")


    def test_control_only_mode_reports_payload_unknown_for_schema3(self):
        control_runner = build_runner(schema="p0-workload/3")
        report_path = os.path.join(self.tmp, "control-only.json")
        argv = [sys.executable, SCRIPT, "--control-only",
                "--control", self.write_json("co-runner.json", control_runner),
                self.write_json("co-collector.json", build_collector()),
                "--json", report_path]
        result = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(report_path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["mode"], "control-only")
        self.assertEqual(report["samples"][0]["payload_equivalence"], "unknown")
        self.assertIn("read payload counts", report["samples"][0]["evidence_gaps"])


if __name__ == "__main__":
    unittest.main()