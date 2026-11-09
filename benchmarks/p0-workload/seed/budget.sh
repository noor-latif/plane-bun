#!/usr/bin/env bash
# Temporary experiment-only parent; never changes docker.service or other stacks.
set -euo pipefail
UNIT=plane-bench-reference.slice
case "${1:-}" in
  start)
    sudo -n systemctl set-property --runtime "$UNIT" CPUQuota=200% MemoryMax=4G MemorySwapMax=0
    sudo -n systemctl start "$UNIT"
    systemctl show "$UNIT" -p ControlGroup -p CPUQuotaPerSecUSec -p MemoryMax -p MemorySwapMax
    ;;
  stop)
    if docker ps --filter label=com.docker.compose.project=plane-bench --format '{{.ID}}' | grep -q .; then
      echo 'Refusing to stop reference slice while experiment containers run' >&2
      exit 1
    fi
    sudo -n systemctl stop "$UNIT"
    sudo -n systemctl revert "$UNIT"
    ;;
  *) echo 'usage: budget.sh start|stop' >&2; exit 1 ;;
esac
