#!/usr/bin/env python3
"""Offline acceptance comparator for recorded P0 baseline/candidate results.

Reads existing `p0-workload/3` runner reports, `p0-cgroup-v2/2` collector reports,
and (optionally) the existing per-condition `provenance.json`.

Two properties are kept separate:

* HARNESS-VALID (required for a sample to count at all): every planned slot is
  accounted for (dispatched + rejected == planned), durability is proven with no
  silent loss, the collector window is intact, and no harness-side gate failure
  is present. A harness-invalid sample is rejected (exit 2).
* CAPACITY-PASS (reported, not an eligibility precondition): zero operation
  errors, zero rejected arrivals, completed == planned. A harness-valid sample
  that fails capacity is still a usable control; its capacity outcome is
  recorded per sample.

Gates (loaded-workload only):

    whole-stack RAM   candidate peak memory.current <= 0.50 * control peak
    per-class p95     candidate <= 1.05 * control
    whole-stack CPU   candidate <= 1.05 * control
                      (over the LOAD window, per SUCCESSFUL operation)

RAM is gated on the peak simultaneous `memory.current` round-sum (the
ROADMAP P0.1 instrument, which includes page cache). `peak_anon_bytes` and
`peak_file_bytes` for that same round are mandatory context, and a RAM win is
only accepted when the worst current-ratio sample also reduces anon at least as
much (`anon_ratio <= current_ratio`); otherwise the verdict is inconclusive, so
a saving manufactured by a cold cache cannot pass. The collector-window CPU and
`cpu_per_dispatched_op` are reported as context; the CPU gate uses the load
window and the successful-op denominator. A candidate must be capacity-pass to
PASS: it cannot win by doing less work.

Aggregation: control estimate = median of its per-sample values; a gate passes
only if the candidate's WORST sample clears it. Nothing is averaged away.

A PASS covers the measured loaded-workload gates only. It is not a cold-idle,
expensive-feature, capability-coverage, or whole-project acceptance result.

Exit codes: 0 PASS, 1 gate FAIL, 2 INCONCLUSIVE/INVALID evidence, 3 usage/IO.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import datetime
from dataclasses import dataclass, field

RUNNER_SCHEMA = "p0-workload/5"
CONTROL_SCHEMAS = ("p0-workload/3", "p0-workload/4", "p0-workload/5")
CANDIDATE_SCHEMA = "p0-workload/5"
# Schemas that carry offered.rejected[] and outcome.readItems. The per-kind coverage
# check decides payload equivalence; /3 has no fields, /4 can still miss read kinds.
EVIDENCE_SCHEMAS = ("p0-workload/4", "p0-workload/5")
COLLECTOR_SCHEMA = "p0-cgroup-v2/2"
MIN_SAMPLES = 3
P95_CEILING = 1.05
CPU_CEILING = 1.05
RAM_CEILING = 0.50
REL_TOL = 1e-9
SCOPE = ("measured loaded-workload gates only (2 vCPU / 4 GiB reference; not cold idle, "
         "expensive-feature, capability-coverage, or release acceptance)")

# Gate failures that mean the measurement itself is not trustworthy. Capacity failures
# (completed/errors/rejected/file-roundtrip) are deliberately absent: they are outcomes.
HARNESS_FAILURES = (
    "aborted:",
    "queue-load-unmeasured",
    "queue-observation-missing",
    "active-user-count-not-20",
    "arrival-dispatch-lag-over-100ms",
    "persisted-or-reconnected-edit-missing",
    "issue-value-or-background-activity-missing",
    "queue-or-durable-work-not-recovered",
)

# Resource limits and the restored snapshot must match across conditions.
INVARIANT_PROVENANCE = ("cpu_quota", "memory_max", "memory_swap_max", "snapshot_sha256")
# A candidate may deliberately change these; they are reported, not treated as a mismatch.
DECLARED_PROVENANCE = ("baseline_worker_concurrency",)
# Harness instrumentation identity. A difference is reported, never a gate failure by itself,
# but it makes a clean single-variable claim unproven.
HARNESS_PROVENANCE = ("harness_revision", "runner_schema")

EXIT_PASS, EXIT_FAIL, EXIT_INVALID, EXIT_USAGE = 0, 1, 2, 3


class InvalidSample(Exception):
    """This sample's evidence is unusable; it is never silently skipped."""


class Incomparable(Exception):
    """The samples cannot be compared as equal work on one fixed window."""


@dataclass
class Sample:
    label: str
    runner: dict
    collector: dict
    p95: dict = field(default_factory=dict)
    cpu_seconds: float = 0.0
    cpu_s_collector_window: float = 0.0
    cpu_per_op: float = 0.0
    cpu_per_dispatched_op: float = 0.0
    load_first_round: int = -1
    load_last_round: int = -1
    load_first_t: float = 0.0
    load_last_t: float = 0.0
    peak_current_bytes: int = 0
    peak_anon_bytes: int = 0
    peak_file_bytes: int = 0
    rejected_detail: list | None = None
    read_payload_counts: dict | None = None
    files_uploaded: int = 0
    files_roundtripped: int = 0
    file_integrity_ok: bool = True
    schema_version: str = ""
    evidence_gaps: list = field(default_factory=list)
    payload_equivalence: str = "unknown"
    planned_ops: int = 0
    dispatched_ops: int = 0
    rejected_ops: int = 0
    errors: int = 0
    completed_ops: int = 0
    capacity_pass: bool = False


def _num(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidSample(f"{where}: not a number ({value!r})")
    if not math.isfinite(value):
        raise InvalidSample(f"{where}: non-finite ({value!r})")
    return float(value)


def _count(value, where):
    number = _num(value, where)
    if not number.is_integer() or number < 0:
        raise InvalidSample(f"{where}: expected a non-negative integer ({value!r})")
    return int(number)


def _need(mapping, key, where):
    if not isinstance(mapping, dict) or key not in mapping:
        raise InvalidSample(f"{where}: missing {key!r}")
    return mapping[key]


def ratio(candidate, control):
    """candidate/control with an explicit zero rule: 0/0 is no regression, x/0 is failure."""
    if control == 0:
        return 1.0 if candidate == 0 else math.inf
    return candidate / control


def p95_from(values):
    """Same nearest-rank rule as the runner (run.ts percentile at p=95)."""
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil((95 / 100) * len(ordered)) - 1)
    return float(ordered[index])


def recipe_slots(offered):
    """Arrival count implied by the declared recipe (mirrors run.ts `arrivals`)."""
    duration = _num(_need(offered, "duration_s", "offered"), "offered.duration_s")
    rate = _num(_need(offered, "steady_rate", "offered"), "offered.steady_rate")
    burst_rate = _num(_need(offered, "burst_rate", "offered"), "offered.burst_rate")
    burst_after = _num(_need(offered, "burst_after_s", "offered"), "offered.burst_after_s")
    burst_duration = _num(_need(offered, "burst_duration_s", "offered"), "offered.burst_duration_s")
    if min(duration, rate, burst_rate) <= 0 or burst_after < 0 or burst_duration < 0:
        raise InvalidSample("offered: invalid recipe")
    if burst_after + burst_duration > duration:
        raise InvalidSample("offered: burst window outside duration")
    total = 0
    for start, end, hz in (
        (0.0, burst_after, rate),
        (burst_after, burst_after + burst_duration, burst_rate),
        (burst_after + burst_duration, duration, rate),
    ):
        index = 0
        while start + index / hz < end - 1e-9:
            total += 1
            index += 1
    return total


def _epoch(value, where):
    if not isinstance(value, str) or not value.strip():
        raise InvalidSample(f"{where}: missing timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise InvalidSample(f"{where}: invalid timestamp ({value!r})") from exc


def _round_t(collector, index):
    return float(collector["rounds"][index]["t"])


def build_sample(label, runner, collector):
    if not isinstance(runner, dict) or not isinstance(collector, dict):
        raise InvalidSample(f"{label}: report is not a JSON object")
    schema_version = runner.get("schema")
    if schema_version not in CONTROL_SCHEMAS:
        raise InvalidSample(
            f"{label}: runner schema {schema_version!r} not in {CONTROL_SCHEMAS!r}"
        )
    if collector.get("schema") != COLLECTOR_SCHEMA:
        raise InvalidSample(f"{label}: collector schema {collector.get('schema')!r} != {COLLECTOR_SCHEMA!r}")

    gate = _need(runner, "gate", label)
    failures = gate.get("failures") or []
    if not isinstance(failures, list):
        raise InvalidSample(f"{label}: gate failures is not a list")
    harness_failures = [f for f in failures if any(str(f).startswith(p) for p in HARNESS_FAILURES)]
    if harness_failures:
        raise InvalidSample(f"{label}: harness-side gate failure {harness_failures!r}")

    offered = _need(runner, "offered", label)
    achieved = _need(runner, "achieved", label)
    planned = _count(_need(offered, "planned_ops", f"{label}.offered"), f"{label}.offered.planned_ops")
    rejected = _count(_need(offered, "rejected_ops", f"{label}.offered"), f"{label}.offered.rejected_ops")
    completed = _count(_need(achieved, "total_ops", f"{label}.achieved"), f"{label}.achieved.total_ops")
    errors = _count(_need(achieved, "errors", f"{label}.achieved"), f"{label}.achieved.errors")
    if planned != recipe_slots(offered):
        raise InvalidSample(f"{label}: planned_ops {planned} not implied by its own recipe "
                            f"({recipe_slots(offered)})")

    outcome = _need(runner, "outcome", label)
    durable = _need(runner, "durable", label)

    operations = _need(runner, "operations", label)
    if not isinstance(operations, list) or not operations:
        raise InvalidSample(f"{label}: empty operations")
    dispatched = len(operations)
    if completed != dispatched:
        raise InvalidSample(f"{label}: completed {completed} != dispatched operations {dispatched}")
    if planned != dispatched + rejected:
        raise InvalidSample(
            f"{label}: planned {planned} != dispatched {dispatched} + rejected {rejected}; "
            "slots are not fully accounted for"
        )

    by_kind = {}
    ok_kind = {}
    failed_kind = {}
    for op in operations:
        if not isinstance(op, dict) or "kind" not in op:
            raise InvalidSample(f"{label}: malformed operation record")
        kind = str(op["kind"])
        ms = _num(op.get("ms"), f"{label}.operations[{kind}].ms")
        if ms < 0:
            raise InvalidSample(f"{label}: negative latency for {kind!r}")
        by_kind.setdefault(kind, []).append(ms)
        if op.get("ok") is True:
            ok_kind[kind] = ok_kind.get(kind, 0) + 1
        else:
            failed_kind[kind] = failed_kind.get(kind, 0) + 1
    if sum(ok_kind.values()) + sum(failed_kind.values()) != dispatched:
        raise InvalidSample(f"{label}: operation ok flags do not account for every dispatched op")
    if sum(failed_kind.values()) != errors:
        raise InvalidSample(
            f"{label}: achieved.errors {errors} != failed operation records {sum(failed_kind.values())}"
        )

    _check_durable(label, outcome, durable, ok_kind, failed_kind)
    files_uploaded = _count(outcome["filesUploaded"], f"{label}.outcome.filesUploaded")
    files_roundtripped = _count(outcome["filesRoundTripOk"], f"{label}.outcome.filesRoundTripOk")

    reported = _need(runner, "latency", label)
    if set(reported) != set(by_kind):
        raise InvalidSample(f"{label}: latency classes {sorted(reported)} != operations classes {sorted(by_kind)}")
    p95 = {}
    for kind, values in by_kind.items():
        computed = p95_from(values)
        declared = _num(_need(reported[kind], "p95", f"{label}.latency.{kind}"), f"{label}.latency.{kind}.p95")
        if not math.isclose(computed, declared, rel_tol=1e-9, abs_tol=1e-9):
            raise InvalidSample(f"{label}: reported p95 for {kind!r} ({declared}) != recomputed ({computed})")
        p95[kind] = computed

    evidence_gaps = []
    rejected_detail = offered.get("rejected")
    if rejected_detail is not None:
        if not isinstance(rejected_detail, list):
            raise InvalidSample(f"{label}: offered.rejected is not a list")
        for entry in rejected_detail:
            if not isinstance(entry, dict) or not {"kind", "t", "reason"} <= set(entry):
                raise InvalidSample(f"{label}: offered.rejected entry lacks kind/t/reason")
    elif schema_version in EVIDENCE_SCHEMAS:
        evidence_gaps.append("offered.rejected[]")

    read_payload_counts = None
    if schema_version in EVIDENCE_SCHEMAS:
        read_items = outcome.get("readItems")
        if not isinstance(read_items, dict) or not read_items:
            evidence_gaps.append("outcome.readItems")
        else:
            for kind, total in read_items.items():
                if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                    raise InvalidSample(f"{label}: outcome.readItems[{kind!r}] is not a non-negative integer")
            sums = {}
            for op in operations:
                if "items" in op:
                    kind = str(op["kind"])
                    items = _count(op["items"], f"{label}.operations[{kind}].items")
                    sums[kind] = sums.get(kind, 0) + items
            if not sums:
                evidence_gaps.append("per-operation read items")
            read_kinds = sorted({str(op["kind"]) for op in operations
                                 if str(op["kind"]).startswith("read")})
            missing_kinds = [kind for kind in read_kinds if kind not in read_items]
            if missing_kinds:
                evidence_gaps.append(f"readItems missing kinds: {', '.join(missing_kinds)}")
            for kind, total in read_items.items():
                if sums.get(kind) != total:
                    raise InvalidSample(
                        f"{label}: outcome.readItems[{kind!r}]={total} != sum of operation items "
                        f"{sums.get(kind)}"
                    )
            read_payload_counts = read_items
    else:
        for field in ("read_payload_counts", "read_payload", "payload_items"):
            value = runner.get(field)
            if value is not None:
                if not isinstance(value, dict) or not value:
                    raise InvalidSample(f"{label}: {field} is not a non-empty dict")
                read_payload_counts = value
                break
        if read_payload_counts is None:
            evidence_gaps.append("read payload counts")

    load_started = _epoch(_need(runner, "load_started_at", label), f"{label}.load_started_at")
    load_completed = _epoch(_need(runner, "load_completed_at", label), f"{label}.load_completed_at")
    if load_completed <= load_started:
        raise InvalidSample(f"{label}: load_completed_at is not after load_started_at")
    drain = _need(runner, "queue", label).get("drain_s")
    if drain is None:
        load_end = None
    else:
        drain_s = _num(drain, f"{label}.queue.drain_s")
        if drain_s < 0:
            raise InvalidSample(f"{label}: negative queue.drain_s")
        load_end = load_completed + drain_s

    (cpu_seconds, collector_cpu, peak_current, peak_anon, peak_file,
     load_first, load_last) = _collector_metrics(label, collector, load_started, load_end)
    successful = dispatched - errors
    if successful <= 0:
        raise InvalidSample(f"{label}: no successful operations to normalize CPU by")
    capacity_pass = errors == 0 and rejected == 0 and completed == planned
    return Sample(
        label=label,
        runner=runner,
        collector=collector,
        p95=p95,
        cpu_seconds=cpu_seconds,
        cpu_s_collector_window=collector_cpu,
        cpu_per_op=cpu_seconds / successful,
        cpu_per_dispatched_op=cpu_seconds / dispatched,
        load_first_round=load_first,
        load_last_round=load_last,
        load_first_t=_round_t(collector, load_first),
        load_last_t=_round_t(collector, load_last),
        peak_current_bytes=peak_current,
        peak_anon_bytes=peak_anon,
        peak_file_bytes=peak_file,
        rejected_detail=rejected_detail,
        read_payload_counts=read_payload_counts,
        files_uploaded=files_uploaded,
        files_roundtripped=files_roundtripped,
        file_integrity_ok=files_roundtripped == files_uploaded,
        schema_version=schema_version,
        evidence_gaps=evidence_gaps,
        payload_equivalence="recorded" if not evidence_gaps else "unknown",
        planned_ops=planned,
        dispatched_ops=dispatched,
        rejected_ops=rejected,
        errors=errors,
        completed_ops=completed,
        capacity_pass=capacity_pass,
    )


def _check_durable(label, outcome, durable, ok_kind, failed_kind):
    pages = _need(durable, "pages", f"{label}.durable")
    if not isinstance(pages, dict) or not pages:
        raise InvalidSample(f"{label}: no page durability evidence")
    markers = 0
    for page_id, page in pages.items():
        expected = _need(page, "expected", f"{label}.durable.pages[{page_id}]")
        missing = _need(page, "missing", f"{label}.durable.pages[{page_id}]")
        xml = _need(page, "persisted_xml", f"{label}.durable.pages[{page_id}]")
        if not expected:
            raise InvalidSample(f"{label}: page {page_id} has no expected edits")
        if missing:
            raise InvalidSample(f"{label}: page {page_id} missing persisted edits {missing!r}")
        if len(set(expected)) != len(expected):
            raise InvalidSample(f"{label}: page {page_id} repeats an expected marker")
        if not isinstance(xml, str):
            raise InvalidSample(f"{label}: page {page_id} persisted_xml is not text")
        for marker in expected:
            if marker not in xml:
                raise InvalidSample(f"{label}: page {page_id} persisted_xml lacks {marker!r}")
        markers += len(expected)

    issues = _need(durable, "issues", f"{label}.durable")
    if not isinstance(issues, dict) or not issues:
        raise InvalidSample(f"{label}: no issue durability evidence")
    activities = 0
    for issue_id, issue in issues.items():
        expected = _need(issue, "expected", f"{label}.durable.issues[{issue_id}]")
        actual = _need(issue, "actual", f"{label}.durable.issues[{issue_id}]")
        missing = _need(issue, "missing_activities", f"{label}.durable.issues[{issue_id}]")
        expected_activities = _need(issue, "expected_activities", f"{label}.durable.issues[{issue_id}]")
        if actual != expected:
            raise InvalidSample(f"{label}: issue {issue_id} final value {actual!r} != expected {expected!r}")
        if missing:
            raise InvalidSample(f"{label}: issue {issue_id} missing activities {missing!r}")
        if len(set(expected_activities)) != len(expected_activities):
            raise InvalidSample(f"{label}: issue {issue_id} repeats an expected activity")
        activities += len(expected_activities)

    checks = (
        ("persistedMarkers", "pagesEdited"),
        ("reconnectMarkers", "persistedMarkers"),
        ("activitiesVerified", "issuesPatched"),
    )
    for left, right in checks:
        a = _count(_need(outcome, left, f"{label}.outcome"), f"{label}.outcome.{left}")
        b = _count(_need(outcome, right, f"{label}.outcome"), f"{label}.outcome.{right}")
        if a != b:
            raise InvalidSample(f"{label}: outcome {left}={a} != {right}={b}")
    for name in ("pagesEdited", "filesUploaded", "issuesPatched"):
        if _count(_need(outcome, name, f"{label}.outcome"), f"{label}.outcome.{name}") <= 0:
            raise InvalidSample(f"{label}: outcome {name} is zero")

    # File round trips may be interrupted by capacity failures; the shortfall must be
    # accounted for by failed file operations, never silent.
    uploaded = _count(outcome["filesUploaded"], f"{label}.outcome.filesUploaded")
    roundtripped = _count(_need(outcome, "filesRoundTripOk", f"{label}.outcome"),
                          f"{label}.outcome.filesRoundTripOk")
    if roundtripped > uploaded:
        raise InvalidSample(f"{label}: filesRoundTripOk {roundtripped} > filesUploaded {uploaded}")
    failed_files = failed_kind.get("file-roundtrip", 0)
    if uploaded - roundtripped > failed_files:
        raise InvalidSample(
            f"{label}: {uploaded - roundtripped} file round trips missing but only "
            f"{failed_files} failed file operations are recorded"
        )

    if markers != _count(outcome["pagesEdited"], f"{label}.outcome.pagesEdited"):
        raise InvalidSample(
            f"{label}: pagesEdited {outcome['pagesEdited']} != expected markers in durable pages ({markers})"
        )
    if activities != _count(outcome["activitiesVerified"], f"{label}.outcome.activitiesVerified"):
        raise InvalidSample(
            f"{label}: activitiesVerified {outcome['activitiesVerified']} != expected activities "
            f"in durable issues ({activities})"
        )

    # Counters must be backed by actual successful operation records, not just by each other.
    operation_backing = {
        "pagesEdited": ("edit-page", "edit-page-reconnect"),
        "issuesPatched": ("write-issue-patch",),
        "issuesCreated": ("write-issue-create",),
    }
    for counter, kinds in operation_backing.items():
        counted = _count(_need(outcome, counter, f"{label}.outcome"), f"{label}.outcome.{counter}")
        observed = sum(ok_kind.get(kind, 0) for kind in kinds)
        if counted != observed:
            raise InvalidSample(
                f"{label}: outcome {counter}={counted} != {observed} successful "
                f"{'/'.join(kinds)} operations"
            )


def _collector_metrics(label, collector, load_start, load_end):
    summary = _need(collector, "summary", label)
    if summary.get("run_valid") is not True:
        raise InvalidSample(f"{label}: collector run invalid {summary.get('invalid_reasons')!r}")
    if summary.get("invalid_reasons"):
        raise InvalidSample(f"{label}: collector invalid reasons {summary['invalid_reasons']!r}")
    if summary.get("interrupted"):
        raise InvalidSample(f"{label}: collector interrupted")
    rounds = _need(collector, "rounds", label)
    if not isinstance(rounds, list) or not rounds:
        raise InvalidSample(f"{label}: collector has no rounds")
    if _count(_need(summary, "rounds_total", label), f"{label}.summary.rounds_total") != len(rounds):
        raise InvalidSample(f"{label}: rounds_total != collected rounds")
    if _count(_need(summary, "rounds_valid", label), f"{label}.summary.rounds_valid") != len(rounds):
        raise InvalidSample(f"{label}: not every collector round is valid")

    containers = _need(collector, "containers", label)
    if not isinstance(containers, list) or not containers:
        raise InvalidSample(f"{label}: collector lists no containers")
    names = sorted(str(_need(c, "name", f"{label}.containers")) for c in containers)
    if len(set(names)) != len(names):
        raise InvalidSample(f"{label}: duplicate container names")

    interval = _num(_need(collector, "interval_s", label), f"{label}.interval_s")
    duration = _num(_need(collector, "duration_s", label), f"{label}.duration_s")
    if interval <= 0 or duration <= 0:
        raise InvalidSample(f"{label}: collector window is not positive")
    stamps = [_num(_need(r, "t", f"{label}.rounds"), f"{label}.rounds.t") for r in rounds]
    for earlier, later in zip(stamps, stamps[1:]):
        if later <= earlier:
            raise InvalidSample(f"{label}: round timestamps are not strictly increasing")
    span = stamps[-1] - stamps[0]
    if span < duration - 1.5 * interval or span > duration + 0.5 * interval:
        raise InvalidSample(
            f"{label}: round timestamps span {span:.3f}s, which cannot cover the declared {duration}s window"
        )
    gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
    median_gap = statistics.median(gaps) if gaps else interval
    if abs(median_gap - interval) > interval * 0.25:
        raise InvalidSample(f"{label}: median round cadence {median_gap:.3f}s is not {interval}s")
    if max(gaps, default=0) > interval * 3:
        raise InvalidSample(
            f"{label}: a {max(gaps):.1f}s sampling gap exceeds three cadences; window has a hole"
        )
    if abs(len(rounds) - (span / interval + 1)) > 1:
        raise InvalidSample(
            f"{label}: {len(rounds)} rounds are inconsistent with a {span:.1f}s span at {interval}s"
        )

    load_indices = [
        index for index, stamp in enumerate(stamps)
        if stamp >= load_start and (load_end is None or stamp <= load_end)
    ]
    if len(load_indices) < 2:
        raise InvalidSample(
            f"{label}: load window has {len(load_indices)} collector rounds; need at least two"
        )
    load_first, load_last = load_indices[0], load_indices[-1]

    peak_round = None
    peak_current = 0
    for round_ in rounds:
        if round_.get("valid") is not True or round_.get("missing"):
            raise InvalidSample(f"{label}: collector round {round_.get('i')} invalid")
        samples = _need(round_, "samples", f"{label}.rounds")
        if sorted(samples) != names:
            raise InvalidSample(f"{label}: round {round_.get('i')} container set differs from inventory")
        total = 0.0
        anon_total = 0.0
        file_total = 0.0
        for name, sample in samples.items():
            total += _num(_need(sample, "memory_current_bytes", f"{label}.{name}"),
                          f"{label}.{name}.memory_current_bytes")
            anon_total += _num(_need(sample, "anon_bytes", f"{label}.{name}"), f"{label}.{name}.anon_bytes")
            file_total += _num(_need(sample, "file_bytes", f"{label}.{name}"), f"{label}.{name}.file_bytes")
            _num(_need(sample, "cpu_usage_usec", f"{label}.{name}"), f"{label}.{name}.cpu_usage_usec")
            swap = sample.get("swap_current_bytes")
            if isinstance(swap, (int, float)) and not isinstance(swap, bool) and swap > 0:
                raise InvalidSample(f"{label}: {name} used swap ({swap} bytes)")
        declared = _num(_need(round_, "round_sum_bytes", f"{label}.rounds"), f"{label}.rounds.round_sum_bytes")
        if abs(total - declared) > 1e-6:
            raise InvalidSample(
                f"{label}: round {round_.get('i')} sum {declared} != sum of container memory ({total})"
            )
        declared_anon = _num(_need(round_, "anon_sum_bytes", f"{label}.rounds"),
                             f"{label}.rounds.anon_sum_bytes")
        if abs(anon_total - declared_anon) > 1e-6:
            raise InvalidSample(
                f"{label}: round {round_.get('i')} anon sum {declared_anon} != sum of container anon ({anon_total})"
            )
        declared_file = _num(_need(round_, "file_sum_bytes", f"{label}.rounds"),
                             f"{label}.rounds.file_sum_bytes")
        if abs(file_total - declared_file) > 1e-6:
            raise InvalidSample(
                f"{label}: round {round_.get('i')} file sum {declared_file} != sum of container file ({file_total})"
            )
        if declared > peak_current:
            peak_current = int(declared)
            peak_round = round_

    reported_peak = _count(_need(summary, "observed_peak_bytes", label), f"{label}.summary.observed_peak_bytes")
    if reported_peak != peak_current:
        raise InvalidSample(f"{label}: summary peak {reported_peak} != recomputed {peak_current}")
    if peak_round is None:
        raise InvalidSample(f"{label}: no peak round")
    peak_anon = int(sum(_num(s["anon_bytes"], f"{label}.anon") for s in peak_round["samples"].values()))
    peak_file = int(sum(_num(s["file_bytes"], f"{label}.file") for s in peak_round["samples"].values()))

    # Whole-stack CPU from raw cumulative counters: the gated value is the load window,
    # the collector-window total is reported as context.
    def window_cpu(first_index, last_index):
        total = 0.0
        for name in names:
            first = _num(rounds[first_index]["samples"][name].get("cpu_usage_usec"),
                         f"{label}.{name}.cpu_usage_usec")
            last = _num(rounds[last_index]["samples"][name].get("cpu_usage_usec"),
                        f"{label}.{name}.cpu_usage_usec")
            if last < first:
                raise InvalidSample(f"{label}: {name} CPU counter went backwards")
            total += last - first
        return total

    load_usec = window_cpu(load_first, load_last)
    collector_usec = window_cpu(0, len(rounds) - 1)
    if load_usec <= 0:
        raise InvalidSample(f"{label}: load-window CPU is not positive")
    if collector_usec <= 0:
        raise InvalidSample(f"{label}: collector-window CPU is not positive")
    return (load_usec / 1e6, collector_usec / 1e6, peak_current, peak_anon, peak_file,
            rounds[load_first]["i"], rounds[load_last]["i"])


def _check_shared(samples):
    """Invariants that must hold across every sample, both conditions.

    `collector.host` is only the cgroup2 mount point, not a host identity, so it is not
    compared here; host identity is handled as metadata and stays unknown unless provenance
    records it.
    """
    first = samples[0]
    recipe_keys = (
        "steady_rate", "burst_rate", "burst_after_s", "burst_duration_s",
        "duration_s", "recovery_s", "users", "editors", "planned_ops",
    )
    all_capacity_pass = all(sample.capacity_pass for sample in samples)
    for sample in samples[1:]:
        for key in recipe_keys:
            a = _num(_need(first.runner["offered"], key, "recipe"), f"offered.{key}")
            b = _num(_need(sample.runner["offered"], key, "recipe"), f"offered.{key}")
            if a != b:
                raise Incomparable(f"{first.label} vs {sample.label}: offered.{key} differs ({a} vs {b})")
        if set(sample.p95) != set(first.p95):
            raise Incomparable(f"{first.label} vs {sample.label}: operation kinds differ")
        if all_capacity_pass:
            for kind in first.p95:
                n_a = sum(1 for op in first.runner["operations"] if op.get("kind") == kind)
                n_b = sum(1 for op in sample.runner["operations"] if op.get("kind") == kind)
                if n_a != n_b:
                    raise Incomparable(f"{first.label} vs {sample.label}: {kind} count differs ({n_a} vs {n_b})")
            if first.read_payload_counts is not None and sample.read_payload_counts is not None:
                common = set(first.read_payload_counts) & set(sample.read_payload_counts)
                differing = {
                    kind: (first.read_payload_counts[kind], sample.read_payload_counts[kind])
                    for kind in common
                    if first.read_payload_counts[kind] != sample.read_payload_counts[kind]
                }
                if differing:
                    raise Incomparable(
                        f"{first.label} vs {sample.label}: read payload counts differ ({differing})"
                    )
        for key in ("interval_s", "duration_s", "access_mode"):
            a = first.collector.get(key)
            b = sample.collector.get(key)
            if a != b:
                raise Incomparable(f"{first.label} vs {sample.label}: collector {key} differs ({a} vs {b})")
        if first.collector.get("instrument") != sample.collector.get("instrument"):
            raise Incomparable(f"{first.label} vs {sample.label}: collector instrument differs")
        if sorted(c["name"] for c in first.collector["containers"]) != \
                sorted(c["name"] for c in sample.collector["containers"]):
            raise Incomparable(f"{first.label} vs {sample.label}: container inventory differs")


def _check_images_within(group):
    """Images must be stable inside one condition; a different condition may use new images."""
    base = _image_map(group[0].collector)
    for sample in group[1:]:
        if _image_map(sample.collector) != base:
            raise Incomparable(
                f"{group[0].label} vs {sample.label}: image map changed within one condition"
            )


def _image_map(collector):
    """Per-container image identity. `image_id` is mandatory: a tag alone cannot establish
    immutability. `image` is compared too when present, but never substitutes for the id.
    """
    fingerprints = {}
    for container in collector["containers"]:
        name = container["name"]
        image = container.get("image")
        image_id = container.get("image_id")
        image = image.strip() if isinstance(image, str) and image.strip() else None
        if not isinstance(image_id, str) or not image_id.strip():
            raise InvalidSample(
                f"container {name}: image_id missing; a tag alone cannot establish image identity"
            )
        fingerprints[name] = (image, image_id.strip())
    return fingerprints


def _harness_identity(prov, labels, schemas):
    """Per-run harness fields keyed by runner label, else the top-level value.

    `runner_schema` falls back to the runner artifact itself (authoritative) when provenance
    does not carry it. Returns (values, evidence, sources) where evidence is "per-run",
    "top-level fallback (weaker)" or "unknown".
    """
    runs = prov.get("runs") if isinstance(prov.get("runs"), list) else []
    by_label = {run.get("label"): run for run in runs if isinstance(run, dict)}
    values = {}
    sources = {}
    for field in HARNESS_PROVENANCE:
        found = []
        source = "per-run"
        for index, label in enumerate(labels):
            run = by_label.get(label) or {}
            value = run.get(field)
            if value is None:
                value = prov.get(field)
                if value is not None and source == "per-run":
                    source = "top-level fallback (weaker)"
            if value is None and field == "runner_schema" and index < len(schemas):
                value = schemas[index]
                if source == "per-run":
                    source = "runner artifact"
            if isinstance(value, str) and value.strip():
                found.append(value.strip())
        if not found:
            values[field] = []
            sources[field] = "unknown"
        else:
            values[field] = sorted(set(found))
            sources[field] = source
    if "unknown" in sources.values():
        evidence = "unknown"
    elif all(source == "per-run" for source in sources.values()):
        evidence = "per-run"
    else:
        evidence = "top-level fallback (weaker)"
    return values, evidence, sources


def _metadata(control_prov, candidate_prov, image_changed, control_labels, candidate_labels,
              control_schemas, candidate_schemas):
    """Invariant limits/snapshot, host identity, and the single changed variable.

    Unknown or unequal metadata blocks PASS but never turns a demonstrated gate failure into
    inconclusive. `collector.host` is only a cgroup mount point, so host identity is unknown
    unless provenance records it.
    """
    block = {
        "status": "unknown",
        "reason": None,
        "host_identity": "unknown",
        "changed_variables": [],
        "declared_unknown": [],
    }
    if control_prov is None or candidate_prov is None:
        block["reason"] = "provenance file absent for one or both conditions"
        return block
    missing = [key for key in INVARIANT_PROVENANCE if key not in control_prov or key not in candidate_prov]
    if missing:
        block["reason"] = f"provenance lacks {missing}"
        return block
    differing = [key for key in INVARIANT_PROVENANCE if control_prov[key] != candidate_prov[key]]
    if differing:
        block["reason"] = f"conditions differ in {differing}"
        return block

    # Observed differences are facts; their causal count is not. An image rebuild and a
    # reported configuration value can be one change, so they are never added up into a
    # "multiple variables" rejection. The ledger judges causality.
    observed = [
        key for key in DECLARED_PROVENANCE
        if key in control_prov and key in candidate_prov and control_prov[key] != candidate_prov[key]
    ]
    if image_changed:
        observed.append("image_set")

    control_harness, control_harness_evidence, control_sources = _harness_identity(
        control_prov, control_labels, control_schemas)
    candidate_harness, candidate_harness_evidence, candidate_sources = _harness_identity(
        candidate_prov, candidate_labels, candidate_schemas)
    harness_changed = control_harness != candidate_harness
    for field in HARNESS_PROVENANCE:
        if control_harness[field] != candidate_harness[field]:
            observed.append(field)
    block["harness"] = {
        "control": control_harness,
        "candidate": candidate_harness,
        "evidence": (
            "unknown" if "unknown" in (control_harness_evidence, candidate_harness_evidence)
            else "per-run" if "per-run" in (control_harness_evidence, candidate_harness_evidence)
            else "top-level fallback (weaker)"
        ),
        "changed": harness_changed,
        "sources": {"control": control_sources, "candidate": candidate_sources},
    }
    if block["harness"]["evidence"] == "unknown":
        block["reason"] = "harness identity not recorded (harness_revision/runner_schema)"
        return block

    block["changed_variables"] = observed
    if harness_changed:
        # The instrumentation change is reported, never a gate failure; it does make a
        # single-variable claim unproven.
        block["single_variable"] = "unknown"
        block["reason"] = (
            f"single changed variable not established; harness differs and observed differences: {observed}"
        )
        return block
    block["single_variable"] = "consistent" if len(observed) <= 1 else "unknown"
    if len(observed) > 1:
        # Not a claim that these are independent variables; the single-variable claim is
        # simply unproven, so PASS is blocked while the measured gates stay in the report.
        block["reason"] = (
            f"single changed variable not established; observed differences: {observed}"
        )
        return block

    # A declared (non-invariant) setting absent on one side is unknown, not a mismatch.
    block["declared_unknown"] = [
        key for key in DECLARED_PROVENANCE
        if (key in control_prov) != (key in candidate_prov)
    ]
    if block["declared_unknown"]:
        block["reason"] = f"declared setting presence unknown: {block['declared_unknown']}"
        return block

    host_keys = ("host", "hostname")
    control_host = next(
        (control_prov[key] for key in host_keys
         if isinstance(control_prov.get(key), str) and control_prov[key].strip()), None)
    candidate_host = next(
        (candidate_prov[key] for key in host_keys
         if isinstance(candidate_prov.get(key), str) and candidate_prov[key].strip()), None)
    control_token = control_prov.get("host_token")
    candidate_token = candidate_prov.get("host_token")
    control_token = control_token.strip() if isinstance(control_token, str) and control_token.strip() else None
    candidate_token = (
        candidate_token.strip() if isinstance(candidate_token, str) and candidate_token.strip() else None
    )

    if control_host and candidate_host:
        if control_host != candidate_host:
            block["reason"] = f"host identity differs ({control_host} vs {candidate_host})"
            return block
        block["host_identity"] = "recorded"
    elif control_token and candidate_token:
        if control_token != candidate_token:
            block["reason"] = f"host token differs ({control_token} vs {candidate_token})"
            return block
        # Experiment-local identity only: a token copied to another machine would match.
        block["host_identity"] = "experiment_token"
        block["host_provenance"] = (
            "experiment-local token; it does not prove the same physical host"
        )
    else:
        block["reason"] = "host identity not recorded in provenance (host/hostname or host_token)"
        return block

    control_facts = control_prov.get("host_facts")
    candidate_facts = candidate_prov.get("host_facts")
    if isinstance(control_facts, dict) and isinstance(candidate_facts, dict):
        fact_keys = ("kernel", "cpu_count")
        differing_facts = [
            key for key in fact_keys
            if key in control_facts and key in candidate_facts
            and control_facts[key] != candidate_facts[key]
        ]
        if differing_facts:
            block["reason"] = f"host facts differ: {differing_facts}"
            return block
        block["host_facts"] = "compatible"

    # Source condition and execution records are required evidence, not identity proof.
    control_source = control_prov.get("api_source_sha256")
    candidate_source = candidate_prov.get("api_source_sha256")
    if not isinstance(control_source, dict) or not control_source:
        block["reason"] = "source condition not recorded (api_source_sha256)"
        return block
    if not isinstance(candidate_source, dict) or not candidate_source:
        block["reason"] = "source condition not recorded (api_source_sha256)"
        return block
    block["source_condition"] = {
        "control": sorted(set(control_source.values())),
        "candidate": sorted(set(candidate_source.values())),
        "changed": set(control_source.values()) != set(candidate_source.values()),
    }
    for role, prov in (("control", control_prov), ("candidate", candidate_prov)):
        runs = prov.get("runs")
        # runner_exit 2 is the runner's documented "gate failed" exit, which is a capacity
        # outcome; 1 means setup/seed failure and is not a successful execution record.
        recorded = isinstance(runs, list) and any(
            isinstance(run, dict) and run.get("runner_exit") in (0, 2) and run.get("collector_exit") == 0
            for run in runs
        )
        if not recorded:
            block["reason"] = f"{role}: no successful execution record (runner_exit/collector_exit)"
            return block
    block["execution"] = "recorded"

    block["status"] = "equal"
    # Snapshot equality is the only restore-identity claim the artifacts support; dataset ids
    # are deliberately not treated as proof of equal restored state.
    block["snapshot"] = "hashes equal across conditions"
    return block


def _capacity_block(control, candidate):
    def side(samples):
        return {
            "all_pass": all(s.capacity_pass for s in samples),
            "samples": [
                {
                    "label": s.label,
                    "pass": s.capacity_pass,
                    "planned": s.planned_ops,
                    "dispatched": s.dispatched_ops,
                    "rejected": s.rejected_ops,
                    "errors": s.errors,
                    "completed": s.completed_ops,
                }
                for s in samples
            ],
        }

    control_block = side(control)
    candidate_block = side(candidate)
    control_completed = statistics.median(s.completed_ops for s in control)
    candidate_completed = statistics.median(s.completed_ops for s in candidate)
    if candidate_completed > control_completed:
        work = "candidate completed more work than control"
    elif candidate_completed < control_completed:
        work = "candidate completed less work than control"
    else:
        work = "equal completed work"
    return {
        "control": control_block,
        "candidate": candidate_block,
        "work_comparison": work,
        "control_completed_median": control_completed,
        "candidate_completed_median": candidate_completed,
    }


def compare(control, candidate, control_prov=None, candidate_prov=None):
    """Return (verdict, report). `control` and `candidate` are Sample lists."""
    if len(control) < MIN_SAMPLES or len(candidate) < MIN_SAMPLES:
        return "inconclusive", {
            "scope": SCOPE,
            "reason": f"need >= {MIN_SAMPLES} samples per side "
                      f"(control {len(control)}, candidate {len(candidate)})",
        }
    try:
        _check_shared(control + candidate)
        _check_images_within(control)
        _check_images_within(candidate)
    except Incomparable as exc:
        return "inconclusive", {"scope": SCOPE, "reason": str(exc)}

    image_changed = _image_map(control[0].collector) != _image_map(candidate[0].collector)
    control_schemas = sorted({s.schema_version for s in control})
    candidate_schemas = sorted({s.schema_version for s in candidate})
    metadata = _metadata(
        control_prov,
        candidate_prov,
        image_changed,
        [s.runner.get("label") for s in control],
        [s.runner.get("label") for s in candidate],
        [s.schema_version for s in control],
        [s.schema_version for s in candidate],
    )
    capacity = _capacity_block(control, candidate)
    kinds = sorted(control[0].p95)
    control_p95 = {kind: statistics.median(s.p95[kind] for s in control) for kind in kinds}
    control_cpu = statistics.median(s.cpu_per_op for s in control)
    control_current = statistics.median(s.peak_current_bytes for s in control)
    control_anon = statistics.median(s.peak_anon_bytes for s in control)
    control_file = statistics.median(s.peak_file_bytes for s in control)

    p95_gates = {}
    for kind in kinds:
        ratios = [ratio(s.p95[kind], control_p95[kind]) for s in candidate]
        p95_gates[kind] = {
            "limit": P95_CEILING,
            "control_median_ms": control_p95[kind],
            "candidate_ms": [s.p95[kind] for s in candidate],
            "ratios": ratios,
            "worst_ratio": max(ratios),
            "pass": max(ratios) <= P95_CEILING * (1 + REL_TOL),
        }
    cpu_ratios = [ratio(s.cpu_per_op, control_cpu) for s in candidate]
    cpu_gate = {
        "limit": CPU_CEILING,
        "control_median_s_per_op": control_cpu,
        "candidate_s_per_op": [s.cpu_per_op for s in candidate],
        "ratios": cpu_ratios,
        "worst_ratio": max(cpu_ratios),
        "pass": max(cpu_ratios) <= CPU_CEILING * (1 + REL_TOL),
    }
    current_ratios = [ratio(s.peak_current_bytes, control_current) for s in candidate]
    anon_ratios = [ratio(s.peak_anon_bytes, control_anon) for s in candidate]
    file_ratios = [ratio(s.peak_file_bytes, control_file) for s in candidate]
    worst_index = max(range(len(candidate)), key=lambda index: current_ratios[index])
    worst_current = current_ratios[worst_index]
    worst_anon = anon_ratios[worst_index]
    ram_pass = worst_current <= RAM_CEILING * (1 + REL_TOL)
    corroborated = worst_anon <= worst_current * (1 + REL_TOL)
    ram_gate = {
        "metric": "peak simultaneous memory.current (round-sum rule)",
        "limit": RAM_CEILING,
        "control_median_bytes": control_current,
        "candidate_bytes": [s.peak_current_bytes for s in candidate],
        "ratios": current_ratios,
        "worst_ratio": worst_current,
        "pass": ram_pass,
        "corroboration": {
            "rule": "the worst current-ratio sample must also reduce anon at least as much "
                    "(anon_ratio <= current_ratio); otherwise the saving is not corroborated",
            "worst_sample": candidate[worst_index].label,
            "anon_ratio": worst_anon,
            "corroborated": corroborated,
        },
        "context_anon": {
            "control_median_bytes": control_anon,
            "candidate_bytes": [s.peak_anon_bytes for s in candidate],
            "ratios": anon_ratios,
        },
        "context_file": {
            "control_median_bytes": control_file,
            "candidate_bytes": [s.peak_file_bytes for s in candidate],
            "ratios": file_ratios,
        },
    }

    gates_pass = all(g["pass"] for g in p95_gates.values()) and cpu_gate["pass"] and ram_gate["pass"]
    control_payload_gap = any(s.schema_version not in EVIDENCE_SCHEMAS for s in control)
    candidate_payload_gap = any(s.schema_version not in EVIDENCE_SCHEMAS for s in candidate)
    control_gaps = sorted({gap for s in control for gap in s.evidence_gaps})
    candidate_gaps = sorted({gap for s in candidate for gap in s.evidence_gaps})
    payload_equivalence = {
        "control_schema": control_schemas,
        "candidate_schema": candidate_schemas,
        "control_payload_equivalence": (
            "unknown" if control_payload_gap or control_gaps else "recorded"
        ),
        "candidate_payload_equivalence": (
            "unknown" if candidate_payload_gap or candidate_gaps else "recorded"
        ),
        "control_evidence_gaps": control_gaps,
        "candidate_evidence_gaps": candidate_gaps,
        "note": "read payload equivalence needs item counts on both sides; a p0-workload/3 "
                "control cannot support a read-side candidate claim",
    }
    report = {
        "scope": SCOPE,
        "aggregation": "control = median of per-sample values; gate = candidate worst sample "
                       "against that median",
        "metadata": metadata,
        "capacity": capacity,
        "payload_equivalence": payload_equivalence,
        "samples": {
            "control": [
                {"label": s.label, "schema": s.schema_version,
                 "payload_equivalence": s.payload_equivalence,
                 "capacity_pass": s.capacity_pass, "planned": s.planned_ops,
                 "dispatched": s.dispatched_ops, "rejected": s.rejected_ops, "errors": s.errors,
                 "completed": s.completed_ops, "cpu_s": s.cpu_seconds,
                 "cpu_s_collector_window": s.cpu_s_collector_window,
                 "load_window": {"first_round": s.load_first_round,
                                 "last_round": s.load_last_round,
                                 "first_t": s.load_first_t,
                                 "last_t": s.load_last_t},
                 "cpu_s_per_op": s.cpu_per_op,
                 "cpu_s_per_dispatched_op": s.cpu_per_dispatched_op,
                 "peak_anon_bytes": s.peak_anon_bytes, "peak_current_bytes": s.peak_current_bytes,
                 "file_integrity": {"uploaded": s.files_uploaded,
                                    "roundtripped": s.files_roundtripped,
                                    "ok": s.file_integrity_ok},
                 "p95": s.p95}
                for s in control
            ],
            "candidate": [
                {"label": s.label, "schema": s.schema_version,
                 "payload_equivalence": s.payload_equivalence,
                 "capacity_pass": s.capacity_pass, "planned": s.planned_ops,
                 "dispatched": s.dispatched_ops, "rejected": s.rejected_ops, "errors": s.errors,
                 "completed": s.completed_ops, "cpu_s": s.cpu_seconds,
                 "cpu_s_collector_window": s.cpu_s_collector_window,
                 "load_window": {"first_round": s.load_first_round,
                                 "last_round": s.load_last_round,
                                 "first_t": s.load_first_t,
                                 "last_t": s.load_last_t},
                 "cpu_s_per_op": s.cpu_per_op,
                 "cpu_s_per_dispatched_op": s.cpu_per_dispatched_op,
                 "peak_anon_bytes": s.peak_anon_bytes, "peak_current_bytes": s.peak_current_bytes,
                 "file_integrity": {"uploaded": s.files_uploaded,
                                    "roundtripped": s.files_roundtripped,
                                    "ok": s.file_integrity_ok},
                 "p95": s.p95}
                for s in candidate
            ],
        },
        "gates": {"p95": p95_gates, "cpu": cpu_gate, "ram": ram_gate},
    }
    if not capacity["candidate"]["all_pass"]:
        report["reason"] = (
            "candidate capacity: "
            + "; ".join(
                f"{s['label']} completed {s['completed']}/{s['planned']}, "
                f"{s['errors']} errors, {s['rejected']} rejected"
                for s in capacity["candidate"]["samples"] if not s["pass"]
            )
        )
        return "fail", report
    if not gates_pass:
        return "fail", report
    if control_payload_gap:
        report["reason"] = (
            "control-side read payload counts not recorded (schema "
            f"{'/'.join(control_schemas)}); payload equivalence unknown"
        )
        return "inconclusive", report
    if control_gaps:
        report["reason"] = f"control evidence unknown: {', '.join(control_gaps)}"
        return "inconclusive", report
    if candidate_payload_gap:
        report["reason"] = (
            "candidate-side read payload counts not recorded (schema "
            f"{'/'.join(candidate_schemas)}); payload equivalence unknown"
        )
        return "inconclusive", report
    if candidate_gaps:
        report["reason"] = f"candidate evidence unknown: {', '.join(candidate_gaps)}"
        return "inconclusive", report
    if not all(s.file_integrity_ok for s in candidate):
        report["reason"] = (
            "file integrity: "
            + "; ".join(
                f"{s.label} round-tripped {s.files_roundtripped}/{s.files_uploaded}"
                for s in candidate if not s.file_integrity_ok
            )
        )
        return "inconclusive", report
    if not ram_gate["corroboration"]["corroborated"]:
        report["reason"] = (
            "memory saving not corroborated by anon: worst current ratio "
            f"{worst_current:.4f} but anon ratio {worst_anon:.4f}"
        )
        return "inconclusive", report
    if metadata["status"] != "equal":
        report["reason"] = f"metadata: {metadata['reason']}"
        return "inconclusive", report
    return "pass", report


def _print_human(verdict, report):
    print(f"verdict: {verdict}")
    print(f"scope: {report.get('scope')}")
    if "samples" not in report:
        print(f"reason: {report['reason']}")
        return
    print(f"aggregation: {report['aggregation']}")
    meta = report["metadata"]
    print(f"metadata: {meta['status']}" + (f" ({meta['reason']})" if meta.get("reason") else ""))
    print(f"host identity: {meta.get('host_identity', 'unknown')}")
    if meta.get("changed_variables"):
        print(f"observed differences: {meta['changed_variables']} "
              f"(single variable: {meta.get('single_variable', 'unknown')})")
    if meta.get("declared_unknown"):
        print(f"declared unknown: {meta['declared_unknown']}")
    capacity = report["capacity"]
    print(f"capacity: control all_pass={capacity['control']['all_pass']} "
          f"candidate all_pass={capacity['candidate']['all_pass']} "
          f"({capacity['work_comparison']})")
    for side in ("control", "candidate"):
        for sample in report["samples"][side]:
            print(f"  {side:9s} {sample['label']}: schema={sample['schema']} "
                  f"payload={sample['payload_equivalence']} "
                  f"capacity={'PASS' if sample['capacity_pass'] else 'FAIL'} "
                  f"completed={sample['completed']}/{sample['planned']} "
                  f"errors={sample['errors']} rejected={sample['rejected']} "
                  f"anon={sample['peak_anon_bytes']} current={sample['peak_current_bytes']} "
                  f"files={sample['file_integrity']['roundtripped']}/{sample['file_integrity']['uploaded']} "
                  f"cpu_load={sample['cpu_s']:.3f}s ({sample['cpu_s_per_op']:.6f}s/successful-op, "
                  f"{sample['cpu_s_per_dispatched_op']:.6f}s/dispatched-op) "
                  f"cpu_window={sample['cpu_s_collector_window']:.3f}s")
    gates = report["gates"]
    for kind, gate in gates["p95"].items():
        print(f"  p95 {kind:24s} worst={gate['worst_ratio']:.4f} limit={gate['limit']:.2f} "
              f"{'PASS' if gate['pass'] else 'FAIL'}")
    print(f"  cpu/op                    worst={gates['cpu']['worst_ratio']:.4f} "
          f"limit={gates['cpu']['limit']:.2f} {'PASS' if gates['cpu']['pass'] else 'FAIL'}")
    print(f"  ram current peak          worst={gates['ram']['worst_ratio']:.4f} "
          f"limit={gates['ram']['limit']:.2f} {'PASS' if gates['ram']['pass'] else 'FAIL'}")
    corr = gates["ram"]["corroboration"]
    print(f"  ram anon corroboration    anon_ratio={corr['anon_ratio']:.4f} "
          f"({'corroborated' if corr['corroborated'] else 'NOT corroborated'})")
    anon_ctx = gates["ram"]["context_anon"]
    file_ctx = gates["ram"]["context_file"]
    print(f"  ram anon (context)        worst={max(anon_ctx['ratios']):.4f} "
          f"control_median={anon_ctx['control_median_bytes']}")
    print(f"  ram file (context)        worst={max(file_ctx['ratios']):.4f} "
          f"control_median={file_ctx['control_median_bytes']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--control", nargs=2, action="append", metavar=("RUNNER", "COLLECTOR"), required=True)
    parser.add_argument("--candidate", nargs=2, action="append", metavar=("RUNNER", "COLLECTOR"))
    parser.add_argument("--control-only", action="store_true",
                        help="report control-side eligibility only; no candidate comparison")
    parser.add_argument("--control-provenance", metavar="PATH")
    parser.add_argument("--candidate-provenance", metavar="PATH")
    parser.add_argument("--json", metavar="PATH", help="write the full report here")
    args = parser.parse_args(argv)

    def load(path):
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    if args.control_only and args.candidate:
        print("error: --control-only cannot be combined with --candidate", file=sys.stderr)
        return EXIT_USAGE
    if not args.control_only and not args.candidate:
        print("error: --candidate is required unless --control-only is set", file=sys.stderr)
        return EXIT_USAGE

    try:
        control = [build_sample(f"control-{i + 1}", load(runner), load(collector))
                   for i, (runner, collector) in enumerate(args.control)]
        candidate = [build_sample(f"candidate-{i + 1}", load(runner), load(collector))
                     for i, (runner, collector) in enumerate(args.candidate or [])]
        control_prov = load(args.control_provenance) if args.control_provenance else None
        candidate_prov = load(args.candidate_provenance) if args.candidate_provenance else None
    except InvalidSample as exc:
        report = {"scope": SCOPE, "verdict": "invalid", "reason": str(exc)}
        _print_human("invalid", report)
        if args.json:
            _write_json(args.json, report)
        return EXIT_INVALID
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.control_only:
        report = {
            "scope": SCOPE,
            "mode": "control-only",
            "samples": [
                {
                    "label": s.label,
                    "schema": s.schema_version,
                    "payload_equivalence": s.payload_equivalence,
                    "evidence_gaps": s.evidence_gaps,
                    "capacity_pass": s.capacity_pass,
                    "planned": s.planned_ops,
                    "dispatched": s.dispatched_ops,
                    "rejected": s.rejected_ops,
                    "errors": s.errors,
                    "completed": s.completed_ops,
                    "peak_anon_bytes": s.peak_anon_bytes,
                    "peak_current_bytes": s.peak_current_bytes,
                    "file_integrity": {"uploaded": s.files_uploaded,
                                       "roundtripped": s.files_roundtripped,
                                       "ok": s.file_integrity_ok},
                }
                for s in control
            ],
        }
        print("verdict: control-only")
        print(f"scope: {SCOPE}")
        for sample in report["samples"]:
            print(f"  {sample['label']}: schema={sample['schema']} "
                  f"payload={sample['payload_equivalence']} "
                  f"capacity={'PASS' if sample['capacity_pass'] else 'FAIL'} "
                  f"completed={sample['completed']}/{sample['planned']} "
                  f"errors={sample['errors']} rejected={sample['rejected']} "
                  f"files={sample['file_integrity']['roundtripped']}/{sample['file_integrity']['uploaded']}")
            if sample["evidence_gaps"]:
                print(f"    evidence gaps: {sample['evidence_gaps']}")
        report["verdict"] = "control-only"
        if args.json:
            _write_json(args.json, report)
        return EXIT_PASS

    try:
        verdict, report = compare(control, candidate, control_prov, candidate_prov)
    except InvalidSample as exc:
        report = {"scope": SCOPE, "verdict": "invalid", "reason": str(exc)}
        _print_human("invalid", report)
        if args.json:
            _write_json(args.json, report)
        return EXIT_INVALID

    report["verdict"] = verdict
    _print_human(verdict, report)
    if args.json:
        _write_json(args.json, report)
    if verdict == "pass":
        return EXIT_PASS
    if verdict == "fail":
        return EXIT_FAIL
    return EXIT_INVALID


def _write_json(path, report):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    sys.exit(main())