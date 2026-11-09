#!/usr/bin/env bash
set -euo pipefail
for _ in $(seq 1 90); do
  if curl -fsS --max-time 5 -o /dev/null http://127.0.0.1:8080/auth/get-csrf-token/; then
    exit 0
  fi
  sleep 2
done
echo 'Reference API readiness timeout' >&2
exit 1
