#!/usr/bin/env bash
# Teardown of the isolated bench stack ONLY. Run from benchmarks/p0-workload/.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
docker compose -p plane-bench \
  -f "$HERE/../../docker-compose.yml" \
  -f "$HERE/deploy/docker-compose.bench.yml" \
  --env-file "$HERE/deploy/plane-bench.env" \
  down -v --remove-orphans
rm -f "$HERE/seed/user.json" "$HERE/seed/dataset.json"
echo "plane-bench stack and its volumes removed"
