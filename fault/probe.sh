#!/usr/bin/env bash
# probe.sh — build fault/Dockerfile with the given salts and report which steps
# were served CACHED. This is the single primitive of the fault-matrix workflow:
# "is salt X in the cache this builder sees?" (side effect: a miss WRITES it).
#
#   usage: probe.sh <logfile> [--salt S] [--bloat-mb N] [--bloat-salt B]
#                             [--timeout SEC] [--builder NAME]
#
#   stdout: one line  ->  rc=<build rc> salt_cached=0|1 bloat_cached=0|1 base_cached=0|1
#   exit:   the build's rc (124 = timed out / hang — always a bug)
set -u
LOG=${1:?usage: probe.sh <logfile> [options]}; shift
SALT=none; BLOAT_MB=0; BLOAT_SALT=none; TMO=600; BUILDER="${MONKCI_BUILDER:-default}"
while [ $# -gt 0 ]; do
  case "$1" in
    --salt)       SALT=$2;       shift 2 ;;
    --bloat-mb)   BLOAT_MB=$2;   shift 2 ;;
    --bloat-salt) BLOAT_SALT=$2; shift 2 ;;
    --timeout)    TMO=$2;        shift 2 ;;
    --builder)    BUILDER=$2;    shift 2 ;;
    *) echo "probe.sh: unknown arg $1" >&2; exit 2 ;;
  esac
done
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

timeout -k 30 "$TMO" docker buildx build --builder "$BUILDER" --progress=plain \
  --build-arg SALT="$SALT" --build-arg BLOAT_MB="$BLOAT_MB" --build-arg BLOAT_SALT="$BLOAT_SALT" \
  --output type=cacheonly "$DIR" >"$LOG" 2>&1
rc=$?

# In --progress=plain output a step is "#N [i/j] RUN <literal instruction>" and,
# when served from cache, a matching "#N CACHED" line follows.
step_cached() {
  local id
  id=$(grep -F "$1" "$LOG" | grep -Eo '^#[0-9]+' | head -1)
  if [ -n "$id" ] && grep -qE "^${id} CACHED" "$LOG"; then echo 1; else echo 0; fi
}

echo "rc=$rc salt_cached=$(step_cached 'RUN echo "salt=') bloat_cached=$(step_cached 'RUN echo "bloat=') base_cached=$(step_cached 'RUN echo shared-base')"
exit "$rc"
