import assert from "node:assert/strict";
import { test } from "node:test";
import { arrivals, missingMarkers, workloadFailures } from "./contract";

test("burst boundary has exactly the declared arrivals without dropped slots", () => {
  const slots = arrivals({ duration: 300, rate: 6, burstAfter: 60, burstRate: 20, burstDuration: 30 });
  assert.equal(slots.length, 2220);
  assert.equal(slots.filter((t) => t < 60).length, 360);
  assert.equal(slots.filter((t) => t >= 60 && t < 90).length, 600);
  assert.equal(slots.filter((t) => t >= 90).length, 1260);
  assert.equal(new Set(slots).size, slots.length);
});

test("non-empty persisted state cannot stand in for missing edits", () => {
  assert.deepEqual(
    missingMarkers("<paragraph>seeded content marker-1-end</paragraph>", ["marker-1-end", "marker-2-end"]),
    ["marker-2-end"]
  );
});

test("skipped arrivals and any operation failure invalidate equal-work claims", () => {
  assert.deepEqual(workloadFailures(100, 99, 0, 1), ["completed:99/100", "rejected-arrivals:1"]);
  assert.deepEqual(workloadFailures(100, 100, 1, 0), ["operation-errors:1"]);
  assert.deepEqual(workloadFailures(100, 100, 0, 0), []);
});

test("invalid rates and out-of-window bursts are rejected", () => {
  assert.throws(() => arrivals({ duration: 30, rate: 0, burstAfter: 10, burstRate: 20, burstDuration: 10 }));
  assert.throws(() => arrivals({ duration: 30, rate: 6, burstAfter: 20, burstRate: 20, burstDuration: 20 }));
});
