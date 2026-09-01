#!/usr/bin/env bash

# Repeatable control-plane reliability baseline.
#
# Runs `AGENTNEXUS_RELIABILITY_RUNS` independent Plan -> Implement -> Review ->
# Test workflows against the real local server, runner, and wrapped SDK harness
# using the deterministic mock LLM. Each sampled run gets a fresh session tree
# and terminal-idle receipt; a single infrastructure failure fails the sample.
#
# Usage:
#   RUNS=100 scripts/run_control_plane_reliability.sh
#   RUNS=5 RELIABILITY_ARTIFACTS=.reliability-results scripts/run_control_plane_reliability.sh
#
# Requires bash, uv, and the test/integration dependencies. On Linux CI the
# runner also needs ripgrep, bubblewrap, and tmux (see ci.yml).

set -euo pipefail

repo_root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
runs="${RUNS:-${AGENTNEXUS_RELIABILITY_RUNS:-100}}"
artifact_dir="${RELIABILITY_ARTIFACTS:-$repo_root/.reliability-results}"

if ! [[ "$runs" =~ ^[0-9]+$ ]] || [ "$runs" -lt 1 ]; then
  echo "RUNS must be a positive integer; got '$runs'" >&2
  exit 2
fi

mkdir -p "$artifact_dir"
echo "Running $runs independent control-plane workflow samples..."
echo "Artifacts: $artifact_dir"

(
  cd "$repo_root"
  AGENTNEXUS_RELIABILITY_RUNS="$runs" \
    uv run --no-sync pytest -q \
      tests/integration/test_control_plane_reliability_runs.py \
      -p no:cacheprovider \
      --maxfail=1 \
      --junitxml="$artifact_dir/reliability.xml"
)

echo "OK: $runs control-plane reliability runs completed."
