#!/usr/bin/env bash
# Private synthetic snapshots; archives and credentials stay outside evidence/git.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="${P0_STATE_DIR:-$HERE/seed/reference-state}"
COMPOSE=(docker compose -p plane-bench -f "$HERE/../../docker-compose.yml"
  -f "$HERE/deploy/docker-compose.bench.yml" --env-file "$HERE/deploy/plane-bench.env")
VOLUMES=(plane-bench_bench_pgdata plane-bench_bench_redisdata plane-bench_bench_rabbitmq plane-bench_bench_uploads)
image_ids() {
  "${COMPOSE[@]}" config --images | sort -u | while read -r image; do
    docker image inspect --format '{{.Id}}' "$image"
  done
}
fix_archive_owner() {
  docker run --rm --pull never --network none --read-only -v "$STATE:/backup" alpine \
    sh -c 'chown "$1:$2" /backup/*.tar.gz; chmod 600 /backup/*.tar.gz' sh "$(id -u)" "$(id -g)"
}
case "${1:-}" in
  save)
    test ! -e "$STATE"
    mkdir -m 700 "$STATE"
    cp "$HERE/seed/user.json" "$STATE/user.json"
    image_ids > "$STATE/images.ids"
    "${COMPOSE[@]}" stop
    for volume in "${VOLUMES[@]}"; do
      docker run --rm --pull never --network none --read-only \
        -v "$volume:/source:ro" -v "$STATE:/backup" alpine \
        tar -czf "/backup/$volume.tar.gz" -C /source .
    done
    fix_archive_owner
    sha256sum "$STATE"/*.tar.gz > "$STATE/SHA256SUMS"
    chmod -R go-rwx "$STATE"
    echo 'Saved stopped-volume snapshot; stack remains stopped'
    ;;
  restore)
    fix_archive_owner
    sha256sum -c "$STATE/SHA256SUMS"
    diff "$STATE/images.ids" <(image_ids)
    "${COMPOSE[@]}" down -v --remove-orphans
    "${COMPOSE[@]}" create --no-build --pull never
    for volume in "${VOLUMES[@]}"; do
      docker run --rm --pull never --network none --read-only \
        -v "$volume:/target" -v "$STATE:/backup:ro" alpine \
        tar -xzf "/backup/$volume.tar.gz" -C /target
    done
    cp "$STATE/user.json" "$HERE/seed/user.json"
    bash "$HERE/seed/budget.sh" start
    "${COMPOSE[@]}" up -d --no-build --pull never
    bash "$HERE/seed/ready.sh"
    ;;
  *) echo 'usage: state.sh save|restore' >&2; exit 1 ;;
esac
