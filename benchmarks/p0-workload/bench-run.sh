#!/usr/bin/env bash
# Run the fixed-rate workload. Run from benchmarks/p0-workload/.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bun "$HERE/run.ts" "$@"
